from datetime import datetime, timedelta, timezone

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
