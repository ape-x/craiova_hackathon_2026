"""Pure checks for initial airport-to-airport private category quotes.

The supplier offers a dated vehicle category, not an allocated car or remaining
fleet. Airport references remain distinct from a passenger's exact curb or
terminal. Source road durations are estimates without departure-time traffic.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

from .airport_catalog import lookup_airport
from .initial_position import initial_position_fingerprint
from .planning_window import planning_horizon_end
from .pricing import AplQuoteFreshness, app_receipt_deadline, aware_timestamp, exact_amount
from .providers.bucharest_cab import SOURCE, SOURCE_URL, SOURCE_AIRPORTS, PRICE_SCRIPT_URL
from .weather import passenger_arrival_pending


ZONE = ZoneInfo("Europe/Bucharest")
CONTEXT_KEY = "airport_cab_context"
QUOTE_KEY = "airport_cab_quote"
OPTION_KEY = "airport_cab_option_id"


def airport_cab_context(world, group):
    """Bind airport-level reported readiness without declaring a meeting point."""
    return {
        "scenario_id": world.scenario_id, "epoch": world.epoch,
        "group_id": group.id, "group_fingerprint": initial_position_fingerprint(world, group),
        "ready_at": group.ready_at,
    }


def airport_cab_timing(world, quote):
    """Conservatively map exact source pickup and estimated duration to minutes."""
    pickup = aware_timestamp(quote["request"]["pickup_at"])
    duration = quote["route"]["duration_seconds"]
    if type(duration) is not int or not 0 < duration <= 86400:
        raise ValueError("A positive bounded supplier duration is required")
    departure = math.ceil((pickup - aware_timestamp(world.epoch)).total_seconds() / 60)
    return departure, departure + math.ceil(duration / 60)


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _money(value, *, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
        raise ValueError("The normalized native EUR amount must retain exact cents")
    return exact_amount(value, positive=positive)


def _advance_deadline(quote):
    request, policy = quote.get("request"), quote.get("policy")
    if not isinstance(request, dict) or not isinstance(policy, dict):
        raise ValueError("The source pickup and advance policy are required")
    if (policy.get("advance_policy_basis") != "conservative_maximum_of_source_day_night_rules"
            or not _integer(policy.get("min_advance_day_hours"), 0, 168)
            or not _integer(policy.get("min_advance_night_hours"), 0, 168)
            or not _integer(policy.get("required_advance_hours"), 0, 168)
            or policy["required_advance_hours"] != max(policy["min_advance_day_hours"], policy["min_advance_night_hours"])
            or any(not isinstance(policy.get(key), str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:00", policy[key])
                for key in ("night_restricted_start_time", "night_restricted_end_time"))):
        raise ValueError("A complete conservative source advance policy is required")
    pickup = aware_timestamp(request.get("pickup_at"))
    local = pickup.astimezone(ZONE)
    if (pickup.second or pickup.microsecond or request.get("timezone") != "Europe/Bucharest"
            or local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset()
            or request.get("supplier_pickup_datetime") != local.strftime("%Y-%m-%d %H:%M:%S")):
        raise ValueError("The advance policy requires an exact unambiguous source pickup")
    return pickup - timedelta(hours=policy["required_advance_hours"])


def _receipt_details(resource):
    quote = resource.provenance.get(QUOTE_KEY)
    if (resource.source != SOURCE or not isinstance(quote, dict)
            or quote.get("provider") != SOURCE or quote.get("source_url") != SOURCE_URL
            or quote.get("state") != "ready" or quote.get("transport") != "private_transfer"
            or quote.get("booking_supported") is not False
            or "source_quoted_at" not in quote or quote["source_quoted_at"] is not None
            or "expires_at" not in quote or quote["expires_at"] is not None
            or resource.valid_until is not None or resource.provenance.get("quoted_at") is not None):
        raise ValueError("A current unbooked source receipt without invented supplier expiry is required")
    freshness = AplQuoteFreshness.model_validate(_plain(resource.quote_freshness))
    received = aware_timestamp(quote.get("received_at"))
    deadline = app_receipt_deadline(resource)
    if freshness.received_at != quote["received_at"] or deadline is None:
        raise ValueError("The application receipt timestamps do not match")
    deadline = min(deadline, _advance_deadline(quote))
    if deadline < received:
        raise ValueError("The receipt already violated the source advance policy")
    return quote, received, deadline


def _source_receipt(resource, now):
    quote, received, deadline = _receipt_details(resource)
    if not received <= now <= deadline:
        raise ValueError("The application receipt or source advance eligibility expired, or the receipt is future-dated")
    return quote


def airport_cab_deadline(resource):
    """Application validity is capped by source lead eligibility, never a hold."""
    try:
        return _receipt_details(resource)[2]
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def airport_cab_current(resource, *, now=None):
    """Honor both the 120-second app window and conservative source lead rule."""
    try:
        _source_receipt(resource, now or datetime.now(timezone.utc))
        return True
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def _valid_airports(world, quote):
    resolved = quote.get("resolved_route")
    if not isinstance(resolved, dict):
        return False
    for role, code in (("origin", "OTP"), ("destination", "CRA")):
        endpoint = resolved.get(role)
        expected = SOURCE_AIRPORTS[code]
        if not isinstance(endpoint, dict) or any(endpoint.get(key) != value for key, value in expected.items() if key not in ("latitude", "longitude")):
            return False
        for key in ("latitude", "longitude"):
            number = endpoint.get(key)
            if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or abs(number - expected[key]) > 0.001:
                return False
        # Catalog nodes retain facility identity; source road references stay
        # in the receipt and must not overwrite these coordinates.
        matches = [item for item in world.airports if item.id == code]
        catalog = lookup_airport(code)
        if (len(matches) != 1 or catalog is None or matches[0].code != code
                or matches[0].kind != "airport" or matches[0].lat != catalog["lat"]
                or matches[0].lon != catalog["lon"]):
            return False
    return True


def _valid_price(option, route):
    """Recheck the supplier's native-EUR category fare and preserved components."""
    price, parts = option.get("price"), option.get("price_components")
    if (not isinstance(price, dict) or price.get("currency") != "EUR"
            or not isinstance(parts, dict) or parts.get("distance_meters") != route["distance_meters"]
            or parts.get("payment_method") != "cash" or parts.get("payment_fee") != "0.00"
            or parts.get("extras_total") != "0.00"):
        return False
    with localcontext(Context(prec=50)):
        amount = _money(price.get("amount"), positive=True)
        per_km = _money(parts.get("price_per_km"))
        distance_price = Decimal(math.ceil(Decimal(route["distance_meters"]) / 1000 * per_km))
        if _money(parts.get("distance_fare_rounded_up")) != distance_price:
            return False
        minimum = _money(parts.get("minimal_order_price"))
        custom = parts.get("custom_price")
        base = max(distance_price, minimum) if custom is None else _money(custom, positive=True)
        if type(parts.get("night_surcharge_applied")) is not bool:
            return False
        fee = _money(parts.get("night_surcharge_fee"))
        surcharge = _money(parts.get("night_surcharge_total"))
        _money(parts.get("card_payment_fee"))
        return surcharge == (fee if parts["night_surcharge_applied"] else Decimal(0)) and amount == base + surcharge


def valid_airport_cab_resource(world, resource, group, *, now=None):
    """Independently validate native fare, initial scope and source route facts."""
    try:
        now = now or datetime.now(timezone.utc)
        if (world.mode != "live" or world.currency != "EUR" or resource.source != SOURCE
                or resource.kind != "bus" or resource.transport_mode != "private_transfer"
                or resource.origin != "OTP" or resource.destination != "CRA" or group.origin != "OTP"
                or passenger_arrival_pending(world, group)
                or group.status == "confirmed" or resource.booking_status != "unbooked" or resource.status != "quoted"
                or group.adult_count != group.size or group.accessible or resource.accessible
                or resource.eligible_group_ids != [group.id] or resource.capacity != group.size
                or resource.capacity_scope != "private_vehicle_quote" or not resource.requires_revalidation
                or resource.cost_per_person != 0 or resource.budget_total_cost is not None
                or resource.price_evidence is not None or resource.quote_total_cost is None
                or resource.external_offer_id is not None or resource.vehicle_id is not None
                or resource.initial_position_assumption is not None or resource.airport_arrival_assumption is not None
                or resource.provenance.get(CONTEXT_KEY) != airport_cab_context(world, group)
                or resource.provenance.get("direction") != "initial_airport_to_airport"
                or resource.provenance.get("estimated_arrival") is not True
                or resource.provenance.get("supplier_booked") is not False):
            return False
        quote = _source_receipt(resource, now)
        request, route = quote.get("request"), quote.get("route")
        if not all(isinstance(item, dict) for item in (request, route)):
            return False
        if (request.get("origin_airport") != "OTP" or request.get("destination_airport") != "CRA"
                or request.get("timezone") != "Europe/Bucharest" or request.get("trip_type") != "from-airport"
                or request.get("baggage_supplier_scoped") is not False
                or not _integer(request.get("passengers"), 1, 7) or request["passengers"] != group.size
                or not _integer(request.get("suitcases"), 0, 7) or not _integer(request.get("handbags"), 0, 7)
                or not _valid_airports(world, quote)):
            return False
        pickup = aware_timestamp(request.get("pickup_at"))
        local = pickup.astimezone(ZONE)
        if (pickup.second or pickup.microsecond or pickup <= now
                or pickup > aware_timestamp(quote["received_at"]) + timedelta(days=31)
                or pickup < aware_timestamp(world.epoch) + timedelta(minutes=max(world.sim_time, group.ready_at))
                or local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset()
                or request.get("supplier_pickup_datetime") != local.strftime("%Y-%m-%d %H:%M:%S")):
            return False
        if (quote.get("price_basis") != "supplier_client_formula_cash_no_extras"
                or quote.get("price_formula_source_url") != PRICE_SCRIPT_URL
                or route.get("duration_basis") != "source_estimate_without_departure_time"
                or not _integer(route.get("distance_meters"), 1, 1_000_000)
                or not _integer(route.get("duration_seconds"), 1, 86400)):
            return False
        departure, arrival = airport_cab_timing(world, quote)
        if (departure != resource.departure or arrival != resource.arrival
                or departure < max(world.sim_time, group.ready_at) or arrival > min(group.deadline, planning_horizon_end(world))):
            return False
        options = quote.get("options")
        if not isinstance(options, list) or not 1 <= len(options) <= 30:
            return False
        identifiers = [item.get("id") for item in options if isinstance(item, dict)]
        if len(identifiers) != len(options) or any(not isinstance(item, str) or not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
            return False
        selected = [item for item in options if item["id"] == resource.provenance.get(OPTION_KEY)]
        if len(selected) != 1:
            return False
        option = selected[0]
        if (option.get("provider_offer_id") is not None or option.get("price_scope") != "per_vehicle"
                or option.get("fits_request") is not True or option.get("availability_status") != "quoted_not_reserved"
                or "remaining_fleet" not in option or option["remaining_fleet"] is not None
                or "supplier_valid_until" not in option or option["supplier_valid_until"] is not None
                or option.get("accessibility_confirmed") is not False
                or not _integer(option.get("provider_vehicle_type_id"), 1, 1_000_000_000)
                or not isinstance(option.get("car_type"), str) or not 1 <= len(option["car_type"]) <= 100):
            return False
        for requested, capacity, minimum in (("passengers", "max_passengers", 1), ("suitcases", "max_suitcases", 0), ("handbags", "max_handbags", 0)):
            if not _integer(option.get(capacity), minimum, 7) or option[capacity] < request[requested]:
                return False
        return _valid_price(option, route) and Decimal(str(resource.quote_total_cost)) == exact_amount(option["price"]["amount"], positive=True)
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, OverflowError):
        return False


def airport_cab_fleet_errors(resources):
    """A conservative application policy, never a supplier fleet count of one."""
    selected = {item.id for item in resources if item.source == SOURCE}
    if len(selected) > 1:
        return ["Bucharest.CAB: multiple-vehicle availability is unverified; this application permits only one category quote across the combined plan and fixed allocations."]
    return []
