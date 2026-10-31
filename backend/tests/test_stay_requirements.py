"""Requested rest cannot be traded away for a cheaper direct flight."""
import copy
from datetime import datetime, timedelta

import pytest

from app.initial_position import initial_position_fingerprint
from app.models import Leg, Strategy
from app.solver import build_candidate, itinerary_options, validate_plan
from app.stay_requirements import (
    REQUIREMENT_KEY, StayRequirementError, itinerary_stay_errors,
    matching_required_stay, read_stay_requirement,
)
from test_airport_arrival import arrival_clock, package
from test_apl_pricing import NOW, fixed_clock


def requirement(**changes):
    return {"source":"operator_requested","checkin_at":"2030-01-04T19:00:00+00:00",
        "checkout_at":"2030-01-05T05:00:00+00:00","rooms":1,"minimum_rest_minutes":480} | changes


def install(world, value):
    group = world.groups[0]
    group.provenance[REQUIREMENT_KEY] = copy.deepcopy(value)
    fingerprint = initial_position_fingerprint(world,group)
    for resource in world.resources:
        for field in ("initial_position_assumption","airport_arrival_assumption"):
            declaration = getattr(resource,field)
            if declaration is not None:
                declaration.group_fingerprint = fingerprint


def state():
    world = package()
    hotel = world.resources[1]
    epoch = datetime.fromisoformat(world.epoch.replace("Z","+00:00"))
    hotel.provenance.update(provider="liteapi",rooms=1,adults=2,
        checkin_at=(epoch+timedelta(minutes=hotel.departure)).isoformat(),
        checkout_at=(epoch+timedelta(minutes=hotel.arrival)).isoformat())
    return world


@pytest.mark.parametrize("objective",["lowest_cost","fastest","balanced"])
def test_requested_stay_survives_every_objective_and_uses_one_covering_source_stay(objective):
    world = state()
    direct = build_candidate(world,Strategy(objective=objective))
    assert [leg.resource_id for leg in direct.allocations[0].itinerary.legs] == ["ONWARD"]
    assert direct.total_cost == 100
    install(world,requirement())
    plan = build_candidate(world,Strategy(objective=objective))
    assert plan.validation["valid"] and plan.recovered_passengers == 2
    assert [leg.resource_id for leg in plan.allocations[0].itinerary.legs] == ["APL1","HOTEL","RETURN","ONWARD"]
    assert plan.total_cost == 330
    assert "requested_hotel_stay" in plan.validation["checks"]
    assert validate_plan(world,plan)["valid"]
    assert not validate_plan(world,direct)["valid"]  # Independent checker rejects an old/direct candidate.


def test_absent_requirement_keeps_overnight_wait_allowed_and_adds_no_group_fields():
    world = state()
    before = world.groups[0].model_dump_json()
    assert read_stay_requirement(world.groups[0]) is None
    plan = build_candidate(world,Strategy(objective="lowest_cost"))
    assert plan.validation["valid"] and [leg.kind for leg in plan.allocations[0].itinerary.legs] == ["flight"]
    assert world.groups[0].model_dump_json() == before
    assert REQUIREMENT_KEY not in world.groups[0].provenance


def test_requirement_is_part_of_existing_fingerprint_before_transfer_assumptions_are_built():
    world = state()
    group = world.groups[0]
    before = initial_position_fingerprint(world,group)
    group.provenance[REQUIREMENT_KEY] = requirement()
    assert initial_position_fingerprint(world,group) != before
    assert not build_candidate(world,Strategy()).allocations  # Old transfer declarations cannot silently survive.
    install(world,requirement())
    assert build_candidate(world,Strategy()).recovered_passengers == 2


@pytest.mark.parametrize("mutation",[
    lambda w:setattr(w.resources[1],"source","synthetic"),
    lambda w:w.resources[1].provenance.update(rooms=2),
    lambda w:w.resources[1].provenance.update(adults=1),
    lambda w:w.resources[1].provenance.pop("checkin_at"),
    lambda w:w.resources[1].provenance.update(checkout_at="2030-01-05T04:00:00+00:00"),
    lambda w:w.resources[1].provenance["property_location"].update(latitude=52.0),
])
def test_room_party_property_and_source_interval_must_match(mutation):
    world = state()
    install(world,requirement())
    plan = build_candidate(world,Strategy())
    mutation(world)
    assert not matching_required_stay(world,world.groups[0],world.resources[1])
    assert not validate_plan(world,plan)["valid"]
    unavailable = build_candidate(world,Strategy())
    assert unavailable.validation["valid"] and not unavailable.allocations
    assert "requested hotel" in unavailable.exceptions[0]["reason"]


def test_rounded_minutes_cannot_inflate_source_rest_or_cover_the_requested_interval():
    world = state()
    install(world,requirement(checkin_at="2030-01-04T19:00:00Z",checkout_at="2030-01-04T19:02:00Z",minimum_rest_minutes=2))
    hotel = world.resources[1]
    hotel.departure,hotel.arrival = 1140,1142
    hotel.provenance.update(checkin_at="2030-01-04T19:00:30Z",checkout_at="2030-01-04T19:01:31Z")
    assert hotel.arrival-hotel.departure == 2
    assert not matching_required_stay(world,world.groups[0],hotel)


def test_offsets_are_compared_as_instants_and_zero_rest_still_requires_a_hotel():
    world = state()
    install(world,requirement(checkin_at="2030-01-04T21:00:00+02:00",checkout_at="2030-01-05T07:00:00+02:00",minimum_rest_minutes=0))
    assert matching_required_stay(world,world.groups[0],world.resources[1])
    assert itinerary_stay_errors(world,world.groups[0],[])
    assert build_candidate(world,Strategy()).recovered_passengers == 2


def test_disjoint_stays_do_not_add_up_to_one_required_stay():
    world = state()
    install(world,requirement())
    first = world.resources[1]
    first.arrival = 1380
    first.provenance["checkout_at"] = "2030-01-04T23:00:00Z"
    second = first.model_copy(deep=True)
    second.id,second.departure,second.arrival = "HOTEL-LATER",1390,1800
    second.provenance.update(checkin_at="2030-01-04T23:10:00Z",checkout_at="2030-01-05T06:00:00Z")
    world.resources.append(second)
    legs = [Leg(resource_id=resource.id,kind="hotel",origin="HP",destination="HP",departure=resource.departure,
        arrival=resource.arrival,cost=40) for resource in (first,second)]
    assert sum(leg.arrival-leg.departure for leg in legs) > 480
    assert itinerary_stay_errors(world,world.groups[0],legs)
    assert not build_candidate(world,Strategy()).allocations


def test_validator_matches_actual_hotel_leg_not_only_resource_presence():
    world = state()
    install(world,requirement())
    plan = build_candidate(world,Strategy())
    plan.allocations[0].itinerary.legs[1].arrival += 1
    errors = itinerary_stay_errors(world,world.groups[0],plan.allocations[0].itinerary.legs)
    assert errors and not validate_plan(world,plan)["valid"]


@pytest.mark.parametrize("value",[
    None,{},requirement(source="supplier"),requirement(rooms=True),requirement(rooms="1"),requirement(rooms=3),
    requirement(minimum_rest_minutes=True),requirement(minimum_rest_minutes=-1),requirement(minimum_rest_minutes=601),
    requirement(checkin_at="2030-01-04T19:00:00"),requirement(checkout_at="2030-01-04T18:00:00Z"),
    requirement(checkout_at="2030-01-04T19:00:00Z"),requirement(invented_inventory=True),
])
def test_malformed_or_contradictory_requirements_are_explicit_invalid_input(value):
    world = state()
    install(world,value)
    with pytest.raises(StayRequirementError):
        read_stay_requirement(world.groups[0])
    with pytest.raises(StayRequirementError):
        itinerary_options(world,world.groups[0])
    plan = build_candidate(world,Strategy())
    assert not plan.allocations and not plan.validation["valid"]
    assert plan.exceptions[0]["code"] == "invalid_hotel_stay_requirement"
    assert "Invalid hotel stay requirement" in plan.exceptions[0]["reason"]
    assert not validate_plan(world,plan)["valid"]


def test_confirmed_source_stay_is_not_invalidated_by_quote_age(monkeypatch):
    world = state()
    install(world,requirement())
    plan = build_candidate(world,Strategy())
    world.groups[0].status = "confirmed"
    for resource in world.resources:
        resource.booking_status = "confirmed"
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            later = NOW+timedelta(days=4)
            return later.astimezone(tz) if tz else later.replace(tzinfo=None)
    for module in ("app.solver", "app.pricing", "app.airport_arrival"):
        monkeypatch.setattr(module+".datetime",Later)
    # The requirement is still checked against original dates and occupancy.
    assert validate_plan(world,plan)["valid"]
