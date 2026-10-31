"""Strict property-local stay checks; unknown late check-in is never inferred."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import re

from .hotel_location import property_zone


SOURCE = "Nuitee / LiteAPI"
SOURCE_URL = "https://api.liteapi.travel/v3.0/data/hotel"


class HotelPolicyError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _clock(value):
    if not isinstance(value, str):
        raise ValueError("Missing clock time")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::00)?\s*(AM|PM)?", value.strip().upper())
    if not match:
        raise ValueError("Unsupported clock time")
    hour, minute, meridiem = int(match[1]), int(match[2]), match[3]
    if minute > 59 or (meridiem and not 1 <= hour <= 12) or (not meridiem and hour > 23):
        raise ValueError("Invalid clock time")
    return time((hour % 12 + (12 if meridiem == "PM" else 0)) if meridiem else hour, minute)


def source_stay_policy(hotel_id, raw, received_at):
    """Keep a small structured source record, never arbitrary provider objects."""
    result = {"hotel_id": hotel_id, "source": SOURCE, "source_url": SOURCE_URL,
              "retrieved_at": received_at, "status": "provided" if isinstance(raw, dict) else "missing" if raw is None else "invalid"}
    raw = raw if isinstance(raw, dict) else {}
    for target, aliases in (("checkin_start", ("checkin_start", "checkinStart", "checkin")),
                            ("checkin_end", ("checkin_end", "checkinEnd")), ("checkout", ("checkout",))):
        values = [raw[key] for key in aliases if raw.get(key) not in (None, "")]
        result[target] = values[0].strip() if values and isinstance(values[0], str) and len(values[0]) <= 100 else None
        if any(not isinstance(value, str) or len(value) > 100 for value in values):
            result["status"] = "invalid"
        if len(values) > 1:
            try:
                if len({_clock(value) for value in values}) != 1:
                    result["status"] = "invalid"
            except ValueError:
                result["status"] = "invalid"
    result["special_instructions_present"] = bool(raw.get("special_instructions") or raw.get("specialInstructions"))
    result["additional_instructions_present"] = bool(raw.get("instructions"))
    return result


def _boundary(day, clock, zone):
    naive = datetime.combine(day, clock)
    first, second = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset() or first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise HotelPolicyError("ambiguous_policy_time", "Property policy crosses an ambiguous or nonexistent local clock time; explicit property confirmation is required.")
    return first


def validate_stay_policy(quote, request, timezone_id, *, now):
    policy = quote.get("stay_policy")
    if not isinstance(policy, dict) or policy.get("status") != "provided":
        raise HotelPolicyError("stay_policy_unavailable", "A complete, unambiguous property check-in and checkout policy is unavailable.")
    try:
        received = datetime.fromisoformat(policy["retrieved_at"].replace("Z", "+00:00"))
        if (policy.get("hotel_id") != quote["id"] or policy.get("source") != SOURCE or policy.get("source_url") != SOURCE_URL
                or received.tzinfo is None or not 0 <= (now - received).total_seconds() <= 120):
            raise ValueError("Unverified property policy")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HotelPolicyError("stay_policy_source_unverified", "The property policy identity or retrieval time could not be verified.") from exc
    if policy.get("special_instructions_present"):
        raise HotelPolicyError("special_checkin_instructions", "The property supplies special check-in instructions; confirm its stay times explicitly before importing.")
    try:
        start = _clock(policy.get("checkin_start"))
        checkout = _clock(policy.get("checkout"))
        end = _clock(policy["checkin_end"]) if policy.get("checkin_end") else None
    except ValueError as exc:
        raise HotelPolicyError("stay_policy_time_unresolved", "Property check-in or checkout times are missing or cannot be interpreted unambiguously.") from exc
    zone = property_zone(timezone_id)
    local_in, local_out = request.checkin_at.astimezone(zone), request.checkout_at.astimezone(zone)
    start_at = _boundary(request.checkin, start, zone)
    end_at = _boundary(request.checkin + timedelta(days=int(end < start)), end, zone) if end is not None else None
    cutoff = _boundary(request.checkout, checkout, zone)
    incoming, outgoing = local_in.astimezone(timezone.utc), local_out.astimezone(timezone.utc)
    if local_out.date() != request.checkout:
        raise HotelPolicyError("stay_policy_date_mismatch", "Checkout must match the quoted checkout date in the property's local time zone.")
    if end_at is None:
        if incoming != start_at.astimezone(timezone.utc):
            raise HotelPolicyError("checkin_end_unknown", f"The property has no stated check-in end; only its exact published check-in start, {start_at.isoformat()} ({timezone_id}), is automatically accepted.")
    elif not start_at.astimezone(timezone.utc) <= incoming <= end_at.astimezone(timezone.utc):
        raise HotelPolicyError("outside_checkin_window", f"Requested arrival falls outside the property's sourced check-in window: {start_at.isoformat()} to {end_at.isoformat()} ({timezone_id}).")
    if outgoing > cutoff.astimezone(timezone.utc):
        raise HotelPolicyError("after_checkout_cutoff", f"Requested checkout is later than the property's sourced local checkout cutoff: {cutoff.isoformat()} ({timezone_id}).")
    return {"validated": True, "source": SOURCE, "hotel_id": quote["id"], "timezone": timezone_id,
            "retrieved_at": policy["retrieved_at"], "checkin_rule": "bounded_window" if end_at else "exact_start_only",
            "checkin_start": start_at.isoformat(), "checkin_end": end_at.isoformat() if end_at else None,
            "checkout_by": cutoff.isoformat(), "additional_instructions_review_required": policy.get("additional_instructions_present") is True}
