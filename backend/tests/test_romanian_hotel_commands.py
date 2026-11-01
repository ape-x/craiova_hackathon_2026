"""Saved Romanian package commands: isolated SQLite and fixture sources only."""
from datetime import datetime, timedelta
import json

import pytest

from app.live_commands import LiveCommands
from app.models import CommandRequest, Resource
from app.store import DomainError
from test_live_commands import FakeAI, intent, interpretation, command_setup
from test_hotel_recovery_profile import hotel_command_setup
from test_recovery_jobs import setup, run_next
from test_romanian_hotel_recovery import romania, request as typed_request, fixture_source_terms


def stable(store):
    return store.read().model_dump(mode="json", exclude={"sim_time"})


async def complete(env):
    store, jobs, *_ = env
    jobs.start("CASE", typed_request(env))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    return jobs.romanian_hotel_profiles.read(store.read(), store.read().groups[0])


def spoken(env, text=None, *, report=None, groups=None, modes=None, key="saved-romanian-command", recovery=True):
    store, jobs, *_ = env
    ai = FakeAI()
    ai.result = interpretation(recovery=intent(incident_id="CASE", group_ids=groups or ["G1"], all_linked_groups=False,
        modes=modes or ["flight", "hotel", "bus"], requires_input_changes=False) if recovery else None,
        operational_report=report)
    jobs.service.ai = ai
    jobs.service.live_commands = LiveCommands(store, jobs)
    req = CommandRequest(expected_version=store.read().version, idempotency_key=key,
        text=text or "Recover RO321 for G1 using the saved Transfero hotel configuration, maximum nine calls")
    return ai, req


def assert_queued(env, result, inputs, total=9):
    store, jobs, *_ = env
    assert "recovery_job" in result, result
    job = result["recovery_job"]
    assert job["status"] == "queued" and job["calls_dispatched"] == 0
    assert job["group_ids"] == ["G1"] and job["max_provider_calls"] == total-1
    assert result["command_interpretation_calls"] == 1 and result["recovery_total_call_limit"] == total
    assert job["romanian_hotel_recovery"] == inputs.model_dump(mode="json")
    private = jobs.ledger.get(job["id"], private=True)["_request"]
    assert not private.get("hotel_recovery") and not private.get("airport_transfers") and not private.get("after_flight_transfer")
    assert "configurația românească salvată" in result["explanation"]
    assert "căutate din nou" in result["explanation"]
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Recover RO321 for G1 using the saved Transfero hotel configuration, maximum nine calls",
    "Recuperează RO321 pentru G1 folosind configurația salvată Direct Aeroport, maximum nouă apeluri",
    "Recalculează RO321 pentru G1 și folosește configurația salvată Hotel Aro Palace, maximum 9 apeluri",
    "Use the saved Romanian coach + hotel configuration for RO321 G1, maximum 9 calls, without bookings",
    "Recover RO321 G1 using the saved Romanian hotel and transfer configuration, maximum 9 calls",
    "Folosește configurația românească salvată Transfero pentru RO321 G1, maximum 9 apeluri",
])
async def test_explicit_reuse_copies_exact_inputs_and_sources_fresh_complete_journey(romania, text):
    inputs = await complete(romania)
    store, jobs, planning, travel, coach, hotel, fx, _, clock = romania
    clock["now"] += timedelta(seconds=1)  # The fresh supplier revision has its own receipt timestamp.
    before = stable(store)
    ai, req = spoken(romania, text)
    result = await jobs.service.command(req)
    job = assert_queued(romania, result, inputs)
    assert result["applied"] is False and stable(store) == before
    assert len(coach.calls) == 2 and len(travel.calls) == 1 and fx.calls == 1
    assert jobs.ledger.get(job["id"], private=True)["_request"]["instructions"] == text
    replay = await jobs.service.command(req)
    assert replay["replayed"] and replay["recovery_job"]["id"] == job["id"] and len(ai.calls) == 1
    finished = await run_next(jobs)
    assert finished["status"] == "completed" and finished["calls_dispatched"] == 8, (finished["message"], finished["steps"])
    assert len(coach.calls) == 4 and len(travel.calls) == 2 and fx.calls == 2
    assert len(planning.decisions) == 2 and not any(r.booking_status == "confirmed" for r in store.read().resources)


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", [
    "but check-in tomorrow", "with two rooms", "with one suitcase", "with zero handbags",
    "with processing 240 minutes", "with hotel margin 30 minutes", "with one night",
    "but stay at Hilton", "but another hotel", "to SBZ", "with destination LHR", "OTP→SBZ", "with budget 200 EUR", "with three passengers",
    "cu trei pasageri", "with wheelchair assistance", "with maximum two hundred euros",
    "and the saved UK hotel configuration", "and the initial transfer", "and saved transfer after flight",
    "and the saved airport transfer configuration", "și configurația salvată de transfer aeroportuar",
    ". Use the other saved transfer configuration",
    "if possible", "unless it is unavailable",
])
async def test_literal_edits_or_ambiguity_cannot_be_omitted_by_model(romania, tail):
    await complete(romania)
    store, jobs, _, travel, coach, *_ = romania
    before = stable(store)
    _, req = spoken(romania, "Recover RO321 for G1 using the saved Transfero hotel configuration "+tail+", maximum 11 calls")
    result = await jobs.service.command(req)
    assert "recovery_job" not in result and not result["applied"], result
    assert stable(store) == before and len(coach.calls) == 2 and len(travel.calls) == 1
    assert len(jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "There is a saved Transfero hotel configuration. Recover RO321 G1, maximum nine calls",
    "We used the saved Transfero hotel configuration. Recover RO321 G1, maximum nine calls",
    "Recover RO321 G1 without using the saved Transfero hotel configuration, maximum nine calls",
    "If possible. Recover RO321 G1 using the saved Transfero hotel configuration, maximum nine calls",
    "Recover RO321 G1 using Transfero and Hotel Aro Palace, maximum nine calls",
    "Recover RO321 G1 using the saved Transfero quote, maximum nine calls",
])
async def test_descriptive_negated_unsaved_or_nonconfiguration_reference_is_not_authority(romania, text):
    await complete(romania)
    store, jobs, *_ = romania
    before = stable(store)
    _, req = spoken(romania, text)
    result = await jobs.service.command(req)
    assert not result["applied"] and "recovery_job" not in result
    assert stable(store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("text,groups,modes", [
    ("Recover RO321 G1 and G2 using the saved Transfero hotel configuration, maximum nine calls", ["G1"], ["flight", "hotel", "bus"]),
    ("Recover RO321 G1 and G2 using the saved Transfero hotel configuration, maximum nine calls", ["G1", "G2"], ["flight", "hotel", "bus"]),
    ("Recover RO321 G1 using the saved Transfero hotel configuration, maximum nine calls", ["G1"], ["flight"]),
])
async def test_scope_and_services_are_not_narrowed_or_expanded(romania, text, groups, modes):
    await complete(romania)
    store, jobs, *_ = romania
    _, req = spoken(romania, text, groups=groups, modes=modes)
    result = await jobs.service.command(req)
    assert not result["applied"] and "recovery_job" not in result and len(jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["", ", maximum eight calls", ", maximum seven calls"])
async def test_default_or_lower_allowance_is_never_raised(romania, limit):
    await complete(romania)
    store, jobs, *_ = romania
    _, req = spoken(romania, "Recover RO321 G1 using the saved Transfero hotel configuration"+limit)
    result = await jobs.service.command(req)
    assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "insufficient_call_allowance"
    assert result["recovery_total_call_limit"] == (7 if "seven" in limit else 8)
    assert len(jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "identity", "readiness", "deadline", "elapsed", "budget"])
async def test_saved_profile_and_current_constraints_must_both_be_valid(romania, change):
    store, jobs, _, _, coach, *_, clock = romania
    if change != "missing":
        await complete(romania)
        if change == "elapsed":
            clock["now"] = datetime.fromisoformat(romania[-2]["checkin_at"])+timedelta(minutes=1)
        else:
            def edit(world):
                group = world.groups[0]
                if change == "identity": group.origin = "SBZ"
                if change == "readiness": group.ready_at = group.deadline
                if change == "deadline": group.deadline = group.ready_at+60
                if change == "budget": group.budget = 0
            store.mutate("constraint-fixture", {}, store.read().version, edit)
    _, req = spoken(romania)
    result = await jobs.service.command(req)
    # Budget alone is a mutable solver constraint; zero budget cannot create a
    # complete plan but does not invalidate input identity or authorize guesses.
    if change == "budget":
        assert result["recovery_job"]["romanian_hotel_recovery"] == jobs.romanian_hotel_profiles.read(store.read(), store.read().groups[0]).model_dump(mode="json")
    else:
        assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "romanian_hotel_setup_required", result
    assert len(coach.calls) == (0 if change == "missing" else 2)


def reports(env, *, airport="OTP", paired="service", swallowed=False):
    store, *_ = env
    ready = "in 30 minutes"
    arrival = {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"], "all_linked_groups": False,
        "airport_code": airport, "airport_reference": airport, "ready_at": None, "ready_at_reference": ready,
        "report_reference": "G1 at "+airport+" ready "+ready}
    if paired == "restriction":
        first = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "SBZ", "airport_reference": "SBZ",
            "operations": ["arrivals"], "action": "restrict", "report_reference": "SBZ is closed for arrivals"}
        bundle = {"type": "airport_restriction_and_arrival", "restriction": first, "arrival": arrival}
    else:
        def add(world):
            world.resources.append(Resource(id="BUS1", kind="bus", name="Existing operator service", origin="OTP", destination="CRA",
                departure=world.groups[0].ready_at, arrival=world.groups[0].ready_at+60, capacity=2, cost_per_person=20, source="operator_report"))
        store.mutate("existing-service-fixture", {}, store.read().version, add)
        first = {"type": "resource_unavailability", "resource_id": "BUS1", "resource_reference": "BUS1", "report_reference": "BUS1 is unavailable"}
        bundle = {"type": "resource_unavailability_and_arrival", "unavailability": first, "arrival": arrival}
    if swallowed:
        first["report_reference"] += ". Use the saved Transfero hotel configuration"
    text = "For RO321. "+first["report_reference"]+". "+arrival["report_reference"]+". Recover RO321 G1 using the saved Transfero hotel configuration, maximum nine calls"
    return bundle, text


@pytest.mark.asyncio
@pytest.mark.parametrize("paired", ["service", "restriction"])
@pytest.mark.parametrize("airport", ["OTP", "SBZ"])
async def test_paired_reports_reuse_post_report_world_or_preserve_both_on_known_block(romania, paired, airport):
    inputs = await complete(romania)
    store, jobs, _, travel, coach, *_ = romania
    bundle, text = reports(romania, paired=paired, airport=airport)
    before = store.read()
    ai, req = spoken(romania, text, report=bundle)
    result = await jobs.service.command(req)
    assert result["applied"] and len(result["reports"]) == 2 and store.read().version == before.version+1, result
    assert store.read().groups[0].origin == airport and result["focus_id"] == airport
    if airport == "OTP":
        assert_queued(romania, result, inputs)
    else:
        assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "romanian_hotel_setup_required"
    assert len(coach.calls) == 2 and len(travel.calls) == 1
    replay = await jobs.service.command(req)
    assert replay["replayed"] and replay["reports"] == result["reports"] and len(ai.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("paired", ["service", "restriction"])
async def test_unexpected_post_report_validation_failure_rolls_back_both_reports(romania, monkeypatch, paired):
    await complete(romania)
    store, jobs, *_ = romania
    bundle, text = reports(romania, paired=paired)
    _, req = spoken(romania, text, report=bundle)
    before = stable(store)
    def reject(*args): raise DomainError(409, "Unexpected validation drift")
    monkeypatch.setattr("app.romanian_hotel_recovery.validate_romanian_hotel_job", reject)
    with pytest.raises(DomainError) as error:
        await jobs.service.command(req)
    assert error.value.status == 409 and stable(store) == before
    assert len(jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("paired", ["service", "restriction"])
async def test_saved_reuse_cannot_be_swallowed_inside_report_quote(romania, paired):
    await complete(romania)
    store, jobs, *_ = romania
    bundle, text = reports(romania, paired=paired, swallowed=True)
    _, req = spoken(romania, text, report=bundle)
    before = stable(store)
    result = await jobs.service.command(req)
    assert not result["applied"] and "recovery_job" not in result and stable(store) == before
    assert len(jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_profile_presence_does_not_implicitly_select_package_and_omission_does_not_fallback(romania):
    await complete(romania)
    store, jobs, *_ = romania
    _, req = spoken(romania, "Recover RO321 G1 by flight, maximum nine calls", modes=["flight"])
    result = await jobs.service.command(req)
    assert "romanian_hotel_recovery" not in jobs.ledger.get(result["recovery_job"]["id"], private=True)["_request"]
    ai, req = spoken(romania, key="omitted-intent", recovery=False)
    result = await jobs.service.command(req)
    assert not result["applied"] and "recovery_job" not in result and len(ai.calls) == 1


@pytest.mark.asyncio
async def test_safe_summary_is_reference_only_and_contains_no_inputs_or_identity_hash(romania):
    await complete(romania)
    store, jobs, *_ = romania
    ai, req = spoken(romania)
    await jobs.service.command(req)
    summary = ai.calls[0][1]["romanian_hotel_profiles"]["G1"]
    assert summary["configured"] and summary["minimum_total_calls"] == 9 and summary["requires_current_validation"]
    assert not {"inputs", "suitcases", "handbags", "scope", "fingerprint", "members", "token"}.intersection(summary)
    assert "fixture-room-offer" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_passenger_label_containing_source_words_never_authorizes_package(romania):
    await complete(romania)
    store, jobs, *_ = romania
    label = "Saved Transfero hotel configuration"
    def rename(world): world.groups[0].name = label
    store.mutate("passenger-label-fixture", {}, store.read().version, rename)
    _, req = spoken(romania, "Recover RO321 for "+label+" using saved hotel settings, maximum eleven calls")
    before = stable(store)
    result = await jobs.service.command(req)
    assert not result["applied"] and "recovery_job" not in result
    assert len(jobs.list("CASE")["jobs"]) == 1 and stable(store) == before


@pytest.mark.asyncio
async def test_explicit_case_cannot_hide_another_named_flight(romania):
    await complete(romania)
    store, jobs, *_ = romania
    def add(world):
        other = world.incidents[0].model_copy(deep=True)
        other.id, other.requested_flight_number, other.group_ids = "OTHER", "RO999", []
        world.incidents.append(other)
    store.mutate("second-case-fixture", {}, store.read().version, add)
    _, req = spoken(romania, "Recover CASE and RO999 for G1 using the saved Transfero hotel configuration, maximum nine calls")
    result = await jobs.service.command(req)
    assert not result["applied"] and "recovery_job" not in result
    assert result["recovery_blocked"]["code"] == "romanian_hotel_setup_required"


@pytest.mark.asyncio
async def test_exact_attached_case_date_is_identity_not_a_stay_edit(romania):
    inputs = await complete(romania)
    store, jobs, *_ = romania
    date = store.read().incidents[0].date_utc
    _, req = spoken(romania, f"Recover RO321 from {date} for G1 using the saved Transfero hotel configuration, maximum nine calls")
    result = await jobs.service.command(req)
    assert_queued(romania, result, inputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("paired", [None, "service", "restriction"])
@pytest.mark.parametrize("extra", ["take one suitcase", "cu două valize", "with budget 200 EUR", "with two rooms",
    "with processing 240 minutes", "check-in tomorrow", "with three passengers",
    "use another hotel", "use route via SBZ", "use the saved airport transfer configuration"])
async def test_report_quote_cannot_erase_additional_package_inputs(romania, paired, extra):
    await complete(romania)
    store, jobs, _, travel, coach, *_ = romania
    bundle, text = reports(romania, paired=paired or "service")
    report = bundle["restriction"] if paired == "restriction" else bundle["unavailability"]
    original = report["report_reference"]
    report["report_reference"] += ", "+extra
    if paired is None:
        value = report
        text = "For RO321. "+report["report_reference"]+". Recover RO321 G1 using the saved Transfero hotel configuration, maximum nine calls"
    else:
        value = bundle
        text = text.replace(original, report["report_reference"], 1)
    before = store.read().version
    _, req = spoken(romania, text, report=value)
    result = await jobs.service.command(req)
    if paired == "restriction" and extra in {"check-in tomorrow", "use another hotel", "use route via SBZ", "use the saved airport transfer configuration"}:
        # Existing restriction grounding rejects timed/mixed assertions before
        # mutation; the new mask guard must not weaken that stricter boundary.
        assert not result["applied"] and store.read().version == before
    else:
        assert result["applied"] and store.read().version == before+1, result
        assert result["recovery_blocked"]["code"] == "romanian_hotel_setup_required", result
        if paired: assert len(result["reports"]) == 2
    assert "recovery_job" not in result
    assert len(jobs.list("CASE")["jobs"]) == 1 and len(coach.calls) == 2 and len(travel.calls) == 1


@pytest.mark.asyncio
async def test_arrival_quote_keeps_readiness_but_cannot_hide_extra_room_input(romania):
    await complete(romania)
    store, jobs, *_ = romania
    bundle, text = reports(romania)
    original = bundle["arrival"]["report_reference"]
    bundle["arrival"]["report_reference"] += ", take two rooms"
    text = text.replace(original, bundle["arrival"]["report_reference"], 1)
    _, req = spoken(romania, text, report=bundle)
    result = await jobs.service.command(req)
    assert result["applied"] and result["recovery_blocked"]["code"] == "romanian_hotel_setup_required", result
    assert "recovery_job" not in result and store.read().groups[0].origin == "OTP"


@pytest.mark.asyncio
async def test_literal_service_label_and_passenger_readiness_are_not_package_edits(romania):
    inputs = await complete(romania)
    store, jobs, *_ = romania
    bundle, text = reports(romania)
    label = "Two suitcases and hotel rooms service"
    def rename(world):
        next(item for item in world.resources if item.id == "BUS1").name = label
        world.groups[0].name = "Passengers with two handbags"
    store.mutate("literal-label-fixture", {}, store.read().version, rename)
    bundle["unavailability"].update(resource_reference=label, report_reference=label+" is unavailable")
    bundle["arrival"]["report_reference"] = "Passengers G1 at OTP ready in 30 minutes"
    text = "For RO321. "+bundle["unavailability"]["report_reference"]+". "+bundle["arrival"]["report_reference"]+". Recover RO321 G1 using the saved Transfero hotel configuration, maximum nine calls"
    _, req = spoken(romania, text, report=bundle)
    result = await jobs.service.command(req)
    assert result["applied"], result
    assert_queued(romania, result, inputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["", ", take one suitcase"])
async def test_shared_report_guard_preserves_uk_reuse_but_not_swallowed_baggage(hotel_command_setup, extra):
    _, store, service, jobs, ai, inputs = hotel_command_setup
    def add(world):
        world.resources.append(Resource(id="Q1", name="Known coach service", kind="bus", origin="LHR", destination="OTP",
            departure=world.groups[0].ready_at, arrival=world.groups[0].ready_at+60, capacity=2, cost_per_person=20, source="operator_report"))
    store.mutate("uk-report-service", {}, store.read().version, add)
    claim = "Q1 is unavailable"+extra
    ai.result = interpretation(operational_report={"type": "resource_unavailability", "resource_id": "Q1",
        "resource_reference": "Q1", "report_reference": claim},
        recovery=intent(group_ids=["G1"], all_linked_groups=False, modes=["flight", "hotel", "bus"]))
    req = CommandRequest(expected_version=store.read().version, idempotency_key="uk-report-bags",
        text=claim+". Recover RO321 for G1 using saved hotel settings, maximum eleven calls")
    result = await service.command(req)
    assert result["applied"] and not next(r for r in store.read().resources if r.id == "Q1").available
    if extra:
        assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "hotel_setup_required", result
        assert not jobs.list("RC-ONE")["jobs"]
    else:
        assert result["recovery_job"]["status"] == "queued" and result["recovery_job"]["calls_dispatched"] == 0
        assert jobs.ledger.get(result["recovery_job"]["id"], private=True)["_request"]["hotel_recovery"] == inputs.model_dump(mode="json")
