"""Offline public airport geography for authoring simulation scenarios.

Geography is reference data, never a claim of live airport availability. Callers
own all simulated operational state. City searches return candidates rather
than guessing which airport the speaker meant.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


_DATA_PATH = Path(__file__).with_name("data") / "airports.json"

# These translate city names, not cities into a preferred airport. Airports in
# an ambiguous city (e.g. London, Paris) therefore remain separate candidates.
_CITY_ALIASES = {
    "lisabona": "lisbon", "lisboa": "lisbon", "londra": "london",
    "bucuresti": "bucharest", "bucurești": "bucharest",
    "viena": "vienna", "wien": "vienna", "roma": "rome",
    "munchen": "munich", "muenchen": "munich", "varsovia": "warsaw",
    "warszawa": "warsaw", "atena": "athens", "athina": "athens",
    "bruxelles": "brussels", "brussel": "brussels", "praga": "prague",
    "praha": "prague", "copenhaga": "copenhagen", "kobenhavn": "copenhagen",
    "venetia": "venice", "venezia": "venice", "florenta": "florence",
    "firenze": "florence", "napoli": "naples", "neapole": "naples",
    "milano": "milan", "genova": "genoa", "torino": "turin",
    "budapesta": "budapest", "belgrad": "belgrade", "sofia": "sofia",
    "chisinau": "chisinau", "cluj": "cluj napoca", "iasi": "iasi",
    "frankfurt pe main": "frankfurt am main", "koln": "cologne",
}

# Avoid matching generic words in airport names or ordinary Romanian speech as
# IATA codes. Embedded codes must also be uppercase, unlike exact-code lookup.
_COMMON_WORDS = frozenset("""
    airport airports international regional municipal aerodrome airfield airpark
    aeroport aeroportul aeroportului aeroporturi aeroporturile aeroporturile
    aeropoto aeroporto aeropuertos aerodromo internationala internationalul
    national military base station terminal general city county district island
    saint sainte santa santo san new north south east west port central
    the and for from with this that into pentru avem este sunt care toate
    schimbam schimba scenariu scenariul avion avionul autobuz autobuzul inchide
    inchidem redirecteaza redirectioneaza deviem aterizare aterizeaza ateriza
    mai sau lui din las cei cea cel vor voi are iar noi lor una unu fie azi
""".split())


def _normalize(value: str) -> str:
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, int]]:
    payload = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    records = tuple(payload["airports"])
    codes: dict[str, int] = {}
    for index, record in enumerate(records):
        for code in (record["iata"], record["icao"]):
            if code:
                codes[code] = index
    return payload["metadata"], records, codes


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    metadata, _, _ = _catalog()
    return {
        **record,
        "source": metadata["source"],
        "source_url": metadata["source_url"],
        "source_date": metadata["source_date"],
    }


def lookup_airport(code: str) -> dict[str, Any] | None:
    """Resolve a complete IATA/ICAO code to reference geography, or ``None``.

    Results are copies. Unknown identifiers never acquire invented coordinates.
    """
    if not isinstance(code, str):
        return None
    _, records, codes = _catalog()
    index = codes.get(code.strip().upper())
    return _public_record(records[index]) if index is not None else None


def nearby_airports(code: str, limit: int = 3, radius_km: float = 1500) -> list[dict[str, Any]]:
    """Ground possible demo alternates in nearby major-airport geography.

    Great-circle distance is only a retrieval aid, never aircraft range, runway
    suitability, weather, permission to land or actual operating availability.
    The decision layer must evaluate the authored operational constraints.
    """
    origin = lookup_airport(code)
    if origin is None or limit <= 0 or not math.isfinite(radius_km) or radius_km <= 0:
        return []
    lat, lon = math.radians(origin["lat"]), math.radians(origin["lon"])
    _, records, _ = _catalog()
    matches = []
    for record in records:
        if record["id"] == origin["id"] or record["type"] != "large_airport" or not record["scheduled_service"]:
            continue
        other_lat, other_lon = math.radians(record["lat"]), math.radians(record["lon"])
        haversine = math.sin((other_lat-lat)/2)**2 + math.cos(lat)*math.cos(other_lat)*math.sin((other_lon-lon)/2)**2
        distance = 6371 * 2 * math.asin(math.sqrt(min(1, max(0, haversine))))
        if distance <= radius_km:
            matches.append((distance, record))
    matches.sort(key=lambda item: (item[0], item[1]["id"]))
    return [{**_public_record(record), "distance_km": round(distance, 1)} for distance, record in matches[:min(limit, 6)]]


@lru_cache(maxsize=1)
def _search_records() -> tuple[tuple[str, frozenset[str]], ...]:
    _, records, _ = _catalog()
    result = []
    for record in records:
        # Dataset municipality suffixes identify suburbs/county, not a second
        # city: Paris (Orly, Val-de-Marne) and London, Essex match their city.
        city = _normalize(re.split(r"[,(]", record["municipality"], maxsplit=1)[0])
        distinctive = frozenset(
            token for token in _normalize(record["name"]).split()
            if len(token) >= 5 and token not in _COMMON_WORDS
        )
        result.append((city, distinctive))
    return tuple(result)


def match_airports(text: str, limit: int = 12) -> list[dict[str, Any]]:
    """Find grounded airport candidates in a spoken or typed scenario.

    Exact IATA/ICAO references rank first. City and airport-name matches preserve
    ambiguity (London does not silently become Heathrow). This is a candidate
    search, not a decision about which airport to add to the simulation.
    """
    if not isinstance(text, str) or not text.strip() or limit <= 0:
        return []
    text = text[:8000]
    limit = min(limit, 50)
    exact = lookup_airport(text)
    if exact is not None:
        return [exact]
    normalized = _normalize(text)
    expanded = normalized
    for alias, canonical in _CITY_ALIASES.items():
        if _contains_phrase(normalized, _normalize(alias)):
            expanded += " " + canonical
    tokens = frozenset(expanded.split()) - _COMMON_WORDS
    explicit_codes = {
        token for token in re.findall(r"\b[A-Z]{3,4}\b", text)
        if token.casefold() not in _COMMON_WORDS
    }
    # Lowercase codes are safe when explicitly introduced as airport/code IDs.
    explicit_codes.update(
        code.upper() for code in re.findall(
            r"\b(?:iata|icao|airport|aeroport(?:ul|ului)?)\s+([a-zA-Z]{3,4})\b",
            text, flags=re.IGNORECASE,
        ) if code.casefold() not in _COMMON_WORDS
    )
    _, records, _ = _catalog()
    matches = []
    for record, (city, distinctive) in zip(records, _search_records(), strict=True):
        explicit = record["iata"] in explicit_codes or record["icao"] in explicit_codes
        city_match = len(city) >= 3 and city not in _COMMON_WORDS and _contains_phrase(expanded, city)
        name_hits = len(tokens & distinctive)
        if not explicit and not city_match and not name_hits:
            continue
        score = (int(explicit), int(city_match), name_hits,
                 int(record["type"] == "large_airport"), int(record["scheduled_service"]))
        matches.append((score, record))
    matches.sort(key=lambda match: (*(-n for n in match[0]), match[1]["code"]))
    return [_public_record(record) for _, record in matches[:limit]]
