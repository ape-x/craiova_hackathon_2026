"""Recovery jobs use isolated SQLite and explicit fake adapters; never live APIs."""
import asyncio
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.airport_catalog import lookup_airport
from app.config import Settings
from app.hotel_location import coordinate_timezone, property_zone
from app.models import Airport, IncidentFlightReference, PassengerGroup, RecoveryIncident, Strategy
from app.providers import ProviderError
from app.recovery_jobs import RecoveryCancellation, RecoveryJobRequest, RecoveryJobs
from app.recovery_ledger import RecoveryLedger
from app.service import RecoveryService
from app.store import DomainError, Store, utcnow


class FakeAI:
    configured=True
    def __init__(self):
        self.plans=[]
        self.decisions=[]
        self.before_choose=None

    async def plan_recovery_queries(self,world,options,request):
        self.plans.append((world,options,request))
        selected={}
        for option in options:
            selected.setdefault(option["group_id"],option["id"])
        strategies=[Strategy(id=value,label=value,objective=value).model_dump() for value in ("balanced","fastest","lowest_cost")]
        return {"query_ids":list(selected.values())[:request["max_search_calls"]],"strategies":strategies,"explanation":"Mock search strategy"}

    async def choose(self,world,candidates,request):
        self.decisions.append((world,candidates,request))
        if self.before_choose:
            await self.before_choose()
        choice=max(candidates,key=lambda item:item["recovered_passengers"])
        return {"plan_id":choice["id"],"provider":"Mock Decisions","explanation":"Mock validated selection"}


class FakeTravel:
    flight_configured=True
    flight_source="Nuitee"
    def __init__(self):
        self.calls=[]
        self.handler=None

    async def search(self,query):
        self.calls.append(query)
        if self.handler:
            return await self.handler(query)
        airport=lookup_airport(query.origin_code)
        zone=property_zone(coordinate_timezone(airport["lat"],airport["lon"]))
        departure=datetime.combine(query.departure_date,time(11),zone).astimezone(timezone.utc)
        arrival=departure+timedelta(hours=2)
        identity=query.origin_code+query.destination_code+query.departure_date.isoformat()
        return {"state":"ready","source":"Nuitee","items":[{"id":identity,"provider":"nuitee","provider_offer_id":identity,
            "name":"Supplier flight offer","origin":query.origin_code,"destination":query.destination_code,
            "departure_at":departure.isoformat(),"arrival_at":arrival.isoformat(),"total_price":"100.00","currency":"EUR",
            "passengers":query.adults,"expires_at":(utcnow()+timedelta(hours=1)).isoformat(),"quoted_at":utcnow().isoformat(),
            "mode":"live","accessibility_confirmed":False,"segments":[{"origin":query.origin_code,"destination":query.destination_code,
                "departure_at":departure.isoformat(),"arrival_at":arrival.isoformat()}]}]}


@pytest.fixture
def setup(tmp_path):
    settings=Settings(_env_file=None,operation_mode="live",database_path=str(tmp_path/"jobs.sqlite3"))
    store=Store(settings.database_path,operation_mode="live")
    def create(world):
        world.epoch=utcnow().replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
        world.budget=1000
        world.airports=[]
        for code in ("CRA","OTP","SBZ"):
            ref=lookup_airport(code)
            world.airports.append(Airport(id=code,code=code,name=ref["name"],lat=ref["lat"],lon=ref["lon"]))
        zone=property_zone("Europe/Bucharest")
        tomorrow=utcnow().astimezone(zone).date()+timedelta(days=1)
        epoch=datetime.fromisoformat(world.epoch)
        ready=int((datetime.combine(tomorrow,time(9),zone)-epoch).total_seconds()/60)
        for group_id in ("G1","G2"):
            world.groups.append(PassengerGroup(id=group_id,name=group_id,size=1,members=["Private fixture name"],origin="CRA",destination="OTP",
                ready_at=ready,deadline=ready+600,budget=500,original_flight_id="RO321",source="manifest_import",adult_count=1,
                token=group_id+"-private-token-"+"x"*24,incident_id="CASE"))
        world.incidents=[RecoveryIncident(id="CASE",created_at=utcnow().isoformat(),kind="destination_unavailable",description="Operator reports SBZ unavailable; passengers are reported at CRA.",
            requested_flight_number="RO321",date_utc=utcnow().date().isoformat(),flight_ref=IncidentFlightReference(lookup_id="lookup-fixture",selection_id="flight-fixture"),
            unavailable_airport_code="SBZ",group_ids=["G1","G2"],status="open")]
    store.mutate("fixture-job-world",{},store.read().version,create)
    ai,travel=FakeAI(),FakeTravel()
    jobs=RecoveryJobs(settings,store,RecoveryService(settings,store,ai),travel,ai)
    return settings,store,jobs,ai,travel


def request(store,**changes):
    return RecoveryJobRequest(**({"expected_version":store.read().version,"idempotency_key":"job-start-fixture-key","group_ids":["G1"]}|changes))


async def run_next(jobs):
    claimed=jobs.ledger.claim_next()
    assert claimed
    await jobs.run(*claimed)
    return jobs.get(claimed[0]["id"])["job"]


@pytest.mark.asyncio
async def test_job_sources_flights_compares_real_adapter_candidates_and_commits_once(setup):
    _,store,jobs,ai,travel=setup
    req=request(store)
    before=store.read()
    started=jobs.start("CASE",req)
    assert started["job"]["status"]=="queued" and started["job"]["calls_dispatched"]==0
    assert store.read().version==before.version
    result=await run_next(jobs)
    assert result["status"]=="completed",result
    assert result["calls_dispatched"]==3 and [step["status"] for step in result["steps"]]==["succeeded"]*3
    assert result["result"]["recovered_passengers"]==1 and result["result"]["exception_passengers"]==0
    world=store.read()
    assert world.version==before.version+1 and world.groups[1]==before.groups[1]
    assert len(world.offers)==1 and world.offers[0].status=="proposed" and world.offers[0].group_id=="G1"
    assert all(row.source=="nuitee" and row.capacity_scope=="quoted_request" for row in world.resources)
    assert len(ai.plans)==len(ai.decisions)==len(travel.calls)==1
    assert len(ai.decisions[0][1])==3
    replay=jobs.start("CASE",req)
    assert replay["replayed"] and replay["job"]["status"]=="completed"
    assert jobs.ledger.claim_next() is None and store.read().version==world.version
    serialized=json.dumps(result)
    assert "private-token" not in serialized and "Private fixture name" not in serialized
    assert result["result"]["source_counts"]=={"Nuitee":1}


@pytest.mark.asyncio
async def test_queued_distinct_scope_rebases_without_replacing_first_proposal(setup):
    _,store,jobs,_,_=setup
    version=store.read().version
    jobs.start("CASE",request(store))
    jobs.start("CASE",request(store,expected_version=version,idempotency_key="second-scope-job",group_ids=["G2"]))
    first=await run_next(jobs)
    snapshot=store.read()
    second=await run_next(jobs)
    assert first["status"]==second["status"]=="completed",second
    after=store.read()
    assert after.offers[0]==snapshot.offers[0] and after.reservations[0]==snapshot.reservations[0]
    assert after.groups[0]==snapshot.groups[0] and len(after.offers)==2
    assert second["applied_version"]==first["applied_version"]+1


def test_start_idempotency_conflicts_and_overlapping_scopes_do_not_dispatch(setup):
    _,store,jobs,_,travel=setup
    req=request(store)
    job=jobs.start("CASE",req)
    assert jobs.start("CASE",req)["replayed"]
    with pytest.raises(DomainError):
        jobs.start("CASE",request(store,objective="fastest"))
    with pytest.raises(DomainError):
        jobs.start("CASE",request(store,idempotency_key="overlapping-attempt"))
    assert not travel.calls
    cancelled=jobs.cancel(job["job"]["id"],RecoveryCancellation(idempotency_key="cancel-queued-key"))
    assert cancelled["job"]["status"]=="cancelled"
    assert jobs.cancel(job["job"]["id"],RecoveryCancellation(idempotency_key="cancel-queued-key"))["replayed"]
    assert jobs.ledger.claim_next() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["timeout","connection","http_503"])
async def test_unknown_dispatch_survives_restart_and_original_key_never_resends(setup,failure):
    settings,store,jobs,ai,travel=setup
    async def failed(query):
        raise ProviderError("Fixture upstream outcome is unknown",provider="Nuitee",code=failure)
    travel.handler=failed
    req=request(store)
    before=store.read().version
    jobs.start("CASE",req)
    result=await run_next(jobs)
    assert result["status"]=="unknown_outcome" and result["steps"][-1]["status"]=="unknown"
    assert result["calls_dispatched"]==2 and len(travel.calls)==1 and not ai.decisions
    replacement=RecoveryJobs(settings,Store(store.path),jobs.service,travel,ai)
    assert replacement.start("CASE",req)["replayed"] and replacement.ledger.claim_next() is None
    assert len(travel.calls)==1 and store.read().version==before


@pytest.mark.asyncio
async def test_cancel_during_supplier_call_records_result_without_decision_or_commit(setup):
    _,store,jobs,ai,travel=setup
    entered,release=asyncio.Event(),asyncio.Event()
    async def delayed(query):
        entered.set()
        await release.wait()
        return {"state":"ready","source":"Nuitee","items":[]}
    travel.handler=delayed
    started=jobs.start("CASE",request(store))
    before=store.read().version
    task=asyncio.create_task(run_next(jobs))
    await asyncio.wait_for(entered.wait(),2)
    cancellation=jobs.cancel(started["job"]["id"],RecoveryCancellation(idempotency_key="cancel-inflight-key"))
    assert cancellation["job"]["cancel_requested"] and cancellation["job"]["status"]=="running"
    release.set()
    result=await task
    assert result["status"]=="cancelled" and result["steps"][-1]["status"]=="succeeded"
    assert not ai.decisions and store.read().version==before


@pytest.mark.asyncio
async def test_changed_scope_stops_before_another_paid_call(setup):
    _,store,jobs,ai,travel=setup
    entered,release=asyncio.Event(),asyncio.Event()
    default_search=FakeTravel().search
    async def delayed(query):
        entered.set()
        await release.wait()
        return await default_search(query)
    travel.handler=delayed
    jobs.start("CASE",request(store))
    task=asyncio.create_task(run_next(jobs))
    await asyncio.wait_for(entered.wait(),2)
    store.mutate("changed-scope",{},store.read().version,lambda world:setattr(world.groups[0],"budget",50))
    current_version=store.read().version
    release.set()
    result=await task
    assert result["status"]=="needs_review" and not ai.decisions
    assert store.read().version==current_version and not store.read().offers


def test_abandoned_dispatch_is_marked_unknown_instead_of_resumed(setup):
    _,store,jobs,_,_=setup
    jobs.start("CASE",request(store))
    job,owner=jobs.ledger.claim_next()
    jobs.ledger.dispatch(job["id"],owner,"planning_queries","OpenAI Responses",{"a":1},["G1"])
    with store.connect() as conn:
        conn.execute("UPDATE recovery_jobs SET lease_until=0 WHERE id=?",(job["id"],))
    other=RecoveryLedger(Store(store.path),60)
    assert other.claim_next() is None
    result=other.get(job["id"])
    assert result["status"]=="unknown_outcome" and result["steps"][0]["status"]=="unknown"


@pytest.mark.asyncio
async def test_missing_credentials_and_unsupported_stages_are_explicit_gaps(setup):
    _,store,jobs,ai,travel=setup
    travel.flight_configured=False
    jobs.start("CASE",request(store,modes=["flight","hotel","bus"]))
    result=await run_next(jobs)
    assert result["status"]=="blocked" and result["calls_dispatched"]==0
    assert {item["code"] for item in result["gaps"]}>={"flight_provider_not_configured","hotel_inputs_required","ground_inputs_required","no_complete_journey"}
    assert not ai.plans and not ai.decisions and not travel.calls


@pytest.mark.asyncio
async def test_dispatch_budget_is_total_for_both_models_and_supplier_queries(setup):
    _,store,jobs,ai,travel=setup
    jobs.start("CASE",request(store,group_ids=["G1","G2"],max_provider_calls=3))
    result=await run_next(jobs)
    assert result["status"]=="completed" and result["calls_dispatched"]==3
    assert len(travel.calls)==1 and result["result"]["recovered_passengers"]==1
    assert any(item["code"]=="search_allowance_uncovered" for item in result["gaps"])


def add_more_groups(store):
    def apply(world):
        template=world.groups[0]
        for index in range(3,7):
            group=template.model_copy(deep=True,update={"id":f"G{index}","name":f"G{index}","token":f"private-group-{index}"})
            world.groups.append(group)
            world.incidents[0].group_ids.append(group.id)
    store.mutate("more-job-groups",{},store.read().version,apply)
    return [group.id for group in store.read().groups]


@pytest.mark.asyncio
async def test_independent_searches_run_with_exactly_three_concurrent_calls(setup):
    _,store,jobs,_,travel=setup
    ids=add_more_groups(store)
    first_wave,release=asyncio.Event(),asyncio.Event()
    active=peak=completed=0
    default_search=FakeTravel().search
    async def delayed(query):
        nonlocal active,peak,completed
        active+=1
        peak=max(peak,active)
        if active==3:
            first_wave.set()
        try:
            await release.wait()
            return await default_search(query)
        finally:
            active-=1
            completed+=1
    travel.handler=delayed
    jobs.start("CASE",request(store,group_ids=ids,max_provider_calls=8))
    task=asyncio.create_task(run_next(jobs))
    await asyncio.wait_for(first_wave.wait(),2)
    assert len(travel.calls)==3 and peak==3
    release.set()
    result=await task
    assert result["status"]=="completed" and result["result"]["recovered_passengers"]==6
    assert peak==3 and completed==6 and result["calls_dispatched"]==8
    assert len(result["steps"])==8 and all(step["status"]=="succeeded" for step in result["steps"])


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel",[False,True])
async def test_parallel_unknown_failure_drains_all_dispatched_outcomes_and_never_applies(setup,cancel):
    _,store,jobs,ai,travel=setup
    ids=add_more_groups(store)
    first_wave,fail,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    seen=0
    default_search=FakeTravel().search
    async def delayed(query):
        nonlocal seen
        seen+=1
        number=seen
        if seen==3:
            first_wave.set()
        if number==1:
            await fail.wait()
            raise ProviderError("Fixture unknown dispatch",provider="Nuitee",code="timeout")
        await release.wait()
        return await default_search(query)
    travel.handler=delayed
    started=jobs.start("CASE",request(store,group_ids=ids,max_provider_calls=8))
    before=store.read().version
    task=asyncio.create_task(run_next(jobs))
    await asyncio.wait_for(first_wave.wait(),2)
    if cancel:
        jobs.cancel(started["job"]["id"],RecoveryCancellation(idempotency_key="parallel-cancel-key"))
    fail.set()
    for _ in range(100):
        if jobs.ledger.get(started["job"]["id"],private=True).get("_halt_dispatch"):
            break
        await asyncio.sleep(.005)
    interim=jobs.get(started["job"]["id"])["job"]
    assert interim["status"]=="running" and len(travel.calls)==3
    release.set()
    result=await task
    assert result["status"]=="unknown_outcome" and result["calls_dispatched"]==4
    assert [step["status"] for step in result["steps"]].count("unknown")==1
    assert [step["status"] for step in result["steps"]].count("succeeded")==3
    assert all(step["status"]!="dispatched" for step in result["steps"])
    assert not ai.decisions and not store.read().offers and store.read().version==before


@pytest.mark.asyncio
async def test_worker_shutdown_drains_parallel_cancellations_and_records_unknown(setup):
    _,store,jobs,_,travel=setup
    ids=add_more_groups(store)
    ready=asyncio.Event()
    stopped=[]
    async def indefinite(query):
        if len(travel.calls)==3:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(query)
    travel.handler=indefinite
    started=jobs.start("CASE",request(store,group_ids=ids,max_provider_calls=8))
    task=asyncio.create_task(run_next(jobs))
    await asyncio.wait_for(ready.wait(),2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result=jobs.get(started["job"]["id"])["job"]
    assert result["status"]=="unknown_outcome" and len(stopped)==3
    assert len(travel.calls)==3 and all(step["status"] in ("succeeded","unknown") for step in result["steps"])
    assert not store.read().offers


@pytest.mark.asyncio
async def test_atomic_job_completion_failure_rolls_back_all_operational_changes(setup,monkeypatch):
    _,store,jobs,_,_=setup
    jobs.start("CASE",request(store))
    before=store.read().model_dump()
    def reject_commit(*args):
        raise DomainError(409,"Fixture commit rejected")
    monkeypatch.setattr(jobs.ledger,"complete_in_transaction",reject_commit)
    result=await run_next(jobs)
    assert result["status"]=="needs_review" and result["calls_dispatched"]==3
    after=store.read()
    assert after.version==before["version"] and after.resources==after.offers==after.reservations==[]
    assert [group.model_dump() for group in after.groups]==before["groups"]
    with store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM idempotency WHERE key LIKE 'recovery-job-apply:%'").fetchone()


@pytest.mark.asyncio
async def test_result_cache_expiry_preserves_dispatch_identity_and_terminal_replay(setup):
    _,store,jobs,_,_=setup
    req=request(store)
    started=jobs.start("CASE",req)
    completed=await run_next(jobs)
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM recovery_steps WHERE result IS NOT NULL").fetchone()[0]==3
        conn.execute("UPDATE recovery_steps SET retain_until=0")
    replay=jobs.start("CASE",req)
    jobs.get(started["job"]["id"])
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM recovery_steps WHERE result IS NOT NULL").fetchone()[0]==0
        queries=[json.loads(row[0]) for row in conn.execute("SELECT query FROM recovery_steps")]
    assert any(query.get("query",{}).get("origin_code")=="CRA" for query in queries)
    assert replay["job"]["result"]==completed["result"] and replay["replayed"]


def test_backup_preserves_operational_ledger_but_disables_historical_queued_and_dispatched_jobs(setup,tmp_path):
    _,store,jobs,_,_=setup
    running=jobs.start("CASE",request(store))["job"]
    queued=jobs.start("CASE",request(store,idempotency_key="queued-backup-key",group_ids=["G2"]))["job"]
    claimed,owner=jobs.ledger.claim_next()
    first=jobs.ledger.dispatch(claimed["id"],owner,"planning_queries","OpenAI Responses",{"query":"retained request"},["G1"])
    jobs.ledger.outcome(claimed["id"],owner,first,"succeeded","Recorded",{"cache":"TEMPORARY-QUOTE-CACHE"})
    jobs.ledger.dispatch(claimed["id"],owner,"searching_flights","Nuitee",{"origin":"CRA","destination":"OTP"},["G1"])
    target=tmp_path/"restored.sqlite3"
    subprocess.run([sys.executable,str(Path(__file__).parents[2]/"scripts"/"backup.py"),str(store.path),str(target)],check=True,capture_output=True)
    restored=Store(str(target))
    ledger=RecoveryLedger(restored,60)
    assert ledger.claim_next() is None
    assert ledger.get(running["id"])["status"]=="unknown_outcome"
    assert ledger.get(queued["id"])["status"]=="needs_review"
    assert ledger.get(running["id"])["steps"][-1]["status"]=="unknown"
    with restored.connect() as conn:
        assert conn.execute("SELECT count(*) FROM recovery_steps WHERE result IS NOT NULL").fetchone()[0]==0
        assert json.loads(conn.execute("SELECT query FROM recovery_steps ORDER BY rowid DESC LIMIT 1").fetchone()[0])["origin"]=="CRA"
    assert b"TEMPORARY-QUOTE-CACHE" not in target.read_bytes()
    assert jobs.ledger.get(running["id"])["status"]=="running" and jobs.ledger.get(queued["id"])["status"]=="queued"
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM recovery_steps WHERE result IS NOT NULL").fetchone()[0]==1
