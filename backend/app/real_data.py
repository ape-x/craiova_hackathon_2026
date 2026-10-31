"""Read-only real-world lookup contracts, independent of operational inventory."""
from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .hotel_location import coordinate_timezone, property_zone
from .providers.base import ProviderError
from .providers.real_data import RealDataProvider


def local_today() -> date:
    return datetime.now(ZoneInfo("Europe/Bucharest")).date()


class RealDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    airport_code: str = Field(min_length=3, max_length=4)
    destination_code: str = Field(min_length=3, max_length=4)
    checkin: date
    checkout: date
    adults: int = Field(default=2, ge=1, le=30, strict=True)
    rooms: int = Field(default=1, ge=1, le=30, strict=True)

    @field_validator("airport_code", "destination_code")
    @classmethod
    def known_airport(cls, value: str) -> str:
        airport = lookup_airport(value)
        if airport is None:
            raise ValueError("Select a known IATA or ICAO airport code")
        return airport["code"]

    @model_validator(mode="after")
    def valid_trip(self):
        airport = lookup_airport(self.airport_code)
        today = datetime.now(property_zone(coordinate_timezone(airport["lat"], airport["lon"]))).date()
        if self.airport_code == self.destination_code:
            raise ValueError("Origin and destination must be different airports")
        if self.checkin < today or self.checkout > today + timedelta(days=500):
            raise ValueError("Stay dates must be today or later and within 500 days")
        if not 1 <= (self.checkout - self.checkin).days <= 30:
            raise ValueError("Choose a stay between 1 and 30 nights")
        if self.rooms > self.adults:
            raise ValueError("Each room requires at least one adult")
        return self


def _airport(code: str) -> dict:
    item = lookup_airport(code)
    if item is None:
        raise ValueError("Unknown airport")
    return {"code": item["code"], "name": item["name"], "latitude": item["lat"], "longitude": item["lon"]}


class RealDataService:
    """Bounded source caches. Google content is fetched without caching."""

    def __init__(self, settings: Any, *, client=None, provider=None):
        self.provider = provider or RealDataProvider(settings, client=client)
        self._cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
        self._locks = {name: asyncio.Lock() for name in ("hotels", "transport", "hotel_offers")}

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def _section(self, name: str, source: str, key: tuple, ttl: int, loader: Callable[[], Awaitable[list]]) -> dict:
        async with self._locks[name]:
            now = time.monotonic()
            cached = self._cache.get(key) if ttl > 0 else None
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return {**copy.deepcopy(cached[1]), "cached": True}
            self._cache.pop(key, None)
            try:
                items = await loader()
            except ProviderError as exc:
                if exc.code == "sandbox":
                    return {"state": "sandbox", "mode": "sandbox", "source": source, "fetched_at": None, "cached": False, "message": str(exc), "items": []}
                return {"state": "error", "source": source, "fetched_at": None, "cached": False, "message": str(exc), "items": []}
            result = {"state": "ready", "source": source, "fetched_at": datetime.now(ZoneInfo("UTC")).isoformat(), "cached": False, "message": None, "items": items}
            if name == "hotel_offers" and any(item.get("pricing", {}).get("status") == "incomplete" for item in items):
                result["message"] = "Some supplier quotes have unresolved mandatory costs. They remain visible but cannot be imported as fully budgeted options."
            if ttl > 0:
                self._cache[key] = (time.monotonic() + ttl, copy.deepcopy(result))
                while len(self._cache) > 64:
                    self._cache.popitem(last=False)
            return result

    async def search(self, request: RealDataRequest) -> dict:
        # Revalidate time-relative rules on each invocation, including long-lived objects.
        request = RealDataRequest.model_validate(request.model_dump())
        airport, destination = _airport(request.airport_code), _airport(request.destination_code)
        hotels_source = "Google Places" if self.provider.google_configured else "OpenStreetMap"
        route_source = "Google Routes" if self.provider.google_configured else "OSRM / OpenStreetMap"
        # Google Places/Routes content is exempted from these reference caches.
        # Only OSM-derived data and short-lived Booking quotes are retained.
        hotel_ttl = 0 if self.provider.google_configured else 3600
        route_ttl = 0 if self.provider.google_configured else 300
        hotels_task = self._section("hotels", hotels_source, ("hotels", airport["code"]), hotel_ttl, lambda: self.provider.hotels(airport))
        route_task = self._section("transport", route_source, ("transport", airport["code"], destination["code"]), route_ttl, lambda: self.provider.route(airport, destination))
        selected = self.provider.hotel_offer_provider
        source = self.provider.hotel_offer_source
        selected_has_credentials = self.provider.liteapi_configured if selected == "liteapi" else self.provider.booking_configured if selected == "booking" else False
        if selected_has_credentials:
            offers_task = self._section("hotel_offers", source, ("offers", selected, *request.model_dump(mode="json").values()), 60, lambda: self.provider.offers(airport, request))
        else:
            async def unavailable():
                return {"state": "not_configured", "source": source, "fetched_at": None, "cached": False, "message": "Connect a live hotel-rate provider to view room availability and prices.", "items": []}
            offers_task = unavailable()
        hotels, transport, offers = await asyncio.gather(hotels_task, route_task, offers_task)
        return {"airport": airport, "destination": destination, "criteria": request.model_dump(mode="json"), "hotels": hotels, "transport": transport, "hotel_offers": offers}
