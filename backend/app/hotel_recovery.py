"""One-group, one-property sourced packages; each external call has its own ledger step."""
from __future__ import annotations

import asyncio
import copy
from contextlib import suppress
from datetime import date, datetime, timedelta
from decimal import Decimal
import time

from pydantic import Field, field_validator, model_validator

from .airport_catalog import lookup_airport
from .hotel_location import coordinate_timezone, property_location, property_zone
from .hotel_recovery_profile import PROFILE_KEY, build_hotel_recovery_profile
from .live_service import HotelQuoteImport, LiveOperations, _hotel_budget_total
from .live_travel import append_travel_quote, retire_travel_quotes
from .models import Model, Strategy
from .pricing import aware_timestamp
from .providers.apl import APLQuoteRequest, LONDON
from .providers.base import ProviderError
from .providers.reference_fx import EcbRateTable, SOURCE_URL as ECB_URL
from .real_data import RealDataRequest
from .scenario_schema import HORIZON_MINUTES, MAX_AIRPORTS, MAX_RESOURCES
from .solver import build_candidate, resource_blocked_by_incident, validate_plan
from .stay_requirements import HotelStayRequirement, REQUIREMENT_KEY
from .store import DomainError, Store, utcnow
from .transfer_planning import (
    InitialPoint, PlanningTransferRequest, assess_transfer_sources, build_transfer_resource,
    meters_between, prepare_transfer_inputs, prepare_transfer_response, resource_direction,
    terminal_ids, transfer_context,
)
from .travel import TravelSearchRequest


REQUIRED_CALLS = 10
HOTEL_SHORTLIST_LIMIT = 1
MAX_VEHICLE_OPTIONS = 3


class HotelRecoveryInputs(Model):
    checkin_at: datetime
    checkout_at: datetime
    rooms: int = Field(ge=1, le=30, strict=True)
    minimum_rest_minutes: int = Field(ge=0, le=HORIZON_MINUTES, strict=True)
    initial_point: InitialPoint
    pickup_at: datetime
    suitcases: int = Field(ge=0, le=200, strict=True)
    onward_airport_code: str
    airport_dropoff: InitialPoint
    airport_processing_minutes: int = Field(ge=1, le=720, strict=True)

    @field_validator("checkin_at", "checkout_at", "pickup_at", mode="before")
    @classmethod
    def explicit_instant(cls, value):
        if not isinstance(value,(str,datetime)):
            raise ValueError("Supply explicit offset-bearing hotel recovery timestamps")
        return value

    @field_validator("checkin_at", "checkout_at", "pickup_at")
    @classmethod
    def precise_instant(cls, value):
        if value.tzinfo is None or value.utcoffset() is None or value.second or value.microsecond:
            raise ValueError("Use an explicit UTC offset and whole minute for hotel recovery times")
        return value

    @field_validator("onward_airport_code")
    @classmethod
    def known_airport(cls, value):
        airport = lookup_airport(value)
        if airport is None:
            raise ValueError("Select a known onward airport")
        return airport["code"]

    @model_validator(mode="after")
    def stay_interval(self):
        self.requirement()
        if self.pickup_at >= self.checkin_at:
            raise ValueError("Initial pickup must precede the requested hotel check-in")
        for instant in (self.pickup_at, self.return_pickup):
            local = instant.astimezone(LONDON)
            if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
                raise ValueError("APL cannot distinguish repeated London pickup clock times")
        return self

    @property
    def return_pickup(self):
        return self.checkout_at + timedelta(minutes=10)

    def requirement(self):
        return HotelStayRequirement(source="operator_requested", checkin_at=self.checkin_at.isoformat(),
            checkout_at=self.checkout_at.isoformat(), rooms=self.rooms, minimum_rest_minutes=self.minimum_rest_minutes)


def validate_hotel_job(world, request):
    inputs = request.hotel_recovery
    if inputs is None:
        return
    if len(request.group_ids) != 1 or set(request.modes) != {"flight", "hotel", "bus"}:
        raise DomainError(422, "Automatic hotel recovery supports one explicit group and requires flight, hotel and bus modes")
    if request.max_provider_calls < REQUIRED_CALLS:
        raise DomainError(422, "This bounded hotel package requires an explicit allowance of at least 10 source/model calls; the caller's limit was not increased", {"required_provider_calls":REQUIRED_CALLS})
    group = next((item for item in world.groups if item.id == request.group_ids[0]), None)
    if group is None or group.adult_count != group.size or group.accessible or inputs.rooms > group.size:
        raise DomainError(422, "Hotel recovery requires one explicitly adult-only group, at least one adult per room, and no unverified accessibility requirements")
    initial = next((item for item in world.airports if item.id == group.origin and item.kind == "airport"), None)
    destination = next((item for item in world.airports if item.id == group.destination and item.kind == "airport"), None)
    onward = lookup_airport(inputs.onward_airport_code)
    if initial is None or destination is None or lookup_airport(destination.code) is None or initial.id != initial.code or destination.id != destination.code:
        raise DomainError(422, "This package requires an initial UK airport and a final airport destination; other physical destination links were not invented")
    for airport, point in ((lookup_airport(initial.code), inputs.initial_point), (onward, inputs.airport_dropoff)):
        try:
            uk = airport is not None and coordinate_timezone(airport["lat"],airport["lon"]) == "Europe/London" and coordinate_timezone(point.latitude,point.longitude) == "Europe/London"
        except ValueError:
            uk = False
        if (not uk or meters_between({"latitude":airport["lat"],"longitude":airport["lon"]},point.model_dump()) > 10_000
                or len(terminal_ids(point.label)) > 1):
            raise DomainError(422, "Declare one point within 10 km of each supported UK airport; this is an app geographic screen, not terminal verification")
    blocked = {item.unavailable_airport_code for item in world.incidents if item.id == group.incident_id}
    if onward["code"] in blocked or destination.code in blocked or onward["code"] == destination.code:
        raise DomainError(422, "The requested onward flight route conflicts with the incident or has identical endpoints")
    epoch = aware_timestamp(world.epoch)
    pickup = (inputs.pickup_at-epoch).total_seconds()/60
    checkout = (inputs.checkout_at-epoch).total_seconds()/60
    if (inputs.pickup_at <= utcnow() or pickup < max(world.sim_time, group.ready_at) or pickup != int(pickup)
            or checkout+10+inputs.airport_processing_minutes >= min(group.deadline,HORIZON_MINUTES)):
        raise DomainError(422, "Pickup must be future and after readiness; the stay and airport allowance must fit the group's deadline and planning horizon")
    if inputs.checkin_at.astimezone(LONDON).date() >= inputs.checkout_at.astimezone(LONDON).date():
        raise DomainError(422, "This hotel rate flow requires checkout on a later property-local date")


async def _parallel(calls):
    """Drain all dispatched calls before propagating a failure or shutdown."""
    tasks = [asyncio.create_task(call) for call in calls]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def reserve_transfer_ordinals(store, job, contexts):
    """Reserve in the manual receipt rowid domain; actual calls live in recovery_steps."""
    with store.connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS transfer_quote_receipts (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
            scenario_id TEXT NOT NULL, status TEXT NOT NULL, lease_until REAL NOT NULL)""")
        conn.execute("BEGIN IMMEDIATE")
        for direction, context in contexts.items():
            key = "hotel-job:"+job["id"]+":"+direction
            identity = "TQJ-"+Store.fingerprint(key)[:24].upper()
            digest = Store.fingerprint({"job_id":job["id"],"direction":direction,"scope":job["_scope_hash"]})
            row = conn.execute("SELECT rowid,fingerprint FROM transfer_quote_receipts WHERE request_key=?",(key,)).fetchone()
            if row and row[1] != digest:
                raise DomainError(409,"Transfer ordinal belongs to a different job scope")
            if row is None:
                inserted = conn.execute("INSERT INTO transfer_quote_receipts VALUES(?,?,?,?,?,?)",(identity,key,digest,job["_scenario_id"],"ordinal",0))
                ordinal = inserted.lastrowid
            else:
                ordinal = row[0]
            context["quote_sequence"] = ordinal
            context["quote_id"] = identity


async def _hotel_rates(provider, airport, query):
    items = await provider.offers(airport,query)
    if not isinstance(items,list) or len(items)>10 or any(not isinstance(item,dict) for item in items):
        raise ProviderError("Hotel supplier returned an invalid bounded rate list",provider="Nuitee / LiteAPI",code="invalid_response")
    return {"source":"Nuitee / LiteAPI","items":items}


async def _rates(fx):
    result = await fx.rates()
    table = result.get("table")
    if table is None:
        code = result.get("code","fx_unavailable")
        raise ProviderError("ECB reference unavailable; no EUR estimate or substitute rate was created",provider="European Central Bank",
            code="timeout" if code=="fx_timeout" else "connection" if code=="fx_transport_error" else code)
    return {**result,"table":{"rate_date":table.rate_date.isoformat(),"retrieved_at":table.retrieved_at.isoformat(),
        "source_units_per_eur":{key:str(value) for key,value in table.source_units_per_eur.items()},"source":table.source,"source_url":table.source_url}}


def _restore_rates(result):
    table = result["table"]
    return {**result,"table":EcbRateTable(rate_date=date.fromisoformat(table["rate_date"]),retrieved_at=aware_timestamp(table["retrieved_at"]),
        source_units_per_eur={key:Decimal(value) for key,value in table["source_units_per_eur"].items()},source=table["source"],source_url=table["source_url"])}


def _immutable_hotel(world, request, group, quote):
    epoch = aware_timestamp(world.epoch)
    resources, _, _ = LiveOperations._quote_resources(world,request,group,[quote],
        int((request.checkin_at-epoch).total_seconds()/60),int((request.checkout_at-epoch).total_seconds()/60))
    resource = resources[0]
    resource.id = "HQ-"+Store.fingerprint(resource.model_dump(mode="json",exclude={"id"}))[:28].upper()
    return resource


async def run_hotel_recovery(jobs, job, owner, world, started):
    from .recovery_jobs import RecoveryJobRequest, gap, journey_waits, scope_fingerprint
    request = RecoveryJobRequest.model_validate({key:value for key,value in job["_request"].items() if key!="incident_id"})
    validate_hotel_job(world,request)
    inputs, group_id = request.hotel_recovery, request.group_ids[0]
    scope = {group_id}
    real, apl, fx = jobs.hotel_provider, jobs.apl_provider, jobs.fx_provider
    if not (jobs.travel.flight_configured and real and real.hotel_offer_provider=="liteapi" and real.liteapi_configured
            and not real.liteapi_sandbox and real.google_configured and apl and apl.configured and fx):
        await jobs._update(job,owner,status="blocked",stage="finished",message="Complete hotel recovery requires production flight, hotel metadata/rates, APL, Google Routes and ECB access; no source was called.",
            gaps=[gap("hotel_package_provider_unavailable","Configure every required real source before this bounded package can start.",scope)])
        return
    projected = world.model_copy(deep=True)
    group = next(item for item in projected.groups if item.id==group_id)
    requirement = inputs.requirement().model_dump(mode="json")
    group.provenance[REQUIREMENT_KEY] = requirement
    profile = build_hotel_recovery_profile(projected,group,inputs)
    group.provenance[PROFILE_KEY] = copy.deepcopy(profile)
    epoch = aware_timestamp(world.epoch)
    earliest = inputs.return_pickup+timedelta(minutes=inputs.airport_processing_minutes)
    query = TravelSearchRequest(kind="flight",origin_code=inputs.onward_airport_code,destination_code=group.destination,
        departure_date=inputs.checkout_at.astimezone(LONDON).date(),adults=group.size,
        earliest_departure_at=earliest,latest_arrival_at=epoch+timedelta(minutes=group.deadline))
    option = {"id":"QRY-"+Store.fingerprint(query.model_dump(mode="json"))[:20],"group_id":group_id,"query":query.model_dump(mode="json"),
        "earliest_departure":int((earliest-epoch).total_seconds()/60),"latest_arrival":group.deadline}
    disclosure = "Bounded search: one hotel property with complete pricing, one onward flight date, and at most three fitting vehicle options per direction. Return pickup is requested checkout plus a 10-minute app connection allowance."
    await jobs._update(job,owner,message=disclosure,gaps=[])
    ai_request = {"objective":job["objective"],"instructions":request.instructions+" "+disclosure,"max_search_calls":1,
        "incident":next(item.model_dump(exclude={"flight","flight_ref","flight_context_state"}) for item in world.incidents if item.id==job["incident_id"])}
    scoped = projected.model_copy(deep=True)
    scoped.groups=[group]
    scoped.offers=[item for item in scoped.offers if item.group_id==group_id]
    timings={"interpretation_ms":0.0,"solver_ms":0.0,"decisions_ms":0.0}
    before=time.perf_counter()
    selection=await jobs._call(job,owner,"planning_queries","OpenAI Responses",ai_request,[group_id],lambda:jobs.ai.plan_recovery_queries(scoped.model_dump(mode="json"),[option],ai_request))
    timings["interpretation_ms"]=round((time.perf_counter()-before)*1000,2)
    if selection.get("query_ids") != [option["id"]]:
        raise DomainError(422,"AI did not select the one approved dated package query; no supplier calls were issued")
    strategies=[Strategy.model_validate(item) for item in selection["strategies"]]
    if not 1<=len(strategies)<=3 or len({item.id for item in strategies})!=len(strategies) or any(item.preferred_airport is not None for item in strategies):
        raise DomainError(502,"AI returned invalid bounded hotel policies")
    reference=lookup_airport(group.origin)
    airport={"code":reference["code"],"name":reference["name"],"latitude":reference["lat"],"longitude":reference["lon"]}
    hotel_query=RealDataRequest(airport_code=group.origin,destination_code=group.destination,checkin=inputs.checkin_at.astimezone(LONDON).date(),
        checkout=inputs.checkout_at.astimezone(LONDON).date(),adults=group.size,rooms=inputs.rooms)
    flights,rates=await _parallel([
        jobs._call(job,owner,"searching_flights",jobs.travel.flight_source,option,[group_id],lambda:jobs._search(query)),
        jobs._call(job,owner,"searching_hotels","Nuitee / LiteAPI",hotel_query.model_dump(mode="json"),[group_id],lambda:_hotel_rates(real,airport,hotel_query)),
    ])
    if await jobs._cancelled(job,owner): return
    imported=[]
    superseded=set()
    for quote in flights["items"]:
        try:
            trial=projected.model_copy(deep=True)
            result=append_travel_quote(trial,quote,option["query"],group,"flight")
            resource=next(item for item in trial.resources if item.id==result["resource_ids"][0])
            # A positive drive needs at least one operation minute after pickup
            # and processing. Check the exact source instant before choosing the
            # shared return context; an inclusive provider lower bound is not enough.
            actual_departure=aware_timestamp(resource.provenance["departure_at"])
            if resource_blocked_by_incident(trial,resource,group) or actual_departure<earliest+timedelta(minutes=1) or resource.arrival>group.deadline:
                continue
            projected=trial
            imported.append((quote,resource.id))
            superseded.update(result.get("superseded_resource_ids",[]))
        except DomainError as exc:
            if isinstance(exc.details,dict) and exc.details.get("quote_revision_conflict"): raise
    if not imported:
        raise DomainError(422,"No sourced flight fits the requested post-stay route and deadline; no hotel or transfer connection was invented")
    complete=[]
    for quote in rates["items"]:
        try:
            total=_hotel_budget_total(quote,Decimal(quote["total_price"]))
            if (total is not None and quote.get("provider")=="liteapi" and quote.get("currency")=="EUR" and quote.get("adults")==group.size and quote.get("rooms")==inputs.rooms
                    and quote.get("checkin")==hotel_query.checkin.isoformat() and quote.get("checkout")==hotel_query.checkout.isoformat()):
                complete.append((total,str(quote["id"]),quote))
        except (KeyError,ValueError,TypeError,ArithmeticError):
            continue
    if not complete:
        raise DomainError(422,"No hotel has complete mandatory EUR pricing and the requested occupancy; no property metadata or transfers were requested")
    selected_hotel=min(complete,key=lambda item:(item[0],item[1]))[2]
    metadata=await jobs._call(job,owner,"locating_hotel","Nuitee / LiteAPI",{"hotel_id":selected_hotel["id"]},[group_id],lambda:real.hotel_location(selected_hotel["id"]))
    located={**selected_hotel,**metadata}
    hotel_request=HotelQuoteImport(group_id=group_id,airport_code=group.origin,checkin=hotel_query.checkin,checkout=hotel_query.checkout,rooms=inputs.rooms,
        checkin_at=inputs.checkin_at,checkout_at=inputs.checkout_at,stay_times_confirmed=False,expected_version=world.version,idempotency_key="hotel-package-"+job["id"])
    group=next(item for item in projected.groups if item.id==group_id)
    hotel=_immutable_hotel(projected,hotel_request,group,located)
    node,_=property_location(hotel.provenance)
    existing=next((item for item in projected.airports if item.id==node.id),None)
    if existing and (existing.kind!="hotel" or existing.lat!=node.lat or existing.lon!=node.lon):
        raise DomainError(409,"The sourced property conflicts with existing hotel geography")
    if not existing: projected.airports.append(node)
    projected.resources.append(hotel)
    # Restrict this package to its new sourced candidates, without changing the
    # real operation or any other group's inventory commitments.
    fresh_ids={identity for _,identity in imported}|{hotel.id}
    for resource in projected.resources:
        if resource.id not in fresh_ids and resource.eligible_group_ids==[group_id] and resource.booking_status!="confirmed":
            resource.available=False
    first_flight=next(item for item in projected.resources if item.id==imported[0][1])
    common={"group_id":group_id,"hotel_node_id":node.id,"suitcases":inputs.suitcases,"expected_version":world.version}
    outgoing=PlanningTransferRequest(**common,pickup_at=inputs.pickup_at,initial_point=inputs.initial_point,idempotency_key="hotel-out-"+job["id"])
    incoming=PlanningTransferRequest(**common,pickup_at=inputs.return_pickup,direction="hotel_to_airport",airport_dropoff=inputs.airport_dropoff,
        onward_resource_id=first_flight.id,airport_processing_minutes=inputs.airport_processing_minutes,idempotency_key="hotel-back-"+job["id"])
    contexts={"initial_airport_to_hotel":transfer_context(projected,outgoing,now=utcnow()),"hotel_to_airport":transfer_context(projected,incoming,now=utcnow())}
    await asyncio.to_thread(reserve_transfer_ordinals,jobs.store,job,contexts)
    prepared={direction:prepare_transfer_inputs(context,metadata) for direction,context in contexts.items()}
    for item in prepared.values():
        if item["blockers"]:
            raise DomainError(422," ".join(reason["message"] for reason in item["blockers"]))
    async def source_pair(direction):
        item=prepared[direction]
        apl_request=APLQuoteRequest.model_validate(item["apl_request"])
        route=item["route_request"]
        return await _parallel([
            jobs._call(job,owner,"quoting_"+direction,"Airport Pickups London",item["apl_request"],[group_id],lambda:apl.quote(apl_request)),
            jobs._call(job,owner,"routing_"+direction,"Google Routes",route,[group_id],lambda:real.dated_route(route["origin"],route["destination"],aware_timestamp(route["pickup_at"]))),
        ])
    # At most three calls run together; all outcomes are drained before failure.
    out_sources,fx_result=await _parallel([source_pair("initial_airport_to_hotel"),jobs._call(job,owner,"reference_fx","European Central Bank",{"url":ECB_URL},[group_id],lambda:_rates(fx))])
    if await jobs._cancelled(job,owner): return
    rates_table=_restore_rates(fx_result)
    outbound_context=contexts["initial_airport_to_hotel"]
    outbound_enriched=fx.enrich(out_sources[0],rates_table)
    outbound_response=assess_transfer_sources(outbound_context,prepare_transfer_response(outbound_context,outbound_context["quote_id"],now=utcnow()),
        hotel_metadata=metadata,apl_quote=out_sources[0],fx_enrichment=outbound_enriched,dated_route=out_sources[1],now=utcnow())
    outbound_options=[item for item in outbound_response["options"] if item["importable"]]
    if not outbound_options:
        reasons=outbound_response["blockers"]+[reason for item in outbound_response["options"] for reason in item["blockers"]]
        await jobs._update(job,owner,status="blocked",stage="finished",message="The initial hotel transfer is not usable; no dependent return calls were made.",
            gaps=[gap(item["code"],item["message"],scope) for item in { (item["code"],item["message"]):item for item in reasons }.values()])
        return
    first_out,_=build_transfer_resource(projected,outbound_context,outbound_response,outbound_options[0]["option_id"],now=utcnow())
    if first_out.arrival+10>hotel.departure:
        await jobs._update(job,owner,status="blocked",stage="finished",message="The sourced initial driving route misses the requested hotel check-in; no dependent return calls were made.",
            gaps=[gap("hotel_checkin_connection","The Google driving estimate plus the app's 10-minute connection allowance reaches the property after requested check-in.",scope)])
        return
    from .solver import resource_cost
    least_out=min(Decimal(item["eur_reference_estimate"]["amount"]) for item in outbound_enriched["options"] if item["id"] in {value["option_id"] for value in outbound_options})
    least_flight=min(resource_cost(item,group) for item in projected.resources if item.id in {identity for _,identity in imported})
    known_minimum=least_out+Decimal(str(resource_cost(hotel,group)))+Decimal(str(least_flight))
    if known_minimum>Decimal(str(group.budget))+Decimal("0.005"):
        await jobs._update(job,owner,status="blocked",stage="finished",message="Known flight, room and outbound costs already exceed this group's budget; no dependent return calls were made.",
            gaps=[gap("hotel_package_budget","The return fare is still unknown; the already sourced parts alone exceed the declared planning budget.",scope)])
        return
    back_sources=await source_pair("hotel_to_airport")
    if await jobs._cancelled(job,owner): return
    assembled=[]
    source_gaps=[]
    for direction,sources in (("initial_airport_to_hotel",out_sources),("hotel_to_airport",back_sources)):
        raw_apl,route=sources
        enriched=fx.enrich(raw_apl,rates_table)
        targets=[None] if direction=="initial_airport_to_hotel" else [identity for _,identity in imported]
        for target in targets:
            context=contexts[direction]
            if target is not None:
                request_for_flight=incoming.model_copy(update={"onward_resource_id":target})
                try: context={**transfer_context(projected,request_for_flight,now=utcnow()),"quote_sequence":context["quote_sequence"],"quote_id":context["quote_id"]}
                except DomainError: continue
            response=assess_transfer_sources(context,prepare_transfer_response(context,context["quote_id"],now=utcnow()),hotel_metadata=metadata,
                apl_quote=raw_apl,fx_enrichment=enriched,dated_route=route,now=utcnow())
            usable=[item for item in response["options"] if item["importable"]]
            if not usable:
                source_gaps.extend(gap(item["code"],item["message"],scope) for item in response["blockers"]+[reason for option in response["options"] for reason in option["blockers"]])
            by_id={item["id"]:item for item in enriched["options"]}
            usable.sort(key=lambda item:(Decimal(by_id[item["option_id"]]["eur_reference_estimate"]["amount"]),item["option_id"]))
            for item in usable[:MAX_VEHICLE_OPTIONS]:
                resource,point=build_transfer_resource(projected,context,response,item["option_id"],now=utcnow())
                if point and not any(node.id==point.id for node in projected.airports): projected.airports.append(point)
                projected.resources.append(resource)
                assembled.append((resource.id,context,response,item["option_id"]))
    if len(projected.resources)>MAX_RESOURCES or len(projected.airports)>MAX_AIRPORTS:
        raise DomainError(422,"The bounded package exceeds operation resource or location limits")
    before=time.perf_counter()
    candidates=await asyncio.to_thread(lambda:[build_candidate(projected,strategy,scope_group_ids=scope) for strategy in strategies])
    candidates=[item for item in candidates if item.validation["valid"] and item.recovered_passengers==group.size]
    timings["solver_ms"]=round((time.perf_counter()-before)*1000,2)
    if not candidates:
        await jobs._update(job,owner,status="blocked",stage="finished",message="The one-property search produced no complete sourced hotel journey under the budget, dates and physical links. No operation changed.",
            gaps=[*{(item["code"],item["message"]):item for item in source_gaps}.values(),gap("no_complete_hotel_journey",disclosure+" Room or transfer evidence, connection time or budget may prevent this package; other properties were not searched.",scope)])
        return
    decision_request={"text":f"Recover only group {group_id} with the explicitly requested full hotel stay. Objective: {job['objective']}. {request.instructions}",
        "context":disclosure+" GBP transfer amounts use labeled ECB planning estimates, not guaranteed EUR settlement. All other allocations are fixed."}
    before=time.perf_counter()
    choice=await jobs._call(job,owner,"deciding","OpenAI Decisions",{"candidate_ids":[item.id for item in candidates],"request":decision_request},[group_id],
        lambda:jobs.ai.choose(projected.model_dump(mode="json"),[item.model_dump(mode="json") for item in candidates],decision_request))
    timings["decisions_ms"]=round((time.perf_counter()-before)*1000,2)
    plan=next((item for item in candidates if item.id==choice.get("plan_id")),None)
    if plan is None: raise DomainError(502,"Decisions did not choose a validated hotel package")
    if await jobs._cancelled(job,owner): return
    chosen={leg.resource_id for allocation in plan.allocations for leg in allocation.itinerary.legs}
    waits=journey_waits(projected,plan)
    completed_result={}
    def apply(current):
        if scope_fingerprint(current,job["incident_id"],[group_id])!=job["_scope_hash"]:
            raise DomainError(409,"Hotel recovery scope changed before commit")
        current_group=next(item for item in current.groups if item.id==group_id)
        current_group.provenance[REQUIREMENT_KEY]=copy.deepcopy(requirement)
        current_group.provenance[PROFILE_KEY]=copy.deepcopy(profile)
        retire_travel_quotes(current,group_id,superseded)
        for quote,identity in imported:
            if identity in chosen: append_travel_quote(current,quote,option["query"],current_group,"flight")
        checked_hotel=_immutable_hotel(current,hotel_request,current_group,located)
        if checked_hotel.id!=hotel.id or hotel.id not in chosen:
            raise DomainError(409,"The selected package no longer has its exact sourced hotel")
        old_hotels=[item.id for item in current.resources if item.kind=="hotel" and item.source==hotel.source and item.external_offer_id==hotel.external_offer_id
            and item.eligible_group_ids==[group_id] and item.id!=hotel.id and item.booking_status!="confirmed" and item.available]
        retire_travel_quotes(current,group_id,old_hotels)
        old_transfers=[item.id for item in current.resources if item.source=="Airport Pickups London" and item.eligible_group_ids==[group_id]
            and item.booking_status!="confirmed" and item.available and resource_direction(item) in contexts]
        retire_travel_quotes(current,group_id,old_transfers)
        if not any(item.id==node.id for item in current.airports): current.airports.append(node.model_copy(deep=True))
        if not any(item.id==hotel.id for item in current.resources): current.resources.append(checked_hotel)
        for identity,context,response,option_id in assembled:
            if identity not in chosen: continue
            resource,point=build_transfer_resource(current,context,response,option_id,now=utcnow())
            if resource.id!=identity: raise DomainError(409,"Transfer source identity changed before commit")
            if point and not any(item.id==point.id for item in current.airports): current.airports.append(point)
            current.resources.append(resource)
        if len(current.resources)>MAX_RESOURCES or len(current.airports)>MAX_AIRPORTS:
            raise DomainError(422,"Operation inventory limits prevent this package")
        validation=validate_plan(current,plan,scope_group_ids=scope)
        if not validation["valid"]: raise DomainError(409,"Package failed current independent validation",validation)
        old_offers={item.id for item in current.offers}
        answer=jobs.service._hold_plan(current,plan,"ai",decision_request["text"],choice.get("provider","OpenAI Decisions"),choice["explanation"],candidates,
            round((time.perf_counter()-started)*1000,2),timings=timings,scope_group_ids=scope)
        completed_result.update(decision_id=answer["decision"]["id"],plan_id=plan.id,recovered_passengers=plan.recovered_passengers,exception_passengers=0,
            offer_ids=[item.id for item in current.offers if item.id not in old_offers],resource_ids=sorted(chosen),waits=waits,
            source_counts={flights["source"]:len(flights["items"]),"Nuitee / LiteAPI":len(rates["items"]),"Airport Pickups London":len(out_sources[0]["options"])+len(back_sources[0]["options"]),"Google Routes":2,"European Central Bank":1},
            hotel_recovery={"hotel_id":selected_hotel["id"],"hotel_node_id":node.id,"hotel_shortlist_limit":HOTEL_SHORTLIST_LIMIT,"flight_query_date":query.departure_date.isoformat(),
                "return_pickup_at":inputs.return_pickup.isoformat(),"return_pickup_basis":"checkout_plus_10_minute_app_allowance","required_provider_calls":REQUIRED_CALLS})
        return answer
    def completed(conn,response):
        jobs.ledger.complete_in_transaction(conn,job["id"],owner,response,completed_result)
    task=asyncio.create_task(asyncio.to_thread(jobs.store.mutate,"recovery-job-apply:"+job["id"],{"job_id":job["id"]},world.version,apply,on_commit=completed,request_started=started))
    try: await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception): await task
        raise
