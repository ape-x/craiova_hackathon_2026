"""One exact Romanian property and two sourced scheduled coaches, without bookings."""
from __future__ import annotations

import asyncio
import copy
from contextlib import suppress
from datetime import datetime, timedelta
from decimal import Decimal
import time
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .airport_constraints import airport_operation_search_allowed, case_airport_operation_blocked
from .hotel_location import property_location, property_zone
from .hotel_recovery import _immutable_hotel, _parallel, _rates, _restore_rates
from .inventory_lifecycle import ensure_inventory_capacity, validate_location_identity
from .live_service import HotelQuoteImport, _hotel_budget_total
from .live_travel import append_travel_quote, retire_travel_quotes
from .models import Model, Strategy
from .planning_window import planning_horizon_end
from .pricing import aware_timestamp
from .providers.base import ProviderError
from .providers.reference_fx import SOURCE_URL as ECB_URL
from .real_data import RealDataRequest
from .solver import build_candidate, validate_plan
from .stay_requirements import HotelStayRequirement, REQUIREMENT_KEY
from .store import DomainError, Store, utcnow
from .travel import TravelSearchRequest
from .weather import passenger_arrival_pending


REQUIRED_CALLS = 8
HOTEL_ID = "lpce923"
ZONE = property_zone("Europe/Bucharest")
MAX_COACH_OPTIONS = 3
DISCLOSURE = (
    "One exact Hotel Aro Palace property, one dated coach search in each direction, "
    "at most three eligible scheduled services per direction, and one onward flight date. "
    "Direct Aeroport schedules use a conservative 12-hour advance rule, 20-minute boarding allowance "
    "and at least 3 hours between return arrival and flight departure. Stops are facility-level; "
    "the exact door/bay remains unknown. RON fares use explicit ECB EUR planning estimates. "
    "Seat counts are observations, not held seats, and no supplier booking is made."
)


class RomanianHotelRecoveryInputs(Model):
    source: Literal["transfero_direct_aeroport"]
    hotel_id: Literal["lpce923"]
    checkin_at: datetime
    checkout_at: datetime
    rooms: int = Field(ge=1, le=12, strict=True)
    minimum_rest_minutes: int = Field(ge=0, le=10080, strict=True)
    suitcases: int = Field(ge=0, le=12, strict=True)
    handbags: int = Field(ge=0, le=12, strict=True)
    standard_baggage_confirmed: Literal[True]
    airport_processing_minutes: int = Field(ge=180, le=720, strict=True)
    hotel_connection_minutes: int = Field(ge=20, le=720, strict=True)

    @field_validator("checkin_at", "checkout_at", mode="before")
    @classmethod
    def explicit_instant(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Supply exact Romanian stay times with UTC offsets")
        value = aware_timestamp(value.isoformat() if isinstance(value, datetime) else value)
        if value.second or value.microsecond or value.utcoffset().total_seconds() % 60:
            raise ValueError("Supply whole-minute Romanian stay times with a minute-aligned UTC offset")
        return value

    @field_validator("standard_baggage_confirmed", mode="before")
    @classmethod
    def explicit_baggage(cls, value):
        if value is not True:
            raise ValueError("Explicitly confirm standard baggage: at most one 25 kg hold bag and one 10 kg hand bag per adult")
        return value

    @model_validator(mode="after")
    def coherent_stay(self):
        self.requirement()
        if self.checkin_at.astimezone(ZONE).date() >= self.checkout_at.astimezone(ZONE).date():
            raise ValueError("Romanian hotel checkout must be on a later property-local date")
        return self

    def requirement(self):
        return HotelStayRequirement(source="operator_requested", checkin_at=self.checkin_at.isoformat(),
            checkout_at=self.checkout_at.isoformat(), rooms=self.rooms, minimum_rest_minutes=self.minimum_rest_minutes)


def validate_romanian_hotel_job(world, request):
    """Known prerequisites only: no source request, inferred bag, or passenger movement."""
    inputs = getattr(request, "romanian_hotel_recovery", None)
    if inputs is None:
        return
    if (len(request.group_ids) != 1 or set(request.modes) != {"flight", "hotel", "bus"}
            or request.hotel_recovery is not None or request.airport_transfers or request.after_flight_transfer is not None):
        raise DomainError(422, "Romanian hotel recovery requires one exact group, Flight, Hotel and Ground transport, and no other package or transfer inputs")
    if request.max_provider_calls < REQUIRED_CALLS:
        raise DomainError(422, "The Romanian hotel package requires at least eight source/model calls; the caller's allowance was not increased", {"required_provider_calls": REQUIRED_CALLS})
    group = next((item for item in world.groups if item.id == request.group_ids[0]), None)
    if (world.mode != "live" or world.currency != "EUR" or group is None or group.adult_count != group.size
            or group.accessible or not 1 <= group.size <= 12 or inputs.rooms > group.size
            or inputs.suitcases > group.size or inputs.handbags > group.size):
        raise DomainError(422, "Romanian hotel recovery needs one explicitly adult-only party of 1–12, no unverified accessibility requirement, at least one adult per room, and no more than one standard hold and hand bag per adult")
    nodes = {item.id: item for item in world.airports}
    origin, destination = nodes.get(group.origin), nodes.get(group.destination)
    if (origin is None or origin.kind != "airport" or origin.id != "OTP" or origin.code != "OTP"
            or destination is None or destination.kind != "airport" or destination.id != destination.code
            or lookup_airport(destination.code) is None or destination.code == "OTP"):
        raise DomainError(422, "This package starts at OTP, uses the exact Hotel Aro Palace stop and returns to OTP before a flight to a different final airport")
    if passenger_arrival_pending(world, group):
        raise DomainError(422, "Record explicit passenger readiness at OTP before the Romanian hotel package; aircraft arrival does not establish passenger position")
    for node, operation in ((origin, "departures"), (destination, "arrivals")):
        if case_airport_operation_blocked(world, group, node.code, operation) or not airport_operation_search_allowed(node, operation):
            raise DomainError(422, "The Romanian package's onward flight conflicts with current airport or case restrictions")
    epoch, now = aware_timestamp(world.epoch), utcnow()
    ready = epoch + timedelta(minutes=max(world.sim_time, group.ready_at))
    latest = epoch + timedelta(minutes=min(group.deadline, planning_horizon_end(world)))
    if (inputs.checkin_at <= max(now, ready) or inputs.checkout_at + timedelta(minutes=inputs.hotel_connection_minutes + inputs.airport_processing_minutes) >= latest
            or inputs.checkin_at <= now + timedelta(hours=12)):
        raise DomainError(422, "The exact hotel stay must follow reported readiness, leave the conservative 12-hour booking notice, and fit the group's deadline with coach boarding and airport margins")


def _exact_property(metadata):
    """Correlate the independently sourced property to the operator's named stop."""
    import re
    import unicodedata
    def normalized(value):
        return "".join(char for char in unicodedata.normalize("NFKD", str(value or "").lower()) if not unicodedata.combining(char))
    try:
        postal = metadata["postal_address"]
        address = normalized(postal["address"])
        if (postal["hotel_id"] != HOTEL_ID or normalized(postal["name"]) != "hotel aro palace"
                or normalized(postal["city"]) != "brasov" or postal["country"] != "RO"
                or "eroilor" not in address or re.findall(r"\d+", address) not in (["27"], ["27", "29"])
                or metadata["latitude"] != 45.645322 or metadata["longitude"] != 25.590917):
            raise ValueError("Property identity changed")
    except (KeyError, TypeError, ValueError):
        raise DomainError(422, "The exact LiteAPI hotel no longer matches Hotel Aro Palace at Eroilor 27–29, Brașov; no nearby property or stop was substituted") from None


async def _exact_rates(provider, query):
    items = await provider.hotel_offers(HOTEL_ID, query)
    if not isinstance(items, list) or len(items) > 1 or any(not isinstance(item, dict) or item.get("id") != HOTEL_ID for item in items):
        raise ProviderError("Exact-property rates did not preserve the requested hotel identity", provider="Nuitee / LiteAPI", code="invalid_response")
    return {"source": "Nuitee / LiteAPI", "items": items}


def _complete_package(plan, group_id, hotel_id, outward, returns, flights):
    allocations = [item for item in plan.allocations if item.group_id == group_id]
    if len(allocations) != 1:
        return False
    legs = allocations[0].itinerary.legs
    return (len(legs) == 4 and [leg.kind for leg in legs] == ["bus", "hotel", "bus", "flight"]
        and legs[0].resource_id in outward and legs[1].resource_id == hotel_id
        and legs[2].resource_id in returns and legs[3].resource_id in flights)


def _coach_resource(*args, **kwargs):
    from .transfero_evidence import build_transfero_resource
    try:
        return build_transfero_resource(*args, **kwargs)
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        raise DomainError(422, "The exact coach receipt no longer satisfies the source, pricing, group, baggage or connection contract") from exc


async def run_romanian_hotel_recovery(jobs, job, owner, world, started):
    from .recovery_jobs import RecoveryJobRequest, gap, journey_waits, scope_fingerprint
    from .providers.transfero import SOURCE, TransferoSearchRequest

    request = RecoveryJobRequest.model_validate({key: value for key, value in job["_request"].items() if key != "incident_id"})
    validate_romanian_hotel_job(world, request)
    inputs, group_id = request.romanian_hotel_recovery, request.group_ids[0]
    scope = {group_id}
    real, coach, fx = jobs.hotel_provider, jobs.transfero_provider, jobs.fx_provider
    async def blocked(code, message):
        await jobs._update(job, owner, status="blocked", stage="finished", message=message,
            gaps=[gap(code, message, [group_id])])
    if not (jobs.travel.flight_configured and real and real.hotel_offer_provider == "liteapi"
            and real.liteapi_configured and not real.liteapi_sandbox and coach and coach.configured and fx):
        await blocked("romanian_package_provider_unavailable", "The Romanian package requires real flight, exact hotel rates/metadata, Transfero and ECB sources; no supplier was called.")
        return
    await jobs._update(job, owner, message=DISCLOSURE, gaps=[])
    projected = world.model_copy(deep=True)
    group = next(item for item in projected.groups if item.id == group_id)
    requirement = inputs.requirement().model_dump(mode="json")
    group.provenance[REQUIREMENT_KEY] = copy.deepcopy(requirement)
    epoch = aware_timestamp(world.epoch)
    results = {}
    for direction, day in (("otp_to_hotel", inputs.checkin_at.astimezone(ZONE).date()), ("hotel_to_otp", inputs.checkout_at.astimezone(ZONE).date())):
        query = TransferoSearchRequest(direction=direction, travel_date=day, adults=group.size,
            suitcases=inputs.suitcases, handbags=inputs.handbags, standard_baggage_confirmed=inputs.standard_baggage_confirmed)
        coach.validate_request(query)
        result = await jobs._call(job, owner, "searching_ground", SOURCE, query.model_dump(mode="json"), [group_id], lambda: coach.search(query))
        if await jobs._cancelled(job, owner):
            return
        if not result.get("options"):
            await blocked("romanian_coach_unavailable", "The requested dated coach direction has no supported party-specific seat/price result. No substitute date, stop or service was queried.")
            return
        results[direction] = result
    hotel_query = RealDataRequest(airport_code="OTP", destination_code=group.destination,
        checkin=inputs.checkin_at.astimezone(ZONE).date(), checkout=inputs.checkout_at.astimezone(ZONE).date(), adults=group.size, rooms=inputs.rooms)
    rates, metadata, fx_result = await _parallel([
        jobs._call(job, owner, "searching_hotels", "Nuitee / LiteAPI", {"hotel_id": HOTEL_ID, **hotel_query.model_dump(mode="json")}, [group_id], lambda: _exact_rates(real, hotel_query)),
        jobs._call(job, owner, "locating_hotel", "Nuitee / LiteAPI", {"hotel_id": HOTEL_ID}, [group_id], lambda: real.hotel_location(HOTEL_ID)),
        jobs._call(job, owner, "reference_fx", "European Central Bank", {"url": ECB_URL, "currency": "RON"}, [group_id], lambda: _rates(fx)),
    ])
    if await jobs._cancelled(job, owner):
        return
    _exact_property(metadata)
    if not rates["items"]:
        await blocked("romanian_hotel_unavailable", "The exact Hotel Aro Palace property returned no dated room offer for the selected party; no other hotel was substituted.")
        return
    quote = rates["items"][0]
    try:
        complete = _hotel_budget_total(quote, Decimal(quote["total_price"]))
    except (KeyError, ValueError, TypeError, ArithmeticError):
        complete = None
    if complete is None:
        await blocked("romanian_hotel_pricing_incomplete", "The exact hotel's mandatory costs are incomplete; no complete-budget plan or onward flight query was produced.")
        return
    located = {**quote, **metadata}
    hotel_request = HotelQuoteImport(group_id=group_id, airport_code="OTP", checkin=hotel_query.checkin, checkout=hotel_query.checkout,
        rooms=inputs.rooms, checkin_at=inputs.checkin_at, checkout_at=inputs.checkout_at, stay_times_confirmed=False,
        expected_version=world.version, idempotency_key="romanian-hotel-" + job["id"])
    try:
        hotel = _immutable_hotel(projected, hotel_request, group, located)
    except DomainError as exc:
        if exc.status != 422:
            raise
        await blocked("romanian_hotel_policy_unusable", exc.message)
        return
    node, _ = property_location(hotel.provenance)
    validate_location_identity(projected, node)
    if not any(item.id == node.id for item in projected.airports):
        projected.airports.append(node)
    projected.resources.append(hotel)
    enriched = {direction: fx.enrich(result, _restore_rates(fx_result), source_currency="RON") for direction, result in results.items()}
    assembled = {}
    for direction, result in enriched.items():
        possible = []
        for option in result["options"]:
            try:
                resource = _coach_resource(projected, group, inputs, node, result, option["id"], now=utcnow())
            except DomainError as exc:
                if exc.status == 422:
                    continue
                raise
            if direction == "otp_to_hotel":
                if (resource.departure < max(projected.sim_time, group.ready_at) + 20
                        or resource.arrival + inputs.hotel_connection_minutes > hotel.departure):
                    continue
            elif resource.departure < hotel.arrival + inputs.hotel_connection_minutes:
                continue
            possible.append((resource, option["id"]))
        possible.sort(key=lambda item: (item[0].budget_total_cost,
            -item[0].arrival if direction == "otp_to_hotel" else item[0].arrival, item[0].id))
        assembled[direction] = possible[:MAX_COACH_OPTIONS]
        if not assembled[direction]:
            await blocked("romanian_coach_connection_unusable", "No fresh sourced coach for this direction fits the declared stay, readiness, bags, 12-hour notice and boarding margins. No flight was queried.")
            return
        projected.resources.extend(resource for resource, _ in assembled[direction])
    fresh_ids = {hotel.id} | {resource.id for items in assembled.values() for resource, _ in items}
    for resource in projected.resources:
        if resource.id not in fresh_ids and resource.eligible_group_ids == [group_id] and resource.booking_status != "confirmed":
            resource.available = False
    earliest = min(aware_timestamp(option["arrival_at"]) for option in enriched["hotel_to_otp"]["options"]
        if option["id"] in {identity for _, identity in assembled["hotel_to_otp"]}) + timedelta(minutes=inputs.airport_processing_minutes)
    latest = epoch + timedelta(minutes=group.deadline)
    if earliest >= latest:
        await blocked("romanian_flight_window_closed", "The actual return coach plus the declared airport margin leaves no onward flight window before the deadline.")
        return
    flight_query = TravelSearchRequest(kind="flight", origin_code="OTP", destination_code=group.destination,
        departure_date=earliest.astimezone(ZONE).date(), adults=group.size, earliest_departure_at=earliest, latest_arrival_at=latest)
    flight_option = {"id": "QRY-" + Store.fingerprint(flight_query.model_dump(mode="json"))[:20], "group_id": group_id,
        "query": flight_query.model_dump(mode="json"), "earliest_departure": int((earliest-epoch).total_seconds()/60), "latest_arrival": group.deadline}
    ai_request = {"objective": job["objective"], "instructions": request.instructions, "context": DISCLOSURE,
        "max_search_calls": 1, "romanian_hotel_recovery": inputs.model_dump(mode="json")}
    scoped = projected.model_copy(deep=True)
    scoped.groups = [group]
    scoped.offers = [item for item in scoped.offers if item.group_id == group_id]
    timings = {"interpretation_ms": 0.0, "solver_ms": 0.0, "decisions_ms": 0.0}
    before = time.perf_counter()
    selection = await jobs._call(job, owner, "planning_queries", "OpenAI Responses", ai_request, [group_id],
        lambda: jobs.ai.plan_recovery_queries(scoped.model_dump(mode="json"), [flight_option], ai_request))
    timings["interpretation_ms"] = round((time.perf_counter()-before)*1000, 2)
    if selection.get("query_ids") != [flight_option["id"]]:
        raise DomainError(422, "AI did not choose the one exact sourced-connection flight query; no flight supplier was called")
    strategies = [Strategy.model_validate(item) for item in selection["strategies"]]
    if not 1 <= len(strategies) <= 3 or len({item.id for item in strategies}) != len(strategies) or any(item.preferred_airport is not None for item in strategies):
        raise DomainError(502, "AI returned invalid bounded Romanian hotel strategies")
    # Source observations may expire during model work. Never buy a flight query
    # using a connection that no longer survives the same original time window.
    surviving = {}
    for direction, entries in assembled.items():
        surviving[direction] = []
        for resource, option_id in entries:
            try:
                checked = _coach_resource(projected, group, inputs, node, enriched[direction], option_id, now=utcnow())
                if checked.id != resource.id:
                    continue
                if direction == "hotel_to_otp" and aware_timestamp(next(item["arrival_at"] for item in enriched[direction]["options"] if item["id"] == option_id)) + timedelta(minutes=inputs.airport_processing_minutes) > earliest:
                    continue
                surviving[direction].append((resource, option_id))
            except DomainError:
                continue
        if not surviving[direction]:
            await blocked("romanian_connection_expired", "The sourced coach connection expired or stopped covering the approved flight window during query planning. No flight call or automatic refresh was made.")
            return
    _immutable_hotel(projected, hotel_request, group, located)
    flights = await jobs._call(job, owner, "searching_flights", jobs.travel.flight_source, flight_option, [group_id], lambda: jobs._search(flight_query))
    imported, superseded = [], set()
    for flight in flights["items"]:
        try:
            trial = projected.model_copy(deep=True)
            result = append_travel_quote(trial, flight, flight_option["query"], group, "flight", stage=True)
            identity = result["resource_ids"][0]
            projected = trial
            imported.append((flight, identity))
            superseded.update(result.get("superseded_resource_ids", []))
        except DomainError as exc:
            if isinstance(exc.details, dict) and exc.details.get("quote_revision_conflict"):
                raise
    active = {resource.id for resource in projected.resources if resource.available}
    imported = [item for item in imported if item[1] in active]
    outward_ids = {resource.id for resource, _ in assembled["otp_to_hotel"]}
    return_ids = {resource.id for resource, _ in assembled["hotel_to_otp"]}
    flight_ids = {identity for _, identity in imported}
    before = time.perf_counter()
    candidates = await asyncio.to_thread(lambda: [build_candidate(projected, strategy, scope_group_ids=scope) for strategy in strategies])
    candidates = [plan for plan in candidates if plan.validation["valid"] and plan.recovered_passengers == group.size
        and _complete_package(plan, group_id, hotel.id, outward_ids, return_ids, flight_ids)]
    timings["solver_ms"] = round((time.perf_counter()-before)*1000, 2)
    if not candidates:
        await blocked("no_complete_romanian_hotel_journey", "No complete sourced Romanian hotel journey fits the actual coach stock, stay, airport margin, flight, budget and deadline. No partial proposal was applied.")
        return
    decision_request = {"text": f"Recover only group {group_id} using the explicitly requested Romanian hotel package. Objective: {job['objective']}. {request.instructions}",
        "context": DISCLOSURE + " All other allocations are fixed."}
    before = time.perf_counter()
    choice = await jobs._call(job, owner, "deciding", "OpenAI Decisions", {"candidate_ids": [plan.id for plan in candidates], "request": decision_request}, [group_id],
        lambda: jobs.ai.choose(projected.model_dump(mode="json"), [plan.model_dump(mode="json") for plan in candidates], decision_request))
    timings["decisions_ms"] = round((time.perf_counter()-before)*1000, 2)
    plan = next((item for item in candidates if item.id == choice.get("plan_id")), None)
    if plan is None:
        raise DomainError(502, "Decisions did not choose a validated complete Romanian hotel package")
    if await jobs._cancelled(job, owner):
        return
    chosen = {leg.resource_id for allocation in plan.allocations for leg in allocation.itinerary.legs}
    waits, completed_result = journey_waits(projected, plan), {}
    def apply(current):
        if scope_fingerprint(current, job["incident_id"], [group_id]) != job["_scope_hash"]:
            raise DomainError(409, "Romanian recovery scope changed before commit")
        original_ids = {item.id for item in current.resources}
        original_locations = {item.id for item in current.airports}
        current_group = next(item for item in current.groups if item.id == group_id)
        current_group.provenance[REQUIREMENT_KEY] = copy.deepcopy(requirement)
        retired = set(superseded)
        retired.update(item.id for item in current.resources if item.eligible_group_ids == [group_id] and item.booking_status != "confirmed"
            and item.id not in chosen and ((item.source == SOURCE) or (item.kind == "hotel" and item.provenance.get("id") == HOTEL_ID)))
        retire_travel_quotes(current, group_id, retired)
        for flight, identity in imported:
            if identity in chosen:
                result = append_travel_quote(current, flight, flight_option["query"], current_group, "flight", stage=True)
                if result["resource_ids"] != [identity]:
                    raise DomainError(409, "The chosen flight revision changed before package commit")
                retired.update(result.get("superseded_resource_ids", []))
        checked_hotel = _immutable_hotel(current, hotel_request, current_group, located)
        if checked_hotel.id != hotel.id or hotel.id not in chosen:
            raise DomainError(409, "The exact chosen hotel changed before package commit")
        validate_location_identity(current, node)
        if not any(item.id == node.id for item in current.airports):
            current.airports.append(node.model_copy(deep=True))
        if not any(item.id == hotel.id for item in current.resources):
            current.resources.append(checked_hotel)
        for direction, entries in assembled.items():
            for resource, option_id in entries:
                if resource.id not in chosen:
                    continue
                checked = _coach_resource(current, current_group, inputs, node, enriched[direction], option_id, now=utcnow())
                if checked.id != resource.id:
                    raise DomainError(409, "The chosen coach source revision changed before package commit")
                if not any(item.id == checked.id for item in current.resources):
                    current.resources.append(checked)
        ensure_inventory_capacity(current, keep_resource_ids=chosen | (original_ids-retired), keep_location_ids=original_locations | {node.id})
        validation = validate_plan(current, plan, scope_group_ids=scope)
        if not validation["valid"]:
            raise DomainError(409, "The complete Romanian package failed current independent validation", validation)
        old_offers = {item.id for item in current.offers}
        answer = jobs.service._hold_plan(current, plan, "ai", decision_request["text"], choice.get("provider", "OpenAI Decisions"),
            choice["explanation"], candidates, round((time.perf_counter()-started)*1000, 2), timings=timings, scope_group_ids=scope)
        legs = plan.allocations[0].itinerary.legs
        selected_times = {direction: next(item["departure_at"] for item in enriched[direction]["options"]
            if item["id"] == next(option_id for resource, option_id in assembled[direction] if resource.id in chosen)) for direction in assembled}
        completed_result.update(decision_id=answer["decision"]["id"], plan_id=plan.id, recovered_passengers=plan.recovered_passengers, exception_passengers=0,
            offer_ids=[item.id for item in current.offers if item.id not in old_offers], resource_ids=sorted(chosen), waits=waits,
            source_counts={flights["source"]: len(flights["items"]), "Nuitee / LiteAPI": len(rates["items"]), SOURCE: sum(len(item["options"]) for item in results.values()), "European Central Bank": 1},
            romanian_hotel_recovery={"inputs": inputs.model_dump(mode="json"), "hotel_id": HOTEL_ID, "hotel_node_id": node.id,
                "outbound_resource_id": legs[0].resource_id, "return_resource_id": legs[2].resource_id, "onward_resource_id": legs[3].resource_id,
                "outbound_departure_at": selected_times["otp_to_hotel"], "return_departure_at": selected_times["hotel_to_otp"],
                "flight_query_date": flight_query.departure_date.isoformat(), "required_provider_calls": REQUIRED_CALLS,
                "policy": {"minimum_advance_hours": 12, "boarding_minutes": 20, "minimum_airport_margin_minutes": 180, "facility_level_only": True}})
        return answer
    def completed(conn, response):
        jobs.ledger.complete_in_transaction(conn, job["id"], owner, response, completed_result)
        committed = jobs.store._load(conn)
        selected_group = next(item for item in committed.groups if item.id == group_id)
        jobs.romanian_hotel_profiles.save_in_transaction(conn, committed, selected_group, inputs, job["id"])
    task = asyncio.create_task(asyncio.to_thread(jobs.store.mutate, "recovery-job-apply:"+job["id"], {"job_id": job["id"]}, world.version,
        apply, on_commit=completed, request_started=started))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise

