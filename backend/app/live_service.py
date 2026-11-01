"""Atomic live-data imports and explicit operator records of external bookings."""
from __future__ import annotations

import asyncio
import copy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .airport_cab_evidence import SOURCE as AIRPORT_CAB_SOURCE, QUOTE_KEY as AIRPORT_CAB_QUOTE
from .providers.transfero import SOURCE as TRANSFERO_SOURCE
from .hotel_location import property_location, property_zone
from .hotel_policy import HotelPolicyError, validate_stay_policy
from .live_data import (
    ManifestImport, ManualResourceInput, PassengerGenerationInput, _new_airports,
    aware, identifier, prepare_generated_passengers, prepare_manifest, prepare_resource, relative_minute,
)
from .models import Allocation, Mutation, Resource
from .real_data import RealDataRequest
from .inventory_lifecycle import archive_resources, ensure_inventory_capacity, resource_history, validate_location_identity
from .solver import units, validate_allocations
from .store import DomainError, Store, audit, utcnow


def _hotel_budget_total(quote: dict, booking_amount: Decimal) -> Decimal | None:
    """Only reconciled, complete EUR totals can support a hard EUR budget."""
    pricing = quote.get("pricing")
    if not isinstance(pricing, dict) or pricing.get("status") not in ("complete", "incomplete"):
        raise ValueError("Missing normalized hotel pricing")
    if pricing["status"] == "incomplete":
        if pricing.get("mandatory_total") is not None:
            raise ValueError("Incomplete pricing cannot have a mandatory total")
        return None

    def money(value):
        if not isinstance(value, dict) or value.get("currency") != "EUR" or not isinstance(value.get("amount"), str):
            raise ValueError("Invalid mandatory cost currency or amount")
        amount = Decimal(value["amount"])
        if not amount.is_finite() or not 0 <= amount <= 1_000_000:
            raise ValueError("Invalid mandatory cost")
        return amount

    if pricing.get("unresolved_reasons") != [] or money(pricing.get("booking_total")) != booking_amount:
        raise ValueError("Unreconciled booking amount")
    fees = pricing.get("excluded_fee_totals")
    if not isinstance(fees, list) or len(fees) > 1:
        raise ValueError("Invalid EUR excluded fee total")
    mandatory = money(pricing.get("mandatory_total"))
    if mandatory != booking_amount + sum((money(fee) for fee in fees), Decimal(0)):
        raise ValueError("Mandatory total disagrees with booking amount and excluded fees")
    # Keep the supplier's exact decimal amount in pricing/provenance. Solver
    # cents are a conservative allowance, never a claim about the actual charge.
    return mandatory.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


class GroupLabelUpdate(Mutation):
    name: str = Field(min_length=1, max_length=140)

    @field_validator("name")
    @classmethod
    def useful_label(cls, value):
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Supply a meaningful group label without control characters")
        return value.strip()


class HotelQuoteImport(Mutation):
    group_id: str
    airport_code: str
    checkin: date
    checkout: date
    rooms: int = Field(ge=1, le=30, strict=True)
    checkin_at: datetime
    checkout_at: datetime
    stay_times_confirmed: bool = Field(strict=True)

    @field_validator("group_id")
    @classmethod
    def group_identifier(cls, value):
        return identifier(value)

    @field_validator("airport_code")
    @classmethod
    def airport_identifier(cls, value):
        reference = lookup_airport(value)
        if reference is None:
            raise ValueError("A known airport code is required")
        return reference["code"]

    @field_validator("checkin_at", "checkout_at", mode="before")
    @classmethod
    def stay_timestamp(cls, value):
        instant = aware(value)
        if instant.second or instant.microsecond:
            raise ValueError("Use whole-minute check-in and checkout timestamps")
        return instant

    @model_validator(mode="after")
    def confirmed_stay(self):
        # Exact date validation requires each supplier property's coordinates.
        # Reject clearly impossible inputs here, before any supplier request.
        if any(abs((instant.date()-day).days) > 1 for instant, day in ((self.checkin_at,self.checkin),(self.checkout_at,self.checkout))):
            raise ValueError("Stay times cannot match the selected local dates")
        if not self.checkin < self.checkout:
            raise ValueError("Checkout date must follow check-in date")
        if self.checkout_at <= self.checkin_at:
            raise ValueError("Checkout must follow check-in")
        return self


class ExternalBooking(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    resource_id: str
    confirmation_reference: str = Field(min_length=1, max_length=1000)
    confirmed_by: str = Field(min_length=1, max_length=200)
    confirmed_at: datetime

    @field_validator("resource_id")
    @classmethod
    def resource_identifier(cls, value):
        return identifier(value)

    @field_validator("confirmation_reference", "confirmed_by")
    @classmethod
    def confirmation_text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("A valid external booking confirmation reference is required")
        return value

    @field_validator("confirmed_at", mode="before")
    @classmethod
    def confirmation_timestamp(cls, value):
        return aware(value)


class ExternalConfirmation(Mutation):
    bookings: list[ExternalBooking] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_segments(self):
        if len({booking.resource_id for booking in self.bookings}) != len(self.bookings):
            raise ValueError("Each itinerary segment needs exactly one confirmation")
        return self


def _live(world):
    if world.mode != "live":
        raise DomainError(409, "Real operational changes require live mode")


def _find(items, item_id, label):
    item = next((item for item in items if item.id == item_id), None)
    if item is None:
        raise DomainError(404, f"Unknown {label}: {item_id}")
    return item


def _invalidate_pending(world, description, *, group_ids=None, resource_ids=None):
    invalidated = set()
    for offer in world.offers:
        affected = (group_ids is None and resource_ids is None) or (
            offer.group_id in (group_ids or set())
            or any(leg.resource_id in (resource_ids or set()) for leg in offer.itinerary.legs)
        )
        if offer.status in ("proposed", "accepted") and affected:
            offer.status, offer.reason = "invalidated", description
            invalidated.add(offer.id)
            group = _find(world.groups, offer.group_id, "group")
            if group.status != "confirmed":
                group.status = "waiting"
    for reservation in world.reservations:
        if reservation.offer_id in invalidated and reservation.status != "confirmed":
            reservation.status = "released"


class LiveOperations:
    def __init__(self, store, recovery, real_data):
        self.store, self.recovery, self.real_data = store, recovery, real_data

    def _import(self, namespace, request, prepare, description):
        def apply(world):
            _live(world)
            additions = prepare(world, request)
            # These imports append new identities; they do not replace an existing
            # group's constraints or allocated services. Preserve its proposals.
            # Quote replacements invalidate their explicit group/resource scope.
            world.airports.extend(additions.get("airports", []))
            world.groups.extend(additions.get("groups", []))
            world.resources.extend(additions.get("resources", []))
            world.budget += sum(group.budget for group in additions.get("groups", []))
            # A confirmed flight route is explicit operator evidence that its
            # endpoints may be used in planning; catalog-only status stays unknown.
            flight_airports = {code for resource in additions.get("resources", []) if resource.kind == "flight" for code in (resource.origin, resource.destination)}
            for airport in world.airports:
                if airport.id in flight_airports and airport.operational_status != "closed":
                    airport.available = True
            audit(world, namespace, description, data={
                "source": additions["provenance"],
                "group_ids": [group.id for group in additions.get("groups", [])],
                "resource_ids": [resource.id for resource in additions.get("resources", [])],
            })
            return {"applied": True, "explanation": description}
        return self.store.mutate(namespace + ":" + request.idempotency_key, request.model_dump(), request.expected_version, apply)

    def manifest(self, request: ManifestImport):
        return self._import("live-manifest", request, prepare_manifest, "Imported the operator-supplied passenger manifest.")

    def generate(self, request: PassengerGenerationInput):
        return self._import("live-test-passengers", request, prepare_generated_passengers, "Created the requested passenger planning assumptions; no transport inventory was created.")

    def label_group(self, group_id: str, request: GroupLabelUpdate):
        def apply(world):
            _live(world)
            group = _find(world.groups, group_id, "passenger group")
            if any(item.id != group.id and item.name.casefold() == request.name.casefold() for item in world.groups):
                raise DomainError(409, "Choose a distinct group label so voice scope remains unambiguous")
            previous = group.name
            group.name = request.name
            if previous != request.name:
                audit(world, "group_label_updated", "Updated the passenger group's display label.", group.id,
                    {"previous_name": previous, "name": request.name})
            return {"applied": previous != request.name, "group_id": group.id,
                "explanation": "Group label updated. Passenger identities, initial requirements and source evidence are preserved."}
        return self.store.mutate("group-label:" + request.idempotency_key,
            {"group_id": group_id, **request.model_dump(mode="json")}, request.expected_version, apply)

    def resource(self, request: ManualResourceInput):
        return self._import("live-resource", request, prepare_resource, "Imported operator-confirmed transport availability.")

    async def hotels(self, request: HotelQuoteImport):
        return await self.recovery._run_claimed("live-hotels", request, self._hotels)

    def _hotel_context(self, world, request):
        _live(world)
        group = _find(world.groups, request.group_id, "passenger group")
        if group.status == "confirmed":
            raise DomainError(409, "This group already has externally confirmed bookings")
        if group.adult_count != group.size:
            raise DomainError(422, "Hotel quote import currently supports verified all-adult groups only")
        if request.rooms > group.size:
            raise DomainError(422, "Each requested room requires at least one adult")
        if request.checkin_at < utcnow():
            raise DomainError(422, "Hotel check-in must not be in the past")
        departure = relative_minute(world, request.checkin_at, rounding="down")
        arrival = relative_minute(world, request.checkout_at, rounding="up")
        if arrival > group.deadline:
            raise DomainError(422, "Hotel checkout exceeds the group's arrival deadline")
        return group, departure, arrival

    async def _hotels(self, request, started):
        world = self.store.read()
        if world.version != request.expected_version:
            raise DomainError(409, "Operational data changed; refresh before requesting quotes")
        group, departure, arrival = self._hotel_context(world, request)
        airport = lookup_airport(request.airport_code)
        # The rate adapter does not use destination_code. Reuse the group's real
        # route solely to satisfy the shared validated search contract.
        destination = next(code for code in (group.destination, group.origin) if code != request.airport_code)
        query = RealDataRequest(airport_code=request.airport_code, destination_code=destination,
            checkin=request.checkin, checkout=request.checkout, adults=group.adult_count, rooms=request.rooms)
        if self.real_data.provider.hotel_offer_provider != "liteapi":
            raise DomainError(503, "Choose the live Nuitee / LiteAPI supplier to import complete hotel quotes")
        quotes = await self.real_data.provider.offers({"code": airport["code"], "name": airport["name"], "latitude": airport["lat"], "longitude": airport["lon"]}, query)
        if not isinstance(quotes, list) or len(quotes) > 10 or any(not isinstance(quote, dict) for quote in quotes):
            raise DomainError(502, "Supplier returned an invalid hotel quote list")
        gate = asyncio.Semaphore(3)
        async def locate(quote):
            pricing = quote.get("pricing")
            if not isinstance(pricing, dict) or pricing.get("status") != "complete":
                return quote
            if request.stay_times_confirmed and quote.get("latitude") is not None and quote.get("longitude") is not None:
                return quote
            async with gate:
                location = await self.real_data.provider.hotel_location(quote["id"])
            return {**quote, **location}
        located = await asyncio.gather(*(locate(quote) for quote in quotes), return_exceptions=True)
        for result in located:
            if isinstance(result, BaseException):
                raise result
        quotes = located
        resources, skipped, exclusions = self._quote_resources(world, request, group, quotes, departure, arrival)

        def apply(current):
            self._hotel_context(current, request)
            incoming = {resource.id for resource in resources}
            protected = {leg.resource_id for offer in current.offers if offer.status == "confirmed" for leg in offer.itinerary.legs}
            replaced = {resource.id for resource in current.resources if (
                group.id in resource.eligible_group_ids and resource.kind == "hotel" and resource.requires_revalidation
                and resource.id not in protected and resource.id not in incoming and resource.booking_status != "confirmed"
            )}
            _invalidate_pending(current, "Hotel offers changed; request a fresh recovery plan.",
                                group_ids={group.id}, resource_ids=replaced)
            for resource in current.resources:
                if resource.id in replaced:
                    resource.available = False
            archive_resources(current, replaced, reason="hotel_quotes_replaced")
            nodes = {node.id: node for node in _new_airports(current, [request.airport_code],
                keep_resource_ids=incoming, keep_location_ids={resource.origin for resource in resources})}
            for resource in resources:
                node, _ = property_location(resource.provenance)
                validate_location_identity(current, node)
                existing = next((item for item in current.airports if item.id == node.id), None)
                if not existing:
                    nodes[node.id] = node
            additions = [resource for resource in resources if not any(item.id == resource.id for item in current.resources)]
            ensure_inventory_capacity(current, extra_resources=len(additions), extra_locations=len(nodes),
                keep_resource_ids=incoming, keep_location_ids={request.airport_code, *(resource.origin for resource in resources)})
            current.airports.extend(nodes.values())
            current.resources.extend(additions)
            audit(current, "live-hotel-quotes", "Imported supplier hotel quotes for the selected group; no booking or room hold was made.", group.id,
                  {"resource_ids": [resource.id for resource in resources], "rooms": request.rooms, "supplier": "Nuitee / LiteAPI", "skipped_incomplete_pricing": skipped,
                   "skipped_stay_policy": len(exclusions), "hotel_exclusions": exclusions})
            explanation = f"Imported {len(resources)} supplier hotel quotes at their actual property locations. Ground connections are required for a complete itinerary; quotes require revalidation before booking."
            if skipped:
                explanation += f" Skipped {skipped} quotes with unresolved mandatory costs."
            if exclusions:
                explanation += f" Excluded {len(exclusions)} properties whose sourced stay policy did not validate the requested times. " + " ".join(dict.fromkeys(item["message"] for item in exclusions))
            return {"applied": True, "explanation": explanation, "focus_id": group.id, "skipped_incomplete_pricing": skipped,
                    "skipped_stay_policy": len(exclusions), "hotel_exclusions": exclusions}
        return self.store.mutate("live-hotels:" + request.idempotency_key, request.model_dump(), world.version, apply, request_started=started)

    @staticmethod
    def _quote_resources(world, request, group, quotes, departure, arrival):
        if not isinstance(quotes, list) or len(quotes) > 10:
            raise DomainError(502, "Supplier returned an invalid hotel quote list")
        resources, seen, exclusions, skipped = [], set(), [], 0
        for quote in quotes:
            try:
                if not isinstance(quote, dict) or quote.get("provider") != "liteapi" or quote.get("currency") != "EUR":
                    raise ValueError("Invalid supplier")
                if quote.get("adults") != group.size or quote.get("rooms") != request.rooms or quote.get("checkin") != request.checkin.isoformat() or quote.get("checkout") != request.checkout.isoformat():
                    raise ValueError("Quote occupancy mismatch")
                hotel_id, name, reference = quote["id"], quote["name"], quote["provider_offer_id"]
                if not all(isinstance(value, str) and value.strip() for value in (hotel_id, name, reference)) or hotel_id in seen:
                    raise ValueError("Invalid quote identity")
                amount = Decimal(quote["total_price"])
                quoted_at = aware(quote["quoted_at"])
                if not amount.is_finite() or not 0 <= amount <= 1_000_000 or not 0 <= (utcnow()-quoted_at).total_seconds() <= 120:
                    raise ValueError("Invalid or stale quote")
                expires_at = aware(quote["expires_at"]) if quote.get("expires_at") is not None else None
                if expires_at is not None and expires_at <= utcnow():
                    raise ValueError("Expired supplier quote")
                budget_total = _hotel_budget_total(quote, amount)
            except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
                raise DomainError(502, "Supplier returned an incomplete, mismatched or stale hotel quote") from exc
            seen.add(hotel_id)
            if budget_total is None:
                skipped += 1
                continue
            try:
                node, location = property_location(quote)
            except (ValueError, KeyError, TypeError) as exc:
                raise DomainError(502, "Supplier property coordinates or time zone could not be verified") from exc
            local = property_zone(location["timezone"])
            policy_validation = None
            if request.stay_times_confirmed:
                if request.checkin_at.astimezone(local).date() != request.checkin or request.checkout_at.astimezone(local).date() != request.checkout:
                    raise DomainError(422, "Stay times must match the selected dates in each property's local time zone")
            else:
                try:
                    policy_validation = validate_stay_policy(quote, request, location["timezone"], now=utcnow())
                except HotelPolicyError as exc:
                    exclusions.append({"hotel_id": hotel_id, "code": exc.code, "message": exc.message})
                    continue
            resource = Resource(id="HQ-PENDING", kind="hotel", name=name[:140], origin=node.id,
                destination=node.id, departure=departure, arrival=arrival, capacity=request.rooms,
                cost_per_person=0, room_capacity=1, accessible=False, source="Nuitee / LiteAPI", status="quoted",
                eligible_group_ids=[group.id], quote_total_cost=float(amount), budget_total_cost=float(budget_total), quote_rooms=request.rooms,
                valid_until=expires_at.isoformat() if expires_at else None, requires_revalidation=True,
                external_offer_id=reference, capacity_scope="quoted_request", provenance={
                    **copy.deepcopy(quote), "checkin_at": request.checkin_at.isoformat(), "checkout_at": request.checkout_at.isoformat(),
                    "stay_times_source": "operator_confirmed" if request.stay_times_confirmed else "supplier_policy",
                    "stay_times_confirmed": request.stay_times_confirmed, "stay_policy_validation": policy_validation,
                    "property_location": location, "search_airport": request.airport_code,
                    "ground_connections_verified": False,
                })
            resource.id = "HQ-" + Store.fingerprint(resource.model_dump(mode="json", exclude={"id"}))[:28].upper()
            previous = resource_history(world, source=resource.source, external_offer_id=reference, group_id=group.id, kind="hotel")
            for old in previous:
                try:
                    newer = aware(old.provenance["quoted_at"]) > quoted_at
                except (KeyError, ValueError, TypeError):
                    newer = False
                if newer:
                    raise DomainError(409, "An older hotel quote cannot replace a newer source revision, including archived history", {"quote_revision_conflict": True})
            active = next((item for item in world.resources if item.id == resource.id), None)
            if (active is not None and not active.available) or (active is None and resource_history(world, resource_id=resource.id)):
                raise DomainError(409, "This hotel quote was retired or archived; retrieve a new supplier quote", {"quote_revision_conflict": True})
            validate_location_identity(world, node)
            resources.append(resource)
        if quotes and not resources:
            if exclusions:
                raise DomainError(422, "No hotel quote passed the sourced stay-policy checks. " + " ".join(dict.fromkeys(item["message"] for item in exclusions)),
                                  {"hotel_exclusions": exclusions, "skipped_stay_policy": len(exclusions), "skipped_incomplete_pricing": skipped})
            raise DomainError(422, "Hotel quotes have unresolved mandatory costs; review the supplier price breakdown before importing a fully budgeted option")
        return resources, skipped, exclusions

    def confirm(self, offer_id: str, request: ExternalConfirmation):
        def apply(world):
            _live(world)
            offer = _find(world.offers, offer_id, "proposal")
            group = _find(world.groups, offer.group_id, "passenger group")
            if group.source == "synthetic":
                raise DomainError(409, "Planning-assumption groups cannot receive real booking confirmations")
            if offer.status not in ("proposed", "accepted"):
                raise DomainError(409, "Only an active proposal can receive external booking confirmations")
            expected_ids = {leg.resource_id for leg in offer.itinerary.legs}
            if not expected_ids or {item.resource_id for item in request.bookings} != expected_ids or len(expected_ids) != len(offer.itinerary.legs):
                raise DomainError(422, "Supply exactly one external confirmation for every itinerary segment")
            if any(item.confirmed_at > utcnow()+timedelta(minutes=5) for item in request.bookings):
                raise DomainError(422, "External confirmation timestamps cannot be in the future")
            resources = {resource.id: resource for resource in world.resources}
            if any(resources.get(resource_id) is None or resources[resource_id].source == "synthetic" for resource_id in expected_ids):
                raise DomainError(409, "Every confirmed segment must use real sourced inventory")
            if any(resources[resource_id].source == AIRPORT_CAB_SOURCE or AIRPORT_CAB_QUOTE in resources[resource_id].provenance for resource_id in expected_ids):
                raise DomainError(409,
                    "Bucharest.CAB supplies unreserved vehicle-category quotes; source booking confirmation is not implemented. No booking or passenger state was changed.",
                    {"code": "airport_cab_booking_not_supported"})
            if any(resources[resource_id].source == TRANSFERO_SOURCE or "transfero_quote" in resources[resource_id].provenance for resource_id in expected_ids):
                raise DomainError(409,
                    "Transfero supplies unreserved scheduled-service quotes; source booking confirmation is not implemented. No booking or passenger state was changed.",
                    {"code": "transfero_booking_not_supported"})
            reservations = [reservation for reservation in world.reservations if reservation.offer_id == offer.id and reservation.status in ("proposed", "accepted")]
            expected = {(resource_id, units(resources[resource_id], group)) for resource_id in expected_ids}
            if len(reservations) != len(expected) or {(item.resource_id, item.quantity) for item in reservations} != expected:
                raise DomainError(409, "The proposal has incomplete local segment records")
            # External evidence permits existing bookings to survive quote expiry.
            # The proposal must still pass exact route, capacity and price checks.
            group.status = "confirmed"
            allocations = [Allocation(group_id=item.group_id, itinerary=item.itinerary) for item in world.offers if item.status in ("proposed", "accepted")]
            validation = validate_allocations(world, allocations)
            if not validation["valid"]:
                raise DomainError(409, "The external booking records do not match a valid complete journey", validation)
            for item in reservations:
                item.status = "confirmed"
            for resource_id in expected_ids:
                if resources[resource_id].eligible_group_ids == [group.id]:
                    resources[resource_id].booking_status = "confirmed"
            offer.status, offer.confirmed_at = "confirmed", utcnow().isoformat()
            offer.external_bookings = [item.model_dump(mode="json") for item in request.bookings]
            offer.reason = "Operator recorded an external booking confirmation for every segment."
            audit(world, "external-bookings-recorded", offer.reason, offer.id, {"group_id": group.id, "resource_ids": sorted(expected_ids)})
            return {"applied": True, "offer": offer.model_dump(mode="json"), "explanation": offer.reason}
        return self.store.mutate("live-confirm:"+request.idempotency_key, {**request.model_dump(), "offer_id": offer_id}, request.expected_version, apply)
