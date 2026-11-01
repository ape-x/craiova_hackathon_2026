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
from .postflight_connection import (DIRECTION as POSTFLIGHT_DIRECTION, KEY as POSTFLIGHT_KEY,
    build_postflight_connection, derive_postflight_pickup, incoming_deadline, incoming_fingerprint, incoming_projection, postflight_group)
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


def cab_context(world, request, provider, *, now, incoming_flight=None, after_flight_transfer=None):
    if (incoming_flight is None) != (after_flight_transfer is None):
        raise DomainError(422, "A planned pickup requires both exact incoming flight evidence and explicit connection inputs")
    connection = None
    if incoming_flight is None:
        group = cab_group(world, request.group_id)
    else:
        from .after_flight_inputs import AfterFlightTransferInput
        supplied = AfterFlightTransferInput.model_validate(after_flight_transfer)
        group = postflight_group(world, request.group_id)
        cab_destination(world)
        connection = build_postflight_connection(world, group, incoming_flight, supplied, now=now)
        if (request.destination_airport != supplied.destination_airport or request.suitcases != supplied.suitcases
                or request.handbags != supplied.handbags or request.pickup_at != derive_postflight_pickup(incoming_flight, supplied)):
            raise DomainError(422, "The source request differs from the exact planned pickup or declared luggage")
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
        "group_fingerprint": initial_position_fingerprint(world, group), "ready_at": group.ready_at,
        **({POSTFLIGHT_KEY: connection} if connection is not None else {})}, query


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
    connection = context.get(POSTFLIGHT_KEY)
    if group is None or context != {"scenario_id": world.scenario_id, "epoch": world.epoch,
            "group_id": group.id, "group_fingerprint": initial_position_fingerprint(world, group), "ready_at": group.ready_at,
            **({POSTFLIGHT_KEY: connection} if connection is not None else {})}:
        raise DomainError(409, "The group's planning context changed; retrieve a current quote")
    (postflight_group if connection is not None else cab_group)(world, group.id)
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
            "airport_cab_context": copy.deepcopy(context), "direction": POSTFLIGHT_DIRECTION if connection is not None else "initial_airport_to_airport",
            **({POSTFLIGHT_KEY: copy.deepcopy(connection)} if connection is not None else {}),
            "estimated_arrival": True, "supplier_booked": False, "quoted_at": None,
            "quote_id": response["quote_id"], "quote_sequence": response["quote_sequence"]})
    destination = cab_destination(world)
    projected = world.model_copy(deep=True)
    if not any(item.id == destination.id for item in projected.airports):
        projected.airports.append(destination)
    if not valid_airport_cab_resource(projected, resource, group, now=now):
        raise DomainError(422, "The source quote has unresolved evidence or group-scope conflicts")
    return resource, destination


def append_airport_cab_options(world, context, response, option_ids, *, now, stage=False, keep_resource_ids=()):
    """Stage alternatives or append one chosen category without external I/O.

    Changes publish only after every option and revision passes. Stage copies
    may exceed persistent capacity by the bounded receipt alternatives; final
    inventory must fit without compacting any unrelated resource or location.
    """
    option_ids = list(option_ids)
    if (not option_ids or len(option_ids) > 30 or (not stage and len(option_ids) != 1)
            or any(not isinstance(item, str) or not item or len(item) > 120 for item in option_ids)
            or len(set(option_ids)) != len(option_ids)):
        raise DomainError(422, "Select bounded distinct categories from one receipt; only one category can be committed")
    trial = world.model_copy(deep=True)
    compiled = [build_airport_cab_resource(trial, context, response, option_id, now=now) for option_id in option_ids]
    resources = [item[0] for item in compiled]
    destination = compiled[0][1]
    history = resource_history(trial, source=SOURCE, group_id=context["group_id"])
    if any(item.provenance.get("quote_sequence", 0) > response["quote_sequence"] for item in history):
        raise DomainError(409, "A newer source revision was already imported; this older receipt cannot replace it")
    active = {item.id: item for item in trial.resources}
    for resource in resources:
        previous = active.get(resource.id)
        if previous is not None:
            if not previous.available:
                raise DomainError(409, "This source option was retired; retrieve a new quote")
            if previous.model_dump(mode="json") != resource.model_dump(mode="json"):
                raise DomainError(409, "The imported source option changed; its immutable receipt cannot overwrite it")
        elif resource_history(trial, resource_id=resource.id):
            raise DomainError(409, "This source option was retired; retrieve a new quote")
    desired = {item.id for item in resources}
    old = [item.id for item in trial.resources if item.source == SOURCE
        and item.eligible_group_ids == [context["group_id"]] and item.available
        and item.booking_status != "confirmed" and item.id not in desired
        and (not stage or item.provenance.get("quote_id") != response["quote_id"])]
    untouched = {item.id for item in trial.resources} - set(old)
    untouched_locations = {item.id for item in trial.airports}
    if old:
        retire_travel_quotes(trial, context["group_id"], old)
    new_resources = [item for item in resources if item.id not in active]
    new_location = not any(item.id == destination.id for item in trial.airports)
    if not stage:
        ensure_inventory_capacity(trial, extra_resources=len(new_resources), extra_locations=int(new_location),
            keep_resource_ids=untouched | desired | set(keep_resource_ids),
            keep_location_ids=untouched_locations | {"OTP", "CRA"})
    if new_location:
        trial.airports.append(destination)
    trial.resources.extend(new_resources)
    applied = bool(new_resources or new_location or old)
    if applied:
        for field in ("resources", "airports", "groups", "offers", "reservations"):
            setattr(world, field, getattr(trial, field))
        world._inventory_context = trial._inventory_context
    return {"applied": applied, "resource_ids": [item.id for item in resources],
        "location_ids": [destination.id], "superseded_resource_ids": old}


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

    def _reserve(self, request, *, incoming_flight=None, after_flight_transfer=None):
        fingerprint = Store.fingerprint({**request.model_dump(mode="json"),
            **({"incoming_flight": incoming_fingerprint(incoming_flight), "after_flight_transfer": after_flight_transfer.model_dump(mode="json")}
                if incoming_flight is not None and after_flight_transfer is not None else {})})
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
                    current, _ = cab_context(world, request, self.provider, now=self.now(),
                incoming_flight=incoming_flight, after_flight_transfer=after_flight_transfer)
                    if current != saved["context"]:
                        raise DomainError(409, "The group's context changed after this search; no source request was repeated")
                    return {"response": {**saved["response"], "replayed": True}, "failure": saved.get("failure")}
                if status in ("dispatched", "unknown"):
                    active = status == "dispatched" and lease > self.now().timestamp()
                    raise DomainError(503, "This source request is processing or has an unknown outcome; it will not be dispatched again with this key",
                        {"in_progress": active, "unknown_outcome": not active}, applied=None)
                raise DomainError(409, "This source receipt expired; an explicit new search requires a new key")
            if request.expected_version != world.version:
                raise DomainError(409, "The operation changed before the source search; refresh it")
            context, query = cab_context(world, request, self.provider, now=self.now(),
                incoming_flight=incoming_flight, after_flight_transfer=after_flight_transfer)
            if conn.execute("SELECT 1 FROM airport_cab_receipts WHERE status='dispatched' AND lease_until>? LIMIT 1", (self.now().timestamp(),)).fetchone():
                raise DomainError(409, "An airport vehicle quote search is already running")
            identity = "ACQ-" + secrets.token_hex(12).upper()
            row = conn.execute("INSERT INTO airport_cab_receipts (id,request_key,fingerprint,scenario_id,status,lease_until) VALUES(?,?,?,?,?,?)",
                (identity, request.idempotency_key, fingerprint, world.scenario_id, "dispatched", self.now().timestamp() + 120))
            return {"quote_id": identity, "context": context, "query": query, "sequence": row.lastrowid, "world": world}

    def _dispatch(self, identity, context, request, *, incoming_flight=None, after_flight_transfer=None):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world = self.store._load(conn)
            current, _ = cab_context(world, request, self.provider, now=self.now(),
                incoming_flight=incoming_flight, after_flight_transfer=after_flight_transfer)
            if current != context:
                raise DomainError(409, "Passenger context changed before a source request; no further request was sent")
            updated = conn.execute("UPDATE airport_cab_receipts SET http_requests=http_requests+1 WHERE id=? AND status='dispatched' AND http_requests<2 AND lease_until>?", (identity, self.now().timestamp()))
            if updated.rowcount != 1:
                raise DomainError(409, "The source request allowance or lease was exhausted")

    def _finish(self, identity, context, response, failure=None, *, unknown=False, incoming_flight=None):
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
                        projected = world
                        if incoming_flight is not None:
                            group = postflight_group(world, context["group_id"])
                            projected = incoming_projection(world, group, incoming_flight, now=self.now())
                        build_airport_cab_resource(projected, context, response, option["id"], now=self.now())
                    except DomainError as exc:
                        reasons.append({"code": "option_not_usable", "message": str(exc)})
                    response["options"].append({"option_id": option["id"], "importable": not reasons, "blockers": reasons})
                if any(item["importable"] for item in response["options"]):
                    response["state"] = "ready"
            conn.execute("DELETE FROM airport_cab_cache WHERE expires<=?", (self.now().timestamp(),))
            conn.execute("INSERT INTO airport_cab_cache VALUES(?,?,?)", (identity, aware_timestamp(response["usable_until"]).timestamp(), json.dumps({"context": context, "response": response, "failure": failure})))
            conn.execute("DELETE FROM airport_cab_cache WHERE id IN (SELECT id FROM airport_cab_cache ORDER BY expires DESC LIMIT -1 OFFSET 64)")
            conn.execute("UPDATE airport_cab_receipts SET status=? WHERE id=?", ("unknown" if unknown else "done", identity))

    async def quote(self, request, *, request_runner=None, propagate_errors=False, incoming_flight=None, after_flight_transfer=None):
        # Private trusted orchestration inputs: the public manual route supplies
        # neither field. Freeze snapshots before the first await/HTTP phase.
        if (incoming_flight is None) != (after_flight_transfer is None):
            raise DomainError(422, "Both incoming evidence and explicit connection inputs are required")
        if incoming_flight is not None:
            from .after_flight_inputs import AfterFlightTransferInput
            incoming_flight = Resource.model_validate(incoming_flight.model_dump(mode="json"))
            after_flight_transfer = AfterFlightTransferInput.model_validate(after_flight_transfer).model_copy(deep=True)
        private = {"incoming_flight": incoming_flight, "after_flight_transfer": after_flight_transfer}
        reserved = await asyncio.to_thread(self._reserve, request, **private)
        if "response" in reserved:
            failure = reserved.get("failure")
            if propagate_errors and failure:
                if failure["kind"] == "domain":
                    raise DomainError(failure["status"], failure["message"])
                raise ProviderError(failure["message"], provider=SOURCE, code=failure["code"])
            return reserved["response"]
        identity, context, world = reserved["quote_id"], reserved["context"], reserved["world"]
        now = self.now()
        response = {"quote_id": identity, "quote_sequence": reserved["sequence"], "group_id": context["group_id"], "source_version": world.version,
            "created_at": now.isoformat(), "usable_until": (now + timedelta(seconds=120)).isoformat(),
            "state": "blocked", "transfer": None, "options": [], "blockers": [], "replayed": False}
        if incoming_flight is not None:
            response["usable_until"] = min(aware_timestamp(response["usable_until"]), incoming_deadline(incoming_flight)).isoformat()
        failure, source_error = None, None
        try:
            try:
                arguments = {"before_dispatch": lambda: asyncio.to_thread(self._dispatch, identity, context, request, **private)}
                if request_runner is not None:
                    arguments["request_runner"] = request_runner
                quote = await self.provider.quote(reserved["query"], **arguments)
                response["transfer"] = quote
                response["usable_until"] = min(aware_timestamp(response["usable_until"]),
                    aware_timestamp(quote["received_at"]) + timedelta(seconds=120),
                    request.pickup_at - timedelta(hours=quote["policy"]["required_advance_hours"])).isoformat()
            except (ProviderError, DomainError) as exc:
                response["blockers"].append({"code": getattr(exc, "code", "context_changed"), "message": str(exc)})
                source_error = exc
                failure = {"kind": "domain" if isinstance(exc, DomainError) else "provider", "message": str(exc),
                    "status": getattr(exc, "status", 502), "code": getattr(exc, "code", "context_changed")}
            unknown = propagate_errors and failure is not None and (failure["code"] in ("timeout", "connection") or str(failure["code"]).startswith("http_5"))
            saving = asyncio.create_task(asyncio.to_thread(self._finish, identity, context, response, failure, unknown=unknown, incoming_flight=incoming_flight))
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                raise
            if propagate_errors and source_error is not None:
                raise source_error
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
            appended = append_airport_cab_options(world, context, response, [request.option_id], now=self.now())
            if not appended["applied"]:
                return {"applied": False, "resource_ids": appended["resource_ids"], "explanation": "This immutable source option is already imported."}
            audit(world, "airport_vehicle_quote_imported", "Imported one group-scoped airport vehicle quote for planning; no vehicle reserved.", appended["resource_ids"][0],
                {"group_id": context["group_id"], "quote_id": quote_id, "source": SOURCE})
            scope = "its exact planned incoming-flight connection" if POSTFLIGHT_KEY in context else "initial-journey planning"
            return {"applied": True, "resource_ids": appended["resource_ids"], "explanation": f"Airport transfer added for {scope}. Driving arrival is estimated; no vehicle or complete journey is booked."}
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
