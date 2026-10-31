"""Real routing/SQLite receipts, mocked interpretation, no network or voice session."""
import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.hotel_recovery_profile import PROFILE_KEY, HotelRecoveryProfileError, build_hotel_recovery_profile, read_hotel_recovery_profile
from app.live_commands import LiveCommands
from app.providers import AIProvider
from app.providers.ai import Interpretation, LiveInterpretation
from app.store import DomainError, utcnow
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup, intent, interpretation, request
from test_operational_reports import reports


@pytest.fixture
def operational_setup(reports, hotel_command_setup):
    return hotel_command_setup


def arrival(store, *, reference=None, **changes):
    stamp = (utcnow() + timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()
    claim = reference or f"Assume G1 at SBZ ready at {stamp}"
    return {"type": "arrival_report", "incident_id": "RC-ONE", "group_ids": ["G1"],
        "all_linked_groups": False, "airport_code": "SBZ", "airport_reference": "SBZ",
        "ready_at": stamp, "ready_at_reference": stamp, "report_reference": claim, **changes}


def outage(*, reference="Q1 is unavailable", **changes):
    return {"type": "resource_unavailability", "resource_id": "Q1",
        "resource_reference": "Q1", "report_reference": reference, **changes}


def set_report(ai, report, recovery=None):
    ai.result = interpretation(operational_report=report, recovery=recovery)


def assert_no_report(store, before, jobs):
    assert store.read().model_dump(exclude={"sim_time"}) == before.model_dump(exclude={"sim_time"})
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_arrival_report_only_preserves_unrelated_data_and_replays_original_receipt(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    set_report(ai, report)
    before = store.read()
    req = request(store, f"For RO321 on 2026-11-01: {report['report_reference']}")
    result = await service.command(req)
    after = store.read()
    assert result["applied"] and result["command_interpretation_calls"] == 1
    assert result["report"]["group_ids"] == ["G1"] and result["report"]["source"] == "operator_report"
    assert result["report"]["affected_offer_ids"] == ["O-G1"]
    assert after.groups[0].origin == "SBZ" and after.groups[1:] == before.groups[1:]
    assert after.resources == before.resources and after.offers[1:] == before.offers[1:]
    assert after.groups[0].provenance[PROFILE_KEY] == before.groups[0].provenance[PROFILE_KEY]
    with pytest.raises(HotelRecoveryProfileError):
        read_hotel_recovery_profile(after, after.groups[0])
    assert "recovery_job" not in result and "recovery_blocked" not in result and jobs.list("RC-ONE")["jobs"] == []
    service.live_commands = LiveCommands(store, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["report"] == result["report"] and len(ai.calls) == 1
    assert store.read().version == before.version + 1


@pytest.mark.asyncio
async def test_exact_service_outage_requires_no_case_and_preserves_source_facts(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage())
    before = store.read()
    result = await service.command(request(store, "Report Q1 is unavailable"))
    after = store.read()
    assert result["applied"] and "incident_id" not in result
    assert after.resources[0].available is False and after.resources[1:] == before.resources[1:]
    assert after.resources[0].model_dump(exclude={"available", "provenance"}) == before.resources[0].model_dump(exclude={"available", "provenance"})
    assert after.resources[0].provenance["supplier_response"] == before.resources[0].provenance["supplier_response"]
    assert result["report"]["affected_group_ids"] == ["G1", "G2"]
    assert after.offers[2] == before.offers[2] and jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_report_and_job_share_post_report_scope_atomic_receipt_and_total_cap(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(), intent(group_ids=["G1"], all_linked_groups=False, max_total_calls=20, call_limit_reference="maximum4calls"))
    before = store.read()
    req = request(store, "Report Q1 is unavailable. Recover RO321 on 2026-11-01 for G1 with maximum 4 calls")
    result = await service.command(req)
    job = result["recovery_job"]
    assert result["applied"] and job["status"] == "queued" and job["calls_dispatched"] == 0
    assert result["recovery_total_call_limit"] == 4 and job["max_provider_calls"] == 3
    assert job["source_version"] == before.version + 1 and job["group_ids"] == ["G1"]
    private = jobs.ledger.get(job["id"], private=True)
    from app.recovery_jobs import scope_fingerprint
    assert private["_scope_hash"] == scope_fingerprint(store.read(), "RC-ONE", ["G1"])
    service.live_commands = LiveCommands(store, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["recovery_job"] == job and replay["report"] == result["report"]
    assert len(ai.calls) == 1 and len(jobs.list("RC-ONE")["jobs"]) == 1


@pytest.mark.parametrize("kind", ["low_cap", "invalid_profile"])
@pytest.mark.asyncio
async def test_known_followup_prerequisite_records_report_without_search(operational_setup, kind):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    recovery = intent(group_ids=["G1"], all_linked_groups=False)
    suffix = " Recover RO321 for G1 with maximum 3 calls"
    expected = "insufficient_call_allowance"
    if kind == "invalid_profile":
        recovery["modes"] = ["flight", "hotel", "bus"]
        suffix = " Recover RO321 for G1 using saved hotel and both transfers and flight with maximum 11 calls"
        expected = "hotel_setup_required"
    set_report(ai, report, recovery)
    result = await service.command(request(store, f"For RO321: {report['report_reference']}." + suffix))
    assert result["applied"] and result["recovery_blocked"]["code"] == expected
    assert "recovery_job" not in result and result["command_interpretation_calls"] == 1
    assert store.read().groups[0].origin == "SBZ"
    recorded = jobs.list("RC-ONE")["jobs"]
    assert recorded == []
    assert all(job["calls_dispatched"] == 0 for job in recorded)


@pytest.mark.parametrize("claim", ["Q1 is not unavailable", "Q1 nu s-a stricat", "If Q1 breaks", "What if Q1 is unavailable?", "Is Q1 unavailable?"])
@pytest.mark.asyncio
async def test_negated_hypothetical_or_question_is_not_an_outage(operational_setup, claim):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(reference=claim))
    before = store.read()
    result = await service.command(request(store, claim))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_model_cannot_quote_away_a_preceding_condition(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage())
    before = store.read()
    result = await service.command(request(store, "If Q1 is unavailable, recover RO321 for G1"))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("claim", ["RO321 diverted to SBZ for G1", "RO321 cannot land; G1 flight ETA at SBZ is {stamp}", "G1 at SBZ is not ready at {stamp}"])
@pytest.mark.asyncio
async def test_diversion_eta_or_negated_readiness_does_not_relocate_passengers(operational_setup, claim):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    report["report_reference"] = claim.format(stamp=report["ready_at"])
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, "For RO321: " + report["report_reference"]))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("change,text_suffix", [
    ({"group_ids": ["G1", "G2"]}, ""), ({"all_linked_groups": True, "group_ids": []}, ""),
    ({"group_ids": ["OUT"]}, ""), ({"incident_id": "MADE-UP"}, ""),
    ({"airport_code": "LHR"}, ""), ({"airport_reference": "London", "airport_code": "LHR"}, " London"),
    ({"ready_at_reference": "14:00 UTC"}, " 14:00 UTC"),
])
@pytest.mark.asyncio
async def test_report_cannot_infer_scope_airport_or_missing_readiness_date(operational_setup, change, text_suffix):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    text = "For RO321: " + report["report_reference"] + text_suffix
    report.update(change)
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, text))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_ambiguous_service_name_needs_exact_id(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(reference="Supplier dated transfer is unavailable", resource_reference="Supplier dated transfer"))
    before = store.read()
    result = await service.command(request(store, "Supplier dated transfer is unavailable"))
    assert result["applied"] is False and "ID-ul exact" in result["explanation"]
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("groups,all_linked", [(["G2"], False), ([], True)])
@pytest.mark.asyncio
async def test_followup_never_changes_report_scope(operational_setup, groups, all_linked):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    set_report(ai, report, intent(group_ids=groups, all_linked_groups=all_linked))
    before = store.read()
    result = await service.command(request(store, "For RO321: " + report["report_reference"] + ". Recover RO321 for G1"))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_zero_cap_skips_even_interpretation_and_report(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    before = store.read()
    result = await service.command(request(store, "Q1 is unavailable with maximum zero calls"))
    assert result["command_interpretation_calls"] == 0 and ai.calls == []
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_unexpected_fault_rolls_back_report_job_receipt_and_reuses_interpretation(operational_setup, monkeypatch):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(), intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    original = jobs.start_in_transaction
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("offline crash after job insert")
    monkeypatch.setattr(jobs, "start_in_transaction", crash)
    req = request(store, "Q1 is unavailable. Recover RO321 for G1 with maximum 4 calls")
    with pytest.raises(RuntimeError, match="after job insert"):
        await service.command(req)
    assert_no_report(store, before, jobs)
    monkeypatch.setattr(jobs, "start_in_transaction", original)
    service.live_commands = LiveCommands(store, jobs)
    result = await service.command(req)
    assert result["applied"] and len(ai.calls) == 1
    assert len(store.read().resources[0].provenance["unavailability_reports"]) == 1
    assert len(jobs.list("RC-ONE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_version_race_and_confirmed_service_fail_before_mutation(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(reference="Q2 is unavailable", resource_id="Q2", resource_reference="Q2"))
    before = store.read()
    with pytest.raises(DomainError, match="confirmed bookings"):
        await service.command(request(store, "Q2 is unavailable"))
    assert_no_report(store, before, jobs)
    set_report(ai, outage())
    async def concurrent():
        store.mutate("other-operator", {}, store.read().version, lambda world: setattr(world, "budget", 700))
    ai.before_return = concurrent
    with pytest.raises(DomainError, match="World changed"):
        await service.command(request(store, "Q1 is unavailable", idempotency_key="different-command-key"))
    assert store.read().resources == before.resources


@pytest.mark.parametrize("date", ["2026-10-31", "30–31 November 2026", "30 și 1 noiembrie 2020 și 6"])
@pytest.mark.asyncio
async def test_actual_ambiguous_or_conflicting_case_date_is_not_normalized(operational_setup, date):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, f"For RO321 on {date}: {report['report_reference']}"))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_relative_readiness_retries_use_original_durable_anchor(operational_setup, monkeypatch):
    _, store, service, jobs, ai, _ = operational_setup
    stamp = utcnow().replace(second=37, microsecond=123)
    monkeypatch.setattr("app.live_commands.utcnow", lambda: stamp)
    report = arrival(store, reference="Assume G1 at SBZ ready in 30 minutes", ready_at=None, ready_at_reference="in 30 minutes")
    set_report(ai, report, intent(group_ids=["G1"], all_linked_groups=False))
    req = request(store, "For RO321: " + report["report_reference"] + ". Recover RO321 for G1 with maximum 4 calls")
    original = jobs.start_in_transaction
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("offline failure")
    monkeypatch.setattr(jobs, "start_in_transaction", crash)
    before = store.read()
    with pytest.raises(RuntimeError):
        await service.command(req)
    assert_no_report(store, before, jobs)
    monkeypatch.setattr("app.live_commands.utcnow", lambda: stamp + timedelta(minutes=20))
    monkeypatch.setattr(jobs, "start_in_transaction", original)
    service.live_commands = LiveCommands(store, jobs)
    result = await service.command(req)
    expected = stamp.replace(second=0, microsecond=0) + timedelta(minutes=31)
    assert datetime.fromisoformat(result["report"]["ready_at"]) == expected
    basis = result["report"]["readiness_basis"]
    assert basis["command_received_at"] == stamp.isoformat() and basis["minutes"] == 30
    assert basis["rounding"] == "ceil_minute" and basis["reference"] == "in 30 minutes"
    assert store.read().groups[0].provenance["arrival_reports"][0]["readiness_basis"] == basis
    assert len(ai.calls) == 1 and len(jobs.list("RC-ONE")["jobs"]) == 1
    replay = await service.command(req)
    assert replay["report"] == result["report"] and len(ai.calls) == 1


@pytest.mark.parametrize("phrase,expected", [("in 0 minutes", True), ("în treizeci de minute", True), ("in -1 minutes", False), ("in 10081 minutes", False), ("in 1.5 minutes", False)])
@pytest.mark.asyncio
async def test_relative_readiness_is_literal_and_bounded(operational_setup, phrase, expected):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store, reference=f"Assume G1 at SBZ ready {phrase}", ready_at=None, ready_at_reference=phrase)
    set_report(ai, report)
    result = await service.command(request(store, "For RO321: " + report["report_reference"]))
    assert result["applied"] is expected
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_conflicting_relative_absolute_readiness_not_silently_selected(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    report.update(ready_at=None, ready_at_reference="in 30 minutes",
        report_reference=f"Assume G1 at SBZ ready in 30 minutes at {report['ready_at']}")
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, "For RO321: " + report["report_reference"]))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_strict_live_schema_uses_nested_anyof_and_simulation_unchanged(operational_setup):
    settings, store, _, _, _, _ = operational_setup
    settings.openai_api_key = "offline-placeholder"
    report = outage()
    async def handle(req):
        body = json.loads(req.content)
        schema = body["text"]["format"]["schema"]
        assert "operational_report" in schema["required"]
        assert "anyOf" in schema["properties"]["operational_report"]
        assert '"oneOf"' not in json.dumps(schema) and '"discriminator"' not in json.dumps(schema)
        for name in ("LiveArrivalReport", "LiveResourceUnavailability"):
            model = schema["$defs"][name]
            assert set(model["properties"]) == set(model["required"]) and model["additionalProperties"] is False
        assert "gpt-6.1-sol" == body["model"] and body["service_tier"] == "ultrafast"
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(interpretation(recovery=None, operational_report=report))}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await AIProvider(settings, client=client).interpret("Q1 is unavailable", store.read().model_dump(mode="json"))
    assert result["operational_report"] == report
    assert "operational_report" not in Interpretation.model_json_schema()["properties"]
    assert "operational_report" in LiveInterpretation.model_json_schema()["required"]


@pytest.mark.asyncio
async def test_older_durable_interpretation_without_report_field_remains_replayable(command_setup):
    _, store, service, jobs, ai = command_setup
    ai.result.pop("operational_report")
    req = request(store)
    await service.live_commands.interpret(req, store.read(), ai)
    service.live_commands = LiveCommands(store, jobs)
    response = await service.command(req)
    assert response["recovery_job"]["status"] == "queued" and len(ai.calls) == 1


@pytest.mark.parametrize("followup", ["Do not search RO321 for G1", "Do not recover RO321 for G1", "If necessary, recover RO321 for G1", "Nu recupera RO321 pentru G1", "If Q2 fails then search RO321 for G1"])
@pytest.mark.asyncio
async def test_model_cannot_queue_negated_or_conditional_followup(operational_setup, followup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(), intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    result = await service.command(request(store, "Q1 is unavailable. " + followup))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("claim", [
    "Q1 is unavailable", "G1 arrived at SBZ", "G1 at SBZ ready in 30 minutes",
    "Grupurile G1 sunt pregătite în treizeci de minute",
    "Pasagerii G1 sunt pregătiți în treizeci de minute",
    "Grupul G1 este pregătit peste treizeci de minute",
    "Persoana din G1 este pregătită în treizeci de minute",
])
@pytest.mark.asyncio
async def test_dropped_report_cannot_dispatch_recovery_against_old_facts(operational_setup, claim):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, None, intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    result = await service.command(request(store, claim + ". Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is False and "raport operațional" in result["explanation"]
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("airport_reference", [None, "Le Havre", "LHR", "Heathrow"])
@pytest.mark.asyncio
async def test_recorded_wrong_airport_speech_cannot_recover_against_saved_lhr_facts(operational_setup, airport_reference):
    _, store, service, jobs, ai, inputs = operational_setup

    def matching_scope(world):
        case = world.incidents[0]
        case.requested_flight_number = "EK5"
        case.date_utc = "2026-10-31"
        case.group_ids = ["G1"]
        world.groups[1].incident_id = None
        group = world.groups[0]
        group.original_flight_id = "EK5"
        group.provenance[PROFILE_KEY] = build_hotel_recovery_profile(world, group, inputs)

    store.mutate("recorded-airport-fixture", {}, store.read().version, matching_scope)
    claim = "Presupune că toate grupurile cazului EK5 sunt la Le Havre și sunt pregătite în treizeci de minute"
    followup = "Recalculează pentru toate grupurile cazului EK5 itinerarul complet salvat, cu hotel, ambele transferuri și zborul. Maxim unsprezece apeluri în total"
    text = claim + ". " + followup
    recovery = intent(modes=["flight", "hotel", "bus"], max_total_calls=11,
        call_limit_reference="Maxim unsprezece apeluri în total")
    report = (arrival(store, reference=claim, group_ids=[], all_linked_groups=True,
        airport_code="LHR", airport_reference=airport_reference, ready_at=None,
        ready_at_reference="în treizeci de minute") if airport_reference is not None else None)
    set_report(ai, report, recovery)
    before = store.read()
    req = request(store, text)
    result = await service.command(req)
    assert result["applied"] is False and "report" not in result and "recovery_job" not in result
    if airport_reference is None:
        assert "raport operațional" in result["explanation"]
    assert_no_report(store, before, jobs)
    assert len(ai.calls) == 1 and ai.calls[0][0] == text
    service.live_commands = LiveCommands(store, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["explanation"] == result["explanation"]
    assert replay["applied"] is False and "report" not in replay and "recovery_job" not in replay
    assert len(ai.calls) == 1
    assert_no_report(store, before, jobs)

    # The profile and whole-case scope are otherwise usable. Refusing the
    # changed location must not disable an explicit ordinary saved-plan request.
    set_report(ai, None, recovery)
    valid = await service.command(request(store, followup, idempotency_key="ordinary-saved-hotel"))
    assert valid["recovery_job"]["status"] == "queued"
    assert valid["recovery_job"]["group_ids"] == ["G1"]
    assert valid["recovery_job"]["max_provider_calls"] == 10
    assert valid["recovery_job"]["calls_dispatched"] == 0


@pytest.mark.asyncio
async def test_explicit_romanian_inability_is_an_operator_outage(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(reference="Q1 nu poate funcționa"))
    result = await service.command(request(store, "Q1 nu poate funcționa"))
    assert result["applied"] and store.read().resources[0].available is False
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_different_service_predicate_cannot_be_bound_to_first_id(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(reference="Q1 is available, but Q2 is unavailable"))
    before = store.read()
    result = await service.command(request(store, "Q1 is available, but Q2 is unavailable"))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_two_group_locations_and_times_cannot_be_braided(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    claim = f"G1 at SBZ, G2 at OTP ready at {report['ready_at']}"
    report.update(group_ids=["G1", "G2"], report_reference=claim)
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, "For RO321: " + claim))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_readiness_of_second_group_cannot_apply_to_first_even_at_same_airport(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    claim = f"G1 at SBZ, G2 at SBZ ready at {report['ready_at']}"
    report.update(group_ids=["G1", "G2"], report_reference=claim)
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, "For RO321: " + claim))
    assert result["applied"] is False
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("subject,all_linked", [("G1, G2", False), ("all groups", True)])
@pytest.mark.asyncio
async def test_common_explicit_readiness_can_cover_all_named_groups(operational_setup, subject, all_linked):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    claim = f"Assume {subject} at SBZ ready at {report['ready_at']}"
    report.update(group_ids=[] if all_linked else ["G1", "G2"], all_linked_groups=all_linked, report_reference=claim)
    set_report(ai, report)
    result = await service.command(request(store, "For RO321: " + claim))
    assert result["applied"] and result["report"]["group_ids"] == ["G1", "G2"]
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_shared_group_name_cannot_expand_singular_reference(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    def duplicate_names(world):
        for group in world.groups[:2]:
            group.name = "Planning group 1"
    store.mutate("duplicate-group-names", {}, store.read().version, duplicate_names)
    report = arrival(store)
    claim = f"Assume Planning group 1 at SBZ ready at {report['ready_at']}"
    report.update(group_ids=["G1", "G2"], report_reference=claim)
    set_report(ai, report)
    before = store.read()
    result = await service.command(request(store, "For RO321: " + claim))
    assert result["applied"] is False and "ID-ul exact" in result["explanation"]
    assert_no_report(store, before, jobs)


@pytest.mark.asyncio
async def test_exact_service_id_disambiguates_shared_descriptive_name(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    claim = "Supplier dated transfer Q1 is unavailable"
    set_report(ai, outage(reference=claim))
    result = await service.command(request(store, claim))
    assert result["applied"] and result["report"]["resource_id"] == "Q1"
    assert store.read().resources[1].available is True
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_hotel_in_report_name_does_not_change_explicit_flight_only_search(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    store.mutate("hotel-shuttle-label", {}, store.read().version,
        lambda world: setattr(world.resources[0], "name", "Hotel shuttle"))
    set_report(ai, outage(reference="Hotel shuttle Q1 is unavailable"), intent(group_ids=["G1"], all_linked_groups=False, modes=["flight"]))
    result = await service.command(request(store, "Hotel shuttle Q1 is unavailable; recover RO321 for G1 using flights only, maximum 4 calls"))
    assert result["applied"] and "recovery_blocked" not in result
    job = result["recovery_job"]
    assert job["modes"] == ["flight"] and job["max_provider_calls"] == 3
    assert "hotel_recovery" not in jobs.ledger.get(job["id"], private=True)["_request"]


@pytest.mark.asyncio
async def test_dropped_hotel_in_actual_followup_cannot_become_flight_only(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, outage(), intent(group_ids=["G1"], all_linked_groups=False, modes=["flight"]))
    result = await service.command(request(store, "Q1 is unavailable; recover RO321 for G1 with hotel and both transfers and flight, maximum 11 calls"))
    assert result["applied"] and result["recovery_blocked"]["code"] == "hotel_setup_required"
    assert "recovery_job" not in result and jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.parametrize("requirements", [
    "with hotel and both transfers and flight, maximum 11 calls",
    "using saved hotel with pickup at 2026-11-02 14:00 UTC, maximum 11 calls",
])
@pytest.mark.asyncio
async def test_overbroad_report_quote_cannot_erase_followup_hotel_or_time_requirements(operational_setup, requirements):
    _, store, service, jobs, ai, _ = operational_setup
    compound = "Q1 is unavailable; recover RO321 for G1 " + requirements
    # The malformed model both quotes away the actual follow-up and drops its
    # hotel mode. The guard must reject before report mutation or job creation.
    set_report(ai, outage(reference=compound), intent(group_ids=["G1"], all_linked_groups=False, modes=["flight"]))
    before = store.read()
    req = request(store, compound)
    result = await service.command(req)
    assert result["applied"] is False and "identificate separat" in result["explanation"]
    assert "report" not in result and "recovery_job" not in result
    assert result["command_interpretation_calls"] == 1 and len(ai.calls) == 1
    assert_no_report(store, before, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["applied"] is False and len(ai.calls) == 1
    assert_no_report(store, before, jobs)
