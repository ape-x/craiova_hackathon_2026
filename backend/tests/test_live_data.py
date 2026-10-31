"""No network: operator data remains attributable and imports are atomic."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app import live_data
from app.live_data import (
    MANIFEST_COLUMNS, ManifestImport, ManualResourceInput, PassengerGenerationInput,
    prepare_generated_passengers, prepare_manifest, prepare_resource, relative_minute,
)
from app.models import Airport, World
from app.store import DomainError


NOW = datetime(2030, 5, 1, 8, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(live_data, "utcnow", lambda: NOW)


@pytest.fixture
def world():
    return World(scenario_id="operator-test", mode="live", epoch="2030-05-01T00:00:00Z", sim_time=480,
                 budget=0, airports=[], resources=[], groups=[])


def evidence():
    return dict(expected_version=1, idempotency_key="import-test-123", source_name="Operator manifest",
                source_reference="reference-123", confirmed_by="Duty operator", observed_at=NOW)


def csv_text(*rows):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=MANIFEST_COLUMNS)
    writer.writeheader()
    for index, changes in enumerate(rows, 1):
        writer.writerow(dict(passenger_id=f"P-{index}", group_id="G-1", name=f"Passenger {index}",
            origin="CRA", destination="LTN", original_flight_id="W41001", ready_at="2030-05-01T10:00:01Z",
            deadline="2030-05-01T18:00:59Z", group_budget_eur="250.50", accessible="false", priority="2",
            is_adult="true") | changes)
    return text.getvalue()


def manifest(*rows):
    return ManifestImport(csv_text=csv_text(*rows), **evidence())


def resource(**changes):
    return ManualResourceInput(**(dict(id="BUS-1", kind="bus", name="Confirmed transfer", origin="CRA", destination="OTP",
        departure_at="2030-05-01T10:00:30Z", arrival_at="2030-05-01T13:00:30Z", capacity=48,
        unit_price_eur="35.20", accessible=True, vehicle_id="REG-123", valid_until=NOW+timedelta(hours=1)) | evidence() | changes))


def generate(**changes):
    return PassengerGenerationInput(**(dict(expected_version=1, idempotency_key="generate-test-123", passengers=7,
        group_size=3, origin="CRA", destination="LTN", original_flight_id="W41001", ready_at="2030-05-01T10:00:01Z",
        deadline="2030-05-01T18:00:59Z", group_budget_eur="300", accessible=False, priority=2, is_adult=True) | changes))


def test_manifest_preserves_people_groups_evidence_and_unknown_airport_status(world):
    before = world.model_dump_json()
    result = prepare_manifest(world, manifest({}, {"accessible": "true", "is_adult": "false"}))
    assert world.model_dump_json() == before
    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group.name == "G-1" and group.size == 2 and group.members == ["Passenger 1", "Passenger 2"]
    assert group.source == "manifest_import" and group.adult_count == 1 and group.accessible
    assert group.budget == 250.50
    assert group.ready_at == 601 and group.deadline == 1080
    assert group.original_flight_id == "W41001" and result.get("resources") is None
    assert group.provenance["passenger_ids"] == ["P-1", "P-2"]
    assert group.provenance["confirmed_by"] == "Duty operator"
    assert len(result["provenance"]["sha256"]) == 64
    assert {airport.id for airport in result["airports"]} == {"CRA", "LTN"}
    assert all(not airport.available and airport.operational_status == "unknown" for airport in result["airports"])


@pytest.mark.parametrize("change", [
    {"passenger_id": "P-1"}, {"group_budget_eur": "250.51"}, {"origin": "OTP"},
    {"ready_at": "2030-05-01T10:00:02Z"}, {"accessible": "unknown"}, {"priority": "1.5"},
    {"is_adult": "1"}, {"name": ""}, {"deadline": "2030-05-01T07:00:00Z"},
    {"ready_at": "2030-05-01T10:00:00"}, {"origin": "ZZZ"}, {"group_budget_eur": "NaN"},
    {"group_budget_eur": "250.501"},
])
def test_manifest_invalid_late_row_never_mutates_world(world, change):
    before = world.model_dump_json()
    with pytest.raises(DomainError):
        prepare_manifest(world, manifest({}, change))
    assert world.model_dump_json() == before


def test_manifest_rejects_duplicate_existing_passengers_and_group_namespace_collision(world):
    first = prepare_manifest(world, manifest({}))
    world.groups.extend(first["groups"])
    with pytest.raises(DomainError):
        prepare_manifest(world, manifest({"group_id": "G-2"}))
    world.groups.clear()
    with pytest.raises(DomainError):
        prepare_manifest(world, manifest({"group_id": "CRA"}))


def test_manifest_accepts_equivalent_timezone_instants_and_rejects_oversized_groups(world):
    result = prepare_manifest(world, manifest({}, {"ready_at": "2030-05-01T12:00:01+02:00", "deadline": "2030-05-01T20:00:59+02:00"}))
    assert result["groups"][0].size == 2
    with pytest.raises(DomainError):
        prepare_manifest(world, manifest(*({} for _ in range(13))))


def test_manifest_headers_empty_and_row_shape_are_strict(world):
    for text in [",".join(MANIFEST_COLUMNS), csv_text({}).replace("is_adult", "other"), csv_text({})+"a,b\n"]:
        with pytest.raises(DomainError):
            prepare_manifest(world, ManifestImport(csv_text=text, **evidence()))


def test_confirmed_manual_resource_has_exact_source_and_expiry_but_no_booking(world):
    before = world.model_dump_json()
    result = prepare_resource(world, resource())
    value = result["resources"][0]
    assert value.kind == "bus" and value.source == "operator_confirmed"
    assert value.capacity == 48 and value.cost_per_person == 35.20 and value.vehicle_id == "REG-123"
    assert value.departure == 600 and value.arrival == 781
    assert value.capacity_scope == "operator_confirmed" and value.booking_status == "unbooked"
    assert value.valid_until == (NOW+timedelta(hours=1)).isoformat()
    assert value.provenance["departure_at"] == "2030-05-01T10:00:30+00:00"
    assert world.model_dump_json() == before


@pytest.mark.parametrize("change", [
    {"kind": "hotel"}, {"vehicle_id": None}, {"capacity": True}, {"accessible": "true"},
    {"unit_price_eur": "-1"}, {"unit_price_eur": "20.123"},
    {"arrival_at": "2030-05-01T09:00:00Z"}, {"name": "  "}, {"departure_at": "2030-05-01T10:00:00"},
])
def test_manual_resource_invalid_evidence_or_inventory_is_rejected(change):
    with pytest.raises(ValidationError):
        resource(**change)


def test_manual_resource_rejects_expired_confirmation_past_departure_and_future_evidence(world):
    for value in [resource(valid_until=NOW-timedelta(minutes=1), observed_at=NOW-timedelta(hours=1)),
                  resource(departure_at=NOW-timedelta(minutes=1)), resource(observed_at=NOW+timedelta(minutes=6))]:
        with pytest.raises(DomainError):
            prepare_resource(world, value)


def test_manual_bus_can_connect_real_station_node_without_creating_airport(world):
    station = Airport(id="ST-REAL", code="ST-REAL", name="Operator station", lat=44.31, lon=23.80,
                      available=True, kind="station")
    world.airports.append(station)
    result = prepare_resource(world, resource(origin="ST-REAL", destination="CRA"))
    assert result["resources"][0].origin == "ST-REAL"
    assert [node.id for node in result["airports"]] == ["CRA"]
    with pytest.raises(DomainError):
        prepare_resource(world, resource(kind="flight", origin="ST-REAL"))
    with pytest.raises(DomainError):
        prepare_resource(world, resource(origin="ST-MISSING"))


def test_explicit_generation_creates_planning_passengers_with_assumed_provenance(world):
    result = prepare_generated_passengers(world, generate())
    assert [group.size for group in result["groups"]] == [3, 3, 1]
    assert all(group.source == "synthetic" and group.name.startswith("Planning group") for group in result["groups"])
    assert all(name.startswith("Planning passenger") for group in result["groups"] for name in group.members)
    assert all(group.provenance["source"] == "operator_requested_test_passengers" for group in result["groups"])
    assert sum(group.adult_count for group in result["groups"]) == 7
    assert all(group.budget == 300 for group in result["groups"])
    assert len({group.id for group in result["groups"]}) == 3
    assert world.resources == [] and world.groups == [] and "resources" not in result


def test_generation_and_import_require_live_mode_and_aware_timestamps(world):
    world.mode = "simulation"
    with pytest.raises(DomainError):
        prepare_generated_passengers(world, generate())
    with pytest.raises(DomainError):
        prepare_manifest(world, manifest({}))
    with pytest.raises(ValidationError):
        generate(ready_at="2030-05-01T10:00:00")


def test_time_rounding_is_conservative_and_bounded(world):
    timestamp = datetime(2030, 5, 1, 10, 0, 1, tzinfo=timezone.utc)
    assert relative_minute(world, timestamp, rounding="up") == 601
    assert relative_minute(world, timestamp, rounding="down") == 600
    with pytest.raises(DomainError):
        relative_minute(world, timestamp+timedelta(days=8), rounding="up")
