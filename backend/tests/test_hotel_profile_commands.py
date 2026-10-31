"""Recorded speech/text routing is tested offline, not microphone acceptance."""
import json
from datetime import timedelta

import httpx
import pytest

from app.hotel_recovery_profile import PROFILE_KEY
from app.live_commands import LiveCommands, literal_call_limit
from app.providers import AIProvider, ProviderError
from app.store import DomainError
from test_live_commands import command_setup, request
from test_hotel_recovery_profile import hotel_command_setup


def hotel_text(suffix="with maximum eleven calls"):
    return "Recalculate RO321 on 2026-11-01 for G1 using saved hotel settings " + suffix


@pytest.mark.asyncio
async def test_saved_hotel_command_interprets_once_and_queues_exact_bounded_inputs(hotel_command_setup):
    _, store, service, jobs, ai, inputs = hotel_command_setup
    before = store.read().model_dump(mode="json")
    req = request(store, hotel_text())
    # Even a model proposing more calls cannot exceed the literal eleven.
    ai.result["recovery"].update(max_total_calls=26, call_limit_reference="maximum eleven calls")
    response = await service.command(req)
    assert response["applied"] is False and response["command_interpretation_calls"] == 1
    assert response["recovery_total_call_limit"] == 11
    job = response["recovery_job"]
    assert job["group_ids"] == ["G1"] and job["max_provider_calls"] == 10
    assert job["status"] == "queued" and job["calls_dispatched"] == 0
    assert "nu rezervări sau tarife salvate" in response["explanation"]
    private = jobs.ledger.get(job["id"], private=True)
    assert private["_request"]["hotel_recovery"] == inputs.model_dump(mode="json")
    assert store.read().model_dump(mode="json") == before
    service.live_commands = LiveCommands(store, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["recovery_job"]["id"] == job["id"]
    assert len(ai.calls) == 1 and len(jobs.list("RC-ONE")["jobs"]) == 1


@pytest.mark.parametrize("suffix,total,queued", [
    ("", 8, False), ("with maximum ten calls", 10, False),
    ("cu maximum unsprezece apeluri", 11, True),
    ("with maximum eleven calls", 11, True),
    ("with maximum twenty-seven calls", 27, True),
    ("cu maximum douăzeci și două de apeluri", 22, True),
    ("maximum eleven calls, but no more than ten calls", 10, False),
])
@pytest.mark.asyncio
async def test_hotel_caps_include_interpretation_and_never_reuse_or_raise_allowance(hotel_command_setup, suffix, total, queued):
    _, store, service, jobs, ai, _ = hotel_command_setup
    response = await service.command(request(store, hotel_text(suffix)))
    assert response["recovery_total_call_limit"] == total and response["command_interpretation_calls"] == 1
    assert len(ai.calls) == 1
    if queued:
        assert response["recovery_job"]["max_provider_calls"] == total - 1
    else:
        assert "minimum 11" in response["explanation"] and "nu am mărit" in response["explanation"]
        assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.parametrize("words,number", [
    ("twelve", 12), ("doisprezece", 12), ("douăsprezece", 12), ("thirteen", 13), ("treisprezece", 13),
    ("fourteen", 14), ("paisprezece", 14), ("fifteen", 15), ("cincisprezece", 15),
    ("sixteen", 16), ("șaisprezece", 16), ("seventeen", 17), ("șaptesprezece", 17),
    ("eighteen", 18), ("optsprezece", 18), ("nineteen", 19), ("nouăsprezece", 19),
    ("twenty", 20), ("douăzeci", 20), ("twenty one", 21), ("douăzeci și unu", 21),
    ("twenty-two", 22), ("twenty three", 23), ("twenty four", 24), ("twenty five", 25),
    ("twenty six", 26), ("douăzeci și șapte", 27),
])
def test_spoken_bounded_call_numbers(words, number):
    assert literal_call_limit(f"maximum {words} calls") == number
    assert literal_call_limit(f"{words} calls in total") == number
    assert literal_call_limit(f"{words} passengers") is None


@pytest.mark.parametrize("change", ["missing", "malformed", "party", "route", "case", "history_only", "wrong_group", "multiple_groups"])
@pytest.mark.asyncio
async def test_false_or_missing_saved_scope_never_queues(hotel_command_setup, change):
    _, store, service, jobs, ai, _ = hotel_command_setup
    text = hotel_text()
    if change in {"missing", "malformed", "party", "route", "case"}:
        def alter(world):
            group = world.groups[0]
            if change == "missing": group.provenance.pop(PROFILE_KEY)
            elif change == "malformed": group.provenance[PROFILE_KEY] = {"schema_version": 10}
            elif change == "party": group.members = ["New party member"]
            elif change == "route": group.destination = "CRA"
            elif change == "case": world.incidents[0].date_utc = "2026-11-02"
        store.mutate("changed-profile-scope", {}, store.read().version, alter)
    elif change == "history_only": text = "Do that again with maximum eleven calls"
    elif change == "wrong_group": ai.result["recovery"].update(group_ids=["G2"])
    elif change == "multiple_groups":
        ai.result["recovery"].update(group_ids=[], all_linked_groups=True)
        text = "Recover RO321 with saved hotel settings and maximum eleven calls"
    response = await service.command(request(store, text, context=hotel_text()))
    assert response["applied"] is False and response["command_interpretation_calls"] == 1
    assert not jobs.list("RC-ONE")["jobs"] and len(ai.calls) == 1


@pytest.mark.parametrize("change", ["expired_pickup", "later_ready", "earlier_deadline", "accessible"])
@pytest.mark.asyncio
async def test_current_constraints_revalidate_saved_inputs_before_any_job(hotel_command_setup, monkeypatch, change):
    _, store, service, jobs, ai, inputs = hotel_command_setup
    if change == "expired_pickup":
        monkeypatch.setattr("app.hotel_recovery.utcnow", lambda: inputs.pickup_at + timedelta(seconds=1))
    else:
        def alter(world):
            group = world.groups[0]
            from datetime import datetime
            if change == "later_ready": group.ready_at = int((inputs.pickup_at - datetime.fromisoformat(world.epoch)).total_seconds() / 60) + 1
            elif change == "earlier_deadline": group.deadline = group.ready_at + 1
            else: group.accessible = True
        store.mutate("current-constraint-change", {}, store.read().version, alter)
    response = await service.command(request(store, hotel_text()))
    assert response["applied"] is False and "condițiile actuale" in response["explanation"]
    assert not jobs.list("RC-ONE")["jobs"] and len(ai.calls) == 1


@pytest.mark.asyncio
async def test_current_budget_is_preserved_not_restored_from_profile(hotel_command_setup):
    _, store, service, jobs, _, _ = hotel_command_setup
    def budget(world):
        world.groups[0].budget = 25
        world.budget = 30
    store.mutate("revised-budget", {}, store.read().version, budget)
    response = await service.command(request(store, hotel_text()))
    assert response["recovery_job"]["status"] == "queued"
    assert store.read().groups[0].budget == 25 and store.read().budget == 30
    assert "budget" not in jobs.ledger.get(response["recovery_job"]["id"], private=True)["_request"]["hotel_recovery"]


@pytest.mark.parametrize("extra", [
    "check in on 2026-11-04", "caută alternative pentru2noiembrie2026", "leave tomorrow",
    "pickup at 15:00", "preluare la ora cinci", "minimum rest eight hours", "check out later",
    "move the pickup", "check in on November 4", "checkout on Sunday",
])
@pytest.mark.asyncio
async def test_unreported_extra_hotel_dates_or_times_cannot_be_silently_ignored(hotel_command_setup, extra):
    _, store, service, jobs, ai, _ = hotel_command_setup
    assert ai.result["recovery"]["requires_input_changes"] is False  # model omission
    response = await service.command(request(store, hotel_text() + ", " + extra))
    assert response["applied"] is False and "date sau ore suplimentare" in response["explanation"]
    assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.parametrize("reference", ["RO321 din1noiembrie2026", "RO321 on November 1, 2026", "flight dated 1 November 2026 RO321"])
@pytest.mark.asyncio
async def test_case_date_is_not_misclassified_as_a_hotel_input_edit(hotel_command_setup, reference):
    _, store, service, _, _, _ = hotel_command_setup
    response = await service.command(request(store, f"Recalculate {reference} for G1 using saved hotel settings, maximum eleven calls"))
    assert response["recovery_job"]["status"] == "queued"


@pytest.mark.asyncio
async def test_model_dropping_hotel_mode_cannot_silently_search_only_flights(hotel_command_setup):
    _, store, service, jobs, ai, _ = hotel_command_setup
    ai.result["recovery"]["modes"] = ["flight"]
    response = await service.command(request(store, hotel_text()))
    assert "nu am extins sau redus" in response["explanation"].lower()
    assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_explicit_flight_only_path_is_unchanged_even_with_saved_profile(hotel_command_setup):
    _, store, service, jobs, ai, _ = hotel_command_setup
    ai.result["recovery"]["modes"] = ["flight"]
    response = await service.command(request(store, "Find only flights for RO321 and G1, maximum four calls"))
    job = response["recovery_job"]
    assert job["max_provider_calls"] == 3
    assert "hotel_recovery" not in jobs.ledger.get(job["id"], private=True)["_request"]


@pytest.mark.asyncio
async def test_unknown_hotel_interpretation_cannot_be_retried_or_reported_as_queued(hotel_command_setup):
    _, store, service, jobs, ai, _ = hotel_command_setup
    async def fail(*args):
        ai.calls.append(args)
        raise ProviderError("Offline timeout", provider="OpenAI Responses", code="timeout")
    ai.interpret = fail
    req = request(store, hotel_text())
    with pytest.raises(DomainError) as first:
        await service.command(req)
    assert first.value.applied is None and first.value.details["interpretation_outcome"] == "unknown"
    with pytest.raises(DomainError) as replay:
        await service.command(req)
    assert replay.value.applied is None and replay.value.details["automatic_retry"] is False
    assert len(ai.calls) == 1 and not jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_unknown_receipt_after_dispatch_recovers_original_job_without_more_interpretation(hotel_command_setup, monkeypatch):
    _, store, service, jobs, ai, _ = hotel_command_setup
    original = jobs.start
    def lost_receipt(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Offline lost receipt after durable commit")
    monkeypatch.setattr(jobs, "start", lost_receipt)
    req = request(store, hotel_text())
    with pytest.raises(RuntimeError, match="lost receipt"):
        await service.command(req)
    result = await service.command(req)
    assert result["replayed"] and result["recovery_job"]["max_provider_calls"] == 10
    assert len(ai.calls) == 1 and len(jobs.list("RC-ONE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_real_adapter_keeps_strict_schema_model_and_profile_summary_offline(hotel_command_setup):
    settings, store, _, _, ai, _ = hotel_command_setup
    settings.openai_api_key = "offline-placeholder"
    async def handle(req):
        body = json.loads(req.content)
        assert body["model"] == "gpt-6.1-sol" and body["service_tier"] == "ultrafast"
        assert body["text"]["format"]["strict"] is True
        schema = body["text"]["format"]["schema"]["$defs"]["LiveRecoveryIntent"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert "11 total" in body["instructions"] and "never silently downgrade" in body["instructions"]
        assert "hotel_recovery_profile" in json.dumps(body["input"])
        assert "PRIVATE PICKUP POINT" not in json.dumps(body)
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(ai.result)}]}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    provider = AIProvider(settings, client=client)
    try:
        result = await provider.interpret(hotel_text(), store.read().model_dump(mode="json"))
        assert result["recovery"]["modes"] == ["flight", "hotel", "bus"]
    finally:
        await client.aclose()
