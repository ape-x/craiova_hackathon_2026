import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import sqlite3
from threading import Event

import pytest
from fastapi.testclient import TestClient

import app.store as store_module
from app.config import Settings
from app.main import create_app
from app.models import AcceptRequest, CommandRequest, ConstraintRequest, EventRequest, Mutation, ReplanRequest
from app.providers import ProviderError
from app.service import RecoveryService
from app.solver import active_offers, build_candidate, default_strategies
from app.store import DomainError, Store, utcnow


def service_at(tmp_path):
    settings=Settings(database_path=str(tmp_path/"release.sqlite3"),_env_file=None)
    return RecoveryService(settings,Store(settings.database_path))


def replan(service,key="release-initial-plan"):
    return asyncio.run(service.replan(ReplanRequest(expected_version=service.store.read().version,idempotency_key=key)))


def event(service,key,kind,target,value=None):
    return service.event(EventRequest(expected_version=service.store.read().version,idempotency_key=key,type=kind,target_id=target,value=value))


def test_reset_revokes_token_before_acceptance_or_constraint_replay(tmp_path):
    service=service_at(tmp_path);replan(service)
    world=service.store.read();offer=world.offers[0]
    group=next(g for g in world.groups if g.id==offer.group_id)
    accepted=AcceptRequest(token=group.token,idempotency_key="accepted-before-reset")
    constraints=ConstraintRequest(idempotency_key="constraints-before-reset",deadline=group.deadline)
    service.accept(offer.id,accepted)
    service.constraints(group.token,constraints)
    service.reset(Mutation(expected_version=service.store.read().version,idempotency_key="reset-revokes-replays"))
    version=service.store.read().version
    with pytest.raises(DomainError) as acceptance_error:
        service.accept(offer.id,accepted)
    with pytest.raises(DomainError) as constraints_error:
        service.constraints(group.token,constraints)
    assert acceptance_error.value.status==constraints_error.value.status==404
    assert service.store.read().version==version and not service.store.read().offers


def test_acceptance_rechecks_idempotency_after_expiry_commit_gap(tmp_path,monkeypatch):
    service=service_at(tmp_path);replan(service)
    world=service.store.read();target,expired=world.offers[:2]
    group=next(g for g in world.groups if g.id==target.group_id)
    request=AcceptRequest(token=group.token,idempotency_key="same-accept-during-expiry")
    def force_expiry(current):
        next(o for o in current.offers if o.id==expired.id).expires_at=(utcnow()-timedelta(seconds=1)).isoformat()
    service.store.mutate("expired-fixture",{},world.version,force_expiry)
    racing=RecoveryService(service.settings,Store(service.settings.database_path))
    committed=Event();resume=Event();paused=Event()
    class PausingConnection(sqlite3.Connection):
        def commit(self):
            super().commit()
            if not paused.is_set():
                paused.set();committed.set()
                assert resume.wait(5),"Second request did not finish during the expiry gap"
    @contextmanager
    def controlled_connect():
        conn=sqlite3.connect(service.settings.database_path,timeout=5,isolation_level=None,factory=PausingConnection)
        try:
            with conn:
                yield conn
        finally:
            conn.close()
    monkeypatch.setattr(racing.store,"connect",controlled_connect)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(racing.accept,target.id,request)
        try:
            assert committed.wait(5)
            second=service.accept(target.id,request)
        finally:
            resume.set()
        result=first.result(timeout=5)
    assert second["offer"]["status"]=="confirmed" and result["replayed"]
    after=service.store.read()
    assert sum(e.type=="offer_confirmed" and e.target_id==target.id for e in after.events)==1
    assert after.version==second["version"]==result["version"]


@pytest.mark.asyncio
async def test_shared_claim_prevents_duplicate_paid_calls_and_replays_after_completion(tmp_path):
    service=service_at(tmp_path);entered=asyncio.Event();release=asyncio.Event()
    class AI:
        interpretations=0;decisions=0
        async def interpret(self,*args):
            self.interpretations+=1;entered.set();await release.wait()
            return {"events":[{"type":"resource_capacity","target_id":"B12","value":9}],"strategies":[],"explanation":"Capacity9","focus_id":"B12"}
        async def choose(self,world,candidates,request):
            self.decisions+=1
            return {"plan_id":candidates[0]["id"],"explanation":"Validated test plan","provider":"test-double"}
    ai=AI();service.ai=ai
    independent=RecoveryService(service.settings,Store(service.settings.database_path),ai)
    request=CommandRequest(expected_version=1,idempotency_key="single-flight-command",text="Set B12 to9 seats")
    first=asyncio.create_task(service.command(request));await entered.wait()
    try:
        with pytest.raises(DomainError) as pending:
            await independent.command(request)
        assert pending.value.status==503 and pending.value.applied is None
        assert pending.value.details["in_progress"] is True
        with pytest.raises(DomainError) as collision:
            await independent.command(request.model_copy(update={"text":"A different command"}))
        assert collision.value.status==409
        assert ai.interpretations==1 and ai.decisions==0
    finally:
        release.set()
    result=await first
    replay=await independent.command(request)
    assert result["world"]["version"]==2 and replay["replayed"]
    assert ai.interpretations==ai.decisions==1
    assert len(service.store.read().decisions)==1
    with service.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM request_claims").fetchone()[0]==0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["provider","timeout","cancelled"])
async def test_planning_claim_released_on_failure_without_detached_apply(tmp_path,failure):
    service=service_at(tmp_path);entered=asyncio.Event();cancelled=asyncio.Event()
    class AI:
        async def interpret(self,*args):
            entered.set()
            if failure=="provider":
                raise ProviderError("Mock upstream rejection",provider="test-double",code="mock_error")
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        async def choose(self,*args):
            pytest.fail("A failed interpretation cannot select or apply a plan")
    service.ai=AI();service._planning_timeout_seconds=.02 if failure=="timeout" else 10
    request=CommandRequest(expected_version=1,idempotency_key="failed-claimed-command",text="Change a capacity")
    task=asyncio.create_task(service.command(request));await entered.wait()
    if failure=="cancelled":
        task.cancel()
    with pytest.raises(ProviderError if failure=="provider" else DomainError if failure=="timeout" else asyncio.CancelledError) as error:
        await task
    if failure=="timeout":
        assert error.value.status==504 and error.value.applied is False
    if failure!="provider":
        assert cancelled.is_set()
    assert service.store.read().version==1 and not service.store.read().offers
    with service.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM request_claims").fetchone()[0]==0


def test_expired_claim_can_be_recovered_but_old_owner_cannot_release_new_claim(tmp_path,monkeypatch):
    store=service_at(tmp_path).store;now=utcnow()
    first=store.claim_request("claim-key",{"command":"A"},1)
    monkeypatch.setattr(store_module,"utcnow",lambda:now+timedelta(seconds=2))
    second=Store(store.path).claim_request("claim-key",{"command":"A"},30)
    assert first["owner"]!=second["owner"]
    store.release_request("claim-key",first["owner"])
    with pytest.raises(DomainError) as error:
        store.claim_request("claim-key",{"command":"A"},30)
    assert error.value.status==503
    store.release_request("claim-key",second["owner"])


def test_pending_http_outcome_is_unknown_and_reuses_same_request_identity(tmp_path):
    settings=Settings(database_path=str(tmp_path/"pending.sqlite3"),_env_file=None)
    app=create_app(settings)
    request=CommandRequest(expected_version=1,idempotency_key="pending-http-command",text="Change B12")
    app.state.store.claim_request("command:"+request.idempotency_key,request.model_dump(),60)
    with TestClient(app) as client:
        response=client.post("/api/commands",json=request.model_dump())
    assert response.status_code==503 and response.headers["Retry-After"]=="2"
    assert response.json()["applied"] is None
    assert response.json()["details"]=={"in_progress":True,"retry_after_seconds":2}


@pytest.mark.parametrize("kind,target,value",[("flight_cancelled","F201",None),("resource_unavailable","F201",None),("flight_diverted","F101","OTP"),("flight_diverted","F110","SBZ")])
def test_flight_lifecycle_rejects_impossible_cancellations_and_diversions(tmp_path,kind,target,value):
    service=service_at(tmp_path)
    with pytest.raises(DomainError) as error:
        event(service,"invalid-flight-lifecycle",kind,target,value)
    assert error.value.status==409
    assert service.store.read().version==1


def test_same_arrival_is_noop_and_repeated_diversion_preserves_confirmations(tmp_path):
    service=service_at(tmp_path)
    event(service,"unchanged-cra-arrival","flight_diverted","F201","CRA")
    assert all(g.status=="scheduled" for g in service.store.read().groups if g.original_flight_id=="F201")
    event(service,"first-otp-diversion","flight_diverted","F201","OTP")
    replan(service)
    world=service.store.read();inbound={g.id for g in world.groups if g.original_flight_id=="F201"}
    offer=next(o for o in active_offers(world) if o.group_id in inbound)
    group=next(g for g in world.groups if g.id==offer.group_id)
    service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="confirmed-otp-transfer"))
    before=service.store.read()
    event(service,"repeat-otp-diversion","flight_diverted","F201","OTP")
    after=service.store.read()
    assert after.offers==before.offers and after.reservations==before.reservations
    assert next(o for o in after.offers if o.id==offer.id).status=="confirmed"


def test_return_to_available_original_arrival_does_not_create_false_recovery_cases(tmp_path):
    service=service_at(tmp_path)
    event(service,"divert-away-from-cra","flight_diverted","F201","OTP")
    event(service,"close-otp-after-diversion","airport_closed","OTP")
    world=service.store.read();inbound={g.id for g in world.groups if g.original_flight_id=="F201"}
    strategies=[s for s in default_strategies(world) if s.preferred_airport=="CRA"]
    assert strategies
    for strategy in strategies:
        plan=build_candidate(world,strategy)
        assert plan.validation["valid"]
        assert not inbound&{e["group_id"] for e in plan.exceptions}
    event(service,"return-to-cra-arrival","flight_diverted","F201","CRA")
    world=service.store.read()
    assert all(g.status=="scheduled" for g in world.groups if g.id in inbound)


def test_flight_cannot_restore_through_a_closed_airport(tmp_path):
    service=service_at(tmp_path)
    event(service,"closed-arrival-cra","airport_closed","CRA")
    before=service.store.read()
    with pytest.raises(DomainError) as error:
        event(service,"invalid-restore-closure","resource_restored","F201")
    assert error.value.status==409
    assert service.store.read().version==before.version
    assert next(r for r in service.store.read().resources if r.id=="F201").status=="needs_diversion"
