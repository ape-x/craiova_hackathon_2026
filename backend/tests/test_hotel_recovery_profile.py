"""Saved inputs bind operator intent, not source inventory or provider usage."""
import copy
import json
from datetime import timedelta

import pytest

from app.airport_catalog import lookup_airport
from app.hotel_recovery import HotelRecoveryInputs
from app.hotel_recovery_profile import (
    PROFILE_KEY, HotelRecoveryProfileError, build_hotel_recovery_profile,
    read_hotel_recovery_profile, hotel_profile_summary,
)
from app.models import Airport
from app.providers.ai import compact_world
from app.store import utcnow
from test_live_commands import command_setup  # isolated SQLite / FakeAI, no network


@pytest.fixture
def hotel_command_setup(command_setup):
    _, store, _, _, ai = command_setup
    pickup = (utcnow() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    inputs = HotelRecoveryInputs(
        checkin_at=pickup + timedelta(hours=2), checkout_at=pickup + timedelta(hours=23),
        pickup_at=pickup, rooms=1, minimum_rest_minutes=8 * 60, suitcases=1,
        initial_point={"label": "PRIVATE PICKUP POINT", "latitude": 51.471234,
                       "longitude": -0.455678, "confirmed": True},
        onward_airport_code="LHR", airport_processing_minutes=120,
        airport_dropoff={"label": "PRIVATE DROPOFF POINT", "latitude": 51.471234,
                         "longitude": -0.455678, "confirmed": True})

    def seed(world):
        from datetime import datetime
        node = lookup_airport("LHR")
        world.airports.append(Airport(id="LHR", code="LHR", name=node["name"], lat=node["lat"], lon=node["lon"]))
        group = world.groups[0]
        group.origin = "LHR"
        group.deadline = int((inputs.checkout_at + timedelta(hours=12) - datetime.fromisoformat(world.epoch)).total_seconds() / 60)
        group.provenance["hotel_stay_requirement"] = inputs.requirement().model_dump(mode="json")
        group.provenance[PROFILE_KEY] = build_hotel_recovery_profile(world, group, inputs)
    store.mutate("fixture-hotel-profile", {}, store.read().version, seed)
    ai.result["recovery"].update(group_ids=["G1"], all_linked_groups=False, modes=["flight", "hotel", "bus"])
    return (*command_setup, inputs)


def test_profile_is_stable_and_returns_an_independent_exact_copy(hotel_command_setup):
    _, store, _, _, _, inputs = hotel_command_setup
    world = store.read()
    group = world.groups[0]
    saved = copy.deepcopy(group.provenance[PROFILE_KEY])
    assert saved == build_hotel_recovery_profile(world, group, inputs)
    assert set(saved) == {"schema_version", "source", "scope_fingerprint", "inputs"}
    actual = read_hotel_recovery_profile(world, group)
    assert actual == inputs.model_dump(mode="json")
    actual["initial_point"]["label"] = "Not the saved input"
    assert group.provenance[PROFILE_KEY] == saved


@pytest.mark.parametrize("field,value", [
    ("status", "offered"), ("name", "New display name"), ("token", "rotated-private-token"),
    ("ready_at", 1), ("deadline", 10000), ("budget", 15), ("priority", 4),
])
def test_profile_identity_excludes_churn_and_current_constraints(hotel_command_setup, field, value):
    _, store, _, _, _, inputs = hotel_command_setup
    world = store.read()
    group = world.groups[0]
    setattr(group, field, value)
    group.provenance["audit_note"] = {"job_id": "changing-job", "time": utcnow().isoformat()}
    assert read_hotel_recovery_profile(world, group) == inputs.model_dump(mode="json")


@pytest.mark.parametrize("field,value", [
    ("size", 2), ("adult_count", 0), ("members", ["DIFFERENT PRIVATE PASSENGER"]),
    ("source", "different_manifest"), ("origin", "LGW"), ("destination", "CRA"),
    ("original_flight_id", "DIFFERENT-FLIGHT"), ("incident_id", "RC-OTHER"),
])
def test_changed_party_or_route_cannot_reuse_a_profile(hotel_command_setup, field, value):
    _, store, _, _, _, _ = hotel_command_setup
    world = store.read()
    setattr(world.groups[0], field, value)
    with pytest.raises(HotelRecoveryProfileError):
        read_hotel_recovery_profile(world, world.groups[0])


@pytest.mark.parametrize("change", ["scenario", "epoch", "case_date", "case_flight", "binding", "mode"])
def test_profile_is_bound_to_this_operation_and_case(hotel_command_setup, change):
    _, store, _, _, _, _ = hotel_command_setup
    world = store.read()
    if change == "scenario": world.scenario_id = "different-operation"
    elif change == "epoch": world.epoch = "2026-01-01T00:00:00+00:00"
    elif change == "case_date": world.incidents[0].date_utc = "2026-11-02"
    elif change == "case_flight": world.incidents[0].requested_flight_number = "OTHER1"
    elif change == "binding": world.incidents[0].group_ids = ["G2"]
    elif change == "mode": world.mode = "simulation"
    with pytest.raises(HotelRecoveryProfileError):
        read_hotel_recovery_profile(world, world.groups[0])


@pytest.mark.parametrize("raw", [None, {}, {"schema_version": 2}, "not-a-profile"])
def test_missing_or_malformed_profiles_fail_closed(hotel_command_setup, raw):
    _, store, _, _, _, _ = hotel_command_setup
    world = store.read()
    world.groups[0].provenance[PROFILE_KEY] = raw
    with pytest.raises(HotelRecoveryProfileError):
        read_hotel_recovery_profile(world, world.groups[0])


@pytest.mark.parametrize("field,value", [("pickup_at", "2026-11-02T12:00:00"), ("rooms", True), ("suitcases", -1)])
def test_profile_inputs_are_revalidated_not_trusted_as_an_arbitrary_dict(hotel_command_setup, field, value):
    _, store, _, _, _, _ = hotel_command_setup
    world = store.read()
    world.groups[0].provenance[PROFILE_KEY]["inputs"][field] = value
    with pytest.raises(HotelRecoveryProfileError):
        read_hotel_recovery_profile(world, world.groups[0])


def test_compact_ai_context_has_dates_and_directions_without_private_profile_data(hotel_command_setup):
    _, store, _, _, _, inputs = hotel_command_setup
    world = store.read()
    compact = compact_world(world.model_dump(mode="json"))
    group = compact["groups"][0]
    summary = group[PROFILE_KEY]
    assert summary["configured"] is True and summary["minimum_total_calls"] == 11
    assert summary["checkin_at"] == inputs.model_dump(mode="json")["checkin_at"]
    assert summary["initial_airport_code"] == summary["onward_airport_code"] == "LHR"
    assert summary["destination_airport_code"] == "OTP"
    serialized = json.dumps(compact)
    for private in ("PRIVATE PICKUP POINT", "PRIVATE DROPOFF POINT", "PRIVATE PASSENGER", "private-token-G1", "51.471234", "-0.455678", "scope_fingerprint"):
        assert private not in serialized
    assert "provenance" not in group and "members" not in group
    world.groups[0].size = 2
    assert hotel_profile_summary(world, world.groups[0]) == {"configured": False, "requires_typed_setup": True}
