"""Search and import real dated travel quotes without booking or inventing stock."""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from .airport_catalog import lookup_airport
from .live_data import aware
from .models import Airport, Model, Mutation, Resource
from .scenario_schema import MAX_RESOURCES, MAX_AIRPORTS
from .store import DomainError, Store, audit
from .travel import TravelSearchRequest


class LiveTravelSearch(Model):
    group_id: str = Field(min_length=1, max_length=80)
    kind: Literal["flight", "bus"]
    origin_code: str
    destination_code: str
    departure_date: date
    passenger_ages: list[int] | None = None

    @field_validator("passenger_ages",mode="before")
    @classmethod
    def strict_ages(cls,value):
        if value is not None and (not isinstance(value,list) or not 1<=len(value)<=30 or any(type(age) is not int or not 1<=age<=120 for age in value)):
            raise ValueError("Provide integer passenger ages from 1 to 120")
        return value


class TravelQuoteImport(Mutation):
    search_id: str = Field(min_length=1, max_length=100)
    offer_id: str = Field(min_length=1, max_length=2000)
    group_id: str = Field(min_length=1, max_length=80)


def group_identity(group):
    return (group.id, group.size, group.adult_count, group.source)


class LiveTravel:
    def __init__(self, store: Store, provider):
        self.store, self.provider = store, provider
        self.searches: OrderedDict[str, dict] = OrderedDict()

    async def search(self, request: LiveTravelSearch):
        world = self.store.read()
        if world.mode != "live":
            raise DomainError(409, "Provider inventory imports require live mode")
        group = next((g for g in world.groups if g.id == request.group_id), None)
        if group is None:
            raise DomainError(404, "Import or generate the passenger group before searching")
        if request.kind == "flight" and group.adult_count != group.size:
            raise DomainError(422, "Flight search currently requires an explicitly adult-only group")
        if request.kind == "bus":
            ages = request.passenger_ages
            if ages is None or len(ages) != group.size:
                raise DomainError(422, "Provide an age for every coach passenger; ages determine fares")
            if group.adult_count is not None and sum(a >= 18 for a in ages) != group.adult_count:
                raise DomainError(422, "Passenger ages do not match the group's declared adult count")
        try:
            query = TravelSearchRequest(kind=request.kind, origin_code=request.origin_code,
                destination_code=request.destination_code, departure_date=request.departure_date,
                adults=group.size, passenger_ages=request.passenger_ages)
        except ValidationError as exc:
            raise DomainError(422,"Invalid travel criteria: check airports, departure date and passenger ages") from exc
        result = await self.provider.search(query)
        if result.get("state") != "ready":
            return result
        key = "search_" + secrets.token_hex(16)
        now = time.monotonic()
        self.searches = OrderedDict((k, v) for k, v in self.searches.items() if v["until"] > now)
        self.searches[key] = {"until": now + 120, "scenario": world.scenario_id,
            "group": group_identity(group), "kind": request.kind, "query":query.model_dump(mode="json"), "result": result}
        while len(self.searches) > 64:
            self.searches.popitem(last=False)
        return {**result, "search_id": key}

    def import_quote(self, request: TravelQuoteImport):
        def apply(world):
            if world.mode != "live":
                raise DomainError(409, "Provider inventory imports require live mode")
            cached = self.searches.get(request.search_id)
            if not cached or cached["until"] <= time.monotonic() or cached["scenario"] != world.scenario_id:
                raise DomainError(409, "Search expired; retrieve current provider offers again")
            group = next((g for g in world.groups if g.id == request.group_id), None)
            if group is None or group_identity(group) != cached["group"]:
                raise DomainError(409, "Passenger group changed; search again for that group")
            quote = next((q for q in cached["result"]["items"] if q["id"] == request.offer_id), None)
            if quote is None or quote.get("mode") != "live" or quote.get("passengers") != group.size:
                raise DomainError(422, "The selected live quote does not cover this passenger group")
            result = append_travel_quote(world, quote, cached["query"], group, cached["kind"])
            if result.get("already_imported"):
                return result
            identity = result["resource_ids"][0]
            resource = next(item for item in world.resources if item.id == identity)
            audit(world, "provider_quote_imported", "Imported a group-specific travel quote; no seat is held or booked.", identity,
                {"provider": quote["provider"], "group_id": group.id, "source": resource.source})
            return {"resource_ids": [identity]}
        return self.store.mutate("travel-import:"+request.idempotency_key,request.model_dump(mode="json"),request.expected_version,apply)


def retire_travel_quotes(world, group_id, resource_ids):
    """Retire only a selected group's unbooked quote revisions, not their facts."""
    resources = [item for item in world.resources if item.id in resource_ids]
    protected = {leg.resource_id for offer in world.offers
        if offer.status == "confirmed" or (offer.group_id != group_id and offer.status in ("held", "proposed", "accepted"))
        for leg in offer.itinerary.legs}
    if any(item.eligible_group_ids != [group_id] or item.booking_status == "confirmed" or item.id in protected for item in resources):
        raise DomainError(409, "A refreshed quote conflicts with protected bookings or another group's inventory; operator review is required",
                          {"quote_revision_conflict": True})
    retired = {item.id for item in resources}
    for resource in resources:
        resource.available = False
    invalidated = set()
    for offer in world.offers:
        if offer.group_id == group_id and offer.status in ("held", "proposed", "accepted") and any(leg.resource_id in retired for leg in offer.itinerary.legs):
            offer.status, offer.reason = "invalidated", "A new supplier quote superseded this unbooked quote; request a current proposal."
            invalidated.add(offer.id)
    for reservation in world.reservations:
        if reservation.offer_id in invalidated and reservation.status != "confirmed":
            reservation.status = "released"
    if invalidated:
        passenger = next(item for item in world.groups if item.id == group_id)
        if passenger.status != "confirmed":
            passenger.status = "waiting"
    return sorted(retired)


def append_travel_quote(world, quote, query, group, kind):
    """Validate one sourced quote and append it to an in-memory world only.

    Callers own the atomic commit. No provider requests or reservations occur here.
    """
    if quote.get("mode") != "live" or quote.get("passengers") != group.size:
        raise DomainError(422, "The selected live quote does not cover this passenger group")
    now = datetime.now(timezone.utc)
    try:
        expires = aware(quote["expires_at"]) if quote.get("expires_at") else None
        quoted = aware(quote["quoted_at"])
        price = Decimal(quote["total_price"])
        departure_time,arrival_time=aware(quote["departure_at"]),aware(quote["arrival_at"])
        if quote["origin"]!=query["origin_code"] or quote["destination"]!=query["destination_code"] or quote["origin"]==quote["destination"]:
            raise ValueError("Route differs from query")
        if not all(isinstance(quote[k],str) and quote[k].strip() for k in ("provider","provider_offer_id")):
            raise ValueError("Missing identity")
    except (KeyError,ValueError,TypeError,InvalidOperation) as exc:
        raise DomainError(502,"Provider quote is incomplete or does not match the search") from exc
    if (expires and expires <= now) or not 0 <= (now-quoted).total_seconds() <= 120:
        raise DomainError(409, "The selected quote is stale; search again")
    if quote.get("currency") != "EUR":
        raise DomainError(422, "Only EUR quotes can be planned; no exchange rate is inferred")
    if not price.is_finite() or not 0 <= price <= 1000000:
        raise DomainError(422, "Provider returned an invalid total price")
    origin, destination = quote["origin"], quote["destination"]
    refs = []
    if kind=="bus":
        try:
            stations=[quote["segments"][0]["departure_station"],quote["segments"][-1]["arrival_station"]]
            codes=[]
            for station in stations:
                code="ST-"+hashlib.sha256((quote["provider"]+str(station["id"])).encode()).hexdigest()[:16].upper()
                lat,lon=station["latitude"],station["longitude"]
                if type(lat) not in (int,float) or type(lon) not in (int,float) or not math.isfinite(lat+lon) or not -90<=lat<=90 or not -180<=lon<=180 or not isinstance(station["name"],str) or not station["name"].strip():
                    raise ValueError("Invalid station")
                codes.append(code)
                if not any(a.id==code for a in [*world.airports,*refs]):
                    refs.append(Airport(id=code,code=code,name=station["name"],lat=lat,lon=lon,available=False,kind="station"))
            origin,destination=codes
            if origin==destination:
                raise ValueError("Same departure and arrival station")
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise DomainError(502,"Coach quote is missing its real departure or arrival station") from exc
    else:
        for code in (origin, destination):
            airport = lookup_airport(code)
            if airport is None:
                raise DomainError(422, "Provider quote does not map to known endpoint airports")
            existing=next((a for a in world.airports if a.id==airport["code"]),None)
            if existing and existing.operational_status=="closed":
                raise DomainError(409,"A confirmed airport closure prevents this flight import")
            if existing is None:
                refs.append(Airport(id=airport["code"], code=airport["code"], name=airport["name"],
                    lat=airport["lat"], lon=airport["lon"], available=False))
    if len(world.airports)+len(refs)>MAX_AIRPORTS:
        raise DomainError(422,"Operation location limit reached")
    epoch = aware(world.epoch)
    departure = math.floor((departure_time-epoch).total_seconds()/60)
    arrival = math.ceil((arrival_time-epoch).total_seconds()/60)
    if departure < world.sim_time or arrival <= departure or arrival > 7*1440:
        raise DomainError(422, "Provider schedule is past or outside the seven-day planning horizon")
    # A supplier identifier can survive a price/time/expiry refresh. Preserve
    # each received quote as its own snapshot instead of reusing older facts.
    identity = "Q-" + Store.fingerprint({"group_id": group.id, "kind": kind, "quote": quote})[:24].upper()
    if any(r.id == identity for r in world.resources):
        return {"resource_ids": [identity], "already_imported": True}
    if len(world.resources) >= MAX_RESOURCES:
        raise DomainError(422, "Resource limit reached")
    resource = Resource(id=identity, kind=kind, name=quote.get("name") or quote["provider"],
        origin=origin, destination=destination, departure=departure, arrival=arrival,
        capacity=group.size, accessible=quote.get("accessibility_confirmed") is True,
        cost_per_person=0, source=quote["provider"], provenance=quote,
        valid_until=expires.isoformat() if expires else None,
        eligible_group_ids=[group.id], quote_total_cost=float(price),
        external_offer_id=quote["provider_offer_id"], capacity_scope="quoted_request",
        requires_revalidation=True, status="quoted")
    previous = [item for item in world.resources if item.kind == kind and item.source == quote["provider"]
        and item.external_offer_id == quote["provider_offer_id"] and group.id in item.eligible_group_ids]
    for item in previous:
        try:
            newer = aware(item.provenance["quoted_at"]) > quoted
        except (KeyError, ValueError, TypeError):
            newer = False
        if newer:
            raise DomainError(409, "Supplier returned an older revision of an already observed offer; no older fare was restored",
                              {"quote_revision_conflict": True})
    retired = retire_travel_quotes(world, group.id, {item.id for item in previous})
    world.airports.extend(refs)
    if resource.kind == "flight":
        for airport in world.airports:
            if airport.id in (origin, destination):
                airport.available = True
    world.resources.append(resource)
    return {"resource_ids": [identity], "superseded_resource_ids": retired}
