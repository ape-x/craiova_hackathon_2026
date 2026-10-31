"""Pure Google Routes TRANSIT request/response adapter; no network or inventory.

Official contracts:
https://developers.google.com/maps/documentation/routes/transit-route
https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes

These are reference journeys, not quotes, seat availability or reservations.
The caller must authenticate, enforce dispatch budgets, display Google Maps and
agency attribution/warnings, and apply provider retention policy. This module
does not persist responses or turn schedules into allocatable Resources.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SOURCE = "Google Maps"
DOCS_URL = "https://developers.google.com/maps/documentation/routes/transit-route"
FIELD_MASK = ",".join((
    "routes.duration", "routes.distanceMeters", "routes.polyline.encodedPolyline",
    "routes.description", "routes.warnings", "routes.travelAdvisory.transitFare",
    "routes.legs.steps.travelMode", "routes.legs.steps.staticDuration",
    "routes.legs.steps.distanceMeters", "routes.legs.steps.startLocation",
    "routes.legs.steps.endLocation", "routes.legs.steps.transitDetails",
))
WALKING_WARNING = "Walking directions may omit suitable pedestrian paths. Verify the route and local conditions before travel."


def _text(value, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        return None
    return value.strip()


def _aware(value: datetime):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timestamp with UTC offset is required")
    return value


class TransitLocation(BaseModel):
    """Coordinates carry their actual catalog/property/operator provenance."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    label: str = Field(min_length=1, max_length=300)
    latitude: float = Field(ge=-90, le=90, strict=True, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, strict=True, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=300)

    @field_validator("label", "source")
    @classmethod
    def clean_text(cls, value):
        clean = _text(value, 300)
        if clean is None:
            raise ValueError("Location label and coordinate source are required")
        return clean

    def waypoint(self):
        return {"location": {"latLng": {"latitude": self.latitude, "longitude": self.longitude}}}


class TransitRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    origin: TransitLocation
    destination: TransitLocation
    departure_at: datetime
    max_walking_minutes: float = Field(ge=0, le=240, strict=True, allow_inf_nan=False)

    @field_validator("departure_at", mode="before")
    @classmethod
    def explicit_timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply a departure timestamp with UTC offset")
        return value

    @model_validator(mode="after")
    def explicit_journey(self):
        _aware(self.departure_at)
        if (self.origin.latitude, self.origin.longitude) == (self.destination.latitude, self.destination.longitude):
            raise ValueError("Origin and destination must differ")
        return self


def build_transit_request(request: TransitRouteRequest, *, now: datetime):
    """Build a body only. The caller supplies its clock immediately before dispatch."""
    request = TransitRouteRequest.model_validate(request.model_dump())
    if request.departure_at <= _aware(now):
        raise ValueError("Choose a future transit departure; no request was built")
    return {
        "origin": request.origin.waypoint(), "destination": request.destination.waypoint(),
        "travelMode": "TRANSIT", "departureTime": request.departure_at.astimezone(timezone.utc).isoformat(),
        "computeAlternativeRoutes": True,
        # This is only a provider preference. The hard walking limit is evaluated
        # against returned steps, never assumed to be enforced by Google.
        "transitPreferences": {"routingPreference": "LESS_WALKING"},
    }


def _object(value):
    return value if isinstance(value, dict) else {}


def _duration(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,8}(?:\.\d{1,9})?s", value):
        return None
    return Decimal(value[:-1])


def _number(value):
    return None if value is None else float(value)


def _distance(value):
    return value if type(value) is int and 0 <= value <= 50_000_000 else None


def _coordinates(value):
    raw = _object(_object(value).get("latLng"))
    lat, lon = raw.get("latitude"), raw.get("longitude")
    if any(isinstance(v, bool) or not isinstance(v, (int, float, Decimal)) or not math.isfinite(v) for v in (lat, lon)):
        return None
    return {"latitude": float(lat), "longitude": float(lon)} if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 50 or "T" not in value:
        return None
    try:
        _aware(datetime.fromisoformat(value))
        return value  # Preserve the source timestamp and offset, not an invented local clock.
    except ValueError:
        return None


def _url(value):
    value = _text(value, 2000)
    if value:
        try:
            parts = urlsplit(value)
            if parts.scheme in ("https", "http") and parts.hostname and not parts.username and not parts.password:
                return value
        except ValueError:
            pass
    return None


def _stop(value):
    value = _object(value)
    return {"name": _text(value.get("name")), "coordinates": _coordinates(value.get("location"))}


def _transit(value):
    value = _object(value)
    stops, line = _object(value.get("stopDetails")), _object(value.get("transitLine"))
    agencies = line.get("agencies") or []
    if not isinstance(agencies, list) or len(agencies) > 20:
        raise ValueError("Invalid transit agency list")
    departure, arrival = _timestamp(stops.get("departureTime")), _timestamp(stops.get("arrivalTime"))
    complete = departure is not None and arrival is not None
    if complete and datetime.fromisoformat(arrival) < datetime.fromisoformat(departure):
        complete = False
    return {
        "departure_stop": _stop(stops.get("departureStop")), "arrival_stop": _stop(stops.get("arrivalStop")),
        "departure_at": departure, "arrival_at": arrival, "schedule_complete": complete,
        "headsign": _text(value.get("headsign")), "stop_count": _distance(value.get("stopCount")),
        "line_name": _text(line.get("name")), "line_short_name": _text(line.get("nameShort")),
        "vehicle_type": _text(_object(line.get("vehicle")).get("type"), 100),
        "agencies": [{"name": _text(_object(a).get("name")), "url": _url(_object(a).get("uri"))} for a in agencies],
    }


def _fare(value):
    if value is None or value == {}:
        return None, "unavailable"
    if not isinstance(value, dict):
        return None, "invalid"
    currency, units, nanos = value.get("currencyCode"), value.get("units", "0"), value.get("nanos", 0)
    if (
        not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
        or not ("units" in value or "nanos" in value)
        or isinstance(units, bool) or not isinstance(units, (str, int))
        or not re.fullmatch(r"\d{1,18}", str(units))
        or type(nanos) is not int or not 0 <= nanos < 1_000_000_000
    ):
        return None, "invalid"
    amount = Decimal(str(units)) + Decimal(nanos) / Decimal(1_000_000_000)
    return {"amount": format(amount, "f"), "currency": currency, "kind": "estimated_route_fare", "party_total": None}, "complete_estimate"


def _route(raw, request, index):
    if not isinstance(raw, dict):
        raise ValueError("Invalid transit route")
    legs = raw.get("legs") or []
    if not isinstance(legs, list) or len(legs) > 20:
        raise ValueError("Invalid transit legs")
    steps, issues, walking = [], [], []
    walking_complete = bool(legs)
    for leg_index, leg in enumerate(legs):
        source_steps = _object(leg).get("steps") or []
        if not isinstance(source_steps, list) or len(source_steps) > 500:
            raise ValueError("Invalid transit steps")
        if not source_steps:
            walking_complete = False
        for raw_step in source_steps:
            raw_step = _object(raw_step)
            mode = raw_step.get("travelMode")
            duration = _duration(raw_step.get("staticDuration"))
            if mode == "WALK":
                if duration is None:
                    walking_complete = False
                else:
                    walking.append(duration)
            elif mode != "TRANSIT":
                walking_complete = False
                mode = "UNKNOWN"
            transit = _transit(raw_step.get("transitDetails")) if mode == "TRANSIT" else None
            if transit and not transit["schedule_complete"]:
                issues.append("incomplete_transit_schedule")
            steps.append({
                "leg_index": leg_index, "mode": mode, "duration_seconds": _number(duration),
                "distance_meters": _distance(raw_step.get("distanceMeters")),
                "start_coordinates": _coordinates(raw_step.get("startLocation")),
                "end_coordinates": _coordinates(raw_step.get("endLocation")), "transit": transit,
            })
    known_walking = sum(walking, Decimal(0))
    if not walking_complete:
        walking_status = "unknown"
        issues.append("incomplete_walking_duration")
    elif known_walking > Decimal(str(request.max_walking_minutes)) * 60:
        walking_status = "exceeds_limit"
    else:
        walking_status = "within_limit"
    if not any(step["mode"] == "TRANSIT" for step in steps):
        issues.append("no_transit_service")
    fare, fare_status = _fare(_object(raw.get("travelAdvisory")).get("transitFare"))
    if fare_status == "invalid":
        issues.append("invalid_estimated_fare")
    warnings = raw.get("warnings") or []
    if not isinstance(warnings, list) or len(warnings) > 50 or any(_text(w, 4000) is None for w in warnings):
        raise ValueError("Invalid provider route warnings")
    return {
        "id": f"transit-option-{index + 1}", "provider": SOURCE, "availability_status": "schedule_only",
        "booking_supported": False, "seat_availability": None, "accessibility_confirmed": None,
        "description": _text(raw.get("description")), "duration_seconds": _number(_duration(raw.get("duration"))),
        "distance_meters": _distance(raw.get("distanceMeters")),
        "encoded_polyline": _text(_object(raw.get("polyline")).get("encodedPolyline"), 200_000),
        "walking": {"status": walking_status, "duration_seconds": _number(known_walking) if walking_complete else None,
                    "known_duration_seconds": _number(known_walking), "limit_minutes": request.max_walking_minutes},
        "estimated_fare": fare, "fare_status": fare_status, "steps": steps,
        "issues": list(dict.fromkeys(issues)), "warnings": warnings,
    }


def normalize_transit_response(payload, request: TransitRouteRequest, *, received_at: datetime):
    """Normalize an ephemeral provider response; never infer missing fare or capacity.

    Validate departure with build_transit_request before any future dispatch.
    Response receipt can legitimately occur after the requested departure.
    """
    request = TransitRouteRequest.model_validate(request.model_dump())
    _aware(received_at)
    if not isinstance(payload, dict) or "error" in payload:
        raise ValueError("Google Routes did not return a successful response")
    routes = payload.get("routes", [])
    if not isinstance(routes, list) or len(routes) > 20:
        raise ValueError("Invalid or unbounded transit response")
    options = [_route(raw, request, index) for index, raw in enumerate(routes)]
    return {
        "provider": SOURCE, "source_url": DOCS_URL, "state": "ready" if options else "no_routes",
        "received_at": received_at.astimezone(timezone.utc).isoformat(), "request": request.model_dump(mode="json"),
        "options": options, "attribution": SOURCE, "walking_warning": WALKING_WARNING,
        "message": "Scheduled reference journeys only. Seats, accessibility, party fares and bookings are not verified.",
    }
