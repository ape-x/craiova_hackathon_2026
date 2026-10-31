"""Offline HTTP/worker acceptance for reports arriving during recovery sourcing.

The real command transaction, SQLite ledger and recovery worker are exercised.
Only interpretation/decision and supplier adapters are fake; the suite's socket
guard forbids live network calls, bookings and paid model consumption.
"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.live_commands import LiveCommands
from app.main import create_app
from app.providers import ProviderError
from test_operational_voice_acceptance import (
    InterpretingAI,
    add_existing_proposals,
    assert_other_scope_unchanged,
    report_command,
)
from test_recovery_jobs import FakeTravel, request, run_next, setup


@contextmanager
def application(fixture, kind="arrival"):
    settings, store, _, _, travel = fixture
    add_existing_proposals(store)
    before = store.read()
    payload, interpreted = report_command(before, kind)
    ai = InterpretingAI(interpreted)
    settings.operator_token = "offline-handoff-operator-" + "x" * 40
    app = create_app(settings)
    jobs = app.state.jobs
    app.state.service.ai = jobs.ai = ai
    jobs.travel = travel
    # Deterministic scheduling of the real durable worker; no app server starts.
    jobs.start_worker = lambda: None
    headers = {"Authorization": "Bearer " + settings.operator_token}
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, jobs=jobs, store=store, ai=ai, travel=travel,
            client=client, headers=headers, before=before, payload=payload)


def start_original(context, **changes):
    body = request(context.store, idempotency_key="handoff-original-attempt",
        max_provider_calls=6, **changes).model_dump(mode="json")
    response = context.client.post("/api/incidents/CASE/recovery-jobs",
        json=body, headers=context.headers)
    assert response.status_code == 202, response.text
    return response.json()["job"]


@asynccontextmanager
async def awaiting_supplier(context, outcome="success"):
    entered, release = asyncio.Event(), asyncio.Event()
    fixture_source = FakeTravel()

    async def delayed(query):
        if len(context.travel.calls) == 1:
            entered.set()
            await release.wait()
            if outcome != "success":
                raise ProviderError("Offline supplier response after report",
                    provider="Nuitee", code=outcome)
        return await fixture_source.search(query)

    context.travel.handler = delayed
    original = start_original(context)
    task = asyncio.create_task(run_next(context.jobs))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert context.jobs.get(original["id"])["job"]["calls_dispatched"] == 2
        yield original, task, release
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(task), 3)


def submit_report(context, payload=None):
    response = context.client.post("/api/commands", json=payload or context.payload,
        headers=context.headers)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["applied"] is True
    assert receipt["command_interpretation_calls"] == 1
    assert receipt["recovery_total_call_limit"] == 4
    assert receipt["recovery_job"]["max_provider_calls"] == 3
    return receipt


def assert_replay_without_work(context, receipt, payload=None):
    version = context.store.read().version
    counts = (len(context.ai.interpretations), len(context.ai.plans),
        len(context.ai.decisions), len(context.travel.calls))
    # Receipt replay must survive replacing the command handler, not rely on RAM.
    context.app.state.service.live_commands = LiveCommands(context.store, context.jobs)
    replay = submit_report(context, payload)
    assert replay["replayed"] is True
    assert replay["report"] == receipt["report"]
    assert replay["recovery_job"]["id"] == receipt["recovery_job"]["id"]
    assert context.store.read().version == version
    assert (len(context.ai.interpretations), len(context.ai.plans),
        len(context.ai.decisions), len(context.travel.calls)) == counts


def assert_world_unchanged(context, expected):
    # Live reads advance the display clock even when no transaction occurred.
    assert context.store.read().model_dump(exclude={"sim_time"}) == expected.model_dump(exclude={"sim_time"})


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "outcome", "old_status"), [
    ("arrival", "success", "cancelled"),
    ("outage", "success", "cancelled"),
    ("arrival", "http_400", "failed"),
])
async def test_live_report_waits_for_known_drain_then_sources_only_updated_scope(setup, kind, outcome, old_status):
    with application(setup, kind) as context:
        async with awaiting_supplier(context, outcome) as (original, old_task, release):
            # A normal start still cannot silently take over an active group.
            ordinary = request(context.store, idempotency_key="ordinary-overlap-attempt").model_dump(mode="json")
            conflict = context.client.post("/api/incidents/CASE/recovery-jobs",
                json=ordinary, headers=context.headers)
            assert conflict.status_code == 409

            receipt = submit_report(context)
            successor = receipt["recovery_job"]
            assert successor["supersedes_job_ids"] == [original["id"]]
            assert successor["status"] == "queued"
            assert successor["stage"] == "waiting_for_previous"
            assert successor["calls_dispatched"] == 0
            assert successor["source_version"] == context.before.version + 1
            prior = context.jobs.get(original["id"])["job"]
            assert prior["status"] == "running"
            assert prior["cancel_requested"] is True
            assert prior["superseded_by_job_id"] == successor["id"]
            assert prior["max_provider_calls"] == 6
            assert context.jobs.ledger.claim_next() is None
            assert len(context.travel.calls) == 1
            assert len(context.ai.plans) == 1
            assert not context.ai.decisions

            reported = context.store.read()
            assert reported.version == context.before.version + 1
            assert next(offer for offer in reported.offers if offer.id == "OLD-G1").status == "invalidated"
            assert_other_scope_unchanged(context.before, reported)
            assert_replay_without_work(context, receipt)

            release.set()
            drained = await old_task
            assert drained["status"] == old_status
            assert drained["calls_dispatched"] == 2
            assert drained["steps"][-1]["status"] == ("succeeded" if outcome == "success" else "failed")
            assert drained["result"] is None
            assert_world_unchanged(context, reported)
            assert not context.ai.decisions

            completed = await run_next(context.jobs)
            assert completed["id"] == successor["id"]
            assert completed["status"] == "completed"
            assert completed["calls_dispatched"] == completed["max_provider_calls"] == 3
            assert completed["result"]["recovered_passengers"] == 1
            assert context.travel.calls[0].origin_code == "CRA"
            assert context.travel.calls[1].origin_code == ("CLJ" if kind == "arrival" else "CRA")
            assert len(context.ai.plans) == len(context.travel.calls) == 2
            assert len(context.ai.decisions) == len(context.ai.interpretations) == 1
            after = context.store.read()
            assert after.version == context.before.version + 2
            assert_other_scope_unchanged(context.before, after)
            assert_replay_without_work(context, receipt)
            assert context.jobs.ledger.claim_next() is None


@pytest.mark.asyncio
async def test_unknown_drain_blocks_successor_without_more_calls_or_proposals(setup):
    with application(setup) as context:
        async with awaiting_supplier(context, "timeout") as (original, old_task, release):
            receipt = submit_report(context)
            reported = context.store.read()
            release.set()
            drained = await old_task
            assert drained["status"] == "unknown_outcome"
            assert drained["steps"][-1]["status"] == "unknown"
            assert drained["calls_dispatched"] == 2
            assert context.jobs.ledger.claim_next() is None
            successor = context.jobs.get(receipt["recovery_job"]["id"])["job"]
            assert successor["status"] == "needs_review"
            assert successor["calls_dispatched"] == 0
            assert successor["steps"] == []
            assert successor["result"] is None
            assert_world_unchanged(context, reported)
            assert_other_scope_unchanged(context.before, reported)
            assert len(context.ai.plans) == len(context.travel.calls) == len(context.ai.interpretations) == 1
            assert not context.ai.decisions
            assert_replay_without_work(context, receipt)
            assert context.jobs.get(original["id"])["job"]["status"] == "unknown_outcome"
            assert context.jobs.get(successor["id"])["job"]["status"] == "needs_review"


def test_queued_predecessor_cancelled_atomically_without_touching_unrelated_job(setup):
    with application(setup) as context:
        original = start_original(context)
        unrelated_request = request(context.store, group_ids=["G2"],
            idempotency_key="handoff-unrelated-attempt")
        unrelated = context.jobs.start("CASE", unrelated_request)["job"]
        denied = context.client.post("/api/commands", json=context.payload)
        assert denied.status_code == 401
        assert context.jobs.get(original["id"])["job"] == original
        assert_world_unchanged(context, context.before)
        assert not context.ai.interpretations

        receipt = submit_report(context)
        successor = receipt["recovery_job"]
        prior = context.jobs.get(original["id"])["job"]
        assert prior["status"] == "cancelled"
        assert prior["cancel_requested"] is True
        assert prior["superseded_by_job_id"] == successor["id"]
        assert prior["calls_dispatched"] == 0
        assert prior["steps"] == []
        assert successor["supersedes_job_ids"] == [original["id"]]
        assert successor["status"] == "queued"
        assert successor["calls_dispatched"] == 0
        assert context.jobs.get(unrelated["id"])["job"] == unrelated
        assert_other_scope_unchanged(context.before, context.store.read())
        assert not context.travel.calls
        assert not context.ai.plans
        assert_replay_without_work(context, receipt)


@pytest.mark.asyncio
async def test_rapid_second_report_preserves_unknown_ancestor_through_cancelled_queue(setup):
    with application(setup) as context:
        async with awaiting_supplier(context, "timeout") as (original, old_task, release):
            first = submit_report(context)
            middle_id = first["recovery_job"]["id"]
            release.set()
            assert (await old_task)["status"] == "unknown_outcome"
            # Another report arrives before the pending successor is claimed.
            # Cancelling that queued intermediary cannot erase the unknown call.
            second_payload, interpreted = report_command(context.store.read(), "arrival")
            second_payload["idempotency_key"] = "handoff-second-arrival-report"
            context.ai.interpretation = interpreted
            second = submit_report(context, second_payload)
            latest_id = second["recovery_job"]["id"]
            assert second["recovery_job"]["supersedes_job_ids"] == [middle_id]
            middle = context.jobs.get(middle_id)["job"]
            assert middle["status"] == "cancelled"
            assert middle["superseded_by_job_id"] == latest_id
            reported = context.store.read()
            assert context.jobs.ledger.claim_next() is None
            latest = context.jobs.get(latest_id)["job"]
            assert latest["status"] == "needs_review"
            assert latest["calls_dispatched"] == 0
            assert latest["steps"] == []
            assert context.jobs.get(original["id"])["job"]["status"] == "unknown_outcome"
            assert_world_unchanged(context, reported)
            assert reported.version == context.before.version + 2
            assert_other_scope_unchanged(context.before, reported)
            assert len(context.ai.plans) == len(context.travel.calls) == 1
            assert len(context.ai.interpretations) == 2
            assert not context.ai.decisions
            assert_replay_without_work(context, second, second_payload)


@pytest.mark.asyncio
async def test_successor_revalidates_passenger_scope_after_known_predecessor_drains(setup):
    with application(setup) as context:
        async with awaiting_supplier(context) as (_, old_task, release):
            receipt = submit_report(context)

            def change_selected_budget(world):
                next(group for group in world.groups if group.id == "G1").budget = 50

            context.store.mutate("offline-handoff-new-budget", {}, context.store.read().version,
                change_selected_budget)
            changed = context.store.read()
            release.set()
            assert (await old_task)["status"] == "cancelled"
            successor = await run_next(context.jobs)
            assert successor["id"] == receipt["recovery_job"]["id"]
            assert successor["status"] == "needs_review"
            assert successor["calls_dispatched"] == 0
            assert successor["steps"] == []
            assert successor["result"] is None
            assert len(context.ai.plans) == len(context.travel.calls) == len(context.ai.interpretations) == 1
            assert not context.ai.decisions
            assert_world_unchanged(context, changed)
            assert_other_scope_unchanged(context.before, changed)
            assert_replay_without_work(context, receipt)
