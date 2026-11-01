"""Observed public Direct Aeroport searches, never checkout, holds or bookings.

The consumer form has no adult maximum; 20 is our request bound. Only the
verified OTP/Aro Palace stops and exact known per-trip conditions are accepted.
Source clocks do not carry offsets: conversion is explicitly our Bucharest
interpretation, with ambiguous/nonexistent local times rejected.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import ProviderError

SOURCE = "Transfero"
OPERATOR = "Direct Aeroport"
OPERATOR_ID = "2995"
HOTEL_ID = "lpce923"
SOURCE_URL = "https://www.transfero.ro/cauta-bilete/"
SEARCH_URL = "https://www.transfero.ro/wp-admin/admin-ajax.php"
POLICY_URL = "https://direct-aeroport.ro/termeni-conditii-calatorie/"
STOP_SOURCE_URL = "https://direct-aeroport.ro/contact/"
TERMS_ID = "57392"
TERMS_SHA256 = "ebd5c726b01c4142f4065b172b38429934bea61c2914ded4b5fbc89d2127b85e"
MAX_ADULTS = 20  # Application bound; the observed form/plus handler has no maximum.
MAX_OPTIONS = 30
MAX_BODY = 1_000_000
ZONE = ZoneInfo("Europe/Bucharest")
STOPS = {
    "otp_arrivals": {"name": "Terminal SOSIRI / ARRIVALS", "city": "Aeroport Otopeni", "latitude": 44.56907, "longitude": 26.0758},
    "otp_departures": {"name": "Terminal PLECARI/ DEPARTURES", "city": "Aeroport Otopeni", "latitude": 44.5713386535645, "longitude": 26.0765895843506},
    "aro_palace": {"name": "Hotel Aro Palace", "city": "Brasov", "latitude": 45.64533, "longitude": 25.59147},
}
POLICY = {
    "source_url": POLICY_URL, "attached_terms_id": TERMS_ID, "attached_terms_sha256": TERMS_SHA256,
    "hold_bags_per_person": 1, "hold_bag_max_kg": 25, "handbags_per_person": 1, "handbag_max_kg": 10,
    "baggage_basis": "operator_published_standard_allowance_not_individual_bag_check",
    "additional_baggage_supported": False,
    "required_advance_hours": 12, "boarding_minutes": 20, "airport_processing_minutes": 180,
    "timing_basis": "app_conservative_maximum_of_operator_and_attached_terms",
    "schedule_basis": "source_estimated_local_timetable",
    "boarding_location_basis": "operator_named_facility_exact_bay_confirmed_after_booking",
}


class TransferoSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    direction: Literal["otp_to_hotel", "hotel_to_otp"]
    travel_date: date
    adults: int = Field(strict=True, ge=1, le=MAX_ADULTS)
    suitcases: int = Field(strict=True, ge=0, le=MAX_ADULTS)
    handbags: int = Field(strict=True, ge=0, le=MAX_ADULTS)
    standard_baggage_confirmed: Literal[True]

    @field_validator("travel_date", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if isinstance(value, datetime) or not isinstance(value, (str, date)) or (isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
            raise ValueError("An explicit YYYY-MM-DD date is required")
        return value

    @field_validator("standard_baggage_confirmed", mode="before")
    @classmethod
    def explicit_acknowledgement(cls, value):
        if value is not True:
            raise ValueError("Explicit standard baggage confirmation is required")
        return value

    @model_validator(mode="after")
    def baggage(self):
        if self.suitcases > self.adults or self.handbags > self.adults:
            raise ValueError("Only the standard per-passenger baggage allowance is supported")
        return self


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Unrecognized source fields")


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,11}", value):
        raise ValueError("Invalid source identifier")
    return value


def _number(value, *, maximum=10000):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid source quantity")
    return value


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", str(value)):
        raise ValueError("Invalid exact fare")
    amount = Decimal(value)
    if not 0 < amount <= 1_000_000:
        raise ValueError("Unsupported fare")
    return amount


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Invalid source clock")
    return value


def _endpoint(value, key):
    _keys(value, ("datetime", "date", "time", "station"))
    _keys(value["station"], ("name", "city", "lat", "long"))
    expected = STOPS[key]
    station = value["station"]
    if (station["name"] != expected["name"] or station["city"] != expected["city"]
            or any(not isinstance(station[k], str) or not re.fullmatch(r"[0-9]+\.[0-9]+", station[k]) for k in ("lat", "long"))
            or Decimal(station["lat"]) != Decimal(str(expected["latitude"]))
            or Decimal(station["long"]) != Decimal(str(expected["longitude"]))):
        raise ValueError("Unknown stop; proximity is not a source binding")
    clock = _clock(value["time"])
    if not isinstance(value["date"], str) or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value["date"]) or value["datetime"] != f"{value['date']} {clock}":
        raise ValueError("Conflicting source date/time")
    local = datetime.strptime(value["datetime"], "%d.%m.%Y %H:%M").replace(tzinfo=ZONE)
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset() or local.astimezone(timezone.utc).astimezone(ZONE) != local:
        raise ValueError("Ambiguous or nonexistent local source time")
    return local, {**expected, "id": key, "source_url": STOP_SOURCE_URL}


def inventory_pool_id(trip_id, route_id, departure_local):
    # A repeated request/party/fare revision never creates new seat stock.
    identity = f"{OPERATOR_ID}|{trip_id}|{route_id}|{departure_local[:10]}"
    return "TRANSFERO-POOL-" + hashlib.sha256(identity.encode()).hexdigest()[:24].upper()


def _option(row, request, received_at):
    _keys(row, ("id", "cid", "locuri", "oraplecare", "orasosire", "durata", "tip", "company", "tickets", "segments"))
    trip, route = _identifier(row["id"]), _identifier(row["cid"])
    seats = _number(row["locuri"])
    if row["company"] != {"id": OPERATOR_ID, "name": OPERATOR}:
        raise ValueError("Unsupported operator")
    if not isinstance(row["segments"], list) or len(row["segments"]) != 1:
        raise ValueError("Only a single source segment is supported")
    segment = row["segments"][0]
    _keys(segment, ("departure", "arrival", "name", "duration", "seat_select", "terms", "termsid", "linkid", "vehicle", "company"))
    if (segment["company"] != row["company"] or segment["linkid"] != trip or segment["seat_select"] is not False
            or segment["termsid"] != TERMS_ID or not isinstance(segment["terms"], str)
            or hashlib.sha256(segment["terms"].encode()).hexdigest() != TERMS_SHA256
            or segment["vehicle"] != row["tip"] or row["tip"] not in ("minivan", "van")
            or not isinstance(segment["name"], str) or not 1 <= len(segment["name"]) <= 200):
        raise ValueError("Changed service identity, policy or vehicle")
    a, b = ("otp_arrivals", "aro_palace") if request.direction == "otp_to_hotel" else ("aro_palace", "otp_departures")
    departure, origin = _endpoint(segment["departure"], a)
    arrival, destination = _endpoint(segment["arrival"], b)
    if (departure.date() != request.travel_date or row["oraplecare"] != departure.strftime("%H:%M")
            or row["orasosire"] != arrival.strftime("%H:%M") or segment["duration"] != row["durata"]):
        raise ValueError("Search date or service times disagree")
    duration = _clock(row["durata"])
    duration_seconds = (int(duration[:2]) * 60 + int(duration[3:])) * 60
    if not 0 < duration_seconds <= 86400 or (arrival.astimezone(timezone.utc) - departure.astimezone(timezone.utc)).total_seconds() != duration_seconds:
        raise ValueError("Source chronology and duration disagree")
    _keys(row["tickets"], ("adulti",))
    _keys(row["tickets"]["adulti"], ("oneway", "ban"))
    fare = row["tickets"]["adulti"]["oneway"]
    _keys(fare, ("name", "id", "tax", "pret", "pret_u", "rate", "pret_ron"))
    amount = _money(fare["pret_ron"])
    if (row["tickets"]["adulti"]["ban"] != "RON" or fare["name"] != "Dus"
            or type(fare["rate"]) is not int or fare["rate"] != request.adults
            or _money(fare["pret"]) != amount or _money(fare["pret_u"]) != amount):
        raise ValueError("Conflicting requested-party fare")
    _identifier(fare["id"])
    _number(fare["tax"], maximum=1_000_000)  # Opaque source field: not a fee calculation or public tax claim.
    if seats < request.adults:
        raise ValueError("Insufficient reported seats")
    if departure < received_at + timedelta(hours=POLICY["required_advance_hours"]):
        raise ValueError("Source advance eligibility does not cover departure")
    local = departure.isoformat()
    pool = inventory_pool_id(trip, route, local)
    option = {
        "inventory_pool_id": pool, "source_trip_id": trip, "source_route_id": route,
        "operator_id": OPERATOR_ID, "operator": OPERATOR, "vehicle_type": row["tip"],
        "departure_at": departure.astimezone(timezone.utc).isoformat(), "arrival_at": arrival.astimezone(timezone.utc).isoformat(),
        "departure_local": local, "arrival_local": arrival.isoformat(), "timezone": "Europe/Bucharest",
        "timezone_basis": "app_interpretation_of_source_local_clock", "departure_stop": origin, "arrival_stop": destination,
        "duration_seconds": duration_seconds, "available_seats": seats,
        "price": {"amount": format(amount, ".2f"), "currency": "RON"}, "price_scope": "requested_party", "adults": request.adults,
        "availability_status": "quoted_not_reserved", "seat_selection": False, "supplier_valid_until": None,
        "terms_id": TERMS_ID, "policy": dict(POLICY), "price_basis": "source_displayed_standard_ticket_party_total",
    }
    encoded = json.dumps(option, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    option["id"] = "TRANSFERO-" + hashlib.sha256(encoded.encode()).hexdigest()[:24].upper()
    return option


def parse_search_response(payload: bytes, request: TransferoSearchRequest, *, received_at: datetime):
    request = TransferoSearchRequest.model_validate(request.model_dump())
    if not isinstance(payload, bytes) or len(payload) > MAX_BODY or received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("Invalid bounded source response")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON source fields")
            result[key] = value
        return result
    data = json.loads(payload, parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid source number")), object_pairs_hook=unique)
    _keys(data, ("d", "i"))
    if not isinstance(data["d"], list) or len(data["d"]) > 100 or data["i"] != []:
        raise ValueError("Unexpected or unbounded search result")
    options, identities, rejected = [], {}, Counter()
    raw_identities = {}
    for row in data["d"]:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            identity = row["id"]
            if identity in raw_identities and raw_identities[identity] != row:
                raise ValueError("Conflicting repeated source service")
            raw_identities[identity] = row
        try:
            option = _option(row, request, received_at)
        except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
            rejected["unverified_or_ineligible_service"] += 1
            continue
        key = option["source_trip_id"]
        if key in identities:
            if option != identities[key]:
                raise ValueError("Conflicting repeated service")
            rejected["duplicate_service"] += 1
            continue
        identities[key] = option
        if len(options) < MAX_OPTIONS:
            options.append(option)
        else:
            rejected["option_limit"] += 1
    return {"provider": SOURCE, "source_url": SOURCE_URL, "state": "ready", "transport": "scheduled_coach",
        "received_at": received_at.astimezone(timezone.utc).isoformat(), "request": request.model_dump(mode="json"),
        "options": options, "rejected_count": sum(rejected.values()),
        "rejection_reasons": [{"code": key, "count": value} for key, value in sorted(rejected.items())],
        "source_quoted_at": None, "expires_at": None, "booking_supported": False, "source_max_adults": None,
        "app_max_adults": MAX_ADULTS, "policy": dict(POLICY),
        "notices": ["Reported seats and the requested-party RON fare are unreserved; revalidate before purchase.",
            "Bucharest offsets are application interpretations of source local clocks; arrival times are estimates.",
            "Exact boarding instructions follow booking. The named hotel is an operator boarding facility, not a verified door or bay.",
            "The application applies conservative 12-hour notice, 20-minute boarding and 3-hour flight connection rules."]}


def validate_normalized_option(quote, option_id, *, now, require_current=True):
    """Pure receipt verification for independent pricing, staging and allocation."""
    if (not isinstance(quote, dict) or quote.get("provider") != SOURCE or quote.get("source_url") != SOURCE_URL
            or quote.get("state") != "ready" or quote.get("transport") != "scheduled_coach"
            or quote.get("booking_supported") is not False or quote.get("policy") != POLICY
            or "source_quoted_at" not in quote or quote["source_quoted_at"] is not None
            or "expires_at" not in quote or quote["expires_at"] is not None
            or now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("A verified unreserved source receipt is required")
    request = TransferoSearchRequest.model_validate(quote.get("request"))
    received = datetime.fromisoformat(quote["received_at"])
    if received.tzinfo is None or received.utcoffset() is None or received > now or (require_current and now > received + timedelta(seconds=120)):
        raise ValueError("The application receipt is stale or future-dated")
    options = quote.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        raise ValueError("A bounded option list is required")
    ids = [option.get("id") if isinstance(option, dict) else None for option in options]
    if any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids) or ids.count(option_id) != 1:
        raise ValueError("Option identity is absent or ambiguous")
    option = options[ids.index(option_id)]
    original = {key: value for key, value in option.items() if key not in ("id", "eur_reference_estimate")}
    encoded = json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if option_id != "TRANSFERO-" + hashlib.sha256(encoded.encode()).hexdigest()[:24].upper():
        raise ValueError("Normalized source option changed")
    a, b = ("otp_arrivals", "aro_palace") if request.direction == "otp_to_hotel" else ("aro_palace", "otp_departures")
    if (option.get("operator") != OPERATOR or option.get("operator_id") != OPERATOR_ID or option.get("policy") != POLICY
            or option.get("departure_stop") != {**STOPS[a], "id": a, "source_url": STOP_SOURCE_URL}
            or option.get("arrival_stop") != {**STOPS[b], "id": b, "source_url": STOP_SOURCE_URL}
            or option.get("timezone") != ZONE.key or option.get("timezone_basis") != "app_interpretation_of_source_local_clock"
            or option.get("availability_status") != "quoted_not_reserved" or option.get("seat_selection") is not False
            or "supplier_valid_until" not in option or option["supplier_valid_until"] is not None
            or option.get("terms_id") != TERMS_ID or option.get("price_scope") != "requested_party"
            or option.get("price_basis") != "source_displayed_standard_ticket_party_total"
            or type(option.get("adults")) is not int or option["adults"] != request.adults
            or type(option.get("available_seats")) is not int or not request.adults <= option["available_seats"] <= 10000
            or type(option.get("duration_seconds")) is not int or not 0 < option["duration_seconds"] <= 86400):
        raise ValueError("Normalized source facts or party scope disagree")
    local_times = []
    for role in ("departure", "arrival"):
        instant = datetime.fromisoformat(option[role + "_at"])
        local = datetime.fromisoformat(option[role + "_local"])
        if (instant.tzinfo is None or instant.utcoffset() != timedelta(0) or instant.second or instant.microsecond
                or local.isoformat() != instant.astimezone(ZONE).isoformat()
                or instant.astimezone(ZONE).replace(fold=0).utcoffset() != instant.astimezone(ZONE).replace(fold=1).utcoffset()):
            raise ValueError("Normalized local clock interpretation differs")
        local_times.append(local)
    departure, arrival = local_times
    if (departure.date() != request.travel_date or (arrival - departure).total_seconds() != option["duration_seconds"]
            or not 0 <= (request.travel_date - received.astimezone(ZONE).date()).days <= 31
            or departure < (now if require_current else received) + timedelta(hours=POLICY["required_advance_hours"])):
        raise ValueError("Source timing or advance eligibility differs")
    if option.get("inventory_pool_id") != inventory_pool_id(_identifier(option.get("source_trip_id")), _identifier(option.get("source_route_id")), option["departure_local"]):
        raise ValueError("Source seat pool identity changed")
    _keys(option.get("price"), ("amount", "currency"))
    price = option["price"]
    if price["currency"] != "RON" or not isinstance(price["amount"], str) or format(_money(price["amount"]), ".2f") != price["amount"]:
        raise ValueError("The original RON party fare changed")
    return option


class TransferoProvider:
    def __init__(self, settings, *, client=None, now=None):
        self.timeout = min(15.0, max(1.0, float(getattr(settings, "request_timeout_seconds", 15))))
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._closed = False

    @property
    def configured(self):
        return not self._closed

    async def aclose(self):
        self._closed = True
        if self._owns_client:
            await self.client.aclose()

    def validate_request(self, request):
        request = TransferoSearchRequest.model_validate(request.model_dump() if isinstance(request, TransferoSearchRequest) else request)
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("A timezone-aware provider clock is required")
        if not 0 <= (request.travel_date - now.astimezone(ZONE).date()).days <= 31:
            raise ProviderError("Choose a departure date within the next 31 days; no source request was sent.", provider=SOURCE, code="invalid_request")
        return request

    async def search(self, request):
        request = self.validate_request(request)
        if self._closed:
            raise ProviderError("The coach search adapter is closed.", provider=SOURCE, code="not_configured")
        a, b = ("1231269", "199") if request.direction == "otp_to_hotel" else ("199", "1231269")
        form = {"action": "get_busses", "adulti": str(request.adults), "copii": "0", "dp": request.travel_date.strftime("%m/%d/%Y"), "di": "", "dep": a, "dest": b}
        from ..provider_budget import consume_call
        consume_call(SOURCE)
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("POST", SEARCH_URL, data=form, follow_redirects=False, timeout=self.timeout,
                        headers={"Accept": "application/json", "User-Agent": "AirportRecoveryLive/0.1 consumer-coach-search"}) as response:
                    if response.status_code != 200:
                        raise ProviderError(f"Transfero search failed (HTTP {response.status_code}); no retry was made.", provider=SOURCE, code=f"http_{response.status_code}")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in ("application/json", "text/html"):
                        raise ValueError("Unexpected content type")
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > MAX_BODY):
                        raise ValueError("Body exceeds bounds")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16_384):
                        body.extend(chunk)
                        if len(body) > MAX_BODY:
                            raise ValueError("Body exceeds bounds")
            return parse_search_response(bytes(body), request, received_at=self._now())
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError("Transfero did not return a bounded result before the deadline; no retry was made.", provider=SOURCE, code="timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Transfero could not be reached; no substitute supply was created.", provider=SOURCE, code="connection") from exc
        except (KeyError, TypeError, ValueError, ArithmeticError, RecursionError) as exc:
            raise ProviderError("Transfero returned unverifiable dated service data; no substitute supply was created.", provider=SOURCE, code="invalid_response") from exc
