"""Separate saved connection inputs and atomic success-only persistence, offline."""
from datetime import timedelta
import json

from fastapi.testclient import TestClient
import pytest

from app.after_flight_inputs import AfterFlightTransferInput
from app.after_flight_profile import AfterFlightProfileError, AfterFlightProfiles
from app.airport_cab_profile import AirportCabProfileError, AirportCabProfiles
from app.airport_cab_recovery import AirportCabRecoveryInput
from app.config import Settings
from app.main import create_app
from app.models import CaseAirportRestriction, IncidentFlightReference, PassengerGroup, RecoveryIncident
from app.recovery_jobs import RecoveryCancellation
from app.store import Store
from test_after_flight_recovery import setup, connection, request
from test_airport_cab_recovery_jobs import (
    NOW, env, change, manual_import, manual_quote, request as initial_request, run_next, stable,
)


@pytest.fixture
def saved(tmp_path):
    store = Store(str(tmp_path / "after-flight-profile.sqlite3"), operation_mode="live")
    def populate(world):
        world.scenario_id, world.epoch = "PROFILE-OPERATION", NOW.isoformat()
        world.groups = [PassengerGroup(id=identity, name="Private name " + identity,
            members=["PRIVATE PASSENGER " + identity], size=1, adult_count=1,
            origin="LHR", destination="CRA", ready_at=0, deadline=1800, budget=500,
            original_flight_id="ORIGINAL", source="manifest_import", token="PRIVATE-TOKEN-" + identity,
            incident_id="CASE" if identity != "G3" else "OTHER") for identity in ("G1", "G2", "G3")]
        world.incidents = [RecoveryIncident(id=identity, created_at=NOW.isoformat(), kind="destination_unavailable",
            description="Operator restriction", requested_flight_number="RO321" if identity == "CASE" else "RO999",
            date_utc=NOW.date().isoformat(), flight_ref=IncidentFlightReference(lookup_id="fixture", selection_id="fixture"),
            unavailable_airport_code="CRA", group_ids=["G1", "G2"] if identity == "CASE" else ["G3"])
            for identity in ("CASE", "OTHER")]
    change(store, populate)
    profiles = AfterFlightProfiles(store)
    inputs = AfterFlightTransferInput(**connection(pickup_window_start="2030-01-04T12:00:00+02:00",
        pickup_window_end="2030-01-04T20:00:00+02:00", suitcases=0, handbags=2, max_inbound_options=1))
    world = store.read()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        profiles.save_in_transaction(conn, world, world.groups[0], inputs, "JOB-ORIGINAL", now=NOW)
    return store, profiles, inputs


def record(store):
    with store.connect() as conn:
        row = conn.execute("SELECT data FROM after_flight_profiles WHERE group_id='G1'").fetchone()
        return json.loads(row[0]) if row else None


def assert_missing(profiles, world):
    with pytest.raises(AfterFlightProfileError) as caught:
        profiles.read(world, world.groups[0])
    assert caught.value.reason == "missing"


def test_exact_inputs_survive_restart_without_becoming_passenger_evidence(saved):
    store, profiles, inputs = saved
    before = store.read().model_dump(mode="json")
    found = AfterFlightProfiles(Store(store.path)).read(store.read(), store.read().groups[0])
    assert found.model_dump(mode="json") == inputs.model_dump(mode="json")
    assert found.pickup_window_start.isoformat() == "2030-01-04T12:00:00+02:00"
    found.suitcases = 7
    assert profiles.read(store.read(), store.read().groups[0]).suitcases == 0
    assert store.read().model_dump(mode="json") == before
    assert set(record(store)) == {"schema_version", "source", "scope_fingerprint", "inputs", "source_job_id", "saved_at"}
    assert record(store)["saved_at"] == NOW.isoformat()


@pytest.mark.parametrize("field,value", [
    ("id", "G9"), ("size", 2), ("adult_count", 0), ("members", ["Different person"]),
    ("source", "different_manifest"), ("origin", "SBZ"), ("destination", "OTP"),
    ("original_flight_id", "OTHER-FLIGHT"), ("incident_id", "OTHER"),
])
def test_party_route_and_original_flight_identity_changes_refuse_reuse(saved, field, value):
    store, profiles, _ = saved
    world = store.read()
    setattr(world.groups[0], field, value)
    with pytest.raises(AfterFlightProfileError):
        profiles.read(world, world.groups[0])
    assert not profiles.summary(world, world.groups[0])["configured"]


@pytest.mark.parametrize("change_name", ["scenario", "epoch", "case_date", "case_flight", "membership", "mode", "missing_group"])
def test_operation_and_reciprocal_case_binding(saved, change_name):
    store, profiles, _ = saved
    world = store.read()
    group = world.groups[0]
    if change_name == "scenario": world.scenario_id = "ANOTHER-OPERATION"
    elif change_name == "epoch": world.epoch = (NOW + timedelta(seconds=1)).isoformat()
    elif change_name == "case_date": world.incidents[0].date_utc = "2030-01-05"
    elif change_name == "case_flight": world.incidents[0].requested_flight_number = "RO999"
    elif change_name == "membership": world.incidents[0].group_ids = ["G2"]
    elif change_name == "mode": world.mode = "simulation"
    elif change_name == "missing_group": world.groups = world.groups[1:]
    with pytest.raises(AfterFlightProfileError):
        profiles.read(world, group)


def test_mutable_constraints_are_revalidated_by_caller_not_silently_rewritten(saved):
    store, profiles, inputs = saved
    world = store.read()
    group = world.groups[0]
    group.ready_at, group.deadline, group.budget = 1800, 1900, 1
    group.accessible, group.status, group.name, group.token = True, "confirmed", "Renamed", "rotated"
    group.provenance["arrival_reports"] = [{"description": "New report"}]
    world.incidents[0].airport_restrictions = [CaseAirportRestriction(airport_code="OTP", operation="arrivals",
        report_id="report-new", received_at=NOW.isoformat(), description="New restriction")]
    assert profiles.read(world, group).model_dump(mode="json") == inputs.model_dump(mode="json")
    summary = profiles.summary(world, group)
    assert summary["configured"] and summary["requires_current_validation"]
    assert summary["max_inbound_options"] == 1
    assert summary["minimum_total_calls"] == 7 and summary["two_variant_total_calls"] == 9


def test_initial_cab_profile_cannot_satisfy_afterflight_setup(saved):
    store, profiles, _ = saved
    initial = AirportCabProfiles(store)
    world = store.read()
    with pytest.raises(AirportCabProfileError) as caught:
        initial.read(world, world.groups[0])
    assert caught.value.reason == "missing"
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM after_flight_profiles")
        initial.save_in_transaction(conn, world, world.groups[0], AirportCabRecoveryInput(group_id="G1",
            destination_airport="CRA", pickup_at=NOW+timedelta(hours=10), suitcases=4, handbags=0), "INITIAL-JOB", now=NOW)
    assert initial.read(world, world.groups[0]).suitcases == 4
    assert_missing(profiles, world)


def test_saved_inputs_do_not_touch_world_audit_and_rollback_preserves_prior_profile(saved):
    store, profiles, inputs = saved
    world = store.read()
    original = record(store)
    with store.connect() as conn:
        before = conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        with pytest.raises(RuntimeError, match="active transaction"):
            profiles.save_in_transaction(conn, world, world.groups[0], inputs, "NO-TRANSACTION", now=NOW)
    with pytest.raises(RuntimeError, match="Commit failed"):
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profiles.save_in_transaction(conn, world, world.groups[0], inputs.model_copy(update={"suitcases": 3}), "NEW-JOB", now=NOW)
            assert profiles.read(world, world.groups[0], conn=conn).suitcases == 3
            assert conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0] == before
            assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == audit_count
            raise RuntimeError("Commit failed")
    assert record(store) == original


def test_projected_report_reads_use_supplied_transaction_and_do_not_rebind_origin(saved, monkeypatch):
    store, profiles, inputs = saved
    world = store.read()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        def forbidden():
            raise AssertionError("Profile read must share its supplied transaction")
        monkeypatch.setattr(store, "connect", forbidden)
        world.groups[0].ready_at += 30
        assert profiles.read(world, world.groups[0], conn=conn) == inputs
        assert profiles.public_for_case(world, "CASE", conn=conn)[0]["configured"]
        world.groups[0].origin = "OTP"
        with pytest.raises(AfterFlightProfileError) as caught:
            profiles.read(world, world.groups[0], conn=conn)
        assert caught.value.reason == "stale"


def test_public_and_reference_summary_do_not_leak_private_identity_or_supply_claims(saved):
    store, profiles, inputs = saved
    world = store.read()
    public = profiles.public_for_case(world, "CASE")
    assert public == [{"group_id": "G1", "configured": True, "inputs": inputs.model_dump(mode="json"),
        "source_job_id": "JOB-ORIGINAL", "saved_at": NOW.isoformat()},
        {"group_id": "G2", "configured": False, "reason": "missing"}]
    summary = profiles.summary(world, world.groups[0])
    assert summary["origin_airport"] == "LHR" and summary["via_airport"] == "OTP" and summary["destination_airport"] == "CRA"
    assert summary["pickup_window_start"] == inputs.pickup_window_start.isoformat()
    assert summary["pickup_window_end"] == inputs.pickup_window_end.isoformat()
    assert summary["airport_processing_minutes"] == 30
    combined = json.dumps([public, summary])
    for private in ("scope_fingerprint", "PRIVATE PASSENGER", "PRIVATE-TOKEN", "Private name", "G3", "price", "availability"):
        assert private not in combined
    assert "suitcases" not in json.dumps(summary) and "handbags" not in json.dumps(summary)
    assert profiles.public_for_case(world.model_dump(mode="json"), "CASE") == public


@pytest.mark.parametrize("corruption", ["bad_json", "large", "initial_inputs", "extra", "naive", "reversed", "boolean", "group", "saved_at", "source"])
def test_corrupt_or_wrong_profile_kind_fails_closed(saved, corruption):
    store, profiles, _ = saved
    raw = record(store)
    if corruption == "bad_json": encoded = "PRIVATE invalid JSON"
    elif corruption == "large": encoded = " " * 8193
    else:
        if corruption == "initial_inputs": raw["inputs"] = {"group_id": "G1", "destination_airport": "CRA", "pickup_at": NOW.isoformat(), "suitcases": 0, "handbags": 0}
        elif corruption == "extra": raw["inputs"]["supplier_quote"] = "PRIVATE quoted fare"
        elif corruption == "naive": raw["inputs"]["pickup_window_start"] = "2030-01-04T12:00:00"
        elif corruption == "reversed": raw["inputs"]["pickup_window_end"] = "2030-01-03T12:00:00+02:00"
        elif corruption == "boolean": raw["inputs"]["suitcases"] = True
        elif corruption == "group": raw["inputs"]["group_id"] = "G2"
        elif corruption == "saved_at": raw["saved_at"] = "2030-01-04T12:00:00"
        elif corruption == "source": raw["source"] = "booked"
        encoded = json.dumps(raw)
    with store.connect() as conn:
        conn.execute("UPDATE after_flight_profiles SET data=? WHERE group_id='G1'", (encoded,))
    world = store.read()
    with pytest.raises(AfterFlightProfileError) as caught:
        profiles.read(world, world.groups[0])
    assert caught.value.reason == "invalid" and "PRIVATE" not in str(caught.value)
    assert profiles.public_for_case(world, "CASE")[0] == {"group_id": "G1", "configured": False, "reason": "invalid"}


def test_authenticated_get_only_reads_exact_case_and_never_starts_recovery(saved):
    store, _, inputs = saved
    settings = Settings(_env_file=None, environment="test", operation_mode="live", database_path=store.path,
        operator_token="offline-profile-secret")
    app = create_app(settings)
    before = store.read().model_dump(mode="json")
    headers = {"Authorization": "Bearer offline-profile-secret"}
    with TestClient(app) as client:
        path = "/api/incidents/CASE/after-flight-transfer-profiles"
        assert client.get(path).status_code == 401
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["world_version"] == before["version"]
        assert response.json()["profiles"][0]["inputs"] == inputs.model_dump(mode="json")
        assert client.get("/api/incidents/MISSING/after-flight-transfer-profiles", headers=headers).status_code == 404
        other = client.get("/api/incidents/OTHER/after-flight-transfer-profiles", headers=headers).json()
        assert other["profiles"] == [{"group_id": "G3", "configured": False, "reason": "missing"}]
        initial = client.get("/api/incidents/CASE/airport-transfer-profiles", headers=headers).json()
        assert all(not item["configured"] for item in initial["profiles"])
        assert app.state.jobs.list("CASE")["jobs"] == []
        with store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM airport_cab_receipts").fetchone()[0] == 0
    assert store.read().model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_successful_chosen_complete_plan_saves_exact_inputs_once_with_commit(setup):
    e = setup
    original = request(e, after_flight_transfer=connection(suitcases=1, handbags=2, max_inbound_options=1))
    before = e.store.read()
    assert_missing(e.jobs.after_flight_profiles, before)
    started = e.jobs.start("CASE", original)
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    after = e.store.read()
    saved_inputs = e.jobs.after_flight_profiles.read(after, after.groups[0])
    assert saved_inputs == original.after_flight_transfer
    assert record(e.store)["source_job_id"] == job["id"]
    assert after.groups[0].origin == before.groups[0].origin and after.groups[0].ready_at == before.groups[0].ready_at
    assert after.groups[0].provenance == before.groups[0].provenance
    with pytest.raises(AirportCabProfileError):
        e.jobs.airport_cab_profiles.read(after, after.groups[0])
    exact_record = record(e.store)
    calls = (len(e.source.calls), len(e.flights.calls), len(e.ai.decisions))
    assert e.jobs.start("CASE", original)["job"]["id"] == started["job"]["id"]
    assert record(e.store) == exact_record
    assert (len(e.source.calls), len(e.flights.calls), len(e.ai.decisions)) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown", "no_category", "cancel", "decision_expiry"])
async def test_uncommitted_source_attempt_never_creates_profile(setup, failure):
    e = setup
    before = stable(e.store.read())
    if failure == "unknown": e.source.timeout_phase = "vehicles"
    elif failure == "no_category":
        def no_category(payload):
            payload.clear()
            payload["error"] = "no_vehicle_types_found"
        e.source.mutate_payload = no_category
    elif failure == "decision_expiry":
        async def expire(): e.clock[0] += timedelta(seconds=121)
        e.ai.before_choose = expire
    started = e.jobs.start("CASE", request(e))
    if failure == "cancel":
        e.source.after_bootstrap = lambda: e.jobs.cancel(started["job"]["id"], RecoveryCancellation(idempotency_key="profile-cancel"))
    job = await run_next(e.jobs)
    assert job["status"] == {"unknown": "unknown_outcome", "no_category": "blocked",
        "cancel": "cancelled", "decision_expiry": "needs_review"}[failure], job
    assert_missing(e.jobs.after_flight_profiles, e.store.read())
    assert stable(e.store.read()) == before


@pytest.mark.asyncio
async def test_failed_new_setup_preserves_prior_success_and_original_inputs(setup):
    e = setup
    e.jobs.start("CASE", request(e))
    first = await run_next(e.jobs)
    assert first["status"] == "completed", first
    original = record(e.store)
    original_world = stable(e.store.read())
    updated = connection(suitcases=2, handbags=3, airport_processing_minutes=45, max_inbound_options=1)
    e.source.timeout_phase = "vehicles"
    e.jobs.start("CASE", request(e, idempotency_key="profile-replacement-failed", after_flight_transfer=updated))
    failed = await run_next(e.jobs)
    assert failed["status"] == "unknown_outcome", failed
    assert record(e.store) == original and stable(e.store.read()) == original_world
    # A prior unknown source outcome is intentionally not retried here; that
    # terminal attempt cannot grant another dispatch or overwrite saved inputs.
    assert e.jobs.after_flight_profiles.read(e.store.read(), e.store.read().groups[0]).suitcases == 0


@pytest.mark.asyncio
async def test_profile_save_failure_rolls_back_world_ledger_and_partial_profile(setup, monkeypatch):
    e = setup
    before = stable(e.store.read())
    save = e.jobs.after_flight_profiles.save_in_transaction
    def fail_after_save(conn, world, group, inputs, job_id, now=None):
        save(conn, world, group, inputs, job_id, now=NOW)
        assert e.jobs.after_flight_profiles.read(world, group, conn=conn) == inputs
        raise RuntimeError("Offline final profile persistence failure")
    monkeypatch.setattr(e.jobs.after_flight_profiles, "save_in_transaction", fail_after_save)
    e.jobs.start("CASE", request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "needs_review" and job["calls_dispatched"] == 6
    assert stable(e.store.read()) == before
    assert_missing(e.jobs.after_flight_profiles, e.store.read())
    with e.store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM idempotency WHERE key LIKE 'recovery-job-apply:%'").fetchone()


@pytest.mark.asyncio
async def test_manual_source_quote_import_and_initial_success_do_not_make_afterflight_profile(env):
    e = env
    response = await manual_quote(e, "profile-manual-quote")
    assert_missing(e.jobs.after_flight_profiles, e.store.read())
    manual_import(e, response, "profile-manual-import")
    assert_missing(e.jobs.after_flight_profiles, e.store.read())
    e.jobs.start("CASE", initial_request(e))
    job = await run_next(e.jobs)
    assert job["status"] == "completed", job
    after = e.store.read()
    assert e.jobs.airport_cab_profiles.read(after, after.groups[0]).group_id == "G1"
    assert_missing(e.jobs.after_flight_profiles, after)
