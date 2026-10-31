"""A declared pickup point is a first-leg fact, never a geographic shortcut."""
import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.initial_position import InitialPositionAssumption, initial_position_fingerprint
from app.models import Airport, Allocation, Itinerary, Leg, Strategy
from app.pricing import cost_summary
from app.service import RecoveryService
from app.solver import build_candidate, resource_feasible, validate_allocations, validate_plan
from test_apl_pricing import fixed_clock, state


def positioned():
    world = state()
    point = Airport(id="MP-PICKUP", code="MP-PICKUP", name="Declared terminal point", kind="meeting_point",
        lat=51.472, lon=-0.488, diversion_eligible=False)
    world.airports.append(point)
    group, resource = world.groups[0], world.resources[0]
    resource.origin = point.id
    resource.initial_position_assumption = InitialPositionAssumption(source="operator_declared", confirmed=True,
        group_id=group.id, group_fingerprint=initial_position_fingerprint(world, group), scenario_id=world.scenario_id,
        epoch=world.epoch, airport_context_id=group.origin, point_id=point.id, label="Operator-declared terminal pickup",
        coordinates={"latitude": point.lat, "longitude": point.lon}, ready_at=group.ready_at)
    return world


def test_first_leg_uses_exact_declared_point_without_rewriting_airport_origin():
    world = positioned()
    original = world.groups[0].model_dump()
    resource_ids = [item.id for item in world.resources]
    plan = build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2
    leg = plan.allocations[0].itinerary.legs[0]
    assert leg.origin == "MP-PICKUP"
    assert leg.initial_position_assumption == world.resources[0].initial_position_assumption
    assert world.groups[0].model_dump() == original
    assert [item.id for item in world.resources] == resource_ids
    service = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=60), None)
    service._hold_plan(world, plan, "solver", "Recover", "fixture", "fixture", [plan], 1)
    assert world.groups[0].origin == "LHR"
    assert world.offers[0].itinerary.legs[0].initial_position_assumption == leg.initial_position_assumption


@pytest.mark.parametrize("change", [
    lambda w: setattr(w.resources[0], "initial_position_assumption", None),
    lambda w: setattr(w.resources[0].initial_position_assumption, "group_id", "OTHER"),
    lambda w: setattr(w.resources[0].initial_position_assumption, "airport_context_id", "OTP"),
    lambda w: setattr(w.resources[0].initial_position_assumption, "point_id", "HP"),
    lambda w: setattr(w.resources[0].initial_position_assumption, "ready_at", 601),
    lambda w: setattr(w.resources[0].initial_position_assumption.coordinates, "latitude", 51.473),
    lambda w: setattr(w.airports[-1], "lon", -0.489),
    lambda w: setattr(w.airports[-1], "kind", "hotel"),
    lambda w: setattr(w, "scenario_id", "other-scenario"),
    lambda w: setattr(w, "epoch", "2030-01-05T00:00:00Z"),
    lambda w: setattr(w.groups[0], "deadline", 901),
    lambda w: setattr(w.groups[0], "budget", 201),
    lambda w: setattr(w.groups[0], "members", ["Changed passenger identity"]),
    lambda w: w.groups[0].provenance.update(source_revision="changed"),
])
def test_missing_changed_or_foreign_start_declaration_cannot_allocate(change):
    world = positioned()
    change(world)
    plan = build_candidate(world, Strategy())
    assert plan.validation["valid"] and not plan.allocations


def test_status_token_and_label_changes_do_not_invalidate_initial_fact():
    world = positioned()
    fingerprint = initial_position_fingerprint(world, world.groups[0])
    world.groups[0].status = "offered"
    world.groups[0].token = "rotated-private-fixture"
    world.groups[0].name = "New presentation label"
    assert initial_position_fingerprint(world, world.groups[0]) == fingerprint
    assert build_candidate(world, Strategy()).recovered_passengers == 2


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_initial_position_requires_an_explicit_boolean_confirmation(confirmation):
    data = positioned().resources[0].initial_position_assumption.model_dump()
    data["confirmed"] = confirmation
    with pytest.raises(ValidationError):
        InitialPositionAssumption.model_validate(data)


def test_initial_point_permission_cannot_be_reused_after_another_leg():
    world = positioned()
    group, transfer = world.groups[0], world.resources[0]
    plan = build_candidate(world, Strategy())
    preceding = world.resources[1].model_copy(update={"id": "PRIOR", "origin": "LHR", "destination": "MP-PICKUP",
        "departure": 601, "arrival": 610, "cost_per_person": 0})
    world.resources.append(preceding)
    assert not resource_feasible(world, transfer, group, "MP-PICKUP", 620, {"PRIOR"})
    legs = [Leg(resource_id="PRIOR", kind="bus", origin="LHR", destination="MP-PICKUP", departure=601, arrival=610, cost=0)] + copy.deepcopy(plan.allocations[0].itinerary.legs)
    altered = Allocation(group_id="G1", itinerary=Itinerary(legs=legs, arrival=740, total_cost=115, cost_evidence=cost_summary(legs)))
    assert not validate_allocations(world, [altered])["valid"]


def test_passenger_snapshot_cannot_omit_or_replace_the_operator_declaration():
    world = positioned()
    plan = build_candidate(world, Strategy())
    plan.allocations[0].itinerary.legs[0].initial_position_assumption = None
    assert not validate_plan(world, plan)["valid"]
