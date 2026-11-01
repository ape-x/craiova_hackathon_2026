"""Read confirmed allocation evidence without inferring ownership from eligibility.

Inconsistent booking records still require operator review. A historical leg by
itself does not assign a currently booked service to its former passenger group.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from .models import World


ACTIVE_OFFER_STATUSES = frozenset({"held", "proposed", "accepted", "confirmed"})


def confirmed_group_ids(world: World) -> set[str]:
    """Return groups protected by any retained confirmation or active booking link."""
    protected = {group.id for group in world.groups if group.status == "confirmed"}
    protected.update(item.group_id for item in world.reservations if item.status == "confirmed")
    booked_resources = {resource.id for resource in world.resources if resource.booking_status == "confirmed"}
    for offer in world.offers:
        if (offer.status == "confirmed" or offer.external_bookings
                or (offer.status in ACTIVE_OFFER_STATUSES
                    and any(leg.resource_id in booked_resources for leg in offer.itinerary.legs))):
            protected.add(offer.group_id)
    return protected


def has_confirmed_groups(world: World, group_ids: Iterable[str]) -> bool:
    return bool(confirmed_group_ids(world).intersection(group_ids))


def confirmation_accounting_issues(world: World) -> list[str]:
    """Check retained confirmations are covered by existing fixed itineraries.

    No missing allocation or reservation is reconstructed. A complete confirmed
    itinerary already accounts for its passengers even if a legacy reservation
    row is absent; the ordinary solver still independently validates that route.
    """
    if world.mode != "live":
        return []
    from .solver import units

    issues: set[str] = set()
    groups = {group.id: group for group in world.groups}
    resources = {resource.id: resource for resource in world.resources}
    fixed = [offer for offer in world.offers if offer.status == "confirmed"]
    fixed_by_id = {offer.id: offer for offer in fixed}
    if len(fixed_by_id) != len(fixed):
        issues.add("duplicate_confirmed_offer")
    by_group: dict[str, int] = {}
    covered_resources: set[str] = set()
    used: Counter[str] = Counter()
    for offer in fixed:
        by_group[offer.group_id] = by_group.get(offer.group_id, 0)+1
        group = groups.get(offer.group_id)
        if group is None or group.status != "confirmed":
            issues.add("confirmed_offer_group_mismatch")
        ids = [leg.resource_id for leg in offer.itinerary.legs]
        if not ids or len(set(ids)) != len(ids):
            issues.add("incomplete_confirmed_itinerary")
        if group is not None and offer.itinerary.legs:
            legs = offer.itinerary.legs
            if (legs[0].origin != group.origin or legs[-1].destination != group.destination
                    or not any(leg.kind in ("flight", "bus") for leg in legs)
                    or any(first.destination != second.origin for first, second in zip(legs, legs[1:]))):
                issues.add("incomplete_confirmed_itinerary")
        for leg in offer.itinerary.legs:
            resource = resources.get(leg.resource_id)
            if resource is None:
                issues.add("missing_confirmed_resource")
            else:
                if (leg.kind, leg.origin, leg.destination, leg.departure, leg.arrival) != (
                        resource.kind, resource.origin, resource.destination, resource.departure, resource.arrival):
                    issues.add("confirmed_itinerary_resource_mismatch")
                if group is not None:
                    used[resource.id] += units(resource, group)
            covered_resources.add(leg.resource_id)
    if any(count > resources[identity].capacity for identity, count in used.items()):
        issues.add("confirmed_capacity_exceeded")
    resource_counts = Counter(resource.id for resource in world.resources)
    if any(resource_counts[identity] > 1 for identity in covered_resources):
        issues.add("duplicate_confirmed_resource")
    if any(count != 1 for count in by_group.values()):
        issues.add("duplicate_confirmed_group_allocation")
    if any(group.status == "confirmed" and by_group.get(group.id) != 1 for group in world.groups):
        issues.add("confirmed_group_without_fixed_allocation")

    for offer in world.offers:
        if not offer.external_bookings:
            continue
        if offer.status != "confirmed":
            issues.add("external_booking_without_fixed_allocation")
        ids = {leg.resource_id for leg in offer.itinerary.legs}
        references = [item.get("resource_id") for item in offer.external_bookings]
        if (any(not isinstance(identity, str) or identity not in ids for identity in references)
                or len({identity for identity in references if isinstance(identity, str)}) != len(references)):
            issues.add("external_booking_resource_mismatch")

    seen: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    for reservation in world.reservations:
        if reservation.status != "confirmed":
            continue
        offer = fixed_by_id.get(reservation.offer_id)
        group = groups.get(reservation.group_id)
        resource = resources.get(reservation.resource_id)
        identity = (reservation.offer_id, reservation.resource_id)
        if identity in seen or reservation.id in seen_ids:
            issues.add("duplicate_confirmed_reservation")
        seen.add(identity)
        seen_ids.add(reservation.id)
        if (offer is None or offer.group_id != reservation.group_id or group is None or resource is None
                or not any(leg.resource_id == reservation.resource_id for leg in offer.itinerary.legs)):
            issues.add("confirmed_reservation_without_fixed_allocation")
        elif reservation.quantity != units(resource, group):
            issues.add("confirmed_reservation_quantity_mismatch")
    if any(resource.booking_status == "confirmed" and resource.id not in covered_resources
            for resource in world.resources):
        issues.add("booked_resource_without_fixed_allocation")
    return sorted(issues)


def require_consistent_confirmations(world: World) -> None:
    """Fail before recovery dispatch/application; emit no supplier booking data."""
    from .store import DomainError

    issues = confirmation_accounting_issues(world)
    if issues:
        raise DomainError(409,
            "Confirmed booking evidence cannot be reconciled with the recorded fixed allocations. Manual review is required before recovery; no booking was changed or missing allocation inferred.",
            {"code": "confirmed_allocation_review_required", "reason_codes": issues})
