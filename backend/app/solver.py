"""Bounded deterministic allocation. Exact validation, no claim of global optimality.

Resources are specific dated services (hotel capacity is rooms, transport capacity
is seats). Every candidate completes the journey; an overnight stay alone is never
counted as recovery. The allocator compares independent orderings over all groups.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from datetime import datetime, timezone
from .models import Allocation, Itinerary, Leg, PassengerGroup, Plan, Resource, Strategy, World
from .airport_catalog import lookup_airport
from .airport_constraints import flight_global_operations_allowed, resource_blocked_by_case
from .weather import departure_allowed, landing_allowed, passenger_arrival_pending
from .initial_position import valid_initial_position
from .airport_arrival import valid_airport_arrival
from .stay_requirements import StayRequirementError, itinerary_stay_errors, read_stay_requirement
from .pricing import APL_SOURCE, app_receipt_deadline, aware_timestamp, cost_summary, matching_cost_summary, valid_apl_resource_price
from .airport_cab_evidence import (
    SOURCE as AIRPORT_CAB_SOURCE, CONTEXT_KEY as AIRPORT_CAB_CONTEXT,
    QUOTE_KEY as AIRPORT_CAB_QUOTE, OPTION_KEY as AIRPORT_CAB_OPTION,
    airport_cab_current, airport_cab_fleet_errors, valid_airport_cab_resource,
)


def units(resource: Resource, group: PassengerGroup) -> int:
    if resource.quote_rooms is not None:
        return resource.quote_rooms
    return math.ceil(group.size/resource.room_capacity) if resource.kind == "hotel" else group.size


def resource_cost(resource: Resource, group: PassengerGroup) -> float:
    return round(resource.budget_total_cost if resource.budget_total_cost is not None else resource.quote_total_cost if resource.quote_total_cost is not None else resource.cost_per_person*units(resource,group),2)


def live_resource_current(resource: Resource) -> bool:
    """Local quote freshness is not a supplier hold or stock guarantee."""
    if resource.source == "synthetic":
        return False
    try:
        now=datetime.now(timezone.utc)
        if resource.source == AIRPORT_CAB_SOURCE:
            return airport_cab_current(resource, now=now)
        if resource.source == APL_SOURCE:
            deadline = app_receipt_deadline(resource)
            if deadline is None or resource.valid_until is not None or resource.provenance.get("quoted_at") is not None:
                return False
            return aware_timestamp(resource.quote_freshness.received_at) <= now <= deadline
        if resource.valid_until:
            until=datetime.fromisoformat(resource.valid_until.replace("Z","+00:00"))
            return until.tzinfo is not None and until>now
        if resource.requires_revalidation:
            at=datetime.fromisoformat(str(resource.provenance.get("quoted_at","")).replace("Z","+00:00"))
            return at.tzinfo is not None and 0 <= (now-at).total_seconds() <= 120
    except (ValueError,TypeError,AttributeError):
        pass
    return False


def vehicle_conflict(a: Resource,b: Resource) -> bool:
    if not a.vehicle_id or a.vehicle_id!=b.vehicle_id or a.id==b.id:
        return False
    first,second=sorted([a,b],key=lambda r:r.departure)
    # No implicit teleportation/deadhead: a later service must begin where the
    # previous one ends, with ten minutes for unloading/turnaround.
    return first.arrival+10>second.departure or first.destination!=second.origin


def vehicle_schedule_errors(resources: list[Resource]) -> list[str]:
    """Adjacent dated services determine continuity, including return trips."""
    vehicles={r.vehicle_id for r in resources if r.vehicle_id}
    errors=[]
    for vehicle in sorted(vehicles):
        services=sorted({r.id:r for r in resources if r.vehicle_id==vehicle}.values(),key=lambda r:(r.departure,r.id))
        for first,second in zip(services,services[1:]):
            if vehicle_conflict(first,second):
                errors.append(f"{vehicle}: incompatible vehicle services {first.id} and {second.id}")
    return errors


def active_offers(world: World, confirmed_only=False):
    statuses = {"confirmed"} if confirmed_only else {"confirmed", "held", "proposed", "accepted"}
    return [offer for offer in world.offers if offer.status in statuses]


def inventory_used(world: World, confirmed_only=False) -> Counter:
    ids = {o.id for o in active_offers(world, confirmed_only)}
    return Counter({r.id: sum(x.quantity for x in world.reservations if x.resource_id==r.id and x.offer_id in ids and x.status in ("held", "proposed", "accepted", "confirmed")) for r in world.resources})


def project_diversion(world: World, airport: str | None) -> tuple[World, list[dict]]:
    projected = world.model_copy(deep=True)
    changes = []
    if world.mode=="live":
        return projected,changes
    pending = [r for r in projected.resources if r.kind=="flight" and r.status=="needs_diversion"]
    if not pending:
        return projected, changes
    candidate = next((a for a in world.airports if a.id==airport and landing_allowed(a) and a.diversion_eligible),None)
    if not candidate:
        return projected, changes
    for flight in pending:
        flight.destination = candidate.id
        flight.available = True
        flight.status = "diverted"
        for group in projected.groups:
            if group.original_flight_id == flight.id and group.status not in ("confirmed",):
                group.origin = candidate.id
                group.ready_at = max(projected.sim_time, flight.arrival+30)
                group.status = "scheduled" if group.origin==group.destination else "waiting"
        changes.append({"type":"flight_diverted", "target_id":flight.id, "value":candidate.id})
    return projected, changes


def resource_blocked_by_incident(world: World, resource: Resource, group: PassengerGroup) -> bool:
    return resource_blocked_by_case(world,resource,group)


def resource_feasible(world: World, r: Resource, g: PassengerGroup, current: str, minute: int, used_ids: set[str]) -> bool:
    if world.mode=="live":
        if r.eligible_group_ids and g.id not in r.eligible_group_ids:
            return False
        cab_evidence = any(key in r.provenance for key in (AIRPORT_CAB_CONTEXT, AIRPORT_CAB_QUOTE, AIRPORT_CAB_OPTION))
        if r.source == AIRPORT_CAB_SOURCE or cab_evidence:
            if (used_ids or current != g.origin
                    or not valid_airport_cab_resource(world, r, g, now=datetime.now(timezone.utc))):
                return False
        elif r.source == APL_SOURCE or r.price_evidence is not None or r.quote_freshness is not None:
            if world.currency != "EUR" or not valid_apl_resource_price(r, g, require_current=g.status != "confirmed"):
                return False
        if g.status!="confirmed" and not live_resource_current(r):
            return False
        if g.status=="confirmed" and r.source=="synthetic":
            return False
        if g.status!="confirmed" and r.kind=="hotel" and r.source=="Nuitee / LiteAPI":
            pricing=r.provenance.get("pricing",{})
            mandatory=pricing.get("mandatory_total") if isinstance(pricing,dict) else None
            if r.budget_total_cost is None or not isinstance(pricing,dict) or pricing.get("status")!="complete" or not isinstance(mandatory,dict) or mandatory.get("currency")!="EUR":
                return False
            location=r.provenance.get("property_location")
            node=next((item for item in world.airports if item.id==r.origin and item.kind=="hotel"),None)
            if not isinstance(location,dict) or node is None or location.get("node_id")!=r.origin or r.origin!=r.destination or location.get("latitude")!=node.lat or location.get("longitude")!=node.lon:
                return False
        if resource_blocked_by_incident(world,r,g):
            return False
    receipt = r.provenance.get("apl_quote", {})
    request = receipt.get("request", {}) if isinstance(receipt, dict) else {}
    returned_to_airport = (r.airport_arrival_assumption is not None or r.provenance.get("direction") == "hotel_to_airport"
        or (r.source == APL_SOURCE and isinstance(request, dict) and request.get("expected_direction") == "to_hub"))
    if returned_to_airport and not valid_airport_arrival(world, r, g):
        return False
    declared_start = r.initial_position_assumption is not None
    if declared_start and (used_ids or current != g.origin or not valid_initial_position(world, r, g)):
        return False
    if not r.available or r.id in used_ids or (r.origin != current and not declared_start) or r.departure < minute:
        return False
    if r.arrival <= r.departure or r.arrival > g.deadline or (g.accessible and not r.accessible):
        return False
    if r.kind == "flight":
        if world.mode == "live":
            if not flight_global_operations_allowed(world,r):
                return False
        else:
            airports = {a.id:a for a in world.airports}
            if not departure_allowed(airports[r.origin]) or not landing_allowed(airports[r.destination]):
                return False
    if r.capacity < units(r,g):
        return False
    return True


def connection_minutes(previous: Resource | None, resource: Resource) -> int | None:
    """A declared airport allowance replaces the generic connection default."""
    assumption = previous.airport_arrival_assumption if previous is not None else None
    if assumption is not None:
        if resource.kind != "flight" or resource.id != assumption.onward_resource_id or resource.origin != assumption.airport_id:
            return None
        return assumption.processing_minutes
    return 30 if resource.kind == "flight" else 10 if previous is not None else 0


def itinerary_options(world: World, group: PassengerGroup, limit=64) -> list[Itinerary]:
    read_stay_requirement(group)  # Invalid initial inputs are not a silent no-route result.
    if passenger_arrival_pending(world,group):
        return []
    options = []
    expansions = 0
    max_legs = 6 if world.mode=="live" else 4
    resources = {resource.id: resource for resource in world.resources}
    ready = max(world.sim_time, group.ready_at)
    def visit(location, minute, legs, seen, cost):
        nonlocal expansions
        expansions += 1
        if expansions>4000:
            return
        previous = resources[legs[-1].resource_id] if legs else None
        if location==group.destination and legs and previous.airport_arrival_assumption is None:
            if any(leg.kind in ("bus","flight") for leg in legs) and not itinerary_stay_errors(world,group,legs):
                options.append(Itinerary(legs=legs,arrival=legs[-1].arrival,total_cost=round(cost,2),cost_evidence=cost_summary(legs)))
            return
        if len(legs)>=max_legs or len(options)>=limit:
            return
        for r in sorted(world.resources,key=lambda x:(x.departure,x.id)):
            connection = connection_minutes(previous, r)
            if connection is None:
                continue
            if not resource_feasible(world,r,group,location,minute+connection,{x.resource_id for x in legs}):
                continue
            if r.kind!="hotel" and r.destination in seen and not any(leg.kind=="hotel" for leg in legs):
                continue
            # A next-day journey requires a dated hotel segment before departure.
            if world.mode!="live" and r.departure//1440 > minute//1440:
                continue
            price = resource_cost(r,group)
            if cost+price > group.budget+1e-6:
                continue
            leg = Leg(resource_id=r.id,kind=r.kind,origin=r.origin,destination=r.destination,departure=r.departure,arrival=r.arrival,cost=price,
                transport_mode=r.transport_mode, price_evidence=r.price_evidence.model_copy(deep=True) if r.price_evidence else None,
                initial_position_assumption=r.initial_position_assumption.model_copy(deep=True) if r.initial_position_assumption else None,
                airport_arrival_assumption=r.airport_arrival_assumption.model_copy(deep=True) if r.airport_arrival_assumption else None)
            visit(r.destination,r.arrival,legs+[leg],seen if r.kind=="hotel" else seen|{r.destination},cost+price)
    visit(group.origin,ready,[],{group.origin},0)
    return options


def fixed_offers(world: World, scope_group_ids: set[str] | None = None):
    return [offer for offer in active_offers(world) if offer.status == "confirmed" or (scope_group_ids is not None and offer.group_id not in scope_group_ids)]


def validate_allocations(world: World, allocations: list[Allocation], include_confirmed=True, scope_group_ids: set[str] | None = None) -> dict:
    """Independent exact checker: never trusts totals or leg fields in a plan."""
    errors: list[str] = []
    resources = {r.id:r for r in world.resources}
    groups = {g.id:g for g in world.groups}
    all_allocations = list(allocations)
    if scope_group_ids is not None and any(item.group_id not in scope_group_ids for item in allocations):
        errors.append("An allocation is outside this recovery job's passenger scope")
    if include_confirmed:
        all_allocations += [Allocation(group_id=o.group_id,itinerary=o.itinerary) for o in fixed_offers(world,scope_group_ids)]
    used = Counter()
    seen_groups = set()
    occupied_resources = set()
    total_cost = 0
    for alloc in all_allocations:
        g = groups.get(alloc.group_id)
        if not g:
            errors.append(f"Unknown group {alloc.group_id}"); continue
        if passenger_arrival_pending(world,g):
            errors.append(f"{g.id}: original arrival remains unavailable; passengers cannot start an onward journey")
        if g.id in seen_groups:
            errors.append(f"Duplicate allocation for {g.id}")
        seen_groups.add(g.id)
        errors.extend(itinerary_stay_errors(world,g,alloc.itinerary.legs))
        location, minute = g.origin,g.ready_at if world.mode=="live" and g.status=="confirmed" else max(world.sim_time,g.ready_at)
        seen_legs, subtotal = set(),0
        if not alloc.itinerary.legs:
            errors.append(f"{g.id}: empty journey")
        elif not any(leg.kind in ("flight","bus") for leg in alloc.itinerary.legs):
            errors.append(f"{g.id}: accommodation alone is not a recovered journey")
        previous = None
        for leg in alloc.itinerary.legs:
            r = resources.get(leg.resource_id)
            if not r:
                errors.append(f"{g.id}: unknown resource {leg.resource_id}"); continue
            connection = connection_minutes(previous, r)
            if connection is None:
                errors.append(f"{g.id}: airport return must connect to its exact onward flight")
                connection = 0
            if not resource_feasible(world,r,g,location,minute+connection,seen_legs):
                errors.append(f"{g.id}: unavailable, inaccessible or discontinuous {r.id}")
            if world.mode!="live" and r.departure//1440 > minute//1440:
                errors.append(f"{g.id}: missing overnight accommodation")
            expected_price = resource_cost(r,g)
            if (leg.origin,leg.destination,leg.departure,leg.arrival,leg.kind)!=(r.origin,r.destination,r.departure,r.arrival,r.kind) or not math.isfinite(leg.cost) or abs(leg.cost-expected_price)>0.005:
                errors.append(f"{g.id}: tampered leg {r.id}")
            if leg.price_evidence != r.price_evidence or leg.transport_mode != r.transport_mode or leg.initial_position_assumption != r.initial_position_assumption or leg.airport_arrival_assumption != r.airport_arrival_assumption:
                errors.append(f"{g.id}: missing or changed source/price evidence for {r.id}")
            if r.price_evidence is not None and leg.cost != expected_price:
                errors.append(f"{g.id}: inconsistent EUR estimate for {r.id}")
            used[r.id] += units(r,g)
            occupied_resources.add(r.id)
            subtotal += expected_price
            location,minute = r.destination,r.arrival
            seen_legs.add(r.id)
            previous = r
        if previous is not None and previous.airport_arrival_assumption is not None:
            errors.append(f"{g.id}: airport return has no bound onward flight")
        if location != g.destination:
            errors.append(f"{g.id}: incomplete destination journey")
        if minute > g.deadline:
            errors.append(f"{g.id}: arrival deadline exceeded")
        if subtotal > g.budget+0.005:
            errors.append(f"{g.id}: group budget exceeded")
        if not math.isfinite(alloc.itinerary.total_cost) or abs(alloc.itinerary.total_cost-subtotal)>0.005 or alloc.itinerary.arrival != minute:
            errors.append(f"{g.id}: inaccurate itinerary totals")
        if not matching_cost_summary(alloc.itinerary.legs, alloc.itinerary.cost_evidence, alloc.itinerary.total_cost):
            errors.append(f"{g.id}: missing or changed itinerary cost evidence")
        total_cost += subtotal
    for rid,count in used.items():
        if count > resources[rid].capacity:
            errors.append(f"{rid}: capacity exceeded ({count}/{resources[rid].capacity})")
    errors.extend(vehicle_schedule_errors([resources[i] for i in occupied_resources]))
    errors.extend(airport_cab_fleet_errors([resources[i] for i in occupied_resources]))
    if total_cost > world.budget+0.005:
        errors.append("Scenario budget exceeded")
    return {"valid":not errors,"errors":errors,"checks":["family_integrity","shared_capacities","temporal_continuity","full_destination","accessibility","deadlines","group_budgets","scenario_budget","hotel_room_occupancy","requested_hotel_stay","vehicle_schedule"],"total_committed_cost":round(total_cost,2)}


def validate_plan(world: World, plan: Plan, *, scope_group_ids: set[str] | None = None) -> dict:
    """Recheck allocation facts and the candidate's own monetary summary."""
    result = validate_allocations(world, plan.allocations, scope_group_ids=scope_group_ids)
    groups = {group.id: group for group in world.groups}
    for exception in plan.exceptions:
        group = groups.get(exception.get("group_id"))
        if group is not None:
            try:
                read_stay_requirement(group)
            except StayRequirementError as exc:
                result["errors"].append(f"{group.id}: {exc}")
    legs = [leg for allocation in plan.allocations for leg in allocation.itinerary.legs]
    if not matching_cost_summary(legs, plan.cost_evidence, plan.total_cost):
        result["errors"].append("Missing or changed plan cost evidence")
    total = round(sum(item.itinerary.total_cost for item in plan.allocations), 2)
    if not math.isfinite(plan.total_cost) or abs(plan.total_cost - total) > 0.005:
        result["errors"].append("Inaccurate plan total")
    result["valid"] = not result["errors"]
    return result


def build_candidate(world: World, strategy: Strategy, scope_group_ids: set[str] | None = None) -> Plan:
    started = time.perf_counter()
    projected,changes = project_diversion(world,strategy.preferred_airport)
    groups = [g for g in projected.groups if g.status not in ("confirmed","scheduled") and (scope_group_ids is None or g.id in scope_group_ids)]
    options, invalid_stays = {}, {}
    for g in groups:
        try:
            options[g.id] = itinerary_options(projected,g)
        except StayRequirementError as exc:
            options[g.id], invalid_stays[g.id] = [], str(exc)
    resources = {r.id:r for r in projected.resources}
    fixed = fixed_offers(projected,scope_group_ids)
    fixed_ids = {offer.id for offer in fixed}
    # The fleet uncertainty policy follows fixed itinerary legs even when a
    # legacy/local reservation row is absent. It is not source stock of one.
    fixed_cab_ids = {leg.resource_id for offer in fixed for leg in offer.itinerary.legs
        if leg.resource_id in resources and resources[leg.resource_id].source == AIRPORT_CAB_SOURCE}
    initial_used = Counter({r.id: sum(item.quantity for item in projected.reservations if item.resource_id==r.id and item.offer_id in fixed_ids and item.status in ("held","proposed","accepted","confirmed")) for r in projected.resources})
    initial_cost = sum(o.itinerary.total_cost for o in fixed)
    weights={k:max(0,min(100,float(strategy.weights.get(k,1)))) for k in ("arrival","cost","stability")}
    old_routes={o.group_id:tuple(l.resource_id for l in o.itinerary.legs) for o in active_offers(projected) if o.status=="held"}
    def route_changed(group_id,itin):
        return int(group_id in old_routes and old_routes[group_id]!=tuple(l.resource_id for l in itin.legs))
    def score_itinerary(itin,group_id):
        transfers = len([l for l in itin.legs if l.kind!="hotel"])-1
        if strategy.objective=="lowest_cost":
            return (itin.total_cost,itin.arrival,transfers)
        if strategy.objective=="fastest":
            return (itin.arrival,itin.total_cost,transfers)
        return (weights["arrival"]*itin.arrival/10+weights["cost"]*itin.total_cost+weights["stability"]*(transfers*12+route_changed(group_id,itin)*50),itin.arrival)
    for group_id,opts in options.items():
        opts.sort(key=lambda itin:score_itinerary(itin,group_id))
    orderings = [
        sorted(groups,key=lambda g:(len(options[g.id]),-g.priority,-g.size,g.id)),
        sorted(groups,key=lambda g:(g.deadline,-g.size,len(options[g.id]),g.id)),
        sorted(groups,key=lambda g:(-g.size,-g.priority,len(options[g.id]),g.id)),
        sorted(groups,key=lambda g:(min((i.total_cost/g.size for i in options[g.id]),default=float("inf")),-g.size,g.id)),
    ]
    best,best_score = [],None
    for ordered in orderings:
        used = initial_used.copy()
        cost = initial_cost
        allocations = []
        occupied = {rid for rid,n in used.items() if n}
        for g in ordered:
            for itin in options[g.id]:
                if cost+itin.total_cost > projected.budget+0.005:
                    continue
                needs = Counter({leg.resource_id:units(resources[leg.resource_id],g) for leg in itin.legs})
                if any(used[rid]+q>resources[rid].capacity for rid,q in needs.items()):
                    continue
                selected_resources = [resources[rid] for rid in occupied|set(needs)]
                if vehicle_schedule_errors(selected_resources) or airport_cab_fleet_errors(
                        [resources[rid] for rid in occupied|set(needs)|fixed_cab_ids]):
                    continue
                allocations.append(Allocation(group_id=g.id,itinerary=itin))
                used.update(needs)
                occupied.update(needs)
                cost += itin.total_cost
                break
        sizes = {g.id:g.size for g in groups}
        pax = sum(sizes[a.group_id] for a in allocations)
        weighted_arrival = sum(a.itinerary.arrival*sizes[a.group_id] for a in allocations)
        priority = sum(next(g.priority*g.size for g in groups if g.id==a.group_id) for a in allocations)
        # Recovery count is always first; an objective never improves by dropping passengers.
        if strategy.objective=="lowest_cost":
            tie=(-cost,-weighted_arrival)
        elif strategy.objective=="fastest":
            tie=(-weighted_arrival,-cost)
        else:
            changes_count=sum(route_changed(a.group_id,a.itinerary) for a in allocations)
            weighted_cost=weights["cost"]*cost+weights["arrival"]*weighted_arrival/60+weights["stability"]*changes_count*50
            tie=(-weighted_cost,-weighted_arrival)
        score = (pax,priority,*tie)
        if best_score is None or score>best_score:
            best,best_score = allocations,score
    recovered = {a.group_id for a in best}
    exception = []
    for g in groups:
        if g.id not in recovered:
            reason = "No complete itinerary meets this group's accessibility, deadline, budget and transfer constraints." if not options[g.id] else "Feasible individual routes exist, but shared seats, rooms, vehicle schedules or remaining scenario budget cannot accommodate this indivisible group in this bounded allocation."
            if g.id in invalid_stays:
                exception.append({"group_id":g.id,"passengers":g.size,"reason":invalid_stays[g.id],"code":StayRequirementError.code})
            else:
                if not options[g.id] and read_stay_requirement(g) is not None:
                    reason = "No complete sourced itinerary includes the requested hotel interval, room count and rest while meeting the group's other constraints."
                elif options[g.id] and all(any(resources[leg.resource_id].source == AIRPORT_CAB_SOURCE for leg in option.legs) for option in options[g.id]):
                    committed_ids = fixed_cab_ids | {leg.resource_id for allocation in best for leg in allocation.itinerary.legs}
                    if any(resources[rid].source == AIRPORT_CAB_SOURCE for rid in committed_ids):
                        reason = "The application permits only one Bucharest.CAB category quote across the combined plan and fixed allocations because multiple-vehicle availability is unverified. Other group and budget constraints still apply."
                exception.append({"group_id":g.id,"passengers":g.size,"reason":reason})
    pax = sum(g.size for g in groups if g.id in recovered)
    arrival = sum(a.itinerary.arrival*next(g.size for g in groups if g.id==a.group_id) for a in best)/pax if pax else 0
    plan = Plan(id=f"plan-{strategy.id}",strategy=strategy,allocations=best,exceptions=exception,recovered_passengers=pax,
        total_cost=round(sum(a.itinerary.total_cost for a in best),2),average_arrival=round(arrival,2),scenario_changes=changes,
        cost_evidence=cost_summary([leg for allocation in best for leg in allocation.itinerary.legs]),
        validation={},latency_ms=round((time.perf_counter()-started)*1000,2))
    plan.validation = validate_plan(projected, plan, scope_group_ids=scope_group_ids)
    return plan


def default_strategies(world: World) -> list[Strategy]:
    base = [("balanced","Balanced recovery"),("fastest","Earliest arrival"),("lowest_cost","Lowest cost")]
    pending = any(r.status=="needs_diversion" for r in world.resources)
    airports = [a.id for a in world.airports if landing_allowed(a) and a.diversion_eligible] if pending else [None]
    return [Strategy(id=f"{objective}-{code}" if code else objective,label=f"{label} via {code}" if code else label,objective=objective,preferred_airport=code) for code in airports for objective,label in base]
