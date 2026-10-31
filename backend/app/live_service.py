"""Atomic live-data imports and explicit operator records of external bookings."""
from __future__ import annotations

import copy
import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .live_data import (
    ManifestImport, ManualResourceInput, PassengerGenerationInput, _new_airports,
    aware, identifier, prepare_generated_passengers, prepare_manifest, prepare_resource, relative_minute,
)
from .models import Allocation, Mutation, Resource
from .real_data import RealDataRequest
from .scenario_schema import MAX_RESOURCES
from .solver import units, validate_allocations
from .store import DomainError, audit, utcnow


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
        return aware(value)

    @model_validator(mode="after")
    def confirmed_stay(self):
        local = ZoneInfo("Europe/Bucharest")
        if not self.stay_times_confirmed:
            raise ValueError("Confirm the property's check-in and checkout times")
        if self.checkin_at.astimezone(local).date() != self.checkin or self.checkout_at.astimezone(local).date() != self.checkout:
            raise ValueError("Stay times must match the selected dates in Europe/Bucharest")
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
    bookings: list[ExternalBooking] = Field(min_length=1, max_length=4)

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


def _invalidate_pending(world, description):
    invalidated = set()
    for offer in world.offers:
        if offer.status in ("proposed", "accepted"):
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
            _invalidate_pending(world, "Operational data changed; request a fresh recovery plan.")
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
        return self._import("live-test-passengers", request, prepare_generated_passengers, "Generated explicitly requested test passengers; no transport inventory was created.")

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
        resources = self._quote_resources(world, request, group, quotes, departure, arrival)

        def apply(current):
            self._hotel_context(current, request)
            _invalidate_pending(current, "Hotel offers changed; request a fresh recovery plan.")
            protected = {leg.resource_id for offer in current.offers if offer.status == "confirmed" for leg in offer.itinerary.legs}
            current.resources = [resource for resource in current.resources if not (
                group.id in resource.eligible_group_ids and resource.kind == "hotel" and resource.requires_revalidation
                and resource.id not in protected and resource.booking_status != "confirmed"
            )]
            if len(current.resources) + len(resources) > MAX_RESOURCES:
                raise DomainError(422, f"Operation limit is {MAX_RESOURCES} resources")
            current.airports.extend(_new_airports(current, [request.airport_code]))
            current.resources.extend(resources)
            audit(current, "live-hotel-quotes", "Imported supplier hotel quotes for the selected group; no booking or room hold was made.", group.id,
                  {"resource_ids": [resource.id for resource in resources], "rooms": request.rooms, "supplier": "Nuitee / LiteAPI"})
            return {"applied": True, "explanation": f"Imported {len(resources)} supplier hotel quotes. They require revalidation before booking.", "focus_id": group.id}
        return self.store.mutate("live-hotels:" + request.idempotency_key, request.model_dump(), world.version, apply, request_started=started)

    @staticmethod
    def _quote_resources(world, request, group, quotes, departure, arrival):
        if not isinstance(quotes, list) or len(quotes) > 10:
            raise DomainError(502, "Supplier returned an invalid hotel quote list")
        resources, seen = [], set()
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
            except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
                raise DomainError(502, "Supplier returned an incomplete, mismatched or stale hotel quote") from exc
            seen.add(hotel_id)
            local_id = "HQ-" + hashlib.sha256((group.id + "|" + hotel_id + "|" + reference).encode()).hexdigest()[:28].upper()
            resources.append(Resource(id=local_id, kind="hotel", name=name[:140], origin=request.airport_code,
                destination=request.airport_code, departure=departure, arrival=arrival, capacity=request.rooms,
                cost_per_person=0, room_capacity=1, accessible=False, source="Nuitee / LiteAPI", status="quoted",
                eligible_group_ids=[group.id], quote_total_cost=float(amount), quote_rooms=request.rooms,
                valid_until=expires_at.isoformat() if expires_at else None, requires_revalidation=True,
                external_offer_id=reference, capacity_scope="quoted_request", provenance={
                    **copy.deepcopy(quote), "checkin_at": request.checkin_at.isoformat(), "checkout_at": request.checkout_at.isoformat(),
                    "stay_times_source": "operator_confirmed", "stay_times_confirmed": True,
                }))
        return resources

    def confirm(self, offer_id: str, request: ExternalConfirmation):
        def apply(world):
            _live(world)
            offer = _find(world.offers, offer_id, "proposal")
            group = _find(world.groups, offer.group_id, "passenger group")
            if group.source == "synthetic":
                raise DomainError(409, "Test passengers cannot receive real booking confirmations")
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
