import asyncio
import logging
import math
import random
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.config import Settings
from app.main import RedactAccessPaths
from app.models import AcceptRequest, Allocation, CommandRequest, EventRequest, Itinerary, Leg, ReplanRequest, ScenarioChange, Strategy
from app.seed import seed_world
from app.service import RecoveryService, apply_change
from app.solver import active_offers, build_candidate, default_strategies, itinerary_options, units, validate_allocations
from app.store import DomainError, Store, utcnow


def make_service(tmp_path):
    settings=Settings(database_path=str(tmp_path/"audit.sqlite3"),_env_file=None)
    return RecoveryService(settings,Store(settings.database_path))


def replan(service,key):
    return asyncio.run(service.replan(ReplanRequest(expected_version=service.store.read().version,idempotency_key=key)))


def event(service,key,kind,target,value=None):
    return service.event(EventRequest(expected_version=service.store.read().version,idempotency_key=key,type=kind,target_id=target,value=value))


def accept(service,offer,key):
    group=next(g for g in service.store.read().groups if g.id==offer.group_id)
    return service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key=key))


def assert_active_inventory(world):
    active=active_offers(world)
    check=validate_allocations(world,[Allocation(group_id=o.group_id,itinerary=o.itinerary) for o in active],include_confirmed=False)
    assert check["valid"],check["errors"]
    groups={g.id:g for g in world.groups}
    resources={r.id:r for r in world.resources}
    expected=Counter()
    for offer in active:
        for leg in offer.itinerary.legs:
            expected[leg.resource_id]+=units(resources[leg.resource_id],groups[offer.group_id])
    actual=Counter()
    for reservation in world.reservations:
        if reservation.status in ("held","confirmed"):
            actual[reservation.resource_id]+=reservation.quantity
    assert actual==expected


def test_capacity_reduction_keeps_confirmed_groups_before_holds(tmp_path):
    service=make_service(tmp_path)
    replan(service,"capacity-initial")
    world=service.store.read()
    dependent=[o for o in active_offers(world) if any(l.resource_id=="B12" for l in o.itinerary.legs)]
    assert len(dependent)>=3
    retained=dependent[:2]
    for i,offer in enumerate(retained):
        accept(service,offer,f"capacity-accept-{i}")
    world=service.store.read()
    required=sum(next(g.size for g in world.groups if g.id==o.group_id) for o in retained)
    event(service,"capacity-reduce","resource_capacity","B12",required)
    world=service.store.read()
    assert all(next(o for o in world.offers if o.id==old.id).status=="confirmed" for old in retained)
    assert all(o.status=="invalidated" for o in world.offers if o.id in {old.id for old in dependent[2:]})
    assert_active_inventory(world)
    # A second decrease can affect a whole family, never part of its seats.
    event(service,"capacity-reduce-again","resource_capacity","B12",required-1)
    assert_active_inventory(service.store.read())


def test_capacity_reduction_with_spare_seats_keeps_every_offer(tmp_path):
    service=make_service(tmp_path)
    replan(service,"spare-seats-plan")
    world=service.store.read()
    resource=next(r for r in world.resources if r.id=="B12")
    occupied=sum(r.quantity for r in world.reservations if r.resource_id==resource.id and r.status=="held")
    statuses={o.id:o.status for o in world.offers}
    event(service,"spare-seats-reduction","resource_capacity",resource.id,occupied)
    assert {o.id:o.status for o in service.store.read().offers}==statuses


def test_second_diversion_closure_invalidates_inbound_connections_and_replans(tmp_path):
    service=make_service(tmp_path)
    event(service,"cascade-close-cra","airport_closed","CRA")
    replan(service,"cascade-first-plan")
    world=service.store.read()
    inbound={g.id for g in world.groups if g.original_flight_id=="F201"}
    offer=next(o for o in active_offers(world) if o.group_id in inbound)
    accept(service,offer,"cascade-inbound-confirm")
    flight=next(r for r in world.resources if r.id=="F201")
    first_airport=flight.destination
    event(service,"cascade-close-diversion","airport_closed",first_airport)
    world=service.store.read()
    assert next(r for r in world.resources if r.id=="F201").status=="needs_diversion"
    assert next(o for o in world.offers if o.id==offer.id).status=="invalidated"
    assert not any(o.group_id in inbound for o in active_offers(world))
    assert_active_inventory(world)
    replan(service,"cascade-second-plan")
    world=service.store.read()
    assert next(r for r in world.resources if r.id=="F201").destination not in ("CRA",first_airport)
    assert_active_inventory(world)


def test_vehicle_can_operate_three_continuous_return_services():
    world=seed_world()
    template=next(r for r in world.resources if r.id=="B12")
    resources=[];groups=[];allocations=[]
    for i,(origin,destination,start) in enumerate([("CRA","OTP",650),("OTP","CRA",710),("CRA","OTP",770)]):
        resource=template.model_copy(update={"id":f"BUS{i}","origin":origin,"destination":destination,"departure":start,"arrival":start+50,"vehicle_id":"same-physical-bus"})
        group=world.groups[i].model_copy(update={"origin":origin,"destination":destination,"size":1,"accessible":False,"ready_at":start,"deadline":1000})
        resources.append(resource);groups.append(group)
        leg=Leg(resource_id=resource.id,kind="bus",origin=origin,destination=destination,departure=start,arrival=start+50,cost=resource.cost_per_person)
        allocations.append(Allocation(group_id=group.id,itinerary=Itinerary(legs=[leg],arrival=leg.arrival,total_cost=leg.cost)))
    world.resources=resources;world.groups=groups
    assert validate_allocations(world,allocations)["valid"]
    resources[2].departure=759
    assert not validate_allocations(world,allocations)["valid"]


def test_hotel_alone_and_uncovered_second_night_never_count_as_recovery():
    world=seed_world();group=world.groups[0]
    group.destination="CRA";group.deadline=5000
    hotel=next(r for r in world.resources if r.id=="H-CRA")
    world.groups=[group];world.resources=[hotel]
    assert not itinerary_options(world,group)
    leg=Leg(resource_id=hotel.id,kind="hotel",origin="CRA",destination="CRA",departure=hotel.departure,arrival=hotel.arrival,cost=hotel.cost_per_person*units(hotel,group))
    alloc=Allocation(group_id=group.id,itinerary=Itinerary(legs=[leg],arrival=leg.arrival,total_cost=leg.cost))
    assert not validate_allocations(world,[alloc])["valid"]
    flight=next(r for r in seed_world().resources if r.id=="F111")
    flight.departure+=1440;flight.arrival+=1440
    group.destination="LTN";world.resources=[hotel,flight]
    assert not itinerary_options(world,group),"One dated hotel night cannot cover an additional uncovered night"


def test_closed_airport_still_allows_ground_transfer():
    world=seed_world();group=world.groups[0];group.destination="OTP"
    apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    world.groups=[group]
    options=itinerary_options(world,group)
    assert options and all(i.legs[-1].kind=="bus" for i in options)
    assert validate_allocations(world,[Allocation(group_id=group.id,itinerary=options[0])])["valid"]


@pytest.mark.parametrize("seed",range(16))
def test_randomized_180_to_300_passenger_scenarios_are_feasible_or_explicit_exceptions(seed):
    rng=random.Random(seed);world=seed_world();target=180+seed*8
    templates=list(world.groups)
    while sum(g.size for g in world.groups)<target:
        remaining=target-sum(g.size for g in world.groups)
        sample=rng.choice(templates)
        size=min(remaining,rng.randint(1,6))
        world.groups.append(sample.model_copy(update={"id":f"R{len(world.groups):03}","size":size,"token":f"synthetic-test-token-{seed}-{len(world.groups)}","members":[f"Test member {i}" for i in range(size)]}))
    for group in world.groups:
        group.accessible=rng.random()<.25
        group.deadline=rng.randint(1100,2600)
        group.budget=rng.randint(150,3500)
    for resource in world.resources:
        if resource.id not in ("F101","F201"):
            resource.capacity=int(resource.capacity*rng.uniform(.25,1.5))
            resource.available=rng.random()>.15
    world.budget=rng.randint(5000,50000)
    if seed%3==0:
        apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    started=time.perf_counter()
    plans=[build_candidate(world,s) for s in default_strategies(world)]
    assert plans
    for plan in plans:
        assert plan.validation["valid"],plan.validation
        projected=world.model_copy(deep=True)
        for change in plan.scenario_changes:
            apply_change(projected,ScenarioChange.model_validate(change))
        groups={g.id:g for g in projected.groups};resources={r.id:r for r in projected.resources}
        recovered={a.group_id for a in plan.allocations};exceptions={e["group_id"] for e in plan.exceptions}
        eligible={g.id for g in projected.groups if g.status not in ("scheduled","confirmed")}
        assert recovered.isdisjoint(exceptions) and recovered|exceptions==eligible
        assert plan.recovered_passengers==sum(groups[i].size for i in recovered)
        counts=Counter();cost=0
        for allocation in plan.allocations:
            group=groups[allocation.group_id];itinerary=allocation.itinerary
            assert itinerary.legs[-1].destination==group.destination
            assert itinerary.arrival<=group.deadline
            for leg in itinerary.legs:
                resource=resources[leg.resource_id]
                quantity=math.ceil(group.size/resource.room_capacity) if resource.kind=="hotel" else group.size
                counts[resource.id]+=quantity
                cost+=resource.cost_per_person*quantity
                assert resource.available and (not group.accessible or resource.accessible)
        assert all(count<=resources[rid].capacity for rid,count in counts.items())
        assert cost<=world.budget+.005 and abs(plan.total_cost-cost)<.005
    assert time.perf_counter()-started<5,"Bounded scenario planning exceeded a generous 5-second CPU budget"


def test_two_constraints_and_normalized_ids_apply_once_atomically(tmp_path):
    service=make_service(tmp_path)
    class AI:
        async def interpret(self,*args):
            return {"events":[{"type":"resource_capacity","target_id":" bus_b12 ","value":9},{"type":"group_deadline","target_id":"group_g001","value":1320}],"strategies":[],"explanation":"Both constraints","focus_id":"B12"}
        async def choose(self,world,candidates,request):
            assert next(r for r in world["resources"] if r["id"]=="B12")["capacity"]==9
            assert next(g for g in world["groups"] if g["id"]=="G001")["deadline"]==1320
            return {"plan_id":candidates[0]["id"],"explanation":"Both constraints validated","provider":"test-double"}
    service.ai=AI()
    request=CommandRequest(expected_version=1,idempotency_key="two-constraints-command",text="Set B12 capacity9 and G001 deadline22:00")
    result=asyncio.run(service.command(request))
    assert result["world"]["version"]==2
    assert asyncio.run(service.command(request))["replayed"]
    world=service.store.read()
    assert len(world.decisions)==1
    assert_active_inventory(world)


def test_sqlite_connections_close_and_zero_budget_never_polls(tmp_path):
    store=Store(str(tmp_path/"closed.sqlite3"))
    with store.connect() as conn:
        assert conn.execute("SELECT 1").fetchone()==(1,)
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    assert not store.acquire_poll(15,0)
    assert store.poll_usage()==0
    assert store.acquire_poll(15,1)
    assert not Store(store.path).acquire_poll(15,1)
    assert store.poll_usage()==1


def test_external_observations_cannot_mutate_scenario_or_disclose_tokens(tmp_path):
    store=Store(str(tmp_path/"provenance.sqlite3"));before=store.read()
    store.set_observations([{"id":"F201","lat":44,"lon":23,"source":"Flightradar24","mode":"LIVE","capacity":999,"observed_at":utcnow().isoformat(),"received_at":utcnow().isoformat()}],{"state":"connected"})
    after=store.read()
    assert before.version==after.version
    assert before.resources==after.resources and before.groups==after.groups
    assert after.observations[0]["mode"]=="LIVE" and after.mode=="simulation"
    record=logging.LogRecord("uvicorn.access",logging.INFO,"",0,'%s - "%s %s HTTP/%s" %d',("client","GET","/api/passenger/secret-token/constraints?credential=secret","1.1",200),None)
    assert RedactAccessPaths().filter(record)
    assert "secret" not in record.getMessage()
    assert "[redacted]/constraints" in record.getMessage()


def test_concurrent_accept_retries_never_duplicate_inventory_or_audit(tmp_path):
    service=make_service(tmp_path);replan(service,"accept-retries-plan")
    before=service.store.read();offer=next(o for o in active_offers(before) if len(o.itinerary.legs)>1)
    expected=[r.model_dump() for r in before.reservations if r.offer_id==offer.id]
    barrier=Barrier(2)
    def retry(index):
        independent=RecoveryService(service.settings,Store(service.settings.database_path))
        barrier.wait()
        return accept(independent,offer,f"concurrent-accept-{index}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(retry,[1,2]))
    assert all(r["offer"]["status"]=="confirmed" for r in responses)
    after=service.store.read()
    actual=[r for r in after.reservations if r.offer_id==offer.id]
    assert len(actual)==len(expected) and all(r.status=="confirmed" for r in actual)
    assert sum(e.type=="offer_confirmed" and e.target_id==offer.id for e in after.events)==1
    assert_active_inventory(after)


def test_incomplete_multi_leg_hold_cannot_partially_confirm(tmp_path):
    service=make_service(tmp_path);replan(service,"incomplete-plan")
    world=service.store.read();offer=next(o for o in active_offers(world) if len(o.itinerary.legs)>1)
    def corrupt(current):
        reservation=next(r for r in current.reservations if r.offer_id==offer.id)
        current.reservations.remove(reservation)
    service.store.mutate("incomplete-test-fixture",{},world.version,corrupt)
    with pytest.raises(DomainError) as error:
        accept(service,offer,"incomplete-accept")
    assert error.value.status==409
    after=service.store.read()
    assert all(r.status=="held" for r in after.reservations if r.offer_id==offer.id)
    assert next(o for o in after.offers if o.id==offer.id).status=="held"


def test_phase_timings_survive_database_read_and_idempotent_replay(tmp_path):
    service=make_service(tmp_path)
    request=ReplanRequest(expected_version=1,idempotency_key="timing-test-plan",mode="solver")
    result=asyncio.run(service.replan(request));timings=result["decision"]["timings_ms"]
    assert timings["solver_ms"]>0 and timings["apply_ms"]>0 and timings["transaction_ms"]>0
    assert timings["interpretation_ms"]==timings["decisions_ms"]==0
    assert timings["server_total_ms"]>=timings["solver_ms"]+timings["transaction_ms"]
    assert service.store.read().decisions[-1].timings_ms==timings
    assert asyncio.run(service.replan(request))["decision"]["timings_ms"]==timings
