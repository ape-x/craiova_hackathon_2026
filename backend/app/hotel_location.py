"""Supplier property geography, with a coordinate-derived IANA time zone.

The offline boundary dataset is pinned independently of the lookup library.
It is reference geography, not evidence of a shuttle or hotel availability.
"""
from __future__ import annotations

import hashlib
import math
from decimal import Decimal
from functools import lru_cache
from importlib.metadata import version
from importlib.resources import files
from zoneinfo import ZoneInfo

from timezonefinder import TimezoneFinder

from .models import Airport


@lru_cache(maxsize=1)
def _finder():
    return TimezoneFinder(in_memory=True)


@lru_cache(maxsize=128)
def property_zone(zone_id: str) -> ZoneInfo:
    # Use the pinned IANA rules, including on hosts with older system tzdata.
    with files("tzdata.zoneinfo").joinpath(*zone_id.split("/")).open("rb") as handle:
        return ZoneInfo.from_file(handle, key=zone_id)


def coordinate_timezone(latitude, longitude) -> str:
    """Resolve source land coordinates; never guess a country or nearby airport."""
    for value, bound in ((latitude, 90), (longitude, 180)):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) or not math.isfinite(float(value)) or not -bound <= value <= bound:
            raise ValueError("Missing or invalid source coordinates")
    latitude, longitude = float(latitude), float(longitude)
    zone_id = _finder().timezone_at(lat=latitude, lng=longitude)
    if not zone_id or zone_id.startswith("Etc/"):
        raise ValueError("Source land time zone could not be resolved")
    property_zone(zone_id)
    return zone_id


def property_location(quote: dict) -> tuple[Airport, dict]:
    hotel_id, name = quote.get("id"), quote.get("name")
    if not isinstance(hotel_id, str) or not hotel_id.strip() or not isinstance(name, str) or not name.strip():
        raise ValueError("Missing supplier property identity")
    zone_id = coordinate_timezone(quote.get("latitude"), quote.get("longitude"))
    latitude, longitude = float(quote["latitude"]), float(quote["longitude"])
    node_id = "HP-" + hashlib.sha256(("liteapi|" + hotel_id).encode()).hexdigest()[:24].upper()
    node = Airport(id=node_id, code=node_id, name=name[:140], lat=latitude, lon=longitude,
                   kind="hotel", diversion_eligible=False, operational_status="unknown")
    context = {
        "node_id": node.id, "hotel_id": hotel_id, "latitude": latitude, "longitude": longitude,
        "source": "Nuitee / LiteAPI", "timezone": zone_id,
        "timezone_source": "timezone-boundary-builder / OpenStreetMap contributors",
        "timezone_source_url": "https://github.com/evansiroky/timezone-boundary-builder",
        "timezone_dataset": "timezonefinder-data " + version("timezonefinder-data"),
        "timezone_rules": "IANA tzdata " + version("tzdata"),
    }
    return node, context
