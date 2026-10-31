import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from app.config import Settings
from app.models import AcceptRequest, EventRequest, ReplanRequest, Strategy
from app.service import RecoveryService
from app.solver import build_candidate
from app.store import DomainError, Store, utcnow


@pytest.fixture
def service(tmp_path):
    settings=Settings(database_path=str(tmp_path/"world.sqlite3"),hold_ttl_seconds=10,_env_file=None)
    return RecoveryService(settings,Store(settings.database_path))


def replan(service,key="plan-test-1"):
    world=service.store.read()
    return asyncio.run(service.replan(ReplanRequest(expected_version=world.version,idempotency_key=key,mode="solver")))


def test_all_resource_holds_confirm_atomic_idempotent_durable(service):
    result=replan(service)
    world=service.store.read()
    offer=next(o for o in world.offers if len(o.itinerary.legs)>1)
    group=next(g for g in world.groups if g.id==offer.group_id)
    req=AcceptRequest(token=group.token,idempotency_key="accept-test-1")
    accepted=service.accept(offer.id,req)
    assert accepted["offer"]["status"]=="confirmed"
    assert "world" not in accepted
    replay=service.accept(offer.id,req)
    assert replay["replayed"]
    fresh=Store(service.settings.database_path).read()
    assert all(r.status=="confirmed" for r in fresh.reservations if r.offer_id==offer.id)
    assert fresh.version==accepted["version"]
    assert len([e for e in fresh.events if e.type=="offer_confirmed"])==1


def test_passenger_cannot_accept_another_group(service):
    replan(service)
    world=service.store.read(); offer=world.offers[0]
    wrong=next(g for g in world.groups if g.id!=offer.group_id)
    with pytest.raises(DomainError) as error:
        service.accept(offer.id,AcceptRequest(token=wrong.token,idempotency_key="wrong-token-1"))
    assert error.value.status==403
    assert service.store.read().version==world.version


def test_expired_hold_releases_every_segment_and_cannot_be_accepted(service):
    replan(service)
    world=service.store.read(); offer=world.offers[0]
    group=next(g for g in world.groups if g.id==offer.group_id)
    def expire(w):
        for o in w.offers:
            if o.id==offer.id:
                o.expires_at=(utcnow()-timedelta(seconds=1)).isoformat()
    service.store.mutate("force-expiry",{},world.version,expire)
    with pytest.raises(DomainError) as error:
        service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="expired-test"))
    assert error.value.status==409
    fresh=service.store.read()
    assert all(r.status=="released" for r in fresh.reservations if r.offer_id==offer.id)


def test_stale_plan_rejected_without_partial_holds(service):
    old=service.store.read()
    service.event(EventRequest(expected_version=old.version,idempotency_key="break-bus-1",type="resource_unavailable",target_id="B12"))
    with pytest.raises(DomainError):
        asyncio.run(service.replan(ReplanRequest(expected_version=old.version,idempotency_key="stale-plan-1")))
    assert not service.store.read().offers


def test_changed_payload_cannot_reuse_idempotency_key(service):
    world=service.store.read()
    req=EventRequest(expected_version=world.version,idempotency_key="same-key-123",type="resource_unavailable",target_id="B12")
    service.event(req)
    assert service.event(req)["replayed"]
    req.target_id="B04"
    with pytest.raises(DomainError) as exc:
        service.event(req)
    assert exc.value.status==409


def test_unaffected_confirmation_survives_bus_failure_and_replan(service):
    replan(service)
    world=service.store.read()
    affected=next(o for o in world.offers if any(l.resource_id=="B12" for l in o.itinerary.legs))
    unaffected=next(o for o in world.offers if all(l.resource_id!="B12" for l in o.itinerary.legs))
    for i,offer in enumerate([affected,unaffected]):
        group=next(g for g in world.groups if g.id==offer.group_id)
        service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key=f"accept-survive-{i}"))
    before=service.store.read()
    service.event(EventRequest(expected_version=before.version,idempotency_key="break-survive",type="resource_unavailable",target_id="B12"))
    after=service.store.read()
    assert next(o for o in after.offers if o.id==affected.id).status=="invalidated"
    assert next(o for o in after.offers if o.id==unaffected.id).status=="confirmed"
    replan(service,"cascade-replan")
    assert next(o for o in service.store.read().offers if o.id==unaffected.id).status=="confirmed"


def test_concurrent_last_seat_plans_only_one_transaction_wins(service):
    world=service.store.read()
    # One transport seat shared by two independently computed plans.
    def shrink(w):
        w.groups=w.groups[:2]
        for g in w.groups:
            g.size=1;g.accessible=False;g.destination="OTP"
        w.resources=[next(r for r in w.resources if r.id=="B12")]
        w.resources[0].capacity=1
    service.store.mutate("last-seat-init",{},world.version,shrink)
    world=service.store.read()
    plan=build_candidate(world,Strategy())
    assert plan.recovered_passengers==1
    barrier=Barrier(2)
    def claim(i):
        independent=RecoveryService(service.settings,Store(service.settings.database_path))
        barrier.wait()
        try:
            return independent.store.mutate(f"race-{i}",{},world.version,lambda w:independent._hold_plan(w,plan,"solver","race","solver","race",[plan],1))
        except DomainError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(claim,[1,2]))
    assert sum(isinstance(r,dict) for r in results)==1
    assert 409 in results
    fresh=service.store.read()
    assert sum(r.quantity for r in fresh.reservations if r.status=="held")==1


def test_budget_cannot_revoke_unaffected_confirmed_commitments(service):
    replan(service)
    world=service.store.read(); offer=world.offers[0]
    group=next(g for g in world.groups if g.id==offer.group_id)
    service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="accept-budget-1"))
    version=service.store.read().version
    with pytest.raises(DomainError):
        service.event(EventRequest(expected_version=version,idempotency_key="budget-too-low",type="budget_changed",value=0))
    assert service.store.read().version==version
