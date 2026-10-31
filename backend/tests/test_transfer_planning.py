"""Planning imports use server receipts and exact scoped input; no external calls."""
import asyncio
import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.hotel_location import property_location
from app.main import create_app
from app.models import Airport, PassengerGroup
from app.providers.base import ProviderError
from app.store import DomainError, Store
from app.transfer_planning import GroundPlanning, PlanningTransferImport, PlanningTransferRequest


def initialize(store):
    def build(world):
        world.airports.append(Airport(id="LHR",code="LHR",name="London Heathrow Airport",lat=51.47002,lon=-0.454295))
        node, location = property_location({"id":"hotel-source-id","name":"Sourced property","latitude":51.53,"longitude":-0.29})
        world.airports.append(node)
        from app.models import Resource
        # Expired room prices do not erase independently sourced geography.
        world.resources.append(Resource(id="OLD-HOTEL",kind="hotel",name=node.name,origin=node.id,destination=node.id,departure=10,arrival=20,capacity=1,cost_per_person=0,
            source="Nuitee / LiteAPI",available=False,provenance={"property_location":location}))
        world.groups.append(PassengerGroup(id="G1",name="Party",size=2,members=["A","B"],adult_count=2,origin="LHR",destination="CRA",ready_at=0,deadline=6000,budget=500,original_flight_id="INPUT",token="token"*10,source="operator_manifest"))
        world.budget=1000
    store.mutate("setup-test",{},1,build)
    return next(item.id for item in store.read().airports if item.kind=="hotel")


class Sources:
    def __init__(self, now):
        self.now=now
        self.provider=SimpleNamespace(configured=True)
        self.google_configured=True
        self.calls=[]
        self.metadata_error=None
        self.route_error=None
        self.terminal=None
        self.dropoff=None
        self.resolved_address=None
        self.city="London"
        self.snap_offset=0
        self.address="628 Western Avenue, London W3 0TA"
        self.price="80.00"
        self.gate=None

    async def hotel_location(self, identity):
        self.calls.append(("metadata",identity))
        if self.metadata_error:
            raise self.metadata_error
        return {"latitude":51.53,"longitude":-0.29,"postal_address":{"hotel_id":identity,"name":"Sourced property","address":self.address,"city":self.city,"country":"GB","source":"Nuitee / LiteAPI","retrieved_at":self.now().isoformat()}}

    async def quote(self, request):
        self.calls.append(("apl",request))
        if self.gate:
            await self.gate.wait()
        now=self.now().isoformat()
        day=self.now().astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()
        return {"provider":"Airport Pickups London","source_url":"https://mcp.airport-pickups-london.com/docs","state":"ready","transport":"private_transfer",
            "received_at":now,"source_quoted_at":None,"expires_at":None,"request":{**request.model_dump(mode="json"),"pickup_at":request.pickup_at.astimezone(timezone.utc).isoformat()},
            "resolved_route":{"pickup":request.origin,"dropoff":self.dropoff or request.destination,"resolved_address":self.resolved_address,"hub":"LHR","direction":"from_hub","pickup_terminal":self.terminal},
            "options":[{"id":"car-1","car_type":"Saloon","price":{"amount":self.price,"currency":"GBP"},"price_scope":"per_vehicle","max_passengers":4,"max_suitcases":4,"fits_request":request.suitcases<=4,
                "availability_status":"quoted_not_reserved","accessibility_confirmed":False,"eur_reference_estimate":{"amount":f'{float(self.price)/.8:.2f}',"currency":"EUR","kind":"ecb_reference_estimate","rounding":"half_even_to_cent"}}],
            "fx":{"state":"ready","source":"European Central Bank","source_url":"https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml","settlement_guaranteed":False,"revalidation_required":True,
                "reference":{"source":"European Central Bank","source_url":"https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml","base_currency":"EUR","rate_date":day,"retrieved_at":now,"as_of":now,"age_calendar_days":0,"age_timezone":"Europe/Berlin","max_age_days":3,"source_units_per_eur":"0.8"}}}

    async def dated_route(self, origin, destination, pickup):
        self.calls.append(("route",origin,destination,pickup))
        if self.route_error:
            raise self.route_error
        return {"source":"Google Routes","source_url":"https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes","fetched_at":self.now().isoformat(),
            "request":{"origin":origin,"destination":destination,"departure_time":pickup.isoformat(),"travel_mode":"DRIVE","routing_preference":"TRAFFIC_AWARE"},
            "duration_seconds":"3600.000000001","distance_meters":18000,"duration_guaranteed":False,"road_endpoints":{"origin":{**origin,"latitude":origin["latitude"]+self.snap_offset},"destination":destination},"warnings":[],"encoded_polyline":None}


@pytest.fixture
def setup(tmp_path):
    store=Store(str(tmp_path/"operation.sqlite3"),operation_mode="live")
    hotel_id=initialize(store)
    clock=[datetime.now(timezone.utc).replace(second=0,microsecond=0)]
    sources=Sources(lambda:clock[0])
    service=GroundPlanning(store,sources,sources,now=lambda:clock[0])
    def request(key="quote-test-1",**changes):
        return PlanningTransferRequest(**({"group_id":"G1","hotel_node_id":hotel_id,"pickup_at":clock[0]+timedelta(days=1),"suitcases":2,
            "initial_point":{"label":"Terminal 5 arrivals meeting point","latitude":51.47,"longitude":-0.48,"confirmed":True},
            "expected_version":store.read().version,"idempotency_key":key}|changes))
    return store,sources,service,request,clock


def import_request(store,key="import-test-1",**changes):
    return PlanningTransferImport(**({"option_id":"car-1","policy_id":"ecb_reference_v1","expected_version":store.read().version,"idempotency_key":key}|changes))


@pytest.mark.asyncio
async def test_scoped_quote_import_preserves_origin_and_exact_money_and_rounds_arrival_up(setup):
    store,sources,service,request,clock=setup
    before=store.read().model_dump(mode="json")
    quote=await service.quote(request())
    assert quote["state"]=="ready",quote
    assert store.read().model_dump(mode="json")["version"]==before["version"]
    assert [item[0] for item in sources.calls]==["metadata","apl","route"]
    assert "628 Western Avenue" in sources.calls[1][1].destination
    result=service.import_quote(quote["quote_id"],import_request(store))
    world=store.read()
    resource=next(item for item in world.resources if item.id==result["resource_ids"][0])
    assert world.groups[0].origin=="LHR" and world.groups[0].status=="waiting"
    assert resource.origin.startswith("MP-") and resource.destination==request().hotel_node_id
    assert len(world.offers)==0 and len(world.reservations)==0
    assert resource.arrival-resource.departure==61
    assert resource.capacity==2 and resource.eligible_group_ids==["G1"] and resource.vehicle_id is None
    assert resource.price_evidence.original.amount=="80.00" and resource.budget_total_cost==100
    assert resource.quote_total_cost is None and resource.valid_until is None
    assert resource.quote_freshness.basis=="app_receipt"
    assert resource.provenance["apl_quote"]["source_quoted_at"] is None
    assert resource.provenance["google_route"]["request"]["origin"]=={"latitude":51.47,"longitude":-0.48}
    assert resource.initial_position_assumption.source=="operator_declared"
    # A completed import remains replayable after its transient source cache expires.
    original=import_request(store,expected_version=before["version"])
    clock[0]+=timedelta(seconds=121)
    service.purge()
    repeated=service.import_quote(quote["quote_id"],original)
    assert repeated["replayed"] is True and repeated["resource_ids"]==result["resource_ids"]


@pytest.mark.asyncio
async def test_completed_search_replay_across_instances_never_repeats_sources(setup):
    store,sources,service,request,clock=setup
    body=request()
    first=await service.quote(body)
    new=GroundPlanning(store,sources,sources,now=lambda:clock[0])
    replay=await new.quote(body)
    assert replay["quote_id"]==first["quote_id"] and replay["replayed"] is True
    assert len(sources.calls)==3
    with pytest.raises(DomainError) as conflict:
        await new.quote(request(suitcases=3))
    assert conflict.value.status==409 and len(sources.calls)==3
    clock[0]+=timedelta(seconds=121)
    new.purge()
    with pytest.raises(DomainError) as expired:
        await new.quote(body)
    assert expired.value.status==409 and len(sources.calls)==3


@pytest.mark.asyncio
async def test_concurrent_search_shares_durable_receipt_without_duplicate_dispatch(setup):
    store,sources,service,request,clock=setup
    sources.gate=asyncio.Event()
    body=request()
    task=asyncio.create_task(service.quote(body))
    while not any(item[0]=="apl" for item in sources.calls):
        if task.done(): task.result()
        await asyncio.sleep(.001)
    second=GroundPlanning(store,sources,sources,now=lambda:clock[0])
    with pytest.raises(DomainError) as pending:
        await second.quote(body)
    assert pending.value.status==503 and pending.value.applied is None
    sources.gate.set()
    result=await task
    assert result["state"]=="ready" and len(sources.calls)==3


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code",[("missing_address","property_address_missing"),("metadata_error","hotel_timeout"),("route_error","route_timeout"),("terminal_conflict","supplier_terminal_conflict"),("address_conflict","supplier_address_unverified"),("far_road","road_connection_unverified"),("bags","party_or_luggage")])
async def test_unproven_inputs_block_import_and_preserve_world(setup,case,code):
    store,sources,service,request,clock=setup
    if case=="missing_address": sources.address=None
    if case=="metadata_error": sources.metadata_error=ProviderError("Metadata timed out",provider="LiteAPI",code="timeout")
    if case=="route_error": sources.route_error=ProviderError("Route timed out",provider="Google Routes",code="timeout")
    if case=="terminal_conflict": sources.terminal="Terminal 2"
    if case=="address_conflict": sources.dropoff="A different street, London"
    if case=="far_road": sources.snap_offset=.002
    version=store.read().version
    quote=await service.quote(request(suitcases=5 if case=="bags" else 2))
    assert quote["state"]=="blocked"
    assert code in {item["code"] for item in quote["blockers"]+[reason for option in quote["options"] for reason in option["blockers"]]}
    if case in ("missing_address","metadata_error"):
        assert [item[0] for item in sources.calls]==["metadata"]
    if case=="route_error": assert quote["transfer"]["options"][0]["price"]["currency"]=="GBP"
    with pytest.raises(DomainError):
        service.import_quote(quote["quote_id"],import_request(store))
    assert store.read().version==version and len(store.read().resources)==1


@pytest.mark.asyncio
async def test_changed_group_or_stale_version_cannot_import_a_paid_quote(setup):
    store,sources,service,request,clock=setup
    quote=await service.quote(request())
    old_version=store.read().version
    store.mutate("constraint-test",{},old_version,lambda world:setattr(world.groups[0],"budget",450))
    with pytest.raises(DomainError) as stale:
        service.import_quote(quote["quote_id"],import_request(store,expected_version=old_version))
    assert stale.value.status==409
    with pytest.raises(DomainError) as changed:
        service.import_quote(quote["quote_id"],import_request(store))
    assert changed.value.status==409 and len(store.read().resources)==1


@pytest.mark.asyncio
async def test_repricing_creates_immutable_revision_and_retires_only_same_group(setup):
    store,sources,service,request,clock=setup
    first=await service.quote(request())
    first_id=service.import_quote(first["quote_id"],import_request(store))["resource_ids"][0]
    original=next(item for item in store.read().resources if item.id==first_id).model_dump(mode="json")
    sources.price="88.00"
    next_quote=await service.quote(request("quote-test-2"))
    second_id=service.import_quote(next_quote["quote_id"],import_request(store,"import-test-2"))["resource_ids"][0]
    resources={item.id:item for item in store.read().resources}
    assert second_id!=first_id and resources[second_id].budget_total_cost==110
    assert resources[first_id].model_dump(mode="json")=={**original,"available":False}


@pytest.mark.asyncio
async def test_unrelated_confirmed_booking_is_byte_identical_after_import(setup):
    from app.models import Resource, Offer, Itinerary, Leg, Reservation
    store,sources,service,request,clock=setup
    quote=await service.quote(request())
    def add_other(world):
        group=world.groups[0].model_copy(deep=True)
        group.id="G2"
        group.status="confirmed"
        group.token="other-token"
        world.groups.append(group)
        resource=Resource(id="OTHER-FLIGHT",kind="flight",name="Existing booking",origin="LHR",destination="CRA",departure=2000,arrival=2100,capacity=2,cost_per_person=100,
            source="Nuitee Flights",eligible_group_ids=["G2"],booking_status="confirmed",quote_total_cost=200)
        world.resources.append(resource)
        itinerary=Itinerary(legs=[Leg(resource_id=resource.id,kind="flight",origin="LHR",destination="CRA",departure=2000,arrival=2100,cost=200)],arrival=2100,total_cost=200)
        world.offers.append(Offer(id="OTHER-OFFER",group_id="G2",itinerary=itinerary,status="confirmed",expires_at="2090-01-01T00:00:00Z",created_version=world.version,external_bookings=[{"reference":"existing-confirmation"}]))
        world.reservations.append(Reservation(id="OTHER-RESERVATION",offer_id="OTHER-OFFER",group_id="G2",resource_id=resource.id,quantity=2,status="confirmed",expires_at="2090-01-01T00:00:00Z"))
    store.mutate("unrelated-booking",{},store.read().version,add_other)
    before=store.read().model_dump(mode="json")
    service.import_quote(quote["quote_id"],import_request(store))
    after=store.read().model_dump(mode="json")
    assert before["offers"]==after["offers"] and before["reservations"]==after["reservations"]
    assert before["groups"]==after["groups"]
    assert next(item for item in before["resources"] if item["id"]=="OTHER-FLIGHT")==next(item for item in after["resources"] if item["id"]=="OTHER-FLIGHT")


@pytest.mark.asyncio
async def test_unknown_dispatch_and_cancelled_sources_never_automatically_repeat(setup):
    store,sources,service,request,clock=setup
    sources.gate=asyncio.Event()
    body=request()
    task=asyncio.create_task(service.quote(body))
    while not any(item[0]=="apl" for item in sources.calls):
        if task.done(): task.result()
        await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    with pytest.raises(DomainError) as unknown:
        await service.quote(body)
    assert unknown.value.applied is None and unknown.value.details["unknown_outcome"] is True
    assert len(sources.calls)==3


@pytest.mark.asyncio
async def test_physical_node_limit_rolls_back_quote_retirement_and_import(setup):
    from app.scenario_schema import MAX_AIRPORTS
    store,sources,service,request,clock=setup
    quote=await service.quote(request())
    def fill(world):
        while len(world.airports)<MAX_AIRPORTS:
            name=f"LIMIT-{len(world.airports)}"
            world.airports.append(Airport(id=name,code=name,name=name,lat=51,lon=0,kind="station"))
    store.mutate("fill-reference-nodes",{},store.read().version,fill)
    before=store.read().model_dump(mode="json")
    with pytest.raises(DomainError) as full:
        service.import_quote(quote["quote_id"],import_request(store))
    assert full.value.status==422
    after=store.read().model_dump(mode="json")
    for field in ("version","airports","resources","offers","reservations","groups"):
        assert before[field]==after[field]


@pytest.mark.asyncio
async def test_far_airport_point_and_ambiguous_terminal_fail_before_any_source_call(setup):
    store,sources,service,request,clock=setup
    for point in ({"label":"Heathrow Terminal 5","latitude":53.48,"longitude":-2.24,"confirmed":True},
                  {"label":"Terminal 2 or T5","latitude":51.47,"longitude":-.48,"confirmed":True}):
        with pytest.raises(DomainError) as invalid:
            await service.quote(request(initial_point=point))
        assert invalid.value.status==422
    assert sources.calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("stated,returned",[("Heathrow Terminal 5 arrivals","T5"),("T5 arrivals","Terminal 5"),("North Terminal meeting point","North"),("South Terminal arrivals","Terminal South")])
async def test_explicit_terminal_synonyms_preserve_source_scope(setup,stated,returned):
    store,sources,service,request,clock=setup
    sources.terminal=returned
    quote=await service.quote(request(initial_point={"label":stated,"latitude":51.47,"longitude":-.48,"confirmed":True}))
    assert quote["state"]=="ready",quote
    assert quote["geographic_screening"]["terminal_or_pedestrian_access_verified"] is False


@pytest.mark.asyncio
async def test_older_cached_fare_cannot_replace_an_imported_newer_fare(setup):
    store,sources,service,request,clock=setup
    older=await service.quote(request())
    sources.price="88.00"
    newer=await service.quote(request("quote-test-newer"))
    new_id=service.import_quote(newer["quote_id"],import_request(store))["resource_ids"][0]
    version=store.read().version
    with pytest.raises(DomainError) as stale:
        service.import_quote(older["quote_id"],import_request(store,"old-import-key"))
    assert stale.value.status==409 and store.read().version==version
    assert next(item for item in store.read().resources if item.id==new_id).available is True


@pytest.mark.asyncio
async def test_imported_point_is_first_leg_only_and_requires_real_onward_inventory(setup):
    from app.models import Resource, Strategy
    from app.solver import build_candidate
    store,sources,service,request,clock=setup
    quote=await service.quote(request())
    identity=service.import_quote(quote["quote_id"],import_request(store))["resource_ids"][0]
    world=store.read()
    assert build_candidate(world,Strategy(),scope_group_ids={"G1"}).recovered_passengers==0
    imported=next(item for item in world.resources if item.id==identity)
    def onward(current):
        current.resources.append(Resource(id="SOURCED-ONWARD",kind="bus",name="Existing sourced onward service",origin=imported.destination,destination="CRA",
            departure=imported.arrival+20,arrival=imported.arrival+1820,capacity=2,cost_per_person=100,quote_total_cost=200,
            source="Fixture transport supplier",valid_until=(clock[0]+timedelta(hours=1)).isoformat(),eligible_group_ids=["G1"]))
    store.mutate("existing-onward-inventory",{},world.version,onward)
    plan=build_candidate(store.read(),Strategy(),scope_group_ids={"G1"})
    assert plan.recovered_passengers==2 and plan.validation["valid"] is True
    leg=plan.allocations[0].itinerary.legs[0]
    assert leg.origin.startswith("MP-") and leg.price_evidence.original.currency=="GBP"
    assert leg.initial_position_assumption.source=="operator_declared"
    assert plan.cost_evidence is not None and store.read().groups[0].origin=="LHR"


@pytest.mark.asyncio
@pytest.mark.parametrize("returned,ready",[("11 High Street, London W30TA",False),("1 High Street, London W30TB",False),("Sourced property, 1 High Street, London W30TA, United Kingdom",True)])
async def test_property_address_match_preserves_street_number_and_postcode_boundaries(setup,returned,ready):
    store,sources,service,request,clock=setup
    sources.address="1 High Street, London W3 0TA"
    sources.dropoff=returned
    quote=await service.quote(request())
    assert (quote["state"]=="ready")==ready
    if not ready:
        assert any(item["code"]=="supplier_address_unverified" for item in quote["blockers"])


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved,dropoff",[(None,"1 High Street, London"),("1 High Street, Southampton","1 High Street, London"),("1 High Street, London","1 High Street, Southampton")])
async def test_source_city_and_each_returned_destination_must_reconcile(setup,resolved,dropoff):
    store,sources,service,request,clock=setup
    sources.address="1 High Street"
    sources.city="Southampton"
    sources.resolved_address=resolved
    sources.dropoff=dropoff
    quote=await service.quote(request())
    assert quote["state"]=="blocked"
    assert any(item["code"]=="supplier_address_unverified" for item in quote["blockers"])


@pytest.mark.asyncio
async def test_backup_removes_cache_preserves_source_and_prevents_restored_dispatch(setup,tmp_path):
    store,sources,service,request,clock=setup
    body=request()
    quote=await service.quote(body)
    second=request("quote-pending-test")
    service._reserve(second)
    destination=tmp_path/"backup.sqlite3"
    script=Path(__file__).resolve().parents[2]/"scripts"/"backup.py"
    subprocess.run([sys.executable,str(script),store.path,str(destination)],check=True,capture_output=True,text=True)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transfer_quote_cache").fetchone()[0]==1
        assert conn.execute("SELECT status FROM transfer_quote_receipts WHERE request_key=?",(second.idempotency_key,)).fetchone()[0]=="dispatched"
    restored=Store(str(destination),operation_mode="live")
    other=GroundPlanning(restored,sources,sources,now=lambda:clock[0])
    with pytest.raises(DomainError) as expired:
        await other.quote(body)
    with pytest.raises(DomainError) as unknown:
        await other.quote(second)
    assert expired.value.status==409 and unknown.value.applied is None
    assert len(sources.calls)==3


def test_planning_routes_require_operator_and_reject_client_price_authority(tmp_path):
    app=create_app(Settings(_env_file=None,database_path=str(tmp_path/"api.sqlite3"),operation_mode="live",operator_token="x"*40))
    with TestClient(app) as client:
        assert client.post("/api/transfers/planning-quotes",json={}).status_code==401
        assert client.post("/api/transfers/planning-quotes/TQ-any/import",json={}).status_code==401
        response=client.post("/api/transfers/planning-quotes/TQ-any/import",headers={"Authorization":"Bearer "+"x"*40},json={"expected_version":1,"idempotency_key":"import-test-1","option_id":"car-1","policy_id":"ecb_reference_v1","price":1})
        assert response.status_code==422
