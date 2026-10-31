import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import AcceptRequest, Allocation, CommandRequest, EventRequest, ReplanRequest, ScenarioChange
from app.scenario_schema import MAX_COMMAND_EVENTS, MAX_GROUPS, MAX_RESOURCES
from app.seed import seed_world
from app.service import RecoveryService, apply_change
from app.service import candidate_rank
from app.solver import active_offers, validate_allocations
from app.store import DomainError, Store


def resource(**updates):
    return {"kind":"flight","name":"New Lisbon recovery service","origin":"CRA","destination":"LIS",
        "departure":780,"arrival":960,"capacity":90,"accessible":True,"cost_per_person":70.0,
        "room_capacity":2,"vehicle_id":None,"available":True,**updates}


def passengers(**updates):
    return {"name":"Lisbon families","passenger_count":10,"group_size":4,"origin":"CRA","destination":"LIS",
        "ready_at":600,"deadline":1200,"accessible":False,"budget_per_person":200.0,"original_flight_id":None,"priority":2,**updates}


def change(world,kind,target=None,value=None):
    apply_change(world,ScenarioChange(type=kind,target_id=target,value=value))


def service_at(tmp_path,ai=None):
    settings=Settings(database_path=str(tmp_path/"authoring.sqlite3"),_env_file=None)
    return RecoveryService(settings,Store(settings.database_path),ai)


def test_new_catalog_airport_resource_and_passenger_batch_preserve_existing_world():
    world=seed_world()
    originals=[g.model_dump() for g in world.groups]
    change(world,"airport_added","LPPT")
    airport=next(a for a in world.airports if a.id=="LIS")
    assert airport.available and airport.diversion_eligible and -10<airport.lon<-8
    change(world,"resource_added","F900",resource(destination="LPPT"))
    change(world,"passengers_added",value=passengers(destination="LPPT"))
    assert [g.model_dump() for g in world.groups[:len(originals)]]==originals
    added=world.groups[len(originals):]
    assert [g.size for g in added]==[4,4,2]
    assert len({g.id for g in world.groups})==len(world.groups)
    assert len({g.token for g in world.groups})==len(world.groups)
    assert all(len(g.token)>=32 and g.destination=="LIS" for g in added)
    assert [g.budget for g in added]==[800,800,400]
    assert all(len(g.members)==g.size for g in added)
    assert world.resources[-1].source=="synthetic" and world.observations==[]
    assert world.events[-1].data["group_ids"]==[g.id for g in added]
    assert all(g.token not in world.events[-1].model_dump_json() for g in added)


def test_custom_airport_and_hotel_have_explicit_location_and_room_inventory():
    world=seed_world()
    change(world,"airport_added","TEST",{"name":"Synthetic demonstration field","lat":44.2,"lon":24.1,"diversion_eligible":False})
    change(world,"resource_added","H-TEST",resource(kind="hotel",origin="TEST",destination="TEST",departure=1320,arrival=1860,capacity=17,room_capacity=3))
    assert world.airports[-1].id=="TEST" and not world.airports[-1].diversion_eligible
    assert world.resources[-1].capacity==17 and world.resources[-1].room_capacity==3
    assert world.events[-2].data["location_source"]=="custom_simulation"


@pytest.mark.parametrize("kind,target,value",[
    ("airport_added","NEVER",None),
    ("airport_added","CRA",None),
    ("resource_added","CRA",resource(destination="LTN")),
    ("resource_added","F900",resource()),
    ("resource_added","F900",resource(destination="LTN",arrival=780)),
    ("resource_added","F900",resource(destination="LTN",departure=300,arrival=500)),
    ("resource_added","B900",resource(kind="bus",destination="OTP",departure=599)),
    ("resource_added","H900",resource(kind="hotel",destination="OTP")),
    ("resource_added","F900",resource(origin="CRA",destination="CRA")),
    ("passengers_added",None,passengers(destination="LTN",ready_at=500)),
    ("passengers_added",None,passengers(destination="LTN",ready_at=1300)),
    ("passengers_added",None,passengers(destination="LTN",original_flight_id="B12")),
    ("passengers_added",None,passengers(destination="LTN",original_flight_id="F101")),
    ("group_destination","G001","UNKNOWN"),
    ("group_priority","G001",11),
    ("airport_diversion_eligibility","CRA","false"),
    ("resource_cost","B12",float("inf")),
    ("resource_cost","B12",True),
])
def test_invalid_authoring_operations_do_not_mutate_world(kind,target,value):
    world=seed_world()
    before=world.model_dump()
    with pytest.raises((DomainError,ValidationError)):
        change(world,kind,target,value)
    assert world.model_dump()==before


@pytest.mark.parametrize("event",[
    {"type":"resource_added","target_id":"F900","value":resource(capacity=True)},
    {"type":"resource_added","target_id":"F900","value":resource(source="live")},
    {"type":"resource_added","target_id":"F900","value":resource(arrival=10081)},
    {"type":"resource_added","target_id":"F900","value":resource(cost_per_person=float("nan"))},
    {"type":"passengers_added","target_id":"CHOSEN-ID","value":passengers()},
    {"type":"passengers_added","target_id":None,"value":passengers(token="model-chosen-secret")},
    {"type":"airport_added","target_id":"TEST","value":{"name":"Test","lat":91.0,"lon":0.0,"diversion_eligible":True}},
    {"type":"run_python","target_id":None,"value":"world.budget=0"},
])
def test_authoring_api_rejects_wrong_types_extra_fields_and_nonfinite_values(event):
    with pytest.raises(ValidationError):
        ScenarioChange.model_validate(event)


def test_world_caps_are_checked_before_appending_any_records():
    world=seed_world()
    world.groups.extend(world.groups[0].model_copy(update={"id":f"TEST-{i}"}) for i in range(MAX_GROUPS-len(world.groups)))
    with pytest.raises(DomainError,match="Scenario limit"):
        change(world,"passengers_added",value=passengers(destination="LTN"))
    assert len(world.groups)==MAX_GROUPS
    world.resources.extend(world.resources[0].model_copy(update={"id":f"RES-{i}"}) for i in range(MAX_RESOURCES-len(world.resources)))
    with pytest.raises(DomainError,match="Scenario limit"):
        change(world,"resource_added","F900",resource(destination="LTN"))
    assert len(world.resources)==MAX_RESOURCES


def test_airborne_schedule_changes_update_passenger_readiness_without_teleportation():
    world=seed_world()
    change(world,"resource_schedule","F201",{"departure":600,"arrival":800})
    inbound=[g for g in world.groups if g.original_flight_id=="F201"]
    assert all(g.origin=="CRA" and g.ready_at==830 for g in inbound)
    before=world.model_dump()
    with pytest.raises(DomainError,match="airborne flight"):
        change(world,"resource_schedule","F201",{"departure":700,"arrival":900})
    with pytest.raises(DomainError,match="flight_diverted"):
        change(world,"resource_route","F201",{"origin":"LTN","destination":"OTP"})
    assert world.model_dump()==before


def test_airport_reopening_preserves_cancelled_services_until_explicit_restoration():
    world=seed_world()
    change(world,"airport_closed","CRA")
    change(world,"airport_restored","CRA")
    assert next(a for a in world.airports if a.id=="CRA").available
    assert not next(r for r in world.resources if r.id=="F110").available
    change(world,"resource_restored","F110")
    assert next(r for r in world.resources if r.id=="F110").available
    change(world,"airport_diversion_eligibility","OTP",False)
    assert not next(a for a in world.airports if a.id=="OTP").diversion_eligible


def test_new_airborne_flight_manifest_requires_post_landing_readiness():
    world=seed_world()
    change(world,"resource_added","F900",resource(origin="LTN",destination="CRA",departure=580,arrival=760,capacity=10))
    with pytest.raises(DomainError,match="30 minutes"):
        change(world,"passengers_added",value=passengers(origin="CRA",destination="OTP",original_flight_id="F900",ready_at=760))
    change(world,"passengers_added",value=passengers(origin="CRA",destination="OTP",original_flight_id="F900",ready_at=790))
    assert sum(g.size for g in world.groups if g.original_flight_id=="F900")==10
    with pytest.raises(DomainError,match="manifest"):
        change(world,"passengers_added",value=passengers(origin="CRA",destination="OTP",original_flight_id="F900",ready_at=790,passenger_count=1))


def test_future_incoming_flight_reroute_and_delay_keep_passengers_at_landing_airport():
    world=seed_world()
    change(world,"resource_added","F900",resource(origin="LTN",destination="CRA",departure=700,arrival=900,capacity=10))
    with pytest.raises(DomainError,match="after their original flight lands"):
        change(world,"passengers_added",value=passengers(origin="CRA",destination="CRA",original_flight_id="F900",ready_at=600))
    change(world,"passengers_added",value=passengers(origin="CRA",destination="CRA",original_flight_id="F900",ready_at=930))
    change(world,"resource_route","F900",{"origin":"LTN","destination":"OTP"})
    change(world,"resource_schedule","F900",{"departure":750,"arrival":950})
    added=[g for g in world.groups if g.original_flight_id=="F900"]
    assert all(g.origin=="OTP" and g.destination=="CRA" and g.ready_at==980 and g.status=="waiting" for g in added)


def test_future_incoming_reroute_to_passenger_destination_removes_recovery_need():
    world=seed_world()
    change(world,"resource_added","F900",resource(origin="LTN",destination="CRA",departure=700,arrival=900,capacity=10))
    change(world,"passengers_added",value=passengers(origin="CRA",destination="OTP",original_flight_id="F900",ready_at=930))
    change(world,"resource_route","F900",{"origin":"LTN","destination":"OTP"})
    added=[g for g in world.groups if g.original_flight_id=="F900"]
    assert all(g.origin==g.destination=="OTP" and g.ready_at==930 and g.status=="scheduled" for g in added)


@pytest.mark.parametrize("kind,value",[
    ("resource_schedule",{"departure":700,"arrival":940}),
    ("resource_route",{"origin":"CRA","destination":"SBZ"}),
    ("resource_cost",50),
])
def test_resource_edits_release_only_dependent_confirmations(tmp_path,kind,value):
    service=service_at(tmp_path)
    asyncio.run(service.replan(ReplanRequest(expected_version=1,idempotency_key="initial-authoring-plan")))
    world=service.store.read()
    affected=next(o for o in active_offers(world) if any(l.resource_id=="B12" for l in o.itinerary.legs))
    unaffected=next(o for o in active_offers(world) if all(l.resource_id!="B12" for l in o.itinerary.legs))
    for offer in (affected,unaffected):
        group=next(g for g in world.groups if g.id==offer.group_id)
        service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="accept-"+offer.id))
    service.event(EventRequest(expected_version=service.store.read().version,idempotency_key="edit-resource",type=kind,target_id="B12",value=value))
    current=service.store.read()
    assert next(o for o in current.offers if o.id==affected.id).status=="invalidated"
    assert next(o for o in current.offers if o.id==unaffected.id).status=="confirmed"
    assert all(r.status=="released" for r in current.reservations if r.offer_id==affected.id)
    assert validate_allocations(current,[Allocation(group_id=o.group_id,itinerary=o.itinerary) for o in active_offers(current)],include_confirmed=False)["valid"]


class AuthoringAI:
    def __init__(self,events):
        self.events=events
        self.contexts=[]
        self.choices=0

    async def interpret(self,text,world,context=""):
        self.contexts.append(context)
        return {"events":self.events,"strategies":[],"explanation":"Created the requested synthetic scenario.","focus_id":"LIS"}

    async def choose(self,world,candidates,request):
        self.choices+=1
        self.projected=world
        return {"plan_id":candidates[0]["id"],"explanation":"Selected a validated recovery plan.","provider":"test-double"}


def test_freeform_command_creates_and_allocates_new_entities_atomically_with_context(tmp_path):
    ai=AuthoringAI([
        {"type":"airport_added","target_id":"LIS","value":None},
        {"type":"resource_added","target_id":"F900","value":resource()},
        {"type":"passengers_added","target_id":None,"value":passengers()},
    ])
    service=service_at(tmp_path,ai)
    before=service.store.read()
    request=CommandRequest(expected_version=1,idempotency_key="author-freeform",text="Add the Lisbon flight and those ten passengers.",context="User: ten passengers need to reach Lisbon today.")
    result=asyncio.run(service.command(request))
    current=service.store.read()
    assert current.version==2 and current.scenario_id==before.scenario_id
    assert ai.contexts==[request.context] and ai.choices==1
    created=current.groups[len(before.groups):]
    assert [g.id for g in created]==[g["id"] for g in ai.projected["groups"][len(before.groups):]]
    assert all(any(o.group_id==g.id and o.status=="held" for o in current.offers) for g in created)
    assert "Created the requested" in result["decision"]["explanation"]
    assert len(current.resources)==len(before.resources)+1
    assert asyncio.run(service.command(request))["replayed"] and ai.choices==1
    assert len(service.store.read().groups)==len(current.groups)


def test_invalid_later_command_event_rolls_back_all_earlier_additions(tmp_path):
    ai=AuthoringAI([
        {"type":"airport_added","target_id":"LIS","value":None},
        {"type":"passengers_added","target_id":None,"value":passengers()},
        {"type":"resource_added","target_id":"F900","value":resource(origin="UNKNOWN")},
    ])
    service=service_at(tmp_path,ai)
    before=service.store.read().model_dump(exclude={"stats"})
    with pytest.raises(DomainError):
        asyncio.run(service.command(CommandRequest(expected_version=1,idempotency_key="atomic-author-fail",text="Compose a scenario")))
    assert service.store.read().model_dump(exclude={"stats"})==before
    assert ai.choices==0


def test_command_supports_more_than_twelve_changes_but_bounds_oversized_scenarios(tmp_path):
    ai=AuthoringAI([{"type":"group_priority","target_id":f"G{i:03}","value":5} for i in range(1,17)])
    service=service_at(tmp_path,ai)
    asyncio.run(service.command(CommandRequest(expected_version=1,idempotency_key="large-author-command",text="Prioritize sixteen families")))
    assert all(g.priority==5 for g in service.store.read().groups[:16])
    ai.events=[{"type":"group_priority","target_id":"G001","value":5}]*(MAX_COMMAND_EVENTS+1)
    version=service.store.read().version
    with pytest.raises(DomainError,match="At most 64"):
        asyncio.run(service.command(CommandRequest(expected_version=version,idempotency_key="oversize-command",text="Change many groups")))
    assert service.store.read().version==version and ai.choices==1


def test_unsupported_model_operation_fails_without_audit_or_state_change(tmp_path):
    ai=AuthoringAI([{"type":"invent_emergency","target_id":None,"value":"anything"}])
    service=service_at(tmp_path,ai)
    with pytest.raises(DomainError,match="invalid scenario operation"):
        asyncio.run(service.command(CommandRequest(expected_version=1,idempotency_key="unsupported-author",text="Do something unsupported")))
    assert service.store.read().version==1 and len(service.store.read().events)==1
    assert ai.choices==0


def test_all_diversion_airports_are_solved_before_eight_candidate_model_shortlist(tmp_path,monkeypatch):
    import app.service as service_module
    ai=AuthoringAI([])
    service=service_at(tmp_path,ai)
    def expand(world):
        for code in ("LIS","BUD","SOF","ATH","VIE","FRA","BER","CDG"):
            change(world,"airport_added",code)
        change(world,"airport_closed","CRA")
    service.store.mutate("expand-airport-test",{},1,expand)
    built=[]
    original=service_module.build_candidate
    def capture(world,strategy):
        plan=original(world,strategy)
        built.append(plan)
        return plan
    monkeypatch.setattr(service_module,"build_candidate",capture)
    result=asyncio.run(service.replan(ReplanRequest(expected_version=2,idempotency_key="many-airport-plan",mode="ai")))
    candidates=result["decision"]["candidates"]
    counts=result["decision"]["candidate_counts"]
    assert counts=={"computed":30,"validated":30,"shortlisted":8,"submitted":8}
    assert {p.strategy.preferred_airport for p in built}=={"OTP","SBZ","LIS","BUD","SOF","ATH","VIE","FRA","BER","CDG"}
    selected_ids={p["id"] for p in candidates}
    assert len(selected_ids)==8
    for objective in ("balanced","fastest","lowest_cost"):
        assert min(built,key=lambda p:candidate_rank(p,objective)).id in selected_ids
    assert "shortlisted 8" in result["decision"]["explanation"]
    audit=next(e for e in service.store.read().events if e.type=="plan_applied")
    assert audit.data["candidate_counts"]==counts


def test_diversion_candidates_cannot_return_airborne_flight_to_its_own_origin(tmp_path):
    ai=AuthoringAI([])
    service=service_at(tmp_path,ai)
    def expand(world):
        change(world,"airport_added","LIS")
        change(world,"resource_added","F900",resource(origin="LIS",destination="CRA",departure=580,arrival=760))
        change(world,"airport_closed","CRA")
    service.store.mutate("new-incoming-flight",{},1,expand)
    result=asyncio.run(service.replan(ReplanRequest(expected_version=2,idempotency_key="reject-origin-diversion",mode="ai")))
    assert result["decision"]["candidate_counts"]["computed"]==9
    assert result["decision"]["candidate_counts"]["validated"]==6
    assert all(plan["strategy"]["preferred_airport"]!="LIS" for plan in result["decision"]["candidates"])
