"""Isolated saved-input persistence; no providers, app server or model calls."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.airport_cab_evidence import airport_cab_context
from app.airport_cab_profile import AirportCabProfileError, AirportCabProfiles
from app.airport_cab_recovery import AirportCabRecoveryInput
from app.initial_position import initial_position_fingerprint
from app.models import CaseAirportRestriction, IncidentFlightReference, PassengerGroup, RecoveryIncident
from app.store import Store


NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "profiles.sqlite3"), operation_mode="live")
    def populate(world):
        world.scenario_id, world.epoch = "PROFILE-OPERATION", NOW.isoformat()
        world.groups = [PassengerGroup(id=identity, name="Private display " + identity,
            members=["PRIVATE PASSENGER " + identity], size=1, adult_count=1,
            origin="OTP", destination="LHR", ready_at=600, deadline=1800, budget=500,
            original_flight_id="ARRIVED", source="manifest_import", token="PRIVATE-TOKEN-" + identity,
            incident_id="CASE" if identity != "G3" else "OTHER") for identity in ("G1", "G2", "G3")]
        world.incidents = [RecoveryIncident(id=identity, created_at=NOW.isoformat(), kind="destination_unavailable",
            description="Operator airport condition", requested_flight_number="RO321" if identity == "CASE" else "RO999",
            date_utc="2030-01-04", flight_ref=IncidentFlightReference(lookup_id="fixture", selection_id="fixture"),
            unavailable_airport_code="OTP", group_ids=["G1", "G2"] if identity == "CASE" else ["G3"])
            for identity in ("CASE", "OTHER")]
    store.mutate("fixture-profile-world", {}, store.read().version, populate)
    profiles = AirportCabProfiles(store)
    inputs = AirportCabRecoveryInput(group_id="G1", destination_airport="CRA",
        pickup_at="2030-01-04T12:00:00+02:00", suitcases=0, handbags=0)
    world = store.read()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        profiles.save_in_transaction(conn, world, world.groups[0], inputs, "JOB-ORIGINAL", now=NOW)
    return store, profiles, inputs


def raw_record(store):
    with store.connect() as conn:
        return json.loads(conn.execute("SELECT data FROM airport_cab_profiles WHERE group_id='G1'").fetchone()[0])


def overwrite(store, value):
    with store.connect() as conn:
        conn.execute("UPDATE airport_cab_profiles SET data=? WHERE group_id='G1'", (value,))


def test_exact_offset_zero_bags_and_detached_typed_inputs(setup):
    store, profiles, inputs = setup
    world = store.read()
    found = profiles.read(world, world.groups[0])
    assert isinstance(found, AirportCabRecoveryInput)
    assert found.model_dump(mode="json") == inputs.model_dump(mode="json")
    assert found.pickup_at.isoformat() == "2030-01-04T12:00:00+02:00"
    assert found.suitcases == found.handbags == 0
    found.suitcases = 4
    assert profiles.read(world, world.groups[0]).suitcases == 0
    stored = raw_record(store)
    assert set(stored) == {"schema_version", "source", "scope_fingerprint", "inputs", "source_job_id", "saved_at"}
    assert stored["schema_version"] == 1 and stored["source"] == "operator_requested"
    assert stored["source_job_id"] == "JOB-ORIGINAL" and stored["saved_at"] == NOW.isoformat()


@pytest.mark.parametrize("field,value", [
    ("size", 2), ("adult_count", 0), ("members", ["DIFFERENT PASSENGER"]),
    ("source", "another_manifest"), ("origin", "LHR"), ("destination", "CRA"),
    ("original_flight_id", "ANOTHER-FLIGHT"), ("incident_id", "OTHER"), ("id", "G9"),
])
def test_identity_changes_reject_reuse(setup, field, value):
    store, profiles, _ = setup
    world = store.read()
    setattr(world.groups[0], field, value)
    with pytest.raises(AirportCabProfileError):
        profiles.read(world, world.groups[0])


@pytest.mark.parametrize("change", ["scenario", "epoch", "case_date", "case_flight", "membership", "mode", "absent_group"])
def test_operation_and_reciprocal_case_identity_are_required(setup, change):
    store, profiles, _ = setup
    world = store.read()
    group = world.groups[0]
    if change == "scenario": world.scenario_id = "ANOTHER-OPERATION"
    elif change == "epoch": world.epoch = (NOW + timedelta(minutes=1)).isoformat()
    elif change == "case_date": world.incidents[0].date_utc = "2030-01-05"
    elif change == "case_flight": world.incidents[0].requested_flight_number = "OTHER1"
    elif change == "membership": world.incidents[0].group_ids = ["G2"]
    elif change == "mode": world.mode = "simulation"
    elif change == "absent_group": world.groups = world.groups[1:]
    with pytest.raises(AirportCabProfileError):
        profiles.read(world, group)


@pytest.mark.parametrize("field,value", [
    ("name", "New display"), ("token", "rotated-private-token"), ("status", "confirmed"),
    ("ready_at", 1801), ("deadline", 500), ("budget", 1), ("priority", 4), ("accessible", True),
])
def test_mutable_constraints_do_not_rewrite_identity_or_assert_current_eligibility(setup, field, value):
    store, profiles, inputs = setup
    world = store.read()
    setattr(world.groups[0], field, value)
    world.groups[0].provenance["arrival_reports"] = [{"description": "New operator report"}]
    world.incidents[0].description = "Changed case narrative"
    world.incidents[0].airport_restrictions = [CaseAirportRestriction(airport_code="CRA", operation="departures",
        report_id="report-new", received_at=NOW.isoformat(), description="New operator constraint")]
    assert profiles.read(world, world.groups[0]).model_dump(mode="json") == inputs.model_dump(mode="json")
    summary = profiles.summary(world, world.groups[0])
    assert summary["configured"] and summary["requires_current_validation"]


def test_saving_never_modifies_world_provenance_or_physical_evidence_hashes(setup):
    store, profiles, inputs = setup
    world = store.read()
    group = world.groups[0]
    before = world.model_dump(mode="json")
    position_hash = initial_position_fingerprint(world, group)
    cab_context = airport_cab_context(world, group)
    with store.connect() as conn:
        raw_world = conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        conn.execute("BEGIN IMMEDIATE")
        profiles.save_in_transaction(conn, world, group, inputs, "JOB-NEW", now=NOW + timedelta(seconds=1))
        assert conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0] == raw_world
        assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == audit_count
    assert world.model_dump(mode="json") == before
    assert initial_position_fingerprint(world, group) == position_hash
    assert airport_cab_context(world, group) == cab_context
    assert store.read().groups[0].provenance == group.provenance


def test_save_requires_transaction_and_rollback_preserves_earlier_profile(setup):
    store, profiles, inputs = setup
    world = store.read()
    original = raw_record(store)
    with store.connect() as conn:
        with pytest.raises(RuntimeError, match="active transaction"):
            profiles.save_in_transaction(conn, world, world.groups[0], inputs, "JOB-NO-TRANSACTION", now=NOW)
    changed = inputs.model_copy(update={"suitcases": 2})
    with pytest.raises(RuntimeError, match="Final commit failed"):
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profiles.save_in_transaction(conn, world, world.groups[0], changed, "JOB-ROLLBACK", now=NOW)
            assert profiles.read(world, world.groups[0], conn=conn).suitcases == 2
            raise RuntimeError("Final commit failed")
    assert raw_record(store) == original


def test_reads_share_transaction_and_validate_projected_report_scope(setup, monkeypatch):
    store, profiles, inputs = setup
    world = store.read()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        def no_connection():
            raise AssertionError("A supplied transaction must not open another connection")
        monkeypatch.setattr(store, "connect", no_connection)
        profiles.save_in_transaction(conn, world, world.groups[0], inputs, "JOB-IN-TRANSACTION", now=NOW)
        world.groups[0].ready_at += 10
        assert profiles.read(world, world.groups[0], conn=conn).pickup_at == inputs.pickup_at
        assert profiles.public_for_case(world, "CASE", conn=conn)[0]["source_job_id"] == "JOB-IN-TRANSACTION"
        world.groups[0].origin = "LHR"
        with pytest.raises(AirportCabProfileError) as rejected:
            profiles.read(world, world.groups[0], conn=conn)
        assert rejected.value.reason == "stale"


def test_public_case_and_summary_are_bounded_reference_data_without_identity_leakage(setup):
    store, profiles, inputs = setup
    world = store.read()
    public = profiles.public_for_case(world, "CASE")
    assert public == [{"group_id": "G1", "configured": True, "inputs": inputs.model_dump(mode="json"),
        "source_job_id": "JOB-ORIGINAL", "saved_at": NOW.isoformat()},
        {"group_id": "G2", "configured": False, "reason": "missing"}]
    summary = profiles.summary(world, world.groups[0])
    assert summary == {"configured": True, "source": "operator_requested",
        "pickup_at": "2030-01-04T12:00:00+02:00", "origin_airport": "OTP", "destination_airport": "CRA",
        "minimum_total_calls": 4, "requires_current_validation": True}
    serialized = json.dumps([public, summary])
    for private in ("scope_fingerprint", "PRIVATE PASSENGER", "PRIVATE-TOKEN", "Private display", "G3"):
        assert private not in serialized
    assert "suitcases" not in json.dumps(summary) and "handbags" not in json.dumps(summary)
    assert profiles.public_for_case(world.model_dump(mode="json"), "CASE") == public
    world.groups[0].origin = "LHR"
    assert profiles.public_for_case(world, "CASE")[0] == {"group_id": "G1", "configured": False, "reason": "stale"}
    world.incidents[0].group_ids.append("G3")
    assert profiles.public_for_case(world, "CASE")[-1] == {"group_id": "G3", "configured": False, "reason": "scope"}


@pytest.mark.parametrize("corruption", ["bad_json", "null", "empty", "large", "schema", "source", "fingerprint",
    "group", "naive_pickup", "seconds", "bool_bags", "negative_bags", "unknown_field", "saved_at", "job_id"])
def test_corrupt_records_fail_closed_without_leaking_raw_data(setup, corruption):
    store, profiles, _ = setup
    raw = raw_record(store)
    if corruption == "bad_json": encoded = "PRIVATE invalid JSON"
    elif corruption == "null": encoded = "null"
    elif corruption == "empty": encoded = "{}"
    elif corruption == "large": encoded = " " * 8193
    else:
        if corruption == "schema": raw["schema_version"] = True
        elif corruption == "source": raw["source"] = "supplier_booked"
        elif corruption == "fingerprint": raw["scope_fingerprint"] = "bad PRIVATE hash"
        elif corruption == "group": raw["inputs"]["group_id"] = "G2"
        elif corruption == "naive_pickup": raw["inputs"]["pickup_at"] = "2030-01-04T12:00:00"
        elif corruption == "seconds": raw["inputs"]["pickup_at"] = "2030-01-04T12:00:01+02:00"
        elif corruption == "bool_bags": raw["inputs"]["suitcases"] = True
        elif corruption == "negative_bags": raw["inputs"]["handbags"] = -1
        elif corruption == "unknown_field": raw["PRIVATE unexpected"] = "ignored?"
        elif corruption == "saved_at": raw["saved_at"] = "2030-01-04T00:00:00"
        elif corruption == "job_id": raw["source_job_id"] = "JOB\nPRIVATE"
        encoded = json.dumps(raw)
    overwrite(store, encoded)
    world = store.read()
    with pytest.raises(AirportCabProfileError) as rejected:
        profiles.read(world, world.groups[0])
    assert rejected.value.reason == "invalid" and "PRIVATE" not in str(rejected.value)
    assert profiles.public_for_case(world, "CASE")[0] == {"group_id": "G1", "configured": False, "reason": "invalid"}


@pytest.mark.parametrize("change", ["wrong_input_group", "bad_bags", "bad_job", "naive_saved", "unlinked", "simulation"])
def test_invalid_save_never_overwrites_prior_inputs(setup, change):
    store, profiles, inputs = setup
    original = raw_record(store)
    world = store.read()
    values, job_id, saved_at = inputs.model_dump(mode="json"), "JOB-INVALID", NOW
    if change == "wrong_input_group": values["group_id"] = "G2"
    elif change == "bad_bags": values["suitcases"] = True
    elif change == "bad_job": job_id = " "
    elif change == "naive_saved": saved_at = NOW.replace(tzinfo=None)
    elif change == "unlinked": world.incidents[0].group_ids = ["G2"]
    elif change == "simulation": world.mode = "simulation"
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(AirportCabProfileError):
            profiles.save_in_transaction(conn, world, world.groups[0], values, job_id, now=saved_at)
    assert raw_record(store) == original


def test_restart_retains_saved_inputs_and_replacement_metadata(setup):
    store, profiles, inputs = setup
    world = store.read()
    revised = inputs.model_copy(update={"handbags": 3})
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        profiles.save_in_transaction(conn, world, world.groups[0], revised, "JOB-REPLACEMENT", now=NOW + timedelta(seconds=30))
    reopened = AirportCabProfiles(Store(store.path))
    assert reopened.read(world, world.groups[0]).handbags == 3
    public = reopened.public_for_case(world, "CASE")[0]
    assert public["source_job_id"] == "JOB-REPLACEMENT"
    assert public["saved_at"] == (NOW + timedelta(seconds=30)).isoformat()
