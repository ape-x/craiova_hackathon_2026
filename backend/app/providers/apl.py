"""Registered APL transfer quotes only; no booking, payment or driver methods.

Official contract: https://mcp.airport-pickups-london.com/docs
GBP is preserved. A quote is neither a vehicle hold nor confirmed transport.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import ProviderError, secret_value
from .real_data import RealDataProvider


SOURCE = "Airport Pickups London"
QUOTE_URL = "https://mcp.airport-pickups-london.com/api/quote"
DOCS_URL = "https://mcp.airport-pickups-london.com/docs"
LONDON = ZoneInfo("Europe/London")


def _text(value, maximum=2000, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError("Invalid transfer text")
    return value.strip()


def _meeting_point(value):
    """Supplier pickup instructions only; not a driver or flight terminal."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid meeting instructions")
    name = _text(value.get("name"), 500)
    instructions = value.get("instructions")
    if instructions is not None:
        if (not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 4096
                or any(ord(character) < 32 and character not in "\n\r\t" for character in instructions)):
            raise ValueError("Invalid meeting instructions")
        instructions = instructions.strip()
    return {"name": name, "instructions": instructions} if name is not None or instructions is not None else None


class APLQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    origin: str = Field(min_length=2, max_length=500)
    destination: str = Field(min_length=2, max_length=500)
    passengers: int = Field(ge=1, le=100, strict=True)
    suitcases: int = Field(ge=0, le=200, strict=True)
    pickup_at: datetime
    expected_hub: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    expected_direction: Literal["from_hub", "to_hub"] | None = None

    @field_validator("origin", "destination")
    @classmethod
    def location(cls, value):
        value = _text(value, 500, required=True)
        if len(value) < 2:
            raise ValueError("Supply an explicit location")
        return value

    @field_validator("pickup_at", mode="before")
    @classmethod
    def timestamp_input(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply an explicit pickup timestamp with UTC offset")
        return value

    @model_validator(mode="after")
    def precise_journey(self):
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("Pickup and dropoff must differ")
        if self.pickup_at.tzinfo is None or self.pickup_at.utcoffset() is None:
            raise ValueError("Pickup time needs a UTC offset")
        if self.pickup_at.second or self.pickup_at.microsecond:
            raise ValueError("Pickup time must be aligned to a whole minute")
        local = self.pickup_at.astimezone(LONDON)
        if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("APL cannot distinguish repeated London clock times; choose an unambiguous pickup time")
        return self

    def supplier_payload(self):
        local = self.pickup_at.astimezone(LONDON)
        return {
            "origin": self.origin, "destination": self.destination,
            "passengers": self.passengers, "suitcases": self.suitcases,
            "transfer_date": local.date().isoformat(), "transfer_time": local.strftime("%H:%M"),
        }


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 64:
        raise ValueError("Invalid transfer price")
    amount = Decimal(str(value))
    if not amount.is_finite() or not 0 <= amount <= 1_000_000 or not -12 <= amount.as_tuple().exponent <= 12:
        raise ValueError("Invalid transfer price")
    return {"amount": format(amount, "f"), "currency": "GBP"}


def _capacity(value, *, bags=False):
    if type(value) is not int or not (0 if bags else 1) <= value <= 200:
        raise ValueError("Missing or invalid vehicle capacity")
    return value


class APLProvider:
    def __init__(self, settings, *, client=None, now=None):
        self.settings = settings
        # Reuse the bounded streaming transport: Decimal JSON, HTTPS only,
        # no redirects/retries, a total timeout and sanitized HTTP errors.
        self._http = RealDataProvider(settings, client=client)
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def configured(self):
        return bool(secret_value(getattr(self.settings, "apl_api_key", "")).strip())

    async def aclose(self):
        await self._http.aclose()

    async def quote(self, request: APLQuoteRequest):
        request = APLQuoteRequest.model_validate(request.model_dump())
        if request.pickup_at <= self._now():
            raise ProviderError("Choose a future pickup time; no quote request was sent.", provider=SOURCE, code="invalid_request")
        if not self.configured:
            raise ProviderError("APL_API_KEY is missing; registered transfer quotes are unavailable.", provider=SOURCE, code="not_configured")
        query = request.supplier_payload()
        payload = await self._http._request("POST", QUOTE_URL, SOURCE,
            headers={"x-api-key": secret_value(self.settings.apl_api_key).strip()}, json=query)
        received_at = self._now().astimezone(timezone.utc).isoformat()
        try:
            return self._normalize(payload, request, query, received_at)
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as exc:
            raise ProviderError("APL returned an invalid or mismatched transfer quote; no substitute transport was created.", provider=SOURCE, code="invalid_response") from exc

    @staticmethod
    def _normalize(payload, request, query, received_at):
        if payload.get("error"):
            raise ValueError("Supplier rejected quote")
        if "currency" in payload and payload["currency"] != "GBP":
            raise ValueError("Quote currency conflicts with GBP price fields")
        for field in ("transfer_date", "transfer_time", "passengers"):
            if payload.get(field) != query[field] or (field == "passengers" and type(payload.get(field)) is not int):
                raise ValueError("Quote differs from requested pickup or party")
        if "suitcases" in payload and (type(payload["suitcases"]) is not int or payload["suitcases"] != request.suitcases):
            raise ValueError("Quote luggage count changed")
        direction = payload.get("direction")
        hub = _text(payload.get("hub"), 3)
        if hub is not None and not re.fullmatch(r"[A-Z]{3}", hub):
            raise ValueError("Invalid hub")
        if request.expected_hub and hub and hub != request.expected_hub:
            raise ValueError("Quote hub changed")
        if direction is not None and direction not in ("from_hub", "to_hub"):
            raise ValueError("Unknown quote direction")
        if request.expected_direction and direction and direction != request.expected_direction:
            raise ValueError("Quote direction changed")
        if "cars" in payload:
            if "from_hub" in payload or "to_hub" in payload:
                raise ValueError("Ambiguous quote format")
            cars = payload["cars"]
            pickup = _text(payload.get("pickup"), required=True)
            dropoff = _text(payload.get("dropoff"), required=True)
        else:
            # The observed directional response contains both outward and
            # reverse prices. The cheaper opposite journey is never a fallback.
            if direction is None or hub is None:
                raise ValueError("Directional quote needs a hub and direction")
            cars = payload[direction]
            pickup, dropoff = _text(payload.get("pickup")), _text(payload.get("dropoff"))
        if not isinstance(cars, list) or len(cars) > 100:
            raise ValueError("Unbounded vehicle quote list")
        options, seen = [], set()
        for car in cars:
            car_type = _text(car.get("car_type"), 100, required=True)
            if car_type in seen:
                raise ValueError("Duplicate vehicle type")
            seen.add(car_type)
            if "currency" in car and car["currency"] != "GBP":
                raise ValueError("Vehicle currency conflicts with GBP price fields")
            price = _price(car["final_price_gbp"] if "final_price_gbp" in car else car["price_gbp"])
            if "final_price_gbp" in car and "price_gbp" in car and Decimal(_price(car["price_gbp"])["amount"]) != Decimal(price["amount"]):
                raise ValueError("Conflicting final prices")
            passengers = _capacity(car.get("max_passengers"))
            suitcases = _capacity(car.get("max_bags"), bags=True)
            identity = hashlib.sha256(json.dumps([query, received_at, direction, car_type], sort_keys=True).encode()).hexdigest()[:24]
            options.append({
                "id": "APLQ-" + identity, "provider_offer_id": None, "car_type": car_type,
                "price": price, "price_scope": "per_vehicle", "max_passengers": passengers, "max_suitcases": suitcases,
                "fits_request": passengers >= request.passengers and suitcases >= request.suitcases,
                "duration_seconds": None, "arrival_at": None, "availability_status": "quoted_not_reserved",
                "accessibility_confirmed": False,
            })
        return {
            "provider": SOURCE, "source_url": DOCS_URL, "state": "ready", "transport": "private_transfer",
            "received_at": received_at, "source_quoted_at": None, "expires_at": None,
            "request": {**query, "pickup_at": request.pickup_at.astimezone(timezone.utc).isoformat(), "timezone": "Europe/London",
                "expected_hub": request.expected_hub, "expected_direction": request.expected_direction},
            "resolved_route": {"pickup": pickup, "dropoff": dropoff, "hub": hub, "direction": direction,
                "resolved_address": _text(payload.get("resolved_address")), "pickup_terminal": _text(payload.get("pickup_terminal"), 100)},
            "price_basis": _text(payload.get("price_basis")),
            "provider_duration_text": _text(payload.get("duration"), 200),
            "meeting_point": _meeting_point(payload.get("meeting_point")),
            "options": options,
            "message": None if any(option["fits_request"] for option in options) else "No quoted vehicle fits the stated passenger and suitcase counts.",
        }
