"""Literal airport-name grounding with no voice, model or network calls."""
import pytest

from app.airport_catalog import match_airports
from app.airport_mentions import MAX_MENTION_TEXT, literal_airport_ids
from app.live_operational_commands import Clarification, _airport
from test_hotel_recovery_profile import hotel_command_setup
from test_live_operational_commands import arrival, operational_setup, set_report
from test_live_commands import command_setup, request
from test_operational_reports import reports


@pytest.mark.parametrize("reference,code", [
    ("London Heathrow Airport", "LHR"),
    ("London Heathrow", "LHR"),
    ("Londra Heathrow", "LHR"),
    ("aeroportul Londra Heathrow", "LHR"),
    ("Heathrow", "LHR"),
    ("aeroportul Heathrow", "LHR"),
    ("Bucharest Henri Coandă International Airport", "OTP"),
    ("Bucharest Henri Coanda", "OTP"),
    ("București Henri Coandă", "OTP"),
    ("BUCURESTI HENRI COANDA INTERNATIONAL AIRPORT", "OTP"),
    ("Henri Coandă International Airport", "OTP"),
    ("Aeroportul Otopeni", "OTP"),
    ("LHR", "LHR"),
    ("lhr", "LHR"),
    ("EGLL", "LHR"),
    ("aeroportul egll", "LHR"),
    ("London Heathrow Airport (LHR)", "LHR"),
    ("Le Havre-Octeville Airport", "LEH"),
    ("General Juan N. Álvarez International Airport", "ACA"),
    ("L.F. Wade International Airport", "BDA"),
    ("M. R. Štefánik Airport", "BTS"),
    ("Albacete Airport / Los Llanos Air Base", "ABC"),
])
def test_literal_full_names_aliases_and_codes_resolve_without_a_city_preference(reference, code):
    assert literal_airport_ids(reference) == {code}
    assert _airport(f"G1 at {reference} ready in thirty minutes", code, reference) == code


@pytest.mark.parametrize("claim,reference", [
    ("G1 at London Heathrow Airport ready in thirty minutes", "Heathrow"),
    ("G1 at Londra Heathrow ready in thirty minutes", "Heathrow"),
    ("G1 at Bucharest Henri Coandă International Airport ready in thirty minutes", "Henri Coandă"),
    ("G1 at London Heathrow Airport (LHR) ready in thirty minutes", "LHR"),
])
def test_short_literal_quote_does_not_turn_its_full_name_into_another_city_mention(claim, reference):
    code = "OTP" if "Coandă" in claim else "LHR"
    assert _airport(claim, code, reference) == code


@pytest.mark.parametrize("reference,code", [
    ("London", "LHR"),
    ("Londra", "LHR"),
    ("Bucharest", "OTP"),
    ("Bathurst Airport", "BHS"),
    ("Newcastle Airport", "NTL"),
    ("London\nHeathrow", "LHR"),
    ("London; Heathrow", "LHR"),
    ("London. Heathrow", "LHR"),
    ("London: Heathrow", "LHR"),
    ("London/Heathrow", "LHR"),
    ("London | Heathrow", "LHR"),
    ("Londra Hethrow", "LHR"),
    ("Heathroww", "LHR"),
    ("Le Havre", "LHR"),
    ("Le Havre-Octeville Airport", "LHR"),
])
def test_ambiguity_collisions_and_wrong_speech_are_not_repaired(reference, code):
    with pytest.raises(Clarification):
        _airport(f"G1 at {reference} ready in thirty minutes", code, reference)


def test_duplicate_full_names_retain_all_catalog_matches():
    assert literal_airport_ids("Bathurst Airport") == {"BHS", "ZBF"}
    assert literal_airport_ids("Newcastle Airport") == {"NCS", "NTL"}


@pytest.mark.parametrize("other", [
    "Gatwick", "London Gatwick Airport", "Londra Gatwick", "LGW", "aeroportul egkk", "Paris",
])
def test_separate_named_city_or_code_mention_is_not_hidden_by_longer_heathrow_name(other):
    claim = f"G1 at London Heathrow Airport and {other} ready in thirty minutes"
    with pytest.raises(Clarification, match="mai multe aeroporturi"):
        _airport(claim, "LHR", "London Heathrow Airport")


@pytest.mark.parametrize("airports,quote", [
    ("lhr and lgw", "lhr"),
    ("lgw or lhr", "lhr"),
    ("lhr/egkk", "lhr"),
    ("egkk | lhr", "lhr"),
    ("lhr, EGKK and stn", "lhr"),
    ("lhr și lgw", "lhr"),
    ("London Heathrow Airport and lgw", "Heathrow"),
    ("egkk or London Heathrow Airport", "Heathrow"),
    ("London Heathrow Airport și aeroportul lgw", "Heathrow"),
    ("lhr and airport can", "lhr"),
])
def test_lowercase_code_alternatives_next_to_literal_reference_require_clarification(airports, quote):
    with pytest.raises(Clarification, match="mai multe aeroporturi"):
        _airport(f"G1 at {airports} ready in thirty minutes", "LHR", quote)


def test_repeated_equivalent_lowercase_codes_remain_the_same_airport():
    assert _airport("G1 at lhr / egll ready in thirty minutes", "LHR", "lhr") == "LHR"


@pytest.mark.parametrize("claim", [
    "G1 at lhr ready in thirty minutes, with bus and car transport requested",
    "G1 at lhr and can be ready in thirty minutes",
    "G1 at London Heathrow Airport, bus and car requested, ready in thirty minutes",
])
def test_airport_code_alternative_detection_does_not_scan_unrelated_lowercase_prose(claim):
    quote = "Heathrow" if "Heathrow" in claim else "lhr"
    assert _airport(claim, "LHR", quote) == "LHR"


@pytest.mark.parametrize("claim,quote", [
    ("G1 at Le Havre ready in thirty minutes", "Heathrow"),
    ("G1 at Le Havre ready in thirty minutes", "LHR"),
    ("G1 at London Heathrow ready in thirty minutes", "London Heathrow Airport"),
    ("G1 at Henri Coandă ready in thirty minutes", "Henri Coanda"),
])
def test_model_reference_must_still_occur_literally_in_the_report(claim, quote):
    with pytest.raises(Clarification, match="exact"):
        _airport(claim, "LHR", quote)


def test_literal_name_resolution_leaves_general_ranked_city_search_unchanged():
    assert {"LHR", "LGW", "STN", "LCY"} <= {item["id"] for item in match_airports("London Heathrow Airport")}
    assert {"OTP", "BBU"} <= {item["id"] for item in match_airports("Bucharest Henri Coandă International Airport")}


def test_oversized_report_cannot_hide_a_second_airport_beyond_a_truncation_boundary():
    with pytest.raises(ValueError):
        literal_airport_ids("London Heathrow Airport " + " " * MAX_MENTION_TEXT + "Gatwick")


@pytest.mark.asyncio
@pytest.mark.parametrize("reference,code", [
    ("London Heathrow Airport", "LHR"),
    ("Londra Heathrow", "LHR"),
    ("Bucharest Henri Coandă International Airport", "OTP"),
])
async def test_natural_full_name_reaches_report_transaction_and_replays_once(operational_setup, reference, code):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    claim = f"Assume G1 at {reference} ready at {report['ready_at']}"
    report.update(airport_code=code, airport_reference=reference, report_reference=claim)
    set_report(ai, report)
    before = store.read()
    command = request(store, "For RO321: " + claim)
    result = await service.command(command)
    assert result["applied"] is True and result["report"]["airport_code"] == code
    assert store.read().groups[0].origin == code
    assert store.read().groups[1:] == before.groups[1:]
    assert store.read().resources == before.resources
    assert jobs.list("RC-ONE")["jobs"] == []
    replay = await service.command(command)
    assert replay["replayed"] and replay["report"] == result["report"]
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_second_airport_in_report_cannot_be_omitted_by_interpretation(operational_setup):
    _, store, service, jobs, ai, _ = operational_setup
    report = arrival(store)
    claim = f"Assume G1 at London Heathrow Airport and London Gatwick Airport ready at {report['ready_at']}"
    report.update(airport_code="LHR", airport_reference="Heathrow", report_reference=claim)
    set_report(ai, report)
    before = store.read().model_dump(exclude={"sim_time"})
    result = await service.command(request(store, "For RO321: " + claim))
    assert result["applied"] is False and "report" not in result
    assert store.read().model_dump(exclude={"sim_time"}) == before
    assert jobs.list("RC-ONE")["jobs"] == []
