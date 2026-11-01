"""Pure case restrictions and source/global flight-operation validation.

Catalog identity resolves IATA/ICAO aliases; it never establishes permission to
land or depart. Operator restrictions are scoped to one explicitly linked case.
"""
from dataclasses import dataclass

from .airport_catalog import lookup_airport
from .weather import departure_allowed, landing_allowed


MAX_FLIGHT_SEGMENTS = 10


def canonical_airport_code(value):
    airport = lookup_airport(value)
    return airport["code"] if airport else None


def _case(world, group):
    if world.mode != "live" or not group.incident_id:
        return None
    return next((case for case in world.incidents
        if case.id == group.incident_id and group.id in case.group_ids), None)


def case_airport_operation_blocked(world, group, airport_code, operation):
    """Legacy bans cover both operations; active reports cover only their role."""
    if operation not in ("arrivals", "departures"):
        raise ValueError("An airport operation must be arrivals or departures")
    case = _case(world, group)
    code = canonical_airport_code(airport_code)
    if case is None or code is None:
        return False
    if case.unavailable_airport_code and canonical_airport_code(case.unavailable_airport_code) == code:
        return True
    return any(canonical_airport_code(entry.airport_code) == code and entry.operation == operation
        for entry in case.airport_restrictions)


def airport_operation_search_allowed(airport, operation):
    """An unknown catalog node permits sourcing, not a flight allocation.

    Catalog-only nodes start unavailable until a real route is sourced. An
    explicit closure or direction-specific weather prohibition still prevents
    searches, and candidate validation separately requires availability.
    """
    if operation not in ("arrivals", "departures"):
        raise ValueError("An airport operation must be arrivals or departures")
    if airport.operational_status == "closed":
        return False
    if not airport.available and airport.operational_status != "unknown":
        return False
    return airport.weather is None or (airport.weather.landing_allowed if operation == "arrivals" else airport.weather.departure_allowed)


@dataclass(frozen=True)
class FlightAirportOperations:
    operations: tuple[tuple[str, str], ...]
    valid: bool
    unresolved_technical_stops: bool = False


def flight_airport_operations(resource):
    """Read only normalized segment endpoints, never arbitrary supplier terms.

    Nuitee preserves opaque technical-stop details without a validated airport
    schema. Positive counts/nonempty details cannot prove those airport roles.
    Connecting marketed segments already expose both operations explicitly.
    """
    origin = canonical_airport_code(resource.origin)
    destination = canonical_airport_code(resource.destination)
    operations = [(origin, "departures"), (destination, "arrivals")]
    valid = bool(origin and destination and origin != destination)
    unresolved = False
    provenance = resource.provenance
    if "segments" in provenance:
        segments = provenance["segments"]
        if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_FLIGHT_SEGMENTS:
            return FlightAirportOperations(tuple((code, role) for code, role in operations if code), False)
        previous = None
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                valid = False
                continue
            start = canonical_airport_code(segment.get("origin"))
            end = canonical_airport_code(segment.get("destination"))
            if not start or not end or start == end or (index == 0 and start != origin) or (previous and start != previous):
                valid = False
            operations.extend(((start, "departures"), (end, "arrivals")))
            previous = end
            count = segment.get("technical_stop_count")
            if count is not None:
                if type(count) is not int or count < 0:
                    valid = False
                elif count > 0:
                    unresolved = True
            stops = segment.get("stops")
            if isinstance(stops, str):
                unresolved |= bool(stops.strip())
            elif isinstance(stops, (list, dict)):
                unresolved |= bool(stops)
            elif stops is not None and stops is not False:
                # Only an explicit empty scalar is harmless; unsupported scalar
                # stop details are not evidence of a usable flight itinerary.
                if type(stops) not in (int, float) or stops != 0:
                    valid = False
        if previous != destination:
            valid = False
    return FlightAirportOperations(tuple(dict.fromkeys((code, role) for code, role in operations if code)), valid, unresolved)


def resource_blocked_by_case(world, resource, group):
    if world.mode != "live" or resource.kind != "flight":
        return False
    case = _case(world, group)
    if case is None or not (case.unavailable_airport_code or case.airport_restrictions):
        return False
    route = flight_airport_operations(resource)
    if not route.valid or route.unresolved_technical_stops:
        return True
    return any(case_airport_operation_blocked(world, group, code, role) for code, role in route.operations)


def flight_global_operations_allowed(world, resource):
    """Preserve source/global restrictions at endpoints and known connections."""
    route = flight_airport_operations(resource)
    if not route.valid:
        return False
    if route.unresolved_technical_stops and any(
        airport.kind == "airport" and (
            airport.operational_status == "closed" or (
                airport.weather is not None and (
                    not airport.weather.landing_allowed or not airport.weather.departure_allowed)))
        for airport in world.airports
    ):
        # The hidden stop may require either operation at a restricted airport.
        # Catalog-only unavailable/unknown nodes do not establish a global ban.
        return False
    nodes = {}
    for airport in world.airports:
        if airport.kind == "airport":
            code = canonical_airport_code(airport.code)
            if code:
                nodes.setdefault(code, []).append(airport)
    if any(canonical_airport_code(endpoint) not in nodes for endpoint in (resource.origin, resource.destination)):
        return False
    for code, operation in route.operations:
        allowed = landing_allowed if operation == "arrivals" else departure_allowed
        if any(node.operational_status == "closed" or not allowed(node) for node in nodes.get(code, ())):
            return False
    return True
