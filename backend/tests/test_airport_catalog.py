"""Offline geography is grounded and does not turn ambiguity into a decision."""

import json
from pathlib import Path

import pytest

from app.airport_catalog import lookup_airport, match_airports


def test_lisbon_iata_and_icao_resolve_same_source_coordinates():
    airport = lookup_airport(" lis ")
    assert airport is not None
    assert airport["id"] == airport["code"] == "LIS"
    assert airport["lat"] == pytest.approx(38.7813)
    assert airport["lon"] == pytest.approx(-9.13592)
    assert airport["country"] == "PT"
    assert lookup_airport("lppt") == airport
    assert airport["source"] == "OurAirports"
    assert airport["source_date"] == "2026-10-31"
    assert "available" not in airport and "diversion_eligible" not in airport


def test_romanian_lisbon_alias_and_embedded_code_ground_real_destination():
    assert match_airports("Redirecționează avionul spre Lisabona")[0]["code"] == "LIS"
    assert match_airports("Du avionul la LIS, fără escală")[0]["code"] == "LIS"
    assert match_airports("Du avionul la aeroportul lppt")[0]["code"] == "LIS"


@pytest.mark.parametrize("text,expected", [
    ("Închide aeroportul din Londra", {"LHR", "LGW", "STN", "LCY"}),
    ("Deviază spre Paris", {"CDG", "ORY"}),
])
def test_city_ambiguity_returns_multiple_airports(text, expected):
    assert expected <= {airport["code"] for airport in match_airports(text)}


def test_unknown_reference_never_receives_coordinates():
    assert lookup_airport("ZZZZZZ") is None
    assert lookup_airport("Lisabona") is None
    assert match_airports("aeroportul ZZZZZZ") == []
    assert match_airports("aeroportul Inventatopolis") == []


def test_ordinary_words_and_generic_airport_terms_do_not_become_codes():
    assert match_airports("mai avem un autobuz sau trebuie schimbat scenariul") == []
    assert match_airports("MAI AVEM UN AUTOBUZ SAU TREBUIE SCHIMBAT SCENARIUL") == []
    assert match_airports("închide aeroportul internațional") == []


def test_named_airport_and_diacritics_are_searchable():
    assert match_airports("Zborul merge la Heathrow")[0]["code"] == "LHR"
    assert "OTP" in {a["code"] for a in match_airports("București")}
    assert match_airports("München")[0]["code"] == "MUC"


def test_returned_records_are_copies_and_limit_is_respected():
    record = lookup_airport("LIS")
    assert record is not None
    record["lat"] = 0
    candidates = match_airports("Lisabona")
    candidates[0]["name"] = "Changed"
    assert lookup_airport("LIS")["lat"] == pytest.approx(38.7813)
    assert lookup_airport("LIS")["name"] != "Changed"
    assert len(match_airports("London", limit=2)) == 2
    assert match_airports("London", limit=0) == []


def test_snapshot_is_bounded_unique_and_geographically_valid():
    path = Path(__file__).parents[1] / "app" / "data" / "airports.json"
    payload = json.loads(path.read_text())
    records = payload["airports"]
    assert payload["metadata"]["record_count"] == len(records)
    assert 4000 < len(records) < 5000
    assert len({record["code"] for record in records}) == len(records)
    for record in records:
        assert record["type"] in {"large_airport", "medium_airport"}
        assert record["iata"] or record["icao"]
        assert -90 <= record["lat"] <= 90
        assert -180 <= record["lon"] <= 180
