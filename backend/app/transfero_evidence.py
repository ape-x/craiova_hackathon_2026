"""Pure source, money, facility and shared-seat checks for the Romanian stay.

The operator names the hotel as its boarding facility. Coordinates are retained
separately from LiteAPI property geography; no walking/vehicle link is invented.
"""
from __future__ import annotations

from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from .airport_catalog import lookup_airport
from .initial_position import initial_position_fingerprint
from .models import Resource
from .planning_window import planning_horizon_end
from .pricing import AplQuoteFreshness, TransferoPriceEvidence, aware_timestamp, build_transfero_price_evidence
from .providers.transfero import SOURCE, HOTEL_ID, POLICY, validate_normalized_option
from .stay_requirements import HotelStayRequirement, read_stay_requirement
from .weather import passenger_arrival_pending

QUOTE_KEY = "transfero_quote"
OPTION_KEY = "transfero_option_id"
CONTEXT_KEY = "transfero_context"
HOTEL_NODE_ID = "HP-" + hashlib.sha256(("liteapi|" + HOTEL_ID).encode()).hexdigest()[:24].upper()
HOTEL_LATITUDE = 45.645322
HOTEL_LONGITUDE = 25.590917


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _inputs(value):
    # Runtime import keeps the source evidence independent of the orchestrator's
    # module initialization while sharing the exact operator-input schema.
    from .romanian_hotel_recovery import RomanianHotelRecoveryInputs
    return RomanianHotelRecoveryInputs.model_validate(_plain(value)).model_dump(mode="json")


def _hotel_identity(node):
    if (node.id != HOTEL_NODE_ID or node.kind != "hotel" or node.code != node.id
            or node.name.casefold() != "hotel aro palace" or node.lat != HOTEL_LATITUDE or node.lon != HOTEL_LONGITUDE):
        raise ValueError("The verified Aro Palace property identity is required")
    return {"node_id": node.id, "hotel_id": HOTEL_ID, "name": node.name, "latitude": node.lat, "longitude": node.lon,
        "binding_basis": "exact_liteapi_property_and_operator_named_boarding_facility",
        "operator_stop_is_precise_door_or_bay": False}


def _context(world, group, inputs, hotel):
    inputs = _inputs(inputs)
    expected = HotelStayRequirement(source="operator_requested", checkin_at=inputs["checkin_at"], checkout_at=inputs["checkout_at"],
        rooms=inputs["rooms"], minimum_rest_minutes=inputs["minimum_rest_minutes"])
    if read_stay_requirement(group) != expected:
        raise ValueError("The exact requested stay must be applied before sourcing transport")
    airport = next((item for item in world.airports if item.id == "OTP"), None)
    catalog = lookup_airport("OTP")
    if (world.mode != "live" or world.currency != "EUR" or group.origin != "OTP" or group.destination == "OTP"
            or group.status == "confirmed" or group.adult_count != group.size or group.accessible
            or inputs["suitcases"] > group.size or inputs["handbags"] > group.size
            or passenger_arrival_pending(world, group) or airport is None or airport.kind != "airport"
            or airport.code != "OTP" or airport.lat != catalog["lat"] or airport.lon != catalog["lon"]):
        raise ValueError("An eligible adult group currently at OTP is required")
    return {"scenario_id": world.scenario_id, "epoch": world.epoch, "group_id": group.id,
        "group_fingerprint": initial_position_fingerprint(world, group), "ready_at": group.ready_at,
        "inputs": inputs, "property": _hotel_identity(hotel)}


def transfero_deadline(resource):
    """Application validity, capped by advance eligibility; never supplier expiry."""
    try:
        quote = resource.provenance[QUOTE_KEY]
        received = aware_timestamp(quote["received_at"])
        option = validate_normalized_option(quote, resource.provenance[OPTION_KEY], now=received, require_current=False)
        freshness = AplQuoteFreshness.model_validate(_plain(resource.quote_freshness))
        if (resource.source != SOURCE or freshness.received_at != quote["received_at"]
                or resource.valid_until is not None or resource.provenance.get("quoted_at") is not None):
            return None
        return min(received + timedelta(seconds=120), aware_timestamp(option["departure_at"]) - timedelta(hours=POLICY["required_advance_hours"]))
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return None


def transfero_current(resource, *, now=None):
    try:
        now = now or datetime.now(timezone.utc)
        deadline = transfero_deadline(resource)
        return deadline is not None and aware_timestamp(resource.quote_freshness.received_at) <= now <= deadline
    except (ValueError, TypeError, AttributeError):
        return False


def build_transfero_resource(world, group, inputs, hotel_node, quote, option_id, *, now=None):
    """Build one party-priced coach revision; callers stage/commit atomically."""
    now = now or datetime.now(timezone.utc)
    context = _context(world, group, inputs, hotel_node)
    option = validate_normalized_option(quote, option_id, now=now)
    price = build_transfero_price_evidence(quote, option_id, now=now)
    request = quote["request"]
    expected_date = aware_timestamp(context["inputs"]["checkin_at" if request["direction"] == "otp_to_hotel" else "checkout_at"]).astimezone(ZoneInfo("Europe/Bucharest")).date().isoformat()
    if (request["adults"] != group.size or request["suitcases"] != context["inputs"]["suitcases"]
            or request["handbags"] != context["inputs"]["handbags"] or request["travel_date"] != expected_date):
        raise ValueError("The source query differs from the exact requested group/stay/bags")
    outbound = request["direction"] == "otp_to_hotel"
    epoch = aware_timestamp(world.epoch)
    departure = math.floor((aware_timestamp(option["departure_at"]) - epoch).total_seconds() / 60)
    arrival = math.ceil((aware_timestamp(option["arrival_at"]) - epoch).total_seconds() / 60)
    identity = json.dumps({"context": context, "quote_received_at": quote["received_at"], "option_id": option_id}, sort_keys=True, separators=(",", ":"))
    resource = Resource(id="TRF-" + hashlib.sha256(identity.encode()).hexdigest()[:24].upper(), kind="bus",
        name=f"Direct Aeroport · {option['departure_stop']['name']} → {option['arrival_stop']['name']}",
        origin="OTP" if outbound else hotel_node.id, destination=hotel_node.id if outbound else "OTP",
        departure=departure, arrival=arrival, capacity=option["available_seats"], accessible=False, available=True,
        cost_per_person=0, status="quoted", source=SOURCE, eligible_group_ids=[group.id],
        budget_total_cost=float(price.estimated_eur.amount), requires_revalidation=True, capacity_scope="source_reported_seats",
        price_evidence=price, quote_freshness=AplQuoteFreshness(basis="app_receipt", received_at=quote["received_at"], max_age_seconds=120),
        provenance={QUOTE_KEY: copy.deepcopy(quote), OPTION_KEY: option_id, CONTEXT_KEY: context,
            "direction": request["direction"], "inventory_pool_id": option["inventory_pool_id"],
            "estimated_arrival": True, "supplier_booked": False})
    if not valid_transfero_resource(world, resource, group, now=now):
        raise ValueError("The exact coach option cannot satisfy this group/stay request")
    return resource


def valid_transfero_resource(world, resource, group, *, now=None):
    try:
        now = now or datetime.now(timezone.utc)
        context = resource.provenance[CONTEXT_KEY]
        hotel = next(item for item in world.airports if item.id == context["property"]["node_id"])
        if context != _context(world, group, context["inputs"], hotel):
            return False
        quote = resource.provenance[QUOTE_KEY]
        option_id = resource.provenance[OPTION_KEY]
        option = validate_normalized_option(quote, option_id, now=now)
        evidence = build_transfero_price_evidence(quote, option_id, now=now)
        request, inputs = quote["request"], context["inputs"]
        outbound = request["direction"] == "otp_to_hotel"
        origin, destination = ("OTP", hotel.id) if outbound else (hotel.id, "OTP")
        epoch = aware_timestamp(world.epoch)
        departure = math.floor((aware_timestamp(option["departure_at"]) - epoch).total_seconds() / 60)
        arrival = math.ceil((aware_timestamp(option["arrival_at"]) - epoch).total_seconds() / 60)
        checkin = math.floor((aware_timestamp(inputs["checkin_at"]) - epoch).total_seconds() / 60)
        checkout = math.ceil((aware_timestamp(inputs["checkout_at"]) - epoch).total_seconds() / 60)
        source_date = aware_timestamp(inputs["checkin_at" if outbound else "checkout_at"]).astimezone(ZoneInfo("Europe/Bucharest")).date().isoformat()
        if (resource.source != SOURCE or resource.kind != "bus" or resource.transport_mode is not None
                or resource.capacity_scope != "source_reported_seats" or resource.capacity != option["available_seats"]
                or resource.eligible_group_ids != [group.id] or request["adults"] != group.size
                or request["suitcases"] != inputs["suitcases"] or request["handbags"] != inputs["handbags"]
                or request["travel_date"] != source_date or resource.accessible or resource.status != "quoted"
                or resource.booking_status != "unbooked" or resource.vehicle_id is not None or resource.external_offer_id is not None
                or resource.quote_total_cost is not None or resource.cost_per_person != 0 or not resource.requires_revalidation
                or resource.initial_position_assumption is not None or resource.airport_arrival_assumption is not None
                or resource.provenance.get("direction") != request["direction"]
                or resource.provenance.get("inventory_pool_id") != option["inventory_pool_id"]
                or resource.provenance.get("estimated_arrival") is not True or resource.provenance.get("supplier_booked") is not False
                or (resource.origin, resource.destination, resource.departure, resource.arrival) != (origin, destination, departure, arrival)
                or arrival > min(group.deadline, planning_horizon_end(world)) or not transfero_current(resource, now=now)
                or TransferoPriceEvidence.model_validate(_plain(resource.price_evidence)) != evidence
                or resource.budget_total_cost is None or Decimal(str(resource.budget_total_cost)) != Decimal(evidence.estimated_eur.amount)):
            return False
        return (departure >= max(world.sim_time, group.ready_at) + POLICY["boarding_minutes"]
                and arrival + inputs["hotel_connection_minutes"] <= checkin) if outbound else departure >= checkout + inputs["hotel_connection_minutes"]
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, StopIteration):
        return False


def transfero_connection(previous, resource):
    """Return (handled, minutes); None rejects an incompatible neighbouring leg."""
    first = previous is not None and previous.source == SOURCE
    second = resource.source == SOURCE
    if not first and not second:
        return False, None
    try:
        coach = resource if second else previous
        inputs = coach.provenance[CONTEXT_KEY]["inputs"]
        direction = coach.provenance["direction"]
        if second:
            if direction == "otp_to_hotel":
                return True, POLICY["boarding_minutes"] if previous is None else None
            if direction == "hotel_to_otp" and previous is not None and previous.kind == "hotel" and previous.destination == coach.origin:
                minutes = inputs["hotel_connection_minutes"]
                return True, minutes if type(minutes) is int and 20 <= minutes <= 720 else None
        elif direction == "otp_to_hotel" and resource.kind == "hotel" and resource.origin == coach.destination:
            minutes = inputs["hotel_connection_minutes"]
            return True, minutes if type(minutes) is int and 20 <= minutes <= 720 else None
        elif direction == "hotel_to_otp" and resource.kind == "flight" and resource.origin == "OTP":
            minutes = inputs["airport_processing_minutes"]
            return True, minutes if type(minutes) is int and 180 <= minutes <= 720 else None
    except (KeyError, TypeError, AttributeError):
        pass
    return True, None


def transfero_pool(resource):
    """Read source seat identity even for a retained older receipt revision."""
    quote = resource.provenance[QUOTE_KEY]
    option = validate_normalized_option(quote, resource.provenance[OPTION_KEY], now=aware_timestamp(quote["received_at"]), require_current=False)
    if (resource.source != SOURCE or resource.provenance.get("inventory_pool_id") != option["inventory_pool_id"]
            or resource.capacity != option["available_seats"]):
        raise ValueError("Unverified shared coach stock")
    return option["inventory_pool_id"], option["available_seats"]


def transfero_remaining(world, resource, used):
    """Latest stock observation; all allocation revisions still consume its seats.

    Equally timed disagreeing observations use the lower count. An old low-stock
    receipt cannot permanently suppress a later valid increase. No source count
    is added to another receipt's count, and no reservation is reconstructed.
    """
    if resource.source != SOURCE:
        return resource.capacity - used[resource.id]
    try:
        pool, _ = transfero_pool(resource)
        observations, consumed = [], 0
        for item in world.resources:
            if item.source == SOURCE and item.provenance.get("inventory_pool_id") == pool:
                other, count = transfero_pool(item)
                if other != pool:
                    raise ValueError("Changed pool")
                observations.append((aware_timestamp(item.provenance[QUOTE_KEY]["received_at"]), count))
                consumed += used[item.id]
        latest = max(stamp for stamp, _ in observations)
        capacity = min(count for stamp, count in observations if stamp == latest)
        return capacity - consumed
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return -1


def transfero_capacity_errors(world, used):
    errors, seen = [], set()
    for resource in world.resources:
        if resource.source != SOURCE or used[resource.id] <= 0:
            continue
        pool = resource.provenance.get("inventory_pool_id", resource.id)
        if pool not in seen and transfero_remaining(world, resource, used) < 0:
            errors.append(f"{resource.id}: shared Direct Aeroport service seat capacity exceeded")
        seen.add(pool)
    return errors
