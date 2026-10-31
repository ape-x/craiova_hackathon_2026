"""Hotel returns use sourced flights and declared allowances; no provider I/O."""
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.live_travel import append_travel_quote
from app.models import Strategy
from app.solver import build_candidate
from app.store import DomainError, Store
from app.transfer_planning import PlanningTransferRequest, build_transfer_resource
from test_transfer_planning import setup, import_request


@pytest.fixture
def returning(setup):
    store,sources,service,initial,clock=setup
    departure=initial().pickup_at+timedelta(hours=4)
    quote={"provider":"nuitee","provider_offer_id":"source-flight-one","mode":"live","passengers":2,
        "origin":"LHR","destination":"CRA","departure_at":departure.isoformat(),"arrival_at":(departure+timedelta(hours=3)).isoformat(),
        "currency":"EUR","total_price":"120.00","quoted_at":clock[0].isoformat(),"expires_at":(clock[0]+timedelta(hours=1)).isoformat(),
        "segments":[{"origin":"LHR","destination":"CRA","departure_at":departure.isoformat(),"arrival_at":(departure+timedelta(hours=3)).isoformat()}]}
    def add(world):
        return append_travel_quote(world,quote,{"origin_code":"LHR","destination_code":"CRA"},world.groups[0],"flight")
    onward_id=store.mutate("source-onward-flight",{},store.read().version,add)["resource_ids"][0]
    original=sources.quote
    async def quote_direction(request):
        result=await original(request)
        result["resolved_route"]["direction"]=request.expected_direction
        return result
    sources.quote=quote_direction
    def request(key="return-quote-1",**changes):
        body=initial(key).receipt_payload()
        body.pop("initial_point")
        body.update({"direction":"hotel_to_airport","onward_resource_id":onward_id,"airport_processing_minutes":90,
            "airport_dropoff":{"label":"Terminal 5 departure dropoff","latitude":51.47,"longitude":-.48,"confirmed":True}})
        return PlanningTransferRequest(**(body|changes))
    return store,sources,service,request,clock,initial,onward_id


@pytest.mark.asyncio
async def test_return_import_uses_exact_hotel_airport_flight_without_creating_a_meeting_node(returning):
    store,sources,service,request,clock,initial,onward_id=returning
    before=store.read().model_dump(mode="json")
    response=await service.quote(request())
    assert response["state"]=="ready",response
    assert response["direction"]=="hotel_to_airport" and response["initial_point"] is None
    assert response["airport_arrival_assumption"]["departure_terminal"] is None
    query=next(call[1] for call in sources.calls if call[0]=="apl")
    assert query.expected_direction=="to_hub" and query.expected_hub=="LHR"
    assert "628 Western Avenue" in query.origin and "Terminal 5" in query.destination
    route=next(call for call in sources.calls if call[0]=="route")
    assert route[1]=={"latitude":51.53,"longitude":-.29} and route[2]=={"latitude":51.47,"longitude":-.48}
    result=service.import_quote(response["quote_id"],import_request(store))
    world=store.read()
    resource=next(item for item in world.resources if item.id==result["resource_ids"][0])
    assert resource.origin==request().hotel_node_id and resource.destination=="LHR"
    assert resource.initial_position_assumption is None
    assert resource.airport_arrival_assumption.onward_resource_id==onward_id
    assert resource.airport_arrival_assumption.processing_minutes==90
    assert resource.price_evidence.original.currency=="GBP" and resource.budget_total_cost==100
    assert resource.arrival-resource.departure==61
    assert len(world.airports)==len(before["airports"]) and world.groups[0].origin=="LHR"
    assert world.model_dump(mode="json")["groups"]==before["groups"]
    assert world.offers==[] and world.reservations==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("case",["missing_flight","other_group","unavailable","synthetic","source_mismatch","expired","changed_departure","closed_airport","far_dropoff"])
async def test_invalid_return_context_fails_before_paid_sources(returning,case):
    store,sources,service,request,clock,initial,onward_id=returning
    changes={}
    if case=="missing_flight": changes["onward_resource_id"]="missing"
    elif case=="far_dropoff": changes["airport_dropoff"]={"label":"T5","latitude":53.48,"longitude":-2.24,"confirmed":True}
    else:
        def modify(world):
            flight=next(item for item in world.resources if item.id==onward_id)
            if case=="other_group": flight.eligible_group_ids=["G2"]
            if case=="unavailable": flight.available=False
            if case=="synthetic": flight.source="synthetic"
            if case=="source_mismatch": flight.provenance["origin"]="MAN"
            if case=="expired": flight.valid_until=(clock[0]-timedelta(seconds=1)).isoformat()
            if case=="changed_departure": flight.departure+=1
            if case=="closed_airport": next(item for item in world.airports if item.id=="LHR").operational_status="closed"
        store.mutate("change-source",{},store.read().version,modify)
    with pytest.raises(DomainError):
        await service.quote(request(**changes))
    assert sources.calls==[]


@pytest.mark.asyncio
async def test_operator_processing_time_is_checked_against_real_driving_estimate(returning):
    store,sources,service,request,clock,initial,onward_id=returning
    response=await service.quote(request(airport_processing_minutes=180))
    # Pickup to departure is 240 minutes; sourced fractional duration rounds to61.
    assert response["state"]=="blocked"
    assert "airport_processing_time" in {item["code"] for item in response["blockers"]}
    before=store.read().version
    with pytest.raises(DomainError): service.import_quote(response["quote_id"],import_request(store))
    assert store.read().version==before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["hotel_pickup","resolved_address","airport_dropoff","terminal_dropoff","wrong_direction"])
async def test_return_supplier_conflicts_cannot_be_imported(returning,kind):
    store,sources,service,request,clock,initial,onward_id=returning
    original=sources.quote
    async def conflicting(query):
        result=await original(query)
        values={"hotel_pickup":("pickup","11 High Street, Southampton"),"resolved_address":("resolved_address","A different property, London"),
            "airport_dropoff":("dropoff","Manchester Airport MAN"),"terminal_dropoff":("dropoff","London Heathrow Airport Terminal 2"),"wrong_direction":("direction","from_hub")}
        field,value=values[kind]
        result["resolved_route"][field]=value
        return result
    sources.quote=conflicting
    response=await service.quote(request())
    assert response["state"]=="blocked" and response["blockers"]
    with pytest.raises(DomainError): service.import_quote(response["quote_id"],import_request(store))


@pytest.mark.asyncio
async def test_changed_flight_source_blocks_atomic_import_without_rewriting_previous_resources(returning):
    store,sources,service,request,clock,initial,onward_id=returning
    response=await service.quote(request())
    def reprice(world):
        flight=next(item for item in world.resources if item.id==onward_id)
        flight.quote_total_cost=999
    store.mutate("changed-source-price",{},store.read().version,reprice)
    before=store.read().model_dump(mode="json")
    with pytest.raises(DomainError): service.import_quote(response["quote_id"],import_request(store))
    assert store.read().model_dump(mode="json")==before


@pytest.mark.asyncio
async def test_outbound_and_return_revisions_replace_only_their_own_direction(returning):
    store,sources,service,request,clock,initial,onward_id=returning
    older_return=await service.quote(request())
    outbound=await service.quote(initial("initial-quote"))
    outbound_id=service.import_quote(outbound["quote_id"],import_request(store,"initial-import"))["resource_ids"][0]
    # Newer opposite-direction quotes must not make the old return obsolete.
    return_id=service.import_quote(older_return["quote_id"],import_request(store,"return-import"))["resource_ids"][0]
    before=store.read()
    outbound_snapshot=next(item for item in before.resources if item.id==outbound_id).model_dump(mode="json")
    sources.price="88.00"
    new_return=await service.quote(request("return-quote-new"))
    new_return_id=service.import_quote(new_return["quote_id"],import_request(store,"return-import-new"))["resource_ids"][0]
    resources={item.id:item for item in store.read().resources}
    assert not resources[return_id].available and resources[new_return_id].available
    assert resources[outbound_id].model_dump(mode="json")==outbound_snapshot
    return_snapshot=resources[new_return_id].model_dump(mode="json")
    new_outbound=await service.quote(initial("initial-quote-new"))
    service.import_quote(new_outbound["quote_id"],import_request(store,"initial-import-new"))
    resources={item.id:item for item in store.read().resources}
    assert not resources[outbound_id].available
    assert resources[new_return_id].model_dump(mode="json")==return_snapshot


@pytest.mark.asyncio
async def test_pure_return_compilation_does_not_mutate_or_dispatch_and_cannot_complete_a_missing_hotel_connection(returning):
    store,sources,service,request,clock,initial,onward_id=returning
    response=await service.quote(request())
    with store.connect() as conn:
        cached=json.loads(conn.execute("SELECT data FROM transfer_quote_cache WHERE id=?",(response["quote_id"],)).fetchone()[0])
    world=store.read()
    # The return alone cannot teleport the group from its original point to HP.
    before=world.model_dump(mode="json")
    resource,node=build_transfer_resource(world,cached["context"],cached["response"],"car-1",now=clock[0])
    assert world.model_dump(mode="json")==before and node is None and len(sources.calls)==3
    projected=world.model_copy(deep=True)
    projected.resources.append(resource)
    plan=build_candidate(projected,Strategy(),scope_group_ids={"G1"})
    assert all(resource.id not in [leg.resource_id for leg in allocation.itinerary.legs] for allocation in plan.allocations)
    # The ordinary direct airport flight may be feasible; the return is unused.
    assert store.read().version==world.version


@pytest.mark.asyncio
async def test_deployed_outbound_receipt_payload_remains_backward_compatible(setup):
    store,sources,service,request,clock=setup
    body=request()
    legacy=body.model_dump(mode="json",exclude={"direction","airport_dropoff","onward_resource_id","airport_processing_minutes"})
    assert body.receipt_payload()==legacy
    result=await service.quote(body)
    with store.connect() as conn:
        stored=conn.execute("SELECT fingerprint FROM transfer_quote_receipts WHERE id=?",(result["quote_id"],)).fetchone()[0]
    assert stored==Store.fingerprint(legacy)
    assert (await service.quote(PlanningTransferRequest(**legacy)))["replayed"] is True
    assert len(sources.calls)==3


@pytest.mark.asyncio
async def test_complete_sourced_outbound_hotel_return_flight_chain_keeps_both_assumptions(returning):
    from app.models import Allocation, Resource
    from app.solver import itinerary_options, validate_allocations
    store,sources,service,request,clock,initial,onward_id=returning
    outgoing=await service.quote(initial("chain-outbound-quote"))
    outbound_id=service.import_quote(outgoing["quote_id"],import_request(store,"chain-outbound-import"))["resource_ids"][0]
    pickup=initial().pickup_at+timedelta(hours=2)
    incoming=await service.quote(request("chain-return-quote",pickup_at=pickup,airport_processing_minutes=30))
    return_id=service.import_quote(incoming["quote_id"],import_request(store,"chain-return-import"))["resource_ids"][0]
    def room(world):
        outbound=next(item for item in world.resources if item.id==outbound_id)
        returning_leg=next(item for item in world.resources if item.id==return_id)
        old=next(item for item in world.resources if item.id=="OLD-HOTEL")
        world.resources.append(Resource(id="SOURCED-ROOM",kind="hotel",name="Quoted room",origin=old.origin,destination=old.destination,
            departure=outbound.arrival+10,arrival=returning_leg.departure-10,capacity=1,quote_rooms=1,cost_per_person=0,
            quote_total_cost=50,budget_total_cost=50,source=old.source,eligible_group_ids=["G1"],valid_until=(clock[0]+timedelta(hours=1)).isoformat(),
            provenance={**old.provenance,"pricing":{"status":"complete","mandatory_total":{"amount":"50.00","currency":"EUR"}}}))
    store.mutate("sourced-fixture-room",{},store.read().version,room)
    world=store.read()
    itinerary=next(item for item in itinerary_options(world,world.groups[0]) if [leg.resource_id for leg in item.legs]==[outbound_id,"SOURCED-ROOM",return_id,onward_id])
    assert validate_allocations(world,[Allocation(group_id="G1",itinerary=itinerary)]) ["valid"] is True
    assert itinerary.legs[0].initial_position_assumption is not None
    assert itinerary.legs[2].initial_position_assumption is None
    assert itinerary.legs[2].airport_arrival_assumption.departure_terminal is None
    assert itinerary.legs[2].airport_arrival_assumption.onward_resource_id==onward_id
    assert itinerary.cost_evidence is not None and itinerary.total_cost==370


@pytest.mark.asyncio
async def test_pickup_instruction_conflict_blocks_outbound_but_does_not_assign_return_terminal(returning):
    store,sources,service,request,clock,initial,onward_id=returning
    original=sources.quote
    async def instructions(query):
        result=await original(query)
        result["meeting_point"]={"name":"Heathrow T2","instructions":"Supplier pickup instruction"}
        return result
    sources.quote=instructions
    outbound=await service.quote(initial("instruction-outbound"))
    assert outbound["state"]=="blocked"
    returned=await service.quote(request("instruction-return"))
    assert returned["state"]=="ready"
    assert returned["airport_arrival_assumption"]["departure_terminal"] is None
    assert returned["transfer"]["meeting_point"]["name"]=="Heathrow T2"


@pytest.mark.parametrize("changes",[{"initial_point":{"label":"T5","latitude":51.47,"longitude":-.48,"confirmed":True}},
    {"airport_processing_minutes":0},{"airport_processing_minutes":True},{"airport_dropoff":None},{"onward_resource_id":None}])
def test_return_request_requires_separate_explicit_inputs(returning,changes):
    *_,request,clock,initial,onward_id=returning
    with pytest.raises(ValidationError): request(**changes)
