"""Bounded public-map and credentialed travel lookups; never inventory writes.

Official contracts: overpass-api.de, project-osrm.org/docs/v5.24.0/api,
developers.google.com/maps/documentation/{places/web-service, routes}, and
developers.booking.com/demand/docs/open-api/3.3. Public map hosts are suitable
for low-volume local evaluation; use an operated service for production.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from .base import ProviderError, secret_value


MAX_BODY = 2_000_000
USER_AGENT = "AirportRecoveryLive/1.0 (+https://github.com/ape-x/craiova_hackathon_2026)"


def safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    return value


def _text(value: Any, maximum: int = 300) -> str | None:
    return value[:maximum] if isinstance(value, str) and value.strip() else None


def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Invalid number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("Out-of-range number")
    return number


def _distance(lat: float, lon: float, airport: dict) -> float:
    a, b = math.radians(airport["latitude"]), math.radians(lat)
    dy, dx = b-a, math.radians(lon-airport["longitude"])
    h = math.sin(dy/2)**2 + math.cos(a)*math.cos(b)*math.sin(dx/2)**2
    return round(6371 * 2 * math.asin(min(1, math.sqrt(h))), 2)


def _invalid(source: str) -> ProviderError:
    return ProviderError(f"{source} returned an invalid response; no substitute data was used.", provider=source, code="invalid_response")


def _opaque_id(value: Any, *, required: bool = False) -> str | None:
    """Preserve provider identifiers exactly; a truncated ID is not reusable."""
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 32768 or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid provider quote identifier")
    return value


def _terms_text(value: Any, maximum: int = 32768) -> str | None:
    """Do not silently truncate conditions that may affect a recommendation."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("Invalid quote terms")
    return value


def _quoted_money(value: Any) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", value["currency"]):
        raise ValueError("Invalid quote currency")
    raw = value.get("amount")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise ValueError("Invalid quote amount")
    amount = Decimal(str(raw))
    if not amount.is_finite() or not 0 <= amount <= 1_000_000:
        raise ValueError("Invalid quote amount")
    return {"amount": format(amount, "f"), "currency": value["currency"]}


def _fee_money(value: dict) -> dict:
    """Missing fee components remain unknown; malformed components fail closed."""
    raw, currency = value.get("amount"), value.get("currency")
    amount = None
    if raw is not None:
        # Reuse strict amount parsing without inferring the fee's real currency.
        amount = _quoted_money({"amount": raw, "currency": "EUR"})["amount"]
    if currency is not None and (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
        raise ValueError("Invalid fee currency")
    return {"amount": amount, "currency": currency}


def _liteapi_pricing(amount: Decimal, room_rates: list[dict]) -> dict:
    """Aggregate the supplier's full-stay rates, never nights or guest counts.

    The official JSON guide says excluded fees are payable at the property;
    included taxes and commission already belong to the booking amount.
    https://docs.liteapi.travel/docs/hotel-rates-api-json-data-structure
    """
    totals = [rate["booking_total"] for rate in room_rates]
    if all(total is not None for total in totals):
        if any(total["currency"] != "EUR" for total in totals) or sum((Decimal(total["amount"]) for total in totals), Decimal(0)) != amount:
            raise ValueError("Offer total disagrees with room totals")
    elif any(total is not None and total["currency"] != "EUR" for total in totals):
        raise ValueError("Room total currency disagrees with offer")
    excluded: dict[str, Decimal] = {}
    reasons = set()
    for rate in room_rates:
        if rate["fees_disclosure"] == "missing":
            reasons.add("fees_not_disclosed")
        for fee in rate["taxes_and_fees"]:
            if fee["included"]:
                continue
            if fee["amount"] is None:
                reasons.add("excluded_fee_amount_missing")
            if fee["currency"] is None:
                reasons.add("excluded_fee_currency_missing")
            if fee["amount"] is None or fee["currency"] is None:
                continue
            currency = fee["currency"]
            excluded[currency] = excluded.get(currency, Decimal(0)) + Decimal(fee["amount"])
            if currency != "EUR":
                reasons.add("unsupported_budget_currency")
    mandatory = amount + excluded.get("EUR", Decimal(0))
    if mandatory > 1_000_000 or any(total > 1_000_000 for total in excluded.values()):
        raise ValueError("Invalid aggregate mandatory cost")
    return {
        "booking_total": {"amount": format(amount, "f"), "currency": "EUR"},
        "excluded_fee_totals": [{"amount": format(total, "f"), "currency": currency} for currency, total in sorted(excluded.items())],
        "mandatory_total": None if reasons else {"amount": format(mandatory, "f"), "currency": "EUR"},
        "status": "incomplete" if reasons else "complete", "unresolved_reasons": sorted(reasons),
        # A search is not a payment. Payment timing belongs to the checkout flow.
        "payment_timing": "unconfirmed",
    }


def _quote_context(request: Any, provider: str, observed_at: str) -> dict:
    return {
        "provider": provider, "provider_offer_id": None,
        "checkin": request.checkin.isoformat(), "checkout": request.checkout.isoformat(),
        "adults": request.adults, "rooms": request.rooms, "occupancies": None,
        "guest_nationality": None, "quoted_at": observed_at,
        # Neither search contract supplies an offer-expiration timestamp. The
        # local cache lifetime must never be presented as a supplier guarantee.
        "expires_at": None, "requires_revalidation": True,
        "availability_status": "quoted_not_reserved",
        "price_scope": "full_stay_all_requested_rooms",
        "refundable": None, "room_rates": [],
    }


def _liteapi_room_terms(rate: dict) -> dict:
    policy = rate.get("cancellationPolicies")
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("Invalid cancellation policy")
    policy = policy or {}
    tag = policy.get("refundableTag")
    refundable = True if tag == "RFN" else False if tag == "NRFN" else None
    penalties = policy.get("cancelPolicyInfos", [])
    if not isinstance(penalties, list) or len(penalties) > 100:
        raise ValueError("Invalid cancellation penalties")
    cancellations = []
    for penalty in penalties:
        money = _quoted_money(penalty)
        cancel_time = _terms_text(penalty.get("cancelTime"), 100)
        if not cancel_time:
            raise ValueError("Missing cancellation deadline")
        cancellations.append({"from": cancel_time, "timezone": _terms_text(penalty.get("timezone"), 100) or "GMT", "type": _terms_text(penalty.get("type"), 40), **money})
    hotel_remarks = policy.get("hotelRemarks", [])
    if not isinstance(hotel_remarks, list) or len(hotel_remarks) > 100:
        raise ValueError("Invalid cancellation remarks")
    cancellation_remarks = [_terms_text(remark) for remark in hotel_remarks]
    retail = rate.get("retailRate", {})
    if not isinstance(retail, dict):
        raise ValueError("Invalid room price")
    booking_total = None
    if "total" in retail:
        totals = retail["total"]
        if not isinstance(totals, list) or len(totals) != 1:
            raise ValueError("Invalid room total")
        booking_total = _quoted_money(totals[0])
    # Nuitee explicitly documents null as all fees included. An absent field
    # has no such guarantee and must not silently become zero excluded fees.
    disclosure = "missing" if "taxesAndFees" not in retail else "all_included"
    fees = retail.get("taxesAndFees")
    if fees is None:
        fees = []
    if not isinstance(fees, list) or len(fees) > 100:
        raise ValueError("Invalid room fees")
    additional_fees, normalized_fees = [], []
    for fee in fees:
        if not isinstance(fee, dict) or type(fee.get("included")) is not bool:
            raise ValueError("Unknown fee inclusion")
        normalized = {"included": fee["included"], **_fee_money(fee), "description": _terms_text(fee.get("description"))}
        normalized_fees.append(normalized)
        if fee["included"] is False and normalized["amount"] is not None and normalized["currency"] is not None:
            additional_fees.append({key: value for key, value in normalized.items() if key != "included"})
    if normalized_fees:
        disclosure = "itemized"
    return {
        "rate_id": _opaque_id(rate.get("rateId")),
        "occupancy_number": rate["occupancyNumber"], "adults": rate["adultCount"], "children": [],
        "room_name": _text(rate.get("name")), "board_type": _text(rate.get("boardType"), 40),
        "refundable": refundable, "cancellation_penalties": cancellations,
        "cancellation_remarks": cancellation_remarks,
        "remarks": _terms_text(rate.get("remarks")), "additional_fees": additional_fees,
        "booking_total": booking_total, "fees_disclosure": disclosure, "taxes_and_fees": normalized_fees,
    }


class RealDataProvider:
    def __init__(self, settings: Any, *, client: httpx.AsyncClient | None = None, now=None):
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(follow_redirects=False)
        self.timeout = min(20.0, float(getattr(settings, "request_timeout_seconds", 20)))
        self._public_gate = asyncio.Lock()
        self._public_last = 0.0
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def google_configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "google_maps_server_api_key", "")))

    @property
    def booking_configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "booking_demand_api_token", "")) and str(getattr(self.settings, "booking_affiliate_id", "")).strip())

    @property
    def liteapi_configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "liteapi_api_key", "")))

    @property
    def hotel_offer_provider(self) -> str | None:
        selected = getattr(self.settings, "hotel_offers_provider", "auto")
        if selected in ("booking", "liteapi"):
            return selected
        if self.liteapi_configured:
            return "liteapi"
        return "booking" if self.booking_configured else None

    @property
    def hotel_offer_source(self) -> str:
        return "Nuitee / LiteAPI" if self.hotel_offer_provider == "liteapi" else "Booking.com" if self.hotel_offer_provider == "booking" else "Hotel rates"

    @property
    def liteapi_sandbox(self) -> bool:
        key = secret_value(getattr(self.settings, "liteapi_api_key", ""))
        return getattr(self.settings, "liteapi_environment", "sandbox") != "live" or key.startswith(("sand_", "sandbox_"))

    @property
    def hotel_offers_configured(self) -> bool:
        if self.hotel_offer_provider == "booking":
            return self.booking_configured
        key = secret_value(getattr(self.settings, "liteapi_api_key", ""))
        return self.hotel_offer_provider == "liteapi" and not self.liteapi_sandbox and key.startswith("prod_")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _request(self, method: str, url: str, source: str, *, public: bool = False, **kwargs) -> dict:
        if safe_url(url) != url or urlsplit(url).scheme != "https":
            raise ProviderError(f"{source} requires a configured HTTPS endpoint.", provider=source, code="configuration")
        if public:
            async with self._public_gate:
                await asyncio.sleep(max(0, 1 - (time.monotonic() - self._public_last)))
                self._public_last = time.monotonic()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **kwargs.pop("headers", {})}
        from ..provider_budget import consume_call
        consume_call(source)
        try:
            # timeout() bounds the whole transfer, not only idle time between chunks.
            async with asyncio.timeout(self.timeout):
                async with self.client.stream(method, url, timeout=self.timeout, follow_redirects=False, headers=headers, **kwargs) as response:
                    if not 200 <= response.status_code < 300:
                        code = response.status_code
                        raise ProviderError(f"{source} request failed (HTTP {code}); no substitute data was used.", provider=source, code=f"http_{code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY:
                            raise _invalid(source)
            payload = json.loads(body, parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite number")))
            if not isinstance(payload, dict):
                raise ValueError("Object required")
            return payload
        except ProviderError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError(f"{source} timed out. Try again later.", provider=source, code="timeout") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"{source} could not be reached. Try again later.", provider=source, code="connection") from exc
        except (ValueError, TypeError) as exc:
            raise _invalid(source) from exc

    async def hotels(self, airport: dict) -> list[dict]:
        return await self._google_hotels(airport) if self.google_configured else await self._osm_hotels(airport)

    async def _osm_hotels(self, airport: dict) -> list[dict]:
        source = "OpenStreetMap"
        query = f'[out:json][timeout:15];nwr["tourism"~"^(hotel|guest_house|hostel|motel|apartment)$"]["name"](around:20000,{airport["latitude"]},{airport["longitude"]});out tags center 100;'
        payload = await self._request("POST", getattr(self.settings, "real_data_overpass_url", "https://overpass-api.de/api/interpreter"), source, public=True, data={"data": query})
        try:
            if payload.get("remark") or not isinstance(payload.get("elements"), list) or len(payload["elements"]) > 100:
                raise ValueError("Incomplete result")
            items = []
            seen = set()
            for row in payload["elements"]:
                if not isinstance(row, dict) or row.get("type") not in ("node", "way", "relation") or isinstance(row.get("id"), bool) or not isinstance(row.get("id"), int):
                    raise ValueError("Invalid map element")
                tags = row.get("tags", {})
                if not isinstance(tags, dict):
                    raise ValueError("Invalid tags")
                name = _text(tags.get("name"))
                if not name:
                    continue
                point = row if row["type"] == "node" else row.get("center", {})
                lat, lon = _number(point.get("lat"), -90, 90), _number(point.get("lon"), -180, 180)
                distance = _distance(lat, lon, airport)
                if distance > 21:
                    continue
                identity = (name.casefold(), round(lat, 4), round(lon, 4))
                if identity in seen:
                    continue
                seen.add(identity)
                parts = [_text(tags.get(key), 150) for key in ("addr:street", "addr:housenumber", "addr:city")]
                items.append({"id": f'osm-{row["type"]}-{row["id"]}', "name": name, "latitude": lat, "longitude": lon, "address": ", ".join(part for part in parts if part) or None, "website": safe_url(tags.get("website") or tags.get("contact:website")), "phone": _text(tags.get("phone") or tags.get("contact:phone"), 80), "distance_km": distance, "source_url": f'https://www.openstreetmap.org/{row["type"]}/{row["id"]}', "wheelchair": tags.get("wheelchair") if tags.get("wheelchair") in ("yes", "no", "limited") else None, "attributions": []})
            return sorted(items, key=lambda row: (row["distance_km"], row["name"]))[:10]
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            raise _invalid(source) from exc

    async def _google_hotels(self, airport: dict) -> list[dict]:
        source = "Google Places"
        payload = await self._request("POST", "https://places.googleapis.com/v1/places:searchNearby", source, headers={"X-Goog-Api-Key": secret_value(self.settings.google_maps_server_api_key), "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.formattedAddress,places.googleMapsUri,places.websiteUri,places.internationalPhoneNumber,places.accessibilityOptions,places.attributions"}, json={"includedTypes": ["hotel"], "maxResultCount": 10, "rankPreference": "DISTANCE", "locationRestriction": {"circle": {"center": {"latitude": airport["latitude"], "longitude": airport["longitude"]}, "radius": 20000.0}}})
        try:
            rows = payload.get("places", [])
            if "error" in payload or not isinstance(rows, list) or len(rows) > 10:
                raise ValueError("Invalid places")
            result = []
            for row in rows:
                lat, lon = _number(row["location"]["latitude"], -90, 90), _number(row["location"]["longitude"], -180, 180)
                name, identity = _text(row["displayName"]["text"]), _text(row["id"])
                source_url = safe_url(row.get("googleMapsUri"))
                if not name or not identity or not source_url:
                    raise ValueError("Missing place identity")
                access = row.get("accessibilityOptions", {}).get("wheelchairAccessibleEntrance")
                credits = row.get("attributions", [])
                if not isinstance(credits, list) or len(credits) > 30:
                    raise ValueError("Invalid source attributions")
                attributions = [{"display_name": _text(credit.get("provider")), "uri": safe_url(credit.get("providerUri"))} for credit in credits]
                result.append({"id": identity, "name": name, "latitude": lat, "longitude": lon, "address": _text(row.get("formattedAddress")), "website": safe_url(row.get("websiteUri")), "phone": _text(row.get("internationalPhoneNumber"), 80), "distance_km": _distance(lat, lon, airport), "source_url": source_url, "wheelchair": "yes" if access is True else "no" if access is False else None, "attributions": [credit for credit in attributions if credit["display_name"]]})
            return result
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise _invalid(source) from exc

    async def route(self, airport: dict, destination: dict) -> list[dict]:
        google = self.google_configured
        source = "Google Routes" if google else "OSRM / OpenStreetMap"
        if google:
            def point(item):
                return {"location": {"latLng": {"latitude": item["latitude"], "longitude": item["longitude"]}}}
            payload = await self._request("POST", "https://routes.googleapis.com/directions/v2:computeRoutes", source, headers={"X-Goog-Api-Key": secret_value(self.settings.google_maps_server_api_key), "X-Goog-FieldMask": "routes.distanceMeters,routes.duration"}, json={"origin": point(airport), "destination": point(destination), "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE", "computeAlternativeRoutes": False, "units": "METRIC"})
        else:
            coordinates = f'{airport["longitude"]},{airport["latitude"]};{destination["longitude"]},{destination["latitude"]}'
            base = getattr(self.settings, "real_data_osrm_url", "https://router.project-osrm.org").rstrip("/")
            payload = await self._request("GET", f"{base}/route/v1/driving/{coordinates}", source, public=True, params={"overview": "false", "alternatives": "false", "steps": "false"})
        try:
            if (not google and payload.get("code") != "Ok") or "error" in payload:
                raise ValueError("Routing failed")
            routes = payload.get("routes")
            if not isinstance(routes, list) or len(routes) > 5:
                raise ValueError("Invalid routes")
            if not routes:
                return []
            row = routes[0]
            if google:
                duration = row.get("duration")
                if not isinstance(duration, str) or not re.fullmatch(r"\d+(?:\.\d+)?s", duration):
                    raise ValueError("Invalid duration")
                seconds = _number(Decimal(duration[:-1]), 0, 7*86400)
                meters = _number(row["distanceMeters"], 0, 20_000_000)
            else:
                seconds, meters = _number(row["duration"], 0, 7*86400), _number(row["distance"], 0, 20_000_000)
            if google:
                link = "https://www.google.com/maps/dir/?" + urlencode({"api": 1, "origin": f'{airport["latitude"]},{airport["longitude"]}', "destination": f'{destination["latitude"]},{destination["longitude"]}', "travelmode": "driving"})
            else:
                link = "https://www.openstreetmap.org/directions?" + urlencode({"engine": "fossgis_osrm_car", "route": f'{airport["latitude"]},{airport["longitude"]};{destination["latitude"]},{destination["longitude"]}'})
            return [{"id": f'{airport["code"]}-{destination["code"]}', "origin": airport["code"], "destination": destination["code"], "distance_km": round(meters/1000, 1), "duration_minutes": round(seconds/60, 1), "source_url": link, "traffic": "live" if google else "none", "vehicle_profile": "driving"}]
        except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
            raise _invalid(source) from exc

    async def dated_route(self, origin: dict, destination: dict, pickup_at: datetime) -> dict:
        """A Google prediction for the exact requested future pickup, not a vehicle.

        Source durations retain their decimal seconds. Operation-minute rounding,
        terminal identity and any road-to-meeting-point connection belong to the
        importer. Road-snapped endpoints never replace requested coordinates.
        Contract: developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes
        """
        source = "Google Routes"
        def coordinates(value):
            if not isinstance(value, dict):
                raise ValueError("Coordinates required")
            return {"latitude": _number(value["latitude"], -90, 90),
                    "longitude": _number(value["longitude"], -180, 180)}
        try:
            start, end = coordinates(origin), coordinates(destination)
            if start == end or not isinstance(pickup_at, datetime) or pickup_at.tzinfo is None or pickup_at.utcoffset() is None:
                raise ValueError("Distinct coordinates and an aware pickup are required")
            departure = pickup_at.astimezone(timezone.utc)
            if departure <= self._now():
                raise ValueError("Pickup must be future")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("Supply distinct finite coordinates and a future pickup datetime with a UTC offset; no route request was sent.", provider=source, code="invalid_request") from exc
        if not self.google_configured:
            raise ProviderError("GOOGLE_MAPS_SERVER_API_KEY is missing; dated driving predictions are unavailable.", provider=source, code="not_configured")
        departure_text = departure.isoformat().replace("+00:00", "Z")
        payload = await self._request("POST", "https://routes.googleapis.com/directions/v2:computeRoutes", source,
            headers={"X-Goog-Api-Key": secret_value(self.settings.google_maps_server_api_key),
                     "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.startLocation,routes.legs.endLocation,routes.warnings,routes.polyline.encodedPolyline,fallbackInfo"},
            json={"origin": {"location": {"latLng": start}}, "destination": {"location": {"latLng": end}},
                  "departureTime": departure_text, "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE",
                  "computeAlternativeRoutes": False, "polylineQuality": "OVERVIEW", "units": "METRIC"})
        fetched_at = self._now().astimezone(timezone.utc)
        if fetched_at >= departure:
            raise ProviderError("The requested pickup elapsed before Google returned the route; no current-time substitute was requested.", provider=source, code="pickup_elapsed")
        if "fallbackInfo" in payload:
            raise ProviderError("Google returned a fallback computation instead of the requested dated traffic-aware route; it cannot support this planning leg.", provider=source, code="routing_fallback")
        try:
            if "error" in payload:
                raise ValueError("Provider error")
            # Protobuf JSON may omit an empty repeated routes field.
            routes = payload.get("routes", [])
            if not isinstance(routes, list):
                raise ValueError("Invalid route list")
            if not routes:
                raise ProviderError("Google found no driving route for those endpoints and pickup time.", provider=source, code="no_route")
            if len(routes) != 1 or not isinstance(routes[0], dict):
                raise ValueError("Exactly one route required")
            row = routes[0]
            duration = row.get("duration")
            if not isinstance(duration, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,9})?s", duration) or not 0 < Decimal(duration[:-1]) <= 7*86400:
                raise ValueError("Invalid duration")
            meters = row.get("distanceMeters")
            if type(meters) is not int or not 0 < meters <= 20_000_000:
                raise ValueError("Invalid distance")
            legs = row.get("legs")
            if not isinstance(legs, list) or len(legs) != 1:
                raise ValueError("Exactly one road leg required")
            road_start = coordinates(legs[0]["startLocation"]["latLng"])
            road_end = coordinates(legs[0]["endLocation"]["latLng"])
            warnings = row.get("warnings", [])
            if not isinstance(warnings, list) or len(warnings) > 20 or any(not isinstance(item, str) or not item.strip() or len(item) > 2048 for item in warnings):
                raise ValueError("Invalid route warnings")
            polyline = row.get("polyline", {})
            if not isinstance(polyline, dict):
                raise ValueError("Invalid polyline object")
            encoded = polyline.get("encodedPolyline")
            if encoded is not None and (not isinstance(encoded, str) or not 1 <= len(encoded) <= 100_000 or any(not 63 <= ord(char) <= 126 for char in encoded)):
                raise ValueError("Invalid encoded polyline")
            return {"source": source, "source_url": "https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes",
                "fetched_at": fetched_at.isoformat(),
                "request": {"origin": start, "destination": end, "departure_time": departure_text,
                            "travel_mode": "DRIVE", "routing_preference": "TRAFFIC_AWARE", "polyline_quality":"OVERVIEW"},
                "duration_seconds": duration[:-1], "distance_meters": meters, "duration_guaranteed": False,
                "road_endpoints": {"origin": road_start, "destination": road_end}, "warnings": warnings, "encoded_polyline":encoded}
        except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
            raise _invalid(source) from exc

    async def offers(self, airport: dict, request: Any) -> list[dict]:
        if self.hotel_offer_provider == "liteapi":
            return await self._liteapi_offers(airport, request)
        if self.hotel_offer_provider != "booking":
            raise ProviderError("Connect a hotel-rate provider to view prices and availability.", provider="Hotel rates", code="not_configured")
        return await self._booking_offers(airport, request)

    async def hotel_location(self, hotel_id: str) -> dict:
        """Fetch exact property geography and stay policy, never airport defaults."""
        from ..hotel_policy import source_stay_policy
        source = "Nuitee / LiteAPI"
        key = secret_value(self.settings.liteapi_api_key)
        if self.hotel_offer_provider != "liteapi" or self.liteapi_sandbox or not key.startswith("prod_"):
            raise ProviderError("Production hotel metadata is not configured.", provider=source, code="not_configured")
        _opaque_id(hotel_id, required=True)
        payload = await self._request("GET", "https://api.liteapi.travel/v3.0/data/hotel", source,
            headers={"X-API-Key": key}, params={"hotelId": hotel_id})
        try:
            data = payload["data"]
            if payload.get("error") or data["id"] != hotel_id:
                raise ValueError("Property identity changed")
            location = data["location"]
            received_at = datetime.now(timezone.utc).isoformat()
            def postal_text(field, maximum):
                value = data.get(field)
                return value.strip() if isinstance(value, str) and value.strip() and len(value) <= maximum and not any(ord(char) < 32 for char in value) else None
            return {"latitude": _number(location["latitude"], -90, 90),
                    "longitude": _number(location["longitude"], -180, 180),
                    "location_source": source, "location_retrieved_at": received_at,
                    "postal_address": {"hotel_id": hotel_id, "name": postal_text("name", 500),
                        "address": postal_text("address", 2000), "city": postal_text("city", 200),
                        "country": postal_text("country", 100), "source": source, "retrieved_at": received_at},
                    "stay_policy": source_stay_policy(hotel_id, data.get("checkinCheckoutTimes"), received_at)}
        except (ValueError, TypeError, KeyError) as exc:
            raise _invalid(source) from exc

    async def hotel_offers(self, hotel_id: str, request: Any) -> list[dict]:
        """One exact property; never substitute a nearby result or perform discovery."""
        if self.hotel_offer_provider != "liteapi":
            raise ProviderError("Exact property rates require production LiteAPI.", provider="Nuitee / LiteAPI", code="not_configured")
        _opaque_id(hotel_id, required=True)
        return await self._liteapi_offers({}, request, hotel_id=hotel_id)

    async def _liteapi_offers(self, airport: dict, request: Any, *, hotel_id: str | None = None) -> list[dict]:
        source = "Nuitee / LiteAPI"
        if not self.liteapi_configured:
            raise ProviderError("Connect your Nuitee / LiteAPI account to view prices and availability.", provider=source, code="not_configured")
        if self.liteapi_sandbox:
            raise ProviderError("Nuitee / LiteAPI is in sandbox mode. Test prices are not live offers.", provider=source, code="sandbox")
        key = secret_value(self.settings.liteapi_api_key)
        if not key.startswith("prod_"):
            raise ProviderError("A production Nuitee / LiteAPI key is required for live prices.", provider=source, code="configuration")
        # Each occupancy object represents one room; no adult is dropped or duplicated.
        quotient, remainder = divmod(request.adults, request.rooms)
        occupancies = [{"adults": quotient + (index < remainder), "children": []} for index in range(request.rooms)]
        payload = await self._request("POST", "https://api.liteapi.travel/v3.0/hotels/rates", source,
            headers={"X-API-Key": key}, json={
                **({"hotelIds": [hotel_id]} if hotel_id is not None else
                   {"latitude": airport["latitude"], "longitude": airport["longitude"], "radius": 20000}),
                "checkin": request.checkin.isoformat(), "checkout": request.checkout.isoformat(),
                "currency": "EUR", "guestNationality": getattr(self.settings, "liteapi_guest_nationality", "RO"),
                "occupancies": occupancies, "includeHotelData": True,
                "maxRatesPerHotel": 1, "limit": 1 if hotel_id is not None else 10, "timeout": 8,
            })
        observed_at = datetime.now(timezone.utc).isoformat()
        # The rates schema makes sandbox optional; production responses may omit it.
        # The environment and production key were checked before I/O. An explicit
        # response flag must still be a real boolean, never a truthy/falsy coercion.
        if payload.get("sandbox") is True:
            raise ProviderError("Nuitee / LiteAPI returned sandbox data. Test prices are not live offers.", provider=source, code="sandbox")
        if "sandbox" in payload and payload["sandbox"] is not False:
            raise ProviderError("Nuitee / LiteAPI returned an invalid response environment.", provider=source, code="unverified_environment")
        if payload.get("error"):
            error = payload["error"]
            if isinstance(error, dict) and error.get("code") == 2001:
                return []
            raise ProviderError("Nuitee / LiteAPI could not return rates for this search.", provider=source, code="upstream_error")
        try:
            rows, hotels = payload.get("data"), payload.get("hotels")
            if not isinstance(rows, list) or len(rows) > 10:
                raise ValueError("Invalid hotel rates")
            if not rows:
                return []
            if not isinstance(hotels, list) or len(hotels) > 10:
                raise ValueError("Missing hotel details")
            names = {}
            locations = {}
            for hotel in hotels:
                if not isinstance(hotel, dict) or not _text(hotel.get("id")) or not _text(hotel.get("name")):
                    raise ValueError("Missing hotel identity")
                if hotel["id"] in names:
                    raise ValueError("Duplicate hotel")
                names[hotel["id"]] = _text(hotel["name"])
                if hotel.get("latitude") is not None and hotel.get("longitude") is not None:
                    locations[hotel["id"]] = {"latitude": _number(hotel["latitude"], -90, 90), "longitude": _number(hotel["longitude"], -180, 180)}
            result = []
            for hotel in rows:
                selected_hotel_id = hotel["hotelId"]
                if (not isinstance(selected_hotel_id, str) or selected_hotel_id not in names
                        or (hotel_id is not None and selected_hotel_id != hotel_id)):
                    raise ValueError("Rates without hotel name")
                room_types = hotel.get("roomTypes")
                if not isinstance(room_types, list) or not 1 <= len(room_types) <= 20:
                    raise ValueError("Incomplete room offers")
                candidates = []
                for offer in room_types:
                    _opaque_id(offer.get("offerId"), required=True)
                    rates = offer.get("rates")
                    if not isinstance(rates, list) or len(rates) != request.rooms:
                        raise ValueError("Incomplete room allocation")
                    seen = set()
                    for rate in rates:
                        index = rate.get("occupancyNumber")
                        if isinstance(index, bool) or not isinstance(index, int) or index in seen or not 1 <= index <= request.rooms:
                            raise ValueError("Invalid occupancy allocation")
                        seen.add(index)
                        adults, children = rate.get("adultCount"), rate.get("childCount", 0)
                        if isinstance(adults, bool) or not isinstance(adults, int) or adults != occupancies[index-1]["adults"] or isinstance(children, bool) or children != 0:
                            raise ValueError("Guest count changed in rate")
                        if rate.get("childrenAges", []) != []:
                            raise ValueError("Guest ages changed in rate")
                    price = offer.get("offerRetailRate")
                    if price is None:
                        # Older responses contain only per-room totals. Every
                        # requested occupancy was validated above before summing.
                        amount = Decimal(0)
                        for rate in rates:
                            totals = rate.get("retailRate", {}).get("total")
                            if not isinstance(totals, list) or len(totals) != 1:
                                raise ValueError("Incomplete room price")
                            amount += self._liteapi_amount(totals[0])
                        if amount > 1_000_000:
                            raise ValueError("Invalid aggregate room price")
                    else:
                        # Current OpenAPI uses an array; the official guide uses an object.
                        if isinstance(price, list):
                            if len(price) != 1:
                                raise ValueError("Ambiguous offer currency")
                            price = price[0]
                        amount = self._liteapi_amount(price)
                    room_rates = [_liteapi_room_terms(rate) for rate in sorted(rates, key=lambda rate: rate["occupancyNumber"])]
                    pricing = _liteapi_pricing(amount, room_rates)
                    candidates.append((amount, offer, room_rates, pricing))
                # Prefer a fully costed quote. A lower base price with unknown
                # extras cannot be called the cheapest mandatory-cost option.
                amount, selected, room_rates, pricing = min(candidates, key=lambda candidate: (
                    candidate[3]["status"] != "complete",
                    Decimal(candidate[3]["mandatory_total"]["amount"]) if candidate[3]["mandatory_total"] else candidate[0],
                ))
                refundable = False if any(rate["refundable"] is False for rate in room_rates) else True if all(rate["refundable"] is True for rate in room_rates) else None
                result.append({
                    **_quote_context(request, "liteapi", observed_at),
                    "id": selected_hotel_id, "name": names[selected_hotel_id], "total_price": format(amount, "f"), "currency": "EUR", "url": None,
                    "provider_offer_id": selected["offerId"], "occupancies": occupancies,
                    "guest_nationality": getattr(self.settings, "liteapi_guest_nationality", "RO"),
                    "refundable": refundable, "room_rates": room_rates, "pricing": pricing,
                    **locations.get(selected_hotel_id, {}),
                })
            return result
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as exc:
            raise _invalid(source) from exc

    @staticmethod
    def _liteapi_amount(price: Any) -> Decimal:
        if not isinstance(price, dict) or price.get("currency") != "EUR":
            raise ValueError("Invalid offer price currency")
        raw = price.get("amount")
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
            raise ValueError("Invalid offer price")
        amount = Decimal(str(raw))
        if not amount.is_finite() or not 0 <= amount <= 1_000_000:
            raise ValueError("Invalid offer price")
        return amount

    async def _booking_offers(self, airport: dict, request: Any) -> list[dict]:
        source = "Booking.com"
        affiliate = str(getattr(self.settings, "booking_affiliate_id", "")).strip()
        if not self.booking_configured or not re.fullmatch(r"[1-9]\d{0,19}", affiliate):
            raise ProviderError("Booking.com partner credentials are missing or invalid.", provider=source, code="configuration")
        headers = {"Authorization": "Bearer " + secret_value(self.settings.booking_demand_api_token), "X-Affiliate-Id": affiliate}
        base = "https://demandapi.booking.com/3.3/accommodations"
        payload = await self._request("POST", base + "/search", source, headers=headers, json={"coordinates": {"latitude": airport["latitude"], "longitude": airport["longitude"], "radius": 20}, "booker": {"country": getattr(self.settings, "booking_booker_country", "ro"), "platform": "desktop"}, "checkin": request.checkin.isoformat(), "checkout": request.checkout.isoformat(), "guests": {"number_of_adults": request.adults, "number_of_rooms": request.rooms}, "currency": "EUR", "rows": 10})
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            rows = payload.get("data")
            if payload.get("errors") or not isinstance(rows, list) or len(rows) > 10:
                raise ValueError("Invalid offers")
            if not rows:
                return []
            ids = []
            for row in rows:
                if not isinstance(row, dict) or isinstance(row.get("id"), bool) or not isinstance(row.get("id"), int) or row["id"] <= 0:
                    raise ValueError("Invalid accommodation ID")
                ids.append(row["id"])
            details = await self._request("POST", base + "/details", source, headers=headers, json={"accommodations": ids, "languages": ["en-gb"]})
            if details.get("errors") or not isinstance(details.get("data"), list) or len(details["data"]) > 10:
                raise ValueError("Invalid accommodation details")
            named = {}
            for row in details["data"]:
                if not isinstance(row, dict):
                    raise ValueError("Invalid property")
                name = row.get("name")
                named[row["id"]] = _text(name.get("en-gb") if isinstance(name, dict) else name)
            result = []
            for row in rows:
                price = row.get("price", {})
                # Full-stay total is kept distinct from per-night and base prices.
                total = price.get("total")
                currency = row.get("currency")
                if isinstance(total, dict):
                    total = total.get("booker_currency")
                    currency = currency.get("booker") if isinstance(currency, dict) else None
                if isinstance(total, bool) or not isinstance(total, (str, int, float, Decimal)):
                    raise ValueError("Missing full-stay price")
                amount = Decimal(str(total))
                if not amount.is_finite() or not 0 <= amount <= 1_000_000 or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                    raise ValueError("Invalid price")
                name = named.get(row["id"])
                if not name:
                    raise ValueError("Property name missing")
                result.append({**_quote_context(request, "booking", observed_at), "id": str(row["id"]), "name": name, "total_price": format(amount, "f"), "currency": currency, "url": safe_url(row.get("url"))})
            return result
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation) as exc:
            raise _invalid(source) from exc
