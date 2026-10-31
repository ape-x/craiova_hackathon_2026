import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import AcceptRequest, Allocation, EventRequest, Offer, ReplanRequest, Reservation, ScenarioChange, Strategy
from app.scenario_authoring import add_passengers, add_resource
from app.seed import seed_world
from app.service import RecoveryService, apply_change
from app.solver import build_candidate, default_strategies, project_diversion, validate_allocations
from app.store import DomainError, Store


def weather(**values):
    return {"condition": "fog", "visibility_m": 150, "wind_kts": 8.0,
            "landing_allowed": False, "departure_allowed": True, **values}


def set_weather(world, airport="CRA", **values):
    apply_change(world, ScenarioChange(type="airport_weather", target_id=airport, value=weather(**values)))


def flight(world, flight_id="F201"):
    return next(r for r in world.resources if r.id==flight_id)


def definition(**updates):
    return {"kind":"flight", "name":"Synthetic arrival", "origin":"LTN", "destination":"CRA",
            "departure":550, "arrival":700, "capacity":20, "accessible":True,
            "cost_per_person":25.0, "room_capacity":1, "vehicle_id":None, "available":True, **updates}


def test_landing_weather_triggers_diversion_without_closing_airport_or_departures():
    world=seed_world()
    set_weather(world)
    cra=next(a for a in world.airports if a.id=="CRA")
    assert cra.available and cra.weather.condition=="fog"
    assert flight(world).status=="needs_diversion" and not flight(world).available
    assert all(g.status=="affected" for g in world.groups if g.original_flight_id=="F201")
    assert flight(world,"F111").available  # departure still allowed
    assert {s.preferred_airport for s in default_strategies(world)}=={"OTP","SBZ"}
    projected, changes=project_diversion(world,"CRA")
    assert changes==[] and flight(projected).status=="needs_diversion"
    with pytest.raises(DomainError,match="not eligible"):
        apply_change(world,ScenarioChange(type="flight_diverted",target_id="F201",value="CRA"))


def test_departure_only_weather_does_not_redirect_already_airborne_outbound():
    world=seed_world()
    set_weather(world,"LTN",landing_allowed=True,departure_allowed=False)
    assert flight(world).available and flight(world).destination=="CRA"
    assert flight(world).status!="needs_diversion"


def test_weather_held_flights_restore_but_manual_cancellation_and_closure_survive():
    world=seed_world()
    set_weather(world,landing_allowed=True,departure_allowed=False)
    assert flight(world,"F111").status=="weather_held"
    apply_change(world,ScenarioChange(type="flight_cancelled",target_id="F111"))
    other=add_resource(world,"WX-F",definition(origin="CRA",destination="OTP",departure=800,arrival=900))
    assert other.status=="weather_held"
    set_weather(world,condition="clear",visibility_m=None,wind_kts=None,landing_allowed=True,departure_allowed=True)
    assert flight(world,"F111").status=="cancelled" and not flight(world,"F111").available
    assert other.status=="scheduled" and other.available
    apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    set_weather(world,condition="clear",landing_allowed=True,departure_allowed=True)
    assert not next(a for a in world.airports if a.id=="CRA").available
    assert not other.available


def test_reopening_airport_does_not_override_weather_and_restoration_is_rejected():
    world=seed_world()
    set_weather(world,departure_allowed=False)
    apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    apply_change(world,ScenarioChange(type="airport_restored",target_id="CRA"))
    assert "CRA" not in {s.preferred_airport for s in default_strategies(world)}
    with pytest.raises(DomainError,match="cannot be restored"):
        apply_change(world,ScenarioChange(type="resource_restored",target_id="F111"))


def test_weather_before_new_airborne_flight_and_manifest_is_order_independent():
    world=seed_world()
    set_weather(world)
    arriving=add_resource(world,"WX-ARR",definition())
    assert arriving.status=="needs_diversion" and not arriving.available
    passengers=add_passengers(world,{
        "name":"Synthetic families", "passenger_count":12, "group_size":3,
        "origin":"CRA", "destination":"CRA", "ready_at":730, "deadline":1300,
        "accessible":False, "budget_per_person":200.0, "original_flight_id":"WX-ARR", "priority":1,
    })
    assert len(passengers)==4 and all(g.status=="affected" for g in passengers)
    assert arriving.departure==550 and arriving.arrival==700


def test_weather_clear_allows_pending_arrival_back_to_original_destination():
    world=seed_world()
    set_weather(world)
    set_weather(world,condition="clear",visibility_m=None,landing_allowed=True)
    assert flight(world).status=="needs_diversion"  # decision still required
    apply_change(world,ScenarioChange(type="flight_diverted",target_id="F201",value="CRA"))
    assert flight(world).available and flight(world).status=="diverted"
    assert all(g.status=="scheduled" for g in world.groups if g.original_flight_id=="F201")


def test_weather_held_incoming_flight_edits_keep_passenger_location_and_readiness_in_sync():
    world=seed_world()
    set_weather(world)
    arriving=add_resource(world,"WX-FUTURE",definition(departure=650,arrival=750))
    passengers=add_passengers(world,{
        "name":"Synthetic connecting families", "passenger_count":6, "group_size":3,
        "origin":"CRA", "destination":"OTP", "ready_at":780, "deadline":1300,
        "accessible":False, "budget_per_person":200.0, "original_flight_id":arriving.id, "priority":1,
    })
    assert arriving.status=="weather_held"
    apply_change(world,ScenarioChange(type="resource_schedule",target_id=arriving.id,value={"departure":650,"arrival":800}))
    assert all(group.ready_at==830 for group in passengers)
    apply_change(world,ScenarioChange(type="resource_route",target_id=arriving.id,value={"origin":"LTN","destination":"SBZ"}))
    assert arriving.available and arriving.status=="scheduled"
    assert all(group.origin=="SBZ" and group.ready_at==830 for group in passengers)


def test_weather_held_inbound_releases_onward_commitments_and_never_teleports_passengers():
    world=seed_world()
    arriving=add_resource(world,"WX-INBOUND",definition(departure=650,arrival=750))
    bus=add_resource(world,"WX-BUS",definition(kind="bus",origin="CRA",destination="OTP",departure=800,arrival=1000))
    groups=add_passengers(world,{
        "name":"Onward family", "passenger_count":3, "group_size":3,
        "origin":"CRA", "destination":"OTP", "ready_at":780, "deadline":1300,
        "accessible":False, "budget_per_person":200.0, "original_flight_id":arriving.id, "priority":1,
    })
    world.resources=[arriving,bus]
    world.groups=groups
    plan=build_candidate(world,Strategy())
    assert plan.recovered_passengers==3
    allocation=plan.allocations[0]
    group=groups[0]
    group.status="confirmed"
    world.offers=[Offer(id="WX-OFFER",group_id=group.id,itinerary=allocation.itinerary,
        status="confirmed",expires_at="2099-01-01T00:00:00Z",created_version=1)]
    world.reservations=[Reservation(id="WX-HOLD",offer_id="WX-OFFER",group_id=group.id,
        resource_id=bus.id,quantity=3,status="confirmed",expires_at="2099-01-01T00:00:00Z")]
    set_weather(world)
    assert arriving.status=="weather_held" and bus.available
    assert world.offers[0].status=="invalidated" and world.reservations[0].status=="released"
    assert not validate_allocations(world,[allocation])["valid"]
    assert build_candidate(world,Strategy()).recovered_passengers==0
    set_weather(world,condition="clear",visibility_m=None,landing_allowed=True)
    assert arriving.available and build_candidate(world,Strategy()).recovered_passengers==3


def test_weather_held_manifest_cannot_claim_arrival_before_original_landing():
    world=seed_world()
    set_weather(world)
    arriving=add_resource(world,"WX-FUTURE",definition(departure=650,arrival=750))
    with pytest.raises(DomainError,match="30 minutes"):
        add_passengers(world,{
            "name":"Too early", "passenger_count":3, "group_size":3,
            "origin":"CRA", "destination":"OTP", "ready_at":600, "deadline":1300,
            "accessible":False, "budget_per_person":200.0, "original_flight_id":arriving.id, "priority":1,
        })


def test_solver_rejects_previously_valid_itinerary_after_weather_change():
    world=seed_world()
    plan=build_candidate(world,Strategy())
    allocation=next(a for a in plan.allocations if any(l.kind=="flight" for l in a.itinerary.legs))
    affected=next(l.destination for l in allocation.itinerary.legs if l.kind=="flight")
    assert validate_allocations(world,[allocation])["valid"]
    set_weather(world,affected)
    assert not validate_allocations(world,[Allocation.model_validate(allocation.model_dump())])["valid"]
    for strategy in default_strategies(world):
        assert build_candidate(world,strategy).validation["valid"]


def test_weather_is_not_a_live_provider_observation():
    world=seed_world()
    world.mode="live"
    before=world.model_dump()
    with pytest.raises(DomainError,match="Simulation edits are disabled"):
        set_weather(world)
    assert world.model_dump()==before


def test_weather_invalidates_confirmed_dependency_and_releases_entire_reservation(tmp_path):
    settings=Settings(database_path=str(tmp_path/"weather.sqlite3"),_env_file=None)
    service=RecoveryService(settings,Store(settings.database_path))
    asyncio.run(service.replan(ReplanRequest(expected_version=1,idempotency_key="weather-initial-plan")))
    world=service.store.read()
    offer=next(o for o in world.offers if any(l.kind=="flight" for l in o.itinerary.legs))
    group=next(g for g in world.groups if g.id==offer.group_id)
    affected=next(l.destination for l in offer.itinerary.legs if l.kind=="flight")
    service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="weather-accept-offer"))
    world=service.store.read()
    service.event(EventRequest(type="airport_weather",target_id=affected,value=weather(),
        expected_version=world.version,idempotency_key="weather-break-arrival"))
    after=service.store.read()
    assert next(o for o in after.offers if o.id==offer.id).status=="invalidated"
    assert all(r.status=="released" for r in after.reservations if r.offer_id==offer.id)
    assert next(g for g in after.groups if g.id==group.id).status=="affected"


@pytest.mark.parametrize("changes",[
    {"visibility_m":-1}, {"visibility_m":True}, {"wind_kts":float("nan")},
    {"landing_allowed":"false"}, {"condition":"meteor"}, {"source":"METAR"},
])
def test_invalid_weather_cannot_enter_scenario(changes):
    with pytest.raises(ValidationError):
        ScenarioChange(type="airport_weather",target_id="CRA",value=weather(**changes))
