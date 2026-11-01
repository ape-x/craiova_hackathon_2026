"""Scoped, replay-safe airport vehicle quotes. No booking or dispatch methods."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator

from .airport_catalog import lookup_airport
from .airport_cab_evidence import airport_cab_timing, valid_airport_cab_resource
from .initial_position import initial_position_fingerprint
from .inventory_lifecycle import archived_location, ensure_inventory_capacity, resource_history, validate_location_identity
from .live_travel import retire_travel_quotes
from .models import Airport, Mutation, Resource
from .planning_window import planning_horizon_end
from .pricing import AplQuoteFreshness, aware_timestamp
from .providers.base import ProviderError
from .providers.bucharest_cab import AirportCabQuoteRequest, BucharestCabProvider, SOURCE, SOURCE_URL
from .store import DomainError, Store, audit, utcnow
from .weather import passenger_arrival_pending


class AirportCabPlanningRequest(Mutation):
    group_id: str = Field(min_length=1, max_length=80)
    destination_airport: Literal["CRA"]
    pickup_at: datetime
    suitcases: int = Field(ge=0, le=7, strict=True)
    handbags: int = Field(ge=0, le=7, strict=True)

    @field_validator("pickup_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply a pickup timestamp with its UTC offset")
        return value


class AirportCabImport(Mutation):
    option_id: str = Field(min_length=1, max_length=120)


def cab_group(world, group_id):
    if world.mode != "live" or world.currency != "EUR":
        raise DomainError(409, "Airport vehicle quotes require a live EUR operation")
    group = next((item for item in world.groups if item.id == group_id), None)
    if group is None:
        raise DomainError(404, "Passenger group not found")
    if group.status == "confirmed" or any(item.group_id == group.id and item.status == "confirmed" for item in world.offers):
        raise DomainError(409, "A confirmed journey requires operational review before sourcing replacement transport")
    if group.adult_count != group.size or not 1 <= group.size <= 7:
        raise DomainError(422, "This source currently supports one explicitly adult-only group of at most seven passengers")
    if group.accessible:
        raise DomainError(422, "This source does not confirm accessible vehicles")
    origin = next((item for item in world.airports if item.id == group.origin), None)
    if origin is None or origin.kind != "airport" or origin.id != "OTP" or origin.code != "OTP":
        raise DomainError(422, "The group must have a reported initial position at OTP for this source")
    origin_reference = lookup_airport("OTP")
    if origin.lat != origin_reference["lat"] or origin.lon != origin_reference["lon"]:
        raise DomainError(409, "The retained OTP location has a conflicting geographic identity")
    cab_destination(world)
    if passenger_arrival_pending(world, group):
        raise DomainError(409, "The group's airport arrival is still pending; a quote cannot move passengers")
    return group


def cab_context(world, request, provider, *, now):
    group = cab_group(world, request.group_id)
    try:
        query = AirportCabQuoteRequest(origin_airport="OTP", destination_airport=request.destination_airport,
            pickup_at=request.pickup_at, passengers=group.size, suitcases=request.suitcases, handbags=request.handbags)
        query = provider.validate_request(query)
    except (ValueError, ProviderError) as exc:
        raise DomainError(422, "Supply a supported future pickup, party and luggage request") from exc
    departure = math.ceil((query.pickup_at - aware_timestamp(world.epoch)).total_seconds() / 60)
    if (query.pickup_at < aware_timestamp(world.epoch) + timedelta(minutes=group.ready_at)
            or departure < max(group.ready_at, world.sim_time)
            or departure >= min(group.deadline, planning_horizon_end(world))):
        raise DomainError(422, "Pickup must follow the group's reported readiness and precede its deadline within the planning window")
    return {"scenario_id": world.scenario_id, "epoch": world.epoch, "group_id": group.id,
        "group_fingerprint": initial_position_fingerprint(world, group), "ready_at": group.ready_at}, query


def cab_destination(world):
    node = next((item for item in world.airports if item.id == "CRA"), None)
    if node is not None:
        record = lookup_airport("CRA")
        if node.kind != "airport" or node.code != "CRA" or node.lat != record["lat"] or node.lon != record["lon"]:
            raise DomainError(409, "The retained CRA location has a conflicting identity")
        return node
    node = archived_location(world, "CRA")
    if node is None:
        record = lookup_airport("CRA")
        node = Airport(id="CRA", code="CRA", name=record["name"], lat=record["lat"], lon=record["lon"],
            available=False, operational_status="unknown", diversion_eligible=False)
    record = lookup_airport("CRA")
    if node.kind != "airport" or node.code != "CRA" or node.lat != record["lat"] or node.lon != record["lon"]:
        raise DomainError(409, "The archived CRA location has a conflicting geographic identity")
    validate_location_identity(world, node)
    return node


def build_airport_cab_resource(world, context, response, option_id, *, now):
    """Compile only the server-held quote, preserving source and group scope."""
    group = next((item for item in world.groups if item.id == context["group_id"]), None)
    if group is None or context != {"scenario_id": world.scenario_id, "epoch": world.epoch,
            "group_id": group.id, "group_fingerprint": initial_position_fingerprint(world, group), "ready_at": group.ready_at}:
        raise DomainError(409, "The group's planning context changed; retrieve a current quote")
    cab_group(world, group.id)
    if aware_timestamp(response["usable_until"]) <= now:
        raise DomainError(409, "The application's source receipt expired; retrieve a new quote")
    quote = response.get("transfer")
    option = next((item for item in (quote or {}).get("options", []) if item["id"] == option_id), None)
    if option is None or not option["fits_request"]:
        raise DomainError(422, "This option does not cover the exact declared party and luggage")
    departure, arrival = airport_cab_timing(world, quote)
    if departure < max(world.sim_time, group.ready_at) or arrival > min(group.deadline, planning_horizon_end(world)):
        raise DomainError(409, "The source driving estimate falls outside the group's current planning window")
    identity = "CAB-" + Store.fingerprint({"quote_id": response["quote_id"], "option_id": option_id})[:28].upper()
    resource = Resource(id=identity, kind="bus", transport_mode="private_transfer", name=SOURCE + " " + option["car_type"],
        origin="OTP", destination="CRA", departure=departure, arrival=arrival, capacity=group.size,
        accessible=False, available=True, cost_per_person=0, quote_total_cost=float(Decimal(option["price"]["amount"])),
        eligible_group_ids=[group.id], source=SOURCE, status="quoted", requires_revalidation=True,
        capacity_scope="private_vehicle_quote", quote_freshness=AplQuoteFreshness(basis="app_receipt", received_at=quote["received_at"], max_age_seconds=120),
        provenance={"airport_cab_quote": copy.deepcopy(quote), "airport_cab_option_id": option_id,
            "airport_cab_context": copy.deepcopy(context), "direction": "initial_airport_to_airport",
            "estimated_arrival": True, "supplier_booked": False, "quoted_at": None,
            "quote_id": response["quote_id"], "quote_sequence": response["quote_sequence"]})
    destination = cab_destination(world)
    projected = world.model_copy(deep=True)
    if not any(item.id == destination.id for item in projected.airports):
        projected.airports.append(destination)
    if not valid_airport_cab_resource(projected, resource, group, now=now):
        raise DomainError(422, "The source quote has unresolved evidence or group-scope conflicts")
    return resource, destination


class AirportCabPlanning:
    def __init__(self, settings, store, *, provider=None, now=None):
        self.store, self.now = store, now or utcnow
        self.provider = provider or BucharestCabProvider(settings, now=self.now)
        with store.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS airport_cab_receipts (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    scenario_id TEXT NOT NULL, status TEXT NOT NULL, lease_until REAL NOT NULL,
                    http_requests INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS airport_cab_cache (
                    id TEXT PRIMARY KEY, expires REAL NOT NULL, data TEXT NOT NULL);
            """)

    def status(self):
        return {"provider": SOURCE, "configured": self.provider.configured, "source_url": SOURCE_URL,
            "supported_routes": [{"origin_airport": "OTP", "origin_name": "Bucharest Henri Coanda Airport",
                "destination_airport": "CRA", "destination_name": "Craiova Airport"}],
            "max_passengers": 7, "max_advance_days": 31, "booking_supported": False,
            "http_requests_per_quote": 2, "fleet_policy": "one_vehicle_per_plan"}

    async def aclose(self):
        await self.provider.aclose()

    def purge(self):
        with self.store.connect() as conn:
            conn.execute("DELETE FROM airport_cab_cache WHERE expires<=?", (self.now().timestamp(),))
            conn.execute("UPDATE airport_cab_receipts SET status='unknown' WHERE status='dispatched' AND lease_until<=?", (self.now().timestamp(),))

    def _reserve(self, request):
        fingerprint = Store.fingerprint(request.model_dump(mode="json"))
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world = self.store._load(conn)
            old = conn.execute("SELECT id,fingerprint,scenario_id,status,lease_until FROM airport_cab_receipts WHERE request_key=?", (request.idempotency_key,)).fetchone()
            if old:
                identity, previous, scenario, status, lease = old
                if previous != fingerprint:
                    raise DomainError(409, "This request key belongs to a different airport transfer search")
                if scenario != world.scenario_id:
                    raise DomainError(409, "This source search belongs to an earlier operation; no request was repeated")
                cached = conn.execute("SELECT data FROM airport_cab_cache WHERE id=? AND expires>?", (identity, self.now().timestamp())).fetchone()
                if status == "done" and cached:
                    saved = json.loads(cached[0])
                    if saved["context"]["epoch"] != world.epoch:
                        raise DomainError(409, "This source search belongs to an earlier operational clock; no request was repeated")
                    current, _ = cab_context(world, request, self.provider, now=self.now())
                    if current != saved["context"]:
                        raise DomainError(409, "The group's context changed after this search; no source request was repeated")
                    return {"response": {**saved["response"], "replayed": True}}
                if status in ("dispatched", "unknown"):
                    active = status == "dispatched" and lease > self.now().timestamp()
                    raise DomainError(503, "This source request is processing or has an unknown outcome; it will not be dispatched again with this key",
                        {"in_progress": active, "unknown_outcome": not active}, applied=None)
                raise DomainError(409, "This source receipt expired; an explicit new search requires a new key")
            if request.expected_version != world.version:
                raise DomainError(409, "The operation changed before the source search; refresh it")
            context, query = cab_context(world, request, self.provider, now=self.now())
            if conn.execute("SELECT 1 FROM airport_cab_receipts WHERE status='dispatched' AND lease_until>? LIMIT 1", (self.now().timestamp(),)).fetchone():
                raise DomainError(409, "An airport vehicle quote search is already running")
            identity = "ACQ-" + secrets.token_hex(12).upper()
            row = conn.execute("INSERT INTO airport_cab_receipts (id,request_key,fingerprint,scenario_id,status,lease_until) VALUES(?,?,?,?,?,?)",
                (identity, request.idempotency_key, fingerprint, world.scenario_id, "dispatched", self.now().timestamp() + 120))
            return {"quote_id": identity, "context": context, "query": query, "sequence": row.lastrowid, "world": world}

    def _dispatch(self, identity, context, request):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world = self.store._load(conn)
            current, _ = cab_context(world, request, self.provider, now=self.now())
            if current != context:
                raise DomainError(409, "Passenger context changed before a source request; no further request was sent")
            updated = conn.execute("UPDATE airport_cab_receipts SET http_requests=http_requests+1 WHERE id=? AND status='dispatched' AND http_requests<2 AND lease_until>?", (identity, self.now().timestamp()))
            if updated.rowcount != 1:
                raise DomainError(409, "The source request allowance or lease was exhausted")

    def _finish(self, identity, context, response):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Assess against the current operation in the receipt transaction;
            # a source response cannot silently restore obsolete passenger facts.
            if response["transfer"] is not None:
                world = self.store._load(conn)
                response["state"], response["options"] = "blocked", []
                for option in response["transfer"]["options"]:
                    reasons = []
                    try:
                        build_airport_cab_resource(world, context, response, option["id"], now=self.now())
                    except DomainError as exc:
                        reasons.append({"code": "option_not_usable", "message": str(exc)})
                    response["options"].append({"option_id": option["id"], "importable": not reasons, "blockers": reasons})
                if any(item["importable"] for item in response["options"]):
                    response["state"] = "ready"
            conn.execute("DELETE FROM airport_cab_cache WHERE expires<=?", (self.now().timestamp(),))
            conn.execute("INSERT INTO airport_cab_cache VALUES(?,?,?)", (identity, aware_timestamp(response["usable_until"]).timestamp(), json.dumps({"context": context, "response": response})))
            conn.execute("DELETE FROM airport_cab_cache WHERE id IN (SELECT id FROM airport_cab_cache ORDER BY expires DESC LIMIT -1 OFFSET 64)")
            conn.execute("UPDATE airport_cab_receipts SET status='done' WHERE id=?", (identity,))

    async def quote(self, request):
        reserved = await asyncio.to_thread(self._reserve, request)
        if "response" in reserved:
            return reserved["response"]
        identity, context, world = reserved["quote_id"], reserved["context"], reserved["world"]
        now = self.now()
        response = {"quote_id": identity, "quote_sequence": reserved["sequence"], "group_id": context["group_id"], "source_version": world.version,
            "created_at": now.isoformat(), "usable_until": (now + timedelta(seconds=120)).isoformat(),
            "state": "blocked", "transfer": None, "options": [], "blockers": [], "replayed": False}
        try:
            try:
                quote = await self.provider.quote(reserved["query"], before_dispatch=lambda: asyncio.to_thread(self._dispatch, identity, context, request))
                response["transfer"] = quote
                response["usable_until"] = min(aware_timestamp(response["usable_until"]),
                    aware_timestamp(quote["received_at"]) + timedelta(seconds=120),
                    request.pickup_at - timedelta(hours=quote["policy"]["required_advance_hours"])).isoformat()
            except (ProviderError, DomainError) as exc:
                response["blockers"].append({"code": getattr(exc, "code", "context_changed"), "message": str(exc)})
            saving = asyncio.create_task(asyncio.to_thread(self._finish, identity, context, response))
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                raise
            return response
        except BaseException:
            with self.store.connect() as conn:
                conn.execute("UPDATE airport_cab_receipts SET status='unknown' WHERE id=? AND status='dispatched'", (identity,))
            raise

    def import_quote(self, quote_id, request):
        def apply(world):
            with self.store.connect() as conn:
                cached = conn.execute("SELECT data FROM airport_cab_cache WHERE id=? AND expires>?", (quote_id, self.now().timestamp())).fetchone()
            if cached is None:
                raise DomainError(409, "Source receipt expired; retrieve a new quote")
            saved = json.loads(cached[0])
            context, response = saved["context"], saved["response"]
            resource, destination = build_airport_cab_resource(world, context, response, request.option_id, now=self.now())
            history = resource_history(world, source=SOURCE, group_id=context["group_id"])
            if any(item.provenance.get("quote_sequence", 0) > response["quote_sequence"] for item in history):
                raise DomainError(409, "A newer source revision was already imported; this older receipt cannot replace it")
            existing = next((item for item in world.resources if item.id == resource.id), None)
            if existing is not None and existing.available:
                return {"applied": False, "resource_ids": [resource.id], "explanation": "This immutable source option is already imported."}
            if existing is not None or resource_history(world, resource_id=resource.id):
                raise DomainError(409, "This source option was retired; retrieve a new quote")
            old = [item.id for item in world.resources if item.source == SOURCE and item.eligible_group_ids == [context["group_id"]] and item.available and item.booking_status != "confirmed"]
            retire_travel_quotes(world, context["group_id"], old)
            nodes = [] if any(item.id == destination.id for item in world.airports) else [destination]
            ensure_inventory_capacity(world, extra_resources=1, extra_locations=len(nodes), keep_location_ids=(resource.origin, resource.destination))
            world.airports.extend(nodes)
            world.resources.append(resource)
            audit(world, "airport_vehicle_quote_imported", "Imported one group-scoped airport vehicle quote for planning; no vehicle reserved.", resource.id,
                {"group_id": context["group_id"], "quote_id": quote_id, "source": SOURCE})
            return {"applied": True, "resource_ids": [resource.id], "explanation": "Airport transfer added for initial-journey planning. Driving arrival is estimated; no vehicle or complete journey is booked."}
        return self.store.mutate("airport-cab-import:" + request.idempotency_key, {"quote_id": quote_id, **request.model_dump(mode="json")}, request.expected_version, apply)


def create_airport_cab_router(service, operator_dependency):
    router = APIRouter(prefix="/api/airport-transfers", dependencies=[Depends(operator_dependency)])

    @router.get("/status")
    def status():
        return service.status()

    @router.post("/quotes")
    async def quote(request: AirportCabPlanningRequest):
        return await service.quote(request)

    @router.post("/quotes/{quote_id}/import")
    def import_quote(quote_id: str, request: AirportCabImport):
        return service.import_quote(quote_id, request)

    return router
