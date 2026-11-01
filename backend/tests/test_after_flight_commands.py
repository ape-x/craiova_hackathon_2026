"""Saved post-flight command composition: real SQLite and mocked sources/AI only."""
from datetime import timedelta
import json

import pytest

from app.live_commands import LiveCommands
from app.models import CommandRequest
from app.providers.ai import compact_world
from app.providers.bucharest_cab import SOURCE
from app.store import DomainError
from test_after_flight_recovery import connection, env, request as typed_request, setup
from test_airport_cab_recovery_jobs import NOW, change, run_next, stable
from test_live_commands import FakeAI, intent, interpretation


async def complete(e, **changes):
    e.jobs.start("CASE", typed_request(e, **changes))
    result = await run_next(e.jobs)
    assert result["status"] == "completed", result
    world = e.store.read()
    return result, e.jobs.after_flight_profiles.read(world, world.groups[0])


def spoken(e, text=None, *, key="offline-spoken-afterflight", modes=None, groups=None, report=None):
    ai = FakeAI()
    ai.result = interpretation(recovery=intent(incident_id="CASE", group_ids=groups or ["G1"],
        all_linked_groups=False, modes=modes or ["flight", "bus"], requires_input_changes=False), operational_report=report)
    service = e.jobs.service
    service.ai = ai
    service.live_commands = LiveCommands(e.store, e.jobs)
    body = CommandRequest(expected_version=e.store.read().version, idempotency_key=key,
        text=text or "Recover RO321 for G1 using the saved after-flight transfer with maximum 7 calls")
    return service, ai, body


def queued(e, response, saved, *, total=7):
    assert "recovery_job" in response, response.get("explanation")
    job = response["recovery_job"]
    assert job["status"] == "queued" and job["calls_dispatched"] == 0
    assert job["group_ids"] == ["G1"] and job["max_provider_calls"] == total-1
    assert job["after_flight_transfer"] == saved.model_dump(mode="json")
    assert response["command_interpretation_calls"] == 1 and response["recovery_total_call_limit"] == total
    assert "configurația salvată" in response["explanation"] and "căutate din nou" in response["explanation"]
    private = e.jobs.ledger.get(job["id"], private=True)["_request"]
    assert not private.get("airport_transfers") and not private.get("hotel_recovery")
    assert private["instructions"]
    return job


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Recover RO321 for G1 using the saved after-flight transfer with maximum 7 calls",
    "Recover RO321 for G1 using the saved transfer after the flight with maximum seven calls",
    "Recuperează RO321 pentru G1 folosind configurația salvată pentru transferul după zbor cu maximum șapte apeluri",
    "Recuperează RO321 pentru G1 și folosește configurația transferului salvat după zbor cu maximum 7 apeluri",
    "Recover RO321 from 2030-01-04 for G1 using the saved postflight configuration, no more than 7 calls, without bookings",
])
async def test_explicit_saved_configuration_is_exact_fresh_scoped_and_replay_safe(setup, text):
    e = setup
    first, saved = await complete(e)
    before = stable(e.store.read())
    service, ai, body = spoken(e, text)
    response = await service.command(body)
    job = queued(e, response, saved)
    assert response["applied"] is False and stable(e.store.read()) == before
    assert len(e.source.calls) == 2 and len(e.flights.calls) == 1
    assert e.jobs.ledger.get(job["id"], private=True)["_request"]["instructions"] == text
    replay = await service.command(body)
    assert replay["replayed"] and replay["recovery_job"]["id"] == job["id"]
    assert len(ai.calls) == 1 and len(e.source.calls) == 2
    result = await run_next(e.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 6, result
    assert len(e.source.calls) == 4 and len(e.flights.calls) == 2
    assert e.jobs.after_flight_profiles.public_for_case(e.store.read(), "CASE")[0]["source_job_id"] == result["id"] != first["id"]
    assert not any(item.booking_status == "confirmed" for item in e.store.read().resources)


@pytest.mark.asyncio
@pytest.mark.parametrize("total,maximum,calls", [(8, 2, 6), (9, 2, 8), (9, 1, 6)])
async def test_spoken_allowance_and_saved_candidate_bound_are_not_increased(setup, total, maximum, calls):
    e = setup
    _, saved = await complete(e, after_flight_transfer=connection(max_inbound_options=maximum))
    e.flights.count = 2
    text = "Recover RO321 G1 using the saved transfer after the flight" + (f" with maximum {total} calls" if total != 8 else "")
    service, _, body = spoken(e, text)
    queued(e, await service.command(body), saved, total=total)
    result = await run_next(e.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == calls, result
    assert e.ai.shortlists[-1][2]["max_inbound_options"] == min(maximum, 2 if total == 9 else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", [
    "using the saved transfer after the flight but pickup at 18:00",
    "using the saved after-flight transfer with two suitcases",
    "using the saved after-flight transfer with processing 45 minutes",
    "using the saved after-flight transfer with window tomorrow",
    "using the saved after-flight transfer to SBZ",
    "using the saved after-flight transfer with destination SBZ",
    "using the saved after-flight transfer LHR→OTP",
    "using the saved after-flight transfer but only one inbound option",
    "folosind transferul salvat după zbor cu doar o variantă",
    "using the saved after-flight transfer, compare two flights",
    "using the saved after-flight transfer and the initial transfer",
    "using the saved after-flight transfer. Use the other saved transfer",
    "using the saved after-flight transfer if possible",
    "without using the saved after-flight transfer",
    "using the saved after-flight transfer with budget 200 EUR",
    "using the saved after-flight transfer with wheelchair assistance",
    "using the saved after-flight transfer with three passengers",
    "folosind configurația salvată de transfer după zbor cu trei pasageri",
    "using the saved after-flight transfer with maximum two hundred euros",
    "folosind transferul salvat după zbor cu maximum două sute de euro",
])
async def test_literal_edits_negation_or_extra_services_are_blocked_despite_model_omission(setup, tail):
    e = setup
    await complete(e)
    before = stable(e.store.read())
    service, ai, body = spoken(e, "Recover RO321 for G1 " + tail + " with maximum 9 calls")
    result = await service.command(body)
    assert "recovery_job" not in result and result["applied"] is False, result
    assert result["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert stable(e.store.read()) == before and len(e.source.calls) == 2
    assert len(e.jobs.list("CASE")["jobs"]) == 1 and len(ai.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "There is a saved after-flight transfer. Recover RO321 for G1 with maximum 7 calls",
    "We used the saved after-flight transfer. Recover RO321 G1 with maximum 7 calls",
    "If possible. Recover RO321 for G1 using the saved after-flight transfer with maximum 7 calls",
    "Recover RO321 for G1 using a transfer after the flight with maximum 7 calls",
])
async def test_saved_profile_existence_does_not_authorize_implicit_or_descriptive_reuse(setup, text):
    e = setup
    await complete(e)
    service, _, body = spoken(e, text)
    result = await service.command(body)
    assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "airport_transfer_setup_required", result
    assert len(e.source.calls) == 2 and len(e.jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_plain_flight_search_does_not_copy_any_saved_transfer(setup):
    e = setup
    await complete(e)
    service, _, body = spoken(e, "Recover RO321 for G1 using flight searches with maximum 7 calls", modes=["flight"])
    result = await service.command(body)
    assert "recovery_job" in result, result
    private = e.jobs.ledger.get(result["recovery_job"]["id"], private=True)["_request"]
    assert "after_flight_transfer" not in private and "airport_transfers" not in private
    assert len(e.source.calls) == 2


@pytest.mark.asyncio
async def test_missing_profile_and_insufficient_allowance_do_not_start_source_work(setup):
    e = setup
    service, _, body = spoken(e)
    result = await service.command(body)
    assert result["recovery_blocked"]["code"] == "airport_transfer_setup_required" and not e.jobs.list("CASE")["jobs"]
    assert not e.source.calls and not e.flights.calls
    await complete(e)
    service, _, body = spoken(e, "Recover RO321 G1 using the saved after-flight transfer with maximum 6 calls", key="offline-low-afterflight")
    result = await service.command(body)
    assert result["recovery_blocked"]["code"] == "insufficient_call_allowance"
    assert result["recovery_total_call_limit"] == 6 and "recovery_job" not in result
    assert len(e.source.calls) == 2


@pytest.mark.asyncio
async def test_interpretation_summary_is_reference_only_and_omits_bags_and_private_fingerprints(setup):
    e = setup
    await complete(e)
    service, ai, body = spoken(e)
    await service.command(body)
    model_world = ai.calls[0][1]
    summary = model_world["after_flight_transfer_profiles"]["G1"]
    assert summary["configured"] and summary["max_inbound_options"] == 2
    assert summary["minimum_total_calls"] == 7 and summary["two_variant_total_calls"] == 9
    assert summary["requires_current_validation"] is True
    assert not {"suitcases", "handbags", "fingerprint", "identity_fingerprint", "source_job_id"} & summary.keys()
    packed = compact_world(model_world)
    assert packed["groups"][0]["after_flight_transfer_profile"] == summary
    assert "offline-one" not in json.dumps(packed)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bus_failure", "restriction", "arrival", "restriction_and_arrival"])
async def test_report_and_saved_afterflight_recovery_use_post_report_scope_without_reinterpreting_inputs(setup, kind):
    e = setup
    _, saved = await complete(e)
    before = e.store.read()
    restriction_claim = "CRA is closed for arrivals"
    restriction = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "CRA",
        "airport_reference": "CRA", "operations": ["arrivals"], "action": "restrict", "report_reference": restriction_claim}
    timestamp = (NOW+timedelta(minutes=1)).isoformat()
    arrival_claim = f"Assume G1 at LHR ready at {timestamp}"
    arrival = {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"], "all_linked_groups": False,
        "airport_code": "LHR", "airport_reference": "LHR", "ready_at": timestamp,
        "ready_at_reference": timestamp, "report_reference": arrival_claim}
    if kind == "bus_failure":
        resource = next(item for item in before.resources if item.source == SOURCE)
        claim = resource.id+" is unavailable"
        report = {"type": "resource_unavailability", "resource_id": resource.id,
            "resource_reference": resource.id, "report_reference": claim}
    elif kind == "restriction":
        claim, report = restriction_claim, restriction
    elif kind == "arrival":
        claim, report = arrival_claim, arrival
    else:
        claim = restriction_claim+". "+arrival_claim
        report = {"type": "airport_restriction_and_arrival", "restriction": restriction, "arrival": arrival}
    text = claim+". Recover RO321 for G1 using the saved transfer after the flight with maximum 7 calls"
    service, ai, body = spoken(e, text, report=report)
    receipt = await service.command(body)
    job = queued(e, receipt, saved)
    assert receipt["applied"] and job["source_version"] == before.version+1
    assert len(ai.calls) == 1 and len(e.source.calls) == 2
    assert e.store.read().groups[1] == before.groups[1]
    if kind == "restriction_and_arrival":
        assert [item["type"] for item in receipt["reports"]] == ["airport_restriction", "arrival_report"]
        assert receipt["report"] == receipt["reports"][1]
    result = await run_next(e.jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 6, result
    assert len(e.source.calls) == 4 and len(e.flights.calls) == 2
    assert e.store.read().groups[0].origin == "LHR"
    replay = await service.command(body)
    assert replay["replayed"] and replay["report"] == receipt["report"]
    assert len(ai.calls) == 1 and len(e.source.calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["arrival_new_origin", "arrival_later_readiness", "restriction", "bundle_missing_profile"])
async def test_known_saved_setup_block_keeps_independently_valid_report_facts(setup, kind):
    e = setup
    if kind != "bundle_missing_profile":
        await complete(e)
    source_calls = len(e.source.calls)
    prior_jobs = len(e.jobs.list("CASE")["jobs"])
    timestamp = (NOW+timedelta(minutes=601 if kind == "arrival_later_readiness" else 1)).isoformat()
    airport = "OTP" if kind == "arrival_new_origin" else "LHR"
    arrival_claim = f"Assume G1 at {airport} ready at {timestamp}"
    arrival = {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"], "all_linked_groups": False,
        "airport_code": airport, "airport_reference": airport, "ready_at": timestamp,
        "ready_at_reference": timestamp, "report_reference": arrival_claim}
    restriction_claim = "OTP is closed for arrivals"
    restriction = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "OTP",
        "airport_reference": "OTP", "operations": ["arrivals"], "action": "restrict", "report_reference": restriction_claim}
    if kind == "restriction":
        claim, report = restriction_claim, restriction
    elif kind == "bundle_missing_profile":
        claim, report = restriction_claim+". "+arrival_claim, {"type": "airport_restriction_and_arrival", "restriction": restriction, "arrival": arrival}
    else:
        claim, report = arrival_claim, arrival
    service, ai, body = spoken(e, claim+". Recover RO321 G1 using the saved after-flight transfer with maximum 7 calls", report=report)
    before = e.store.read()
    response = await service.command(body)
    assert response["applied"] and response["recovery_blocked"]["code"] == "airport_transfer_setup_required", response
    assert "recovery_job" not in response and e.store.read().version == before.version+1
    assert len(e.jobs.list("CASE")["jobs"]) == prior_jobs and len(e.source.calls) == source_calls
    if kind == "bundle_missing_profile":
        assert len(response["reports"]) == 2 and e.store.read().groups[0].ready_at == 1
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_model_cannot_drop_a_second_literally_requested_group(setup):
    e = setup
    await complete(e)
    service, _, body = spoken(e, "Recover RO321 for G1 and G2 using the saved after-flight transfer with maximum 7 calls", groups=["G1"])
    response = await service.command(body)
    assert "recovery_job" not in response and response["recovery_blocked"]["code"] == "airport_transfer_setup_required", response
    assert len(e.source.calls) == 2 and len(e.jobs.list("CASE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_requested_flight_only_does_not_silently_drop_saved_afterflight_ground_leg(setup):
    e = setup
    await complete(e)
    service, _, body = spoken(e, modes=["flight"])
    response = await service.command(body)
    assert "recovery_job" not in response and response["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert len(e.source.calls) == 2


@pytest.mark.asyncio
async def test_elapsed_window_blocks_saved_reuse_without_a_later_pickup(setup):
    e = setup
    _, saved = await complete(e)
    e.clock[0] = saved.pickup_window_start+timedelta(minutes=1)
    before = stable(e.store.read())
    service, _, body = spoken(e)
    result = await service.command(body)
    assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert stable(e.store.read()) == before and len(e.source.calls) == 2
    assert e.jobs.after_flight_profiles.read(e.store.read(), e.store.read().groups[0]) == saved


@pytest.mark.asyncio
async def test_stale_party_profile_is_not_replaced_with_current_capacity_assumptions(setup):
    e = setup
    await complete(e)
    def change_party(world):
        world.groups[0].size = world.groups[0].adult_count = 3
    change(e.store, change_party)
    service, _, body = spoken(e)
    result = await service.command(body)
    assert "recovery_job" not in result and result["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert len(e.source.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [409, 500])
async def test_unexpected_post_report_validation_error_rolls_back_report(setup, monkeypatch, status):
    e = setup
    await complete(e)
    stamp = (NOW+timedelta(minutes=1)).isoformat()
    claim = f"Assume G1 at LHR ready at {stamp}"
    report = {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"], "all_linked_groups": False,
        "airport_code": "LHR", "airport_reference": "LHR", "ready_at": stamp, "ready_at_reference": stamp, "report_reference": claim}
    def unexpected(*args):
        raise DomainError(status, "Offline unexpected validation fault")
    monkeypatch.setattr("app.after_flight_commands.validate_after_flight_job", unexpected)
    service, _, body = spoken(e, claim+". Recover RO321 for G1 using the saved after-flight transfer with maximum 7 calls", report=report)
    before = stable(e.store.read())
    with pytest.raises(DomainError) as error:
        await service.command(body)
    assert error.value.status == status and stable(e.store.read()) == before
    assert len(e.jobs.list("CASE")["jobs"]) == 1 and len(e.source.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle", [False, True])
@pytest.mark.parametrize("brand", [False, True])
async def test_report_reference_cannot_swallow_a_separate_use_saved_instruction(setup, bundle, brand):
    e = setup
    await complete(e)
    reuse = "Use the saved Bucharest.CAB transfer after the flight" if brand else "Use the saved after-flight transfer configuration"
    before = e.store.read()
    if bundle:
        stamp = (NOW+timedelta(minutes=1)).isoformat()
        claim = f"Assume G1 at LHR ready at {stamp}"
        report = {"type": "airport_restriction_and_arrival",
            "restriction": {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "CRA", "airport_reference": "CRA",
                "operations": ["arrivals"], "action": "restrict", "report_reference": "CRA is closed for arrivals"},
            "arrival": {"type": "arrival_report", "incident_id": "CASE", "group_ids": ["G1"], "all_linked_groups": False,
                "airport_code": "LHR", "airport_reference": "LHR", "ready_at": stamp, "ready_at_reference": stamp,
                "report_reference": claim+". "+reuse}}
        prefix = "CRA is closed for arrivals. "+claim
    else:
        resource = next(item for item in before.resources if item.source == SOURCE)
        prefix = resource.id+" is unavailable"
        report = {"type": "resource_unavailability", "resource_id": resource.id, "resource_reference": resource.id,
            "report_reference": prefix+". "+reuse}
    service, _, body = spoken(e, prefix+". "+reuse+". Recalculate RO321 for G1, maximum 7 calls", report=report)
    result = await service.command(body)
    assert result["applied"] is False and "recovery_job" not in result, result
    assert stable(e.store.read()) == stable(before)
    assert len(e.jobs.list("CASE")["jobs"]) == 1 and len(e.source.calls) == 2


@pytest.mark.asyncio
async def test_separate_imperative_use_can_request_afterflight_recovery_after_report(setup):
    e = setup
    _, saved = await complete(e)
    resource = next(item for item in e.store.read().resources if item.source == SOURCE)
    claim = resource.id+" is unavailable"
    report = {"type": "resource_unavailability", "resource_id": resource.id,
        "resource_reference": resource.id, "report_reference": claim}
    service, _, body = spoken(e, claim+". Use the saved after-flight transfer configuration for RO321 G1, maximum 7 calls", report=report)
    receipt = await service.command(body)
    queued(e, receipt, saved)
    assert receipt["applied"] and len(e.source.calls) == 2


@pytest.mark.asyncio
async def test_negative_airport_inability_does_not_negate_separate_saved_reuse_clause(setup):
    e = setup
    _, saved = await complete(e)
    claim = "CRA nu mai permite aterizarea"
    report = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "CRA", "airport_reference": "CRA",
        "operations": ["arrivals"], "action": "restrict", "report_reference": claim}
    service, _, body = spoken(e, claim+". Folosește configurația salvată de transfer după zbor pentru RO321 G1, maximum 7 apeluri", report=report)
    response = await service.command(body)
    queued(e, response, saved)
    assert response["applied"] and len(e.source.calls) == 2
