"""The voice bridge cannot substitute an initial pickup for a future connection.

All command interpretation and source fixtures below are offline mocks; the
suite-level socket guard disables live traffic before app imports.
"""
from types import SimpleNamespace

import pytest

from app.airport_cab_commands import AirportTransferSetupRequired, resolve_saved_airport_transfers
from app.live_commands import normalized
from app.airport_cab_commands import _after_flight_transfer_requested
from test_airport_cab_voice import complete_typed, env, profile_clock, spoken
from test_airport_cab_recovery_jobs import stable


@pytest.mark.parametrize("text", [
    "Recover RO321 for G1 with a transfer after the flight lands at OTP",
    "Recover RO321 for G1 using the saved transfer after the incoming flight",
    "After the flight arrives at OTP, use a transfer to CRA for G1",
    "After landing at OTP, recover RO321 for G1 using a taxi to CRA",
    "Use a transfer after arrival of the flight at OTP",
    "Recover RO321 with a flight to OTP then a transfer to CRA for G1",
    "Find a flight to OTP. Then use a transfer to CRA",
    "Fly to OTP followed by a cab to CRA",
    "The flight will arrive at OTP, then take the transfer to CRA",
    "We land at OTP, then use the transfer to CRA",
    "Zbor la OTP și de acolo transfer la CRA",
    "Recuperează RO321 pentru G1 cu transfer după zbor",
    "Recuperează RO321 pentru G1 folosind transferul salvat după zborul viitor",
    "După ce aterizează avionul la OTP, folosește transferul către CRA",
    "După sosirea zborului la OTP, mergem cu mașina la CRA",
    "Caută zbor la OTP, apoi transfer la CRA pentru G1",
    "Zburăm la OTP, apoi folosim taxiul spre CRA",
    "Recuperează RO321 cu zbor la OTP urmat de transfer la CRA",
    "Use the saved transfer if the flight to OTP then transfer to CRA is feasible",
    "Do not use a transfer after the flight; recover G1",
])
def test_explicit_future_connection_cannot_fall_through_without_a_saved_keyword(text):
    world = SimpleNamespace(groups=[SimpleNamespace(id="G1", name="Passenger party")])
    with pytest.raises(AirportTransferSetupRequired, match="Flight to OTP") as error:
        resolve_saved_airport_transfers(None, world, None, ["G1"], ["flight", "bus"], text, 8)
    assert error.value.code == "airport_transfer_setup_required"


@pytest.mark.parametrize("text", [
    "Recover RO321 for G1 by flight and ground transport",
    "Use the saved hotel, both transfers and onward flight",
    "Use the saved transfer followed by a flight",
    "Change the flight provider, then use the saved transfer",
    "After checking flight prices, review the transfer",
    "Run a transfer search after the flight search",
    "After the flight provider changes, use the saved transfer",
    "After the flight results, review the transfer source",
    "Verifică transferul după căutarea zborurilor",
    "Schimbă furnizorul zborului, apoi verifică transferul",
    "Folosește transferul salvat și apoi zborul",
    "Use saved transfer inputs without bookings",
])
def test_source_changes_and_existing_initial_or_hotel_journeys_are_not_future_connections(text):
    assert not _after_flight_transfer_requested(normalized(text))


@pytest.mark.parametrize("name", ["Transfer after flight", "Zbor la OTP apoi transfer", "After landing taxi"])
def test_group_names_are_references_not_route_instructions(name):
    world = SimpleNamespace(groups=[SimpleNamespace(id="G1", name=name)])
    assert resolve_saved_airport_transfers(None, world, None, ["G1"], ["flight", "bus"],
        f"Recover RO321 for {name} with maximum 8 calls", 7) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_interpretation_omission_does_not_queue_ordinary_or_saved_initial_recovery(env, saved):
    if saved:
        await complete_typed(env)
    before, jobs, calls = stable(env.store.read()), len(env.jobs.list("CASE")["jobs"]), len(env.source.calls)
    text = "Recover RO321 for G1 with a flight to OTP then a transfer to CRA with maximum 9 calls"
    if saved:
        text = "Recover RO321 for G1 using the saved transfer after the flight lands at OTP with maximum 9 calls"
    service, ai, body = spoken(env, text, modes=["flight", "bus"], total=9)
    receipt = await service.command(body)
    assert receipt["applied"] is False and "recovery_job" not in receipt
    assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert "Flight to OTP + transfer to CRA" in receipt["explanation"]
    assert stable(env.store.read()) == before and len(env.jobs.list("CASE")["jobs"]) == jobs
    assert len(env.source.calls) == calls and len(ai.calls) == 1
    replay = await service.command(body)
    assert replay["replayed"] and replay["recovery_blocked"] == receipt["recovery_blocked"]
    assert len(ai.calls) == 1 and len(env.source.calls) == calls


@pytest.mark.asyncio
async def test_valid_operator_report_stays_recorded_when_future_connection_followup_needs_setup(env):
    claim = "OTP is closed for departures"
    report = {"type": "airport_restriction", "incident_id": "CASE", "airport_code": "OTP", "airport_reference": "OTP",
        "operations": ["departures"], "action": "restrict", "report_reference": claim}
    service, ai, body = spoken(env, claim + ". Recover RO321 for G1 with a flight to OTP then transfer to CRA with maximum 9 calls",
        modes=["flight", "bus"], report=report, total=9)
    before = env.store.read()
    receipt = await service.command(body)
    assert receipt["applied"] and "recovery_job" not in receipt, receipt
    assert receipt["recovery_blocked"]["code"] == "airport_transfer_setup_required"
    assert env.store.read().version == before.version + 1
    assert env.store.read().groups == before.groups
    assert not env.source.calls and not env.jobs.list("CASE")["jobs"] and len(ai.calls) == 1
