"""A planned quoted-flight connection; never a report that passengers arrived."""
from __future__ import annotations

import math
from datetime import timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .after_flight_inputs import AfterFlightTransferInput
from .airport_catalog import lookup_airport
from .initial_position import initial_position_fingerprint
from .inventory_lifecycle import resource_history, validate_location_identity
from .models import Airport
from .pricing import aware_timestamp
from .store import DomainError, Store
from .weather import passenger_arrival_pending

DIRECTION = "after_flight_airport_to_airport"
KEY = "postflight_connection"


class PostflightConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["operator_declared_processing_window"]
    arrival_basis: Literal["supplier_schedule_not_observed"]
    pickup_rule: Literal["max_window_start_ceil_arrival_plus_processing"]
    incoming_resource_id: str = Field(min_length=1, max_length=100)
    incoming_resource_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_arrival_at: str
    airport_processing_minutes: int = Field(ge=1, le=720)
    pickup_window_start: str
    pickup_window_end: str
    derived_pickup_at: str


def incoming_fingerprint(resource):
    return Store.fingerprint(resource.model_dump(mode="json"))


def incoming_deadline(incoming):
    deadline = aware_timestamp(incoming.provenance["quoted_at"]) + timedelta(seconds=120)
    if incoming.valid_until is not None:
        deadline = min(deadline, aware_timestamp(incoming.valid_until))
    return deadline


def derive_postflight_pickup(incoming_flight, transfer):
    transfer = AfterFlightTransferInput.model_validate(transfer)
    arrival = aware_timestamp(incoming_flight.provenance["arrival_at"]).astimezone(timezone.utc)
    available = arrival + timedelta(minutes=transfer.airport_processing_minutes)
    rounded = available.replace(second=0, microsecond=0) + (timedelta(minutes=1) if available.second or available.microsecond else timedelta())
    pickup = max(transfer.pickup_window_start.astimezone(timezone.utc), rounded)
    if pickup > transfer.pickup_window_end:
        raise DomainError(422, "The quoted flight and declared processing allowance miss the exact pickup window; no time was shifted")
    return pickup


def postflight_group(world, group_id):
    if world.mode != "live" or world.currency != "EUR":
        raise DomainError(409, "A planned airport connection requires a live EUR operation")
    groups = [item for item in world.groups if item.id == group_id]
    if len(groups) != 1:
        raise DomainError(409, "The planned connection needs exactly one existing passenger group")
    group = groups[0]
    cases = [item for item in world.incidents if item.id == group.incident_id and group.id in item.group_ids]
    if (len(cases) != 1 or group.origin == "OTP" or group.destination != "CRA"
            or group.status == "confirmed" or any(item.group_id == group.id and item.status == "confirmed" for item in world.offers)
            or group.adult_count != group.size or not 1 <= group.size <= 7 or group.accessible
            or passenger_arrival_pending(world, group)):
        raise DomainError(422, "The planned connection needs an unconfirmed, ready, adult-only group ending at CRA; passenger location and accessibility are not inferred")
    ref = lookup_airport(group.origin)
    origin = next((item for item in world.airports if item.id == group.origin), None)
    if ref is None or origin is None or origin.kind != "airport" or origin.code != ref["code"] or (origin.lat, origin.lon) != (ref["lat"], ref["lon"]):
        raise DomainError(409, "The reported departure airport has an unresolved geographic identity")
    return group


def incoming_projection(world, group, incoming, *, now):
    """Revalidate a server-held normalized quote without moving its passengers."""
    from .solver import resource_feasible
    try:
        quote = incoming.provenance
        epoch = aware_timestamp(world.epoch)
        departure, arrival, observed = (aware_timestamp(quote[key]) for key in ("departure_at", "arrival_at", "quoted_at"))
        expiry = aware_timestamp(quote["expires_at"]) if quote.get("expires_at") else None
        identity = "Q-" + Store.fingerprint({"group_id": group.id, "kind": "flight", "quote": quote})[:24].upper()
        if (incoming.id != identity or incoming.kind != "flight" or incoming.source == "synthetic"
                or incoming.source != quote["provider"] or not incoming.available or incoming.status != "quoted"
                or incoming.origin != group.origin or incoming.destination != "OTP"
                or quote["origin"] != incoming.origin or quote["destination"] != "OTP"
                or quote["mode"] != "live" or quote["currency"] != "EUR" or type(quote["passengers"]) is not int
                or quote["passengers"] != group.size or incoming.capacity != group.size
                or incoming.eligible_group_ids != [group.id] or incoming.capacity_scope != "quoted_request"
                or incoming.booking_status != "unbooked" or not incoming.requires_revalidation
                or incoming.external_offer_id != quote["provider_offer_id"]
                or incoming.quote_total_cost is None or incoming.quote_total_cost > group.budget
                or Decimal(str(incoming.quote_total_cost)) != Decimal(quote["total_price"])
                or incoming.departure != math.floor((departure - epoch).total_seconds() / 60)
                or incoming.arrival != math.ceil((arrival - epoch).total_seconds() / 60)
                or not 0 <= (now - observed).total_seconds() <= 120
                or (expiry is not None and (expiry <= now or aware_timestamp(incoming.valid_until) != expiry))
                or (expiry is None and incoming.valid_until is not None)):
            raise ValueError("Flight source evidence changed")
        retained = resource_history(world, resource_id=incoming.id)
        if retained and (not any(item.id == incoming.id for item in world.resources)
                or any(incoming_fingerprint(item) != incoming_fingerprint(incoming) for item in retained)):
            raise ValueError("Flight revision was retired or changed")
        for item in resource_history(world, source=incoming.source, external_offer_id=incoming.external_offer_id, group_id=group.id, kind="flight"):
            if aware_timestamp(item.provenance.get("quoted_at")) > observed:
                raise ValueError("A newer incoming revision exists")
        projected = world.model_copy(deep=True)
        for code in (incoming.origin, incoming.destination):
            ref = lookup_airport(code)
            node = Airport(id=code, code=code, name=ref["name"], lat=ref["lat"], lon=ref["lon"], operational_status="unknown")
            validate_location_identity(projected, node)
            current = next((item for item in projected.airports if item.id == code), None)
            if current is None:
                projected.airports.append(node)
            elif current.operational_status == "unknown":
                # Same source endpoint evidence as a staged live flight import;
                # explicit airport closure and weather permissions stay intact.
                current.available = True
        if not any(item.id == incoming.id for item in projected.resources):
            projected.resources.append(incoming.model_copy(deep=True))
        if not resource_feasible(projected, incoming, group, group.origin, max(world.sim_time, group.ready_at) + 30, set()):
            raise ValueError("The incoming flight no longer passes current scope, route, capacity or airport constraints")
        return projected
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        raise DomainError(409, "The exact incoming flight source revision is no longer eligible for this planned connection") from exc


def build_postflight_connection(world, group, incoming, transfer, *, now):
    transfer = AfterFlightTransferInput.model_validate(transfer)
    if transfer.group_id != group.id or postflight_group(world, group.id) != group:
        raise DomainError(409, "The connection belongs to a different passenger group")
    incoming_projection(world, group, incoming, now=now)
    pickup = derive_postflight_pickup(incoming, transfer)
    return PostflightConnection(source="operator_declared_processing_window", arrival_basis="supplier_schedule_not_observed",
        pickup_rule="max_window_start_ceil_arrival_plus_processing", incoming_resource_id=incoming.id,
        incoming_resource_fingerprint=incoming_fingerprint(incoming), source_arrival_at=aware_timestamp(incoming.provenance["arrival_at"]).astimezone(timezone.utc).isoformat(),
        airport_processing_minutes=transfer.airport_processing_minutes,
        pickup_window_start=transfer.pickup_window_start.astimezone(timezone.utc).isoformat(),
        pickup_window_end=transfer.pickup_window_end.astimezone(timezone.utc).isoformat(), derived_pickup_at=pickup.isoformat()).model_dump()


def valid_postflight_connection(world, resource, group, *, now):
    try:
        raw = resource.provenance[KEY]
        value = PostflightConnection.model_validate(raw)
        context = {"scenario_id": world.scenario_id, "epoch": world.epoch, "group_id": group.id,
            "group_fingerprint": initial_position_fingerprint(world, group), "ready_at": group.ready_at, KEY: raw}
        if (resource.provenance.get("direction") != DIRECTION or resource.provenance.get("airport_cab_context") != context):
            return False
        incoming = next(item for item in world.resources if item.id == value.incoming_resource_id)
        quote_request = resource.provenance["airport_cab_quote"]["request"]
        transfer = AfterFlightTransferInput(group_id=group.id, via_airport="OTP", destination_airport="CRA",
            pickup_window_start=value.pickup_window_start, pickup_window_end=value.pickup_window_end,
            airport_processing_minutes=value.airport_processing_minutes, suitcases=quote_request["suitcases"], handbags=quote_request["handbags"])
        return (raw == build_postflight_connection(world, group, incoming, transfer, now=now)
            and aware_timestamp(quote_request["pickup_at"]) == aware_timestamp(value.derived_pickup_at))
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, DomainError):
        return False
