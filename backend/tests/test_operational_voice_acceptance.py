"""HTTP -> interpreted report -> durable worker -> proposal, with offline adapters.

These tests prove orchestration, not speech recognition or live supplier coverage.
The global test socket guard prevents any paid/provider request.
"""
import copy
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.live_commands import LiveCommands
from app.main import create_app
from app.models import Itinerary, Leg, Offer, Reservation, Resource
from app.providers import ProviderError
from app.store import utcnow
from test_recovery_jobs import FakeAI, setup, run_next


class InterpretingAI(FakeAI):
    def __init__(self, result):
        super().__init__()
        self.interpretation = result
        self.interpretations = []

    async def interpret(self, text, world, context=""):
        self.interpretations.append((text, context))
        return copy.deepcopy(self.interpretation)


def add_existing_proposals(store):
    def apply(world):
        for group in world.groups:
            resource = Resource(id="BUS-" + group.id, name="Dated fixture coach " + group.id,
                kind="bus", origin=group.origin, destination=group.destination,
                departure=group.ready_at + 30, arrival=group.ready_at + 150,
                capacity=1, eligible_group_ids=[group.id], cost_per_person=40,
                source="offline-coach-fixture", valid_until=(utcnow() + timedelta(hours=1)).isoformat(),
                provenance={"supplier_schedule": "exact original fixture evidence"})
            world.resources.append(resource)
            leg = Leg(resource_id=resource.id, kind="bus", origin=resource.origin,
                destination=resource.destination, departure=resource.departure, arrival=resource.arrival, cost=40)
            offer = Offer(id="OLD-" + group.id, group_id=group.id, status="proposed",
                itinerary=Itinerary(legs=[leg], arrival=resource.arrival, total_cost=40),
                expires_at=(utcnow() + timedelta(minutes=30)).isoformat(), created_version=world.version)
            world.offers.append(offer)
            world.reservations.append(Reservation(id="RES-" + group.id, offer_id=offer.id,
                group_id=group.id, resource_id=resource.id, quantity=1, status="proposed", expires_at=offer.expires_at))
            group.status = "offered"
    store.mutate("fixture-existing-proposals", {}, store.read().version, apply)


def report_command(world, kind):
    group = next(item for item in world.groups if item.id == "G1")
    ready = datetime.fromisoformat(world.epoch) + timedelta(minutes=group.ready_at)
    if kind in ("arrival", "outage_and_arrival"):
        ready_reference = ready.strftime("%Y-%m-%d %H:%M UTC")
        statement = f"For case CASE, passengers G1 have arrived at CLJ and are ready at {ready_reference}."
        report = {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"],
            "all_linked_groups": False, "airport_code": "CLJ", "airport_reference": "CLJ",
            "ready_at": ready.isoformat(), "ready_at_reference": ready_reference,
            "report_reference": statement}
        if kind == "outage_and_arrival":
            outage = "Service BUS-G1 is unavailable."
            report = {"type": "resource_unavailability_and_arrival",
                "unavailability": {"type": "resource_unavailability", "resource_id": "BUS-G1",
                    "resource_reference": "BUS-G1", "report_reference": outage},
                "arrival": report}
            statement = outage + " " + statement
    else:
        statement = "Service BUS-G1 is unavailable."
        report = {"type": "resource_unavailability", "resource_id": "BUS-G1",
            "resource_reference": "BUS-G1", "report_reference": statement}
    text = statement + " Recover CASE for G1 using flights only, maximum 4 calls."
    interpreted = {"events": [], "strategies": [], "focus_id": None,
        "explanation": "Offline model fixture; no completed journey is asserted.",
        "operational_report": report,
        "recovery": {"action": "start", "incident_id": "CASE", "group_ids": ["G1"],
            "all_linked_groups": False, "objective": "balanced", "modes": ["flight"],
            "max_total_calls": 4, "call_limit_reference": "maximum 4 calls", "requires_input_changes": False}}
    return {"text": text, "expected_version": world.version,
        "idempotency_key": "operator-voice-acceptance-" + kind}, interpreted


def assert_other_scope_unchanged(before, after):
    assert next(item for item in after.groups if item.id == "G2") == next(item for item in before.groups if item.id == "G2")
    for field, identity in (("resources", "BUS-G2"), ("offers", "OLD-G2"), ("reservations", "RES-G2")):
        assert next(item for item in getattr(after, field) if item.id == identity) == next(item for item in getattr(before, field) if item.id == identity)
    assert after.incidents == before.incidents


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["arrival", "outage", "outage_and_arrival"])
async def test_report_over_http_then_worker_builds_new_scoped_proposal_and_replays_once(setup, kind):
    settings, store, _, _, travel = setup
    add_existing_proposals(store)
    before = store.read()
    payload, interpreted = report_command(before, kind)
    ai = InterpretingAI(interpreted)
    settings.operator_token = "offline-operator-token-" + "x" * 40
    app = create_app(settings)
    jobs = app.state.jobs
    app.state.service.ai = jobs.ai = ai
    jobs.travel = travel
    jobs.start_worker = lambda: None
    headers = {"Authorization": "Bearer " + settings.operator_token}
    with TestClient(app) as client:
        assert client.post("/api/commands", json=payload).status_code == 401
        assert not ai.interpretations
        response = client.post("/api/commands", json=payload, headers=headers)
        assert response.status_code == 200, response.json()
        receipt = response.json()
        assert receipt["applied"] is True, receipt
        assert receipt["command_interpretation_calls"] == 1
        assert receipt["recovery_total_call_limit"] == 4
        assert receipt["recovery_job"]["max_provider_calls"] == 3
        assert receipt["recovery_job"]["source_version"] == before.version + 1
        assert receipt["recovery_job"]["calls_dispatched"] == 0 and not travel.calls
        if kind == "outage_and_arrival":
            assert [report["type"] for report in receipt["reports"]] == ["resource_unavailability", "arrival_report"]
            assert receipt["report"] == receipt["reports"][1]
            assert receipt["reports"][0]["id"] != receipt["report"]["id"]
        reported = store.read()
        assert reported.version == before.version + 1
        assert next(item for item in reported.offers if item.id == "OLD-G1").status == "invalidated"
        assert_other_scope_unchanged(before, reported)
        result = await run_next(jobs)
        assert result["status"] == "completed", result
        assert result["calls_dispatched"] == 3
        assert result["result"]["recovered_passengers"] == 1
        assert result["result"]["exception_passengers"] == 0
        after = store.read()
        assert after.version == before.version + 2
        assert_other_scope_unchanged(before, after)
        proposal = next(item for item in after.offers if item.group_id == "G1" and item.status == "proposed")
        assert proposal.itinerary.legs[0].kind == "flight"
        expected_origin = "CLJ" if kind in ("arrival", "outage_and_arrival") else "CRA"
        assert travel.calls[0].origin_code == expected_origin
        assert proposal.itinerary.legs[0].origin == expected_origin
        assert next(item for item in after.groups if item.id == "G1").deadline == before.groups[0].deadline
        if kind in ("outage", "outage_and_arrival"):
            unavailable = next(item for item in after.resources if item.id == "BUS-G1")
            assert unavailable.available is False
            assert unavailable.provenance["supplier_schedule"] == "exact original fixture evidence"
        assert not any(item.status == "confirmed" for item in after.offers)
        assert not any(item.booking_status == "confirmed" for item in after.resources)
        # Replay the original pre-report version after both commits and rebuild
        # the command handler to prove durability, not an in-memory request cache.
        app.state.service.live_commands = LiveCommands(app.state.store, jobs)
        replay = client.post("/api/commands", json=payload, headers=headers).json()
        assert replay["replayed"] and replay["report"]["id"] == receipt["report"]["id"]
        assert replay["recovery_job"]["id"] == receipt["recovery_job"]["id"]
        if kind == "outage_and_arrival":
            assert replay["reports"] == receipt["reports"]
        assert len(ai.interpretations) == len(ai.plans) == len(ai.decisions) == len(travel.calls) == 1
        assert jobs.ledger.claim_next() is None
        assert store.read().version == after.version


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["outage", "outage_and_arrival"])
async def test_unknown_supplier_outcome_preserves_report_without_replaying_paid_work(setup, kind):
    settings, store, jobs, _, travel = setup
    add_existing_proposals(store)
    before = store.read()
    payload, interpreted = report_command(before, kind)
    ai = InterpretingAI(interpreted)
    jobs.ai = jobs.service.ai = ai
    jobs.service.live_commands = LiveCommands(store, jobs)
    from app.models import CommandRequest
    request = CommandRequest.model_validate(payload)
    async def timeout(_):
        raise ProviderError("Offline timeout after dispatch", provider="Nuitee", code="timeout")
    travel.handler = timeout
    receipt = await jobs.service.command(request)
    assert receipt["applied"] is True, receipt
    result = await run_next(jobs)
    assert result["status"] == "unknown_outcome" and result["calls_dispatched"] == 2
    assert result["steps"][-1]["status"] == "unknown"
    after = store.read()
    assert after.version == before.version + 1
    assert_other_scope_unchanged(before, after)
    assert next(item for item in after.resources if item.id == "BUS-G1").available is False
    if kind == "outage_and_arrival":
        assert after.groups[0].origin == "CLJ"
        assert travel.calls[0].origin_code == "CLJ"
        assert receipt["report"] == receipt["reports"][1]
    assert not any(item.status == "proposed" and item.group_id == "G1" for item in after.offers)
    jobs.service.live_commands = LiveCommands(store, jobs)
    replay = await jobs.service.command(request)
    assert replay["replayed"] and replay["report"]["id"] == receipt["report"]["id"]
    if kind == "outage_and_arrival":
        assert replay["reports"] == receipt["reports"]
    assert jobs.ledger.claim_next() is None
    assert len(ai.interpretations) == len(ai.plans) == len(travel.calls) == 1
    assert not ai.decisions
