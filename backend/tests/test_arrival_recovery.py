"""Structured report/recovery transactions; isolated SQLite and fake sources only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.operational_reports import ArrivalReportInput, OperationalReports, apply_arrival_report
from app.store import DomainError
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup
from test_recovery_jobs import request as job_request, run_next, setup


def arrival(store, **changes):
    world = store.read()
    ready = datetime.fromisoformat(world.epoch) + timedelta(minutes=world.groups[0].ready_at + 30)
    return ArrivalReportInput(**({"expected_version": world.version,
        "idempotency_key": "structured-arrival-recovery", "group_ids": ["G1"],
        "airport_code": "LHR", "ready_at": ready,
        "description": "Operator reports these passengers ready at London Heathrow.",
        "recovery": {"objective": "fastest", "modes": ["flight"], "max_provider_calls": 3}} | changes))


def stable(store):
    return store.read().model_dump(exclude={"sim_time"})


@pytest.mark.asyncio
async def test_structured_arrival_queues_exact_reported_scope_and_completes_with_original_quota(setup):
    _, store, jobs, ai, travel = setup
    before = store.read()
    request = arrival(store)
    reports = OperationalReports(store, jobs)
    result = reports.arrival("CASE", request)
    job = result["recovery_job"]
    assert result["applied"] and result["focus_id"] == "LHR"
    assert result["command_interpretation_calls"] == 0
    assert result["recovery_provider_call_limit"] == job["max_provider_calls"] == 3
    assert job["group_ids"] == ["G1"] and job["objective"] == "fastest"
    assert job["status"] == "queued" and job["calls_dispatched"] == 0
    assert job["source_version"] == before.version + 1
    assert not ai.plans and not ai.decisions and not travel.calls
    after = store.read()
    assert after.groups[0].origin == "LHR" and after.groups[1:] == before.groups[1:]
    assert after.incidents == before.incidents and after.resources == before.resources
    node = next(item for item in after.airports if item.id == "LHR")
    assert not node.available and node.operational_status == "unknown"
    final = await run_next(jobs)
    assert final["status"] == "completed", final
    assert final["calls_dispatched"] == 3 and len(travel.calls) == 1
    assert travel.calls[0].origin_code == "LHR" and travel.calls[0].destination_code == "OTP"
    assert len(ai.plans) == len(ai.decisions) == 1
    after = stable(store)
    replay = OperationalReports(store, jobs).arrival("CASE", request)
    assert replay["replayed"] and replay["report"] == result["report"]
    assert replay["recovery_job"]["id"] == job["id"] and stable(store) == after
    assert jobs.ledger.claim_next() is None and len(travel.calls) == 1


def test_structured_http_route_uses_injected_jobs_and_replays_without_interpretation(setup):
    settings, store, _, ai, travel = setup
    settings.operator_token = "offline-arrival-test-token"
    app = create_app(settings)
    app.state.jobs.ai = ai
    app.state.jobs.travel = travel
    app.state.jobs.start_worker = lambda: None
    body = arrival(store).model_dump(mode="json")
    path = "/api/incidents/CASE/arrival-report"
    headers = {"Authorization": "Bearer " + settings.operator_token}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        first = client.post(path, json=body, headers=headers)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["applied"] and result["command_interpretation_calls"] == 0
        assert result["recovery_job"]["group_ids"] == result["report"]["group_ids"] == ["G1"]
        replay = client.post(path, json=body, headers=headers)
        assert replay.status_code == 200 and replay.json()["replayed"]
        conflict = client.post(path, json={**body, "recovery": {"max_provider_calls": 8}}, headers=headers)
        assert conflict.status_code == 409
        assert len(app.state.jobs.list("CASE")["jobs"]) == 1
    assert not ai.plans and not ai.decisions and not travel.calls


def test_same_key_concurrency_records_one_arrival_and_one_job(setup):
    _, store, jobs, _, _ = setup
    reports, request = OperationalReports(store, jobs), arrival(store)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reports.arrival("CASE", request), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["report"] == results[1]["report"]
    assert results[0]["recovery_job"]["id"] == results[1]["recovery_job"]["id"]
    assert len(jobs.list("CASE")["jobs"]) == 1
    assert len(store.read().groups[0].provenance["arrival_reports"]) == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_arrival_successor_waits_for_prior_dispatch_and_never_bypasses_unknown_outcome(setup, unknown):
    _, store, jobs, _, travel = setup
    old = jobs.start("CASE", job_request(store, max_provider_calls=6))["job"]
    _, owner = jobs.ledger.claim_next()
    step = jobs.ledger.dispatch(old["id"], owner, "searching", "Offline fixture", {"old": True}, ["G1"])
    receipt = OperationalReports(store, jobs).arrival("CASE", arrival(store))
    new = receipt["recovery_job"]
    assert new["stage"] == "waiting_for_previous" and new["supersedes_job_ids"] == [old["id"]]
    assert new["calls_dispatched"] == 0 and new["max_provider_calls"] == 3
    assert jobs.get(old["id"])["job"]["cancel_requested"] is True
    assert jobs.get(old["id"])["job"]["max_provider_calls"] == 6
    assert jobs.ledger.claim_next() is None
    if not unknown:
        jobs.ledger.outcome(old["id"], owner, step, "succeeded", "Offline known outcome", {})
    jobs.ledger.finish(old["id"], owner, "cancelled", "Offline drained attempt")
    claimed = jobs.ledger.claim_next()
    if unknown:
        assert claimed is None
        assert jobs.get(new["id"])["job"]["status"] == "needs_review"
        assert jobs.get(new["id"])["job"]["blocking_job_ids"] == [old["id"]]
    else:
        assert claimed[0]["id"] == new["id"]
    assert jobs.get(new["id"])["job"]["calls_dispatched"] == 0 and not travel.calls


@pytest.mark.parametrize("known", [False, True])
def test_failed_enqueue_rolls_back_partial_successor_and_predecessor_cancellation(setup, monkeypatch, known):
    _, store, jobs, _, _ = setup
    original = jobs.start("CASE", job_request(store))["job"]
    before = stable(store)
    start = jobs.start_in_transaction
    def partial_then_fail(*args, **kwargs):
        start(*args, **kwargs)
        if known:
            raise DomainError(429, "Offline queue rejection after partial write", {"code": "recovery_queue_full"})
        raise RuntimeError("Offline unexpected failure after partial write")
    monkeypatch.setattr(jobs, "start_in_transaction", partial_then_fail)
    reports, request = OperationalReports(store, jobs), arrival(store)
    if known:
        result = reports.arrival("CASE", request)
        assert result["applied"] and result["recovery_blocked"]["code"] == "recovery_queue_unavailable"
        assert "recovery_job" not in result and store.read().groups[0].origin == "LHR"
        monkeypatch.setattr(jobs, "start_in_transaction", start)
        replay = reports.arrival("CASE", request)
        assert replay["replayed"] and replay["recovery_blocked"] == result["recovery_blocked"]
    else:
        with pytest.raises(RuntimeError, match="unexpected"):
            reports.arrival("CASE", request)
        assert stable(store) == before
        with store.connect() as conn:
            assert conn.execute("SELECT count(*) FROM idempotency WHERE key=?", ("operator-arrival:" + request.idempotency_key,)).fetchone()[0] == 0
    only = jobs.list("CASE")["jobs"]
    assert len(only) == 1 and only[0]["id"] == original["id"]
    assert only[0]["status"] == "queued" and not only[0]["cancel_requested"]
    assert "superseded_by_job_id" not in only[0]


@pytest.mark.parametrize("with_inputs", [False, True])
def test_changed_origin_never_reuses_saved_hotel_or_accepts_old_pickup(hotel_command_setup, with_inputs):
    _, store, _, jobs, _, inputs = hotel_command_setup
    options = {"modes": ["flight", "hotel", "bus"], "max_provider_calls": 10}
    if with_inputs:
        options["hotel_recovery"] = inputs.model_dump(mode="json")
    result = OperationalReports(store, jobs).arrival("RC-ONE", arrival(store, airport_code="SBZ", recovery=options))
    assert result["applied"] and result["focus_id"] == "SBZ"
    assert result["recovery_blocked"]["code"] == "hotel_setup_required"
    assert "recovery_job" not in result and jobs.list("RC-ONE")["jobs"] == []
    assert store.read().groups[0].origin == "SBZ"


def test_explicit_fresh_hotel_inputs_are_validated_on_post_report_scope(hotel_command_setup):
    _, store, _, jobs, _, inputs = hotel_command_setup
    result = OperationalReports(store, jobs).arrival("RC-ONE", arrival(store, airport_code="LHR",
        ready_at=inputs.pickup_at - timedelta(minutes=30),
        recovery={"modes": ["flight", "hotel", "bus"], "max_provider_calls": 10,
                  "hotel_recovery": inputs.model_dump(mode="json")}))
    assert result["applied"] and result["recovery_job"]["max_provider_calls"] == 10
    with store.connect() as conn:
        saved = jobs.ledger._load(conn, result["recovery_job"]["id"])
    assert saved["_request"]["hotel_recovery"] == inputs.model_dump(mode="json")
    assert saved["_request"]["group_ids"] == ["G1"]


@pytest.mark.parametrize("reason", ["no_jobs", "no_ai", "too_many_groups"])
def test_known_prerequisites_preserve_report_without_narrowing_or_spending(setup, reason):
    _, store, jobs, ai, travel = setup
    ids = ["G1"]
    if reason == "too_many_groups":
        def many(world):
            for i in range(3, 26):
                group = world.groups[0].model_copy(deep=True)
                group.id = f"G{i}"
                world.groups.append(group)
                world.incidents[0].group_ids.append(group.id)
        store.mutate("offline-many-groups", {}, store.read().version, many)
        ids = [group.id for group in store.read().groups]
    if reason == "no_ai":
        jobs.ai = None
    result = OperationalReports(store, None if reason == "no_jobs" else jobs).arrival("CASE", arrival(store, group_ids=ids))
    assert result["applied"] and "recovery_blocked" in result and "recovery_job" not in result
    assert set(result["report"]["group_ids"]) == set(ids)
    assert all(group.origin == "LHR" for group in store.read().groups if group.id in ids)
    assert jobs.list("CASE")["jobs"] == [] and not ai.plans and not travel.calls


def test_report_only_remains_default_and_replays_pre_extension_payload(setup):
    _, store, jobs, _, _ = setup
    request = arrival(store, recovery=None)
    payload = {**request.model_dump(mode="json"), "incident_id": "CASE"}
    assert "recovery" not in payload
    old = store.mutate("operator-arrival:" + request.idempotency_key, payload, request.expected_version,
        lambda world: apply_arrival_report(world, "CASE", request))
    replay = OperationalReports(store, jobs).arrival("CASE", request)
    assert replay["replayed"] and replay["report"] == old["report"]
    assert "recovery_job" not in replay and "recovery_blocked" not in replay
    assert jobs.list("CASE")["jobs"] == []


@pytest.mark.parametrize("group_id", ["g1", "G" * 41])
def test_existing_noncanonical_group_id_is_reported_exactly_but_never_renamed_for_recovery(setup, group_id):
    _, store, jobs, ai, travel = setup
    def rename(world):
        world.groups[0].id = group_id
        world.incidents[0].group_ids[0] = group_id
    store.mutate("offline-noncanonical-group", {}, store.read().version, rename)
    before = store.read()
    request = arrival(store, group_ids=[group_id])
    result = OperationalReports(store, jobs).arrival("CASE", request)
    assert result["applied"] and result["report"]["group_ids"] == [group_id]
    assert result["recovery_blocked"]["code"] == "recovery_prerequisite_failed"
    assert "recovery_job" not in result
    after = store.read()
    assert after.groups[0].id == group_id and after.groups[0].origin == "LHR"
    assert after.groups[1:] == before.groups[1:] and after.incidents == before.incidents
    assert jobs.list("CASE")["jobs"] == [] and not ai.plans and not travel.calls
    if group_id == "g1":
        assert "normalization would change" in result["recovery_blocked"]["explanation"]
    else:
        assert "supported exact identifiers" in result["recovery_blocked"]["explanation"]


@pytest.mark.parametrize("options", [
    {"max_provider_calls": 2}, {"max_provider_calls": 27}, {"max_provider_calls": True},
    {"max_provider_calls": "3"}, {"group_ids": ["G2"]}, {"instructions": "Replace group scope"},
    {"modes": []}, {"modes": ["train"]}, {"objective": "anything"},
])
def test_invalid_recovery_input_never_mutates_report_or_dispatches(setup, options):
    _, store, jobs, _, travel = setup
    before = stable(store)
    with pytest.raises(ValidationError):
        arrival(store, recovery=options)
    assert stable(store) == before and jobs.list("CASE")["jobs"] == [] and not travel.calls


@pytest.mark.parametrize("problem", ["stale", "wrong_scope", "confirmed"])
def test_report_scope_and_confirmation_fail_before_any_job_changes(setup, problem):
    _, store, jobs, _, _ = setup
    req = arrival(store)
    if problem == "wrong_scope":
        req.group_ids = ["MISSING"]
    elif problem == "confirmed":
        store.mutate("offline-confirmed", {}, store.read().version, lambda world: setattr(world.groups[0], "status", "confirmed"))
        req.expected_version = store.read().version
    else:
        req.expected_version -= 1
    before = stable(store)
    with pytest.raises(DomainError):
        OperationalReports(store, jobs).arrival("CASE", req)
    assert stable(store) == before and jobs.list("CASE")["jobs"] == []
