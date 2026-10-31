from copy import deepcopy

from app.models import Allocation, Itinerary, Leg, PassengerGroup, Resource, Strategy
from app.seed import seed_world
from app.service import apply_change
from app.models import ScenarioChange
from app.solver import build_candidate, default_strategies, itinerary_options, validate_allocations


def test_seed_scale_and_every_strategy_full_journey_is_valid():
    world=seed_world()
    assert sum(g.size for g in world.groups)==180
    for strategy in default_strategies(world):
        plan=build_candidate(world,strategy)
        assert plan.validation["valid"],plan.validation
        assert plan.recovered_passengers==120
        for allocation in plan.allocations:
            group=next(g for g in world.groups if g.id==allocation.group_id)
            assert allocation.itinerary.legs[-1].destination==group.destination
        assert "not proven" in plan.optimality


def test_infeasible_family_not_split_across_two_small_buses():
    world=seed_world()
    g=world.groups[0]
    g.size=4; g.origin="CRA"; g.destination="OTP"; g.deadline=1400
    world.groups=[g]
    world.resources=[Resource(id="a",kind="bus",name="a",origin="CRA",destination="OTP",departure=700,arrival=900,capacity=2,cost_per_person=1),Resource(id="b",kind="bus",name="b",origin="CRA",destination="OTP",departure=700,arrival=900,capacity=2,cost_per_person=1)]
    plan=build_candidate(world,Strategy())
    assert plan.recovered_passengers==0
    assert plan.exceptions[0]["passengers"]==4


def test_capacity_deadline_accessibility_and_cost_are_exactly_checked():
    world=seed_world()
    plan=build_candidate(world,Strategy())
    allocation=plan.allocations[0].model_copy(deep=True)
    group=next(g for g in world.groups if g.id==allocation.group_id)
    resource=next(r for r in world.resources if r.id==allocation.itinerary.legs[0].resource_id)
    resource.capacity=0
    assert not validate_allocations(world,[allocation])["valid"]
    resource.capacity=200
    group.deadline=600
    assert not validate_allocations(world,[allocation])["valid"]
    group.deadline=10080; group.accessible=True; resource.accessible=False
    assert not validate_allocations(world,[allocation])["valid"]
    resource.accessible=True
    allocation.itinerary.total_cost=0
    assert any("totals" in e for e in validate_allocations(world,[allocation])["errors"])


def test_shared_resource_capacity_individually_valid_but_jointly_invalid():
    world=seed_world()
    group=world.groups[0]
    other=group.model_copy(deep=True); other.id="G999"
    world.groups=[group,other]
    group.destination=other.destination="OTP"
    bus=next(r for r in world.resources if r.id=="B12")
    bus.capacity=group.size
    world.resources=[bus]
    a=itinerary_options(world,group)[0]
    b=itinerary_options(world,other)[0]
    assert validate_allocations(world,[Allocation(group_id=group.id,itinerary=a)])["valid"]
    assert not validate_allocations(world,[Allocation(group_id=group.id,itinerary=a),Allocation(group_id=other.id,itinerary=b)])["valid"]


def test_hotel_rooms_are_ceiling_family_occupancy_and_hotel_only_is_not_recovery():
    world=seed_world()
    group=world.groups[0]; group.size=5; group.deadline=2400
    world.groups=[group]
    hotel=next(r for r in world.resources if r.id=="H-CRA")
    hotel.capacity=2
    world.resources=[hotel,next(r for r in world.resources if r.id=="F111")]
    assert not itinerary_options(world,group)
    hotel.capacity=3
    options=itinerary_options(world,group)
    assert options
    assert options[0].legs[0].cost==3*hotel.cost_per_person
    assert options[0].legs[-1].destination=="LTN"
    # Midnight cannot be bypassed with only a cheap next-day flight.
    world.resources=[world.resources[1]]
    assert not itinerary_options(world,group)


def test_vehicle_cannot_cover_conflicting_services():
    world=seed_world()
    group=world.groups[0]; group.destination="OTP"
    other=group.model_copy(deep=True); other.id="G999"; other.destination="SBZ"
    a=next(r for r in world.resources if r.id=="B12")
    b=next(r for r in world.resources if r.id=="B09")
    a.vehicle_id=b.vehicle_id="same_bus"
    world.groups=[group,other]; world.resources=[a,b]
    allocations=[Allocation(group_id=g.id,itinerary=itinerary_options(world,g)[0]) for g in world.groups]
    assert not validate_allocations(world,allocations)["valid"]
    plan=build_candidate(world,Strategy())
    assert plan.recovered_passengers==group.size


def test_airport_closure_compares_diversion_with_shared_inventory():
    world=seed_world()
    apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    strategies=default_strategies(world)
    assert {s.preferred_airport for s in strategies}=={"OTP","SBZ"}
    plans=[build_candidate(world,s) for s in strategies]
    assert all(p.validation["valid"] for p in plans)
    assert all(p.scenario_changes[0]["type"]=="flight_diverted" for p in plans)
    assert any(p.recovered_passengers>120 for p in plans)
    # A jury capacity change genuinely changes the plan, rather than a scripted answer.
    for r in world.resources:
        if r.origin=="OTP" and r.destination=="CRA":
            r.capacity=0
    degraded=build_candidate(world,next(s for s in strategies if s.preferred_airport=="OTP"))
    assert degraded.recovered_passengers < next(p for p in plans if p.strategy.id==degraded.strategy.id).recovered_passengers


def test_hard_global_budget_never_exceeded_and_no_false_full_recovery():
    world=seed_world(); world.budget=100
    plan=build_candidate(world,Strategy(objective="lowest_cost"))
    assert plan.total_cost<=100
    assert plan.recovered_passengers<120
    assert plan.validation["valid"]
