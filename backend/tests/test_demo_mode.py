"""Demo demand uses the real-supply workflow, without any provider calls."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.demo import DemoCaseDefinition, build_demo_case
from app.models import RecoveryIncident, Resource, World
from app.seed import empty_live_world, seed_demo_world, seed_world
from app.store import DomainError, Store


def definition(**changes):
    now = datetime.now(timezone.utc)
    return DemoCaseDefinition(**{
        "ready_at": now + timedelta(days=1),
        "deadline": now + timedelta(days=2),
        **changes,
    })


def test_demo_configuration_and_seed_keep_all_supply_empty(monkeypatch):
    monkeypatch.setenv("OPERATION_MODE", "demo")
    assert Settings(_env_file=None).operation_mode == "demo"
    world = seed_demo_world()
    assert world.mode == "live" and world.demo
    assert world.resources == world.offers == world.reservations == world.observations == []
    assert {airport.id for airport in world.airports} == {"LHR", "OTP"}
    assert all(not airport.available and airport.operational_status == "unknown" for airport in world.airports)
    assert world.budget == 2000
    assert len(world.groups) == len(world.incidents) == 1
    group, incident = world.groups[0], world.incidents[0]
    assert (group.origin, group.destination, group.size, group.adult_count) == ("LHR", "OTP", 2, 2)
    assert group.source == "synthetic" and group.provenance["fictional"] is True
    assert all("Fictional adult" in member for member in group.members)
    assert group.original_flight_id == group.incident_id == incident.id
    assert incident.id.startswith("DEMO-") and incident.group_ids == [group.id]
    assert incident.source == "demo_scenario" and incident.description.startswith("DEMO — hypothetical incident.")
    assert incident.flight is incident.flight_ref is None and incident.flight_context_state == "not_applicable"
    midnight = datetime.fromisoformat(world.epoch)
    assert midnight.hour == midnight.minute == midnight.second == 0 and midnight.utcoffset() == timedelta(0)
    assert midnight + timedelta(minutes=group.ready_at) == midnight + timedelta(days=1, hours=8)
    assert midnight + timedelta(minutes=group.deadline) == midnight + timedelta(days=2, hours=22)
    assert len(world.events) == 1 and world.events[0].version == world.version


@pytest.mark.parametrize("reopen_mode", [None, "demo"])
def test_demo_reopen_and_replay_preserve_state_without_flight_cache(tmp_path, monkeypatch, reopen_mode):
    def no_flight_cache(*args):
        pytest.fail("Fictional incidents must not hydrate from FR24")

    monkeypatch.setattr(Store, "_incident_flight", no_flight_cache)
    path = str(tmp_path / "demo.sqlite3")
    store = Store(path, operation_mode="demo")
    initial = store.read()
    response = store.mutate("demo-local-edit", {"budget": 2500}, initial.version,
        lambda world: setattr(world.groups[0], "budget", 2500))
    reopened = Store(path, operation_mode=reopen_mode)
    current = reopened.read()
    assert current.demo and current.mode == "live" and current.scenario_id == initial.scenario_id
    assert current.version == response["world"]["version"] and current.groups[0].budget == 2500
    assert current.groups[0].ready_at == initial.groups[0].ready_at
    assert current.incidents[0].flight_context_state == "not_applicable"
    replay = reopened.replay("demo-local-edit", {"budget": 2500})
    assert replay["replayed"] and replay["world"]["incidents"][0]["flight_ref"] is None
    assert replay["world"]["incidents"][0]["flight_context_state"] == "not_applicable"
    with reopened.connect() as conn:
        assert conn.execute("SELECT count(*) FROM world_archives").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM flight_lookups").fetchone()[0] == 0


@pytest.mark.parametrize("source_mode,target_mode", [
    ("simulation", "demo"), ("live", "demo"), ("demo", "live"), ("demo", "simulation"),
])
def test_mode_switch_archives_demo_distinctly_and_keeps_provider_budget(tmp_path, source_mode, target_mode):
    path = str(tmp_path / "switch.sqlite3")
    original = Store(path, operation_mode=source_mode)
    before = original.read()
    original.mutate("previous-request", {"edit": True}, before.version, lambda world: None)
    assert original.reserve_fr24_request(0, 80, 4000, 15) is None
    switched = Store(path, operation_mode=target_mode)
    current = switched.read()
    assert current.scenario_id != before.scenario_id and current.version > before.version
    assert current.demo is (target_mode == "demo")
    assert current.mode == ("simulation" if target_mode == "simulation" else "live")
    assert switched.replay("previous-request", {"edit": True}) is None
    assert switched.poll_usage() == 1 and switched.fr24_credits_reserved() == 15
    with switched.connect() as conn:
        mode, data = conn.execute("SELECT mode,data FROM world_archives").fetchone()
    archived = json.loads(data)
    assert mode == source_mode and archived["scenario_id"] == before.scenario_id
    assert archived["demo"] is (source_mode == "demo")
    if source_mode == "demo":
        assert archived["incidents"][0]["flight_context_state"] == "not_applicable"
        assert archived["incidents"][0]["flight_ref"] is None
    if target_mode != "simulation":
        assert current.resources == []


def test_add_demo_case_preserves_other_case_and_supply_and_groups_families():
    world = seed_demo_world()
    first_group, first_case = world.groups[0].model_dump(), world.incidents[0].model_dump()
    world.resources.append(Resource(id="SOURCE-FLIGHT", kind="flight", name="Source fixture",
        origin="LHR", destination="OTP", departure=world.groups[0].ready_at + 60,
        arrival=world.groups[0].ready_at + 240, capacity=2, cost_per_person=250,
        source="test_provider", provenance={"fixture": True}))
    before_supply = [resource.model_dump() for resource in world.resources]
    receipt = build_demo_case(world, definition(passengers=5, group_size=2, group_budget_eur="1234.50"))
    groups = [group for group in world.groups if group.id in receipt["group_ids"]]
    assert [group.size for group in groups] == [2, 2, 1]
    assert all(group.adult_count == group.size and group.budget == 1234.50 for group in groups)
    assert all(group.incident_id == receipt["incident_id"] for group in groups)
    assert world.groups[0].model_dump() == first_group and world.incidents[0].model_dump() == first_case
    assert [resource.model_dump() for resource in world.resources] == before_supply
    assert len({group.name for group in world.groups}) == len(world.groups)
    assert world.budget == 2000 + 3 * 1234.50


@pytest.mark.parametrize("changes", [
    {"origin": "NOT_AN_AIRPORT"}, {"origin": "OTP"}, {"passengers": 501},
    {"passengers": True}, {"group_size": 13}, {"group_budget_eur": "1.001"},
    {"ready_at": "2030-01-01T08:00:00"}, {"description": "valid\ntext"},
])
def test_invalid_demo_definition_is_rejected(changes):
    with pytest.raises(ValidationError):
        definition(**changes)


@pytest.mark.parametrize("changes", [
    {"ready_at": datetime.now(timezone.utc) - timedelta(minutes=1)},
    {"deadline": datetime.now(timezone.utc) + timedelta(days=8)},
    {"passengers": 500, "group_size": 1},
])
def test_failed_case_validation_leaves_all_existing_state_unchanged(changes):
    world = seed_demo_world()
    before = world.model_dump()
    with pytest.raises(DomainError):
        build_demo_case(world, definition(**changes))
    assert world.model_dump() == before


@pytest.mark.parametrize("factory", [empty_live_world, seed_world])
def test_fictional_case_builder_is_not_available_in_other_modes(factory):
    world = factory()
    before = world.model_dump()
    with pytest.raises(DomainError, match="require demo mode"):
        build_demo_case(world, definition())
    assert world.model_dump() == before


def test_demo_schema_cannot_claim_provider_flight_evidence():
    world = seed_demo_world()
    case = world.incidents[0].model_dump()
    with pytest.raises(ValidationError, match="fictional demo incident"):
        RecoveryIncident.model_validate({**case, "flight_ref": {"lookup_id": "invented", "selection_id": "invented"}})
    with pytest.raises(ValidationError, match="sourced flight reference"):
        RecoveryIncident.model_validate({**case, "source": "operator_report"})
    with pytest.raises(ValidationError, match="live inventory workflow"):
        World.model_validate({**world.model_dump(), "mode": "simulation"})
    with pytest.raises(ValidationError, match="require a demo operation"):
        World.model_validate({**world.model_dump(), "demo": False})


def test_existing_worlds_without_demo_marker_remain_compatible():
    payload = empty_live_world().model_dump()
    payload.pop("demo")
    world = World.model_validate(payload)
    assert world.demo is False and world.mode == "live"


def test_demo_backup_restores_fictional_context_and_idempotent_receipts(tmp_path):
    source, destination = tmp_path / "demo.sqlite3", tmp_path / "restored.sqlite3"
    store = Store(str(source), operation_mode="demo")
    original = store.read()
    store.mutate("demo-before-backup", {"budget": 2300}, original.version,
        lambda world: setattr(world, "budget", 2300))
    script = Path(__file__).resolve().parents[2] / "scripts" / "backup.py"
    result = subprocess.run([sys.executable, str(script), str(source), str(destination)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    restored = Store(str(destination), operation_mode="demo")
    world = restored.read()
    assert world.scenario_id == original.scenario_id and world.demo and world.budget == 2300
    assert world.incidents[0].flight_context_state == "not_applicable"
    assert world.incidents[0].flight_ref is None and world.resources == []
    replay = restored.replay("demo-before-backup", {"budget": 2300})
    assert replay["replayed"] and replay["world"]["incidents"][0]["flight_context_state"] == "not_applicable"
