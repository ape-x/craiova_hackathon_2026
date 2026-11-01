"""Bounded anonymous, quote-only Bucharest.CAB consumer adapter.

The source's own JavaScript calculates cash/no-extras fares from returned
vehicle tariffs and its route distance. Categories are offers, never fleet
stock. No booking, price-storage, payment, autocomplete or Google API methods
are exposed. The fixed airport references were verified through the source's
normal public autocomplete/place-details and quote responses on 2026-11-01.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
from html.parser import HTMLParser
import json
import math
import re
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import ProviderError

SOURCE = "Bucharest.CAB"
SOURCE_URL = "https://www.bucharest.cab/en/book"
QUOTE_URL = "https://www.bucharest.cab/en/"
LOCAL_ZONE = ZoneInfo("Europe/Bucharest")
MAX_BODY = 1_000_000
MAX_BOOTSTRAP_BODY = 250_000
MAX_OPTIONS = 30
MAX_NODES = 20_000
MAX_DEPTH = 64
MAX_ADVANCE_DAYS = 31
HTTP_REQUEST_COUNT = 2
PRICE_SCRIPT_URL = "https://www.bucharest.cab/plugins/content/tulipbooking/media/js/tulipbooking.js"
SOURCE_AIRPORTS = {
    "OTP": {"airport_code": "OTP", "place_id": "ChIJd49YtoEcskARfodtna1dThE",
        "name": "Bucharest Henri Coandă International Airport",
        "address": "Calea Bucureștilor 224E, 075150 Otopeni, Romania",
        "latitude": 44.5668635, "longitude": 26.0949265},
    "CRA": {"airport_code": "CRA", "place_id": "ChIJacWVAiDUUkcRabz12pg8Tz4",
        "name": "Craiova international Airport",
        "address": "Calea București 325, 200621 Craiova, Romania",
        "latitude": 44.3127651, "longitude": 23.878591099999998},
}
_SEARCH_LABELS = {
    "OTP": "Bucharest Henri Coandă International Airport (OTP), Calea Bucureștilor, Otopeni, Romania",
    "CRA": "Craiova international Airport (CRA), Calea București, Craiova, Romania",
}
_ROUTE_ADDRESSES = {
    "OTP": "Bucharest Henri Coandă Int'l Airport (OTP), Calea Bucureștilor 224E, 075150 Otopeni, Romania",
    "CRA": "Craiova international Airport (CRA), Calea București 325, 200621 Craiova, Romania",
}
_VEHICLE_FIELDS = frozenset((
    "id", "type", "text_color", "vehicle_types_icons_id", "passengers", "suitcases", "handbags",
    "price_per_km", "price_per_hour", "baby_seat", "baby_seat_max_number", "baby_seat_price",
    "child_seat", "child_seat_max_number", "child_seat_price", "booster_seat", "booster_seat_max_number",
    "booster_seat_price", "meet_greet_price", "status", "minimal_order_price", "additional_stop_price",
    "created_at", "updated_at", "vehicle_image_base64", "skis", "skis_max_number", "skis_price",
    "snowboards", "snowboards_max_number", "snowboards_price", "bicycles", "bicycles_max_number",
    "bicycles_price", "pets", "pets_max_number", "pets_price", "night_surcharge", "night_surcharge_fee",
    "night_surcharge_start_time", "night_surcharge_end_time", "break_hours", "custom_price", "currency",
))


class AirportCabQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    origin_airport: str = Field(pattern=r"^OTP$")
    destination_airport: str = Field(pattern=r"^CRA$")
    pickup_at: datetime
    passengers: int = Field(ge=1, le=7, strict=True)
    suitcases: int = Field(ge=0, le=7, strict=True)
    handbags: int = Field(ge=0, le=7, strict=True)

    @field_validator("pickup_at", mode="before")
    @classmethod
    def timestamp_input(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply an explicit pickup timestamp with UTC offset")
        return value

    @model_validator(mode="after")
    def unambiguous_time(self):
        stamp = self.pickup_at
        if stamp.tzinfo is None or stamp.utcoffset() is None or stamp.second or stamp.microsecond:
            raise ValueError("Pickup needs a UTC offset and a whole minute")
        local = stamp.astimezone(LOCAL_ZONE)
        if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("The supplier cannot distinguish repeated local clocks")
        # ZoneInfo datetime inputs can represent a nonexistent spring clock.
        if isinstance(stamp.tzinfo, ZoneInfo) and stamp.astimezone(timezone.utc).astimezone(stamp.tzinfo).replace(tzinfo=None) != stamp.replace(tzinfo=None):
            raise ValueError("Pickup is a nonexistent local clock")
        return self


def _text(value, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid source text")
    return value.strip()


def _int(value, lower=0, upper=1_000_000):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("Invalid source integer")
    return value


def _money(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", value):
        raise ValueError("Invalid source decimal money")
    amount = Decimal(value)
    if not 0 <= amount <= 1_000_000:
        raise ValueError("Source amount exceeds bounds")
    return amount


def _amount(value):
    return format(value.quantize(Decimal("0.01")), ".2f")


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:00", value):
        raise ValueError("Invalid policy clock")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _json(raw):
    def reject_constant(_):
        raise ValueError("Nonfinite source number")
    result = json.loads(raw, object_pairs_hook=_unique_object, parse_float=Decimal, parse_constant=reject_constant)
    stack, count = [(result, 0)], 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise ValueError("Source structure exceeds bounds")
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    if not isinstance(result, dict):
        raise ValueError("Source object required")
    return result


class _Bootstrap(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.current, self.nodes = [], None, 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_NODES or len(attrs) > 100:
            raise ValueError("Bootstrap exceeds bounds")
        if tag == "script":
            self.current = []

    def handle_data(self, data):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ValueError("Bootstrap exceeds bounds")
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def _bootstrap_token(raw):
    parser = _Bootstrap()
    parser.feed(raw.decode("utf-8"))
    configs = []
    for script in parser.scripts:
        matches = re.findall(r"\bvar\s+tulipBookingScriptData\s*=\s*(\{.*?\});", script, re.S)
        configs.extend(_json(match) for match in matches)
    if len(configs) != 1:
        raise ValueError("Missing or ambiguous source configuration")
    config = configs[0]
    token = config.get("csrfToken")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9]{16,128}", token):
        raise ValueError("Invalid anonymous session token")
    url = urlsplit(_text(config.get("ajax_get_suitable_vehicles_url"), 2000))
    query = Counter(parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=5))
    if (url.scheme != "https" or url.hostname != "www.bucharest.cab" or url.username or url.password or url.port is not None
            or url.fragment or url.path not in ("/index.php", "/en/")
            or query.pop(("format", "json"), 0) not in (1, 2)
            or query != Counter({("option", "com_tulipbooking"): 1, ("task", "ajax.getSuitableVehicles"): 1})):
        raise ValueError("Unrecognized source quote endpoint")
    return token


def _endpoint(value, code):
    expected = SOURCE_AIRPORTS[code]
    if not isinstance(value, dict) or value.get("id") != expected["place_id"]:
        raise ValueError("Source airport reference changed")
    if not isinstance(value.get("types"), list) or "airport" not in value["types"]:
        raise ValueError("Source reference is not an airport")
    if _text(value.get("formattedAddress")) != expected["address"] or _text(value.get("displayName", {}).get("text")) != expected["name"]:
        raise ValueError("Source airport identity changed")
    coordinates = value.get("location", {})
    normalized = dict(expected)
    for key in ("latitude", "longitude"):
        number = coordinates.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float, Decimal)) or not math.isfinite(float(number)) or abs(float(number) - expected[key]) > 0.001:
            raise ValueError("Source airport coordinates changed")
        normalized[key] = float(number)
    if not any(isinstance(item, dict) and item.get("shortText") == "RO" and "country" in item.get("types", []) for item in value.get("addressComponents", [])):
        raise ValueError("Source airport country changed")
    return normalized


def normalize_quote(payload, request, *, received_at):
    """Pure parser of the observed source JSON, with no resource allocation."""
    if payload.get("success") is not True or payload.get("error"):
        raise ValueError("Source did not return a successful quote")
    local = request.pickup_at.astimezone(LOCAL_ZONE)
    supplier_clock = local.strftime("%Y-%m-%d %H:%M:%S")
    if payload.get("timezone") != "Europe/Bucharest" or payload.get("datetime_param") != supplier_clock:
        raise ValueError("Source pickup scope changed")
    if payload.get("datetime") != request.pickup_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        raise ValueError("Source normalized pickup differs")
    # The observed response does not echo party/bags. A future explicit echo
    # must agree; the exact HTTP request scopes party, while bags are local fit.
    for key in ("passengers", "suitcases", "handbags"):
        if key in payload and (type(payload[key]) is not int or payload[key] != getattr(request, key)):
            raise ValueError("Source party scope changed")
    origin = _endpoint(payload.get("start_place_data"), request.origin_airport)
    destination = _endpoint(payload.get("end_place_data"), request.destination_airport)
    matrix = payload["distance"]
    if matrix.get("status") != "OK" or matrix.get("origin_addresses") != [_ROUTE_ADDRESSES[request.origin_airport]] or matrix.get("destination_addresses") != [_ROUTE_ADDRESSES[request.destination_airport]]:
        raise ValueError("Source route changed")
    rows = matrix["rows"]
    if len(rows) != 1 or len(rows[0]["elements"]) != 1 or rows[0]["elements"][0].get("status") != "OK":
        raise ValueError("Missing unambiguous source route")
    element = rows[0]["elements"][0]
    distance = _int(element["distance"]["value"], 1)
    duration = _int(element["duration"]["value"], 1, 86400)
    website = payload["website"]
    if website.get("currency") != "EUR" or website.get("name") != SOURCE:
        raise ValueError("Source currency or supplier changed")
    card_fee = _money(website["card_payment_fee"])
    day_hours = _int(website["min_advance_day_booking"], 0, 168)
    night_hours = _int(website["min_advance_night_booking"], 0, 168)
    required_hours = max(day_hours, night_hours)
    policy = {"min_advance_day_hours": day_hours, "min_advance_night_hours": night_hours,
        "night_restricted_start_time": _clock(website["night_restricted_start_time"]),
        "night_restricted_end_time": _clock(website["night_restricted_end_time"]),
        "required_advance_hours": required_hours, "advance_policy_basis": "conservative_maximum_of_source_day_night_rules"}
    if request.pickup_at - received_at < timedelta(hours=required_hours):
        raise ProviderError("The pickup does not meet the verified supplier advance-booking window; no later pickup was substituted.", provider=SOURCE, code="pickup_too_soon")
    cars = payload["vehicle_types"]
    if not isinstance(cars, list) or len(cars) > MAX_OPTIONS:
        raise ValueError("Unbounded source vehicles")
    options, seen_ids, seen_names = [], set(), set()
    received = received_at.astimezone(timezone.utc).isoformat()
    normalized_request = {**request.model_dump(mode="json"), "pickup_at": request.pickup_at.astimezone(timezone.utc).isoformat(),
        "timezone": "Europe/Bucharest", "supplier_pickup_datetime": supplier_clock,
        "trip_type": "from-airport", "baggage_supplier_scoped": False}
    for car in cars:
        # The public endpoint returns tariffs rather than a final amount. New
        # fields could change mandatory fees or operating restrictions, so an
        # unfamiliar vehicle schema cannot silently reuse the observed formula.
        if not isinstance(car, dict) or set(car) - _VEHICLE_FIELDS or car.get("break_hours") != []:
            raise ValueError("Unverified vehicle tariff or operating restriction")
        identity = _int(car["id"], 1, 1_000_000_000)
        car_type = _text(car["type"], 100)
        if identity in seen_ids or car_type.casefold() in seen_names or type(car.get("status")) is not int or car["status"] != 1:
            raise ValueError("Duplicate or inactive source vehicle")
        seen_ids.add(identity); seen_names.add(car_type.casefold())
        if "currency" in car and car["currency"] != "EUR":
            raise ValueError("Vehicle currency changed")
        capacities = {"max_passengers": _int(car["passengers"], 1, 7),
            "max_suitcases": _int(car["suitcases"], 0, 7), "max_handbags": _int(car["handbags"], 0, 7)}
        per_km, minimum = _money(car["price_per_km"]), _money(car["minimal_order_price"])
        custom = None if car.get("custom_price") is None else _money(car["custom_price"])
        if custom is not None and custom <= 0:
            raise ValueError("Unusable custom price")
        distance_fare = (Decimal(distance) / 1000 * per_km).quantize(Decimal("1"), rounding=ROUND_CEILING)
        if type(car.get("night_surcharge")) is not bool:
            raise ValueError("Unknown night surcharge")
        night_fee = _money(car["night_surcharge_fee"])
        surcharge = night_fee if car["night_surcharge"] else Decimal(0)
        fare = (custom if custom is not None else max(distance_fare, minimum)) + surcharge
        if not 0 < fare <= 1_000_000:
            raise ValueError("Unusable source fare")
        components = {"distance_meters": distance, "price_per_km": _amount(per_km),
            "distance_fare_rounded_up": _amount(distance_fare), "minimal_order_price": _amount(minimum),
            "custom_price": _amount(custom) if custom is not None else None,
            "night_surcharge_applied": car["night_surcharge"], "night_surcharge_fee": _amount(night_fee),
            "night_surcharge_total": _amount(surcharge), "payment_method": "cash", "payment_fee": "0.00",
            "card_payment_fee": _amount(card_fee), "extras_total": "0.00"}
        digest = hashlib.sha256(json.dumps([normalized_request, received, identity, components], sort_keys=True).encode()).hexdigest()[:24]
        options.append({"id": "BCCQ-" + digest, "provider_offer_id": None, "provider_vehicle_type_id": identity,
            "car_type": car_type, "price": {"amount": _amount(fare), "currency": "EUR"},
            "price_scope": "per_vehicle", **capacities,
            "fits_request": capacities["max_passengers"] >= request.passengers and capacities["max_suitcases"] >= request.suitcases and capacities["max_handbags"] >= request.handbags,
            "availability_status": "quoted_not_reserved", "remaining_fleet": None, "supplier_valid_until": None,
            "accessibility_confirmed": False, "price_components": components})
    return {"provider": SOURCE, "source_url": SOURCE_URL, "state": "ready", "transport": "private_transfer",
        "booking_supported": False, "received_at": received, "source_quoted_at": None, "expires_at": None,
        "http_request_count": HTTP_REQUEST_COUNT, "request": normalized_request,
        "resolved_route": {"origin": origin, "destination": destination},
        "route": {"distance_meters": distance, "duration_seconds": duration,
            "duration_text": _text(element["duration"]["text"], 100), "duration_basis": "source_estimate_without_departure_time"},
        "policy": policy, "price_basis": "supplier_client_formula_cash_no_extras", "price_formula_source_url": PRICE_SCRIPT_URL,
        "options": options, "notices": [
            "Vehicle categories are quoted, not reserved; remaining fleet and supplier quote expiry are unknown.",
            "The source receives date, endpoints and party; suitcase and cabin-bag fit is checked locally against source vehicle capacities.",
            "Prices use the supplier's cash/no-extras formula; payment-link and optional-extra fees are not included.",
            "Route duration is the source estimate without departure-time traffic; no airport meeting point or curb access is asserted.",
            "The longer source day/night advance-booking requirement is enforced because the rule-selection clock is undocumented.",
        ]}


class BucharestCabProvider:
    def __init__(self, settings, *, client=None, now=None):
        self.timeout = min(max(float(getattr(settings, "request_timeout_seconds", 30)), 0.01), 45)
        self.client = client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        self._owns_client = client is None
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
        request = AirportCabQuoteRequest.model_validate(request.model_dump() if isinstance(request, AirportCabQuoteRequest) else request)
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Provider clock needs an offset")
        if request.pickup_at <= now or request.pickup_at > now + timedelta(days=MAX_ADVANCE_DAYS):
            raise ProviderError("Choose a future pickup within 31 days; no source request was sent.", provider=SOURCE, code="invalid_request")
        return request

    async def _get(self, url, *, cookies, params=None, html=False):
        # Construct requests independently of shared-client default cookies,
        # auth and headers. Only this anonymous quote's cookies are reused.
        request = httpx.Request("GET", url, params=params, cookies=cookies,
            headers={"Accept": "text/html" if html else "application/json", "User-Agent": "AirportRecoveryLive/0.1 consumer-quote-search",
                "Referer": SOURCE_URL, "X-Requested-With": "XMLHttpRequest"},
            extensions={"timeout": {key: self.timeout for key in ("connect", "read", "write", "pool")}})
        from ..provider_budget import consume_call
        consume_call(SOURCE)
        response = await self.client.send(request, stream=True, auth=None, follow_redirects=False)
        try:
            if response.status_code != 200:
                raise ProviderError(f"Bucharest.CAB quote search failed (HTTP {response.status_code}); no retry was made.", provider=SOURCE, code=f"http_{response.status_code}")
            expected = "text/html" if html else "application/json"
            if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != expected:
                raise ValueError("Unexpected source content type")
            maximum = MAX_BOOTSTRAP_BODY if html else MAX_BODY
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise ValueError("Source body exceeds bounds")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16384):
                body.extend(chunk)
                if len(body) > maximum:
                    raise ValueError("Source body exceeds bounds")
            cookies.extract_cookies(response)
            return bytes(body)
        finally:
            await response.aclose()

    async def _bounded_operation(self, operation, deadline):
        """Translate source/parse failures inside the runner's recorded phase."""
        try:
            async with asyncio.timeout_at(deadline):
                return await operation()
        except ProviderError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError("Bucharest.CAB quote search timed out; no automatic retry was made.", provider=SOURCE, code="timeout") from None
        except httpx.HTTPError:
            raise ProviderError("Bucharest.CAB could not be reached; no substitute transport was created.", provider=SOURCE, code="connection") from None
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, RecursionError):
            raise ProviderError("Bucharest.CAB returned an unverifiable quote; no substitute transport was created.", provider=SOURCE, code="invalid_response") from None

    async def quote(self, request: AirportCabQuoteRequest, *, before_dispatch=None, request_runner=None):
        """Run exactly two phases; the bootstrap result is private session data.

        A runner must return the operation's value to this adapter, but must
        never persist or expose the bootstrap value. It may record only safe
        bootstrap-received metadata. The vehicles result is normalized public
        evidence. No runner means the same bounded manual search as before.
        """
        request = self.validate_request(request)
        if self._closed:
            raise ProviderError("Bucharest.CAB quote search is closed.", provider=SOURCE, code="not_configured")
        deadline = asyncio.get_running_loop().time() + self.timeout
        cookies = httpx.Cookies()

        async def phase(name, operation):
            # The source context guard precedes the job runner, so a rejected
            # context cannot create a fictitious recovery-ledger HTTP dispatch.
            if before_dispatch is not None:
                await self._bounded_operation(before_dispatch, deadline)
            invoked = False
            async def bounded():
                nonlocal invoked
                if invoked:
                    raise ProviderError("A source phase cannot be dispatched twice; no retry was made.", provider=SOURCE, code="request_reused")
                invoked = True
                return await self._bounded_operation(operation, deadline)
            return await request_runner(name, bounded) if request_runner is not None else await bounded()

        async def bootstrap():
            return _bootstrap_token(await self._get(SOURCE_URL, cookies=cookies, html=True))

        token = await phase("bootstrap", bootstrap)

        async def vehicles():
            params = {"option": "com_tulipbooking", "format": "json", "task": "ajax.getSuitableVehicles", "csrfToken": token,
                "start": _SEARCH_LABELS[request.origin_airport], "end": _SEARCH_LABELS[request.destination_airport],
                "tripType": "from-airport", "placeIdStart": SOURCE_AIRPORTS[request.origin_airport]["place_id"],
                "placeIdEnd": SOURCE_AIRPORTS[request.destination_airport]["place_id"],
                "passengers": str(request.passengers), "pickupDateTime": request.pickup_at.astimezone(LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S"), "hourlyRent": ""}
            payload = _json(await self._get(QUOTE_URL, cookies=cookies, params=params))
            if payload.get("error") in ("min_advance_day_booking", "min_advance_night_booking"):
                raise ProviderError("The supplier requires more advance notice for this pickup; no later time was substituted.", provider=SOURCE, code="pickup_too_soon")
            if payload.get("error") == "no_vehicle_types_found":
                raise ProviderError("The supplier returned no vehicle categories for the requested journey.", provider=SOURCE, code="no_vehicles")
            return normalize_quote(payload, request, received_at=self._now())

        return await phase("vehicles", vehicles)
