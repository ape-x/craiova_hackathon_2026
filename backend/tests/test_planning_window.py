"""Long-lived live operations keep exact source dates; all providers are fixtures."""
from datetime import datetime, timedelta

import pytest

from app.live_data import relative_minute
from app.models import ConstraintRequest, ScenarioChange
from app.planning_window import planning_horizon_end
from app.seed import empty_live_world
from app.service import apply_change
from app.store import DomainError
from test_hotel_recovery_jobs import package, package_request, run_next
from test_recovery_jobs import setup
from test_live_service import ops, manifest_request, resource_request
from test_live_travel import live, search, select
from test_transfer_planning import setup as ground_setup, import_request


def age_operation(store, days=14):
    """An isolated fixture with an old, stable epoch and unchanged actual dates."""
    shift = days * 1440
    def age(world):
        world.epoch = (datetime.fromisoformat(world.epoch) - timedelta(days=days)).isoformat()
        for group in world.groups:
            group.ready_at += shift
            group.deadline += shift
    store.mutate("age-operation-fixture", {}, store.read().version, age)
    return store.read().epoch


def test_live_window_moves_but_epoch_and_simulation_window_do_not():
    world = empty_live_world()
    world.sim_time = 20 * 1440 + 123
    epoch = world.epoch
    end = world.sim_time + 7 * 1440
    timestamp = datetime.fromisoformat(epoch) + timedelta(minutes=end)
    assert planning_horizon_end(world) == end
    assert relative_minute(world, timestamp, rounding="up") == end
    with pytest.raises(DomainError):
        relative_minute(world, timestamp + timedelta(minutes=1), rounding="down")
    with pytest.raises(DomainError):
        relative_minute(world, datetime.fromisoformat(epoch) - timedelta(minutes=1), rounding="up")
    assert world.epoch == epoch
    world.mode = "simulation"
    assert planning_horizon_end(world) == 7 * 1440
    with pytest.raises(DomainError):
        relative_minute(world, timestamp, rounding="up")


def test_live_manifest_service_and_passenger_deadline_work_after_two_weeks(ops):
    epoch = age_operation(ops.store)
    ops.manifest(manifest_request(ops))
    result = ops.resource(resource_request(ops))
    group = ops.store.read().groups[0]
    resource = ops.store.read().resources[0]
    assert min(group.ready_at, resource.departure) > 7 * 1440
    original_evidence = resource.provenance.copy()
    deadline = ops.store.read().sim_time + 2 * 1440
    request = ConstraintRequest(idempotency_key="later-deadline-fixture", deadline=deadline)
    ops.recovery.constraints(group.token, request)
    after = ops.store.read()
    assert after.groups[0].deadline == deadline and after.epoch == epoch
    assert after.resources[0].provenance == original_evidence
    assert result["applied"]
    assert ops.recovery.constraints(group.token, request)["replayed"]
    invalid = ConstraintRequest(idempotency_key="invalid-deadline-fixture", deadline=planning_horizon_end(after)+1)
    with pytest.raises(DomainError):
        ops.recovery.constraints(group.token, invalid)
    assert ops.store.read().groups[0].deadline == deadline


def test_early_live_deadline_is_not_subject_to_simulation_ten_am_minimum():
    world = empty_live_world()
    from app.models import PassengerGroup
    world.sim_time = 60
    world.groups.append(PassengerGroup(id="G1", name="Operator group", size=1, origin="CRA", destination="OTP",
        original_flight_id="RO321", token="fixture-token", deadline=400, ready_at=80, budget=500))
    request = ConstraintRequest(idempotency_key="early-deadline-fixture", deadline=120)
    apply_change(world, ScenarioChange(type="group_deadline", target_id="G1", value=request.deadline))
    assert world.groups[0].deadline == 120
    with pytest.raises(DomainError):
        apply_change(world, ScenarioChange(type="group_deadline", target_id="G1", value=59))


@pytest.mark.asyncio
async def test_source_flight_import_retains_absolute_schedule_after_two_weeks(live):
    epoch = age_operation(live.store)
    result = await live.travel.search(search())
    imported = live.travel.import_quote(select(live, result))
    assert len(imported["resource_ids"]) == 1 and not imported["replayed"]
    resource = live.store.read().resources[0]
    assert resource.departure > 7 * 1440
    actual = datetime.fromisoformat(epoch) + timedelta(minutes=resource.departure)
    assert actual == datetime.fromisoformat(live.provider.item["departure_at"])
    assert live.store.read().epoch == epoch


@pytest.mark.asyncio
async def test_dated_ground_quote_can_import_after_two_weeks(ground_setup):
    store, sources, service, request, clock = ground_setup
    epoch = age_operation(store)
    quoted = await service.quote(request())
    assert quoted["state"] == "ready", quoted
    result = service.import_quote(quoted["quote_id"], import_request(store))
    resource = next(item for item in store.read().resources if item.id in result["resource_ids"])
    assert resource.departure > 7 * 1440
    assert datetime.fromisoformat(epoch)+timedelta(minutes=resource.departure) == request().pickup_at
    assert store.read().epoch == epoch and len(sources.calls) == 3


@pytest.mark.asyncio
async def test_complete_hotel_chain_after_two_weeks_keeps_epoch_profile_and_ten_calls(package):
    store, jobs, ai, travel, sources, fx, inputs = package
    epoch = age_operation(store)
    before = store.read()
    jobs.start("CASE", package_request(store, inputs))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    assert result["calls_dispatched"] == 10 and len(result["steps"]) == 10
    after = store.read()
    assert after.epoch == epoch and after.scenario_id == before.scenario_id
    assert after.groups[1].model_dump() == before.groups[1].model_dump()
    assert len(after.resources) - len(before.resources) == 4
    assert all(item.departure > 7 * 1440 for item in after.resources if item.id not in {r.id for r in before.resources})
    from app.hotel_recovery_profile import read_hotel_recovery_profile
    saved = read_hotel_recovery_profile(after, after.groups[0])
    assert datetime.fromisoformat(saved["checkin_at"]) == datetime.fromisoformat(inputs["checkin_at"])
    assert not any(item.booking_status == "confirmed" for item in after.resources)
