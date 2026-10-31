"""Offline airport-level return plans; no terminal assignment is invented."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

import app.airport_arrival as arrivals
from app.airport_arrival import AirportArrivalAssumption, onward_flight_fingerprint, valid_airport_arrival
from app.initial_position import initial_position_fingerprint
from app.models import Allocation, Resource
from app.pricing import app_receipt_deadline, cost_summary
from app.providers.ai import AIProvider
from app.solver import itinerary_options, validate_allocations
from test_apl_pricing import NOW, fixed_clock
from test_initial_position import positioned


@pytest.fixture(autouse=True)
def arrival_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(arrivals, "datetime", Clock)


def package():
    world = positioned()
    world.sim_time, world.budget = 1020, 1000
    group = world.groups[0]
    group.ready_at, group.deadline, group.budget = 1030, 2200, 1000
    outward = world.resources[0]
    outward.departure, outward.arrival = 1040, 1080
    outward.initial_position_assumption.ready_at = group.ready_at
    outward.initial_position_assumption.group_fingerprint = initial_position_fingerprint(world, group)
    epoch = datetime.fromisoformat(world.epoch.replace("Z", "+00:00"))
    instant = lambda minute: (epoch+timedelta(minutes=minute)).isoformat()
    hotel = Resource(id="HOTEL", kind="hotel", name="Sourced stay", origin="HP", destination="HP", departure=1090,
        arrival=1800, capacity=1, quote_rooms=1, room_capacity=2, cost_per_person=0, quote_total_cost=40,
        budget_total_cost=40, source="Nuitee / LiteAPI", valid_until=(NOW+timedelta(hours=1)).isoformat(),
        eligible_group_ids=[group.id], provenance={"pricing":{"status":"complete","mandatory_total":{"amount":"40.00","currency":"EUR"}},
            "property_location":{"node_id":"HP","hotel_id":"property-source","latitude":51.52,"longitude":-0.29}})
    flight = Resource(id="ONWARD", kind="flight", name="Sourced onward flight", origin="LHR", destination="OTP",
        departure=1961, arrival=2100, capacity=2, cost_per_person=0, quote_total_cost=100, source="nuitee",
        eligible_group_ids=[group.id], capacity_scope="quoted_request", external_offer_id="source-offer",
        valid_until=(NOW+timedelta(hours=1)).isoformat(), requires_revalidation=True,
        provenance={"provider":"nuitee","mode":"live","origin":"LHR","destination":"OTP",
            "departure_at":instant(1961.5),"arrival_at":instant(2100),"quoted_at":NOW.isoformat(),
            "segments":[{"origin":"LHR","destination":"OTP","flight_number":"source-123"}]})
    returned = outward.model_copy(deep=True)
    returned.id, returned.origin, returned.destination = "RETURN", "HP", "LHR"
    returned.departure, returned.arrival = 1810, 1841
    returned.initial_position_assumption = None
    quote = returned.provenance["apl_quote"]
    quote["request"].update(origin="Sourced property", destination="Heathrow (LHR), declared dropoff",
        pickup_at=instant(1810), expected_hub="LHR", expected_direction="to_hub")
    quote["resolved_route"] = {"hub":None,"direction":None}
    returned.airport_arrival_assumption = AirportArrivalAssumption(source="operator_declared",confirmed=True,
        group_id=group.id,group_fingerprint=initial_position_fingerprint(world,group),scenario_id=world.scenario_id,
        epoch=world.epoch,airport_id="LHR",onward_resource_id=flight.id,onward_flight_fingerprint=onward_flight_fingerprint(flight),
        onward_departure_at=flight.provenance["departure_at"],processing_minutes=120,
        dropoff={"label":"Declared airport dropoff T5 (not a flight terminal assignment)",
            "coordinates":{"latitude":51.472,"longitude":-0.488}})
    returned.provenance.update(direction="hotel_to_airport",
        source_hotel={"node_id":"HP","hotel_id":"property-source","latitude":51.52,"longitude":-0.29},
        google_route={"source":"Google Routes","source_url":arrivals.GOOGLE_ROUTE_SOURCE,"fetched_at":NOW.isoformat(),
            "request":{"origin":{"latitude":51.52,"longitude":-0.29},"destination":{"latitude":51.472,"longitude":-0.488},
                "departure_time":instant(1810),"travel_mode":"DRIVE"},
            "duration_seconds":"1800.01","duration_guaranteed":False})
    world.resources = [outward,hotel,returned,flight]
    return world


def return_options(world):
    return [item for item in itinerary_options(world,world.groups[0]) if any(leg.resource_id=="RETURN" for leg in item.legs)]


def allocation(world):
    return Allocation(group_id=world.groups[0].id,itinerary=return_options(world)[0])


def test_complete_four_leg_journey_preserves_road_endpoint_and_declared_allowance():
    world = package()
    original = world.model_dump(mode="json")
    item = allocation(world)
    assert [leg.resource_id for leg in item.itinerary.legs] == ["APL1","HOTEL","RETURN","ONWARD"]
    assert validate_allocations(world,[item])["valid"]
    returned = item.itinerary.legs[2]
    assert returned.destination == item.itinerary.legs[3].origin == "LHR"
    assert returned.airport_arrival_assumption.departure_terminal is None
    assert returned.airport_arrival_assumption.processing_minutes == 120
    assert returned.airport_arrival_assumption.dropoff.coordinates.longitude != world.airports[0].lon
    assert returned.initial_position_assumption is None
    assert item.itinerary.legs[0].initial_position_assumption == world.resources[0].initial_position_assumption
    assert world.model_dump(mode="json") == original


@pytest.mark.parametrize("change",[
    lambda w:setattr(w.resources[2],"airport_arrival_assumption",None),
    lambda w:setattr(w.resources[2].airport_arrival_assumption,"group_id","G2"),
    lambda w:setattr(w.resources[2].airport_arrival_assumption,"airport_id","OTP"),
    lambda w:setattr(w.resources[2].airport_arrival_assumption,"onward_resource_id","MISSING"),
    lambda w:setattr(w.resources[2].airport_arrival_assumption,"processing_minutes",121),
    lambda w:setattr(w.resources[2].airport_arrival_assumption.dropoff.coordinates,"longitude",-0.46),
    lambda w:setattr(w.resources[2],"arrival",1840),
    lambda w:w.resources[2].provenance["google_route"]["request"].update(origin={"latitude":51.47,"longitude":-0.46}),
    lambda w:w.resources[2].provenance["google_route"].update(duration_seconds="NaN"),
    lambda w:w.resources[2].provenance.pop("google_route"),
    lambda w:w.resources[2].provenance["source_hotel"].update(latitude=51.53),
    lambda w:w.resources[2].provenance["apl_quote"]["request"].update(expected_direction="from_hub"),
    lambda w:w.resources[2].provenance["apl_quote"]["resolved_route"].update(hub="LGW"),
    lambda w:setattr(w.resources[3],"quote_total_cost",101),
    lambda w:w.resources[3].provenance.update(departure_at="2030-01-05T09:00:00Z"),
    lambda w:setattr(w.resources[3],"available",False),
    lambda w:setattr(w.groups[0],"budget",999),
])
def test_changed_or_missing_return_evidence_cannot_generate_or_validate_a_package(change):
    world = package()
    existing = allocation(world)
    change(world)
    assert return_options(world) == []
    assert not validate_allocations(world,[existing])["valid"]


def test_source_departure_floor_and_processing_boundary_are_checked_independently():
    world = package()
    assert valid_airport_arrival(world,world.resources[2],world.groups[0])
    world.resources[3].departure += 1
    world.resources[2].airport_arrival_assumption.onward_flight_fingerprint = onward_flight_fingerprint(world.resources[3])
    assert not valid_airport_arrival(world,world.resources[2],world.groups[0])


def test_operator_allowance_replaces_rather_than_adds_a_hidden_default():
    world = package()
    flight = world.resources[-1]
    flight.departure = 1856
    flight.provenance["departure_at"] = "2030-01-05T06:56:30+00:00"
    declaration = world.resources[2].airport_arrival_assumption
    declaration.processing_minutes = 15
    declaration.onward_departure_at = flight.provenance["departure_at"]
    declaration.onward_flight_fingerprint = onward_flight_fingerprint(flight)
    item = allocation(world)
    assert item.itinerary.legs[-1].departure-item.itinerary.legs[-2].arrival == 15
    assert validate_allocations(world,[item])["valid"]


def test_exact_onward_flight_is_required_even_if_another_flight_looks_equivalent():
    world = package()
    item = allocation(world)
    other = world.resources[-1].model_copy(deep=True)
    other.id = "OTHER"
    world.resources.append(other)
    assert all(option.legs[-1].resource_id=="ONWARD" for option in return_options(world))
    item.itinerary.legs[-1].resource_id = "OTHER"
    assert not validate_allocations(world,[item])["valid"]


def test_plan_cannot_drop_the_source_assumption_or_end_at_the_airport_return():
    world = package()
    item = allocation(world)
    item.itinerary.legs[2].airport_arrival_assumption = None
    assert not validate_allocations(world,[item])["valid"]
    item = allocation(world)
    item.itinerary.legs.pop()
    item.itinerary.arrival = item.itinerary.legs[-1].arrival
    item.itinerary.total_cost = sum(leg.cost for leg in item.itinerary.legs)
    item.itinerary.cost_evidence = cost_summary(item.itinerary.legs)
    world.groups[0].destination = "LHR"
    declaration = world.resources[2].airport_arrival_assumption
    declaration.group_fingerprint = initial_position_fingerprint(world,world.groups[0])
    world.resources[0].initial_position_assumption.group_fingerprint = declaration.group_fingerprint
    item.itinerary.legs[0].initial_position_assumption = world.resources[0].initial_position_assumption.model_copy(deep=True)
    item.itinerary.legs[2].airport_arrival_assumption = declaration.model_copy(deep=True)
    result = validate_allocations(world,[item])
    assert not result["valid"] and any("no bound onward flight" in message for message in result["errors"])


def test_route_receipt_limits_new_plans_but_confirmed_history_remains_valid():
    world = package()
    item = allocation(world)
    route = world.resources[2].provenance["google_route"]
    route["fetched_at"] = (NOW-timedelta(seconds=5)).isoformat()
    assert app_receipt_deadline(world.resources[2]) == NOW+timedelta(seconds=115)
    route["fetched_at"] = (NOW-timedelta(seconds=121)).isoformat()
    assert not return_options(world)
    world.groups[0].status = "confirmed"
    for resource in world.resources:
        resource.booking_status = "confirmed"
    assert validate_allocations(world,[item])["valid"]


@pytest.mark.parametrize("changed",[
    {"confirmed":1},{"processing_minutes":True},{"processing_minutes":0},{"processing_minutes":721},
    {"processing_minutes":"120"},{"departure_terminal":"T5"},{"onward_departure_at":"2030-01-05T08:41:30"},
])
def test_declared_inputs_require_exact_types_and_do_not_claim_a_terminal(changed):
    raw = package().resources[2].airport_arrival_assumption.model_dump() | changed
    with pytest.raises(ValidationError):
        AirportArrivalAssumption.model_validate(raw)


@pytest.mark.asyncio
async def test_decisions_sees_airport_assumption_without_airline_or_gate_guarantees():
    world = package()
    item = allocation(world)
    plan = {"id":"return-plan","strategy":{"objective":"balanced"},"allocations":[item.model_dump(mode="json")],
        "exceptions":[],"recovered_passengers":2,"total_cost":item.itinerary.total_cost,
        "cost_evidence":item.itinerary.cost_evidence.model_dump(),"average_arrival":item.itinerary.arrival,
        "validation":validate_allocations(world,[item]),"scenario_changes":[]}
    seen=[]
    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={"answers":[{"name":"approve_recovery_plan","type":"choice","choice":True}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        provider = AIProvider(SimpleNamespace(openai_api_key="mock-only",request_timeout_seconds=1),client=client)
        await provider.choose(world.model_dump(mode="json"),[plan],"Plan the sourced hotel return")
    evidence=seen[0]["input"][0]["content"][0]["data"]
    assumption=next(r for r in evidence["world"]["resources"] if r["id"]=="RETURN")["airport_arrival_assumption"]
    assert assumption["source"]=="operator_declared" and assumption["departure_terminal"] is None
    prompt=json.dumps(seen[0])
    assert "not airline guidance" in prompt and "boarding is not guaranteed" in prompt
