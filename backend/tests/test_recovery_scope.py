"""Recovery jobs protect unrelated allocations; all supply below is a fixture."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import Airport, Allocation, Itinerary, Leg, Offer, PassengerGroup, RecoveryIncident, Reservation, Resource, Strategy, World
from app.service import RecoveryService
from app.solver import build_candidate, itinerary_options, validate_allocations
from app.store import DomainError


def expiry():
    return (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()


def group(identity, **changes):
    return PassengerGroup(**({"id": identity, "name": identity, "size": 2, "origin": "CRA", "destination": "OTP",
        "deadline": 2000, "ready_at": 600, "budget": 1000, "original_flight_id": "original", "token": identity,
        "source": "operator_input"} | changes))


def resource(identity="B1", **changes):
    return Resource(**({"id": identity, "name": identity, "kind": "bus", "origin": "CRA", "destination": "OTP",
        "departure": 650, "arrival": 750, "capacity": 8, "cost_per_person": 10,
        "source": "fixture_supplier", "valid_until": expiry()} | changes))


def itinerary(resources, passengers=2):
    legs = [Leg(resource_id=r.id, kind=r.kind, origin=r.origin, destination=r.destination,
        departure=r.departure, arrival=r.arrival, cost=r.cost_per_person * (1 if r.kind == "hotel" else passengers)) for r in resources]
    return Itinerary(legs=legs, arrival=legs[-1].arrival, total_cost=sum(leg.cost for leg in legs))


def world():
    return World(scenario_id="scope-fixture", epoch="2030-01-01T00:00:00Z", mode="live", sim_time=600, budget=80,
        airports=[Airport(id=code, code=code, name=code, lat=44, lon=24, operational_status="open") for code in ("CRA", "OTP", "LTN", "SBZ")],
        groups=[group("GNEW")], resources=[resource()])


def commit_existing(state, identity, status, selected_resource=None):
    selected_resource = selected_resource or state.resources[0]
    passenger = group(identity, status="confirmed" if status == "confirmed" else "offered")
    state.groups.append(passenger)
    offer = Offer(id="O-" + identity, group_id=identity, itinerary=itinerary([selected_resource]),
        status=status, expires_at=expiry(), created_version=state.version,
        confirmed_at=expiry() if status == "confirmed" else None,
        external_bookings=[{"provider": "fixture_supplier", "reference": "fixture-confirmed"}] if status == "confirmed" else [])
    state.offers.append(offer)
    state.reservations.append(Reservation(id="R-" + identity, offer_id=offer.id, group_id=identity,
        resource_id=selected_resource.id, quantity=2, status=status, expires_at=offer.expires_at))
    return offer


def hold(state, plan, scope=None):
    service = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=60), None)
    return service._hold_plan(state, plan, "solver", "Recover selected incident only", "fixture solver", "Scoped allocation",
        [plan], 1, scope_group_ids={"GNEW"} if scope is None else scope)


def protected_bytes(state):
    return {"groups": [g.model_dump_json() for g in state.groups if g.id != "GNEW"],
        "offers": [o.model_dump_json() for o in state.offers if o.group_id != "GNEW"],
        "reservations": [r.model_dump_json() for r in state.reservations if r.group_id != "GNEW"]}


@pytest.mark.parametrize("status", ["proposed", "accepted", "confirmed"])
def test_each_out_of_scope_status_reserves_capacity_and_global_budget(status):
    state = world()
    state.resources[0].capacity = 2
    commit_existing(state, "GOTHER", status)
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    assert plan.validation["valid"] and plan.recovered_passengers == 0
    assert [e["group_id"] for e in plan.exceptions] == ["GNEW"]
    state.resources[0].capacity = 4
    state.budget = 39
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    assert plan.validation["valid"] and plan.recovered_passengers == 0
    state.budget = 40
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    assert plan.recovered_passengers == 2 and plan.total_cost == 20
    assert plan.validation["total_committed_cost"] == 40


def test_scoped_apply_preserves_all_unrelated_rows_byte_for_byte_and_replaces_only_own_offer():
    state = world()
    for identity, status in (("GP", "proposed"), ("GA", "accepted"), ("GC", "confirmed")):
        commit_existing(state, identity, status)
    old = Offer(id="O-OLD-SCOPED", group_id="GNEW", itinerary=itinerary(state.resources), status="proposed",
        expires_at=expiry(), created_version=state.version)
    state.offers.append(old)
    state.reservations.append(Reservation(id="R-OLD-SCOPED", offer_id=old.id, group_id="GNEW", resource_id="B1", quantity=2, status="proposed", expires_at=old.expires_at))
    before = protected_bytes(state)
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    assert plan.validation["valid"] and plan.validation["total_committed_cost"] == 80
    assert [a.group_id for a in plan.allocations] == ["GNEW"]
    hold(state, plan)
    assert protected_bytes(state) == before
    assert old.status == "superseded"
    assert next(r for r in state.reservations if r.id == "R-OLD-SCOPED").status == "released"
    assert [o.group_id for o in state.offers if o.status == "proposed"] == ["GP", "GNEW"]


def test_scope_is_checked_independently_of_claimed_plan_validation_before_apply():
    state = world()
    state.groups.append(group("GOTHER"))
    wrong = Allocation(group_id="GOTHER", itinerary=itinerary(state.resources))
    result = validate_allocations(state, [wrong], scope_group_ids={"GNEW"})
    assert not result["valid"] and any("scope" in error for error in result["errors"])
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    plan.allocations = [wrong]
    before = state.model_dump_json()
    with pytest.raises(DomainError):
        hold(state, plan)
    assert state.model_dump_json() == before


def test_foreign_exception_cannot_mutate_an_unrelated_group():
    state = world()
    state.groups.append(group("GOTHER"))
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    plan.exceptions.append({"group_id": "GOTHER", "passengers": 2, "reason": "outside job scope"})
    before = state.model_dump_json()
    with pytest.raises(DomainError):
        hold(state, plan)
    assert state.model_dump_json() == before


def test_incident_blocks_intermediate_flight_airport_for_selected_group():
    state = world()
    state.groups[0].destination = "LTN"
    state.resources = [resource("F1", kind="flight", destination="LTN", departure=700, arrival=900,
        provenance={"segments": [{"origin": "CRA", "destination": "OTP"}, {"origin": "OTP", "destination": "LTN"}]})]
    assert build_candidate(state, Strategy(), scope_group_ids={"GNEW"}).recovered_passengers == 2
    state.incidents = [RecoveryIncident(id="CASE", created_at=expiry(), kind="destination_unavailable", description="Operator reports OTP unavailable",
        requested_flight_number="F123", date_utc="2030-01-01", flight_ref={"lookup_id": "fixture", "selection_id": "fixture"},
        unavailable_airport_code="OTP", group_ids=["GNEW"], status="open")]
    state.groups[0].incident_id = "CASE"
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    assert plan.validation["valid"] and plan.recovered_passengers == 0
    forged = Allocation(group_id="GNEW", itinerary=itinerary(state.resources))
    assert not validate_allocations(state, [forged], scope_group_ids={"GNEW"})["valid"]


def test_hotel_only_and_disconnected_physical_hotel_never_count_as_recovery():
    state = world()
    state.resources = [resource("H1", kind="hotel", origin="HOTEL", destination="HOTEL", departure=720, arrival=1600, cost_per_person=20)]
    hotel = state.resources[0]
    state.airports.append(Airport(id="HOTEL", code="HOTEL", name="Physical hotel", kind="hotel", lat=44.1, lon=24.1))
    state.resources.append(resource("F1", kind="flight", origin="LTN", departure=1700, arrival=1800))
    assert build_candidate(state, Strategy(), scope_group_ids={"GNEW"}).recovered_passengers == 0
    disconnected = Allocation(group_id="GNEW", itinerary=itinerary(state.resources))
    assert not validate_allocations(state, [disconnected], scope_group_ids={"GNEW"})["valid"]
    state.groups[0].origin = state.groups[0].destination = "HOTEL"
    state.resources = [hotel]
    assert build_candidate(state, Strategy(), scope_group_ids={"GNEW"}).recovered_passengers == 0
    hotel_only = Allocation(group_id="GNEW", itinerary=itinerary(state.resources))
    assert any("accommodation alone" in error for error in validate_allocations(state, [hotel_only], scope_group_ids={"GNEW"})["errors"])


def test_complete_airport_hotel_airport_flight_chain_is_recoverable_when_real_transfers_exist():
    state = world()
    state.budget = 200
    state.airports.append(Airport(id="HOTEL", code="HOTEL", name="Physical hotel", kind="hotel", lat=44.1, lon=24.1))
    state.resources = [resource("OUT", destination="HOTEL", departure=650, arrival=700),
        resource("H1", kind="hotel", origin="HOTEL", destination="HOTEL", departure=720, arrival=1600, cost_per_person=20),
        resource("BACK", origin="HOTEL", destination="CRA", departure=1620, arrival=1650),
        resource("F1", kind="flight", departure=1700, arrival=1800)]
    valid = Allocation(group_id="GNEW", itinerary=itinerary(state.resources))
    assert validate_allocations(state, [valid], scope_group_ids={"GNEW"})["valid"]
    plan = build_candidate(state, Strategy(), scope_group_ids={"GNEW"})
    assert plan.recovered_passengers == 2
    from app.solver import itinerary_options
    assert any([leg.resource_id for leg in option.legs]==["OUT","H1","BACK","F1"] for option in itinerary_options(state,state.groups[0]))
