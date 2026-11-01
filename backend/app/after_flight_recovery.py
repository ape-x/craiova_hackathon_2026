"""Bounded discovery of an incoming source flight and its exact airport pickup.

Search intent is not inventory. Only actual source flights and subsequently
quoted vehicle categories enter an ephemeral candidate world; the final commit
imports the chosen flight before its bound vehicle and never moves passengers.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import math
import time

from .after_flight_inputs import AfterFlightTransferInput
from .airport_catalog import lookup_airport
from .airport_constraints import airport_operation_search_allowed, case_airport_operation_blocked
from .airport_cab_planning import AirportCabPlanningRequest, append_airport_cab_options, cab_context
from .hotel_location import coordinate_timezone, property_zone
from .inventory_lifecycle import ensure_inventory_capacity, resource_history
from .live_travel import append_travel_quote
from .models import Strategy
from .planning_window import planning_horizon_end
from .pricing import aware_timestamp
from .providers.base import ProviderError
from .providers.bucharest_cab import SOURCE
from .solver import build_candidate, fixed_offers, validate_plan
from .store import DomainError, Store
from .travel import TravelSearchRequest
from .weather import passenger_arrival_pending


REQUIRED_CALLS = 6
MAX_SEARCH_DATES = 7
AFTER_FLIGHT_CONTEXT = (
    "The operator requested a planned incoming flight to OTP followed by a Bucharest.CAB "
    "vehicle to CRA, within an explicit pickup window and processing allowance. An incoming "
    "flight query is search intent only: no ground connection exists until a real category "
    "is quoted for that exact flight. Source flight arrivals are supplier schedules, not "
    "observed passenger arrivals. Pickups use the later of the declared window start and "
    "the source arrival plus explicit processing rounded up to a whole minute; they are "
    "never silently shifted to satisfy source lead time. Vehicles are unreserved category "
    "quotes with native EUR cash prices and no optional extras. Road arrivals are estimates "
    "without departure-time traffic; terminal or curb access is unverified. Fleet stock and "
    "supplier expiry are unknown. At most one category is allowed across the combined plan "
    "and fixed allocations by application policy. All other groups' allocations are fixed. "
    "These are sourced proposals, not bookings or guaranteed arrivals."
)


def validate_after_flight_job(world, request, airport_cab):
    """Known source prerequisites; no I/O, relocation, source facts or inferred bags."""
    inputs = getattr(request, "after_flight_transfer", None)
    if inputs is None:
        return
    if (request.group_ids != [inputs.group_id] or set(request.modes) != {"flight", "bus"}
            or request.hotel_recovery is not None or request.airport_transfers):
        raise DomainError(422, "After-flight airport recovery requires exactly its one selected group, Flight and Ground transport, and no hotel or initial airport transfer inputs")
    if request.max_provider_calls < REQUIRED_CALLS:
        raise DomainError(422, "Allow at least six calls: query planning, one dated flight search, incoming-flight selection, two vehicle source requests and the final decision", {"required_provider_calls": REQUIRED_CALLS})
    if world.mode != "live" or world.currency != "EUR" or airport_cab is None or not airport_cab.provider.configured:
        raise DomainError(422, "After-flight sourcing requires the live EUR operation and the configured airport vehicle source")
    group = next((item for item in world.groups if item.id == inputs.group_id), None)
    if group is None or group.adult_count != group.size or not 1 <= group.size <= 7 or group.accessible:
        raise DomainError(422, "After-flight sourcing requires one explicitly adult-only group of 1–7 passengers without an unverified accessibility requirement")
    origin = next((item for item in world.airports if item.id == group.origin and item.kind == "airport"), None)
    reference = lookup_airport(origin.code) if origin else None
    if (origin is None or origin.id != origin.code or reference is None
            or (origin.lat, origin.lon) != (reference["lat"], reference["lon"])
            or origin.code == "OTP" or group.destination != "CRA"):
        raise DomainError(422, "After-flight recovery starts at a known airport other than OTP and preserves CRA as the group's final airport destination")
    if passenger_arrival_pending(world, group):
        raise DomainError(422, "Record explicit passenger readiness before sourcing an onward incoming flight; an aircraft arrival was not assumed")
    for code, operation in ((origin.code, "departures"), ("OTP", "arrivals")):
        if (case_airport_operation_blocked(world, group, code, operation)
                or any(item.id == code and not airport_operation_search_allowed(item, operation) for item in world.airports)):
            raise DomainError(422, "The incoming flight conflicts with current case, airport or weather restrictions")
    epoch, now = aware_timestamp(world.epoch), airport_cab.now()
    ready = epoch + timedelta(minutes=max(world.sim_time, group.ready_at))
    latest = epoch + timedelta(minutes=min(group.deadline, planning_horizon_end(world)))
    if (inputs.pickup_window_start <= now or inputs.pickup_window_start < ready
            or inputs.pickup_window_end > latest
            or inputs.pickup_window_end - timedelta(minutes=inputs.airport_processing_minutes) <= max(now, ready) + timedelta(minutes=30)):
        raise DomainError(422, "The explicit pickup window must be future, after readiness, and within the group's deadline and planning horizon, leaving time for the incoming flight and declared processing")


def incoming_query_options(world, group, inputs, now):
    """Date-bounded prospective searches, with no provisional ground resource."""
    epoch = aware_timestamp(world.epoch)
    earliest = max(now, epoch + timedelta(minutes=max(world.sim_time, group.ready_at))) + timedelta(minutes=30)
    latest = min(epoch + timedelta(minutes=group.deadline), inputs.pickup_window_end - timedelta(minutes=inputs.airport_processing_minutes))
    if latest <= earliest:
        return []
    origin = lookup_airport(group.origin)
    zone = property_zone(coordinate_timezone(origin["lat"], origin["lon"]))
    first, last = earliest.astimezone(zone).date(), latest.astimezone(zone).date()
    result = []
    for offset in range(min(MAX_SEARCH_DATES, (last-first).days+1)):
        query = TravelSearchRequest(kind="flight", origin_code=origin["code"], destination_code="OTP",
            departure_date=first + timedelta(days=offset), adults=group.size,
            earliest_departure_at=earliest, latest_arrival_at=latest)
        data = {"group_id": group.id, "query": query.model_dump(mode="json"),
            "earliest_departure": math.ceil((earliest-epoch).total_seconds()/60),
            "latest_arrival": math.floor((latest-epoch).total_seconds()/60),
            "purpose": "incoming_flight_for_explicit_after_flight_transfer"}
        result.append({"id": "QRY-" + Store.fingerprint(data)[:20], **data})
    return result


def _incoming_current(world, group, incoming, inputs, now):
    from .postflight_connection import derive_postflight_pickup, incoming_deadline, incoming_projection
    try:
        pickup = derive_postflight_pickup(incoming, inputs)
        eligible = (incoming.kind == "flight" and incoming.origin == group.origin and incoming.destination == "OTP"
            and incoming.eligible_group_ids == [group.id] and pickup <= inputs.pickup_window_end and now < incoming_deadline(incoming))
        if eligible:
            incoming_projection(world, group, incoming, now=now)
        return eligible
    except (ValueError, TypeError, KeyError, DomainError):
        return False


def _chosen_binding(plan, group_id, branches):
    if len(plan.allocations) != 1 or plan.allocations[0].group_id != group_id:
        return None
    legs = plan.allocations[0].itinerary.legs
    if len(legs) != 2:
        return None
    return next((entry for entry in branches if entry["incoming_id"] == legs[0].resource_id
        and legs[1].resource_id in entry["resources"]), None)


async def run_after_flight_recovery(jobs, job, owner, world, started):
    from .postflight_connection import derive_postflight_pickup
    from .recovery_jobs import RecoveryJobRequest, gap, journey_waits, scope_fingerprint

    request = RecoveryJobRequest.model_validate({key: value for key, value in job["_request"].items() if key != "incident_id"})
    validate_after_flight_job(world, request, jobs.airport_cab)
    inputs = request.after_flight_transfer
    group = next(item for item in world.groups if item.id == inputs.group_id)
    scope, gaps, source_counts = {group.id}, [], {}
    timings = {"interpretation_ms": 0.0, "solver_ms": 0.0, "decisions_ms": 0.0}

    async def blocked(code, message):
        gaps.append(gap(code, message, [group.id]))
        await jobs._update(job, owner, status="blocked", stage="finished", message=message, gaps=gaps)

    fixed_ids = {leg.resource_id for offer in fixed_offers(world, scope) for leg in offer.itinerary.legs}
    if any(resource_history(world, resource_id=identity, source=SOURCE) for identity in fixed_ids):
        await blocked("airport_transfer_fleet_unverified", "A fixed allocation already uses this vehicle source. Additional fleet availability is unknown; no incoming flight or second vehicle was queried.")
        return
    if not jobs.travel.flight_configured:
        await blocked("flight_provider_not_configured", "Connect the real flight-offer provider; FR24 observations do not supply priced seats.")
        return
    options = incoming_query_options(world, group, inputs, jobs.airport_cab.now())
    if not options:
        await blocked("after_flight_window_uncovered", "No dated incoming flight search fits the declared readiness, pickup window and processing allowance.")
        return
    scoped = world.model_copy(deep=True)
    scoped.groups = [item for item in scoped.groups if item.id == group.id]
    scoped.offers = [item for item in scoped.offers if item.group_id == group.id]
    planner_request = {"objective": job["objective"], "instructions": request.instructions, "max_search_calls": 1,
        "context": AFTER_FLIGHT_CONTEXT, "after_flight_transfer": inputs.model_dump(mode="json"),
        "incident": next(item.model_dump(exclude={"flight", "flight_ref", "flight_context_state"}) for item in world.incidents if item.id == job["incident_id"])}
    before = time.perf_counter()
    searches = await jobs._call(job, owner, "planning_queries", "OpenAI Responses", planner_request, [group.id],
        lambda: jobs.ai.plan_recovery_queries(scoped.model_dump(mode="json"), options, planner_request))
    timings["interpretation_ms"] = round((time.perf_counter()-before)*1000, 2)
    known = {item["id"]: item for item in options}
    selected = searches.get("query_ids", [])
    if not isinstance(selected, list) or len(selected) > 1 or any(not isinstance(item, str) or item not in known for item in selected):
        raise DomainError(502, "AI returned an unapproved incoming flight search; no supplier call was issued")
    strategies = [Strategy.model_validate(item) for item in searches["strategies"]]
    if not 1 <= len(strategies) <= 3 or len({item.id for item in strategies}) != len(strategies) or any(item.preferred_airport is not None for item in strategies):
        raise DomainError(502, "AI returned invalid after-flight recovery policies")
    if not selected:
        await blocked("search_allowance_uncovered", "AI selected no incoming flight search within this request's single-query allowance; no source was queried.")
        return
    if await jobs._cancelled(job, owner):
        return
    option = known[selected[0]]
    query = TravelSearchRequest.model_validate(option["query"])
    await jobs._update(job, owner, stage="searching_flights", message="Sourcing one dated incoming flight search; no vehicle connection has yet been assumed.")
    flights = await jobs._call(job, owner, "searching_flights", jobs.travel.flight_source, option, [group.id], lambda: jobs._search(query))
    source_counts[flights["source"]] = len(flights["items"])
    flight_world, imported = world.model_copy(deep=True), {}
    for quote in flights["items"]:
        try:
            trial = flight_world.model_copy(deep=True)
            added = append_travel_quote(trial, quote, option["query"], group, "flight", stage=True)
            identity = added["resource_ids"][0]
            resource = next(item for item in trial.resources if item.id == identity)
            if not _incoming_current(trial, group, resource, inputs, jobs.airport_cab.now()):
                raise DomainError(422, "The incoming quote does not satisfy the current route, window, party and source constraints")
            flight_world, imported[identity] = trial, quote
        except DomainError as exc:
            if isinstance(exc.details, dict) and exc.details.get("quote_revision_conflict"):
                raise
            reason = gap("incoming_quote_not_usable", exc.message, [group.id])
            if reason not in gaps:
                gaps.append(reason)
    incoming = {item.id: item for item in flight_world.resources if item.id in imported and _incoming_current(flight_world, group, item, inputs, jobs.airport_cab.now())}
    if not incoming:
        await blocked("no_incoming_flight_offers", "No current sourced incoming flight fits the declared pickup connection. No vehicle request or allocation was made.")
        return
    recorded = await asyncio.to_thread(jobs.ledger.get, job["id"], private=True)
    # Reserve the shortlist model call and final decision before two HTTP calls
    # per candidate. An operator maximum of two is not a promise to spend eight.
    limit = min(inputs.max_inbound_options, (job["max_provider_calls"]-recorded["calls_dispatched"]-2)//2)
    if limit < 1:
        await blocked("after_flight_allowance_uncovered", "The remaining allowance cannot cover incoming selection, one complete vehicle quote and the final decision.")
        return
    candidates = [{"resource_id": item.id, "origin": item.origin, "destination": item.destination,
        "departure_at": item.provenance["departure_at"], "arrival_at": item.provenance["arrival_at"],
        "source": item.source, "total_cost_eur": item.quote_total_cost,
        "expires_at": item.valid_until, "derived_pickup_at": derive_postflight_pickup(item, inputs).isoformat()}
        for item in incoming.values()]
    shortlist_request = {"objective": job["objective"], "instructions": request.instructions,
        "max_inbound_options": limit, "after_flight_transfer": inputs.model_dump(mode="json"), "context": AFTER_FLIGHT_CONTEXT}
    await jobs._update(job, owner, stage="selecting_incoming_flights", gaps=gaps,
        message="AI is selecting exact incoming source offers for bounded vehicle quotation.")
    shortlist = await jobs._call(job, owner, "selecting_incoming_flights", "OpenAI Responses", {"candidates": candidates, "request": shortlist_request}, [group.id],
        lambda: jobs.ai.select_airport_transfer_flights(scoped.model_dump(mode="json"), candidates, shortlist_request))
    ids = shortlist.get("flight_resource_ids")
    if not isinstance(ids, list) or len(ids) > limit or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids) or not set(ids) <= incoming.keys():
        raise DomainError(502, "AI returned an unapproved incoming flight identity; no vehicle call was issued")
    if not ids:
        await blocked("no_incoming_flight_selected", "AI selected no incoming source offer for vehicle quotation; no replacement offer was invented.")
        return
    projected, branches, observed = flight_world.model_copy(deep=True), [], []
    await jobs._update(job, owner, stage="sourcing_airport_transfers", message="Checking a fresh vehicle quote for each explicitly selected incoming flight.")
    for identity in ids:
        if await jobs._cancelled(job, owner):
            return
        flight = incoming[identity]
        if not _incoming_current(flight_world, group, flight, inputs, jobs.airport_cab.now()):
            gaps.append(gap("incoming_flight_expired", "The exact incoming offer expired before its vehicle request. No refresh, replacement flight or shifted pickup was issued.", [group.id]))
            continue
        pickup = derive_postflight_pickup(flight, inputs)
        cab_request = AirportCabPlanningRequest(group_id=group.id, destination_airport="CRA", pickup_at=pickup,
            suitcases=inputs.suitcases, handbags=inputs.handbags, expected_version=job["_planning_version"],
            idempotency_key="recovery-postflight-" + Store.fingerprint({"job_id": job["id"], "incoming_id": identity}))
        context, _ = cab_context(world, cab_request, jobs.airport_cab.provider, now=jobs.airport_cab.now(), incoming_flight=flight, after_flight_transfer=inputs)

        async def phase_runner(phase, operation):
            if phase not in ("bootstrap", "vehicles"):
                raise DomainError(502, "Airport source requested an unsupported dispatch phase")
            if not _incoming_current(flight_world, group, flight, inputs, jobs.airport_cab.now()):
                raise DomainError(409, "The exact incoming source flight expired before another vehicle request; no further call was issued")
            private = {}

            async def execute():
                private["value"] = await operation()
                return {"bootstrap_received": True} if phase == "bootstrap" else private["value"]

            await jobs._call(job, owner, "searching_airport_transfer", SOURCE,
                {"phase": phase, "incoming_resource_id": identity, "pickup_at": pickup.isoformat(),
                    "after_flight_transfer": inputs.model_dump(mode="json")}, [group.id], execute, optional_reference=True)
            return private["value"]

        try:
            response = await jobs.airport_cab.quote(cab_request, request_runner=phase_runner, propagate_errors=True,
                incoming_flight=flight, after_flight_transfer=inputs)
        except (TimeoutError, ProviderError) as exc:
            code = getattr(exc, "code", "timeout")
            if code in ("no_vehicles", "pickup_too_soon"):
                gaps.append(gap("airport_transfer_source_rejected", "The source rejected this shortlisted flight's exact vehicle request ("+code+"). No pickup was shifted and no unselected flight was substituted.", [group.id]))
                await jobs._update(job, owner, gaps=gaps)
                continue
            unknown = code in ("timeout", "connection") or str(code).startswith("http_5")
            message = str(exc) if isinstance(exc, ProviderError) else "Provider deadline reached; the source outcome and charges are unconfirmed."
            await asyncio.to_thread(jobs.ledger.halt, job["id"], owner, "unknown_outcome" if unknown else "failed", message+" No retry was issued.")
            raise
        usable = {entry["option_id"] for entry in response["options"] if entry["importable"]}
        source_options = (response.get("transfer") or {}).get("options", [])
        observed.append({"group_id": group.id, "quote_id": response["quote_id"], "quote_sequence": response["quote_sequence"],
            "pickup_at": pickup.isoformat(), "usable_until": response["usable_until"], "state": response["state"],
            "http_request_count": (response.get("transfer") or {}).get("http_request_count", 0), "arrival_estimated": True,
            "supplier_booked": False, "blockers": response["blockers"], "postflight_connection": context["postflight_connection"],
            "options": [{**{key: item[key] for key in ("id", "car_type", "price", "max_passengers", "max_suitcases", "max_handbags", "fits_request")},
                "importable": item["id"] in usable} for item in source_options]})
        source_counts[SOURCE] = source_counts.get(SOURCE, 0)+len(source_options)
        await jobs._update(job, owner, airport_transfer_quotes=observed, source_counts=source_counts, gaps=gaps)
        if not usable:
            gaps.append(gap("airport_transfer_not_usable", "The source returned no importable category for this exact incoming flight and declared pickup inputs.", [group.id]))
            continue
        # Every branch starts from the same actual flight world. A later receipt
        # is an alternative, not permission to retire the earlier ephemeral one.
        branch = flight_world.model_copy(deep=True)
        added = append_airport_cab_options(branch, context, response, sorted(usable), now=jobs.airport_cab.now(), stage=True)
        resources = {item.id: item.provenance["airport_cab_option_id"] for item in branch.resources if item.id in added["resource_ids"]}
        branches.append({"incoming_id": identity, "context": context, "response": response, "resources": resources})
        projected.resources.extend(item.model_copy(deep=True) for item in branch.resources if item.id in resources)
        known_nodes = {item.id for item in projected.airports}
        projected.airports.extend(item.model_copy(deep=True) for item in branch.airports if item.id not in known_nodes)
    if await jobs._cancelled(job, owner):
        return
    fresh = {identity for entry in branches for identity in entry["resources"]}
    for item in projected.resources:
        if item.eligible_group_ids == [group.id] and item.id not in fresh | set(ids) and item.booking_status != "confirmed":
            item.available = False
    await jobs._update(job, owner, stage="evaluating", gaps=gaps, message="Validating complete flight-to-vehicle journeys and their exact source bindings.")
    before = time.perf_counter()
    plans = await asyncio.to_thread(lambda: [build_candidate(projected, strategy, scope_group_ids=scope) for strategy in strategies])
    plans = [item for item in plans if item.validation["valid"] and _chosen_binding(item, group.id, branches) is not None]
    timings["solver_ms"] = round((time.perf_counter()-before)*1000, 2)
    if not plans:
        await blocked("no_complete_journey", "No complete current incoming-flight and sourced-vehicle pair satisfies the exact connection, deadline, budget and fixed-allocation checks. No operational inventory changed.")
        return
    if await jobs._cancelled(job, owner):
        return
    decision_request = {"text": f"Recover only incident {job['incident_id']} group {group.id}. Objective: {job['objective']}. {request.instructions}", "context": AFTER_FLIGHT_CONTEXT}
    before = time.perf_counter()
    choice = await jobs._call(job, owner, "deciding", "OpenAI Decisions", {"candidate_ids": [item.id for item in plans], "request": decision_request}, [group.id],
        lambda: jobs.ai.choose(projected.model_dump(mode="json"), [item.model_dump(mode="json") for item in plans], decision_request))
    timings["decisions_ms"] = round((time.perf_counter()-before)*1000, 2)
    plan = next((item for item in plans if item.id == choice.get("plan_id")), None)
    if plan is None:
        raise DomainError(502, "Decisions selected no validated after-flight candidate")
    if await jobs._cancelled(job, owner):
        return
    binding = _chosen_binding(plan, group.id, branches)
    transfer_id = plan.allocations[0].itinerary.legs[1].resource_id
    chosen = {binding["incoming_id"], transfer_id}
    waits, completed_result = journey_waits(projected, plan), {}
    if any(item["overnight"] is True for item in waits):
        gaps.append(gap("overnight_wait_without_accommodation", "This sourced connection includes a wait across local midnight without accommodation. Review the explicit waiting period before accepting the proposal.", [group.id]))
    await jobs._update(job, owner, stage="applying", gaps=gaps, message="Checking current source freshness and committing only the selected flight and its bound vehicle.")

    def apply(current):
        if scope_fingerprint(current, job["incident_id"], [group.id]) != job["_scope_hash"]:
            raise DomainError(409, "After-flight passenger or case scope changed before commit")
        # The original lower window bound is not an expiry. Scope, immutable
        # source binding and current independent validation still apply after
        # that bound passes, provided this exact derived pickup remains usable.
        current_group = next(item for item in current.groups if item.id == group.id)
        original_ids = {item.id for item in current.resources}
        locations = {item.id for item in current.airports}
        # Both selected source revisions must retire before capacity compaction:
        # the old vehicle still protects its old incoming flight until retired.
        # This is one chosen pair, not the speculative category projections.
        added = append_travel_quote(current, imported[binding["incoming_id"]], option["query"], current_group, "flight",
            stage=True)
        if added["resource_ids"] != [binding["incoming_id"]]:
            raise DomainError(409, "The chosen incoming source identity changed before commit")
        vehicle = append_airport_cab_options(current, binding["context"], binding["response"], [binding["resources"][transfer_id]],
            now=jobs.airport_cab.now(), stage=True)
        superseded = set(added.get("superseded_resource_ids", [])) | set(vehicle["superseded_resource_ids"])
        ensure_inventory_capacity(current, keep_resource_ids=chosen | (original_ids-superseded), keep_location_ids=locations)
        validation = validate_plan(current, plan, scope_group_ids=scope)
        if not validation["valid"]:
            raise DomainError(409, "The complete after-flight connection failed current independent validation", validation)
        old_offers = {item.id for item in current.offers}
        answer = jobs.service._hold_plan(current, plan, "ai", decision_request["text"], choice.get("provider", "OpenAI Decisions"),
            choice["explanation"], plans, round((time.perf_counter()-started)*1000, 2), timings=timings, scope_group_ids=scope)
        completed_result.update(decision_id=answer["decision"]["id"], plan_id=plan.id,
            recovered_passengers=plan.recovered_passengers, exception_passengers=0,
            offer_ids=[item.id for item in current.offers if item.id not in old_offers], resource_ids=sorted(chosen),
            waits=waits, source_counts=source_counts, after_flight_transfer={"inputs": inputs.model_dump(mode="json"),
                "incoming_resource_id": binding["incoming_id"], "transfer_resource_id": transfer_id})
        return answer

    def completed(conn, response):
        jobs.ledger.complete_in_transaction(conn, job["id"], owner, response, completed_result)
        committed = jobs.store._load(conn)
        selected_group = next(item for item in committed.groups if item.id == inputs.group_id)
        jobs.after_flight_profiles.save_in_transaction(conn, committed, selected_group, inputs, job["id"])

    task = asyncio.create_task(asyncio.to_thread(jobs.store.mutate, "recovery-job-apply:"+job["id"], {"job_id": job["id"]}, world.version,
        apply, on_commit=completed, request_started=started))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise
