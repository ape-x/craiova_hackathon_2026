from datetime import datetime, timedelta, timezone
import json

import pytest

from app.config import Settings
from app.models import PassengerGroup, Resource, Strategy
from app.seed import seed_world
from app.solver import build_candidate, resource_cost
from app.store import Store


def test_live_start_archives_the_old_simulation_and_keeps_resources_empty(tmp_path):
    path=str(tmp_path/'world.sqlite3')
    previous=Store(path).read()
    live=Store(path,operation_mode='live')
    world=live.read()
    assert world.mode=='live' and world.version>previous.version
    assert world.groups==world.resources==world.offers==world.reservations==[]
    assert world.budget==0 and world.stats['total_passengers']==0
    assert world.airports[0].operational_status=='unknown'
    with live.connect() as conn:
        archived=conn.execute('SELECT data FROM world_archives').fetchone()[0]
        assert seed_world().resources[0].id in archived
    assert Store(path,operation_mode='live').read().scenario_id==world.scenario_id
    assert live.audit_history()==[]


def test_application_defaults_live_and_simulation_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("OPERATION_MODE", raising=False)
    assert Settings(_env_file=None).operation_mode == "live"
    assert Settings(_env_file=None, operation_mode="simulation").operation_mode == "simulation"
    monkeypatch.setenv("OPERATION_MODE", "simulation")
    assert Settings(_env_file=None).operation_mode == "simulation"


@pytest.mark.parametrize("mode", ["live", "simulation"])
def test_reopening_without_a_mode_preserves_state_and_idempotency(tmp_path, mode):
    path = str(tmp_path / "preserve.sqlite3")
    store = Store(path, operation_mode=mode)
    result = store.mutate("preserved-operation", {"budget": 321}, store.read().version,
                          lambda world: setattr(world, "budget", 321))
    reopened = Store(path)
    world = reopened.read()
    assert world.mode == mode and world.version == result["world"]["version"]
    assert world.scenario_id == result["world"]["scenario_id"] and world.budget == 321
    assert reopened.replay("preserved-operation", {"budget": 321})["replayed"]
    with reopened.connect() as conn:
        assert conn.execute("SELECT count(*) FROM world_archives").fetchone()[0] == 0


def test_mode_switch_never_copies_legacy_observations_into_an_archive(tmp_path):
    path = str(tmp_path / "mode-switch.sqlite3")
    store = Store(path, operation_mode="simulation")
    original = store.read().model_dump(mode="json")
    original["observations"] = [{"id": "legacy-aircraft-position"}]
    original["provider_status"]["flights"] = {"state": "connected", "payload": "legacy-feed-payload"}
    with store.connect() as conn:
        conn.execute("UPDATE world SET data=?", (json.dumps(original),))
    live = Store(path, operation_mode="live")
    with live.connect() as conn:
        archived = json.loads(conn.execute("SELECT data FROM world_archives").fetchone()[0])
    assert archived["observations"] == [] and "flights" not in archived["provider_status"]
    assert archived["groups"] == original["groups"] and archived["resources"] == original["resources"]
    assert live.read().mode == "live"


def test_legacy_archives_are_scrubbed_without_resetting_current_world(tmp_path):
    path = str(tmp_path / "old-archive.sqlite3")
    store = Store(path, operation_mode="live")
    before = store.read()
    old = seed_world().model_dump(mode="json")
    old["observations"] = [{"id": "old-aircraft-position"}]
    old["provider_status"]["flights"] = {"state": "connected"}
    with store.connect() as conn:
        conn.execute("INSERT INTO world_archives VALUES(?,?,?,?)", ("old", datetime.now(timezone.utc).isoformat(), "simulation", json.dumps(old)))
        conn.execute("DELETE FROM controls WHERE key='archive_observation_redaction_v1'")
    reopened = Store(path)
    with reopened.connect() as conn:
        archived = json.loads(conn.execute("SELECT data FROM world_archives WHERE id='old'").fetchone()[0])
    assert archived["observations"] == [] and "flights" not in archived["provider_status"]
    assert archived["groups"] == old["groups"]
    assert reopened.read().scenario_id == before.scenario_id
    assert reopened.read().version == before.version and reopened.read().mode == "live"


def live_fixture(tmp_path):
    store=Store(str(tmp_path/'live.sqlite3'),operation_mode='live')
    world=store.read()
    world.budget=1000
    world.groups=[PassengerGroup(id='TEST1',name='Test group',size=2,members=['Test 1','Test 2'],origin='CRA',destination='OTP',deadline=world.sim_time+300,ready_at=world.sim_time,budget=200,original_flight_id='REAL-FLIGHT-REF',token='test-token',adult_count=2)]
    from app.models import Airport
    world.airports.append(Airport(id='OTP',code='OTP',name='Bucharest',lat=44.57,lon=26.1,available=True))
    world.resources=[Resource(id='REAL-BUS',kind='bus',name='Operator-confirmed bus',origin='CRA',destination='OTP',departure=world.sim_time+30,arrival=world.sim_time+180,capacity=10,cost_per_person=25,source='operator_confirmed',valid_until=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),vehicle_id='OP-BUS-1')]
    return world


def test_live_planner_rejects_synthetic_or_stale_resources(tmp_path):
    world=live_fixture(tmp_path)
    assert build_candidate(world,Strategy()).recovered_passengers==2
    world.resources[0].source='synthetic'
    assert build_candidate(world,Strategy()).recovered_passengers==0
    world.resources[0].source='operator_confirmed'
    world.resources[0].valid_until=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    assert build_candidate(world,Strategy()).recovered_passengers==0


def test_group_bound_quote_keeps_exact_total_and_cannot_be_reused_by_other_group(tmp_path):
    world=live_fixture(tmp_path)
    resource=world.resources[0]
    resource.source='duffel'
    resource.capacity=2
    resource.quote_total_cost=57.45
    resource.eligible_group_ids=['TEST1']
    resource.capacity_scope='quoted_request'
    resource.requires_revalidation=True
    assert resource_cost(resource,world.groups[0])==57.45
    plan=build_candidate(world,Strategy())
    assert plan.recovered_passengers==2 and plan.total_cost==57.45
    world.groups[0].id='OTHER'
    assert build_candidate(world,Strategy()).recovered_passengers==0
