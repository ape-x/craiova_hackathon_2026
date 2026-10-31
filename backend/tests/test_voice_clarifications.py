"""Clarification copy matches the unchanged strict scoped-command contract."""
import pytest

from app.hotel_recovery_profile import read_hotel_recovery_profile
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup, request


BAD = "Recalculate RO321 on 31 or 1 November 2026 for G1 using saved hotel settings with maximum eleven calls"
CORRECTED = "Recalculate RO321 on 2026-11-01 for G1 using saved hotel settings with maximum eleven calls"


@pytest.mark.asyncio
async def test_complete_date_correction_keeps_scope_and_cap_without_replaying_bad_command(hotel_command_setup):
    _, store, service, jobs, ai, inputs = hotel_command_setup
    before = store.read()
    bad_request = request(store, BAD, idempotency_key="ambiguous-voice-request")
    clarification = await service.command(bad_request)
    text = clarification["explanation"]
    for essential in ("comanda completă corectată", "ID-ul exact", "YYYY-MM-DD",
                      "grupul/grupurile", "serviciile cerute", "limita totală explicită",
                      "pentru noua cerere", "Nu am pornit căutarea", "1 apel"):
        assert essential in text
    assert clarification["applied"] is False
    assert clarification["command_interpretation_calls"] == 1
    assert jobs.list("RC-ONE")["jobs"] == []
    assert store.read().version == before.version
    # An exact retry returns the earlier rejection without another interpretation.
    assert (await service.command(bad_request))["replayed"] is True
    assert len(ai.calls) == 1

    result = await service.command(request(store, CORRECTED, idempotency_key="corrected-voice-request",
        context=f"Previous request: {BAD}\nVerified reply: {text}"))
    assert result["recovery_total_call_limit"] == 11
    assert result["command_interpretation_calls"] == 1
    assert result["recovery_job"]["incident_id"] == "RC-ONE"
    assert result["recovery_job"]["group_ids"] == ["G1"]
    assert result["recovery_job"]["max_provider_calls"] == 10
    assert result["recovery_job"]["calls_dispatched"] == 0
    assert result["recovery_job"]["status"] == "queued"
    assert [call[0] for call in ai.calls] == [BAD, CORRECTED]
    private = jobs.ledger.get(result["recovery_job"]["id"], private=True)
    assert private["_request"]["instructions"] == CORRECTED
    assert private["_request"]["hotel_recovery"] == inputs.model_dump(mode="json")
    after = store.read()
    assert after.version == before.version
    assert read_hotel_recovery_profile(after, after.groups[0]) == inputs.model_dump(mode="json")


@pytest.mark.parametrize("correction,expected", [
    ("The correct date is 2026-11-01", "Specifică explicit"),
    ("RO321 on 2026-11-01 with maximum eleven calls", "Numește explicit grupurile"),
    ("Recalculate RO321 on 2026-11-01 for G1 using saved hotel settings", "Limita cerută este 8"),
])
@pytest.mark.asyncio
async def test_clarification_history_never_supplies_missing_scope_or_new_call_permission(hotel_command_setup, correction, expected):
    _, store, service, jobs, ai, _ = hotel_command_setup
    first = await service.command(request(store, BAD, idempotency_key="bad-date-before-correction"))
    corrected = await service.command(request(store, correction, idempotency_key="incomplete-date-correction",
        context=f"Previous request: {BAD}\nVerified reply: {first['explanation']}"))
    assert corrected["applied"] is False and expected in corrected["explanation"]
    assert corrected["command_interpretation_calls"] == 1
    assert jobs.list("RC-ONE")["jobs"] == []
    assert [call[0] for call in ai.calls] == [BAD, correction]
