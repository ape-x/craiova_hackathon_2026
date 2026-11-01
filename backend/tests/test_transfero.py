"""Offline observed-protocol checks; fixtures contain no customer or booking data."""
import asyncio
import copy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
import httpx
import pytest
from pydantic import ValidationError
from app.providers.base import ProviderError
from app.providers.transfero import MAX_BODY, POLICY, SEARCH_URL, TransferoProvider, TransferoSearchRequest, parse_search_response, validate_normalized_option

FIXTURES = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)

@pytest.fixture(autouse=True)
def fixture_source_terms(monkeypatch):
    # Authored minimal policy fixture, not redistributed carrier contract text.
    # Production keeps the exact independently observed hash; private raw
    # replays verify it separately without making a supplier request.
    import app.providers.transfero as provider
    digest=hashlib.sha256((FIXTURES/'transfero_terms_57392.txt').read_bytes()).hexdigest()
    monkeypatch.setattr(provider,'TERMS_SHA256',digest)
    monkeypatch.setitem(POLICY,'attached_terms_sha256',digest)

def source_payload(direction='otp_to_hotel'):
    row = json.loads((FIXTURES / 'transfero_direct_services.json').read_text())['outbound' if direction == 'otp_to_hotel' else 'return']
    row['segments'][0]['terms'] = (FIXTURES / 'transfero_terms_57392.txt').read_text()
    return {'d': [row], 'i': []}

def source_request(direction='otp_to_hotel', **changes):
    return TransferoSearchRequest.model_validate({'direction': direction, 'travel_date': '2026-11-02' if direction == 'otp_to_hotel' else '2026-11-03',
        'adults': 2, 'suitcases': 2, 'handbags': 2, 'standard_baggage_confirmed': True, **changes})

def parsed_quote(direction='otp_to_hotel', *, payload=None, now=NOW):
    return parse_search_response(json.dumps(payload or source_payload(direction)).encode(), source_request(direction), received_at=now)

@pytest.mark.parametrize('direction,depart,arrive,seats', [('otp_to_hotel', '2026-11-02T12:30:00+00:00', '2026-11-02T15:30:00+00:00', 13),
    ('hotel_to_otp', '2026-11-03T04:00:00+00:00', '2026-11-03T06:30:00+00:00', 19)])
def test_observed_party_fare_stops_and_seats(direction, depart, arrive, seats):
    quote = parsed_quote(direction)
    assert quote['source_max_adults'] is None and quote['app_max_adults'] == 20
    assert quote['booking_supported'] is False and quote['expires_at'] is None
    option = validate_normalized_option(quote, quote['options'][0]['id'], now=NOW)
    assert (option['departure_at'], option['arrival_at'], option['available_seats']) == (depart, arrive, seats)
    assert option['price'] == {'amount': '260.00', 'currency': 'RON'} and option['adults'] == 2
    assert option['price_scope'] == 'requested_party' and option['policy'] == POLICY
    assert option['supplier_valid_until'] is None and option['seat_selection'] is False
    assert option['availability_status'] == 'quoted_not_reserved' and 'tax' not in json.dumps(quote)

@pytest.mark.parametrize('change', [
    lambda r: r.update(locuri='1'), lambda r: r.update(locuri=None), lambda r: r.update(locuri=True),
    lambda r: r['tickets']['adulti']['oneway'].update(rate=1), lambda r: r['tickets']['adulti']['oneway'].update(rate=True),
    lambda r: r['tickets']['adulti'].update(ban='EUR'), lambda r: r['tickets']['adulti']['oneway'].update(pret_ron=0),
    lambda r: r['tickets']['adulti']['oneway'].update(pret_u=130), lambda r: r['tickets']['adulti']['oneway'].update(booking_fee=20),
    lambda r: r['tickets'].update(children={}), lambda r: r['segments'][0].update(terms='Unknown mandatory fee'),
    lambda r: r['segments'][0].update(termsid='999'), lambda r: r['segments'][0].update(linkid='999'),
    lambda r: r['segments'][0]['departure'].update(date='03.11.2026'),
    lambda r: r['segments'][0]['departure']['station'].update(name='Nearby hotel'),
    lambda r: r['segments'][0]['arrival']['station'].update(long='25.590917'),
    lambda r: r['company'].update(name='Unknown carrier'), lambda r: r.update(durata='00:10'),
    lambda r: r['segments'].append(copy.deepcopy(r['segments'][0])),
])
def test_unverified_fare_party_policy_timing_or_stop_never_creates_supply(change):
    data = source_payload(); change(data['d'][0]); quote = parsed_quote(payload=data)
    assert quote['options'] == [] and quote['rejected_count'] == 1

def test_duplicate_does_not_mask_conflicting_invalid_service():
    data = source_payload(); data['d'].append(copy.deepcopy(data['d'][0]))
    assert parsed_quote(payload=data)['rejected_count'] == 1
    data['d'][1]['locuri'] = None
    with pytest.raises(ValueError, match='Conflicting'): parsed_quote(payload=data)

def test_party_fare_revision_does_not_create_new_stock():
    first = parsed_quote()['options'][0]; data = source_payload()
    data['d'][0]['tickets']['adulti']['oneway'].update(rate=4, pret=520, pret_u=520, pret_ron=520)
    second = parse_search_response(json.dumps(data).encode(), source_request(adults=4), received_at=NOW)['options'][0]
    assert first['id'] != second['id'] and first['inventory_pool_id'] == second['inventory_pool_id']

@pytest.mark.parametrize('day', ['25.10.2026', '29.03.2026'])
def test_dst_ambiguous_or_nonexistent_clock_rejected(day):
    data = source_payload('hotel_to_otp'); row = data['d'][0]; row.update(oraplecare='03:30', orasosire='06:00')
    for role, clock in [('departure','03:30'),('arrival','06:00')]:
        row['segments'][0][role].update(date=day,time=clock,datetime=day+' '+clock)
    iso = datetime.strptime(day, '%d.%m.%Y').date().isoformat()
    result = parse_search_response(json.dumps(data).encode(),source_request('hotel_to_otp',travel_date=iso),received_at=datetime.fromisoformat(iso+'T00:00:00+00:00'))
    assert result['options'] == []

@pytest.mark.parametrize('changes',[{'adults':True},{'adults':21},{'suitcases':3},{'handbags':3},{'standard_baggage_confirmed':1},
    {'standard_baggage_confirmed':False},{'travel_date':1793577600},{'unexpected':'x'}])
def test_request_requires_explicit_scope(changes):
    with pytest.raises(ValidationError): source_request(**changes)

@pytest.mark.asyncio
async def test_only_one_exact_post_no_bags_checkout_or_auth():
    seen=[]
    def handler(request):
        seen.append(request)
        assert str(request.url)==SEARCH_URL and request.method=='POST' and 'authorization' not in request.headers
        assert parse_qs(request.content.decode(),keep_blank_values=True)=={'action':['get_busses'],'adulti':['2'],'copii':['0'],
            'dp':['11/02/2026'],'di':[''],'dep':['1231269'],'dest':['199']}
        return httpx.Response(200,json=source_payload(),headers={'content-type':'text/html; charset=UTF-8'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider=TransferoProvider(SimpleNamespace(),client=client,now=lambda:NOW)
        assert len((await provider.search(source_request()))['options'])==1 and len(seen)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('mode,code',[('timeout','timeout'),('transport','connection'),('redirect','http_302'),('server','http_507'),
    ('large','invalid_response'),('invalid','invalid_response'),('duplicate_json','invalid_response')])
async def test_sanitized_bounded_failure_no_retry(mode,code):
    seen=[]
    def handler(request):
        seen.append(request)
        if mode=='timeout': raise httpx.ReadTimeout('SECRET token',request=request)
        if mode=='transport': raise httpx.ConnectError('SECRET token',request=request)
        if mode=='redirect': return httpx.Response(302,headers={'location':'https://attacker.invalid/SECRET'})
        if mode=='server': return httpx.Response(507,text='SECRET')
        if mode=='large': return httpx.Response(200,content=b'x'*(MAX_BODY+1),headers={'content-type':'text/html'})
        if mode=='duplicate_json': return httpx.Response(200,content=b'{"d":[],"d":[],"i":[]}',headers={'content-type':'application/json'})
        return httpx.Response(200,text='SECRET')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as exc: await TransferoProvider(SimpleNamespace(),client=client,now=lambda:NOW).search(source_request())
    assert exc.value.code==code and 'SECRET' not in str(exc.value) and len(seen)==1

@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry():
    started=asyncio.Event(); calls=[]
    async def handler(request):
        calls.append(request);started.set();await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task=asyncio.create_task(TransferoProvider(SimpleNamespace(),client=client,now=lambda:NOW).search(source_request()))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert len(calls)==1

@pytest.mark.asyncio
async def test_invalid_horizon_before_http():
    def forbidden(request): raise AssertionError('must not dispatch')
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ProviderError) as exc:
            await TransferoProvider(SimpleNamespace(),client=client,now=lambda:NOW).search(source_request(travel_date='2026-12-03'))
    assert exc.value.code=='invalid_request'

@pytest.mark.asyncio
async def test_total_deadline_includes_awaited_response_without_retry():
    calls=[]
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(.1)
        return httpx.Response(200,json=source_payload())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider=TransferoProvider(SimpleNamespace(),client=client,now=lambda:NOW)
        provider.timeout=.01
        with pytest.raises(ProviderError) as exc: await provider.search(source_request())
    assert exc.value.code=='timeout' and len(calls)==1
