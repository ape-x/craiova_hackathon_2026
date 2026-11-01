"""Separate exact Romanian inputs and atomic successful-plan persistence, offline."""
from datetime import datetime, timedelta
import json

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.models import CaseAirportRestriction
from app.providers.base import ProviderError
from app.recovery_jobs import RecoveryCancellation
from app.romanian_hotel_profile import RomanianHotelProfileError, RomanianHotelProfiles
from app.romanian_hotel_recovery import RomanianHotelRecoveryInputs, validate_romanian_hotel_job
from app.store import DomainError, Store
from test_recovery_jobs import setup, run_next
from test_romanian_hotel_recovery import romania, request, world_snapshot
from test_transfero import fixture_source_terms


def record(store):
    with store.connect() as conn:
        row=conn.execute("SELECT data FROM romanian_hotel_profiles WHERE group_id='G1'").fetchone()
    return json.loads(row[0]) if row else None


def missing(profiles, world):
    with pytest.raises(RomanianHotelProfileError) as exc:
        profiles.read(world,world.groups[0])
    assert exc.value.reason=='missing'


@pytest.fixture
def saved(romania):
    store,jobs,*_=romania
    inputs=RomanianHotelRecoveryInputs.model_validate(romania[-2])
    world=store.read()
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        jobs.romanian_hotel_profiles.save_in_transaction(conn,world,world.groups[0],inputs,'JOB-ORIGINAL',now=romania[-1]['now'])
    return store,jobs.romanian_hotel_profiles,inputs,romania


def test_exact_inputs_survive_restart_without_changing_world_or_source_evidence(saved):
    store,profiles,inputs,env=saved
    before=world_snapshot(store)
    restored=RomanianHotelProfiles(Store(store.path))
    world=store.read()
    found=restored.read(world,world.groups[0])
    assert found.model_dump(mode='json')==inputs.model_dump(mode='json')
    found.suitcases=0
    assert profiles.read(world,world.groups[0]).suitcases==2
    assert world_snapshot(store)==before
    assert set(record(store))=={'schema_version','source','scope_fingerprint','inputs','source_job_id','saved_at'}
    assert record(store)['source_job_id']=='JOB-ORIGINAL' and record(store)['saved_at']==env[-1]['now'].isoformat()
    assert all('romanian_hotel_profile' not in group.provenance for group in world.groups)
    assert not env[4].calls and not env[5].calls and not env[3].calls


@pytest.mark.parametrize('field,value',[
    ('id','G9'),('size',3),('adult_count',1),('members',['Different person A','Different person B']),
    ('source','changed_manifest'),('origin','SBZ'),('destination','LHR'),('original_flight_id','OTHER-FLIGHT'),('incident_id','OTHER'),
])
def test_party_route_and_original_flight_changes_refuse_reuse(saved,field,value):
    store,profiles,_,_=saved;world=store.read();setattr(world.groups[0],field,value)
    with pytest.raises(RomanianHotelProfileError):profiles.read(world,world.groups[0])
    assert profiles.summary(world,world.groups[0])['configured'] is False


@pytest.mark.parametrize('mutation',['scenario','epoch','case_date','case_flight','membership','mode','missing_group','duplicate_group','duplicate_membership','detached_group'])
def test_exact_operation_and_reciprocal_group_scope(saved,mutation):
    store,profiles,_,_=saved;world=store.read();group=world.groups[0]
    if mutation=='scenario':world.scenario_id='OTHER-SCENARIO'
    elif mutation=='epoch':world.epoch=(datetime.fromisoformat(world.epoch)+timedelta(minutes=1)).isoformat()
    elif mutation=='case_date':world.incidents[0].date_utc='2035-01-01'
    elif mutation=='case_flight':world.incidents[0].requested_flight_number='RO999'
    elif mutation=='membership':world.incidents[0].group_ids=['G2']
    elif mutation=='mode':world.mode='simulation'
    elif mutation=='missing_group':world.groups=world.groups[1:]
    elif mutation=='duplicate_group':world.groups.append(group.model_copy(deep=True))
    elif mutation=='duplicate_membership':world.incidents[0].group_ids.append(group.id)
    elif mutation=='detached_group':group=group.model_copy(update={'members':['Other','People']})
    with pytest.raises(RomanianHotelProfileError):profiles.read(world,group)


def test_mutable_constraints_do_not_rewrite_inputs_and_current_preflight_still_rejects(saved):
    store,profiles,inputs,env=saved;world=store.read();group=world.groups[0]
    group.ready_at=int((inputs.checkin_at-datetime.fromisoformat(world.epoch)).total_seconds()/60)+1
    group.budget=1;group.deadline+=30;group.name='Renamed';group.token='rotated'
    world.incidents[0].airport_restrictions=[CaseAirportRestriction(airport_code='OTP',operation='departures',
        report_id='REPORT',received_at=env[-1]['now'].isoformat(),description='Operator restriction')]
    exact=profiles.read(world,group)
    assert exact==inputs and profiles.summary(world,group)['requires_current_validation']
    with pytest.raises(DomainError):
        validate_romanian_hotel_job(world,request(env,romanian_hotel_recovery=exact))
    assert not env[4].calls and not env[5].calls and not env[3].calls


def test_projected_report_read_uses_same_transaction_and_no_implicit_rebinding(saved,monkeypatch):
    store,profiles,inputs,_=saved;world=store.read()
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        monkeypatch.setattr(store,'connect',lambda: (_ for _ in ()).throw(AssertionError('unexpected connection')))
        world.groups[0].ready_at+=30
        assert profiles.read(world,world.groups[0],conn=conn)==inputs
        assert profiles.public_for_case(world,'CASE',conn=conn)[0]['configured']
        world.groups[0].origin='SBZ'
        with pytest.raises(RomanianHotelProfileError) as exc:profiles.read(world,world.groups[0],conn=conn)
        assert exc.value.reason=='stale'


def test_public_receipt_and_summary_are_reference_only_and_case_scoped(saved):
    store,profiles,inputs,env=saved;world=store.read()
    public=profiles.public_for_case(world,'CASE')
    assert public==[{'group_id':'G1','configured':True,'inputs':inputs.model_dump(mode='json'),'source_job_id':'JOB-ORIGINAL','saved_at':env[-1]['now'].isoformat()},
        {'group_id':'G2','configured':False,'reason':'missing'}]
    assert profiles.public_for_case(world.model_dump(mode='json'),'CASE')==public
    summary=profiles.summary(world,world.groups[0]);encoded=json.dumps([summary,public])
    assert summary['minimum_total_calls']==9 and summary['hotel_id']=='lpce923' and summary['onward_airport']=='OTP'
    assert summary['checkin_at']==inputs.checkin_at.isoformat() and summary['checkout_at']==inputs.checkout_at.isoformat()
    for private in ('scope_fingerprint','Fixture adult','private-token','eur_reference_estimate','quoted_at','availability','budget'):
        assert private not in encoded
    assert 'suitcases' not in json.dumps(summary) and 'handbags' not in json.dumps(summary)


@pytest.mark.parametrize('corruption',['bad_json','large','uk_inputs','cab_inputs','extra_quote','naive','reversed','boolean','source','saved_at','version'])
def test_corrupt_or_wrong_kind_profile_fails_closed(saved,corruption):
    store,profiles,_,_=saved;raw=record(store)
    if corruption=='bad_json':encoded='PRIVATE broken JSON'
    elif corruption=='large':encoded=' '*8193
    else:
        if corruption=='uk_inputs':raw['inputs']={'pickup_at':'2035-01-01T10:00:00Z','onward_airport_code':'LHR'}
        elif corruption=='cab_inputs':raw['inputs']={'group_id':'G1','destination_airport':'CRA','pickup_at':'2035-01-01T10:00:00Z','suitcases':1,'handbags':0}
        elif corruption=='extra_quote':raw['inputs']['supplier_quote']='PRIVATE fare'
        elif corruption=='naive':raw['inputs']['checkin_at']='2035-01-01T10:00:00'
        elif corruption=='reversed':raw['inputs']['checkout_at']=raw['inputs']['checkin_at']
        elif corruption=='boolean':raw['inputs']['suitcases']=True
        elif corruption=='source':raw['source']='booked'
        elif corruption=='saved_at':raw['saved_at']='2035-01-01T10:00:00'
        elif corruption=='version':raw['schema_version']=True
        encoded=json.dumps(raw)
    with store.connect() as conn:conn.execute("UPDATE romanian_hotel_profiles SET data=? WHERE group_id='G1'",(encoded,))
    world=store.read()
    with pytest.raises(RomanianHotelProfileError) as exc:profiles.read(world,world.groups[0])
    assert exc.value.reason=='invalid' and 'PRIVATE' not in str(exc.value)
    assert profiles.public_for_case(world,'CASE')[0]=={'group_id':'G1','configured':False,'reason':'invalid'}


def test_profile_save_requires_transaction_and_failed_update_keeps_original(saved):
    store,profiles,inputs,env=saved;world=store.read();original=record(store);before=world_snapshot(store)
    with store.connect() as conn:
        with pytest.raises(RuntimeError,match='active transaction'):
            profiles.save_in_transaction(conn,world,world.groups[0],inputs,'NO-TRANSACTION')
    with pytest.raises(RuntimeError,match='rollback'):
        with store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed=inputs.model_copy(update={'suitcases':0})
            profiles.save_in_transaction(conn,world,world.groups[0],changed,'NEW-JOB',now=env[-1]['now'])
            assert profiles.read(world,world.groups[0],conn=conn).suitcases==0
            raise RuntimeError('rollback')
    assert record(store)==original and world_snapshot(store)==before


@pytest.mark.asyncio
async def test_success_saves_only_complete_chosen_configuration_and_replay_cannot_resave(romania):
    store,jobs,_,_,coach,_,_,inputs,_=romania
    before=world_snapshot(store);missing(jobs.romanian_hotel_profiles,store.read())
    req=request(romania);started=jobs.start('CASE',req)
    missing(jobs.romanian_hotel_profiles,store.read())
    result=await run_next(jobs)
    assert result['status']=='completed' and result['calls_dispatched']==8,result
    world=store.read();saved=record(store)
    assert [leg.kind for leg in world.offers[-1].itinerary.legs]==['bus','hotel','bus','flight']
    assert jobs.romanian_hotel_profiles.read(world,world.groups[0]).model_dump(mode='json')==RomanianHotelRecoveryInputs.model_validate(inputs).model_dump(mode='json')
    assert saved['source_job_id']==started['job']['id'] and saved['saved_at']
    assert world.groups[1].model_dump(mode='json')==before['groups'][1]
    assert 'romanian_hotel_profile' not in world.groups[0].provenance
    assert jobs.start('CASE',req)['replayed'] and record(store)==saved and len(coach.calls)==2


@pytest.mark.asyncio
@pytest.mark.parametrize('problem',['no_seats','incomplete_hotel','unknown','cancel','expired','ledger_rollback'])
async def test_failed_or_partial_attempt_never_creates_profile(romania,monkeypatch,problem):
    store,jobs,ai,_,coach,hotel,_,_,clock=romania
    if problem=='no_seats':coach.seats=1
    elif problem=='incomplete_hotel':hotel.complete=False
    elif problem=='unknown':
        async def fail(query):raise ProviderError('Offline unknown',provider='Transfero',code='http_507')
        coach.handler=fail
    elif problem=='expired':
        async def expire():clock['now']+=timedelta(seconds=121)
        ai.before_choose=expire
    elif problem=='ledger_rollback':
        monkeypatch.setattr(jobs.ledger,'complete_in_transaction',lambda *a: (_ for _ in ()).throw(RuntimeError('Offline ledger rollback')))
    before=world_snapshot(store);started=jobs.start('CASE',request(romania))
    if problem=='cancel':
        async def cancel(query):jobs.cancel(started['job']['id'],RecoveryCancellation(idempotency_key='romanian-profile-cancel'))
        coach.handler=cancel
    result=await run_next(jobs)
    assert result['status'] in {'blocked','unknown_outcome','cancelled','needs_review'},result
    assert world_snapshot(store)==before
    missing(jobs.romanian_hotel_profiles,store.read())


@pytest.mark.asyncio
async def test_profile_write_failure_rolls_back_world_ledger_receipt_and_partial_profile(romania,monkeypatch):
    store,jobs,*_=romania;before=world_snapshot(store)
    original=jobs.romanian_hotel_profiles.save_in_transaction
    def fail_after_save(conn,world,group,inputs,job_id,now=None):
        original(conn,world,group,inputs,job_id,now=now)
        assert jobs.romanian_hotel_profiles.read(world,group,conn=conn)==inputs
        raise RuntimeError('Offline profile persistence failure')
    monkeypatch.setattr(jobs.romanian_hotel_profiles,'save_in_transaction',fail_after_save)
    jobs.start('CASE',request(romania));result=await run_next(jobs)
    assert result['status']=='needs_review' and result['calls_dispatched']==8,result
    assert world_snapshot(store)==before and record(store) is None
    with store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM idempotency WHERE key LIKE 'recovery-job-apply:%'").fetchone()
        assert not conn.execute("SELECT 1 FROM recovery_jobs WHERE status='completed'").fetchone()


@pytest.mark.asyncio
async def test_failed_new_configuration_keeps_previous_successful_inputs(romania):
    store,jobs,_,_,coach,_,_,inputs,_=romania
    jobs.start('CASE',request(romania));first=await run_next(jobs)
    assert first['status']=='completed',first
    saved=record(store);before=world_snapshot(store)
    async def unknown(query):raise ProviderError('Offline timeout',provider='Transfero',code='timeout')
    coach.handler=unknown
    jobs.start('CASE',request(romania,idempotency_key='romanian-profile-new-failed',romanian_hotel_recovery=inputs|{'suitcases':1,'handbags':1}))
    result=await run_next(jobs)
    assert result['status']=='unknown_outcome' and record(store)==saved and world_snapshot(store)==before
    assert jobs.romanian_hotel_profiles.read(store.read(),store.read().groups[0]).suitcases==2


def test_authenticated_singular_review_is_read_only_and_exact_scope(saved):
    store,_,inputs,_=saved;before=world_snapshot(store)
    settings=Settings(_env_file=None,environment='test',operation_mode='live',database_path=store.path,operator_token='offline-profile-token')
    with TestClient(create_app(settings)) as client:
        path='/api/incidents/CASE/groups/G1/romanian-hotel-recovery-profile';headers={'Authorization':'Bearer offline-profile-token'}
        assert client.get(path).status_code==401
        response=client.get(path,headers=headers)
        assert response.status_code==200,response.text
        assert response.json()=={'world_version':before['version'],'incident_id':'CASE','group_id':'G1','configured':True,
            'inputs':inputs.model_dump(mode='json'),'source_job_id':'JOB-ORIGINAL','saved_at':record(store)['saved_at']}
        assert client.get(path.replace('G1','G2'),headers=headers).json()['configured'] is False
        assert client.get(path.replace('CASE','MISSING'),headers=headers).status_code==404
        assert client.get(path.replace('G1','missing'),headers=headers).status_code==404
    assert world_snapshot(store)==before


@pytest.mark.asyncio
async def test_quote_only_work_does_not_save_or_modify_world(romania):
    from app.providers.transfero import TransferoSearchRequest
    from app.real_data import RealDataRequest
    store,jobs,_,_,coach,hotel,_,raw,_=romania
    inputs=RomanianHotelRecoveryInputs.model_validate(raw);before=world_snapshot(store)
    source=await coach.search(TransferoSearchRequest(direction='otp_to_hotel',travel_date=inputs.checkin_at.date(),
        adults=2,suitcases=inputs.suitcases,handbags=inputs.handbags,standard_baggage_confirmed=True))
    rooms=await hotel.hotel_offers('lpce923',RealDataRequest(airport_code='OTP',destination_code='CRA',
        checkin=inputs.checkin_at.date(),checkout=inputs.checkout_at.date(),adults=2,rooms=1))
    assert source['options'] and rooms
    assert record(store) is None and world_snapshot(store)==before
    missing(jobs.romanian_hotel_profiles,store.read())


def test_uk_and_initial_cab_profiles_cannot_become_romanian_setup(romania):
    from app.airport_cab_recovery import AirportCabRecoveryInput
    from app.hotel_recovery import HotelRecoveryInputs
    from app.hotel_recovery_profile import build_hotel_recovery_profile
    store,jobs,*_=romania;world=store.read();group=world.groups[0]
    inputs=RomanianHotelRecoveryInputs.model_validate(romania[-2])
    point={'label':'Offline point','latitude':51.47,'longitude':-.45,'confirmed':True}
    uk=HotelRecoveryInputs(checkin_at=inputs.checkin_at,checkout_at=inputs.checkout_at,
        pickup_at=inputs.checkin_at-timedelta(hours=3),rooms=1,minimum_rest_minutes=480,suitcases=1,
        initial_point=point,onward_airport_code='LHR',airport_processing_minutes=120,airport_dropoff=point)
    group.provenance['hotel_recovery_profile']=build_hotel_recovery_profile(world,group,uk)
    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        jobs.airport_cab_profiles.save_in_transaction(conn,world,group,AirportCabRecoveryInput(group_id='G1',destination_airport='CRA',
            pickup_at=inputs.checkin_at-timedelta(hours=3),suitcases=1,handbags=0),'CAB-ONLY')
        assert jobs.airport_cab_profiles.read(world,group,conn=conn).suitcases==1
        with pytest.raises(RomanianHotelProfileError) as exc:jobs.romanian_hotel_profiles.read(world,group,conn=conn)
    assert exc.value.reason=='missing' and record(store) is None
