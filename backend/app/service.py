from __future__ import annotations

import secrets
import time
import asyncio
import logging
import sqlite3
from datetime import timedelta
from pydantic import ValidationError
from .config import Settings
from .models import Allocation, Decision, Offer, Reservation, ScenarioChange, Strategy, World, canonical_id
from .scenario_authoring import add_airport, add_passengers, add_resource, airport_reference, validate_route, validate_schedule
from .scenario_schema import MAX_COMMAND_EVENTS, WeatherDefinition
from .weather import apply_weather_to_flight, departure_allowed, landing_allowed
from .scenario_reset import reset_scenario
from .solver import active_offers, build_candidate, default_strategies, units, validate_allocations
from .store import DomainError, Store, audit, utcnow


def lookup(items,target,label):
    found=next((x for x in items if x.id==target),None)
    if not found:
        raise DomainError(404,f"Unknown {label}: {target}")
    return found


def invalidate(world: World,offer: Offer,reason: str):
    if offer.status not in ("held","confirmed"):
        return
    was_confirmed=offer.status=="confirmed"
    offer.status="invalidated"
    offer.reason=reason
    for reservation in world.reservations:
        if reservation.offer_id==offer.id:
            reservation.status="released"
    lookup(world.groups,offer.group_id,"group").status="affected"
    audit(world,"confirmation_affected" if was_confirmed else "offer_invalidated",reason,offer.id,{"group_id":offer.group_id})


def strict_number(value,minimum=0,maximum=100000,integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not minimum<=value<=maximum or (integer and int(value)!=value):
        raise DomainError(422,f"Value must be {'an integer' if integer else 'a number'} between {minimum} and {maximum}")
    return int(value) if integer else float(value)


def candidate_rank(plan,objective="balanced"):
    if objective=="fastest":
        secondary=(plan.average_arrival,plan.total_cost)
    elif objective=="lowest_cost":
        secondary=(plan.total_cost,plan.average_arrival)
    else:
        secondary=(plan.total_cost+plan.average_arrival*.1,plan.average_arrival)
    return (-plan.recovered_passengers,*secondary,plan.id)


def shortlist_candidates(candidates,limit=8):
    """Bound model input only after all eligible airport plans are evaluated.

    Retain the global winners for each measured objective, then add the best
    remaining airport alternatives before filling with other strong candidates.
    """
    if len(candidates)<=limit:
        return candidates
    chosen=[]
    seen=set()
    def include(plan):
        if plan.id not in seen and len(chosen)<limit:
            chosen.append(plan)
            seen.add(plan.id)
    for objective in ("balanced","fastest","lowest_cost"):
        include(min(candidates,key=lambda plan:candidate_rank(plan,objective)))
    represented={plan.strategy.preferred_airport for plan in chosen}
    for plan in sorted(candidates,key=candidate_rank):
        if plan.strategy.preferred_airport not in represented:
            include(plan)
            represented.add(plan.strategy.preferred_airport)
    for plan in sorted(candidates,key=candidate_rank):
        include(plan)
    return chosen


def apply_change(world: World,event: ScenarioChange):
    kind,target,value=event.type,event.target_id,event.value
    if kind=="airport_weather" and world.mode!="simulation":
        raise DomainError(409,"Simulation edits are disabled outside simulation mode.")
    if kind=="scenario_reset":
        reset_scenario(world,value)
        return
    impacted_resources=set()
    impacted_groups=set()
    diverted_groups=set()
    if kind=="airport_added":
        airport=add_airport(world,target,value)
        audit(world,kind,f"Added simulated airport {airport.id}: {airport.name}.",airport.id,{"value":value,"source":"simulation","location_source":"airport_catalog" if value is None else "custom_simulation"})
        return
    elif kind=="resource_added":
        resource=add_resource(world,target,value)
        audit(world,kind,f"Added synthetic {resource.kind} {resource.id}: {resource.origin} → {resource.destination}, {resource.capacity} {'rooms' if resource.kind=='hotel' else 'seats'}.",resource.id,{"value":value,"source":"simulation"})
        return
    elif kind=="passengers_added":
        groups=add_passengers(world,value)
        audit(world,kind,f"Added {sum(g.size for g in groups)} synthetic passengers in {len(groups)} whole groups: {groups[0].origin} → {groups[0].destination}.",None,{"value":value,"group_ids":[g.id for g in groups],"source":"simulation"})
        return
    elif kind in ("resource_schedule","resource_route","resource_cost"):
        resource=lookup(world.resources,target,"resource")
        if kind=="resource_cost":
            cost=strict_number(value,0,100000)
            if cost!=resource.cost_per_person:
                resource.cost_per_person=cost
                impacted_resources.add(resource.id)
        elif kind=="resource_schedule":
            departure,arrival=value["departure"],value["arrival"]
            airborne=resource.kind=="flight" and resource.departure<=world.sim_time<resource.arrival and (resource.available or resource.status=="needs_diversion")
            if resource.departure<=world.sim_time and (not airborne or departure!=resource.departure):
                raise DomainError(409,"A departed service cannot be moved back in time; an airborne flight may only change its arrival time")
            validate_schedule(world,departure,arrival,allow_airborne=airborne)
            if (departure,arrival)!=(resource.departure,resource.arrival):
                resource.departure,resource.arrival=departure,arrival
                impacted_resources.add(resource.id)
                if resource.kind=="flight" and (resource.available or resource.status in ("needs_diversion","weather_held")):
                    for group in world.groups:
                        if group.original_flight_id==resource.id and group.origin==resource.destination:
                            group.ready_at=max(world.sim_time,arrival+30)
                            impacted_groups.add(group.id)
        else:
            if resource.departure<=world.sim_time:
                raise DomainError(409,"A departed service cannot change route; use flight_diverted for an airborne flight")
            origin,destination=validate_route(world,resource.kind,value["origin"],value["destination"],available=resource.available)
            if (origin,destination)!=(resource.origin,resource.destination):
                previous_destination=resource.destination
                resource.origin,resource.destination=origin,destination
                impacted_resources.add(resource.id)
                for group in world.groups:
                    if group.original_flight_id==resource.id:
                        if group.origin==previous_destination and resource.kind=="flight" and (resource.available or resource.status=="weather_held"):
                            group.origin=destination
                            group.ready_at=max(world.sim_time,resource.arrival+30)
                            impacted_groups.add(group.id)
                        if group.status=="scheduled" and group.origin!=group.destination:
                            group.status="waiting"
        if kind in ("resource_schedule","resource_route") and apply_weather_to_flight(world,resource):
            impacted_resources.add(resource.id)
            for group in world.groups:
                if group.original_flight_id==resource.id and resource.status in ("needs_diversion","weather_held"):
                    group.status="affected"
                    if resource.status=="needs_diversion":
                        diverted_groups.add(group.id)
    elif kind=="airport_weather":
        airport=airport_reference(world,target)
        airport.weather=WeatherDefinition.model_validate(value)
        for resource in world.resources:
            if resource.kind!="flight" or airport.id not in (resource.origin,resource.destination):
                continue
            if not apply_weather_to_flight(world,resource):
                continue
            impacted_resources.add(resource.id)
            for group in world.groups:
                if group.original_flight_id!=resource.id:
                    continue
                if resource.status=="needs_diversion":
                    group.status="affected"
                    diverted_groups.add(group.id)
                elif resource.status=="weather_held" and group.origin==resource.destination:
                    group.status="affected"
                    diverted_groups.add(group.id)
                elif resource.status=="weather_held" and group.status=="scheduled":
                    group.status="affected"
                elif resource.available and group.origin==group.destination and not any(o.group_id==group.id for o in active_offers(world)):
                    group.status="scheduled"
    elif kind in ("airport_restored","airport_diversion_eligibility"):
        airport=airport_reference(world,target)
        if kind=="airport_restored":
            # Reopening the location does not silently reinstate cancelled flights.
            airport.available=True
        else:
            if not isinstance(value,bool):
                raise DomainError(422,"Diversion eligibility must be a boolean")
            airport.diversion_eligible=value
    elif kind in ("resource_unavailable","flight_cancelled","resource_capacity","resource_accessibility","resource_restored"):
        r=lookup(world.resources,target,"resource")
        if kind=="flight_cancelled" and r.kind!="flight":
            raise DomainError(422,"flight_cancelled requires a flight")
        if kind in ("resource_unavailable","flight_cancelled"):
            if r.kind=="flight" and r.departure<=world.sim_time<r.arrival and (r.available or r.status=="needs_diversion"):
                raise DomainError(409,"An airborne flight cannot be cancelled in this scenario; use a simulated diversion or airport closure")
            r.available=False
            r.status="cancelled" if r.kind=="flight" else "unavailable"
            impacted_resources.add(r.id)
            for g in world.groups:
                if g.original_flight_id==r.id and g.status=="scheduled":
                    g.status="waiting"
        elif kind=="resource_capacity":
            capacity=strict_number(value,0,10000,True)
            r.capacity=capacity
            # Preserve confirmations before temporary holds. A reduced capacity
            # does not invalidate a journey that still fits the shared resource.
            dependent=[o for o in active_offers(world) if any(l.resource_id==r.id for l in o.itinerary.legs)]
            remaining=capacity
            for offer in sorted(dependent,key=lambda o:(o.status!="confirmed",o.created_version,o.confirmed_at or "",o.id)):
                required=units(r,lookup(world.groups,offer.group_id,"group"))
                if required<=remaining:
                    remaining-=required
                else:
                    invalidate(world,offer,f"{r.id} capacity reduced; this whole group's allocation no longer fits.")
        elif kind=="resource_accessibility":
            if not isinstance(value,bool):
                raise DomainError(422,"Accessibility must be a boolean")
            r.accessible=value
            if not value:
                impacted_resources.add(r.id)
        else:
            if r.kind=="flight" and any((a.id==r.destination and not landing_allowed(a)) or (a.id==r.origin and not departure_allowed(a)) for a in world.airports):
                raise DomainError(409,"This flight still touches an unavailable airport and cannot be restored; choose an eligible diversion if it is airborne")
            r.available=True
            r.status="scheduled"
    elif kind=="airport_closed":
        airport=lookup(world.airports,target,"airport")
        airport.available=False
        for r in world.resources:
            if r.kind=="flight" and airport.id in (r.origin,r.destination):
                # An airport closing after takeoff cannot undo that departure.
                # Its airborne outbound flight remains subject to arrival closures.
                if r.origin==airport.id and r.destination!=airport.id and r.departure<=world.sim_time<r.arrival and (r.available or r.status=="needs_diversion"):
                    continue
                airborne=r.destination==airport.id and r.departure<=world.sim_time<r.arrival and r.available
                pending=r.status=="needs_diversion"
                r.available=False
                r.status="needs_diversion" if airborne or pending else "cancelled"
                impacted_resources.add(r.id)
                for g in world.groups:
                    if g.original_flight_id==r.id and (airborne or pending or g.status=="scheduled"):
                        g.status="affected"
                        if airborne or pending:
                            diverted_groups.add(g.id)
    elif kind=="flight_diverted":
        flight=lookup(world.resources,target,"flight")
        airport=lookup(world.airports,canonical_id(value) if isinstance(value,str) else value,"airport")
        if flight.kind!="flight" or not landing_allowed(airport) or not airport.diversion_eligible or airport.id==flight.origin:
            raise DomainError(422,"Airport is not eligible for this simulated diversion")
        if not flight.departure<=world.sim_time<flight.arrival or (not flight.available and flight.status!="needs_diversion"):
            raise DomainError(409,"Only an airborne flight or a pending airborne diversion can be diverted")
        if flight.destination==airport.id and flight.available and flight.status!="needs_diversion":
            audit(world,kind,f"{flight.id} already arrives at {airport.id}; existing passenger commitments are unchanged.",flight.id,{"value":airport.id,"source":"simulation","no_change":True})
            return
        flight.destination=airport.id
        flight.available=True
        flight.status="diverted"
        for g in world.groups:
            if g.original_flight_id==flight.id:
                for offer in active_offers(world):
                    if offer.group_id==g.id:
                        invalidate(world,offer,"Arrival airport changed in the scenario; recovery must be replanned.")
                g.origin=airport.id
                g.ready_at=max(world.sim_time,flight.arrival+30)
                g.status="scheduled" if g.origin==g.destination else "waiting"
        impacted_resources.add(flight.id)
    elif kind=="budget_changed":
        budget=strict_number(value,0,1000000)
        confirmed=sum(o.itinerary.total_cost for o in active_offers(world,True))
        if budget+0.005<confirmed:
            raise DomainError(409,"New budget is below already-confirmed commitments; confirmations are preserved",{"minimum_budget":confirmed})
        world.budget=budget
        if sum(o.itinerary.total_cost for o in active_offers(world))>budget+0.005:
            for offer in list(active_offers(world)):
                if offer.status=="held":
                    invalidate(world,offer,"Scenario budget changed; unaccepted offer needs reevaluation.")
    elif kind in ("group_deadline","group_accessibility","group_budget","group_destination","group_priority"):
        group=lookup(world.groups,target,"group")
        impacted_groups.add(group.id)
        if kind=="group_deadline":
            group.deadline=strict_number(value,world.sim_time,10080,True)
        elif kind=="group_budget":
            group.budget=strict_number(value,0,100000)
        elif kind=="group_destination":
            if not isinstance(value,str):
                raise DomainError(422,"Group destination must be an airport ID")
            group.destination=airport_reference(world,value).id
            if group.status=="scheduled" and group.destination!=group.origin:
                group.status="waiting"
        elif kind=="group_priority":
            group.priority=strict_number(value,1,10,True)
        else:
            if not isinstance(value,bool):
                raise DomainError(422,"Accessibility must be a boolean")
            group.accessible=value
    else:
        raise DomainError(422,f"Unsupported scenario operation: {kind}")
    for offer in list(active_offers(world)):
        resources={l.resource_id for l in offer.itinerary.legs}
        if offer.group_id in diverted_groups:
            invalidate(world,offer,"The original arrival is unavailable; its passengers require a new arrival and recovery plan.")
        elif resources & impacted_resources:
            # Availability, reduced capacity and accessibility are relevant dependencies.
            # Do not disturb a non-accessibility-needing party solely for that change.
            if kind=="resource_accessibility" and not lookup(world.groups,offer.group_id,"group").accessible:
                continue
            invalidate(world,offer,f"{target} changed ({kind}); its dependent itinerary requires recovery.")
        elif offer.group_id in impacted_groups:
            check=validate_allocations(world,[Allocation(group_id=offer.group_id,itinerary=offer.itinerary)],include_confirmed=False)
            if not check["valid"]:
                invalidate(world,offer,"Passenger constraints changed: "+"; ".join(check["errors"]))
    if kind=="group_destination" and group.origin==group.destination:
        group.status="scheduled"
    if kind=="resource_route":
        for group in world.groups:
            if group.id in impacted_groups and group.origin==group.destination:
                group.status="scheduled"
    audit(world,kind,f"Scenario event: {kind} · {target or 'global'}"+(f" → {value}" if value is not None else ""),target,{"value":value,"source":"simulation"})


def validated_candidates(world: World,candidates):
    valid=[]
    for plan in candidates:
        if not plan.validation["valid"]:
            continue
        if plan.scenario_changes:
            projected=world.model_copy(deep=True)
            try:
                for change in plan.scenario_changes:
                    apply_change(projected,ScenarioChange.model_validate(change))
            except (DomainError,ValidationError):
                continue
            if not validate_allocations(projected,plan.allocations)["valid"]:
                continue
        valid.append(plan)
    return valid


class RecoveryService:
    def __init__(self,settings: Settings,store: Store,ai=None):
        self.settings,self.store,self.ai=settings,store,ai
        self._planning_timeout_seconds=2*settings.request_timeout_seconds+30

    async def _run_claimed(self,namespace,request,operation):
        started=time.perf_counter()
        key=namespace+":"+request.idempotency_key
        claim=self.store.claim_request(key,request.model_dump(),self._planning_timeout_seconds+30)
        if "replay" in claim:
            return claim["replay"]
        try:
            # Only provider calls yield inside the operation. The atomic SQLite
            # apply stays synchronous, so cancellation cannot leave a detached
            # commit running after this claim is released.
            async with asyncio.timeout(self._planning_timeout_seconds):
                return await operation(request,started)
        except TimeoutError as exc:
            raise DomainError(504,"Planning exceeded the server deadline; no scenario changes were applied. Upstream usage may still be billable.") from exc
        finally:
            try:
                self.store.release_request(key,claim["owner"])
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Planning finished, but its request claim could not be released; its lease will expire")

    def event(self,request):
        payload=request.model_dump()
        return self.store.mutate("event:"+request.idempotency_key,payload,request.expected_version,
            lambda world:apply_change(world,ScenarioChange(type=request.type,target_id=request.target_id,value=request.value)))

    def _hold_plan(self,world,plan,mode,request,provider,explanation,candidates,latency,focus_id=None,timings=None,apply_started=None,candidate_counts=None):
        apply_started=apply_started if apply_started is not None else time.perf_counter()
        for change in plan.scenario_changes:
            apply_change(world,ScenarioChange.model_validate(change))
        check=validate_allocations(world,plan.allocations)
        if not check["valid"]:
            raise DomainError(409,"Candidate failed authoritative validation",check)
        for offer in active_offers(world):
            if offer.status=="held":
                offer.status="superseded"
                offer.reason="Replaced by a newer validated recovery plan."
                for r in world.reservations:
                    if r.offer_id==offer.id:
                        r.status="released"
        expires=(utcnow()+timedelta(seconds=self.settings.hold_ttl_seconds)).isoformat()
        resources={r.id:r for r in world.resources}
        groups={g.id:g for g in world.groups}
        for allocation in plan.allocations:
            group=groups[allocation.group_id]
            offer=Offer(id="of_"+secrets.token_hex(10),group_id=group.id,itinerary=allocation.itinerary,status="held",expires_at=expires,created_version=world.version+1)
            world.offers.append(offer)
            group.status="offered"
            for leg in allocation.itinerary.legs:
                world.reservations.append(Reservation(id="rs_"+secrets.token_hex(10),offer_id=offer.id,group_id=group.id,resource_id=leg.resource_id,quantity=units(resources[leg.resource_id],group),status="held",expires_at=expires))
        for exception in plan.exceptions:
            groups[exception["group_id"]].status="exception"
        record=Decision(id="dec_"+secrets.token_hex(10),timestamp=utcnow().isoformat(),source_version=world.version,applied_version=world.version+1,
            mode=mode,provider=provider,request=request,explanation=explanation,selected_plan_id=plan.id,candidates=candidates,latency_ms=latency,focus_id=focus_id,
            timings_ms={**(timings or {}),"apply_ms":round((time.perf_counter()-apply_started)*1000,2)},candidate_counts=candidate_counts or {"computed":len(candidates),"validated":len(candidates),"shortlisted":len(candidates),"submitted":len(candidates) if mode=="ai" else 0})
        world.decisions.append(record)
        audit(world,"plan_applied",f"{mode.upper()}: {plan.recovered_passengers} passengers offered full journeys; €{plan.total_cost:.2f} held atomically.",plan.id,{"decision_id":record.id,"validation":check,"optimality":plan.optimality,"candidate_counts":record.candidate_counts})
        return {"decision":record.model_dump(mode="json")}

    async def replan(self,request):
        return await self._run_claimed("replan",request,self._replan)

    async def _replan(self,request,started):
        payload=request.model_dump()
        key="replan:"+request.idempotency_key
        replay=self.store.replay(key,payload)
        if replay:
            return replay
        world=self.store.read()
        if world.version!=request.expected_version:
            raise DomainError(409,"World changed; refresh before planning",{"current_version":world.version})
        solver_started=time.perf_counter()
        candidates=[build_candidate(world,s) for s in default_strategies(world) or [Strategy()]]
        computed_count=len(candidates)
        candidates=validated_candidates(world,candidates)
        validated_count=len(candidates)
        candidates=shortlist_candidates(candidates)
        candidate_counts={"computed":computed_count,"validated":validated_count,"shortlisted":len(candidates),"submitted":len(candidates) if request.mode=="ai" else 0}
        timings={"interpretation_ms":0.0,"solver_ms":round((time.perf_counter()-solver_started)*1000,2),"decisions_ms":0.0}
        if not candidates:
            raise DomainError(409,"No candidate passed exact validation")
        if request.mode=="ai":
            if not self.ai:
                raise DomainError(503,"AI is not configured; select deterministic solver mode explicitly")
            decisions_started=time.perf_counter()
            selection=await self.ai.choose(world.model_dump(mode="json"),[p.model_dump(mode="json") for p in candidates],{"text":"Replan all affected groups preserving unaffected confirmations"})
            timings["decisions_ms"]=round((time.perf_counter()-decisions_started)*1000,2)
            plan=next((p for p in candidates if p.id==selection["plan_id"]),None)
            if plan is None:
                raise DomainError(502,"AI selected a plan outside the validated candidate set")
            provider=selection.get("provider","OpenAI Decisions")
            explanation=selection["explanation"]
        else:
            plan=min(candidates,key=lambda p:(-p.recovered_passengers,p.total_cost+p.average_arrival*.1,p.id))
            provider="deterministic bounded allocator"
            explanation=f"Deterministic mode compared {validated_count} validated allocations. Selected {plan.recovered_passengers} recovered passengers for €{plan.total_cost:.2f}; {sum(e['passengers'] for e in plan.exceptions)} remain exceptions. Full journeys and shared inventory were checked. Global optimality is not proven."
        if validated_count>len(candidates):
            explanation+=f" Evaluated all {validated_count} valid airport/objective candidates; shortlisted {len(candidates)} for selection, retaining global objective winners and diverse airport alternatives."
        latency=round((time.perf_counter()-started)*1000,2)
        return self.store.mutate(key,payload,world.version,lambda current:self._hold_plan(current,plan,request.mode,"Replan recovery",provider,explanation,candidates,latency,timings=timings,candidate_counts=candidate_counts),request_started=started)

    async def command(self,request):
        return await self._run_claimed("command",request,self._command)

    async def _command(self,request,started):
        payload=request.model_dump()
        key="command:"+request.idempotency_key
        replay=self.store.replay(key,payload)
        if replay:
            return replay
        if not self.ai:
            raise DomainError(503,"AI is not configured; structured scenario controls and deterministic solver remain available")
        world=self.store.read()
        if world.version!=request.expected_version:
            raise DomainError(409,"World changed; refresh before sending a command",{"current_version":world.version})
        interpretation_started=time.perf_counter()
        if request.context:
            interpreted=await self.ai.interpret(request.text,world.model_dump(mode="json"),request.context)
        else:
            interpreted=await self.ai.interpret(request.text,world.model_dump(mode="json"))
        timings={"interpretation_ms":round((time.perf_counter()-interpretation_started)*1000,2)}
        if not interpreted.get("events") and not interpreted.get("strategies"):
            return self.store.remember(key,payload,world.version,{"explanation":interpreted.get("explanation","No operational change requested."),"focus_id":interpreted.get("focus_id"),"applied":False,"mode":"ai"})
        if len(interpreted.get("events",[]))>MAX_COMMAND_EVENTS:
            raise DomainError(422,f"At most {MAX_COMMAND_EVENTS} operational changes are allowed in one command")
        try:
            changes=[ScenarioChange.model_validate(e) for e in interpreted.get("events",[])]
        except ValidationError as exc:
            raise DomainError(422,"AI returned an invalid scenario operation; no changes were applied") from exc
        reset_positions=[i for i,event in enumerate(changes) if event.type=="scenario_reset"]
        if reset_positions and reset_positions != [0]:
            raise DomainError(422,"A scenario reset must be the first and only reset in a command; no changes were applied")
        projected=world.model_copy(deep=True)
        for event in changes:
            apply_change(projected,event)
        # Resetting a scene alone has no recovery decision to make.
        reset_only=len(changes)==1 and bool(reset_positions)
        if reset_only or (not projected.groups and not any(r.status=="needs_diversion" for r in projected.resources)):
            def commit_scene(current):
                for event in changes:
                    apply_change(current,event)
                return {"explanation":interpreted.get("explanation","Updated the synthetic scene."),"focus_id":interpreted.get("focus_id"),"applied":True,"mode":"ai"}
            return self.store.mutate(key,payload,world.version,commit_scene,request_started=started)
        strategies=[Strategy.model_validate(s) for s in interpreted.get("strategies",[])][:6]
        # Pending diversions must compare every eligible airport; never silently fix a choice.
        if any(r.status=="needs_diversion" for r in projected.resources):
            strategies=default_strategies(projected)
        if not strategies:
            strategies=default_strategies(projected) or [Strategy()]
        for idx,s in enumerate(strategies):
            s.id=f"{s.id}-{idx}"
        solver_started=time.perf_counter()
        candidates=[build_candidate(projected,s) for s in strategies]
        computed_count=len(candidates)
        candidates=validated_candidates(projected,candidates)
        validated_count=len(candidates)
        candidates=shortlist_candidates(candidates)
        candidate_counts={"computed":computed_count,"validated":validated_count,"shortlisted":len(candidates),"submitted":len(candidates)}
        timings["solver_ms"]=round((time.perf_counter()-solver_started)*1000,2)
        if not candidates:
            raise DomainError(409,"No candidate passed exact validation")
        decisions_started=time.perf_counter()
        selection=await self.ai.choose(projected.model_dump(mode="json"),[p.model_dump(mode="json") for p in candidates],{"text":request.text,"interpretation":interpreted})
        timings["decisions_ms"]=round((time.perf_counter()-decisions_started)*1000,2)
        plan=next((p for p in candidates if p.id==selection["plan_id"]),None)
        if plan is None:
            raise DomainError(502,"AI chose an unvalidated plan")
        def commit(current):
            apply_started=time.perf_counter()
            for event in changes:
                apply_change(current,event)
            explanation=selection["explanation"]
            if changes and interpreted.get("explanation"):
                explanation=interpreted["explanation"]+"\n\n"+explanation
            if validated_count>len(candidates):
                explanation+=f" Evaluated all {validated_count} valid airport/objective candidates; shortlisted {len(candidates)} for the decision model, retaining global objective winners and diverse airport alternatives."
            return self._hold_plan(current,plan,"ai",request.text,selection.get("provider","OpenAI"),explanation,candidates,round((time.perf_counter()-started)*1000,2),interpreted.get("focus_id"),timings=timings,apply_started=apply_started,candidate_counts=candidate_counts)
        return self.store.mutate(key,payload,world.version,commit,request_started=started)

    def accept(self,offer_id,request):
        payload={**request.model_dump(),"offer_id":offer_id}
        # Include the token identity: unrelated passengers cannot replay each other's response.
        key="accept:"+Store.fingerprint(request.token)+":"+request.idempotency_key
        def authorize(world):
            offer=lookup(world.offers,offer_id,"offer")
            group=lookup(world.groups,offer.group_id,"group")
            if not secrets.compare_digest(group.token.encode(),request.token.encode()):
                raise DomainError(403,"This passenger token cannot accept that offer")
        def confirm(world):
            offer=lookup(world.offers,offer_id,"offer")
            group=lookup(world.groups,offer.group_id,"group")
            if offer.status=="confirmed":
                return {"offer":offer.model_dump(mode="json"),"already_confirmed":True}
            if offer.status!="held":
                raise DomainError(409,"Offer is no longer available",{"status":offer.status})
            held=[Allocation(group_id=o.group_id,itinerary=o.itinerary) for o in active_offers(world) if o.status=="held"]
            check=validate_allocations(world,held)
            if not check["valid"]:
                raise DomainError(409,"Inventory changed; offer must be replanned",check)
            reservations=[r for r in world.reservations if r.offer_id==offer.id and r.status=="held"]
            resources={r.id:r for r in world.resources}
            expected={(leg.resource_id,units(resources[leg.resource_id],group)) for leg in offer.itinerary.legs}
            actual={(r.resource_id,r.quantity) for r in reservations}
            if len(reservations)!=len(offer.itinerary.legs) or actual!=expected:
                raise DomainError(409,"Incomplete reservation; no segments were confirmed")
            for r in reservations:
                r.status="confirmed"
            offer.status="confirmed"
            offer.confirmed_at=utcnow().isoformat()
            group.status="confirmed"
            audit(world,"offer_confirmed",f"{group.name}: every segment confirmed atomically in the simulator.",offer.id,{"group_id":group.id})
            return {"offer":offer.model_dump(mode="json")}
        result=self.store.mutate(key,payload,None,confirm,authorize=authorize)
        # Never expose operator world or other passenger records in this response.
        return {"offer":result["offer"],"version":result["world"]["version"],"replayed":result["replayed"],"simulated":True}

    def passenger(self,token):
        world=self.store.read()
        group=next((g for g in world.groups if secrets.compare_digest(g.token.encode(),token.encode())),None)
        if not group:
            raise DomainError(404,"Passenger link not found")
        offers=[o for o in world.offers if o.group_id==group.id]
        active=next((o for o in reversed(offers) if o.status in ("held","confirmed")),None)
        return {"version":world.version,"epoch":world.epoch,"sim_time":world.sim_time,"group":group.model_dump(exclude={"token"}),
            "offer":active.model_dump(mode="json") if active else None,"last_offer":offers[-1].model_dump(mode="json") if offers else None,
            "airports":[a.model_dump() for a in world.airports],"resources":[r.model_dump() for r in world.resources if active and r.id in {l.resource_id for l in active.itinerary.legs}],"simulated":True,"currency":world.currency}

    def constraints(self,token,request):
        payload={**request.model_dump(),"token":token}
        if all(getattr(request,k) is None for k in ("deadline","accessible","budget")):
            raise DomainError(422,"Specify at least one changed constraint")
        def authorize(world):
            if not any(secrets.compare_digest(g.token.encode(),token.encode()) for g in world.groups):
                raise DomainError(404,"Passenger link not found")
        def update(world):
            group=next((g for g in world.groups if secrets.compare_digest(g.token.encode(),token.encode())),None)
            if not group:
                raise DomainError(404,"Passenger link not found")
            for key,event_type in [("deadline","group_deadline"),("accessible","group_accessibility"),("budget","group_budget")]:
                value=getattr(request,key)
                if value is not None:
                    apply_change(world,ScenarioChange(type=event_type,target_id=group.id,value=value))
            return {}
        result=self.store.mutate("constraints:"+Store.fingerprint(token)+":"+request.idempotency_key,payload,None,update,authorize=authorize)
        return {"version":result["world"]["version"],"replayed":result["replayed"],"passenger":self.passenger(token)}

    def reset(self,request):
        def reset_world(world):
            reset_scenario(world,"seed")
        return self.store.mutate("reset:"+request.idempotency_key,request.model_dump(),request.expected_version,reset_world)
