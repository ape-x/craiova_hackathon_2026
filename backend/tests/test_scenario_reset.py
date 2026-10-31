import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import CommandRequest, ScenarioChange
from app.service import RecoveryService, apply_change
from app.store import DomainError, Store


RESET = {"type": "scenario_reset", "target_id": None, "value": "empty"}


class SceneAI:
    def __init__(self, events, reject=False):
        self.events, self.reject, self.choices, self.interpretations = events, reject, 0, 0

    async def interpret(self, text, world):
        self.interpretations += 1
        return {"events": self.events, "strategies": [], "explanation": "Scenariu nou de la zero.", "focus_id": None}

    async def choose(self, world, candidates, request):
        self.choices += 1
        if self.reject:
            raise DomainError(502, "Decision provider unavailable")
        return {"plan_id": candidates[0]["id"], "explanation": "Selected a validated journey.", "provider": "test-double"}


def setup(tmp_path, events, reject=False):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "reset.sqlite3"))
    ai = SceneAI(events, reject)
    service = RecoveryService(settings, Store(settings.database_path), ai)
    request = CommandRequest(expected_version=1, idempotency_key="new-scene-reset", text="Resetează complet și creează un scenariu nou.")
    return service, ai, request


def authored_scene():
    return [RESET,
        {"type": "airport_added", "target_id": "OPO", "value": None},
        {"type": "airport_added", "target_id": "LIS", "value": None},
        {"type": "resource_added", "target_id": "B-DEMO", "value": {
            "kind": "bus", "name": "Porto to Lisbon demo", "origin": "OPO", "destination": "LIS",
            "departure": 660, "arrival": 900, "capacity": 12, "accessible": True,
            "cost_per_person": 40.0, "room_capacity": 1, "vehicle_id": None, "available": True}},
        {"type": "passengers_added", "target_id": None, "value": {
            "name": "Demo families", "passenger_count": 12, "group_size": 3, "origin": "OPO", "destination": "LIS",
            "ready_at": 600, "deadline": 1200, "accessible": False, "budget_per_person": 200.0,
            "original_flight_id": None, "priority": 1}}]


@pytest.mark.parametrize("kind", ["empty", "seed"])
def test_command_reset_is_atomic_monotonic_and_revokes_old_tokens(tmp_path, kind):
    service, ai, request = setup(tmp_path, [{**RESET, "value": kind}])
    before = service.store.read()
    token = before.groups[0].token
    result = asyncio.run(service.command(request))
    current = service.store.read()
    assert result["applied"] and result["explanation"] == "Scenariu nou de la zero."
    assert current.version == before.version + 1 and current.scenario_id != before.scenario_id
    assert not current.offers and not current.reservations and not current.decisions
    assert ai.choices == 0
    if kind == "empty":
        assert not current.resources and not current.groups and not current.airports
        assert current.epoch == before.epoch and current.sim_time == before.sim_time
    else:
        assert len(current.groups) == len(before.groups)
    with pytest.raises(DomainError) as error:
        service.passenger(token)
    assert error.value.status == 404
    assert asyncio.run(service.command(request))["replayed"]
    assert ai.interpretations == 1 and service.store.read().scenario_id == current.scenario_id


def test_reset_and_new_inventory_commit_as_one_new_scenario(tmp_path):
    service, ai, request = setup(tmp_path, authored_scene())
    before = service.store.read()
    result = asyncio.run(service.command(request))
    current = service.store.read()
    assert current.scenario_id != before.scenario_id and current.version == 2
    assert {a.id for a in current.airports} == {"OPO", "LIS"}
    assert [r.id for r in current.resources] == ["B-DEMO"]
    assert sum(g.size for g in current.groups) == 12 and len(current.offers) == 4
    assert result["decision"]["mode"] == "ai" and ai.choices == 1
    assert not {g.token for g in current.groups} & {g.token for g in before.groups}


@pytest.mark.parametrize("failure", ["invalid_event", "decision_failure", "late_reset", "duplicate_reset"])
def test_failed_new_scene_never_erases_current_scene(tmp_path, failure):
    events = authored_scene()
    if failure == "invalid_event":
        events.append({"type": "resource_unavailable", "target_id": "MISSING", "value": None})
    elif failure == "late_reset":
        events = [events[1], RESET]
    elif failure == "duplicate_reset":
        events.append(RESET)
    service, ai, request = setup(tmp_path, events, reject=failure == "decision_failure")
    before = service.store.read().model_dump()
    with pytest.raises(DomainError):
        asyncio.run(service.command(request))
    assert service.store.read().model_dump() == before


@pytest.mark.parametrize("target,value", [("CRA", "empty"), (None, None), (None, "anything"), (None, True)])
def test_reset_contract_rejects_ambiguous_targets_or_modes(target, value):
    with pytest.raises(ValidationError):
        ScenarioChange(type="scenario_reset", target_id=target, value=value)


def test_reset_is_unavailable_for_real_inventory(tmp_path):
    service, _, _ = setup(tmp_path, [])
    # Exercise the defensive boundary even in simulation-only model releases.
    world = service.store.read().model_copy(update={"mode": "live"})
    before = world.model_dump()
    with pytest.raises(DomainError):
        apply_change(world, ScenarioChange(**RESET))
    assert world.model_dump() == before
