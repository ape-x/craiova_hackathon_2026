"""Bounded, incident-scoped sourcing and real AI selection; never supplier booking."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import logging
import sqlite3
import time
from typing import Literal

from pydantic import Field, field_validator, model_serializer

from .airport_catalog import lookup_airport
from .hotel_location import coordinate_timezone, property_zone
from .hotel_recovery import HotelRecoveryInputs, run_hotel_recovery, validate_hotel_job
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

    @model_serializer(mode="wrap")
    def compatible_payload(self, handler):
        result = handler(self)
        if self.hotel_recovery is None:
            result.pop("hotel_recovery",None)
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
    selected=[]
    for group_id in group_ids:
        group=next((item for item in world.groups if item.id==group_id),None)
        if group is None or group.incident_id!=incident.id or group_id not in incident.group_ids:
            raise DomainError(409,"Every selected passenger group must be explicitly bound to this incident")
        if group.status=="confirmed" or any(offer.group_id==group_id and offer.status=="confirmed" for offer in world.offers):
            raise DomainError(409,"Supplier-confirmed groups require separate operator handling; this job cannot replace their bookings")
        selected.append(group.model_dump(exclude={"token","members","name"}))
    context=incident.model_dump(exclude={"flight","flight_context_state","flight_ref"})
    active=[offer.model_dump() for offer in world.offers if offer.group_id in group_ids and offer.status in ("held","proposed","accepted","confirmed")]
    return Store.fingerprint({"scenario":world.scenario_id,"epoch":world.epoch,"incident":context,"groups":selected,"active_offers":active})


def gap(code,message,group_ids):
    return {"code":code,"message":message,"group_ids":list(group_ids)}


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
    locations={item.id:item for item in world.airports}
    epoch=datetime.fromisoformat(world.epoch)
    options_by_group=[]
    gaps=[]
    for group in groups:
        if group.adult_count!=group.size:
            gaps.append(gap("passenger_composition_required","Flight searches currently require an explicitly adult-only group; no ages were inferred.",[group.id]))
            continue
        blocked={item.unavailable_airport_code for item in world.incidents if item.id==group.incident_id and item.unavailable_airport_code}
        origins={}
        expansions=0
        def visit(location,minute,legs,cost):
            nonlocal expansions
            expansions+=1
            if expansions>4000:
                return
            node=locations.get(location)
            if node and node.kind=="airport" and lookup_airport(node.code) and node.operational_status!="closed" and node.code not in blocked:
                origins[location]=min(minute,origins.get(location,minute))
            if len(legs)>=5:
                return
            for resource in world.resources:
                if resource.kind not in ("hotel","bus"):
                    continue
                connection=10 if legs else 0
                if not resource_feasible(world,resource,group,location,minute+connection,{item.id for item in legs}):
                    continue
                from .solver import resource_cost
                total=cost+resource_cost(resource,group)
                if total<=group.budget:
                    visit(resource.destination,resource.arrival,legs+[resource],total)
        visit(group.origin,max(world.sim_time,group.ready_at),[],0)
        destinations={}
        target=locations.get(group.destination)
        if target and target.kind=="airport" and lookup_airport(target.code) and target.code not in blocked and target.operational_status!="closed":
            destinations[target.id]=group.deadline
        for resource in world.resources:
            node=locations.get(resource.origin)
            if resource.kind=="bus" and resource.destination==group.destination and node and node.kind=="airport" and node.code not in blocked and resource_feasible(world,resource,group,resource.origin,max(world.sim_time,group.ready_at),set()):
                destinations[resource.origin]=max(destinations.get(resource.origin,0),resource.departure-10)
        choices=[]
        for origin,ready in sorted(origins.items()):
            airport=lookup_airport(origin)
            if airport is None:
                continue
            zone=property_zone(coordinate_timezone(airport["lat"],airport["lon"]))
            first=(epoch+timedelta(minutes=ready)).astimezone(zone).date()
            for destination,latest in sorted(destinations.items()):
                if origin==destination or ready+30>=latest:
                    continue
                last=(epoch+timedelta(minutes=latest)).astimezone(zone).date()
                for offset in range(min(7,(last-first).days+1)):
                    day=first+timedelta(days=offset)
                    try:
                        request=TravelSearchRequest(kind="flight",origin_code=origin,destination_code=destination,departure_date=day,adults=group.size,
                            earliest_departure_at=max(utcnow(),epoch+timedelta(minutes=ready+30)),latest_arrival_at=epoch+timedelta(minutes=latest))
                    except ValueError:
                        continue
                    data={"group_id":group.id,"query":request.model_dump(mode="json"),"earliest_departure":ready+30,"latest_arrival":latest}
                    choices.append({"id":"QRY-"+Store.fingerprint(data)[:20],**data})
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


class RecoveryJobs:
    def __init__(self,settings,store,service,travel_provider,ai,*,hotel_provider=None,apl_provider=None,fx_provider=None):
        self.settings,self.store,self.service,self.travel,self.ai=settings,store,service,travel_provider,ai
        self.hotel_provider,self.apl_provider,self.fx_provider=hotel_provider,apl_provider,fx_provider
        self.ledger=RecoveryLedger(store,settings.request_timeout_seconds+60)
        self.task=None

    def start(self,incident_id,request,*,on_created=None):
        payload={**request.model_dump(mode="json"),"incident_id":incident_id}
        replay=self.ledger.replay("recovery-job:"+request.idempotency_key,payload)
        if replay and on_created is None:
            return replay
        world=self.store.read()
        digest=scope_fingerprint(world,incident_id,request.group_ids)
        validate_hotel_job(world,request)
        return self.ledger.create(incident_id,request,digest,world.scenario_id,on_created=on_created)

    def start_in_transaction(self,conn,world,incident_id,request):
        """Validate the saved post-report world and queue without opening a connection."""
        if not conn.in_transaction:
            raise RuntimeError("Recovery job creation requires an active transaction")
        digest=scope_fingerprint(world,incident_id,request.group_ids)
        validate_hotel_job(world,request)
        return self.ledger.create_in_transaction(conn,world,incident_id,request,digest,world.scenario_id)

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

    async def _call(self,job,owner,stage,provider,query,group_ids,call):
        current=await asyncio.to_thread(self.store.read)
        if current.version!=job["_planning_version"] or scope_fingerprint(current,job["incident_id"],job["group_ids"])!=job["_scope_hash"]:
            raise DomainError(409,"Operation changed while sourcing; no further provider call was issued. Review the recorded results before a new attempt.")
        step=await asyncio.to_thread(self.ledger.dispatch,job["id"],owner,stage,provider,query,group_ids)
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
            unknown=code in ("timeout","connection","http_500","http_502","http_503","http_504")
            message=str(exc) if isinstance(exc,ProviderError) else "Provider deadline reached; upstream outcome and charges are unconfirmed."
            await asyncio.to_thread(self.ledger.outcome,job["id"],owner,step,"unknown" if unknown else "failed",message)
            await asyncio.to_thread(self.ledger.halt,job["id"],owner,"unknown_outcome" if unknown else "failed",message+" No automatic retry was issued; other dispatched outcomes are being recorded.")
            raise
        except Exception:
            await asyncio.to_thread(self.ledger.outcome,job["id"],owner,step,"unknown","Provider dispatch did not produce a recordable outcome. No retry was issued.")
            await asyncio.to_thread(self.ledger.halt,job["id"],owner,"unknown_outcome","Provider outcome could not be established; no automatic retry or plan application was attempted.")
            raise

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
        groups=[item for item in world.groups if item.id in job["group_ids"]]
        gaps=[]
        if not self.ai or not self.ai.configured:
            await self._update(job,owner,status="blocked",stage="finished",message="Real AI is unavailable; no scripted planning fallback was used.",gaps=[gap("ai_not_configured","Connect the real Responses and Decisions provider.",job["group_ids"])])
            return
        if job["_request"].get("hotel_recovery") is not None:
            await run_hotel_recovery(self,job,owner,world,started)
            return
        for mode in job["modes"]:
            if mode in ("hotel","bus"):
                gaps.append(gap("hotel_inputs_required" if mode=="hotel" else "ground_inputs_required","Automatic hotel sourcing requires confirmed stay times, room occupancy and verified property transfers." if mode=="hotel" else "Automatic coach/private-transfer sourcing requires exact passenger ages or baggage, physical pickup links, EUR costs and sourced arrival times. Existing complete sourced links may still be used.",job["group_ids"]))
        options=[]
        if "flight" in job["modes"]:
            if self.travel.flight_configured:
                options,missing=await asyncio.to_thread(flight_query_options,world,groups)
                gaps.extend(missing)
            else:
                gaps.append(gap("flight_provider_not_configured","Connect a production flight-offer account. FR24 observations do not provide fares or seats.",job["group_ids"]))
        await self._update(job,owner,gaps=gaps)
        strategies=[Strategy(id=name,label=label,objective=name) for name,label in (("balanced","Balanced recovery"),("fastest","Earliest arrival"),("lowest_cost","Lowest cost"))]
        staged=[]
        source_counts={}
        timings={"interpretation_ms":0.0,"solver_ms":0.0,"decisions_ms":0.0}
        if options:
            scope_world=world.model_copy(deep=True)
            scope_world.groups=groups
            scope_world.offers=[offer for offer in world.offers if offer.group_id in job["group_ids"]]
            request={"objective":job["objective"],"instructions":job["_request"]["instructions"],"max_search_calls":job["max_provider_calls"]-2,
                     "incident":next(item.model_dump(exclude={"flight","flight_ref","flight_context_state"}) for item in world.incidents if item.id==job["incident_id"])}
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
                source_counts[result["source"]]=source_counts.get(result["source"],0)+len(result["items"])
                if not result["items"]:
                    gaps.append(gap("no_flight_offers","The supplier returned no offers for a selected dated search; this is not proof that no other service exists.",[option["group_id"]]))
                for quote in result["items"]:
                    staged.append((option,quote))
        if await self._cancelled(job,owner):
            return
        projected=world.model_copy(deep=True)
        imported=[]
        superseded={}
        group_map={item.id:item for item in groups}
        for option,quote in staged:
            group=group_map[option["group_id"]]
            try:
                trial=projected.model_copy(deep=True)
                added=append_travel_quote(trial,quote,option["query"],group,"flight",
                    keep_resource_ids={identity for _,_,identity in imported})
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
        if not candidates or not any(item.recovered_passengers for item in candidates):
            gaps.append(gap("no_complete_journey","No complete sourced itinerary passed budget, accessibility, deadline, connection and incident checks. Accommodation alone is not recovery.",job["group_ids"]))
            await self._update(job,owner,status="blocked",stage="finished",message="No complete sourced recovery proposal is currently feasible; no operational allocation changed.",gaps=gaps)
            return
        if await self._cancelled(job,owner):
            return
        before=time.perf_counter()
        decision_request={"text":f"Recover only incident {job['incident_id']} groups {', '.join(job['group_ids'])}. Objective: {job['objective']}. {job['_request']['instructions']}","context":"All other groups' allocations are fixed. These are sourced quotes, not bookings. Explain remaining exceptions honestly."}
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
            for option,quote,resource_id in imported:
                if resource_id not in chosen_resources:
                    continue
                group=next(item for item in current.groups if item.id==option["group_id"])
                append_travel_quote(current,quote,option["query"],group,"flight", keep_resource_ids=chosen_resources)
            before_ids={offer.id for offer in current.offers}
            answer=self.service._hold_plan(current,plan,"ai",decision_request["text"],choice.get("provider","OpenAI Decisions"),choice["explanation"],candidates,round((time.perf_counter()-started)*1000,2),timings=timings,scope_group_ids=scope)
            commit_result.update(decision_id=answer["decision"]["id"],plan_id=plan.id,recovered_passengers=plan.recovered_passengers,
                exception_passengers=sum(item["passengers"] for item in plan.exceptions),offer_ids=[offer.id for offer in current.offers if offer.id not in before_ids],
                resource_ids=sorted({leg.resource_id for allocation in plan.allocations for leg in allocation.itinerary.legs}),source_counts=source_counts,waits=waits)
            return answer
        def completed(conn,response):
            self.ledger.complete_in_transaction(conn,job["id"],owner,response,commit_result)
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
