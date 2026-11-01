"""Bounded, incident-scoped sourcing and real AI selection; never supplier booking."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import logging
import sqlite3
import time
from typing import Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from .airport_catalog import lookup_airport
from .airport_constraints import case_airport_operation_blocked
from .airport_cab_recovery import AIRPORT_TRANSFER_CONTEXT, AirportCabRecoveryInput, source_airport_transfers, validate_airport_transfer_job, validate_airport_transfer_selection
from .airport_cab_profile import AirportCabProfiles
from .after_flight_inputs import AfterFlightTransferInput
from .after_flight_profile import AfterFlightProfiles
from .romanian_hotel_profile import RomanianHotelProfiles
from .after_flight_recovery import run_after_flight_recovery, validate_after_flight_job
from .confirmed_allocations import confirmed_group_ids, require_consistent_confirmations
from .hotel_location import coordinate_timezone, property_zone
from .hotel_recovery import HotelRecoveryInputs, _rates, _restore_rates, run_hotel_recovery, validate_hotel_job
from .romanian_hotel_recovery import RomanianHotelRecoveryInputs, run_romanian_hotel_recovery, validate_romanian_hotel_job
from .live_data import identifier
from .live_travel import append_travel_quote, retire_travel_quotes
from .models import Model, Mutation, Strategy
from .providers.base import ProviderError
from .recovery_ledger import RecoveryLedger, TERMINAL
from .solver import build_candidate, resource_blocked_by_incident, resource_feasible
from .store import DomainError, Store, utcnow
from .travel import TravelSearchRequest


class RecoveryJobRequest(Mutation):
    group_ids: list[str] = Field(min_length=1,max_length=24)
    objective: Literal["balanced","fastest","lowest_cost"] = "balanced"
    modes: list[Literal["flight","hotel","bus"]] = Field(default_factory=lambda:["flight"],min_length=1,max_length=3)
    max_provider_calls: int = Field(default=8,ge=3,le=26,strict=True)
    instructions: str = Field(default="",max_length=2000)
    hotel_recovery: HotelRecoveryInputs | None = None
    airport_transfers: list[AirportCabRecoveryInput] = Field(default_factory=list, max_length=12)
    after_flight_transfer: AfterFlightTransferInput | None = None
    romanian_hotel_recovery: RomanianHotelRecoveryInputs | None = None

    @model_validator(mode="before")
    @classmethod
    def exact_after_flight_scope(cls, value):
        if isinstance(value, dict) and value.get("romanian_hotel_recovery") is not None:
            ids = value.get("group_ids")
            if (not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str)
                    or identifier(ids[0]) != ids[0]):
                raise ValueError("Romanian hotel recovery requires one exact passenger group identifier without normalization or duplicates")
        if isinstance(value, dict) and value.get("after_flight_transfer") is not None:
            transfer = value["after_flight_transfer"]
            identity = transfer.group_id if isinstance(transfer, AfterFlightTransferInput) else transfer.get("group_id") if isinstance(transfer, dict) else None
            if not isinstance(identity, str) or value.get("group_ids") != [identity]:
                raise ValueError("After-flight recovery requires the exact single passenger group identifier without normalization, duplicates or substitutions")
        return value

    @model_serializer(mode="wrap")
    def compatible_payload(self, handler):
        result = handler(self)
        if self.hotel_recovery is None:
            result.pop("hotel_recovery",None)
        if not self.airport_transfers:
            result.pop("airport_transfers",None)
        if self.after_flight_transfer is None:
            result.pop("after_flight_transfer",None)
        if self.romanian_hotel_recovery is None:
            result.pop("romanian_hotel_recovery",None)
        return result

    @field_validator("group_ids")
    @classmethod
    def normalize_groups(cls,value):
        return sorted({identifier(item) for item in value})

    @field_validator("modes")
    @classmethod
    def unique_modes(cls,value):
        return sorted(set(value))


class RecoveryCancellation(Model):
    idempotency_key: str = Field(min_length=8,max_length=100)


def scope_fingerprint(world,incident_id,group_ids):
    if world.mode!="live":
        raise DomainError(409,"Sourced recovery jobs require the live operation")
    incident=next((item for item in world.incidents if item.id==incident_id),None)
    if incident is None:
        raise DomainError(404,"Recovery incident not found")
    protected = confirmed_group_ids(world)
    selected=[]
    for group_id in group_ids:
        group=next((item for item in world.groups if item.id==group_id),None)
        if group is None or group.incident_id!=incident.id or group_id not in incident.group_ids:
            raise DomainError(409,"Every selected passenger group must be explicitly bound to this incident")
        if group_id in protected:
            raise DomainError(409,"Supplier-confirmed groups require separate operator handling; this job cannot replace their bookings")
        selected.append(group.model_dump(exclude={"token","members","name"}))
    context=incident.model_dump(exclude={"flight","flight_context_state","flight_ref"})
    active=[offer.model_dump() for offer in world.offers if offer.group_id in group_ids and offer.status in ("held","proposed","accepted","confirmed")]
    return Store.fingerprint({"scenario":world.scenario_id,"epoch":world.epoch,"incident":context,"groups":selected,"active_offers":active})


def gap(code,message,group_ids):
    return {"code":code,"message":message,"group_ids":list(group_ids)}


def fixed_confirmed_airport_conflicts(world):
    """Find fixed confirmed flight legs rejected by a current linked case rule.

    Scoped candidate validation retains every confirmed offer, including those
    in another case. Only those fixed offers are checked here: an orphan booking
    record or a historical declined proposal is not proof of a fixed conflict.
    Current source segments and retained itinerary endpoints are facts already
    recorded in the world; missing source segments are never reconstructed.
    """
    if world.mode != "live":
        return []
    groups = {group.id: group for group in world.groups}
    resources = {resource.id: resource for resource in world.resources}
    conflicts = []
    for offer in world.offers:
        group = groups.get(offer.group_id)
        if offer.status != "confirmed" or group is None:
            continue
        blocked = set()
        for leg in offer.itinerary.legs:
            if leg.kind != "flight":
                continue
            resource = resources.get(leg.resource_id)
            if ((resource is not None and resource.kind == "flight" and resource_blocked_by_incident(world, resource, group))
                    or case_airport_operation_blocked(world, group, leg.origin, "departures")
                    or case_airport_operation_blocked(world, group, leg.destination, "arrivals")):
                blocked.add(leg.resource_id)
        if blocked:
            conflicts.append({"incident_id": group.incident_id, "group_id": group.id,
                "offer_id": offer.id, "resource_ids": sorted(blocked)})
    return conflicts


def journey_waits(world,plan):
    """Disclose dated waits, including connections inside a supplier quote."""
    groups={item.id:item for item in world.groups}
    resources={item.id:item for item in world.resources}
    epoch=datetime.fromisoformat(world.epoch)
    result=[]
    def append(group_id,location,start,end):
        if end<=start:
            return
        node=next((item for item in world.airports if item.id==location),None)
        reference=lookup_airport(location) if node is None else None
        zone_id=None
        overnight=None
        try:
            if node or reference:
                zone_id=coordinate_timezone(node.lat if node else reference["lat"],node.lon if node else reference["lon"])
                zone=property_zone(zone_id)
                overnight=start.astimezone(zone).date()!=end.astimezone(zone).date()
        except ValueError:
            pass
        result.append({"group_id":group_id,"location_id":location,"from_at":start.isoformat(),"to_at":end.isoformat(),
                       "duration_minutes":round((end-start).total_seconds()/60,1),"timezone":zone_id,"overnight":overnight,"accommodation_included":False})
    for allocation in plan.allocations:
        group=groups[allocation.group_id]
        at=epoch+timedelta(minutes=max(world.sim_time,group.ready_at))
        location=group.origin
        for leg in allocation.itinerary.legs:
            append(group.id,location,at,epoch+timedelta(minutes=leg.departure))
            segments=resources[leg.resource_id].provenance.get("segments",[])
            if isinstance(segments,list):
                for first,second in zip(segments,segments[1:]):
                    try:
                        start=datetime.fromisoformat(first["arrival_at"].replace("Z","+00:00"))
                        end=datetime.fromisoformat(second["departure_at"].replace("Z","+00:00"))
                        if start.tzinfo is not None and end.tzinfo is not None and first["destination"]==second["origin"]:
                            append(group.id,first["destination"],start,end)
                    except (KeyError,TypeError,ValueError,AttributeError):
                        continue
            at=epoch+timedelta(minutes=leg.arrival)
            location=leg.destination
    return result


def flight_query_options(world,groups):
    """Reference routes require actual endpoints or already-sourced ground links."""
    from .ground_query_paths import (
        MAX_GROUND_LEGS, MAX_GROUP_QUERIES, MAX_QUERY_PAIRS, MAX_SEARCH_DATES,
        cents, fixed_capacity, ground_paths,
    )
    epoch=datetime.fromisoformat(world.epoch)
    now=utcnow()
    occupied=fixed_capacity(world,[group.id for group in groups])
    options_by_group=[]
    gaps=[]
    for group in groups:
        if group.adult_count!=group.size:
            gaps.append(gap("passenger_composition_required","Flight searches currently require an explicitly adult-only group; no ages were inferred.",[group.id]))
            continue
        prefixes,suffixes,limited=ground_paths(world,group,occupied)
        choices=[]
        seen=set()
        pairs=0
        for prefix in prefixes:
            origin=prefix.airport_id
            earliest=prefix.minute+prefix.flight_buffer
            airport=lookup_airport(origin)
            if airport is None:
                continue
            zone=property_zone(coordinate_timezone(airport["lat"],airport["lon"]))
            first=(epoch+timedelta(minutes=prefix.minute)).astimezone(zone).date()
            for suffix in suffixes:
                pairs+=1
                if pairs>MAX_QUERY_PAIRS or len(choices)>=MAX_GROUP_QUERIES:
                    limited=True
                    break
                destination,latest=suffix.airport_id,suffix.minute
                if (origin==destination or earliest>=latest
                        or len(prefix.resource_ids)+len(suffix.resource_ids)>MAX_GROUND_LEGS
                        or prefix.resource_ids & suffix.resource_ids
                        or (not prefix.has_hotel and prefix.locations & suffix.locations)
                        or prefix.cost_cents+suffix.cost_cents>cents(group.budget)):
                    continue
                last=(epoch+timedelta(minutes=latest)).astimezone(zone).date()
                for offset in range(min(MAX_SEARCH_DATES,(last-first).days+1)):
                    if len(choices)>=MAX_GROUP_QUERIES:
                        limited=True
                        break
                    day=first+timedelta(days=offset)
                    try:
                        request=TravelSearchRequest(kind="flight",origin_code=origin,destination_code=destination,departure_date=day,adults=group.size,
                            earliest_departure_at=max(now,epoch+timedelta(minutes=earliest)),latest_arrival_at=epoch+timedelta(minutes=latest))
                    except ValueError:
                        continue
                    data={"group_id":group.id,"query":request.model_dump(mode="json"),"earliest_departure":earliest,"latest_arrival":latest}
                    identity="QRY-"+Store.fingerprint(data)[:20]
                    if identity not in seen:
                        seen.add(identity)
                        choices.append({"id":identity,**data})
            if pairs>MAX_QUERY_PAIRS or len(choices)>=MAX_GROUP_QUERIES:
                break
        if limited:
            gaps.append(gap("physical_route_search_limited","Existing ground-path exploration reached its bounded state, edge or query limit. The returned routes are a partial search; no additional transport was inferred.",[group.id]))
        if not choices:
            gaps.append(gap("physical_route_missing","No flight-search route has a verified connection from the group's current location to its final destination under this incident. Airport, hotel or station transfers were not invented.",[group.id]))
        options_by_group.append(choices)
    # Bounded round-robin avoids excluding an entire later group when many
    # alternate dates/ground-linked airports exist for the first group.
    options=[]
    for index in range(max((len(items) for items in options_by_group),default=0)):
        for items in options_by_group:
            if index<len(items) and len(options)<256:
                options.append(items[index])
    return options,gaps


def current_flight_connection(world,groups,option):
    """Require a currently valid ground witness without changing the approved query.

    Different categories can justify the same dated flight search. Rebuilding
    bounded paths accepts any surviving witness; its window must cover the
    originally approved one. The moving current-time clamp is not route identity.
    """
    def route(query):
        return {key:value for key,value in query.items() if key not in ("earliest_departure_at","latest_arrival_at")}
    current,_=flight_query_options(world,groups)
    return any(item["group_id"]==option["group_id"] and route(item["query"])==route(option["query"])
        and item["earliest_departure"]<=option["earliest_departure"]
        and item["latest_arrival"]>=option["latest_arrival"] for item in current)


class RecoveryJobs:
    def __init__(self,settings,store,service,travel_provider,ai,*,hotel_provider=None,apl_provider=None,fx_provider=None,coach_fares=None,airport_cab=None,transfero_provider=None):
        self.settings,self.store,self.service,self.travel,self.ai=settings,store,service,travel_provider,ai
        self.hotel_provider,self.apl_provider,self.fx_provider=hotel_provider,apl_provider,fx_provider
        self.coach_fares=coach_fares
        self.airport_cab=airport_cab
        self.transfero_provider=transfero_provider
        self.airport_cab_profiles=AirportCabProfiles(store)
        self.after_flight_profiles=AfterFlightProfiles(store)
        self.romanian_hotel_profiles=RomanianHotelProfiles(store)
        self.ledger=RecoveryLedger(store,settings.request_timeout_seconds+60)
        self.task=None

    def start(self,incident_id,request,*,on_created=None):
        payload={**request.model_dump(mode="json"),"incident_id":incident_id}
        replay=self.ledger.replay("recovery-job:"+request.idempotency_key,payload)
        if replay and on_created is None:
            return replay
        world=self.store.read()
        digest=scope_fingerprint(world,incident_id,request.group_ids)
        validate_romanian_hotel_job(world,request)
        validate_hotel_job(world,request)
        validate_airport_transfer_job(world,request,self.airport_cab)
        validate_after_flight_job(world,request,self.airport_cab)
        return self.ledger.create(incident_id,request,digest,world.scenario_id,on_created=on_created)

    def start_in_transaction(self,conn,world,incident_id,request,*,supersede_active=False):
        """Validate the saved post-report world and queue without opening a connection."""
        if not conn.in_transaction:
            raise RuntimeError("Recovery job creation requires an active transaction")
        digest=scope_fingerprint(world,incident_id,request.group_ids)
        validate_romanian_hotel_job(world,request)
        validate_hotel_job(world,request)
        validate_airport_transfer_job(world,request,self.airport_cab)
        validate_after_flight_job(world,request,self.airport_cab)
        return self.ledger.create_in_transaction(conn,world,incident_id,request,digest,world.scenario_id,supersede_active=supersede_active)

    def get(self,job_id):
        return {"job":self.ledger.get(job_id)}

    def list(self,incident_id):
        world=self.store.read()
        if not any(item.id==incident_id for item in world.incidents):
            raise DomainError(404,"Recovery incident not found")
        return {"jobs":self.ledger.list(incident_id)}

    def cancel(self,job_id,request):
        return self.ledger.cancel(job_id,request.idempotency_key)

    def start_worker(self):
        self.task=asyncio.create_task(self._worker())

    async def aclose(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _worker(self):
        while True:
            try:
                claimed=await asyncio.to_thread(self.ledger.claim_next)
                if claimed:
                    await self.run(*claimed)
                else:
                    await asyncio.sleep(.5)
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Recovery ledger temporarily unavailable; no provider dispatch was retried")
                await asyncio.sleep(1)

    async def _update(self,job,owner,**changes):
        await asyncio.to_thread(self.ledger.update,job["id"],owner,**changes)

    async def _cancelled(self,job,owner):
        current=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
        if current["cancel_requested"]:
            await self._update(job,owner,status="cancelled",stage="finished",message="Recovery cancelled. Recorded provider calls remain in the ledger; no plan was applied.")
            return True
        return False

    async def _call(self,job,owner,stage,provider,query,group_ids,call,*,optional_reference=False):
        current=await asyncio.to_thread(self.store.read)
        if current.version!=job["_planning_version"] or scope_fingerprint(current,job["incident_id"],job["group_ids"])!=job["_scope_hash"]:
            raise DomainError(409,"Operation changed while sourcing; no further provider call was issued. Review the recorded results before a new attempt.")
        require_consistent_confirmations(current)
        step=await asyncio.to_thread(self.ledger.dispatch,job["id"],owner,stage,provider,query,group_ids,expected_version=job["_planning_version"])
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                result=await call()
            await asyncio.to_thread(self.ledger.outcome,job["id"],owner,step,"succeeded","Provider result recorded; no supplier hold or booking was made.",result,len(result.get("items",result.get("options",[]))))
            return result
        except asyncio.CancelledError:
            with suppress(DomainError):
                await asyncio.to_thread(self.ledger.outcome,job["id"],owner,step,"unknown","Worker stopped after dispatch. Provider outcome and charges are unconfirmed; no retry was issued.")
            raise
        except (TimeoutError,ProviderError) as exc:
            code=getattr(exc,"code","timeout")
            unknown=code in ("timeout","connection") or str(code).startswith("http_5")
            message=str(exc) if isinstance(exc,ProviderError) else "Provider deadline reached; upstream outcome and charges are unconfirmed."
            await asyncio.to_thread(self.ledger.outcome,job["id"],owner,step,"unknown" if unknown else "failed",message)
            if not optional_reference:
                await asyncio.to_thread(self.ledger.halt,job["id"],owner,"unknown_outcome" if unknown else "failed",message+" No automatic retry was issued; other dispatched outcomes are being recorded.")
            raise
        except Exception:
            await asyncio.to_thread(self.ledger.outcome,job["id"],owner,step,"unknown","Provider dispatch did not produce a recordable outcome. No retry was issued.")
            await asyncio.to_thread(self.ledger.halt,job["id"],owner,"unknown_outcome","Provider outcome could not be established; no automatic retry or plan application was attempted.")
            raise

    async def _ground_references(self,job,owner,world,groups,gaps,*,reserve_calls):
        """Observe dated consumer fares without creating inventory or route links.

        Flight sourcing and candidate validation run first. Only spare calls may
        be used here; an independently valid plan retains its Decisions call.
        A failed optional GET remains visible in the ledger and never retries.
        """
        from .providers.bileteria import BileteriaSearchRequest, SOURCE
        from .providers.reference_fx import SOURCE as FX_SOURCE, SOURCE_URL as FX_URL

        references=[]
        if self.coach_fares is None or not self.coach_fares.provider.configured:
            gaps.append(gap("ground_fare_provider_unavailable","Romanian coach fare references are unavailable. No ground inventory or connection was invented.",job["group_ids"]))
            await self._update(job,owner,ground_references=references,gaps=gaps)
            return references
        locations={node.id:node for node in world.airports}
        epoch=datetime.fromisoformat(world.epoch)
        zone=property_zone("Europe/Bucharest")
        searches={}
        for group in groups:
            start,end=locations.get(group.origin),locations.get(group.destination)
            if not start or not end or start.kind!="airport" or end.kind!="airport" or (start.code,end.code)!=("CRA","OTP"):
                gaps.append(gap("ground_fare_route_unsupported","Automatic consumer coach fare references currently cover only Craiova airport → Otopeni airport. No nearby stop or transfer was assumed.",[group.id]))
                continue
            if group.adult_count!=group.size or not 1<=group.size<=8:
                gaps.append(gap("ground_fare_party_unsupported","Consumer coach search needs an explicitly adult-only party of at most eight passengers. Child ages or discounted fares were not inferred.",[group.id]))
                continue
            ready=max(world.sim_time,group.ready_at)
            if ready>=group.deadline:
                gaps.append(gap("ground_fare_window_closed","The group's readiness/deadline window has elapsed; no dated coach reference was requested.",[group.id]))
                continue
            day=(epoch+timedelta(minutes=ready)).astimezone(zone).date()
            request=BileteriaSearchRequest(origin_airport="CRA",destination_airport="OTP",travel_date=day,adults=group.size)
            try:
                self.coach_fares.provider.validate_request(request)
            except (ValueError,ProviderError):
                gaps.append(gap("ground_fare_date_unsupported","The group's earliest local travel date is outside the consumer source's supported search window. Its date was not replaced.",[group.id]))
                continue
            key=Store.fingerprint(request.model_dump(mode="json"))
            searches.setdefault(key,{"request":request,"group_ids":[]})["group_ids"].append(group.id)
        for item in searches.values():
            current=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
            if current["cancel_requested"]:
                return
            if current["max_provider_calls"]-current["calls_dispatched"]<=reserve_calls:
                gaps.append(gap("ground_fare_allowance_uncovered","The explicit call allowance cannot cover this coach reference while preserving an independently valid plan's decision. No extra request was made.",item["group_ids"]))
                continue
            query=item["request"]
            try:
                result=await self._call(job,owner,"searching_ground",SOURCE,query.model_dump(mode="json"),item["group_ids"],
                    lambda:self.coach_fares.provider.search(query),optional_reference=True)
            except (TimeoutError,ProviderError):
                gaps.append(gap("ground_fare_source_failed","The consumer coach source did not return a usable dated fare reference. Its failed or unknown request is recorded; no retry or substitute fare was used.",item["group_ids"]))
                continue
            # The normalized provider response contains bounded public facts,
            # not HTML, operational tokens, capacity, holds or allocations.
            references.append({"group_ids":item["group_ids"],"result":self.coach_fares.enrich(result,{"table":None,"cache":None,"code":"fx_not_requested"})})
            gaps.append(gap("ground_availability_unverified" if result["options"] else "ground_fare_not_found",
                "Dated coach fares are reference information only. Seats, luggage, exact physical connections and suitability for the group's readiness/deadline remain unverified; these services were not allocated. Only the earliest local calendar date was searched."
                if result["options"] else "No priced consumer coach reference was returned for the requested party and earliest local travel date. This does not establish that no other transport exists.",item["group_ids"]))
            await self._update(job,owner,ground_references=references,gaps=gaps)
        priced=[entry for entry in references if entry["result"]["options"]]
        if priced:
            current=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
            if current["cancel_requested"]:
                return
            ids=sorted({identity for entry in priced for identity in entry["group_ids"]})
            if current["max_provider_calls"]-current["calls_dispatched"]>reserve_calls:
                try:
                    source=await self._call(job,owner,"reference_fx",FX_SOURCE,{"url":FX_URL,"currency":"RON"},ids,
                        lambda:_rates(self.coach_fares.fx),optional_reference=True)
                    rates=_restore_rates(source)
                    for entry in priced:
                        entry["result"]=self.coach_fares.enrich(entry["result"],rates)
                except (TimeoutError,ProviderError):
                    gaps.append(gap("ground_fare_fx_unavailable","The ECB reference was unavailable. Original RON fares remain recorded; no EUR amount was invented.",ids))
            else:
                gaps.append(gap("ground_fare_fx_allowance_uncovered","The explicit call allowance has no spare ECB lookup. Original RON fares remain recorded without EUR estimates.",ids))
        await self._update(job,owner,ground_references=references,gaps=gaps)
        return references

    async def run(self,job,owner):
        started=time.perf_counter()
        try:
            await self._run(job,owner,started)
        except asyncio.CancelledError:
            current=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
            if current["status"] not in TERMINAL:
                public=await asyncio.to_thread(self.ledger.get,job["id"])
                unknown=any(step["status"] in ("dispatched","unknown") for step in public["steps"])
                await asyncio.to_thread(self.ledger.finish,job["id"],owner,"unknown_outcome" if unknown else "needs_review","Worker stopped; review recorded outcomes before explicitly starting another attempt.")
            raise
        except Exception as exc:
            current=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
            if current["status"] not in TERMINAL:
                message=current["message"] if current.get("_failure_status") else exc.message if isinstance(exc,DomainError) else "Recovery stopped after an internal validation failure; no new provider call was retried."
                status=current.get("_failure_status") or ("cancelled" if current["cancel_requested"] else "needs_review")
                await asyncio.to_thread(self.ledger.finish,job["id"],owner,status,message)

    async def _run(self,job,owner,started):
        world=await asyncio.to_thread(self.store.read)
        job["_planning_version"]=world.version
        if world.scenario_id!=job["_scenario_id"] or scope_fingerprint(world,job["incident_id"],job["group_ids"])!=job["_scope_hash"]:
            raise DomainError(409,"Selected passengers or incident constraints changed. No new provider request was issued; review and start a new job explicitly.")
        if await self._cancelled(job,owner):
            return
        conflicts = await asyncio.to_thread(fixed_confirmed_airport_conflicts, world)
        if conflicts:
            await self._update(job, owner, status="blocked", stage="finished",
                message="Fixed confirmed travel conflicts with case airport restrictions. Manual review is required before recovery; no provider request was issued and no booking was changed.",
                gaps=[gap("confirmed_airport_restriction_conflict",
                    "Confirmed flight itineraries retained by every recovery candidate cannot pass the current airport restrictions of their linked cases. Review those confirmed journeys before starting another recovery; out-of-scope bookings were not cancelled or replaced.",
                    sorted({item["group_id"] for item in conflicts}))])
            return
        groups=[item for item in world.groups if item.id in job["group_ids"]]
        require_consistent_confirmations(world)
        gaps=[]
        if not self.ai or not self.ai.configured:
            await self._update(job,owner,status="blocked",stage="finished",message="Real AI is unavailable; no scripted planning fallback was used.",gaps=[gap("ai_not_configured","Connect the real Responses and Decisions provider.",job["group_ids"])])
            return
        if job["_request"].get("romanian_hotel_recovery") is not None:
            await run_romanian_hotel_recovery(self,job,owner,world,started)
            return
        if job["_request"].get("hotel_recovery") is not None:
            await run_hotel_recovery(self,job,owner,world,started)
            return
        if job["_request"].get("after_flight_transfer") is not None:
            await run_after_flight_recovery(self,job,owner,world,started)
            return
        validate_airport_transfer_job(world,RecoveryJobRequest.model_validate({key:value for key,value in job["_request"].items() if key!="incident_id"}),self.airport_cab)
        source_counts={}
        projected,airport_transfers=await source_airport_transfers(self,job,owner,world,gaps,source_counts)
        if await self._cancelled(job,owner):
            return
        airport_transfer_ids={identity for item in airport_transfers for identity in item["resources"]}
        for mode in job["modes"]:
            if mode=="hotel" or (mode=="bus" and self.coach_fares is None and not job["_request"].get("airport_transfers")):
                gaps.append(gap("hotel_inputs_required" if mode=="hotel" else "ground_inputs_required","Automatic hotel sourcing requires confirmed stay times, room occupancy and verified property transfers." if mode=="hotel" else "Automatic coach/private-transfer sourcing requires exact passenger ages or baggage, physical pickup links, EUR costs and sourced arrival times. Existing complete sourced links may still be used.",job["group_ids"]))
        options=[]
        if "flight" in job["modes"]:
            if self.travel.flight_configured:
                options,missing=await asyncio.to_thread(flight_query_options,projected,groups)
                gaps.extend(missing)
            else:
                gaps.append(gap("flight_provider_not_configured","Connect a production flight-offer account. FR24 observations do not provide fares or seats.",job["group_ids"]))
        await self._update(job,owner,gaps=gaps)
        strategies=[Strategy(id=name,label=label,objective=name) for name,label in (("balanced","Balanced recovery"),("fastest","Earliest arrival"),("lowest_cost","Lowest cost"))]
        staged=[]
        timings={"interpretation_ms":0.0,"solver_ms":0.0,"decisions_ms":0.0}
        recorded=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
        search_allowance=job["max_provider_calls"]-recorded["calls_dispatched"]-2
        if options and search_allowance<1:
            gaps.append(gap("search_allowance_uncovered","The remaining allowance cannot cover query planning, a new flight search and the final decision. Existing sourced journeys may still be evaluated; no allowance was increased.",sorted({item["group_id"] for item in options})))
            options=[]
        if options:
            scope_world=projected.model_copy(deep=True)
            scope_world.groups=[group for group in scope_world.groups if group.id in job["group_ids"]]
            scope_world.offers=[offer for offer in scope_world.offers if offer.group_id in job["group_ids"]]
            request={"objective":job["objective"],"instructions":job["_request"]["instructions"],"max_search_calls":search_allowance,
                     "incident":next(item.model_dump(exclude={"flight","flight_ref","flight_context_state"}) for item in world.incidents if item.id==job["incident_id"])}
            if airport_transfers:
                request["context"]=AIRPORT_TRANSFER_CONTEXT
            before=time.perf_counter()
            searches=await self._call(job,owner,"planning_queries","OpenAI Responses",request,job["group_ids"],lambda:self.ai.plan_recovery_queries(scope_world.model_dump(mode="json"),options,request))
            timings["interpretation_ms"]=round((time.perf_counter()-before)*1000,2)
            known={item["id"]:item for item in options}
            selected=searches.get("query_ids",[])
            if len(selected)>request["max_search_calls"] or len(selected)!=len(set(selected)) or not set(selected)<=known.keys():
                raise DomainError(502,"AI returned an unapproved search query; no supplier call was issued")
            strategies=[Strategy.model_validate(item) for item in searches["strategies"]]
            if not 1<=len(strategies)<=3 or len({item.id for item in strategies})!=len(strategies) or any(item.preferred_airport is not None for item in strategies):
                raise DomainError(502,"AI returned invalid recovery policies")
            uncovered=sorted({item["group_id"] for item in options}-{known[key]["group_id"] for key in selected})
            if uncovered:
                gaps.append(gap("search_allowance_uncovered","No supplier query was selected for these groups within this job's explicit request allowance.",uncovered))
            await self._update(job,owner,gaps=gaps)
            semaphore=asyncio.Semaphore(3)
            async def source_option(key):
                async with semaphore:
                    option=known[key]
                    if airport_transfers and not await asyncio.to_thread(current_flight_connection,projected,groups,option):
                        return None
                    query=TravelSearchRequest.model_validate(option["query"])
                    return await self._call(job,owner,"searching_flights",self.travel.flight_source,option,[option["group_id"]],lambda:self._search(query))
            tasks=[asyncio.create_task(source_option(key)) for key in selected]
            try:
                results=await asyncio.gather(*tasks,return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                raise
            failures=[item for item in results if isinstance(item,BaseException)]
            if failures:
                current=await asyncio.to_thread(self.ledger.get,job["id"],private=True)
                if current["cancel_requested"] and not current.get("_failure_status"):
                    await self._cancelled(job,owner)
                    return
                raise failures[0]
            for key,result in zip(selected,results):
                option=known[key]
                if result is None:
                    gaps.append(gap("ground_connection_expired","The ground connection no longer supports this approved flight-search window. No flight request, shifted pickup or automatic source refresh was issued.",[option["group_id"]]))
                    continue
                source_counts[result["source"]]=source_counts.get(result["source"],0)+len(result["items"])
                if not result["items"]:
                    gaps.append(gap("no_flight_offers","The supplier returned no offers for a selected dated search; this is not proof that no other service exists.",[option["group_id"]]))
                for quote in result["items"]:
                    staged.append((option,quote))
        if await self._cancelled(job,owner):
            return
        imported=[]
        superseded={}
        group_map={item.id:item for item in groups}
        for option,quote in staged:
            group=group_map[option["group_id"]]
            try:
                trial=projected.model_copy(deep=True)
                added=append_travel_quote(trial,quote,option["query"],group,"flight",
                    keep_resource_ids={identity for _,_,identity in imported}|airport_transfer_ids|
                        ({item.id for item in world.resources} if airport_transfers else set()),
                    keep_location_ids={item.id for item in world.airports} if airport_transfers else set(),stage=bool(airport_transfers))
                resource=next(item for item in trial.resources if item.id==added["resource_ids"][0])
                if resource_blocked_by_incident(trial,resource,group) or resource.departure<option["earliest_departure"] or resource.arrival>option["latest_arrival"]:
                    raise DomainError(422,"Sourced offer does not meet this incident's route or timing constraints")
                projected=trial
                imported.append((option,quote,resource.id))
                superseded.setdefault(group.id,set()).update(added.get("superseded_resource_ids",[]))
            except DomainError as exc:
                if isinstance(exc.details,dict) and exc.details.get("quote_revision_conflict"):
                    raise
                capacity = isinstance(exc.details,dict) and exc.details.get("inventory_capacity_exhausted")
                reason=gap("inventory_capacity_exhausted" if capacity else "quote_not_usable",exc.message,[group.id])
                if reason not in gaps:
                    gaps.append(reason)
        await self._update(job,owner,stage="evaluating",message="Validating complete sourced journeys and preserving every out-of-scope allocation.",gaps=gaps)
        before=time.perf_counter()
        scope=set(job["group_ids"])
        candidates=await asyncio.to_thread(lambda:[build_candidate(projected,item,scope_group_ids=scope) for item in strategies])
        candidates=[item for item in candidates if item.validation["valid"]]
        timings["solver_ms"]=round((time.perf_counter()-before)*1000,2)
        ground_references=[]
        cab_candidate=any(leg.resource_id in airport_transfer_ids for candidate in candidates for allocation in candidate.allocations for leg in allocation.itinerary.legs)
        if "bus" in job["modes"] and self.coach_fares is not None and not cab_candidate:
            ground_references=await self._ground_references(job,owner,world,groups,gaps,reserve_calls=int(any(item.recovered_passengers for item in candidates))) or []
            if await self._cancelled(job,owner):
                return
        elif "bus" in job["modes"] and self.coach_fares is not None and cab_candidate:
            gaps.append(gap("ground_reference_deferred","Optional coach fare observations were deferred while a short-lived airport vehicle quote awaits the final decision.",job["group_ids"]))
        if not candidates or not any(item.recovered_passengers for item in candidates):
            gaps.append(gap("no_complete_journey","No complete sourced itinerary passed budget, accessibility, deadline, connection and incident checks. Accommodation alone is not recovery.",job["group_ids"]))
            await self._update(job,owner,status="blocked",stage="finished",message="No complete sourced recovery proposal is currently feasible; no operational allocation changed.",gaps=gaps)
            return
        if await self._cancelled(job,owner):
            return
        before=time.perf_counter()
        decision_request={"text":f"Recover only incident {job['incident_id']} groups {', '.join(job['group_ids'])}. Objective: {job['objective']}. {job['_request']['instructions']}","context":"All other groups' allocations are fixed. These are sourced quotes, not bookings. Explain remaining exceptions honestly."}
        if airport_transfers:
            decision_request["context"]+=" "+AIRPORT_TRANSFER_CONTEXT
        if "bus" in job["modes"] and self.coach_fares is not None:
            # Application-authored bounded facts only. Supplier HTML, messages
            # and instructions never enter this independent decision context.
            count=sum(len(entry["result"]["options"]) for entry in ground_references)
            ground_codes=sorted({item["code"] for item in gaps if item["code"].startswith("ground_")})[:10]
            decision_request["context"]+=(f" Consumer coach observations: {len(ground_references)}; displayed fare options: {count}. "
                "Coach seat availability and physical connections are unverified. These references are excluded from validated candidates and are not resources, allocations or bookings. "
                "Recorded ground-reference gap codes: "+(", ".join(ground_codes) or "none")+".")
        choice=await self._call(job,owner,"deciding","OpenAI Decisions",{"candidate_ids":[item.id for item in candidates],"request":decision_request},job["group_ids"],lambda:self.ai.choose(projected.model_dump(mode="json"),[item.model_dump(mode="json") for item in candidates],decision_request))
        timings["decisions_ms"]=round((time.perf_counter()-before)*1000,2)
        plan=next((item for item in candidates if item.id==choice.get("plan_id")),None)
        if plan is None:
            raise DomainError(502,"Decisions selected no validated scoped candidate")
        if await self._cancelled(job,owner):
            return
        waits=journey_waits(projected,plan)
        overnight_groups=sorted({item["group_id"] for item in waits if item["overnight"] is True})
        if overnight_groups:
            gaps.append(gap("overnight_wait_without_accommodation","The sourced itinerary includes a wait across local midnight with no accommodation included for that interval. No mandatory rest requirement was assumed; review the recorded wait durations.",overnight_groups))
        await self._update(job,owner,stage="applying",message="Checking current version and quote freshness before the atomic scoped commit.",gaps=gaps)
        commit_result={}
        chosen_resources={leg.resource_id for allocation in plan.allocations for leg in allocation.itinerary.legs}
        def apply(current):
            if scope_fingerprint(current,job["incident_id"],job["group_ids"])!=job["_scope_hash"]:
                raise DomainError(409,"Passenger or incident constraints changed; sourced results were not applied")
            # A newer observation retires an old fare even when another offer
            # wins. This does not import unused quotes that expired meanwhile.
            for group_id,resource_ids in superseded.items():
                retire_travel_quotes(current,group_id,resource_ids)
            for transfer in airport_transfers:
                from .airport_cab_planning import append_airport_cab_options
                selected=[option_id for resource_id,option_id in transfer["resources"].items() if resource_id in chosen_resources]
                if selected:
                    append_airport_cab_options(current,transfer["context"],transfer["response"],selected,
                        now=self.airport_cab.now(),keep_resource_ids=chosen_resources)
                else:
                    retire_travel_quotes(current,transfer["context"]["group_id"],transfer["superseded_resource_ids"])
            for option,quote,resource_id in imported:
                if resource_id not in chosen_resources:
                    continue
                group=next(item for item in current.groups if item.id==option["group_id"])
                append_travel_quote(current,quote,option["query"],group,"flight", keep_resource_ids=chosen_resources|
                    ({item.id for item in world.resources} if airport_transfers else set()),
                    keep_location_ids={item.id for item in world.airports} if airport_transfers else set())
            before_ids={offer.id for offer in current.offers}
            validate_airport_transfer_selection(current,plan,job["_request"].get("airport_transfers",[]),fresh_resource_ids=airport_transfer_ids)
            answer=self.service._hold_plan(current,plan,"ai",decision_request["text"],choice.get("provider","OpenAI Decisions"),choice["explanation"],candidates,round((time.perf_counter()-started)*1000,2),timings=timings,scope_group_ids=scope)
            commit_result.update(decision_id=answer["decision"]["id"],plan_id=plan.id,recovered_passengers=plan.recovered_passengers,
                exception_passengers=sum(item["passengers"] for item in plan.exceptions),offer_ids=[offer.id for offer in current.offers if offer.id not in before_ids],
                resource_ids=sorted({leg.resource_id for allocation in plan.allocations for leg in allocation.itinerary.legs}),source_counts=source_counts,waits=waits)
            return answer
        def completed(conn,response):
            self.ledger.complete_in_transaction(conn,job["id"],owner,response,commit_result)
            committed=self.store._load(conn)
            selected_groups={allocation.group_id for allocation in plan.allocations
                if any(leg.resource_id in airport_transfer_ids for leg in allocation.itinerary.legs)}
            for raw in job["_request"].get("airport_transfers",[]):
                if raw["group_id"] in selected_groups:
                    group=next(item for item in committed.groups if item.id==raw["group_id"])
                    self.airport_cab_profiles.save_in_transaction(conn,committed,group,
                        AirportCabRecoveryInput.model_validate(raw),job["id"])
        task=asyncio.create_task(asyncio.to_thread(self.store.mutate,"recovery-job-apply:"+job["id"],{"job_id":job["id"]},world.version,apply,on_commit=completed,request_started=started))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # SQLite cannot be cancelled by cancelling to_thread. Await the
            # commit/rollback before allowing shutdown to relinquish ownership.
            with suppress(Exception):
                await task
            raise

    async def _search(self,query):
        result=await self.travel.search(query)
        if result.get("state")!="ready":
            raise ProviderError(result.get("message") or "Flight supplier did not return a live result.",provider=result.get("source","flight supplier"),code=result.get("code",result.get("state","provider_error")))
        if not isinstance(result.get("items"),list) or len(result["items"])>10:
            raise ProviderError("Flight supplier returned an unbounded offer list.",provider=result.get("source","flight supplier"),code="invalid_response")
        return result
