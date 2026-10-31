from app.models import ScenarioChange
from app.seed import seed_world
from app.service import apply_change


def inbound_snapshot(world):
    flight=next(r for r in world.resources if r.id=="F201")
    groups=[g.model_dump(exclude={"token"}) for g in world.groups if g.original_flight_id==flight.id]
    return flight.model_copy(deep=True),groups


def test_departure_airport_closure_preserves_airborne_flight_but_arrival_closure_requires_diversion():
    world=seed_world()
    before_flight,before_groups=inbound_snapshot(world)
    assert before_flight.departure<=world.sim_time<before_flight.arrival
    apply_change(world,ScenarioChange(type="airport_closed",target_id="LTN"))
    flight,groups=inbound_snapshot(world)
    assert flight==before_flight and groups==before_groups
    assert sum(g["size"] for g in groups)==60
    assert all(g["status"]=="scheduled" for g in groups)
    assert not next(a for a in world.airports if a.id=="LTN").available
    assert not next(r for r in world.resources if r.id=="F110").available

    apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    flight,groups=inbound_snapshot(world)
    assert not flight.available and flight.status=="needs_diversion"
    assert all(g["status"]=="affected" for g in groups)


def test_departure_airport_closure_does_not_cancel_pending_airborne_diversion():
    world=seed_world()
    apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    before_flight,before_groups=inbound_snapshot(world)
    apply_change(world,ScenarioChange(type="airport_closed",target_id="LTN"))
    flight,groups=inbound_snapshot(world)
    assert flight==before_flight and flight.status=="needs_diversion"
    assert groups==before_groups


def test_departure_airport_closure_before_takeoff_still_cancels_future_flight():
    world=seed_world()
    world.sim_time=599
    apply_change(world,ScenarioChange(type="airport_closed",target_id="LTN"))
    flight,groups=inbound_snapshot(world)
    assert not flight.available and flight.status=="cancelled"
    assert all(g["status"]=="affected" for g in groups)
