"""Independent money/route/stock checks using observed consumer source fixtures."""
import copy
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
import app.pricing as pricing
import app.solver as solver
import app.transfero_evidence as evidence
from app.airport_catalog import lookup_airport
from app.ground_query_paths import fixed_capacity, ground_paths
from app.models import Airport, Allocation, Offer, PassengerGroup, Resource, Strategy, World
from app.providers.ecb_rates import EcbRates
from app.providers.reference_fx import EcbRateTable
from app.romanian_hotel_recovery import RomanianHotelRecoveryInputs
from app.stay_requirements import REQUIREMENT_KEY
from test_transfero import NOW, parsed_quote, source_payload, fixture_source_terms


def enriched(direction='otp_to_hotel', *, payload=None, now=NOW, rates=None):
    quote=parsed_quote(direction,payload=payload,now=now)
    rates=rates or {'table':EcbRateTable(now.date(),now,{'RON':Decimal('5.0000')}),'cache':'fresh'}
    return EcbRates.enrich(SimpleNamespace(_now=lambda:now,MAX_RATE_AGE_DAYS=3),quote,rates,source_currency='RON')


def environment(groups=1, *, now=NOW):
    epoch='2026-11-01T00:00:00+00:00'
    def minute(iso): return int((datetime.fromisoformat(iso)-datetime.fromisoformat(epoch)).total_seconds()/60)
    airports=[Airport(id=code,code=code,name=lookup_airport(code)['name'],lat=lookup_airport(code)['lat'],lon=lookup_airport(code)['lon']) for code in ('OTP','CRA')]
    hotel=Airport(id=evidence.HOTEL_NODE_ID,code=evidence.HOTEL_NODE_ID,name='Hotel Aro Palace',lat=evidence.HOTEL_LATITUDE,lon=evidence.HOTEL_LONGITUDE,kind='hotel')
    inputs=RomanianHotelRecoveryInputs(source='transfero_direct_aeroport',hotel_id='lpce923',checkin_at='2026-11-02T18:00:00+02:00',
        checkout_at='2026-11-03T05:30:00+02:00',rooms=1,minimum_rest_minutes=480,suitcases=2,handbags=2,
        standard_baggage_confirmed=True,airport_processing_minutes=180,hotel_connection_minutes=20)
    passengers=[PassengerGroup(id=f'G{i+1}',name='Fixture group',size=2,adult_count=2,origin='OTP',destination='CRA',
        ready_at=minute('2026-11-02T12:00:00+02:00'),deadline=minute('2026-11-03T18:00:00+02:00'),budget=1000,
        original_flight_id='reported-arrived',token='fixture',source='operator_input',provenance={REQUIREMENT_KEY:inputs.requirement().model_dump(mode='json')}) for i in range(groups)]
    world=World(scenario_id='transfero-offline',epoch=epoch,mode='live',sim_time=390,airports=airports+[hotel],resources=[],groups=passengers,budget=5000)
    for group in passengers:
        for direction in ('otp_to_hotel','hotel_to_otp'):
            quote=enriched(direction,now=now)
            world.resources.append(evidence.build_transfero_resource(world,group,inputs,hotel,quote,quote['options'][0]['id'],now=now))
        world.resources.append(Resource(id='H-'+group.id,kind='hotel',name='Hotel Aro Palace',origin=hotel.id,destination=hotel.id,
            departure=minute(inputs.checkin_at.isoformat()),arrival=minute(inputs.checkout_at.isoformat()),capacity=4,quote_rooms=1,cost_per_person=0,
            budget_total_cost=100,source='Nuitee / LiteAPI',eligible_group_ids=[group.id],requires_revalidation=True,
            provenance={'id':'lpce923','name':'Hotel Aro Palace','provider':'liteapi','adults':2,'rooms':1,'quoted_at':now.isoformat(),
                'checkin_at':inputs.checkin_at.isoformat(),'checkout_at':inputs.checkout_at.isoformat(),
                'property_location':{'node_id':hotel.id,'latitude':hotel.lat,'longitude':hotel.lon},
                'pricing':{'status':'complete','mandatory_total':{'amount':'100.00','currency':'EUR'}}}))
        world.resources.append(Resource(id='F-'+group.id,kind='flight',name='Fixture flight',origin='OTP',destination='CRA',
            departure=minute('2026-11-03T11:30:00+02:00'),arrival=minute('2026-11-03T13:00:00+02:00'),capacity=2,cost_per_person=0,
            quote_total_cost=100,source='Nuitee',eligible_group_ids=[group.id],requires_revalidation=True,provenance={'quoted_at':now.isoformat()}))
    return world,inputs


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(solver,'datetime',Clock)
    monkeypatch.setattr(pricing,'datetime',Clock)
    monkeypatch.setattr(evidence,'datetime',Clock)


def test_four_leg_journey_retains_ron_once_and_explicit_eur_estimate():
    world,_=environment()
    plan=solver.build_candidate(world,Strategy())
    assert plan.validation['valid'] and plan.recovered_passengers==2
    assert [leg.kind for leg in plan.allocations[0].itinerary.legs]==['bus','hotel','bus','flight']
    assert plan.total_cost==304
    assert plan.cost_evidence.original_ron=='520.00' and plan.cost_evidence.original_gbp=='0'
    assert plan.cost_evidence.estimated_eur=='104.00'
    for resource in world.resources[:2]:
        assert resource.quote_total_cost is None and resource.budget_total_cost==52
        assert resource.price_evidence.original.currency=='RON' and resource.valid_until is None
        assert resource.provenance.get('quoted_at') is None
        assert pricing.app_receipt_deadline(resource)==NOW+timedelta(seconds=120)


@pytest.mark.parametrize('change',[
    lambda r: setattr(r,'capacity',999),lambda r:setattr(r,'budget_total_cost',260),lambda r:setattr(r,'quote_total_cost',260),
    lambda r:setattr(r,'accessible',True),lambda r:setattr(r,'booking_status','confirmed'),lambda r:setattr(r,'vehicle_id','invented'),
    lambda r:setattr(r,'valid_until',(NOW+timedelta(hours=1)).isoformat()),lambda r:r.provenance.update(quoted_at=NOW.isoformat()),
    lambda r:r.provenance.update(inventory_pool_id='otherpool'),lambda r:setattr(r,'arrival',r.arrival-20),
    lambda r:r.provenance['transfero_context']['inputs'].update(suitcases=0),
    lambda r:r.provenance['transfero_quote']['fx']['reference'].update(source_currency='GBP'),
    lambda r:r.provenance['transfero_quote']['options'][0]['price'].update(amount='1.00'),
])
def test_independent_resource_validator_rejects_changed_source_or_context(change):
    world,_=environment();resource=world.resources[0];change(resource)
    assert not evidence.valid_transfero_resource(world,resource,world.groups[0],now=NOW)
    assert not solver.itinerary_options(world,world.groups[0])


@pytest.mark.parametrize('resource_index,minutes',[(0,-11),(1,-11),(3,-1)])
def test_missing_boarding_hotel_or_flight_buffer_cannot_be_allocated(resource_index,minutes):
    world,_=environment()
    # Alter original schedule to create actual insufficient gap; source-bus
    # tampering fails independently, and flight needs the full180-minute gap.
    world.resources[resource_index].departure+=minutes
    assert not solver.itinerary_options(world,world.groups[0])


def test_initial_boarding_requires_twenty_minutes_not_generic_zero():
    world,_=environment();world.groups[0].ready_at=world.resources[0].departure-19
    assert not solver.itinerary_options(world,world.groups[0])


def test_repeated_party_quotes_share_stock_in_allocator_validator_and_query():
    world,inputs=environment(groups=2)
    # Both independent group receipts report only3 remaining seats, not3 each.
    hotel=world.airports[-1]
    for group in world.groups:
        for direction in ('otp_to_hotel','hotel_to_otp'):
            data=source_payload(direction);data['d'][0]['locuri']='3'
            quote=enriched(direction,payload=data)
            replacement=evidence.build_transfero_resource(world,group,inputs,hotel,quote,quote['options'][0]['id'],now=NOW)
            old=next(r for r in world.resources if r.source=='Transfero' and r.eligible_group_ids==[group.id] and r.provenance['direction']==direction)
            world.resources[world.resources.index(old)]=replacement
    plan=solver.build_candidate(world,Strategy())
    assert plan.validation['valid'] and plan.recovered_passengers==2 and len(plan.allocations)==1
    itineraries=[solver.itinerary_options(world,g)[0] for g in world.groups]
    forced=[Allocation(group_id=g.id,itinerary=it) for g,it in zip(world.groups,itineraries)]
    result=solver.validate_allocations(world,forced)
    assert not result['valid'] and any('shared Direct Aeroport' in err for err in result['errors'])
    world.offers=[Offer(id='KEEP',group_id='G1',itinerary=itineraries[0],status='proposed',expires_at=(NOW+timedelta(seconds=100)).isoformat(),created_version=1)]
    occupied=fixed_capacity(world,{'G2'})  # No reservation rows are needed or invented.
    prefixes,_,_=ground_paths(world,world.groups[1],occupied)
    assert not any(path.has_hotel for path in prefixes)


def test_query_prefix_carries_full_onward_airport_margin():
    world,_=environment();prefixes,_,_=ground_paths(world,world.groups[0],Counter())
    returned=[p for p in prefixes if p.has_hotel and p.airport_id=='OTP']
    assert returned and all(p.flight_buffer==180 for p in returned)


def test_advance_cutoff_shortens_receipt_without_supplier_expiry():
    near=datetime(2026,11,2,0,29,30,tzinfo=timezone.utc)
    world,_=environment(now=near);resource=world.resources[0]
    assert evidence.transfero_deadline(resource)==near+timedelta(seconds=30)
    assert evidence.transfero_current(resource,now=near+timedelta(seconds=30))
    assert not evidence.transfero_current(resource,now=near+timedelta(seconds=31))


def test_missing_stale_or_wrong_currency_fx_never_supplies_budget():
    for rates in ({'table':None,'cache':'fresh'}, {'table':EcbRateTable(NOW.date()-timedelta(days=4),NOW,{'RON':Decimal('5')}),'cache':'fresh'},
                  {'table':EcbRateTable(NOW.date(),NOW,{'GBP':Decimal('0.8')}),'cache':'fresh'}):
        quote=enriched(rates=rates)
        assert quote['options'][0]['price']=={'amount':'260.00','currency':'RON'}
        assert quote['options'][0]['eur_reference_estimate'] is None
        with pytest.raises(ValueError): pricing.build_transfero_price_evidence(quote,quote['options'][0]['id'],now=NOW)


def test_newer_stock_increase_replaces_old_observation_but_retains_fixed_usage():
    world,inputs=environment(groups=2);hotel=world.airports[-1]
    # Earlier2-seat quote and a later6-seat quote describe the same actual
    # service. The old group's2 held passengers still consume the newer pool.
    old_data=source_payload();old_data['d'][0]['locuri']='2'
    new_data=source_payload();new_data['d'][0]['locuri']='6'
    old_quote=enriched(payload=old_data)
    new_quote=enriched(payload=new_data,now=NOW+timedelta(seconds=10))
    old=evidence.build_transfero_resource(world,world.groups[0],inputs,hotel,old_quote,old_quote['options'][0]['id'],now=NOW)
    new=evidence.build_transfero_resource(world,world.groups[1],inputs,hotel,new_quote,new_quote['options'][0]['id'],now=NOW+timedelta(seconds=10))
    world.resources=[old,new]
    assert evidence.transfero_remaining(world,new,Counter({old.id:2}))==4
    assert not evidence.transfero_capacity_errors(world,Counter({old.id:2,new.id:2}))
    old.available=False  # An unallocated retired low count cannot poison6.
    assert evidence.transfero_remaining(world,new,Counter())==6


def test_same_observation_time_disagreement_uses_lower_stock_not_sum():
    world,inputs=environment(groups=2);hotel=world.airports[-1];resources=[]
    for group,count in zip(world.groups,(3,6)):
        data=source_payload();data['d'][0]['locuri']=str(count);quote=enriched(payload=data)
        resources.append(evidence.build_transfero_resource(world,group,inputs,hotel,quote,quote['options'][0]['id'],now=NOW))
    world.resources=resources
    assert evidence.transfero_remaining(world,resources[1],Counter({resources[0].id:2}))==1
    assert evidence.transfero_capacity_errors(world,Counter({r.id:2 for r in resources}))


def test_gbp_and_ron_are_never_combined_as_one_original_currency():
    from test_apl_pricing import quote as apl_fixture
    source=apl_fixture();source['received_at']=NOW.isoformat()
    source['fx']['reference'].update(rate_date=NOW.date().isoformat(),retrieved_at=NOW.isoformat(),as_of=NOW.isoformat())
    apl=pricing.build_apl_price_evidence(source,'APLQ-fixture',now=NOW)
    ron=enriched();coach=pricing.build_transfero_price_evidence(ron,ron['options'][0]['id'],now=NOW)
    summary=pricing.cost_summary([{'cost':95,'price_evidence':apl.model_dump(mode='json')},{'cost':52,'price_evidence':coach.model_dump(mode='json')}])
    assert summary.original_gbp=='76.00' and summary.original_ron=='260.00' and summary.estimated_eur=='147.00'
    assert not pricing.matching_cost_summary([{'cost':95,'price_evidence':apl.model_dump(mode='json')}],summary,147)
