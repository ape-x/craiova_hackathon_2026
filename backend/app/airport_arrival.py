"""Airport-level return planning inputs, never a terminal or boarding guarantee."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Literal

from pydantic import Field, field_validator

from .initial_position import PositionCoordinates, initial_position_fingerprint
from .pricing import APL_SOURCE, PriceModel, RECEIPT_MAX_AGE_SECONDS, aware_timestamp


GOOGLE_ROUTE_SOURCE = "https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes"


class AirportDropoff(PriceModel):
    label: str = Field(min_length=1, max_length=500)
    coordinates: PositionCoordinates

    @field_validator("label")
    @classmethod
    def meaningful_label(cls, value):
        if not value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Supply a named airport dropoff point")
        return value.strip()


class AirportArrivalAssumption(PriceModel):
    source: Literal["operator_declared"]
    confirmed: Literal[True]
    group_id: str
    group_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_id: str
    epoch: str
    airport_id: str = Field(pattern=r"^[A-Z]{3}$")
    onward_resource_id: str
    onward_flight_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    onward_departure_at: str
    dropoff: AirportDropoff
    processing_minutes: int = Field(ge=1, le=720)
    # A named road endpoint is not evidence of the airline's terminal assignment.
    departure_terminal: None = None

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("The operator must explicitly confirm the airport planning inputs")
        return value

    @field_validator("epoch", "onward_departure_at")
    @classmethod
    def aware_instant(cls, value):
        aware_timestamp(value)
        return value


def onward_flight_fingerprint(resource) -> str:
    # Quote revisions stay distinct. Recording a booking, changing a display
    # label or operational status does not rewrite the original source facts.
    evidence = resource.model_dump(mode="json", exclude={"name", "status", "available", "booking_status"})
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def valid_airport_arrival(world, resource, group, *, now=None) -> bool:
    """Reconcile a return's declared airport allowance with its dated sources.

    The solver separately checks the onward flight's availability/freshness and
    requires it to be the next leg. The dropoff deliberately need not equal the
    airport catalog's reference coordinate.
    """
    try:
        raw = resource.airport_arrival_assumption
        assumption = AirportArrivalAssumption.model_validate(raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw)
        airport = next((item for item in world.airports if item.id == assumption.airport_id), None)
        hotel = next((item for item in world.airports if item.id == resource.origin), None)
        onward = next((item for item in world.resources if item.id == assumption.onward_resource_id), None)
        if not (world.mode == "live" and resource.kind == "bus" and resource.transport_mode == "private_transfer"
                and resource.source == APL_SOURCE and resource.initial_position_assumption is None
                and resource.eligible_group_ids == [group.id] and assumption.group_id == group.id
                and assumption.scenario_id == world.scenario_id and assumption.epoch == world.epoch
                and assumption.group_fingerprint == initial_position_fingerprint(world, group)
                and airport is not None and airport.kind == "airport" and airport.code == airport.id
                and resource.destination == airport.id and hotel is not None and hotel.kind == "hotel"
                and onward is not None and onward.kind == "flight" and onward.source != "synthetic"
                and onward.origin == airport.id and onward.eligible_group_ids == [group.id]
                and onward_flight_fingerprint(onward) == assumption.onward_flight_fingerprint):
            return False
        epoch = aware_timestamp(world.epoch)
        departure = aware_timestamp(onward.provenance["departure_at"])
        if (departure != aware_timestamp(assumption.onward_departure_at)
                or math.floor((departure-epoch).total_seconds()/60) != onward.departure
                or onward.provenance.get("origin") != airport.id
                or onward.provenance.get("mode") != "live"
                or onward.provenance.get("provider") != onward.source
                or resource.arrival + assumption.processing_minutes > onward.departure):
            return False
        route = resource.provenance["google_route"]
        query = route["request"]
        pickup = epoch + timedelta(minutes=resource.departure)
        hotel_coordinates = {"latitude": hotel.lat, "longitude": hotel.lon}
        if (route.get("source") != "Google Routes" or route.get("source_url") != GOOGLE_ROUTE_SOURCE
                or query["origin"] != hotel_coordinates or query["destination"] != assumption.dropoff.coordinates.model_dump()
                or aware_timestamp(query["departure_time"]) != pickup
                or query.get("travel_mode") != "DRIVE" or route.get("duration_guaranteed") is not False):
            return False
        duration = route["duration_seconds"]
        if not isinstance(duration, str) or len(duration) > 32:
            return False
        seconds = Decimal(duration)
        if not seconds.is_finite() or not 0 < seconds <= 7*86400:
            return False
        arrival = resource.departure + int((seconds/60).to_integral_value(rounding=ROUND_CEILING))
        source_hotel = resource.provenance["source_hotel"]
        if (arrival != resource.arrival or source_hotel.get("node_id") != hotel.id
                or source_hotel.get("latitude") != hotel.lat or source_hotel.get("longitude") != hotel.lon):
            return False
        receipt = resource.provenance["apl_quote"]
        request, resolved = receipt["request"], receipt.get("resolved_route", {})
        if (request.get("expected_hub") != airport.id or request.get("expected_direction") != "to_hub"
                or aware_timestamp(request["pickup_at"]) != pickup
                or resolved.get("hub") not in (None, airport.id)
                or resolved.get("direction") not in (None, "to_hub")):
            return False
        # A route's app receipt is not a supplier expiry. Keep recorded journeys
        # valid after this local proposal window, while preserving all bindings.
        fetched = aware_timestamp(route["fetched_at"])
        if group.status != "confirmed" and not 0 <= ((now or datetime.now(timezone.utc))-fetched).total_seconds() <= RECEIPT_MAX_AGE_SECONDS:
            return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, OverflowError):
        return False
