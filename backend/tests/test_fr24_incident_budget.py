"""Shared FR24 accounting, failures and lookup concurrency use mock transports."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json

import httpx
import pytest

from app.config import Settings
from app.incidents import FlightLookupRequest, IncidentService
from app.providers import FlightProvider, ProviderError
from app.store import Store, utcnow


def fixture(tmp_path, **changes):
    settings=Settings(_env_file=None,operation_mode="live",database_path=str(tmp_path/"budget.sqlite3"),fr24_api_key="fixture-fr24",fr24_max_requests=84,**changes)
    store=Store(settings.database_path,operation_mode="live")
    return settings,store


def legacy_budget(store,count=80):
    with store.connect() as conn:
        conn.execute("INSERT INTO leases VALUES('fr24',0,?)",(count,))
        conn.execute("DELETE FROM controls WHERE key='fr24_credits_reserved'")
    return Store(store.path,operation_mode="live",legacy_fr24_credits_per_request=40)


def release_interval(store):
    with store.connect() as conn:
        conn.execute("UPDATE leases SET expires=0 WHERE name='fr24'")


@pytest.mark.asyncio
async def test_old_80_calls_3200_credits_survive_and_polling_shares_the_lookup_budget(tmp_path):
    settings,store=fixture(tmp_path)
    store=legacy_budget(store)
    assert store.poll_usage()==80 and store.fr24_credits_reserved()==3200
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("flight-summary/full"):
            return httpx.Response(200,json={"data":[]})
        return httpx.Response(200,json={"data":[{"fr24_id":"abcdef12","lat":44.32,"lon":23.88,"timestamp":utcnow().isoformat(),"callsign":"ROT321"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings.fr24_max_requests=80
        provider=FlightProvider(settings,client=client,usage_store=store)
        service=IncidentService(settings,store,provider)
        request=FlightLookupRequest(flight_number="RO321",date_utc=utcnow().date())
        failed=await service.lookup(request)
        assert failed["state"]=="error" and failed["code"]=="budget_exhausted" and calls==[]
        settings.fr24_max_requests=84
        assert (await service.lookup(request))["state"]=="no_match"
        assert store.poll_usage()==81 and store.fr24_credits_reserved()==3215
        another_worker=FlightProvider(settings,client=client,usage_store=Store(store.path))
        with pytest.raises(ProviderError) as interval:
            await another_worker.fetch()
        assert interval.value.code=="poll_interval" and len(calls)==1
        release_interval(store)
        assert len(await another_worker.fetch())==1
        assert store.poll_usage()==82 and store.fr24_credits_reserved()==3255
    assert Store(store.path).poll_usage()==82 and Store(store.path).fr24_credits_reserved()==3255


@pytest.mark.asyncio
async def test_different_lookup_does_not_queue_behind_an_inflight_provider_call(tmp_path):
    settings,store=fixture(tmp_path)
    entered,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200,json={"data":[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service=IncidentService(settings,store,FlightProvider(settings,client=client,usage_store=store))
        pending=asyncio.create_task(service.lookup(FlightLookupRequest(flight_number="RO321",date_utc=utcnow().date())))
        await asyncio.wait_for(entered.wait(),1)
        try:
            other=await asyncio.wait_for(service.lookup(FlightLookupRequest(flight_number="RO322",date_utc=utcnow().date())),1)
            assert other["state"]=="error" and other["code"]=="lookup_in_progress"
            assert len(calls)==1 and store.poll_usage()==1
        finally:
            release.set()
            await pending


@pytest.mark.asyncio
async def test_credit_cap_blocks_lookup_without_spending_a_request(tmp_path):
    settings,store=fixture(tmp_path)
    with store.connect() as conn:
        conn.execute("UPDATE controls SET value='3990' WHERE key='fr24_credits_reserved'")
    calls=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:calls.append(request))) as client:
        service=IncidentService(settings,store,FlightProvider(settings,client=client,usage_store=store))
        result=await service.lookup(FlightLookupRequest(flight_number="RO321",date_utc=utcnow().date()))
    assert result["state"]=="error" and result["code"]=="budget_exhausted"
    assert store.poll_usage()==0 and store.fr24_credits_reserved()==3990 and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status",[401,403,429,503])
async def test_provider_failure_keeps_conservative_charge_and_never_retries(tmp_path,status):
    settings,store=fixture(tmp_path)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status,text="UPSTREAM_PRIVATE_BODY")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service=IncidentService(settings,store,FlightProvider(settings,client=client,usage_store=store))
        request=FlightLookupRequest(flight_number="RO321",date_utc=utcnow().date())
        result=await service.lookup(request)
        assert result["state"]=="error" and result["code"]==f"http_{status}"
        assert "UPSTREAM_PRIVATE_BODY" not in json.dumps(result)
        assert (await service.lookup(request))["code"]=="poll_interval"
    assert len(calls)==1 and store.poll_usage()==1 and store.fr24_credits_reserved()==15
    assert store.read().incidents==[]


@pytest.mark.asyncio
async def test_old_summary_reserves_six_credits_per_result_and_never_invents_nulls(tmp_path):
    settings,store=fixture(tmp_path)
    first=utcnow()-timedelta(days=35)
    row={"fr24_id":"abcdef12","flight":"RO321","first_seen":first.isoformat(),"last_seen":None,"flight_ended":"true"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"data":[row]}))) as client:
        service=IncidentService(settings,store,FlightProvider(settings,client=client,usage_store=store))
        result=await service.lookup(FlightLookupRequest(flight_number="RO321",date_utc=first.date()))
    match=result["matches"][0]
    assert match["status"]=="ended" and match["freshness"]=="historical"
    assert match["origin"] is match["intended_destination"] is match["actual_destination"] is match["aircraft_type"] is None
    assert store.fr24_credits_reserved()==30


def test_multiple_store_instances_cannot_spend_the_last_request_twice(tmp_path):
    _,store=fixture(tmp_path)
    stores=[Store(store.path),Store(store.path)]
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda item:item.reserve_fr24_request(0,1,15,15),stores))
    assert results.count(None)==1 and results.count("budget_exhausted")==1
    assert store.poll_usage()==1 and store.fr24_credits_reserved()==15


@pytest.mark.asyncio
async def test_lookup_timeout_releases_claim_but_keeps_budget(tmp_path):
    settings,store=fixture(tmp_path,request_timeout_seconds=1)
    calls=[]
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(2)
        return httpx.Response(200,json={"data":[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service=IncidentService(settings,store,FlightProvider(settings,client=client,usage_store=store))
        result=await service.lookup(FlightLookupRequest(flight_number="RO321",date_utc=utcnow().date()))
    assert result["state"]=="error" and result["code"]=="timeout"
    assert len(calls)==store.poll_usage()==1 and store.fr24_credits_reserved()==15
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM request_claims").fetchone()[0]==0
