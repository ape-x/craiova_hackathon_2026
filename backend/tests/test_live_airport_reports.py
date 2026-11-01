"""Real receipt/queue routing with mocked interpretation and disabled sockets."""
import pytest

from app.live_commands import LiveCommands
from app.providers.ai import LiveInterpretation, compact_world
from app.recovery_jobs import scope_fingerprint
from app.store import DomainError
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup, intent, request
from test_live_operational_commands import assert_no_report, outage, set_report


def report(claim="OTP is closed", *, operations=None, action="restrict", **changes):
    return {"type": "airport_restriction", "incident_id": "RC-ONE", "airport_code": "OTP",
        "airport_reference": "OTP", "operations": operations or ["arrivals", "departures"],
        "action": action, "report_reference": claim, **changes}


@pytest.mark.parametrize("claim,operations,action", [
    ("OTP is closed", ["arrivals", "departures"], "restrict"),
    ("Assume OTP closed", ["arrivals", "departures"], "restrict"),
    ("OTP este închis", ["arrivals", "departures"], "restrict"),
    ("OTP is closed for arrivals", ["arrivals"], "restrict"),
    ("OTP arrivals and departures are prohibited", ["arrivals", "departures"], "restrict"),
    ("OTP is closed for departures", ["departures"], "restrict"),
    ("RO321 cannot land at OTP", ["arrivals"], "restrict"),
    ("RO321 can't land at OTP", ["arrivals"], "restrict"),
    ("RO321 nu poate ateriza la OTP", ["arrivals"], "restrict"),
    ("OTP nu mai permite aterizarea", ["arrivals"], "restrict"),
    ("OTP nu permite decolarea", ["departures"], "restrict"),
    ("OTP no longer allows arrivals", ["arrivals"], "restrict"),
    ("No departures at OTP", ["departures"], "restrict"),
    ("OTP arrivals are prohibited", ["arrivals"], "restrict"),
    ("OTP departures are suspended", ["departures"], "restrict"),
    ("Clear arrival restrictions at OTP", ["arrivals"], "clear"),
    ("Elimină restricția pentru aterizări la OTP", ["arrivals"], "clear"),
    ("OTP is reopened", ["arrivals", "departures"], "clear"),
    ("OTP este redeschis", ["arrivals", "departures"], "clear"),
    ("OTP departures are allowed", ["departures"], "clear"),
])
@pytest.mark.asyncio
async def test_literal_direction_and_action_record_without_moving_anyone(command_setup, claim, operations, action):
    _, store, service, jobs, ai = command_setup
    set_report(ai, report(claim, operations=operations, action=action))
    before = store.read()
    req = request(store, "For RO321 on 2026-11-01: " + claim)
    result = await service.command(req)
    assert result["applied"] is True, result["explanation"]
    after = store.read()
    assert result["report"]["operations"] == sorted(operations)
    assert result["report"]["action"] == action
    assert result["report"]["recovery_group_ids"] == []
    assert result["command_interpretation_calls"] == 1
    assert after.groups == before.groups and after.resources == before.resources and after.airports == before.airports
    assert after.incidents[0].flight_ref == before.incidents[0].flight_ref
    assert jobs.list("RC-ONE")["jobs"] == []
    service.live_commands = LiveCommands(store, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["report"] == result["report"] and len(ai.calls) == 1


@pytest.mark.parametrize("text,changes", [
    ("OTP is closed for arrivals", {"operations": ["arrivals", "departures"]}),
    ("OTP is closed", {"operations": ["arrivals"]}),
    ("OTP is closed", {"action": "clear"}),
    ("OTP is not closed", {}),
    ("If OTP is closed", {"report_reference": "OTP is closed"}),
    ("Do not assume OTP is closed", {"report_reference": "OTP is closed"}),
    ("Is OTP closed?", {"report_reference": "OTP closed"}),
    ("OTP was closed yesterday", {}),
    ("OTP is closed tomorrow", {"report_reference": "OTP is closed"}),
    ("OTP is closed only if visibility worsens", {"report_reference": "OTP is closed"}),
    ("OTP is closed when visibility drops", {}),
    ("OTP is closed once repairs begin", {}),
    ("OTP is closed while repairs run", {"report_reference": "OTP is closed"}),
    ("OTP is closed during the night", {}),
    ("OTP is closed overnight", {}),
    ("OTP este închis când scade vizibilitatea", {}),
    ("OTP este închis odată ce încep reparațiile", {}),
    ("OTP este închis în timpul nopții", {}),
    ("OTP is closed for G1 only", {"report_reference": "OTP is closed"}),
    ("For G1: OTP is closed", {"report_reference": "OTP is closed"}),
    ("OTP is closed on 2026-11-04", {}),
    ("OTP is closed for two hours", {}),
    ("OTP is closed today", {}),
    ("OTP is closed except for arrivals", {"operations": ["arrivals"]}),
    ("OTP is closed in thirty minutes", {}),
    ("OTP arrivals are restricted but departures are allowed", {}),
    ("OTP arrivals are restricted but departures are unaffected", {}),
    ("OTP closed to arrivals but open to departures", {}),
    ("OTP is closed and LHR is closed", {}),
    ("OTP is closed and LHR is closed", {"report_reference": "OTP is closed"}),
    ("OTP is closed and open for departures", {"report_reference": "OTP is closed"}),
    ("OTP is closed. G1 is ready at SBZ in 30 minutes", {"report_reference": "OTP is closed"}),
    ("OTP is closed. Q1 is broken", {"report_reference": "OTP is closed"}),
    ("OTP is closed. Divert RO321 to CRA", {"report_reference": "OTP is closed"}),
    ("OTP is closed", {"airport_reference": "LHR", "airport_code": "LHR"}),
    ("OTP is closed", {"incident_id": "RC-OTHER"}),
    ("OTP is closed", {"operations": ["arrivals", "arrivals"]}),
])
@pytest.mark.asyncio
async def test_cannot_widen_omit_negation_change_time_scope_or_drop_another_fact(command_setup, text, changes):
    _, store, service, jobs, ai = command_setup
    set_report(ai, report(text, **changes), intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    result = await service.command(request(store, f"For RO321: {text}. Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is False, result
    assert result["command_interpretation_calls"] == 1
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("claim", ["OTP is reopened", "OTP arrivals are prohibited", "OTP nu mai permite aterizarea", "OTP arrivals and departures are prohibited"])
@pytest.mark.asyncio
async def test_omitted_restriction_never_becomes_recovery_on_old_facts(command_setup, claim):
    _, store, service, jobs, ai = command_setup
    set_report(ai, None, intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    result = await service.command(request(store, f"{claim}. Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is False and "recovery_job" not in result
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_service_word_cannot_hide_coordinated_airport_restriction(command_setup):
    _, store, service, jobs, ai = command_setup
    set_report(ai, outage(reference="Q1 is broken"), intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    result = await service.command(request(store,
        "The bus Q1 is broken and OTP departures are prohibited. Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_report_queues_exact_groups_on_updated_scope_one_interpretation_and_total_cap(command_setup):
    _, store, service, jobs, ai = command_setup
    set_report(ai, report("OTP is closed for arrivals", operations=["arrivals"]), intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    req = request(store, "For RO321: OTP is closed for arrivals. Recover RO321 for G1 with maximum 4 calls")
    result = await service.command(req)
    assert result["applied"] and result["recovery_job"]["max_provider_calls"] == 3
    assert result["recovery_job"]["group_ids"] == result["report"]["recovery_group_ids"] == ["G1"]
    assert result["recovery_job"]["calls_dispatched"] == 0
    assert result["recovery_total_call_limit"] == 4 and result["command_interpretation_calls"] == 1
    assert result["recovery_job"]["source_version"] == before.version + 1
    private = jobs.ledger.get(result["recovery_job"]["id"], private=True)
    assert private["_scope_hash"] == scope_fingerprint(store.read(), "RC-ONE", ["G1"])
    assert private["_request"]["instructions"] == req.text
    replay = await service.command(req)
    assert replay["replayed"] and replay["recovery_job"] == result["recovery_job"] and len(ai.calls) == 1


@pytest.mark.asyncio
async def test_insufficient_allowance_preserves_report_but_never_increases_limit(command_setup):
    _, store, service, jobs, ai = command_setup
    set_report(ai, report(), intent(group_ids=["G1"], all_linked_groups=False))
    result = await service.command(request(store, "For RO321: OTP is closed. Recover RO321 for G1 with maximum 3 calls"))
    assert result["applied"] and result["recovery_blocked"]["code"] == "insufficient_call_allowance"
    assert result["recovery_total_call_limit"] == 3
    assert result["report"]["recovery_group_ids"] == ["G1"]
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.parametrize("operations,queued", [(["arrivals"], True), (["departures"], False)])
@pytest.mark.asyncio
async def test_explicit_saved_hotel_scope_is_revalidated_after_directional_report(hotel_command_setup, operations, queued):
    _, store, service, jobs, ai, inputs = hotel_command_setup
    claim = f"LHR is closed for {operations[0]}"
    set_report(ai, report(claim, operations=operations, airport_code="LHR", airport_reference="LHR"),
        intent(group_ids=["G1"], all_linked_groups=False, modes=["flight", "hotel", "bus"]))
    result = await service.command(request(store,
        f"For RO321: {claim}. Recover RO321 for G1 using saved hotel, both transfers and flight with maximum 11 calls"))
    assert result["applied"], result
    if queued:
        job = result["recovery_job"]
        assert job["max_provider_calls"] == 10 and set(job["modes"]) == {"flight", "hotel", "bus"}
        assert jobs.ledger.get(job["id"], private=True)["_request"]["hotel_recovery"] == inputs.model_dump(mode="json")
    else:
        assert result["recovery_blocked"]["code"] == "hotel_setup_required"
        assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.parametrize("known", [True, False])
@pytest.mark.asyncio
async def test_queue_failure_uses_report_transaction_and_savepoint(command_setup, monkeypatch, known):
    _, store, service, jobs, ai = command_setup
    set_report(ai, report(), intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    real_start = jobs.start_in_transaction
    def fail(conn, *args, **kwargs):
        real_start(conn, *args, **kwargs)
        if known:
            raise DomainError(409, "Queue unavailable", {"code": "recovery_queue_full"})
        raise RuntimeError("Unexpected after partial insertion")
    monkeypatch.setattr(jobs, "start_in_transaction", fail)
    req = request(store, "For RO321: OTP is closed. Recover RO321 for G1 with maximum 4 calls")
    if known:
        result = await service.command(req)
        assert result["applied"] and result["recovery_blocked"]["code"] == "recovery_queue_unavailable"
        assert store.read().version == before.version + 1
    else:
        with pytest.raises(RuntimeError, match="partial insertion"):
            await service.command(req)
        assert_no_report(store, before, jobs)
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_strict_interpretation_and_compact_context_include_current_directions(command_setup):
    _, store, service, _, ai = command_setup
    set_report(ai, report())
    await service.command(request(store, "For RO321: OTP is closed"))
    schema = LiveInterpretation.model_json_schema()
    shape = schema["$defs"]["LiveAirportRestrictionReport"]
    assert set(shape["required"]) == set(shape["properties"])
    assert shape["additionalProperties"] is False
    compact = compact_world(store.read().model_dump(mode="json"))
    assert {item["operation"] for item in compact["incidents"][0]["airport_restrictions"]} == {"arrivals", "departures"}
    assert "airport_restriction_reports" not in compact["incidents"][0]
