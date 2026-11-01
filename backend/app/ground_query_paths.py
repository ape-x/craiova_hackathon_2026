"""Bounded existing-inventory paths around a prospective sourced flight.

This module only reads dated resources. It never creates transport or requests
a quote; a later solver still validates the complete priced itinerary.
"""
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal

from .airport_catalog import lookup_airport
from .airport_constraints import airport_operation_search_allowed, case_airport_operation_blocked
from .solver import connection_minutes, fixed_offers, resource_cost, resource_feasible, units


MAX_GROUND_LEGS = 5  # The live solver allows six legs, including the flight.
MAX_PATH_STATES = 4000  # Per group and traversal direction.
MAX_PATH_EDGE_CHECKS = 12000
MAX_QUERY_PAIRS = 4096  # Per group, including incompatible path pairs.
MAX_GROUP_QUERIES = 256
MAX_SEARCH_DATES = 7


@dataclass(frozen=True)
class GroundPath:
    airport_id: str
    minute: int
    cost_cents: int
    resource_ids: frozenset[str]
    locations: frozenset[str]
    has_hotel: bool = False
    flight_buffer: int = 30


def cents(value):
    return int(Decimal(str(value)) * 100)


def fixed_capacity(world, group_ids):
    """Match the solver's reservations that this recovery cannot replace."""
    identities = {offer.id for offer in fixed_offers(world, set(group_ids))}
    occupied = Counter()
    for item in world.reservations:
        if item.offer_id in identities and item.status in ("held", "proposed", "accepted", "confirmed"):
            occupied[item.resource_id] += item.quantity
    return occupied


def ground_paths(world, group, occupied):
    """Return prefix/suffix alternatives without collapsing time/cost tradeoffs.

    A suffix's minute is the latest flight arrival: its first bus departure
    minus the same ten-minute connection used by the solver. Reverse traversal
    preserves all intermediate pickup and arrival constraints.
    """
    nodes = {node.id: node for node in world.airports}
    forward, reverse = defaultdict(list), defaultdict(list)
    for resource in world.resources:
        if resource.kind not in ("bus", "hotel") or resource.capacity - occupied[resource.id] < units(resource, group):
            continue
        forward[resource.origin].append(resource)
        if resource.kind == "bus" and resource.airport_arrival_assumption is None:
            reverse[resource.destination].append(resource)
        # An explicitly declared initial point is attached to the group's
        # airport context, not connected by an invented airport walking leg.
        if resource.initial_position_assumption is not None and resource.origin != group.origin:
            forward[group.origin].append(resource)
    for entries in forward.values():
        entries.sort(key=lambda item: (item.departure, resource_cost(item, group), item.id))
    for entries in reverse.values():
        entries.sort(key=lambda item: (-item.departure, resource_cost(item, group), item.id))

    def airport(location, backwards):
        node = nodes.get(location)
        operation = "arrivals" if backwards else "departures"
        return bool(node and node.kind == "airport" and lookup_airport(node.code)
            and airport_operation_search_allowed(node, operation)
            and not case_airport_operation_blocked(world, group, node.code, operation))

    ready = max(world.sim_time, group.ready_at)
    budget = cents(group.budget)

    def traverse(backwards):
        start = group.destination if backwards else group.origin
        queue = deque([(start, group.deadline if backwards else ready, 0, (), frozenset({start}), False)])
        paths, visited, checks, limited = [], 0, 0, False
        while queue:
            if visited >= MAX_PATH_STATES:
                limited = True
                break
            location, minute, cost, legs, locations, has_hotel = queue.popleft()
            visited += 1
            # A return transfer authorizes exactly its existing onward quote
            # revision. A fresh search may produce another resource ID, so this
            # prefix cannot justify prospective flight sourcing. The existing
            # bound itinerary remains available to the solver itself.
            if legs and not backwards and legs[-1].airport_arrival_assumption is not None:
                continue
            if airport(location, backwards):
                paths.append(GroundPath(location, minute, cost, frozenset(item.id for item in legs), locations, has_hotel))
            if len(legs) >= MAX_GROUND_LEGS:
                continue
            for resource in (reverse if backwards else forward).get(location, ()):
                checks += 1
                if checks > MAX_PATH_EDGE_CHECKS:
                    return paths, True
                used = {item.id for item in legs}
                if resource.id in used:
                    continue
                next_location = resource.origin if backwards else resource.destination
                if next_location in locations and (backwards or (resource.kind != "hotel" and not has_hotel)):
                    continue
                if backwards:
                    # A declared first-position pickup cannot occur after the
                    # prospective flight. Neither can a return bound to a next flight.
                    if resource.initial_position_assumption is not None or resource.arrival > minute:
                        continue
                    at, not_before = resource.origin, ready
                else:
                    connection = connection_minutes(legs[-1] if legs else None, resource)
                    if connection is None:
                        continue
                    at, not_before = location, minute + connection
                if not resource_feasible(world, resource, group, at, not_before, used):
                    continue
                total = cost + cents(resource_cost(resource, group))
                if total > budget:
                    continue
                next_minute = resource.departure - 10 if backwards else resource.arrival
                if backwards and next_minute < ready:
                    continue
                if visited + len(queue) >= MAX_PATH_STATES:
                    limited = True
                    continue
                queue.append((next_location, next_minute, total, legs + (resource,),
                    locations | {next_location}, has_hotel or resource.kind == "hotel"))
        return paths, limited

    prefixes, prefix_limited = traverse(False)
    suffixes, suffix_limited = traverse(True)
    prefixes.sort(key=lambda item: (item.minute + item.flight_buffer, item.cost_cents, len(item.resource_ids), item.airport_id, tuple(sorted(item.resource_ids))))
    suffixes.sort(key=lambda item: (-item.minute, item.cost_cents, len(item.resource_ids), item.airport_id, tuple(sorted(item.resource_ids))))
    return prefixes, suffixes, prefix_limited or suffix_limited
