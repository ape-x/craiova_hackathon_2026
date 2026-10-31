"""Official dated offers from Nuitee, Duffel and Distribusion; no order/hold endpoints.

Contracts: duffel.com/docs/api/offer-requests/create-offer-request and
docs.distribusion.com/dt/api/api-endpoints/connections/connections-find.
Distribusion's per-account Offers flow must be enabled by its partner team.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..travel import TravelSearchRequest
from .base import ProviderError, secret_value
from .real_data import RealDataProvider, _invalid, _number, _opaque_id, _quoted_money, _terms_text, _text


def _list(value: Any, maximum: int = 1000) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Invalid provider list")
    return value


def _instant(value: Any, zone: str | None = None) -> datetime:
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Missing provider time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not zone:
            raise ValueError("Missing local time zone")
        tz = ZoneInfo(zone)
        first, second = parsed.replace(tzinfo=tz, fold=0), parsed.replace(tzinfo=tz, fold=1)
        if first.utcoffset() != second.utcoffset() or first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != parsed:
            raise ValueError("Ambiguous local time")
        parsed = first
    return parsed.astimezone(timezone.utc)


def _identity(value: Any) -> str:
    return _opaque_id(value, required=True)


@lru_cache(maxsize=1)
def _airport_timezones() -> dict[str, str]:
    # Offline, licensed reference data; never infer a zone from server location.
    return json.loads((Path(__file__).parents[1] / "data" / "airport_timezones.json").read_text())["timezones"]


def _provider_terms(value: Any, depth: int = 0) -> Any:
    """Retain provider conditions/baggage, including exact decimal amounts."""
    if depth > 8:
        raise ValueError("Excessive provider nesting")
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite provider amount")
        return str(value)
    if isinstance(value, str):
        return _terms_text(value)
    if isinstance(value, list):
        return [_provider_terms(item, depth+1) for item in _list(value, 100)]
    if isinstance(value, dict) and len(value) <= 100:
        return {_identity(key): _provider_terms(item, depth+1) for key, item in value.items()}
    raise ValueError("Invalid provider terms")


def _conditions(value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Invalid offer conditions")
    result = {}
    for key in ("refund_before_departure", "change_before_departure"):
        condition = value.get(key)
        if condition is None:
            continue
        if not isinstance(condition, dict) or type(condition.get("allowed")) is not bool:
            raise ValueError("Invalid offer condition")
        result[key] = {"allowed": condition["allowed"], "penalty": None}
        if condition.get("penalty_amount") is not None:
            result[key]["penalty"] = _quoted_money({"amount": condition["penalty_amount"], "currency": condition.get("penalty_currency")})
    return result


class TravelProvider(RealDataProvider):
    @property
    def _nuitee_live(self) -> bool:
        return secret_value(getattr(self.settings, "liteapi_api_key", "")).startswith("prod_") and getattr(self.settings, "liteapi_environment", "sandbox") == "live"

    @property
    def flight_source(self) -> str:
        selected = getattr(self.settings, "flight_offers_provider", "auto")
        if selected in ("nuitee", "duffel"):
            return "Nuitee" if selected == "nuitee" else "Duffel"
        if self._nuitee_live:
            return "Nuitee"
        if secret_value(getattr(self.settings, "duffel_access_token", "")):
            return "Duffel"
        return "Nuitee" if self.liteapi_configured else "Duffel"

    @property
    def flight_environment(self) -> str:
        if self.flight_source == "Nuitee":
            return "live" if self._nuitee_live else "sandbox"
        key = secret_value(getattr(self.settings, "duffel_access_token", ""))
        return "live" if getattr(self.settings, "duffel_environment", "sandbox") == "live" and not key.startswith("duffel_test_") else "sandbox"

    @property
    def flight_configured(self) -> bool:
        if self.flight_source == "Nuitee":
            return self._nuitee_live
        key = secret_value(getattr(self.settings, "duffel_access_token", ""))
        return bool(key) and not key.startswith("duffel_test_") and getattr(self.settings, "duffel_environment", "sandbox") == "live"

    @property
    def ground_configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "distribusion_api_key", ""))) and getattr(self.settings, "distribusion_environment", "sandbox") == "live" and getattr(self.settings, "distribusion_offers_enabled", False) is True

    async def search(self, request: TravelSearchRequest) -> dict:
        request = TravelSearchRequest.model_validate(request.model_dump())
        source = self.flight_source if request.kind == "flight" else "Distribusion"
        try:
            if source == "Nuitee":
                items = await self.nuitee_offers(request)
            else:
                items = await self.flight_offers(request) if request.kind == "flight" else await self.ground_offers(request)
        except ProviderError as exc:
            state = "sandbox" if exc.code == "sandbox" else "not_configured" if exc.code == "not_configured" else "error"
            return {"state": state, "source": source, "items": [], "message": str(exc)}
        return {"state": "ready", "source": source, "items": items, "message": None}

    def _credentials(self, source: str) -> str:
        flight = source == "Duffel"
        key = secret_value(getattr(self.settings, "duffel_access_token" if flight else "distribusion_api_key", ""))
        if not key:
            raise ProviderError(f"Connect a live {source} account to search dated offers.", provider=source, code="not_configured")
        environment = getattr(self.settings, "duffel_environment" if flight else "distribusion_environment", "sandbox")
        if environment != "live" or (flight and key.startswith("duffel_test_")):
            raise ProviderError(f"{source} is in sandbox mode. Test fares are not live offers.", provider=source, code="sandbox")
        if not flight and getattr(self.settings, "distribusion_offers_enabled", False) is not True:
            raise ProviderError("Ask Distribusion to enable its Offers API flow for this account.", provider=source, code="not_configured")
        return key

    @staticmethod
    def _base(request: TravelSearchRequest, source: str, identity: str, amount: str, currency: str, expires: datetime, observed: datetime, segments: list[dict]) -> dict:
        return {
            "id": identity, "provider": source.lower(), "provider_offer_id": identity,
            "origin": request.origin_code, "destination": request.destination_code,
            "departure_at": segments[0]["departure_at"], "arrival_at": segments[-1]["arrival_at"],
            "total_price": amount, "currency": currency, "passengers": request.adults,
            "expires_at": expires.isoformat(), "quoted_at": observed.isoformat(),
            "segments": segments, "accessibility_confirmed": False, "mode": "live",
            "requires_revalidation": True, "availability_status": "quoted_not_reserved",
            "price_scope": "all_requested_passengers", "url": None,
        }

    async def nuitee_offers(self, request: TravelSearchRequest) -> list[dict]:
        """Search only. Production Flights needs separate Nuitee approval.

        Official contract: docs.liteapi.travel/reference/post_flights-rates.
        Flights uses the Hotels key/environment, with no documented mandatory
        response live_mode flag. A test provider never becomes a live quote.
        """
        source = "Nuitee"
        if not self.liteapi_configured:
            raise ProviderError("Connect a production Nuitee account with Flights access.", provider=source, code="not_configured")
        if not self._nuitee_live:
            raise ProviderError("Nuitee requires a production key and live environment. Sandbox flights are not live offers.", provider=source, code="sandbox")
        ages = request.passenger_ages
        counts = {"adults": request.adults if ages is None else sum(age >= 12 for age in ages), "children": 0 if ages is None else sum(2 <= age < 12 for age in ages), "infants": 0 if ages is None else sum(age < 2 for age in ages)}
        if not counts["adults"]:
            raise ProviderError("Nuitee flight searches require at least one passenger aged 12 or older.", provider=source, code="invalid_passengers")
        body = {"legs": [{"origin": request.origin_code, "destination": request.destination_code, "date": request.departure_date.isoformat()}], **counts, "currency": "EUR", "cabinClass": "ECONOMY"}
        if ages is not None:
            body.update(childrenAges=[age for age in ages if 2 <= age < 12], infantAges=[age for age in ages if age < 2])
        try:
            payload = await self._request("POST", "https://api.liteapi.travel/v3.0/flights/rates", source, headers={"X-API-Key": secret_value(self.settings.liteapi_api_key)}, json=body)
        except ProviderError as exc:
            if exc.code in ("http_401", "http_403"):
                raise ProviderError("Nuitee Flights access was denied. Request production Flights API access in the Nuitee dashboard; hotel activation alone does not grant it.", provider=source, code=exc.code) from exc
            raise
        observed = datetime.now(timezone.utc)
        try:
            # Not required by the flight schema; if returned, contradictory
            # environment flags must still fail closed.
            if payload.get("sandbox", False) is not False or payload.get("live_mode", True) is not True:
                raise ProviderError("Nuitee returned a sandbox or unverified response.", provider=source, code="sandbox")
            if payload.get("error") or payload.get("errors"):
                raise ValueError("Provider search error")
            result = []
            seen = set()
            for batch in _list(payload["data"], 100):
                for journey in _list(batch["journeys"], 1000):
                    parameters = journey["parameters"]
                    if any(type(parameters.get(key)) is not int for key in counts):
                        raise ValueError("Passenger composition changed")
                    # Some production batches leave all three echoed counts
                    # at zero. Accept that omission only if the actual quote
                    # independently reconciles against every requested pax.
                    omitted_composition = all(parameters[key] == 0 for key in counts)
                    if not omitted_composition and any(parameters[key] != value for key, value in counts.items()):
                        raise ValueError("Passenger composition changed")
                    raw_segments = _list(journey["segments"], 10)
                    if not raw_segments:
                        raise ValueError("Missing itinerary")
                    segments = []
                    previous_arrival = None
                    for segment in raw_segments:
                        origin, destination = _identity(segment["originCode"]), _identity(segment["destinationCode"])
                        departure = _instant(segment["departureTime"], _airport_timezones().get(origin))
                        arrival = _instant(segment["arrivalTime"], _airport_timezones().get(destination))
                        if segment.get("direction", "OUTBOUND") != "OUTBOUND" or arrival <= departure or (previous_arrival and departure < previous_arrival) or (segments and segments[-1]["destination"] != origin):
                            raise ValueError("Unexpected or disconnected itinerary")
                        carrier = segment["carrier"]
                        if any(str(carrier.get(key, "")).casefold() == "nuitee air" for key in ("marketingName", "operatingName")):
                            raise ProviderError("Nuitee returned its test airline.", provider=source, code="sandbox")
                        stop_count = segment.get("stopCount")
                        if stop_count is not None and (type(stop_count) is not int or stop_count < 0):
                            raise ValueError("Invalid technical stop count")
                        segments.append({"id": _identity(segment["segmentKey"]), "mode": "flight", "origin": origin, "destination": destination, "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(), "departure_local": segment["departureTime"], "arrival_local": segment["arrivalTime"], "carrier": _text(carrier.get("operatingName") or carrier.get("marketingName")), "flight_number": _text(segment["flight"].get("marketingNumber"), 20), "technical_stop_count": stop_count, "stops": _provider_terms(segment.get("stops"))})
                        previous_arrival = arrival
                    if segments[0]["origin"] != request.origin_code or segments[-1]["destination"] != request.destination_code or raw_segments[0]["departureTime"][:10] != request.departure_date.isoformat():
                        raise ValueError("Route or date changed")
                    if _instant(segments[0]["departure_at"]) <= observed:
                        continue
                    for offer in _list(journey["offers"], 100):
                        upstream_provider = offer.get("provider")
                        if upstream_provider is not None and not isinstance(upstream_provider, dict):
                            raise ValueError("Invalid upstream provider")
                        provider_code = _identity(upstream_provider["code"]) if upstream_provider and upstream_provider.get("code") else None
                        if (provider_code and provider_code.upper() == "E2E") or offer.get("sandbox", False) is not False or offer.get("live_mode", True) is not True:
                            raise ProviderError("Nuitee returned a test or unverified offer.", provider=source, code="sandbox")
                        expiry = _instant(offer["expiration"])
                        if expiry <= observed:
                            continue
                        price = offer["pricing"]["display"]
                        money = _quoted_money({"amount": price["total"], "currency": price["currency"]})
                        if money["currency"] != "EUR":
                            raise ValueError("Requested currency changed")
                        if omitted_composition:
                            components = ("base", "fees", "taxes")
                            expected_components = {key: Decimal(0) for key in components}
                            for category, count in counts.items():
                                if count:
                                    breakdown = price["perPassenger"][category[:-1] if category != "children" else "child"]
                                    passenger_money = _quoted_money({"amount": breakdown["total"], "currency": breakdown["currency"]})
                                    if passenger_money["currency"] != money["currency"]:
                                        raise ValueError("Passenger currency changed")
                                    amounts = {key: Decimal(_quoted_money({"amount": breakdown[key], "currency": breakdown["currency"]})["amount"]) for key in components}
                                    if abs(sum(amounts.values()) - Decimal(passenger_money["amount"])) > Decimal("0.015"):
                                        raise ValueError("Passenger price components do not reconcile")
                                    for key in components:
                                        expected_components[key] += amounts[key] * count
                            # FX conversion rounds base/fees/taxes separately.
                            # Reconcile each component using its EUR half-cent
                            # bound, not a wider arbitrary tolerance on total.
                            tolerance = Decimal("0.005") * (sum(counts.values()) + 1)
                            actual_components = {key: Decimal(_quoted_money({"amount": price[key], "currency": price["currency"]})["amount"]) for key in components}
                            if any(abs(actual_components[key] - expected_components[key]) > tolerance for key in components) or abs(sum(actual_components.values()) - Decimal(money["amount"])) > Decimal("0.015"):
                                raise ValueError("Group total does not match requested passenger breakdown")
                        fare = offer.get("fare") or {}
                        seats = fare.get("seatsRemaining")
                        if seats is not None and (type(seats) is not int or seats < 0):
                            raise ValueError("Invalid reported seats")
                        if seats is not None and seats < counts["adults"] + counts["children"]:
                            continue
                        segment_fares = _list(offer.get("segmentFares", []), 10)
                        insufficient = False
                        for segment_fare in segment_fares:
                            if segment_fare["segmentKey"] not in {segment["id"] for segment in segments}:
                                raise ValueError("Fare refers to an unknown segment")
                            segment_seats = segment_fare.get("seatsRemaining")
                            if segment_seats is not None and (type(segment_seats) is not int or segment_seats < 0):
                                raise ValueError("Invalid segment seats")
                            insufficient |= segment_seats is not None and segment_seats < counts["adults"] + counts["children"]
                        if insufficient:
                            continue
                        identity = _identity(offer["offerId"])
                        if identity in seen:
                            raise ValueError("Duplicate offer identity")
                        seen.add(identity)
                        result.append({**self._base(request, source, identity, money["amount"], money["currency"], expiry, observed, segments), "provider_journey_id": _identity(journey["journeyKey"]), "content_provider": provider_code, "conditions": _provider_terms(offer.get("terms")), "baggage": _provider_terms(offer.get("baggage")), "segment_fares": _provider_terms(offer.get("segmentFares", [])), "price_breakdown": _provider_terms(price), "reported_seats_left": seats, "passenger_composition_source": "request_reconciled_with_price_breakdown" if omitted_composition else "provider", "reported_passenger_composition": {key: parameters[key] for key in counts}})
            return sorted(result, key=lambda row: Decimal(row["total_price"]))[:10]
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, ZoneInfoNotFoundError) as exc:
            raise _invalid(source) from exc

    async def flight_offers(self, request: TravelSearchRequest) -> list[dict]:
        source = "Duffel"
        key = self._credentials(source)
        passengers = [{"type": "adult"} for _ in range(request.adults)] if request.passenger_ages is None else [{"age": age} for age in request.passenger_ages]
        # A bounded search, not an order. The carrier timeout is below the
        # total HTTP timeout and may omit slower carrier responses.
        payload = await self._request("POST", "https://api.duffel.com/air/offer_requests", source,
            headers={"Authorization": "Bearer " + key, "Duffel-Version": "v2"},
            params={"return_offers": "true", "supplier_timeout": 10000},
            json={"data": {"slices": [{"origin": request.origin_code, "destination": request.destination_code, "departure_date": request.departure_date.isoformat()}], "passengers": passengers, "cabin_class": "economy"}})
        observed = datetime.now(timezone.utc)
        try:
            data = payload["data"]
            if not isinstance(data, dict) or data.get("live_mode") is not True:
                raise ProviderError("Duffel did not return a verified live search.", provider=source, code="sandbox" if isinstance(data, dict) and data.get("live_mode") is False else "unverified_environment")
            search_id = _identity(data["id"])
            requested_passengers = _list(data["passengers"], 30)
            requested_ids = {_identity(p["id"]) for p in requested_passengers}
            if len(requested_passengers) != request.adults or len(requested_ids) != request.adults:
                raise ValueError("Passenger count changed")
            if request.passenger_ages is not None and (any(type(p.get("age")) is not int for p in requested_passengers) or sorted(p["age"] for p in requested_passengers) != sorted(request.passenger_ages)):
                raise ValueError("Passenger ages changed")
            result = []
            for offer in _list(data["offers"], 1000):
                if offer.get("live_mode") is not True:
                    raise ProviderError("Duffel returned an unverified offer environment.", provider=source, code="sandbox" if offer.get("live_mode") is False else "unverified_environment")
                owner = offer["owner"]
                if owner.get("iata_code") == "ZZ" or owner.get("name") == "Duffel Airways":
                    raise ProviderError("Duffel returned a sandbox airline.", provider=source, code="sandbox")
                actual_passengers = _list(offer["passengers"], 30)
                actual_ids = {_identity(p["id"]) for p in actual_passengers}
                if len(actual_passengers) != request.adults or actual_ids != requested_ids:
                    raise ValueError("Incomplete passenger quote")
                expiry = _instant(offer["expires_at"])
                if expiry <= observed:
                    continue
                slices = _list(offer["slices"], 1)
                if len(slices) != 1:
                    raise ValueError("Unexpected itinerary")
                raw_segments = _list(slices[0]["segments"], 10)
                if not raw_segments:
                    raise ValueError("Missing flight segments")
                segments = []
                previous_arrival = None
                for segment in raw_segments:
                    origin, destination = segment["origin"], segment["destination"]
                    departure = _instant(segment["departing_at"], origin.get("time_zone"))
                    arrival = _instant(segment["arriving_at"], destination.get("time_zone"))
                    if arrival <= departure or (previous_arrival and departure < previous_arrival):
                        raise ValueError("Invalid flight chronology")
                    if segments and segments[-1]["destination"] != origin["iata_code"]:
                        raise ValueError("Disconnected flight itinerary")
                    segments.append({"id": _identity(segment["id"]), "mode": "flight", "origin": _identity(origin["iata_code"]), "destination": _identity(destination["iata_code"]), "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(), "carrier": _text(segment.get("operating_carrier", owner).get("name")), "flight_number": _text(segment.get("marketing_carrier_flight_number"), 20)})
                    previous_arrival = arrival
                if segments[0]["origin"] != request.origin_code or segments[-1]["destination"] != request.destination_code or raw_segments[0]["departing_at"][:10] != request.departure_date.isoformat():
                    raise ValueError("Flight does not match requested route or date")
                if _instant(segments[0]["departure_at"]) <= observed:
                    continue
                money = _quoted_money({"amount": offer["total_amount"], "currency": offer["total_currency"]})
                identity = _identity(offer["id"])
                result.append({**self._base(request, source, identity, money["amount"], money["currency"], expiry, observed, segments), "provider_search_id": search_id, "conditions": _conditions(offer.get("conditions")), "reported_seats_left": None})
            # Compare only within a currency; do not invent exchange rates.
            return sorted(result, key=lambda row: (row["currency"], Decimal(row["total_price"])))[:10]
        except ProviderError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, ZoneInfoNotFoundError) as exc:
            raise _invalid(source) from exc

    async def ground_offers(self, request: TravelSearchRequest) -> list[dict]:
        source = "Distribusion"
        key = self._credentials(source)
        body = {"locale": "en", "currency": "EUR", "number_of_connections": 10, "search_bounds": [{"is_requested": True, "departure_date": request.departure_date.isoformat(), "departure_location": {"type": "iata", "value": request.origin_code}, "arrival_location": {"type": "iata", "value": request.destination_code}}]}
        if request.passenger_ages is None:
            body["pax"] = request.adults
        else:
            body["passengers"] = [{"pax": count, "max_age": age} for age, count in sorted(Counter(request.passenger_ages).items())]
        payload = await self._request("POST", "https://api.distribusion.com/retailers/v4/connections/find", source, headers={"Api-Key": key}, json=body)
        observed = datetime.now(timezone.utc)
        try:
            if payload.get("errors") or payload.get("meta", {}).get("currency") != "EUR":
                raise ValueError("Search failed or currency changed")
            rows = _list(payload["data"], 100)
            if not rows:
                return []
            indexed = {}
            for entity in _list(payload["included"], 5000):
                identity = (entity["type"], _identity(entity["id"]))
                if identity in indexed:
                    raise ValueError("Duplicate provider identity")
                indexed[identity] = entity
            passenger_nodes = [v for (kind, _), v in indexed.items() if kind == "passengers"]
            passenger_ids = {p["id"] for p in passenger_nodes}
            if len(passenger_ids) != request.adults:
                raise ValueError("Incomplete passenger quote")
            if request.passenger_ages is not None and (any(type(p["attributes"].get("age")) is not int for p in passenger_nodes) or sorted(p["attributes"]["age"] for p in passenger_nodes) != sorted(request.passenger_ages)):
                raise ValueError("Passenger ages changed")

            def related(entity, key, plural=False):
                refs = entity["relationships"][key]["data"]
                refs = _list(refs, 100) if plural else [refs]
                values = [indexed[(ref["type"], ref["id"])] for ref in refs]
                return values if plural else values[0]

            def station(entity, key):
                node = related(entity, key)
                attr = node["attributes"]
                return {"id": node["id"], "name": _terms_text(attr["name"], 1000), "time_zone": attr.get("time_zone"), "latitude": _number(attr["latitude"], -90, 90), "longitude": _number(attr["longitude"], -180, 180)}

            result = []
            for connection in rows:
                if connection.get("type") != "connections":
                    raise ValueError("Unexpected provider resource")
                if connection["attributes"].get("booked_out") is not False:
                    continue
                raw_segments = related(connection, "segments", plural=True)
                raw_segments.sort(key=lambda segment: segment["attributes"]["index"])
                if not raw_segments:
                    raise ValueError("Missing ground segments")
                segments = []
                previous_arrival = None
                for segment in raw_segments:
                    vehicle = related(segment, "vehicle")
                    vehicle_type = related(vehicle, "vehicle_type")["attributes"]["code"]
                    # This endpoint is also a rail/ferry API. Only explicit bus
                    # segments belong in a bus search, never relabel a train.
                    if vehicle_type != "BUS":
                        segments = []
                        break
                    origin, destination = station(segment, "departure_station"), station(segment, "arrival_station")
                    attr = segment["attributes"]
                    departure, arrival = _instant(attr["departure_time"], origin["time_zone"]), _instant(attr["arrival_time"], destination["time_zone"])
                    if arrival <= departure or (previous_arrival and departure < previous_arrival) or (segments and segments[-1]["destination"] != origin["id"]):
                        raise ValueError("Invalid ground chronology")
                    carrier = related(segment, "operating_carrier")["attributes"]
                    segments.append({"id": segment["id"], "mode": "bus", "origin": origin["id"], "destination": destination["id"], "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(), "departure_station": origin, "arrival_station": destination, "carrier": _terms_text(carrier.get("trade_name") or carrier.get("legal_name"), 1000), "service_number": _text(attr.get("vehicle_number"), 100)})
                    previous_arrival = arrival
                if not segments:
                    continue
                if raw_segments[0]["attributes"]["departure_time"][:10] != request.departure_date.isoformat():
                    raise ValueError("Ground departure date changed")
                if _instant(segments[0]["departure_at"]) <= observed:
                    continue
                segment_ids = {s["id"] for s in segments}
                for fare in related(connection, "fares", plural=True):
                    attr = fare["attributes"]
                    if attr.get("booking_type") != "standalone" or attr.get("booked_out") is not False:
                        continue
                    if attr.get("passenger_coverage_type") != "collective" or attr.get("segment_coverage_type") != "collective":
                        continue
                    quoted_passengers = _list(attr.get("passenger_ids"), 30)
                    quoted_segments = _list(attr.get("segment_ids"), 10)
                    if len(quoted_passengers) != request.adults or set(quoted_passengers) != passenger_ids or len(quoted_segments) != len(segment_ids) or set(quoted_segments) != segment_ids:
                        continue
                    expiry = _instant(attr["offer_expires_at"])
                    if expiry <= observed:
                        continue
                    seats = attr.get("seats_left")
                    if seats is not None and (type(seats) is not int or seats < 0):
                        raise ValueError("Invalid reported seats")
                    if seats is not None and seats < request.adults:
                        continue
                    cents = attr["price"]
                    if isinstance(cents, bool) or not isinstance(cents, (int, float, Decimal)) or Decimal(str(cents)) != Decimal(str(cents)).to_integral_value():
                        raise ValueError("Invalid minor-unit fare")
                    money = _quoted_money({"amount": Decimal(str(cents)) / 100, "currency": "EUR"})
                    identity = _identity(attr["offer_id"])
                    fare_class = related(fare, "fare_class") if fare["relationships"]["fare_class"]["data"] is not None else None
                    conditions = [{"id": c["id"], "description": _terms_text(c["attributes"].get("description")), "refundable": c["attributes"].get("refundable"), "exchangeable": c["attributes"].get("exchangeable")} for c in related(fare, "conditions", plural=True)]
                    result.append({**self._base(request, source, identity, money["amount"], "EUR", expiry, observed, segments), "provider_connection_id": connection["id"], "fare_name": _terms_text(fare_class["attributes"]["name"], 1000) if fare_class else None, "conditions": conditions, "reported_seats_left": seats})
            return sorted(result, key=lambda row: Decimal(row["total_price"]))[:10]
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, ZoneInfoNotFoundError) as exc:
            raise _invalid(source) from exc
