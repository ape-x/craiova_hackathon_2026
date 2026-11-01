"""A sourced return's exact flight binding cannot authorize new flight searches."""
from datetime import timedelta

import pytest

from app.airport_arrival import valid_airport_arrival
from app.models import Resource, Strategy
from app.recovery_jobs import flight_query_options
from app.solver import build_candidate, connection_minutes, resource_feasible
from app.store import utcnow
from test_transfer_planning import import_request
from test_transfer_return import returning, setup


@pytest.mark.asyncio
async def test_bound_return_remains_a_valid_existing_plan_but_not_a_new_search_prefix(returning):
    store, _, planning, request, _, _, onward_id = returning

    # Saved sourced hotel geography supplies the declared current location.
    hotel_id = request().hotel_node_id

    def locate(world):
        world.groups[0].origin = hotel_id

    store.mutate("at-hotel-for-bound-return", {}, store.read().version, locate)
    response = await planning.quote(request())  # Local fake adapters only.
    assert response["state"] == "ready", response
    imported = planning.import_quote(response["quote_id"], import_request(store))
    world = store.read()
    group = world.groups[0]
    bound = next(item for item in world.resources if item.id == imported["resource_ids"][0])
    onward = next(item for item in world.resources if item.id == onward_id)
    assert valid_airport_arrival(world, bound, group)
    assert resource_feasible(world, bound, group, group.origin, max(world.sim_time, group.ready_at), set())
    assert connection_minutes(bound, onward) == 90
    newer_flight = onward.model_copy(update={"id": "NEW-QUOTE-REVISION"})
    assert connection_minutes(bound, newer_flight) is None
    plan = build_candidate(world, Strategy(), scope_group_ids={group.id})
    assert plan.validation["valid"] and plan.recovered_passengers == group.size
    assert [leg.resource_id for leg in plan.allocations[0].itinerary.legs] == [bound.id, onward.id]

    before = world.model_dump(mode="json")
    found, gaps = flight_query_options(world, [group])
    assert found == []
    assert any(item["code"] == "physical_route_missing" for item in gaps)
    assert world.model_dump(mode="json") == before

    # A separate source service with no exact-flight restriction still permits
    # a prospective query; pruning the bound path must not remove this route.
    unbound = Resource(id="INDEPENDENT-COACH", name="Independent offline coach fixture", kind="bus",
        origin=hotel_id, destination="LHR", departure=bound.departure, arrival=bound.arrival + 10,
        capacity=group.size, cost_per_person=10, source="offline-coach-fixture",
        eligible_group_ids=[group.id], valid_until=(utcnow() + timedelta(hours=1)).isoformat())
    world.resources.append(unbound)
    found, gaps = flight_query_options(world, [group])
    assert found and gaps == []
    assert all(item["query"]["origin_code"] == "LHR" and item["query"]["destination_code"] == "CRA" for item in found)
    assert {item["earliest_departure"] for item in found} == {unbound.arrival + 30}
