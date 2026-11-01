"""Omitted airport/aircraft facts cannot launch a recovery on unchanged facts."""
import pytest

from app.live_operational_commands import _unsupported_airport_change
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup, intent, request
from test_live_operational_commands import arrival, assert_no_report, operational_setup, outage, set_report
from test_operational_reports import reports


@pytest.mark.parametrize("claim", [
    "OTP is closed", "OTP este închis", "The airport is closed", "Heathrow Airport closure",
    "RO321 cannot land at OTP", "RO321 can't land at OTP", "RO321 can not land at OTP",
    "Avionul RO321 nu poate ateriza la OTP", "RO321 nu mai poate să aterizeze la OTP",
    "RO321 diverted to CRA", "RO321 is diverting to CRA", "Avionul RO321 deviază la CRA",
    "Avionul RO321 este deviat la CRA", "Pista este închisă",
])
@pytest.mark.asyncio
async def test_omitted_unsupported_constraint_blocks_recovery_and_preserves_world(operational_setup, claim):
    _, store, service, jobs, ai, _ = operational_setup
    set_report(ai, None, intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    req = request(store, claim + ". Recover RO321 for G1 with maximum 4 calls")
    result = await service.command(req)
    assert result["applied"] is False and "nu este reprezentată complet" in result["explanation"]
    assert result["command_interpretation_calls"] == 1
    assert "recovery_job" not in result
    assert_no_report(store, before, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and len(ai.calls) == 1


@pytest.mark.parametrize("additional", [
    "OTP is closed", "Divert RO321 to CRA", "RO321 is diverting to CRA", "RO321 cannot land at OTP",
    "OTP is unavailable", "OTP este indisponibil", "Aeroportul OTP este indisponibil",
    "London Heathrow Airport is unavailable",
])
@pytest.mark.asyncio
async def test_kept_arrival_with_dropped_airport_change_does_not_apply_partial_command(operational_setup, additional):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    set_report(ai, report, intent(group_ids=["G1"], all_linked_groups=False))
    before = store.read()
    result = await service.command(request(store,
        f"For RO321: {report['report_reference']}. {additional}. Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is False and "nu este reprezentată complet" in result["explanation"]
    assert_no_report(store, before, jobs)


@pytest.mark.parametrize("background", ["After the diversion,", "După deviere,", "RO321 was diverted. After the diversion,"])
@pytest.mark.asyncio
async def test_explicit_passenger_readiness_after_described_diversion_still_records_and_queues(operational_setup, background):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    set_report(ai, report, intent(group_ids=["G1"], all_linked_groups=False, modes=["flight"]))
    result = await service.command(request(store,
        f"For RO321: {background} {report['report_reference']}. Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is True, result
    assert result["report"]["airport_code"] == "SBZ"
    assert result["recovery_job"]["group_ids"] == ["G1"]
    assert result["recovery_job"]["calls_dispatched"] == 0


@pytest.mark.parametrize("claim", ["Bus Q1 at LHR is unavailable", "Autobuzul Q1 la LHR este indisponibil"])
@pytest.mark.asyncio
async def test_service_outage_at_airport_does_not_become_an_airport_change(operational_setup, claim):
    _, store, service, _, ai, _ = operational_setup
    set_report(ai, outage(reference=claim), intent(group_ids=["G1"], all_linked_groups=False, modes=["flight"]))
    result = await service.command(request(store, f"{claim}. Recover RO321 for G1 with maximum 4 calls"))
    assert result["applied"] is True, result
    assert result["report"]["type"] == "resource_unavailability"
    assert result["recovery_job"]["group_ids"] == ["G1"]


@pytest.mark.parametrize("text", [
    "G1 at SBZ ready in 30 minutes", "G1 is ready at London Heathrow Airport in 30 minutes",
    "Pasagerii G1 sunt pregătiți la Heathrow peste treizeci de minute",
    "The ticket office is closed. G1 at SBZ ready in 30 minutes",
    "Q1 is unavailable. Recover RO321 for G1", "Search RO321 for G1, lowest cost",
    "Bus Q1 at LHR is unavailable", "Autocarul Q1 la OTP este indisponibil",
    "Hotel H1 near Heathrow is unavailable", "Hotelul H1 la OTP este indisponibil",
])
def test_supported_readiness_or_unrelated_closure_is_not_an_aircraft_change(text):
    assert not _unsupported_airport_change(text)
