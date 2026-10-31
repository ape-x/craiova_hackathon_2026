"""Scoped initial airport/hotel and flight-bound return planning; never bookings."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .hotel_location import coordinate_timezone
from .initial_position import InitialPositionAssumption, initial_position_fingerprint
from .airport_arrival import AirportArrivalAssumption, onward_flight_fingerprint, valid_airport_arrival
from .solver import live_resource_current, resource_blocked_by_incident
from .weather import departure_allowed, landing_allowed
from .live_travel import retire_travel_quotes
from .models import Airport, Model, Mutation, Resource
from .pricing import AplQuoteFreshness, build_apl_price_evidence, aware_timestamp
from .providers.apl import APLQuoteRequest, LONDON
from .providers.base import ProviderError
from .scenario_schema import HORIZON_MINUTES, MAX_AIRPORTS, MAX_RESOURCES
from .store import DomainError, Store, audit, utcnow


CACHE_SECONDS = 120
MAX_SNAP_METERS = 100
MAX_AIRPORT_POINT_METERS = 10_000


class InitialPoint(Model):
    label: str = Field(min_length=2, max_length=160)
    latitude: float = Field(ge=-90, le=90, strict=True)
    longitude: float = Field(ge=-180, le=180, strict=True)
    confirmed: Literal[True]

    @field_validator("label")
    @classmethod
    def clean_label(cls, value):
        if len(value.strip()) < 2 or any(ord(char) < 32 for char in value):
            raise ValueError("Provide a named declared point without control characters")
        return value.strip()

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Confirm the operator-declared point")
        return value


class PlanningTransferRequest(Mutation):
    group_id: str = Field(min_length=1, max_length=80)
    hotel_node_id: str = Field(min_length=1, max_length=80)
    pickup_at: datetime
    suitcases: int = Field(ge=0, le=200, strict=True)
    direction: Literal["initial_airport_to_hotel", "hotel_to_airport"] = "initial_airport_to_hotel"
    initial_point: InitialPoint | None = None
    airport_dropoff: InitialPoint | None = None
    onward_resource_id: str | None = Field(default=None, min_length=1, max_length=80)
    airport_processing_minutes: int | None = Field(default=None, ge=1, le=720, strict=True)

    def receipt_payload(self):
        # Deployed outbound keys must retain their exact pre-return fingerprint.
        value = self.model_dump(mode="json")
        if self.direction == "initial_airport_to_hotel":
            for field in ("direction", "airport_dropoff", "onward_resource_id", "airport_processing_minutes"):
                value.pop(field)
        else:
            value.pop("initial_point")
        return value

    @model_validator(mode="after")
    def precise_pickup(self):
        if self.direction == "initial_airport_to_hotel":
            if self.initial_point is None or any(value is not None for value in (self.airport_dropoff, self.onward_resource_id, self.airport_processing_minutes)):
                raise ValueError("Initial airport-to-hotel planning requires initial_point and no return-flight inputs")
        elif self.initial_point is not None or any(value is None for value in (self.airport_dropoff, self.onward_resource_id, self.airport_processing_minutes)):
            raise ValueError("Hotel-to-airport planning requires airport_dropoff, onward_resource_id and airport_processing_minutes, without initial_point")
        value = self.pickup_at
        if value.tzinfo is None or value.utcoffset() is None or value.second or value.microsecond:
            raise ValueError("Pickup requires an explicit UTC offset and whole minute")
        local = value.astimezone(LONDON)
        if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("APL cannot distinguish repeated London clock times")
        return self


class PlanningTransferImport(Mutation):
    option_id: str = Field(min_length=1, max_length=200)
    policy_id: Literal["ecb_reference_v1"]


def blocker(code, message):
    return {"code": code, "message": message}


def meters_between(a, b):
    first, second = math.radians(a["latitude"]), math.radians(b["latitude"])
    dy, dx = second-first, math.radians(b["longitude"]-a["longitude"])
    hav = math.sin(dy/2)**2 + math.cos(first)*math.cos(second)*math.sin(dx/2)**2
    return 6_371_000 * 2 * math.asin(min(1, math.sqrt(hav)))


def terminal_ids(label):
    text=str(label).upper().strip()
    found={"T"+str(int(value)) for value in re.findall(r"\b(?:TERMINAL\s*|T\s*)([0-9]{1,2})\b",text)}
    if re.fullmatch(r"[0-9]{1,2}",text):
        found.add("T"+str(int(text)))
    for direction in ("NORTH","SOUTH"):
        if text==direction or re.search(rf"\b(?:{direction}\s+TERMINAL|TERMINAL\s+{direction})\b",text):
            found.add(direction)
    return found


def contains_address(source, returned):
    def tokens(value):
        # UK postcode spacing is cosmetic; street-number boundaries are not.
        text=re.sub(r"\b([A-Z]{1,2}[0-9][0-9A-Z]?)\s*([0-9][A-Z]{2})\b",r"\1\2",str(value).upper())
        return re.findall(r"[A-Z0-9]+",text)
    wanted, actual=tokens(source),tokens(returned)
    return bool(wanted) and any(actual[index:index+len(wanted)]==wanted for index in range(len(actual)-len(wanted)+1))


def full_postcodes(value):
    return {first+last for first,last in re.findall(r"\b([A-Z]{1,2}[0-9][0-9A-Z]?)\s*([0-9][A-Z]{2})\b",str(value).upper())}


def transfer_context(world, request, *, now):
    if world.mode != "live":
        raise DomainError(409, "Sourced transfer planning requires live mode")
    group = next((item for item in world.groups if item.id == request.group_id), None)
    if group is None:
        raise DomainError(404, "Passenger group not found")
    if group.status == "confirmed" or any(item.group_id == group.id and item.status == "confirmed" for item in world.offers):
        raise DomainError(409, "A supplier-confirmed group requires separate operator handling")
    if group.adult_count != group.size or group.accessible:
        raise DomainError(422, "This transfer flow requires an explicitly adult-only party without unverified accessibility or child-seat requirements")
    flight = None
    airport_id = group.origin
    point = request.initial_point
    if request.direction == "hotel_to_airport":
        flight = next((item for item in world.resources if item.id == request.onward_resource_id), None)
        if flight is None:
            raise DomainError(404, "Select an existing sourced onward flight")
        if (flight.kind != "flight" or flight.source == "synthetic" or flight.provenance.get("mode") != "live"
                or not flight.external_offer_id or flight.provenance.get("provider_offer_id") != flight.external_offer_id
                or flight.provenance.get("provider") != flight.source or flight.provenance.get("origin") != flight.origin
                or flight.provenance.get("destination") != flight.destination or flight.eligible_group_ids != [group.id]
                or flight.booking_status != "unbooked" or not flight.available or not live_resource_current(flight)
                or flight.capacity < group.size or flight.arrival > group.deadline or resource_blocked_by_incident(world, flight, group)):
            raise DomainError(409, "The onward flight must be a current, available sourced quote for this exact group and its incident constraints")
        try:
            actual_departure = aware_timestamp(flight.provenance["departure_at"])
            if math.floor((actual_departure-aware_timestamp(world.epoch)).total_seconds()/60) != flight.departure:
                raise ValueError("Departure does not match")
        except (KeyError, ValueError, TypeError) as exc:
            raise DomainError(409, "The onward flight lacks matching sourced departure evidence") from exc
        arrival_airport = next((item for item in world.airports if item.id == flight.destination and item.kind == "airport"), None)
        if arrival_airport is None or arrival_airport.operational_status == "closed" or not landing_allowed(arrival_airport):
            raise DomainError(409, "The onward flight's destination is not available for this journey")
        airport_id, point = flight.origin, request.airport_dropoff
    airport = next((item for item in world.airports if item.id == airport_id and item.kind == "airport"), None)
    catalog = lookup_airport(airport.code) if airport else None
    if airport is None or catalog is None or len(airport.code) != 3:
        raise DomainError(422, "The selected journey must identify a supported UK airport")
    try:
        valid_uk_points = coordinate_timezone(airport.lat, airport.lon) == "Europe/London" and coordinate_timezone(point.latitude, point.longitude) == "Europe/London"
    except ValueError:
        valid_uk_points = False
    if not valid_uk_points:
        raise DomainError(422, "This flow supports UK airport meeting points only")
    point_distance = meters_between({"latitude":catalog["lat"],"longitude":catalog["lon"]},point.model_dump())
    if point_distance > MAX_AIRPORT_POINT_METERS:
        raise DomainError(422, "The declared airport point is over 10 km from the airport reference. This app geographic screen does not verify a terminal or pedestrian access; no source request was sent")
    if len(terminal_ids(point.label)) > 1:
        raise DomainError(422, "Declare one terminal or airport point, not conflicting terminal alternatives")
    hotel = next((item for item in world.airports if item.id == request.hotel_node_id and item.kind == "hotel"), None)
    if hotel is None:
        raise DomainError(404, "Select an existing sourced hotel property")
    properties = [resource.provenance.get("property_location") for resource in world.resources if resource.source == "Nuitee / LiteAPI" and resource.kind == "hotel"]
    matches = [item for item in properties if isinstance(item, dict) and item.get("node_id") == hotel.id and item.get("latitude") == hotel.lat and item.get("longitude") == hotel.lon and isinstance(item.get("hotel_id"), str) and item["hotel_id"]]
    ids = {item["hotel_id"] for item in matches}
    if len(ids) != 1:
        raise DomainError(409, "The hotel node lacks one consistent sourced property identity")
    departure = (request.pickup_at-datetime.fromisoformat(world.epoch)).total_seconds()/60
    if request.pickup_at <= now or departure != int(departure) or departure < max(world.sim_time, group.ready_at) or departure > min(group.deadline,HORIZON_MINUTES):
        raise DomainError(422, "Pickup must be future, after the group's reported readiness and within its deadline")
    if flight is not None and (airport.id != airport.code or airport.operational_status == "closed" or not departure_allowed(airport) or int(departure)+request.airport_processing_minutes >= flight.departure):
        raise DomainError(422, "The airport must permit departure and pickup must leave time for the declared airport processing before the exact onward flight")
    identity = {"scenario": world.scenario_id, "group_id": group.id, "airport_context_id": airport.id, **point.model_dump()}
    point_id = "MP-"+Store.fingerprint(identity)[:24].upper()
    assumption = InitialPositionAssumption(source="operator_declared", confirmed=True, group_id=group.id,
        group_fingerprint=initial_position_fingerprint(world, group), scenario_id=world.scenario_id, epoch=world.epoch,
        airport_context_id=airport.id, point_id=point_id, label=point.label,
        coordinates={"latitude":point.latitude,"longitude":point.longitude}, ready_at=group.ready_at)
    onward = None
    if flight is not None:
        assumption = AirportArrivalAssumption(source="operator_declared", confirmed=True, group_id=group.id,
            group_fingerprint=initial_position_fingerprint(world, group), scenario_id=world.scenario_id, epoch=world.epoch,
            airport_id=airport.id, onward_resource_id=flight.id, onward_flight_fingerprint=onward_flight_fingerprint(flight),
            onward_departure_at=actual_departure.isoformat(), dropoff={"label":point.label,"coordinates":{"latitude":point.latitude,"longitude":point.longitude}},
            processing_minutes=request.airport_processing_minutes, departure_terminal=None)
        onward = {"resource_id":flight.id,"origin":flight.origin,"destination":flight.destination,
            "departure_at":actual_departure.isoformat(),"departure":flight.departure,"source":flight.source,"departure_terminal":None}
    return {"direction":request.direction,"airport_id":airport.id,"airport_point":{"label":point.label,"coordinates":{"latitude":point.latitude,"longitude":point.longitude}},"onward_flight":onward,
        "scenario_id":world.scenario_id,"source_version":world.version,"group_id":group.id,"size":group.size,
        "hotel_node_id":hotel.id,"hotel_id":next(iter(ids)),"hotel_name":hotel.name,
        "hotel_coordinates":{"latitude":hotel.lat,"longitude":hotel.lon},"airport_code":airport.code,
        "airport_reference_distance_m":round(point_distance,2),
        "airport_name":airport.name,"departure":int(departure),"deadline":group.deadline,
        "assumption":assumption.model_dump(mode="json"),"request":request.receipt_payload()}


def route_points(context):
    if context.get("direction") == "hotel_to_airport":
        return context["hotel_coordinates"], context["assumption"]["dropoff"]["coordinates"]
    return context["assumption"]["coordinates"], context["hotel_coordinates"]


def resource_direction(resource):
    return resource.provenance.get("direction", "initial_airport_to_hotel")


def build_transfer_resource(world, context, response, option_id, *, now):
    """Compile verified server source receipts without I/O or mutating a world.

    Jobs may use this against a projected world; the caller still owns revision
    retirement, capacity limits and an atomic operational commit.
    """
    fresh = transfer_context(world, PlanningTransferRequest.model_validate(context["request"]), now=now)
    if (fresh["scenario_id"] != context["scenario_id"] or fresh["assumption"] != context["assumption"]
            or fresh["hotel_coordinates"] != context["hotel_coordinates"] or fresh["hotel_id"] != context["hotel_id"]):
        raise DomainError(409, "Passenger, onward flight or property context changed; retrieve a current planning quote")
    if aware_timestamp(response["usable_until"]) <= now:
        raise DomainError(409, "Planning source receipt expired; retrieve a new explicit quote")
    eligibility = next((item for item in response["options"] if item["option_id"] == option_id), None)
    if response["state"] != "ready" or not eligibility or not eligibility["importable"]:
        raise DomainError(422, "This source option has unresolved import blockers", eligibility or {"blockers":response["blockers"]})
    quote, route = response["transfer"],response["route"]
    try:
        evidence = build_apl_price_evidence(quote,option_id,now=now)
    except (ValueError,TypeError,KeyError) as exc:
        raise DomainError(409, "The quote or ECB reference is no longer usable; revalidate before planning") from exc
    fingerprint = Store.fingerprint({"quote_id":response["quote_id"],"option_id":option_id,"context":context["assumption"],"quote":quote,"route":route})
    resource_id = "APL-"+fingerprint[:28].upper()
    is_return = context.get("direction") == "hotel_to_airport"
    assumption, initial, point = None, None, None
    if is_return:
        assumption = AirportArrivalAssumption.model_validate(context["assumption"])
        origin, destination = context["hotel_node_id"],assumption.airport_id
    else:
        initial = InitialPositionAssumption.model_validate(context["assumption"])
        origin, destination = initial.point_id,context["hotel_node_id"]
        existing = next((item for item in world.airports if item.id == initial.point_id), None)
        if existing and (existing.kind != "meeting_point" or existing.lat != initial.coordinates.latitude or existing.lon != initial.coordinates.longitude):
            raise DomainError(409, "Meeting-point identity conflicts with stored geography")
        if existing is None:
            point = Airport(id=initial.point_id,code=initial.point_id,name=initial.label,lat=initial.coordinates.latitude,lon=initial.coordinates.longitude,kind="meeting_point",diversion_eligible=False)
    option = next(item for item in quote["options"] if item["id"] == option_id)
    arrival = context["departure"]+int((Decimal(route["duration_seconds"])/60).to_integral_value(rounding=ROUND_CEILING))
    resource = Resource(id=resource_id,kind="bus",transport_mode="private_transfer",name="APL "+option["car_type"],origin=origin,destination=destination,
        departure=context["departure"],arrival=arrival,capacity=context["size"],accessible=False,cost_per_person=0,source="Airport Pickups London",status="quoted",
        eligible_group_ids=[context["group_id"]],quote_total_cost=None,budget_total_cost=float(Decimal(evidence.estimated_eur.amount)),valid_until=None,requires_revalidation=True,
        capacity_scope="private_vehicle_quote",price_evidence=evidence,quote_freshness=AplQuoteFreshness(basis="app_receipt",received_at=quote["received_at"],max_age_seconds=CACHE_SECONDS),
        initial_position_assumption=initial,airport_arrival_assumption=assumption,
        provenance={"apl_quote":copy.deepcopy(quote),"apl_option_id":option_id,"google_route":copy.deepcopy(route),
            "source_hotel":copy.deepcopy(response["hotel"]),"road_snap_offsets_m":copy.deepcopy(response.get("road_snap_offsets_m")),
            "geographic_screening":copy.deepcopy(response["geographic_screening"]),"direction":context.get("direction","initial_airport_to_hotel"),
            "quote_id":response["quote_id"],"quote_sequence":context["quote_sequence"],"estimated_arrival":True,"supplier_booked":False})
    group = next(item for item in world.groups if item.id == context["group_id"])
    if is_return and not valid_airport_arrival(world,resource,group,now=now):
        raise DomainError(409, "The source return route no longer supports the exact onward flight and declared airport processing")
    return resource, point


class GroundPlanning:
    def __init__(self, store, transfers, routes, *, now=None):
        self.store, self.transfers, self.routes = store, transfers, routes
        self.now = now or utcnow
        with store.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS transfer_quote_receipts (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    scenario_id TEXT NOT NULL, status TEXT NOT NULL, lease_until REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS transfer_quote_cache (
                    id TEXT PRIMARY KEY, expires REAL NOT NULL, data TEXT NOT NULL);
            """)

    def purge(self):
        with self.store.connect() as conn:
            conn.execute("DELETE FROM transfer_quote_cache WHERE expires<=?", (self.now().timestamp(),))
            conn.execute("UPDATE transfer_quote_receipts SET status='unknown' WHERE status='dispatched' AND lease_until<=?", (self.now().timestamp(),))

    def _context(self, world, request):
        return transfer_context(world, request, now=self.now())

    def _reserve(self, request):
        payload = request.receipt_payload()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT id,fingerprint,scenario_id,status,lease_until FROM transfer_quote_receipts WHERE request_key=?", (request.idempotency_key,)).fetchone()
            world = self.store._load(conn)
            if previous:
                identity, fingerprint, scenario, status, lease = previous
                if fingerprint != Store.fingerprint(payload):
                    raise DomainError(409, "This transfer-search key identifies different inputs")
                if scenario != world.scenario_id:
                    raise DomainError(409, "This search belongs to an earlier operation; no provider request was repeated")
                cached = conn.execute("SELECT data,expires FROM transfer_quote_cache WHERE id=?", (identity,)).fetchone()
                if cached and cached[1] > self.now().timestamp():
                    return {"response":{**json.loads(cached[0])["response"],"replayed":True}}
                if status == "dispatched" and lease > self.now().timestamp():
                    raise DomainError(503, "This transfer search is still processing. Retry the same request key to recover its result.", {"in_progress":True,"retry_after_seconds":2}, applied=None)
                if status in ("dispatched", "unknown"):
                    raise DomainError(503, "The earlier source dispatch has an unknown outcome and will not be repeated automatically. Review before starting an explicit new search.", {"unknown_outcome":True}, applied=None)
                raise DomainError(409, "The earlier quote cache expired; the same key will not dispatch another search")
            if world.version != request.expected_version:
                raise DomainError(409, "Operation changed before the transfer search; refresh the passenger context")
            context = self._context(world, request)
            active = conn.execute("SELECT COUNT(*) FROM transfer_quote_receipts WHERE status='dispatched' AND lease_until>?", (self.now().timestamp(),)).fetchone()[0]
            if active:
                raise DomainError(409, "A planning transfer search is already running; wait before starting another")
            identity = "TQ-"+secrets.token_hex(12).upper()
            inserted=conn.execute("INSERT INTO transfer_quote_receipts VALUES(?,?,?,?,?,?)", (identity,request.idempotency_key,Store.fingerprint(payload),world.scenario_id,"dispatched",self.now().timestamp()+120))
            context["quote_sequence"]=inserted.lastrowid
            return {"quote_id":identity,"context":context}

    def _finish(self, identity, context, response):
        expiry = aware_timestamp(response["usable_until"]).timestamp()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM transfer_quote_cache WHERE expires<=?", (self.now().timestamp(),))
            conn.execute("INSERT INTO transfer_quote_cache VALUES(?,?,?)", (identity,expiry,json.dumps({"context":context,"response":response})))
            conn.execute("DELETE FROM transfer_quote_cache WHERE id IN (SELECT id FROM transfer_quote_cache ORDER BY expires DESC LIMIT -1 OFFSET 64)")
            conn.execute("UPDATE transfer_quote_receipts SET status='done' WHERE id=?", (identity,))

    async def quote(self, request: PlanningTransferRequest):
        reserved = await asyncio.to_thread(self._reserve, request)
        if "response" in reserved:
            return reserved["response"]
        identity, context = reserved["quote_id"], reserved["context"]
        assumption = context["assumption"]
        is_return = context.get("direction") == "hotel_to_airport"
        response = {"quote_id":identity,"state":"blocked","group_id":context["group_id"],"hotel_node_id":context["hotel_node_id"],
            "direction":context.get("direction","initial_airport_to_hotel"),"source_version":context["source_version"],"created_at":self.now().isoformat(),
            "usable_until":(self.now()+timedelta(seconds=CACHE_SECONDS)).isoformat(),
            "initial_point":None if is_return else {"id":assumption["point_id"],"label":assumption["label"],**assumption["coordinates"],
                "source":"operator_declared","confirmed":True,"airport_context_id":assumption["airport_context_id"]},
            "airport_arrival_assumption":copy.deepcopy(assumption) if is_return else None,
            "onward_flight":copy.deepcopy(context.get("onward_flight")),
            "geographic_screening":{"airport_reference_distance_m":context["airport_reference_distance_m"],"airport_reference_limit_m":MAX_AIRPORT_POINT_METERS,"road_snap_limit_m":MAX_SNAP_METERS,"policy_source":"application","terminal_or_pedestrian_access_verified":False},
            "hotel":None,"transfer":None,"route":None,"blockers":[],"options":[],"replayed":False}
        try:
            await self._source(context, request, response)
            self._assess(context, response)
            # A request cancellation must not abandon an in-flight SQLite
            # receipt commit or create a second dispatch on an original-key retry.
            saving = asyncio.create_task(asyncio.to_thread(self._finish,identity,context,response))
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                raise
            return response
        except BaseException:
            with self.store.connect() as conn:
                conn.execute("UPDATE transfer_quote_receipts SET status='unknown' WHERE id=? AND status='dispatched'", (identity,))
            raise

    async def _source(self, context, request, response):
        if not self.transfers.provider.configured or not self.routes.google_configured:
            response["blockers"].append(blocker("provider_not_configured", "APL and Google server routing must both be configured; no source request was sent"))
            return
        try:
            metadata = await self.routes.hotel_location(context["hotel_id"])
        except ProviderError as exc:
            response["blockers"].append(blocker("hotel_"+exc.code, str(exc)))
            return
        postal = metadata.get("postal_address")
        if metadata.get("latitude") != context["hotel_coordinates"]["latitude"] or metadata.get("longitude") != context["hotel_coordinates"]["longitude"]:
            response["blockers"].append(blocker("property_location_changed", "The exact supplier property's coordinates changed; refresh its sourced hotel node before planning a transfer"))
            return
        if not isinstance(postal, dict) or postal.get("hotel_id") != context["hotel_id"] or not postal.get("address") or not postal.get("country"):
            response["blockers"].append(blocker("property_address_missing", "The supplier has not provided a usable address and country for this exact hotel; no APL or route request was sent"))
            return
        if re.sub(r"[^a-z]", "", postal["country"].lower()) not in ("gb","uk","unitedkingdom","greatbritain") or coordinate_timezone(context["hotel_coordinates"]["latitude"],context["hotel_coordinates"]["longitude"]) != "Europe/London":
            response["blockers"].append(blocker("property_outside_scope", "This transfer flow supports sourced UK hotel properties only"))
            return
        response["hotel"] = {"node_id":context["hotel_node_id"],"hotel_id":context["hotel_id"],"name":postal.get("name") or context["hotel_name"],
            "address":postal["address"],"city":postal.get("city"),"country":postal["country"],**context["hotel_coordinates"],
            "source":postal.get("source"),"retrieved_at":postal.get("retrieved_at")}
        destination = ", ".join(dict.fromkeys(str(item).strip() for item in (postal.get("name") or context["hotel_name"], postal["address"], postal.get("city"), postal["country"]) if item))
        point = request.airport_dropoff if request.direction == "hotel_to_airport" else request.initial_point
        origin = f'{context["airport_name"]} ({context["airport_code"]}), {point.label}'
        if request.direction == "hotel_to_airport":
            origin, destination = destination, origin
        if len(origin) > 500 or len(destination) > 500:
            response["blockers"].append(blocker("source_address_too_long", "The exact property or pickup address exceeds APL's supported input; it was not truncated"))
            return
        query = APLQuoteRequest(origin=origin,destination=destination,passengers=context["size"],suitcases=request.suitcases,pickup_at=request.pickup_at,expected_hub=context["airport_code"],expected_direction="to_hub" if request.direction == "hotel_to_airport" else "from_hub")
        context["apl_request"] = query.model_dump(mode="json")
        route_origin, route_destination = route_points(context)
        tasks = [asyncio.create_task(self.transfers.quote(query)),
                 asyncio.create_task(self.routes.dated_route(route_origin,route_destination,request.pickup_at))]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(("transfer","route"), results):
            if isinstance(result, (ProviderError,DomainError)):
                response["blockers"].append(blocker(name+"_"+getattr(result,"code","unavailable"),str(result)))
            elif isinstance(result, BaseException):
                response["blockers"].append(blocker(name+"_unknown", "A source request did not produce a verified result; it was not retried"))
            else:
                response[name] = result

    def _assess(self, context, response):
        quote, route = response["transfer"], response["route"]
        is_return = context.get("direction") == "hotel_to_airport"
        point_label = context["assumption"]["dropoff"]["label"] if is_return else context["assumption"]["label"]
        limits = [aware_timestamp(response["usable_until"]), aware_timestamp(context["request"]["pickup_at"])]
        if quote:
            limits.append(aware_timestamp(quote["received_at"])+timedelta(seconds=CACHE_SECONDS))
            echoed = quote.get("request",{})
            expected_request = context.get("apl_request",{})
            try:
                pickup_matches = aware_timestamp(echoed.get("pickup_at")) == aware_timestamp(expected_request.get("pickup_at"))
            except (ValueError,TypeError):
                pickup_matches = False
            if not pickup_matches or any(echoed.get(key) != value for key,value in expected_request.items() if key!="pickup_at"):
                response["blockers"].append(blocker("supplier_request_mismatch", "The server-held APL receipt does not match the exact pickup, destination, party and luggage request"))
            resolved = quote.get("resolved_route", {})
            if resolved.get("hub") not in (None,context["airport_code"]) or resolved.get("direction") not in (None,"to_hub" if is_return else "from_hub"):
                response["blockers"].append(blocker("supplier_route_conflict", "APL returned a conflicting airport or direction"))
            # A supplier pickup terminal is not an onward flight's departure terminal.
            terminal = None if is_return else resolved.get("pickup_terminal")
            stated_terminal, returned_terminal = terminal_ids(point_label), terminal_ids(terminal) if terminal else set()
            if terminal and (len(stated_terminal)!=1 or len(returned_terminal)!=1 or stated_terminal!=returned_terminal):
                response["blockers"].append(blocker("supplier_terminal_conflict", "The returned supplier terminal does not match the operator-declared meeting point"))
            meeting = quote.get("meeting_point")
            meeting_terms = terminal_ids(meeting.get("name")) if isinstance(meeting,dict) and meeting.get("name") else set()
            if not is_return and meeting_terms and (len(stated_terminal)!=1 or meeting_terms!=stated_terminal):
                response["blockers"].append(blocker("supplier_terminal_conflict", "APL's pickup instructions name a different terminal from the operator-declared initial point"))
            if is_return and resolved.get("dropoff") is not None:
                dropoff = resolved["dropoff"]
                returned_terms = terminal_ids(dropoff)
                if not (contains_address(context["airport_code"],dropoff) or contains_address(context["airport_name"],dropoff)):
                    response["blockers"].append(blocker("supplier_airport_unverified", "APL's returned dropoff cannot be reconciled to the selected onward airport"))
                if returned_terms and (len(stated_terminal)!=1 or returned_terms!=stated_terminal):
                    response["blockers"].append(blocker("supplier_terminal_conflict", "APL's returned dropoff conflicts with the operator-declared airport point; the flight terminal remains unknown"))
            address = response["hotel"]["address"] if response["hotel"] else None
            city = response["hotel"].get("city") if response["hotel"] else None
            source_postcodes = full_postcodes(address) if address else set()
            destinations = [resolved[key] for key in ("resolved_address","pickup" if is_return else "dropoff") if resolved.get(key) is not None]
            conflicts = [value for value in destinations if address and (not contains_address(address,value)
                or (city and not contains_address(city,value) and not (source_postcodes and source_postcodes <= full_postcodes(value))))]
            if conflicts:
                response["blockers"].append(blocker("supplier_address_unverified", "APL's returned hotel endpoint cannot be reconciled to this supplier property's exact sourced address"))
            if not quote.get("options"):
                response["blockers"].append(blocker("no_vehicle_options", "APL returned no vehicle options for this exact request"))
        if route:
            limits.append(aware_timestamp(route["fetched_at"])+timedelta(seconds=CACHE_SECONDS))
            expected_origin, expected_destination = route_points(context)
            if route["request"]["origin"] != expected_origin or route["request"]["destination"] != expected_destination or aware_timestamp(route["request"]["departure_time"]) != aware_timestamp(context["request"]["pickup_at"]):
                response["blockers"].append(blocker("route_request_mismatch", "The dated route evidence does not match the requested points and pickup"))
            offsets = {side:round(meters_between(route["request"][side],route["road_endpoints"][side]),2) for side in ("origin","destination")}
            response["road_snap_offsets_m"] = offsets
            if any(value > MAX_SNAP_METERS for value in offsets.values()):
                response["blockers"].append(blocker("road_connection_unverified", "Google's road endpoint is over 100 metres from a requested point; this app screening rule cannot verify that physical connection"))
            arrival = context["departure"]+int((Decimal(route["duration_seconds"])/60).to_integral_value(rounding=ROUND_CEILING))
            if is_return and arrival+context["assumption"]["processing_minutes"] > context["onward_flight"]["departure"]:
                response["blockers"].append(blocker("airport_processing_time", "The driving estimate plus the operator-declared airport processing time misses the exact onward flight"))
            if arrival > min(context["deadline"],HORIZON_MINUTES):
                response["blockers"].append(blocker("arrival_deadline", "Even the sourced driving estimate arrives after the group's deadline"))
        if quote and route:
            for option in quote["options"]:
                reasons = list(response["blockers"])
                if not option.get("fits_request"):
                    reasons.append(blocker("party_or_luggage", "This vehicle quote cannot fit the declared party and suitcases"))
                try:
                    build_apl_price_evidence(quote,option["id"],now=self.now())
                except (ValueError,TypeError,KeyError) as exc:
                    reasons.append(blocker("reference_price_unavailable", str(exc)))
                response["options"].append({"option_id":option["id"],"importable":not reasons,"blockers":reasons})
            if any(item["importable"] for item in response["options"]):
                response["state"] = "ready"
        response["usable_until"] = min(limits).isoformat()

    def import_quote(self, quote_id, request: PlanningTransferImport):
        payload = {"quote_id":quote_id,**request.model_dump(mode="json")}
        def apply(world):
            with self.store.connect() as conn:
                row = conn.execute("SELECT data,expires FROM transfer_quote_cache WHERE id=?", (quote_id,)).fetchone()
            if not row or row[1] <= self.now().timestamp():
                raise DomainError(409, "Planning quote expired; retrieve a new explicit source quote")
            cached = json.loads(row[0])
            context, response = cached["context"], cached["response"]
            resource, point = build_transfer_resource(world,context,response,request.option_id,now=self.now())
            if any(item.id == resource.id for item in world.resources):
                return {"resource_ids":[resource.id],"applied":False,"explanation":"This immutable planning quote is already imported."}
            direction = resource_direction(resource)
            same_direction = [item for item in world.resources if item.source=="Airport Pickups London"
                and item.eligible_group_ids==[context["group_id"]] and resource_direction(item)==direction]
            if any(item.provenance.get("quote_sequence",0)>context["quote_sequence"] for item in same_direction):
                raise DomainError(409,"A newer APL source revision was already imported for this group and direction; an older cached quote cannot replace it")
            if len(world.resources) >= MAX_RESOURCES:
                raise DomainError(422,f"Operation limit is {MAX_RESOURCES} resources")
            if point is not None and len(world.airports) >= MAX_AIRPORTS:
                raise DomainError(422,f"Operation limit is {MAX_AIRPORTS} airport, station, property and meeting-point locations")
            old = [item.id for item in same_direction if item.booking_status != "confirmed" and item.available]
            retire_travel_quotes(world,context["group_id"],old)
            if point is not None:
                world.airports.append(point)
            world.resources.append(resource)
            audit(world,"transfer_quote_imported","Imported a group-only APL planning quote with declared position/airport-processing assumptions and estimated EUR cost; no vehicle is reserved.",resource.id,
                {"group_id":context["group_id"],"quote_id":quote_id,"policy_id":request.policy_id,"direction":direction})
            detail = "hotel-to-airport leg is bound to the exact onward flight and operator-declared processing time" if direction=="hotel_to_airport" else "initial-point to hotel leg is available for planning"
            return {"resource_ids":[resource.id],"applied":True,"explanation":f"The sourced {detail}. EUR and driving arrival are estimates; no vehicle or complete journey is booked."}
        return self.store.mutate("transfer-import:"+request.idempotency_key,payload,request.expected_version,apply)


def create_planning_transfer_router(service, operator_dependency):
    router = APIRouter(prefix="/api/transfers",dependencies=[Depends(operator_dependency)])
    @router.post("/planning-quotes")
    async def quote(request: PlanningTransferRequest):
        return await service.quote(request)
    @router.post("/planning-quotes/{quote_id}/import")
    def import_quote(quote_id: str, request: PlanningTransferImport):
        return service.import_quote(quote_id,request)
    return router
