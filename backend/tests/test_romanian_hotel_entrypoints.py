"""Typed report routing and spoken guards, isolated SQLite and mocked models only."""
from datetime import timedelta

import pytest

from app.airport_restrictions import AirportRestrictions
from app.operational_reports import ArrivalRecoveryOptions, OperationalReports
from app.store import DomainError, utcnow
from app.live_service import ExternalConfirmation, LiveOperations
from test_airport_restriction_reports import report, stable
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup, intent, request
from test_live_operational_commands import outage, set_report
from test_operational_reports import arrival, reports
from test_recovery_jobs import setup
from test_romanian_hotel_recovery import romania, request as package_request, run_next, fixture_source_terms


def package_inputs():
    checkin = (utcnow() + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    return {"source": "transfero_direct_aeroport", "hotel_id": "lpce923",
            "checkin_at": checkin.isoformat(), "checkout_at": (checkin + timedelta(hours=16)).isoformat(),
            "rooms": 1, "minimum_rest_minutes": 480, "suitcases": 1, "handbags": 1,
            "standard_baggage_confirmed": True, "airport_processing_minutes": 180,
            "hotel_connection_minutes": 20}


@pytest.mark.parametrize("wording", ["Transfero", "Direct Aeroport", "Hotel Aro Palace", "hotel în Brașov", "Romanian coach + hotel"])
@pytest.mark.asyncio
async def test_explicit_romanian_package_never_reuses_valid_uk_profile(hotel_command_setup, wording):
    _, store, service, jobs, ai, _ = hotel_command_setup
    before = stable(store)
    result = await service.command(request(store,
        f"Recover RO321 on 2026-11-01 for G1 using saved hotel settings with {wording}, maximum eleven calls"))
    assert result["applied"] is False and result["command_interpretation_calls"] == 1
    assert "Romanian coach + hotel" in result["explanation"]
    assert not jobs.list("RC-ONE")["jobs"] and len(ai.calls) == 1
    assert stable(store) == before


@pytest.mark.asyncio
async def test_omitted_romanian_intent_cannot_fall_through_to_global_strategy(hotel_command_setup):
    _, store, service, jobs, ai, _ = hotel_command_setup
    ai.result.update(recovery=None, strategies=["balanced"])
    before = stable(store)
    result = await service.command(request(store, "Plan Transfero and Hotel Aro Palace with maximum eleven calls"))
    assert not result["applied"] and "Romanian coach + hotel" in result["explanation"]
    assert stable(store) == before and not jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_valid_report_survives_unsupported_spoken_package_without_uk_substitution(hotel_command_setup, reports):
    _, store, service, jobs, ai, _ = hotel_command_setup
    set_report(ai, outage(), intent(group_ids=["G1"], all_linked_groups=False,
        modes=["flight", "hotel", "bus"], max_total_calls=11, call_limit_reference="maximum eleven calls"))
    before = store.read()
    req = request(store, "Report Q1 is unavailable. Recover RO321 on 2026-11-01 for G1 using Transfero and Hotel Aro Palace with maximum eleven calls")
    result = await service.command(req)
    assert result["applied"] and result["recovery_blocked"]["code"] == "romanian_hotel_setup_required"
    assert not jobs.list("RC-ONE")["jobs"] and len(ai.calls) == 1
    assert store.read().version == before.version + 1
    assert not store.read().resources[0].available
    again = await service.command(req)
    assert again["replayed"] and again["report"] == result["report"] and len(ai.calls) == 1


def test_absent_new_package_preserves_legacy_report_payload_fingerprints():
    omitted = ArrivalRecoveryOptions().model_dump(mode="json")
    explicit_null = ArrivalRecoveryOptions(romanian_hotel_recovery=None).model_dump(mode="json")
    assert omitted == explicit_null and "romanian_hotel_recovery" not in omitted


@pytest.mark.parametrize("instruction", ["Use Transfero and Hotel Aro Palace", "Folosește pachetul Direct Aeroport"])
@pytest.mark.asyncio
async def test_report_reference_cannot_hide_separate_romanian_selection(hotel_command_setup, reports, instruction):
    _, store, service, jobs, ai, _ = hotel_command_setup
    claim = "Q1 is unavailable. " + instruction
    set_report(ai, outage(reference=claim), intent(group_ids=["G1"], all_linked_groups=False,
        modes=["flight", "hotel", "bus"], max_total_calls=11, call_limit_reference="maximum eleven calls"))
    before = stable(store)
    result = await service.command(request(store,
        claim + ". Recalculate RO321 on 2026-11-01 for G1 using saved hotel settings, maximum eleven calls"))
    assert not result["applied"] and "recovery_job" not in result
    assert not jobs.list("RC-ONE")["jobs"] and stable(store) == before and len(ai.calls) == 1


@pytest.mark.asyncio
async def test_affected_service_source_name_is_not_a_new_package_instruction(hotel_command_setup, reports):
    _, store, service, jobs, ai, inputs = hotel_command_setup
    claim = "Transfero service Q1 is unavailable"
    set_report(ai, outage(reference=claim), intent(group_ids=["G1"], all_linked_groups=False,
        modes=["flight", "hotel", "bus"], max_total_calls=11, call_limit_reference="maximum eleven calls"))
    result = await service.command(request(store,
        claim + ". Recalculate RO321 on 2026-11-01 for G1 using saved hotel settings, maximum eleven calls"))
    assert result["applied"] and result["recovery_job"]["status"] == "queued"
    saved = jobs.ledger.get(result["recovery_job"]["id"], private=True)["_request"]
    assert saved["hotel_recovery"] == inputs.model_dump(mode="json")
    assert not saved.get("romanian_hotel_recovery") and result["recovery_job"]["calls_dispatched"] == 0


@pytest.mark.asyncio
async def test_planning_receipt_cannot_be_confirmed_as_a_transfero_booking(romania):
    store, jobs, *_ = romania
    jobs.start("CASE", package_request(romania))
    result = await run_next(jobs)
    assert result["status"] == "completed"
    world = store.read()
    assert world.groups[0].source != "synthetic"
    offer = next(item for item in world.offers if item.id == result["result"]["offer_ids"][0])
    before = stable(store)
    request = ExternalConfirmation(expected_version=world.version, idempotency_key="offline-no-coach-booking",
        bookings=[{"resource_id": leg.resource_id, "confirmation_reference": "OFFLINE-REFERENCE", "confirmed_by": "Offline operator fixture",
                   "confirmed_at": utcnow().isoformat()} for leg in offer.itinerary.legs])
    with pytest.raises(DomainError) as error:
        LiveOperations(store, jobs.service, None).confirm(offer.id, request)
    assert error.value.status == 409 and error.value.details["code"] == "transfero_booking_not_supported"
    assert stable(store) == before


@pytest.mark.parametrize("endpoint", ["arrival", "restriction"])
@pytest.mark.parametrize("status", [422, 409])
def test_typed_package_reaches_post_report_preflight_and_only_known_failures_keep_report(setup, monkeypatch, endpoint, status):
    _, store, jobs, _, travel = setup
    options = {"modes": ["flight", "hotel", "bus"], "max_provider_calls": 8,
               "romanian_hotel_recovery": package_inputs()}
    called = []
    def reject(world, supplied):
        called.append(supplied.romanian_hotel_recovery.model_dump(mode="json"))
        assert supplied.hotel_recovery is None and supplied.max_provider_calls == 8
        raise DomainError(status, "Exact Romanian stay requires review")
    before = stable(store)
    if endpoint == "restriction":
        monkeypatch.setattr("app.airport_restrictions.validate_romanian_hotel_job", reject)
        req = report(store, airport_code="LHR", recovery={"group_ids": ["G1"], **options})
        service = AirportRestrictions(store, jobs)
        submit = lambda: service.report("CASE", req)
    else:
        monkeypatch.setattr("app.operational_reports.validate_romanian_hotel_job", reject)
        req = arrival(store, airport_code="OTP", group_ids=["G1"], recovery=options)
        service = OperationalReports(store, jobs)
        submit = lambda: service.arrival("CASE", req)
    if status == 422:
        result = submit()
        assert result["applied"] and result["recovery_blocked"]["code"] == "romanian_hotel_setup_required"
        assert "recovery_job" not in result
        again = submit()
        assert again["replayed"] and again["report"] == result["report"]
        assert store.read().version == before["version"] + 1
    else:
        with pytest.raises(DomainError) as error:
            submit()
        assert error.value.status == 409 and stable(store) == before
    assert len(called) == 1 and not travel.calls and not jobs.list("CASE")["jobs"]
