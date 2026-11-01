"""A passenger arrival report resolves only that exact passenger-location hold."""
import pytest

from app.models import Resource
from app.weather import current_operator_arrival, passenger_arrival_pending
from test_operational_reports import arrival, reports
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup


def reported(reports):
    _, store, service = reports
    def seed(world):
        group = world.groups[0]
        group.original_flight_id = "DIVERTED"
        world.resources.append(Resource(id="DIVERTED", name="Original disrupted flight", kind="flight",
            origin="LHR", destination="OTP", departure=1, arrival=2, capacity=10,
            cost_per_person=50, status="needs_diversion", available=False))
    store.mutate("source-pending-flight", {}, store.read().version, seed)
    before = store.read()
    assert passenger_arrival_pending(before, before.groups[0])
    request = arrival(store)
    service.arrival("RC-ONE", request)
    return before, store.read()


def test_report_resolves_only_passenger_hold_preserving_original_source_and_airport(reports):
    before, world = reported(reports)
    group = world.groups[0]
    assert current_operator_arrival(world, group) and not passenger_arrival_pending(world, group)
    assert world.resources == before.resources
    assert world.resources[-1].status == "needs_diversion" and not world.resources[-1].available
    airport = next(a for a in world.airports if a.id == group.origin)
    assert not airport.available and airport.operational_status == "unknown"
    assert not current_operator_arrival(world, world.groups[1])


@pytest.mark.parametrize("field,value", [
    ("source", "supplier_schedule"), ("type", "flight_arrival"), ("id", "invented"),
    ("scenario_id", "other"), ("epoch", "2020-01-01T00:00:00Z"),
    ("original_flight_id", "different"), ("group_id", "G2"), ("incident_id", "other"),
    ("airport_code", "OTP"), ("ready_at", "2026-01-01T00:00:00Z"),
    ("ready_at", "2026-01-01T00:00:00"), ("received_at", "invalid"),
])
def test_wrong_or_malformed_binding_cannot_clear_pending(reports, field, value):
    _, world = reported(reports)
    group = world.groups[0]
    group.provenance["arrival_reports"][-1][field] = value
    assert passenger_arrival_pending(world, group)


@pytest.mark.parametrize("change", ["readiness", "origin", "case", "latest", "duplicate", "reversed", "legacy", "mode"])
def test_current_state_or_report_history_change_cannot_reuse_old_arrival(reports, change):
    _, world = reported(reports)
    group = world.groups[0]
    history = group.provenance["arrival_reports"]
    if change == "readiness": group.ready_at += 1
    elif change == "origin": group.origin = "OTP"
    elif change == "case": world.incidents[0].group_ids.remove(group.id)
    elif change == "latest": history.append({**history[-1], "id": "opr_"+"a"*24, "airport_code": "OTP"})
    elif change == "duplicate": history.append(dict(history[-1]))
    elif change == "reversed": history.insert(0, {**history[-1], "id": "opr_"+"a"*24, "received_at": "2099-01-01T00:00:00Z"})
    elif change == "legacy": history[-1].pop("original_flight_id")
    elif change == "mode": world.mode = "simulation"
    assert passenger_arrival_pending(world, group)
