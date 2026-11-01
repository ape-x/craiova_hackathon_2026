"""Existing-source ground chains around a prospective flight; no provider I/O."""
from datetime import datetime, timedelta

import pytest

from app.airport_catalog import lookup_airport
from app.models import Airport, Itinerary, Leg, Offer, Reservation, Resource, Strategy
from app.recovery_jobs import flight_query_options
from app.solver import build_candidate
from app.store import utcnow
from test_recovery_jobs import setup


def node(world, identity, kind="station"):
    reference = lookup_airport(identity) if kind == "airport" else None
    world.airports.append(Airport(id=identity, code=identity, name=identity, kind=kind,
        lat=reference["lat"] if reference else 44.3, lon=reference["lon"] if reference else 23.8))


def service(world, identity, origin, destination, departure, arrival, cost=20, **changes):
    group = world.groups[0]
    value = Resource(id=identity, name="Offline sourced service " + identity, kind="bus",
        origin=origin, destination=destination, departure=group.ready_at + departure,
        arrival=group.ready_at + arrival, capacity=group.size, cost_per_person=cost,
        eligible_group_ids=[group.id], source="offline-coach-fixture",
        valid_until=(utcnow() + timedelta(hours=1)).isoformat())
    for key, item in changes.items():
        setattr(value, key, item)
    world.resources.append(value)
    return value


@pytest.fixture
def chain(setup):
    world = setup[1].read()
    group = world.groups[0]
    group.origin = "SBZ"
    world.incidents[0].unavailable_airport_code = "OTP"
    node(world, "CRA-STATION")
    service(world, "FIRST", "CRA", "CRA-STATION", 180, 210, 20)
    service(world, "LAST", "CRA-STATION", "OTP", 220, 250, 30)
    return world, group


def options(world, group):
    return flight_query_options(world, [group])


def assert_no_link(world, group):
    found, gaps = options(world, group)
    assert found == []
    assert any(item["code"] == "physical_route_missing" for item in gaps)


def test_two_sourced_ground_legs_enable_alternate_arrival_and_complete_solver_plan(chain):
    world, group = chain
    before = world.model_dump(mode="json")
    found, gaps = options(world, group)
    assert gaps == []
    assert len(found) == 1
    query = found[0]
    assert (query["query"]["origin_code"], query["query"]["destination_code"]) == ("SBZ", "CRA")
    assert query["earliest_departure"] == group.ready_at + 30
    assert query["latest_arrival"] == group.ready_at + 170
    assert datetime.fromisoformat(query["query"]["latest_arrival_at"]) == datetime.fromisoformat(world.epoch) + timedelta(minutes=group.ready_at + 170)
    assert world.model_dump(mode="json") == before
    service(world, "FLIGHT", "SBZ", "CRA", 30, 170, 100, kind="flight", source="offline-flight-fixture")
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"] is True
    assert plan.recovered_passengers == group.size
    assert [leg.resource_id for leg in plan.allocations[0].itinerary.legs] == ["FLIGHT", "FIRST", "LAST"]


@pytest.mark.parametrize("failure", ["expired", "unknown_freshness", "unknown_capacity", "small_capacity",
    "wrong_group", "unavailable", "synthetic", "unverified_apl_price", "tight_connection", "late_arrival",
    "broken_stop_identity", "closed_alternate", "insufficient_budget", "reused_resource_id"])
def test_unusable_ground_chain_never_authorizes_a_flight_search(chain, failure):
    world, group = chain
    first, last = world.resources
    if failure == "expired":
        first.valid_until = (utcnow() - timedelta(seconds=1)).isoformat()
    elif failure == "unknown_freshness":
        first.valid_until = None
        first.requires_revalidation = True
    elif failure == "unknown_capacity":
        first.capacity = 0
    elif failure == "small_capacity":
        group.size = group.adult_count = 2
        last.capacity = 2
    elif failure == "wrong_group":
        first.eligible_group_ids = ["G2"]
    elif failure == "unavailable":
        first.available = False
    elif failure == "synthetic":
        first.source = "synthetic"
    elif failure == "unverified_apl_price":
        first.source = "Airport Pickups London"
    elif failure == "tight_connection":
        last.departure = first.arrival + 9
    elif failure == "late_arrival":
        last.arrival = group.deadline + 1
    elif failure == "broken_stop_identity":
        last.origin = "OTHER-STATION"
    elif failure == "closed_alternate":
        next(item for item in world.airports if item.id == "CRA").operational_status = "closed"
    elif failure == "reused_resource_id":
        last.id = first.id
    else:
        group.budget = 49  # Each leg fits individually; their 50 EUR sum does not.
    assert_no_link(world, group)


def test_group_party_cost_is_not_confused_with_one_seat_price(chain):
    world, group = chain
    group.size = group.adult_count = 2
    for resource in world.resources:
        resource.capacity = 2
    group.budget = 90  # Two seats cost 2 * (20 + 30), not 50 EUR.
    assert_no_link(world, group)


def test_fixed_other_group_reservations_reduce_available_ground_capacity(chain):
    world, group = chain
    first = world.resources[0]
    leg = Leg(resource_id=first.id, kind=first.kind, origin=first.origin, destination=first.destination,
        departure=first.departure, arrival=first.arrival, cost=20)
    world.offers.append(Offer(id="FIXED-G2", group_id="G2", status="proposed",
        itinerary=Itinerary(legs=[leg], arrival=leg.arrival, total_cost=20),
        expires_at=first.valid_until, created_version=world.version))
    world.reservations.append(Reservation(id="FIXED-SEAT", offer_id="FIXED-G2", group_id="G2",
        resource_id=first.id, quantity=1, status="proposed", expires_at=first.valid_until))
    assert_no_link(world, group)


def fixed_ground_journey(world, *, status, reservations, capacity):
    """A complete retained journey occupies both legs of the alternate-airport link."""
    other = world.groups[1]
    other.status = "confirmed" if status == "confirmed" else "offered"
    legs = []
    for resource in world.resources:
        resource.capacity = capacity
        resource.eligible_group_ids = [group.id for group in world.groups]
        legs.append(Leg(resource_id=resource.id, kind=resource.kind,
            origin=resource.origin, destination=resource.destination,
            departure=resource.departure, arrival=resource.arrival,
            cost=resource.cost_per_person * other.size))
    offer = Offer(id="FIXED-JOURNEY", group_id=other.id, status=status,
        itinerary=Itinerary(legs=legs, arrival=legs[-1].arrival,
            total_cost=sum(leg.cost for leg in legs)),
        expires_at=world.resources[0].valid_until, created_version=world.version)
    world.offers.append(offer)
    if reservations:
        world.reservations.extend(Reservation(id="FIXED-" + leg.resource_id,
            offer_id=offer.id, group_id=other.id, resource_id=leg.resource_id,
            quantity=other.size, status=status, expires_at=offer.expires_at) for leg in legs)
    return other


@pytest.mark.parametrize("reservations", [False, True])
@pytest.mark.parametrize("status", ["confirmed", "held", "proposed", "accepted"])
def test_fixed_ground_itinerary_at_capacity_cannot_authorize_an_alternate_flight(chain, reservations, status):
    from app.confirmed_allocations import confirmation_accounting_issues
    world, group = chain
    fixed_ground_journey(world, status=status, reservations=reservations, capacity=1)
    assert confirmation_accounting_issues(world) == []
    before = world.model_dump(mode="json")
    assert_no_link(world, group)
    assert world.model_dump(mode="json") == before
    # Independent final allocation agrees: a real incoming flight alone cannot
    # make the fully occupied ground connection a complete new journey.
    service(world, "FLIGHT", "SBZ", "CRA", 30, 170, 100, kind="flight", source="offline-flight-fixture")
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"] is True
    assert plan.recovered_passengers == 0 and plan.allocations == []


@pytest.mark.parametrize("reservations", [False, True])
@pytest.mark.parametrize("status", ["confirmed", "held", "proposed", "accepted"])
def test_fixed_ground_itinerary_and_mirrored_rows_consume_capacity_only_once(chain, reservations, status):
    world, group = chain
    fixed_ground_journey(world, status=status, reservations=reservations, capacity=2)
    before = world.model_dump(mode="json")
    found, gaps = options(world, group)
    assert gaps == [] and len(found) == 1
    assert (found[0]["query"]["origin_code"], found[0]["query"]["destination_code"]) == ("SBZ", "CRA")
    assert world.model_dump(mode="json") == before
    service(world, "FLIGHT", "SBZ", "CRA", 30, 170, 100, kind="flight", source="offline-flight-fixture")
    before = world.model_dump(mode="json")
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"] is True
    assert plan.recovered_passengers == group.size
    assert [leg.resource_id for leg in plan.allocations[0].itinerary.legs] == ["FLIGHT", "FIRST", "LAST"]
    assert world.model_dump(mode="json") == before


@pytest.mark.parametrize("reservations", [False, True])
@pytest.mark.parametrize("status", ["held", "proposed", "accepted"])
def test_selected_unconfirmed_ground_journey_remains_replaceable_during_query_discovery(chain, reservations, status):
    world, group = chain
    other = fixed_ground_journey(world, status=status, reservations=reservations, capacity=1)
    before = world.model_dump(mode="json")
    found, _ = flight_query_options(world, [group, other])
    assert any(item["group_id"] == group.id and item["query"]["destination_code"] == "CRA" for item in found)
    assert world.model_dump(mode="json") == before


def test_prefix_and_suffix_costs_share_one_budget_and_keep_cheaper_later_prefix(chain):
    world, group = chain
    node(world, "CLJ", "airport")
    group.budget = 100
    service(world, "EARLY-EXPENSIVE", "SBZ", "CLJ", 10, 60, 70)
    service(world, "LATE-CHEAP", "SBZ", "CLJ", 20, 90, 20)
    found, _ = options(world, group)
    via_cluj = [item for item in found if item["query"]["origin_code"] == "CLJ"]
    assert len(via_cluj) == 1
    assert via_cluj[0]["earliest_departure"] == group.ready_at + 120
    assert via_cluj[0]["latest_arrival"] == group.ready_at + 170
    # A source flight then consumes only the remaining 30 EUR.
    service(world, "ONWARD", "CLJ", "CRA", 120, 170, 30, kind="flight", source="offline-flight-fixture")
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"] and plan.recovered_passengers == group.size
    assert [leg.resource_id for leg in plan.allocations[0].itinerary.legs] == ["LATE-CHEAP", "ONWARD", "FIRST", "LAST"]


def test_later_expensive_suffix_does_not_hide_a_valid_cheaper_earlier_one(chain):
    world, group = chain
    node(world, "CLJ", "airport")
    node(world, "CHEAP-STATION")
    group.budget = 100
    service(world, "PREFIX", "SBZ", "CLJ", 10, 60, 60)
    service(world, "CHEAP-FIRST", "CRA", "CHEAP-STATION", 160, 180, 10)
    service(world, "CHEAP-LAST", "CHEAP-STATION", "OTP", 190, 230, 10)
    found, _ = options(world, group)
    via_cluj = [item for item in found if item["query"]["origin_code"] == "CLJ"]
    assert len(via_cluj) == 1
    assert via_cluj[0]["latest_arrival"] == group.ready_at + 150


@pytest.mark.parametrize("tail_legs,allowed", [(5, True), (6, False)])
def test_ground_suffix_reserves_one_of_the_six_solver_legs_for_the_flight(chain, tail_legs, allowed):
    world, group = chain
    world.resources = []
    stops = ["CRA"] + [f"STOP-{index}" for index in range(tail_legs - 1)] + ["OTP"]
    for identity in stops[1:-1]:
        node(world, identity)
    for index, (origin, destination) in enumerate(zip(stops, stops[1:])):
        service(world, f"LEG-{index}", origin, destination, 180 + index * 30, 200 + index * 30, 1)
    found, _ = options(world, group)
    assert bool(found) is allowed
    if allowed:
        node(world, "CLJ", "airport")
        service(world, "EXTRA-PREFIX", "SBZ", "CLJ", 10, 50, 1)
        found, _ = options(world, group)
        assert not any(item["query"]["origin_code"] == "CLJ" for item in found)


def test_cycles_are_not_used_and_equivalent_paths_do_not_duplicate_queries(chain):
    from app.ground_query_paths import fixed_capacity, ground_paths
    world, group = chain
    service(world, "CYCLE-TO-CRA", "CRA-STATION", "CRA", 230, 240, 1)
    service(world, "CYCLE-BACK", "CRA", "CRA-STATION", 250, 260, 1)
    for index in range(20):
        service(world, f"EQUIVALENT-{index}", "CRA", "CRA-STATION", 180, 210, 20)
    _, suffixes, _ = ground_paths(world, group, fixed_capacity(world, [group.id]))
    assert not any("CYCLE-TO-CRA" in path.resource_ids for path in suffixes)
    found, gaps = options(world, group)
    assert len(found) == 1 and gaps == []


def test_bounded_branching_is_reported_without_starving_a_later_group(chain, monkeypatch):
    import app.ground_query_paths as paths
    world, group = chain
    other = world.groups[1]
    node(world, "CLJ", "airport")
    other.destination = "CLJ"
    for index in range(30):
        service(world, f"BRANCH-{index}", f"UNKNOWN-STOP-{index}", "OTP", 220, 250, 1)
    monkeypatch.setattr(paths, "MAX_PATH_STATES", 4)
    monkeypatch.setattr(paths, "MAX_PATH_EDGE_CHECKS", 6)
    checked = []
    original = paths.resource_feasible

    def counted(*args, **kwargs):
        checked.append(args[2].id)
        return original(*args, **kwargs)

    monkeypatch.setattr(paths, "resource_feasible", counted)
    found, gaps = flight_query_options(world, [group, other])
    assert any(item["group_id"] == other.id for item in found)
    assert any(item["code"] == "physical_route_search_limited" and item["group_ids"] == [group.id] for item in gaps)
    assert checked.count(group.id) <= 2 * paths.MAX_PATH_EDGE_CHECKS


def test_query_limits_keep_round_robin_group_fairness(chain, monkeypatch):
    import app.ground_query_paths as paths
    world, group = chain
    node(world, "CLJ", "airport")
    other = world.groups[1]
    other.origin, other.destination = "SBZ", "CLJ"
    for index in range(8):
        service(world, f"TIMED-{index}", "CRA", "CRA-STATION", 150 + index, 180 + index, 20)
    monkeypatch.setattr(paths, "MAX_GROUP_QUERIES", 3)
    found, gaps = flight_query_options(world, [group, other])
    assert [item["group_id"] for item in found[:2]] == [group.id, other.id]
    assert sum(item["group_id"] == group.id for item in found) == 3
    assert len({item["id"] for item in found}) == len(found)
    assert any(item["code"] == "physical_route_search_limited" and item["group_ids"] == [group.id] for item in gaps)


def test_pair_enumeration_is_bounded_and_disclosed_without_losing_found_routes(chain, monkeypatch):
    import app.ground_query_paths as paths
    world, group = chain
    node(world, "CLJ", "airport")
    service(world, "EXTRA-PREFIX", "SBZ", "CLJ", 10, 60, 10)
    monkeypatch.setattr(paths, "MAX_QUERY_PAIRS", 1)
    found, gaps = options(world, group)
    assert len(found) == 1 and found[0]["query"]["origin_code"] == "SBZ"
    assert any(item["code"] == "physical_route_search_limited" for item in gaps)
