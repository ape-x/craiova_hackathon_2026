import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import CommandRequest, Offer, Reservation, ScenarioChange, Strategy
from app.scenario_authoring import add_resource
from app.scenario_schema import ResourceSchedule
from app.seed import seed_world
from app.service import RecoveryService, apply_change
from app.solver import build_candidate
from app.store import DomainError, Store


def schedule(world, resource_id, departure=None, arrival=None):
    apply_change(world, ScenarioChange(type="resource_schedule", target_id=resource_id,
                                      value={"departure": departure, "arrival": arrival}))


def resource(world, resource_id="F201"):
    return next(item for item in world.resources if item.id == resource_id)


def set_departure_weather(world, blocked=True):
    apply_change(world, ScenarioChange(type="airport_weather", target_id="LTN", value={
        "condition": "storm" if blocked else "clear", "visibility_m": None,
        "wind_kts": None, "landing_allowed": True, "departure_allowed": not blocked,
    }))


@pytest.mark.parametrize("payload", [
    {"departure": None, "arrival": None},
    {"arrival": 800},
    {"departure": 600},
    {"departure": None, "arrival": True},
    {"departure": None, "arrival": 10081},
    {"departure": -1, "arrival": None},
])
def test_schedule_patch_requires_both_keys_and_at_least_one_valid_time(payload):
    with pytest.raises(ValidationError):
        ResourceSchedule.model_validate(payload)


@pytest.mark.parametrize("sim_time", [600, 610])
def test_airborne_arrival_patch_preserves_departure_at_and_after_takeoff(sim_time):
    world = seed_world()
    world.sim_time = sim_time
    schedule(world, "F201", arrival=810)
    assert (resource(world).departure, resource(world).arrival) == (600, 810)
    assert all(group.ready_at == 840 and group.origin == "CRA"
               for group in world.groups if group.original_flight_id == "F201")


def test_future_departure_only_patch_preserves_arrival():
    world = seed_world()
    schedule(world, "F110", departure=900)
    assert (resource(world, "F110").departure, resource(world, "F110").arrival) == (900, 1080)


def test_pending_diversion_accepts_arrival_only_patch_without_restoring_flight():
    world = seed_world()
    apply_change(world, ScenarioChange(type="airport_closed", target_id="CRA"))
    schedule(world, "F201", arrival=810)
    assert resource(world).departure == 600 and resource(world).arrival == 810
    assert resource(world).status == "needs_diversion" and not resource(world).available
    assert all(group.ready_at == 840 for group in world.groups if group.original_flight_id == "F201")


def test_explicit_airborne_departure_change_is_rejected_without_mutation():
    world = seed_world()
    before = world.model_dump()
    with pytest.raises(DomainError, match="F201 is an airborne flight.*departure is fixed at 600"):
        schedule(world, "F201", departure=630, arrival=780)
    assert world.model_dump() == before


@pytest.mark.parametrize("resource_id,sim_time,arrival", [
    ("F201", 750, 810),
    ("B12", 690, 960),
    ("B12", 800, 960),
    ("H-CRA", 1320, 1920),
])
def test_completed_flight_and_started_ground_services_remain_immutable(resource_id, sim_time, arrival):
    world = seed_world()
    world.sim_time = sim_time
    before = world.model_dump()
    with pytest.raises(DomainError, match=f"{resource_id} has already departed or completed"):
        schedule(world, resource_id, arrival=arrival)
    assert world.model_dump() == before


def test_arrival_patch_cannot_move_an_airborne_flight_into_the_past():
    world = seed_world()
    world.sim_time = 650
    before = world.model_dump()
    with pytest.raises(DomainError, match="completed service"):
        schedule(world, "F201", arrival=650)
    assert world.model_dump() == before


@pytest.mark.parametrize("resource_id,sim_time", [("F201", 800), ("B12", 800)])
def test_identical_schedule_is_audited_noop_even_after_departure(resource_id, sim_time):
    world = seed_world()
    world.sim_time = sim_time
    before = world.model_dump(exclude={"events"})
    schedule(world, resource_id, arrival=resource(world, resource_id).arrival)
    assert world.model_dump(exclude={"events"}) == before
    assert world.events[-1].data["no_change"] is True


@pytest.mark.parametrize("sim_time", [600, 620, 800])
def test_weather_held_flight_can_be_rescheduled_after_its_old_departure_or_arrival(sim_time):
    world = seed_world()
    world.sim_time = 599
    set_departure_weather(world)
    assert resource(world).status == "weather_held"
    world.sim_time = sim_time
    schedule(world, "F201", departure=sim_time + 30, arrival=sim_time + 180)
    assert resource(world).status == "weather_held" and not resource(world).available
    assert all(group.ready_at == sim_time + 210 for group in world.groups
               if group.original_flight_id == "F201")
    set_departure_weather(world, blocked=False)
    assert resource(world).available and resource(world).status == "scheduled"


def test_weather_held_reschedule_cannot_create_a_takeoff_at_current_time():
    world = seed_world()
    world.sim_time = 599
    set_departure_weather(world)
    world.sim_time = 620
    before = world.model_dump()
    with pytest.raises(DomainError, match="held on the ground.*after simulation time 620"):
        schedule(world, "F201", departure=620, arrival=800)
    assert world.model_dump() == before


def test_future_cancelled_flight_schedule_patch_does_not_restore_service():
    world = seed_world()
    schedule(world, "F101", departure=700, arrival=910)
    assert resource(world, "F101").status == "cancelled"
    assert not resource(world, "F101").available
    world.sim_time = 800
    before = world.model_dump()
    with pytest.raises(DomainError, match="already departed or completed"):
        schedule(world, "F101", departure=900, arrival=1110)
    assert world.model_dump() == before


def test_arrival_delay_invalidates_missed_onward_offer_and_releases_reservations():
    world = seed_world()
    incoming = resource(world)
    bus = add_resource(world, "ONWARD", {
        "kind": "bus", "name": "Onward connection", "origin": "CRA", "destination": "OTP",
        "departure": 800, "arrival": 1000, "capacity": 10, "accessible": True,
        "cost_per_person": 20.0, "room_capacity": 1, "vehicle_id": None, "available": True,
    })
    group = next(group for group in world.groups if group.original_flight_id == incoming.id)
    group.destination = "OTP"
    group.status = "waiting"
    world.resources = [incoming, bus]
    world.groups = [group]
    plan = build_candidate(world, Strategy())
    assert plan.recovered_passengers == group.size
    group.status = "confirmed"
    world.offers = [Offer(id="ONWARD-OFFER", group_id=group.id, itinerary=plan.allocations[0].itinerary,
                          status="confirmed", expires_at="2099-01-01T00:00:00Z", created_version=1)]
    world.reservations = [Reservation(id="ONWARD-HOLD", offer_id="ONWARD-OFFER", group_id=group.id,
                                     resource_id=bus.id, quantity=group.size, status="confirmed",
                                     expires_at="2099-01-01T00:00:00Z")]
    schedule(world, incoming.id, arrival=810)
    assert incoming.departure == 600 and group.ready_at == 840
    assert world.offers[0].status == "invalidated" and world.reservations[0].status == "released"
    assert group.status == "affected"


class ScheduleAI:
    def __init__(self, events):
        self.events = events
        self.choices = 0

    async def interpret(self, text, world):
        return {"events": self.events, "strategies": [], "explanation": "Updated the schedule.", "focus_id": None}

    async def choose(self, world, candidates, request):
        self.choices += 1
        return {"plan_id": candidates[0]["id"], "explanation": "Validated plan.", "provider": "test-double"}


def test_command_rolls_back_earlier_changes_when_later_departure_edit_is_invalid(tmp_path):
    ai = ScheduleAI([
        {"type": "resource_schedule", "target_id": "F110", "value": {"departure": None, "arrival": 1110}},
        {"type": "resource_schedule", "target_id": "F201", "value": {"departure": 630, "arrival": 780}},
    ])
    settings = Settings(database_path=str(tmp_path / "schedule.sqlite3"), _env_file=None)
    service = RecoveryService(settings, Store(settings.database_path), ai)
    before = service.store.read().model_dump(exclude={"stats"})
    with pytest.raises(DomainError, match="airborne flight"):
        asyncio.run(service.command(CommandRequest(expected_version=1, idempotency_key="invalid-schedule-batch",
                                                    text="Delay these flights by thirty minutes.")))
    assert service.store.read().model_dump(exclude={"stats"}) == before
    assert ai.choices == 0


def test_reset_then_new_airborne_arrival_patch_commits_atomically(tmp_path):
    ai = ScheduleAI([
        {"type": "scenario_reset", "target_id": None, "value": "empty"},
        {"type": "airport_added", "target_id": "LTN", "value": None},
        {"type": "airport_added", "target_id": "CRA", "value": None},
        {"type": "resource_added", "target_id": "NEW-INBOUND", "value": {
            "kind": "flight", "name": "New inbound", "origin": "LTN", "destination": "CRA",
            "departure": 550, "arrival": 750, "capacity": 60, "accessible": True,
            "cost_per_person": 0.0, "room_capacity": 1, "vehicle_id": None, "available": True,
        }},
        {"type": "resource_schedule", "target_id": "NEW-INBOUND", "value": {"departure": None, "arrival": 810}},
    ])
    settings = Settings(database_path=str(tmp_path / "schedule-reset.sqlite3"), _env_file=None)
    service = RecoveryService(settings, Store(settings.database_path), ai)
    result = asyncio.run(service.command(CommandRequest(expected_version=1, idempotency_key="reset-schedule-batch",
                                                        text="Create an airborne flight and delay its arrival.")))
    world = service.store.read()
    assert result["applied"] and world.version == 2
    assert len(world.resources) == 1 and world.groups == []
    assert (resource(world, "NEW-INBOUND").departure, resource(world, "NEW-INBOUND").arrival) == (550, 810)
