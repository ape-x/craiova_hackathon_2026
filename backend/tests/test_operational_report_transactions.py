"""Report, dependent job and replay receipt share one isolated SQLite commit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.hotel_recovery import validate_hotel_job
from app.hotel_recovery_profile import read_hotel_recovery_profile
from app.operational_reports import ArrivalReportInput, apply_arrival_report, apply_resource_unavailability
from app.recovery_jobs import RecoveryJobs, RecoveryJobRequest, scope_fingerprint
from app.store import DomainError
from test_operational_reports import reports, arrival, unavailable, stable
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup


@pytest.fixture
def transaction_setup(reports):
    settings, store, _ = reports
    # Queuing must not inspect a provider or start a worker.
    jobs = RecoveryJobs(settings, store, None, object(), object())
    return store, jobs


def combined(store, jobs, kind, request, *, fault=None, hotel_recovery=None):
    key = "command:" + request.idempotency_key
    target = "RC-ONE" if kind == "arrival" else "Q1"
    payload = {"kind": kind, "target": target, **request.model_dump(mode="json")}

    def apply(world):
        if kind == "arrival":
            return apply_arrival_report(world, target, request)
        return apply_resource_unavailability(world, target, request)

    def enqueue(conn, response):
        assert conn.in_transaction
        assert conn.execute("SELECT 1 FROM idempotency WHERE key=?", (key,)).fetchone() is None
        post_report_world = store._load(conn)
        assert response["world"]["version"] == post_report_world.version
        if fault == "before_job":
            raise RuntimeError("Fixture failure after report, before job")
        job_request = RecoveryJobRequest(expected_version=post_report_world.version,
            idempotency_key="job-" + request.idempotency_key, group_ids=["G1"],
            modes=["flight", "hotel", "bus"] if hotel_recovery else ["flight"],
            max_provider_calls=10 if hotel_recovery else 3, hotel_recovery=hotel_recovery)
        result = jobs.start_in_transaction(conn, post_report_world, "RC-ONE", job_request)
        response.update(recovery_job=result["job"], incident_id="RC-ONE", callback_receipt="recorded")
        if fault == "after_job":
            raise RuntimeError("Fixture failure after job, before receipt")

    return store.mutate(key, payload, request.expected_version, apply, on_commit=enqueue)


def counts(store, key):
    with store.connect() as conn:
        return (conn.execute("SELECT COUNT(*) FROM recovery_jobs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM idempotency WHERE key=?", (key,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0])


@pytest.mark.parametrize("kind", ["arrival", "unavailable"])
def test_atomic_report_job_and_enriched_receipt_use_post_report_scope(transaction_setup, kind):
    store, jobs = transaction_setup
    request = arrival(store) if kind == "arrival" else unavailable(store)
    before = store.read()
    old_scope = scope_fingerprint(before, "RC-ONE", ["G1"])
    result = combined(store, jobs, kind, request)
    after = store.read()
    job = jobs.ledger.get(result["recovery_job"]["id"], private=True)
    assert after.version == before.version + 1
    assert job["source_version"] == job["_request"]["expected_version"] == after.version
    assert job["_scope_hash"] == scope_fingerprint(after, "RC-ONE", ["G1"]) != old_scope
    assert job["group_ids"] == ["G1"] and job["status"] == "queued"
    assert job["calls_dispatched"] == 0 and jobs.ledger.get(job["id"])["steps"] == []
    assert result["callback_receipt"] == "recorded" and result["applied"] is True
    assert after.groups[-1] == before.groups[-1]
    assert after.offers[-1] == before.offers[-1] and after.reservations[-1] == before.reservations[-1]
    with store.connect() as conn:
        saved = json.loads(conn.execute("SELECT result FROM idempotency WHERE key=?",
            ("command:"+request.idempotency_key,)).fetchone()[0])
    assert saved["recovery_job"]["id"] == job["id"] and saved["callback_receipt"] == "recorded"
    replay = combined(store, jobs, kind, request)
    assert replay["replayed"] is True and replay["report"] == result["report"]
    assert replay["recovery_job"] == result["recovery_job"] and replay["callback_receipt"] == "recorded"
    assert stable(store.read()) == stable(after)


@pytest.mark.parametrize("kind", ["arrival", "unavailable"])
@pytest.mark.parametrize("fault", ["before_job", "after_job", "receipt_insert"])
def test_report_job_audit_and_receipt_roll_back_together(transaction_setup, kind, fault):
    store, jobs = transaction_setup
    request = arrival(store) if kind == "arrival" else unavailable(store)
    key = "command:" + request.idempotency_key
    before = stable(store.read())
    before_counts = counts(store, key)
    if fault == "receipt_insert":
        with store.connect() as conn:
            # Fail the real final INSERT after the world and job have been written.
            conn.execute("CREATE TRIGGER reject_command_receipt BEFORE INSERT ON idempotency "
                "WHEN NEW.key LIKE 'command:%' BEGIN SELECT RAISE(ABORT, 'Fixture receipt write failed'); END")
    with pytest.raises(sqlite3.IntegrityError if fault == "receipt_insert" else RuntimeError):
        combined(store, jobs, kind, request, fault=fault)
    assert stable(store.read()) == before
    assert counts(store, key) == before_counts
    if fault == "receipt_insert":
        with store.connect() as conn:
            conn.execute("DROP TRIGGER reject_command_receipt")
    result = combined(store, jobs, kind, request)
    assert result["applied"] is True and result["replayed"] is False
    assert counts(store, key)[:2] == (1, 1)


def test_concurrent_same_request_produces_one_report_one_job_and_same_receipt(transaction_setup):
    store, jobs = transaction_setup
    request = arrival(store)
    before = store.read()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: combined(store, jobs, "arrival", request), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["report"] == results[1]["report"]
    assert results[0]["recovery_job"] == results[1]["recovery_job"]
    after = store.read()
    assert after.version == before.version + 1
    assert len(after.groups[0].provenance["arrival_reports"]) == 1
    assert counts(store, "command:"+request.idempotency_key)[:2] == (1, 1)


@pytest.mark.parametrize("kind", ["arrival", "unavailable"])
def test_confirmed_booking_blocks_composed_report_without_queue_or_receipt(transaction_setup, kind):
    store, jobs = transaction_setup
    def confirm(world):
        world.groups[0].status = "confirmed"
        world.offers[0].status = "confirmed"
        world.offers[0].external_bookings = [{"resource_id": "Q1", "confirmation_reference": "fixture-booking"}]
        world.reservations[0].status = "confirmed"
    store.mutate("fixture-confirmed", {}, store.read().version, confirm)
    request = arrival(store) if kind == "arrival" else unavailable(store)
    before = stable(store.read())
    with pytest.raises(DomainError) as blocked:
        combined(store, jobs, kind, request)
    assert blocked.value.status == 409
    assert stable(store.read()) == before
    assert counts(store, "command:"+request.idempotency_key)[:2] == (0, 0)


def test_active_group_guard_rolls_back_new_report_and_keeps_original_job(transaction_setup):
    store, jobs = transaction_setup
    first = combined(store, jobs, "arrival", arrival(store))
    before = stable(store.read())
    request = arrival(store, idempotency_key="another-report-transaction")
    with pytest.raises(DomainError) as conflict:
        combined(store, jobs, "arrival", request)
    assert conflict.value.details == {"code": "recovery_group_active"}
    assert stable(store.read()) == before
    assert jobs.ledger.get(first["recovery_job"]["id"])["status"] == "queued"
    assert counts(store, "command:"+request.idempotency_key)[:2] == (1, 0)


def test_hotel_inputs_are_validated_against_relocated_world_before_queueing(transaction_setup):
    store, jobs = transaction_setup
    before = store.read()
    inputs = read_hotel_recovery_profile(before, before.groups[0])
    valid = RecoveryJobRequest(expected_version=before.version, idempotency_key="hotel-preflight",
        group_ids=["G1"], modes=["flight", "hotel", "bus"], max_provider_calls=10, hotel_recovery=inputs)
    validate_hotel_job(before, valid)
    request = arrival(store)  # UK configuration cannot be used after relocation to SBZ.
    with pytest.raises(DomainError) as incompatible:
        combined(store, jobs, "arrival", request, hotel_recovery=inputs)
    assert incompatible.value.status == 422
    assert stable(store.read()) == stable(before)
    assert counts(store, "command:"+request.idempotency_key)[:2] == (0, 0)


def test_transaction_start_requires_caller_transaction_and_current_persisted_version(transaction_setup):
    store, jobs = transaction_setup
    world = store.read()
    request = RecoveryJobRequest(expected_version=world.version, idempotency_key="transaction-version",
        group_ids=["G1"], max_provider_calls=3)
    with store.connect() as conn:
        with pytest.raises(RuntimeError, match="active transaction"):
            jobs.start_in_transaction(conn, world, "RC-ONE", request)
        conn.execute("BEGIN IMMEDIATE")
        forged = world.model_copy(update={"version": world.version + 1})
        with pytest.raises(DomainError) as stale:
            jobs.start_in_transaction(conn, forged, "RC-ONE",
                request.model_copy(update={"expected_version": forged.version}))
        assert stale.value.details == {"code": "recovery_world_changed"}
    assert counts(store, "unused")[:2] == (0, 0)


def test_transaction_job_replay_preserves_identity_and_rejects_changed_payload(transaction_setup):
    store, jobs = transaction_setup
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        world = store._load(conn)
        request = RecoveryJobRequest(expected_version=world.version, idempotency_key="direct-transaction-job",
            group_ids=["G1"], max_provider_calls=3)
        first = jobs.start_in_transaction(conn, world, "RC-ONE", request)
        replay = jobs.start_in_transaction(conn, world, "RC-ONE", request)
        assert replay["replayed"] and first["job"] == replay["job"]
        with pytest.raises(DomainError) as conflict:
            jobs.start_in_transaction(conn, world, "RC-ONE", request.model_copy(update={"objective": "fastest"}))
        assert conflict.value.details == {"code": "recovery_job_key_conflict"}
    assert counts(store, "unused")[:2] == (1, 0)


def test_full_queue_rejects_atomic_report_without_losing_existing_work(transaction_setup):
    store, jobs = transaction_setup
    group_ids = [f"QUEUED-{index}" for index in range(20)]
    def seed(world):
        reference = world.groups[0]
        world.groups.extend(reference.model_copy(deep=True, update={"id": group_id,
            "token": reference.token + group_id, "status": "waiting"}) for group_id in group_ids)
        next(item for item in world.incidents if item.id == "RC-ONE").group_ids.extend(group_ids)
    store.mutate("populate-queue-groups", {}, store.read().version, seed)
    for group_id in group_ids:
        jobs.start("RC-ONE", RecoveryJobRequest(expected_version=store.read().version,
            idempotency_key="queue-" + group_id, group_ids=[group_id], max_provider_calls=3))
    before = stable(store.read())
    request = unavailable(store)
    with pytest.raises(DomainError) as full:
        combined(store, jobs, "unavailable", request)
    assert full.value.status == 429 and full.value.details == {"code": "recovery_queue_full"}
    assert stable(store.read()) == before
    assert counts(store, "command:"+request.idempotency_key)[:2] == (20, 0)


def test_server_relative_readiness_basis_is_preserved_without_expanding_typed_endpoint(transaction_setup):
    store, _ = transaction_setup
    request = arrival(store)
    world = store.read()
    before = stable(world)
    anchor = request.ready_at - timedelta(minutes=30)
    basis = {"kind": "command_relative_minutes", "reference": "în 30 de minute",
        "command_received_at": (anchor - timedelta(seconds=23)).isoformat(),
        "anchor_at": anchor.isoformat(), "rounding": "ceil_minute", "minutes": 30}
    result = apply_arrival_report(world, "RC-ONE", request, readiness_basis=basis)
    saved_basis = result["report"]["readiness_basis"]
    assert saved_basis == basis
    basis["minutes"] = 99
    assert saved_basis["minutes"] == 30
    assert world.groups[0].provenance["arrival_reports"][-1]["readiness_basis"] == saved_basis
    assert world.events[-1].data["readiness_basis"] == saved_basis
    assert stable(store.read()) == before  # The shared helper never persists on its own.
    with pytest.raises(ValidationError):
        ArrivalReportInput.model_validate({**request.model_dump(), "readiness_basis": saved_basis})
