"""Real-flight case lifecycle, with isolated SQLite and mocked FR24 only."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

import app.store as store_module
from app.config import Settings
from app.incidents import FlightContextSelection, FlightLookupRequest, IncidentCreate, IncidentGroupBinding, IncidentService
from app.main import create_app
from app.models import AcceptRequest, Airport, Itinerary, Leg, Offer, PassengerGroup, Reservation, Resource, Strategy
from app.providers.flights import FlightProvider
from app.service import RecoveryService
from app.solver import build_candidate, resource_feasible
from app.store import DomainError, Store, utcnow


def summary(**changes):
    first = utcnow()-timedelta(minutes=10)
    return {"fr24_id": "abcdef12", "flight": "RO321", "callsign": "ROT321", "reg": "PRIVATE-TAIL",
            "type": "A320", "orig_iata": "CRA", "orig_icao": "LRCV", "dest_iata": "OTP",
            "dest_icao": "LROP", "dest_iata_actual": "SBZ", "dest_icao_actual": "LRSB",
            "first_seen": first.isoformat(), "last_seen": (utcnow()-timedelta(seconds=30)).isoformat(),
            "datetime_takeoff": first.isoformat(), "datetime_landed": None, "flight_ended": False, **changes}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(_env_file=None, operation_mode="live", database_path=str(tmp_path/"incidents.sqlite3"), fr24_api_key="fixture-fr24", fr24_max_requests=84)
    store = Store(settings.database_path, operation_mode="live")
    rows, calls = [summary()], []
    def handler(request):
        calls.append(request)
        assert request.url.host == "fr24api.flightradar24.com"
        assert request.url.path == "/api/flight-summary/full"
        assert request.url.params["flights"] == "RO321" and request.url.params["limit"] == "5"
        return httpx.Response(200, json={"data": rows})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = FlightProvider(settings, client=client, usage_store=store)
    service = IncidentService(settings, store, provider)
    return settings, store, service, rows, calls, client


async def lookup(setup):
    _, _, service, rows, _, _ = setup
    return await service.lookup(FlightLookupRequest(flight_number="ro-321", date_utc=rows[0]["first_seen"][:10] if rows else (utcnow()-timedelta(minutes=10)).date()))


def create_request(store, result, **changes):
    return IncidentCreate(**({"expected_version": store.read().version, "idempotency_key": "incident-create-key",
        "lookup_id": result["lookup_id"], "selection_id": result["matches"][0]["selection_id"],
        "kind": "destination_unavailable", "description": "Operator reports destination cannot accept this flight.",
        "unavailable_airport_code": "OTP", "affected_group_ids": []} | changes))


def add_groups(store, *, offers=False, confirmed=False):
    def apply(world):
        world.budget = 1000
        world.airports.append(Airport(id="OTP",code="OTP",name="Bucharest",lat=44.57,lon=26.1,available=True))
        world.airports[0].available = True
        for index in (1,2):
            group_id, resource_id = f"G{index}", f"Q{index}"
            world.groups.append(PassengerGroup(id=group_id,name=group_id,size=1,members=["Fixture passenger"],origin="CRA",destination="OTP",
                ready_at=world.sim_time,deadline=world.sim_time+400,budget=500,original_flight_id="RO321",token=f"token-{index}-"+"x"*30,
                adult_count=1,source="manifest_import",status="confirmed" if confirmed and index==1 else "offered" if offers else "waiting"))
            resource=Resource(id=resource_id,kind="flight",name="Sourced flight quote",origin="CRA",destination="OTP",departure=world.sim_time+60,
                arrival=world.sim_time+120,capacity=1,cost_per_person=0,quote_total_cost=100,source="Nuitee",eligible_group_ids=[group_id],
                valid_until=(utcnow()+timedelta(hours=1)).isoformat(),provenance={"quoted_at":utcnow().isoformat()})
            world.resources.append(resource)
            if offers:
                expiry=(utcnow()+timedelta(minutes=10)).isoformat()
                status="confirmed" if confirmed and index==1 else "proposed"
                world.offers.append(Offer(id="O"+str(index),group_id=group_id,status=status,created_version=world.version+1,expires_at=expiry,
                    itinerary=Itinerary(legs=[Leg(resource_id=resource_id,kind="flight",origin="CRA",destination="OTP",departure=resource.departure,arrival=resource.arrival,cost=100)],arrival=resource.arrival,total_cost=100),
                    external_bookings=[{"confirmation_reference":"PRESERVED-PNR"}] if status=="confirmed" else []))
                world.reservations.append(Reservation(id="R"+str(index),offer_id="O"+str(index),group_id=group_id,resource_id=resource_id,quantity=1,status=status,expires_at=expiry))
    store.mutate("fixture-groups",{},store.read().version,apply)


@pytest.mark.asyncio
async def test_lookup_is_dated_cached_and_does_not_create_inventory(setup):
    _,store,_,_,calls,client=setup
    before=store.read()
    result=await lookup(setup)
    match=result["matches"][0]
    assert result["state"]=="ready" and match["status"]=="tracking" and match["freshness"]=="current"
    assert match["intended_destination"]["catalog_code"]=="OTP" and match["actual_destination"]["catalog_code"]=="SBZ"
    assert [match[key]["icao"] for key in ("origin","intended_destination","actual_destination")]==["LRCV","LROP","LRSB"]
    assert "capacity" not in match and match["landed_at"] is None
    assert (await lookup(setup))["cached"] and len(calls)==1
    assert store.poll_usage()==1 and store.fr24_credits_reserved()==15
    assert store.read().version==before.version and store.read().resources==store.read().groups==[]
    await client.aclose()


@pytest.mark.asyncio
async def test_overnight_flight_uses_utc_first_seen_date_and_preserves_icao_only_facts(setup):
    _,_,_,rows,calls,client=setup
    first=utcnow().replace(hour=23,minute=55,second=0,microsecond=0)-timedelta(days=1)
    rows[0].update(first_seen=first.isoformat(),last_seen=utcnow().isoformat(),datetime_takeoff=first.isoformat(),orig_iata=None,dest_iata=None,dest_iata_actual=None)
    result=await lookup(setup)
    assert result["state"]=="ready" and result["matches"][0]["freshness"]=="current"
    assert result["matches"][0]["origin"]=={"iata":None,"icao":"LRCV","catalog_code":"CRA"}
    assert result["matches"][0]["intended_destination"]["catalog_code"]=="OTP"
    assert calls[0].url.params["flight_datetime_from"].startswith(first.date().isoformat())
    assert result["matches"][0]["last_seen"][:10]!=first.date().isoformat()
    await client.aclose()


@pytest.mark.asyncio
async def test_documented_long_icao_aliases_are_supported_without_inventing_nulls(setup):
    _,_,_,rows,_,client=setup
    for primary,alias in (("orig_icao","origin_icao"),("dest_icao","destination_icao"),("dest_icao_actual","destination_icao_actual")):
        rows[0][alias]=rows[0].pop(primary)
    result=await lookup(setup)
    assert result["state"]=="ready" and result["matches"][0]["actual_destination"]["icao"]=="LRSB"
    await client.aclose()


@pytest.mark.asyncio
async def test_ambiguous_lookup_requires_an_exact_server_selection(setup):
    _,store,service,rows,_,client=setup
    rows.append(summary(fr24_id="abcdef13"))
    result=await lookup(setup)
    assert len(result["matches"])==2
    with pytest.raises(DomainError):
        service.create(create_request(store,result,selection_id="invented-selection"))
    assert store.read().incidents==[]
    result2=service.create(create_request(store,result,selection_id=result["matches"][1]["selection_id"]))
    assert result2["world"]["incidents"][0]["flight"]["fr24_id"]=="abcdef13"
    await client.aclose()


@pytest.mark.asyncio
async def test_no_match_and_provider_errors_never_become_cancellations(setup):
    _,store,service,rows,_,client=setup
    rows.clear()
    result=await lookup(setup)
    assert result["state"]=="no_match" and result["matches"]==[]
    assert store.read().incidents==[] and "does not establish" in result["message"]
    service.flights.settings.fr24_api_key=""
    other=await service.lookup(FlightLookupRequest(flight_number="RO322",date_utc=utcnow().date()))
    assert other["state"]=="not_configured" and other["code"]=="not_configured"
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes",[{"flight":"RO999"},{"first_seen":"2022-06-01T00:00:00Z"},{"fr24_id":"not-a-flight"},{"orig_iata":"OTP","origin_icao":"LRCV"},{"datetime_takeoff":"2026-10-31"},{"origin_icao":"LROP"},{"orig_icao":None,"origin_icao":"LRCV"}])
async def test_mismatched_provider_identifiers_cannot_seed_a_case(setup,changes):
    _,store,service,rows,_,client=setup
    day=rows[0]["first_seen"][:10]
    rows[0].update(changes)
    result=await service.lookup(FlightLookupRequest(flight_number="RO321",date_utc=day))
    assert result["state"]=="error" and result["code"]=="invalid_response"
    assert store.read().incidents==[] and store.poll_usage()==1
    await client.aclose()


@pytest.mark.asyncio
async def test_case_append_idempotency_and_unrelated_operation_survive(setup):
    _,store,service,_,_,client=setup
    add_groups(store)
    before=store.read()
    result=await lookup(setup)
    request=create_request(store,result)
    created=service.create(request)
    after=store.read()
    assert after.scenario_id==before.scenario_id and after.groups==before.groups and after.resources==before.resources
    assert after.incidents[0].status=="needs_passenger_data" and after.incidents[0].source=="operator_report"
    assert service.create(request)["replayed"] and store.read().version==created["world"]["version"]
    with pytest.raises(DomainError) as stale:
        service.create(request.model_copy(update={"idempotency_key":"stale-incident-key"}))
    assert stale.value.status==409 and len(store.read().incidents)==1
    await client.aclose()


@pytest.mark.asyncio
async def test_binding_invalidates_only_affected_pending_proposal_and_other_acceptance_works(setup):
    settings,store,service,_,_,client=setup
    add_groups(store,offers=True)
    result=await lookup(setup)
    created=service.create(create_request(store,result,affected_group_ids=["G1"]))
    world=store.read()
    assert world.groups[0].incident_id==created["incident_id"] and world.groups[1].incident_id is None
    assert [offer.status for offer in world.offers]==["invalidated","proposed"]
    assert [row.status for row in world.reservations]==["released","proposed"]
    assert all(airport.operational_status=="unknown" for airport in world.airports)
    accepted=RecoveryService(settings,store).accept("O2",AcceptRequest(token=world.groups[1].token,idempotency_key="unrelated-accept"))
    assert accepted["offer"]["status"]=="accepted"
    assert build_candidate(store.read(),Strategy()).recovered_passengers==1
    await client.aclose()


@pytest.mark.asyncio
async def test_confirmed_binding_and_wrong_flight_roll_back_everything(setup):
    _,store,service,_,_,client=setup
    add_groups(store,offers=True,confirmed=True)
    result=await lookup(setup)
    before=store.read()
    with pytest.raises(DomainError) as error:
        service.create(create_request(store,result,affected_group_ids=["G1"]))
    assert error.value.status==409 and "supplier-confirmed" in error.value.message
    after=store.read()
    assert after.version==before.version and after.offers==before.offers and after.reservations==before.reservations
    assert after.incidents==[] and after.offers[0].external_bookings[0]["confirmation_reference"]=="PRESERVED-PNR"
    store.mutate("different-original-flight",{},after.version,lambda world:setattr(world.groups[1],"original_flight_id","RO999"))
    before=store.read().version
    with pytest.raises(DomainError,match="different original flight"):
        service.create(create_request(store,result,affected_group_ids=["G2"]))
    assert store.read().version==before
    await client.aclose()


@pytest.mark.asyncio
async def test_append_only_group_binding_and_intermediate_airport_exclusion(setup):
    _,store,service,_,_,client=setup
    add_groups(store)
    result=await lookup(setup)
    created=service.create(create_request(store,result,affected_group_ids=["G1"]))
    request=IncidentGroupBinding(expected_version=store.read().version,idempotency_key="bind-second-group",group_ids=["G2"])
    bound=service.bind(created["incident_id"],request)
    assert bound["world"]["incidents"][0]["group_ids"]==["G1","G2"]
    assert service.bind(created["incident_id"],request)["replayed"]
    world=store.read()
    next(airport for airport in world.airports if airport.id=="SBZ").available=True
    resource=world.resources[0].model_copy(update={"destination":"SBZ","provenance":{"segments":[{"origin":"CRA","destination":"OTP"},{"origin":"OTP","destination":"SBZ"}]}})
    assert not resource_feasible(world,resource,world.groups[0],"CRA",world.sim_time,set())
    resource.provenance={"segments":[{"origin":"CRA","destination":"SBZ"}]}
    assert resource_feasible(world,resource,world.groups[0],"CRA",world.sim_time,set())
    await client.aclose()


@pytest.mark.asyncio
async def test_expired_cache_replay_and_exact_refresh_preserve_incident(setup,monkeypatch):
    _,store,service,_,_,client=setup
    result=await lookup(setup)
    request=create_request(store,result)
    created=service.create(request)
    with store.connect() as conn:
        payloads=[row[0] for row in conn.execute("SELECT result FROM idempotency")]
        payloads.append(conn.execute("SELECT data FROM world").fetchone()[0])
    assert all("PRIVATE-TAIL" not in item for item in payloads)
    start=utcnow()
    monkeypatch.setattr(store_module,"utcnow",lambda:start+timedelta(hours=2))
    replay=service.create(request)
    assert replay["replayed"] and replay["world"]["incidents"][0]["flight"] is None
    assert store.read().incidents[0].flight_context_state=="expired"
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM flight_lookups").fetchone()[0]==0
    fresh={**result,"lookup_id":"lookup_refreshed","expires_at":(start+timedelta(hours=2,minutes=5)).isoformat()}
    store.save_flight_lookup("fresh-query",fresh)
    refresh=FlightContextSelection(expected_version=store.read().version,idempotency_key="refresh-same-flight",lookup_id=fresh["lookup_id"],selection_id=result["matches"][0]["selection_id"])
    updated=service.refresh(created["incident_id"],refresh)
    assert updated["world"]["incidents"][0]["flight"]["fr24_id"]=="abcdef12"
    assert updated["world"]["incidents"][0]["unavailable_airport_code"]=="OTP"
    wrong={**fresh,"lookup_id":"lookup_wrong_leg","matches":[{**fresh["matches"][0],"selection_id":"different-selection","fr24_id":"abcdef13"}]}
    store.save_flight_lookup("wrong-query",wrong)
    before=store.read().version
    with pytest.raises(DomainError,match="same exact flight"):
        service.refresh(created["incident_id"],refresh.model_copy(update={"expected_version":before,"idempotency_key":"refresh-wrong-flight","lookup_id":wrong["lookup_id"],"selection_id":"different-selection"}))
    assert store.read().version==before
    await client.aclose()


@pytest.mark.asyncio
async def test_lookup_concurrent_duplicates_bill_only_once(setup):
    _,store,service,rows,calls,client=setup
    entered,release=asyncio.Event(),asyncio.Event()
    original=service.flights.lookup
    async def blocked(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    service.flights.lookup=blocked
    first=asyncio.create_task(lookup(setup))
    await entered.wait()
    second=await lookup(setup)
    assert second["state"]=="error" and second["code"]=="lookup_in_progress"
    release.set()
    assert (await first)["state"]=="ready"
    assert (await lookup(setup))["cached"] and len(calls)==store.poll_usage()==1
    await client.aclose()


@pytest.mark.asyncio
async def test_backup_removes_flight_lookup_cache_and_preserves_case(setup,tmp_path):
    _,store,service,_,_,client=setup
    result=await lookup(setup)
    created=service.create(create_request(store,result))
    destination=tmp_path/"backup.sqlite3"
    script=Path(__file__).resolve().parents[2]/"scripts"/"backup.py"
    output=subprocess.run([sys.executable,str(script),store.path,str(destination)],capture_output=True,text=True,timeout=10)
    assert output.returncode==0,output.stderr
    assert b"PRIVATE-TAIL" not in destination.read_bytes()
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT count(*) FROM flight_lookups").fetchone()[0]==0
        case=json.loads(conn.execute("SELECT data FROM world").fetchone()[0])["incidents"][0]
        assert case["id"]==created["incident_id"] and case["flight"] is None
    assert store.read().incidents[0].flight.registration=="PRIVATE-TAIL"
    await client.aclose()


def test_incident_api_auth_and_missing_provider_are_explicit(tmp_path):
    settings=Settings(_env_file=None,operation_mode="live",database_path=str(tmp_path/"api.sqlite3"),operator_token="x"*40)
    app=create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/flights/lookup",json={"flight_number":"RO321","date_utc":utcnow().date().isoformat()}).status_code==401
        assert client.post("/api/incidents",json={}).status_code==401
        assert client.post("/api/auth/login",json={"token":"x"*40}).status_code==200
        result=client.post("/api/flights/lookup",json={"flight_number":"RO321","date_utc":utcnow().date().isoformat()})
        assert result.status_code==200 and result.json()["state"]=="not_configured"
        assert client.get("/api/world").json()["incidents"]==[]


def test_incident_create_refresh_and_binding_api_contract(setup):
    settings,store,service,rows,_,provider_client=setup
    app=create_app(settings)
    app.state.incidents.flights=service.flights
    with TestClient(app) as client:
        selected=client.post("/api/flights/lookup",json={"flight_number":"RO321","date_utc":rows[0]["first_seen"][:10]})
        assert selected.status_code==200 and selected.json()["state"]=="ready"
        lookup_result=selected.json()
        request=create_request(store,lookup_result).model_dump(mode="json")
        created=client.post("/api/incidents",json=request)
        assert created.status_code==200,created.text
        incident_id=created.json()["incident_id"]
        assert created.json()["world"]["incidents"][0]["flight_context_state"]=="available"
        assert client.post("/api/incidents",json=request).json()["replayed"]
        version=store.read().version
        missing=client.post(f"/api/incidents/{incident_id}/groups",json={"expected_version":version,"idempotency_key":"api-missing-group","group_ids":["MISSING"]})
        assert missing.status_code==404 and store.read().version==version
        refreshed=client.post(f"/api/incidents/{incident_id}/refresh-flight",json={"expected_version":version,"idempotency_key":"api-context-refresh","lookup_id":lookup_result["lookup_id"],"selection_id":lookup_result["matches"][0]["selection_id"]})
        assert refreshed.status_code==200 and refreshed.json()["incident_id"]==incident_id
    asyncio.run(provider_client.aclose())


@pytest.mark.parametrize("pricing",[None,[],"legacy",{"status":"complete","mandatory_total":None},{"status":"complete","mandatory_total":[]}])
def test_legacy_hotel_pricing_cannot_crash_or_satisfy_hard_budget(setup,pricing):
    _,store,_,_,_,client=setup
    add_groups(store)
    world=store.read()
    hotel=world.resources[0].model_copy(update={"kind":"hotel","destination":"CRA","source":"Nuitee / LiteAPI","budget_total_cost":101,"provenance":{"pricing":pricing}})
    assert not resource_feasible(world,hotel,world.groups[0],"CRA",world.sim_time,set())
    asyncio.run(client.aclose())
