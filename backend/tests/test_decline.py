import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.config import Settings
from app.models import AcceptRequest, EventRequest, Mutation, ReplanRequest
from app.service import RecoveryService
from app.store import DomainError, Store


@pytest.fixture
def service(tmp_path):
    settings=Settings(database_path=str(tmp_path/"decline.sqlite3"),_env_file=None)
    service=RecoveryService(settings,Store(settings.database_path))
    asyncio.run(service.replan(ReplanRequest(expected_version=1,idempotency_key="decline-fixture-plan")))
    return service


def offered_journey(service):
    world=service.store.read()
    offer=next(o for o in world.offers if o.status=="held" and len(o.itinerary.legs)>1)
    group=next(g for g in world.groups if g.id==offer.group_id)
    return world,offer,group


def test_decline_releases_whole_journey_once_and_preserves_other_inventory(service):
    before,offer,group=offered_journey(service)
    unrelated=next(o for o in before.offers if o.status=="held" and o.group_id!=group.id)
    unrelated_group=next(g for g in before.groups if g.id==unrelated.group_id)
    service.accept(unrelated.id,AcceptRequest(token=unrelated_group.token,idempotency_key="decline-unrelated-confirmation"))
    before=service.store.read()
    other_reservations=[r.model_dump() for r in before.reservations if r.offer_id!=offer.id]
    other_offers=[o.model_dump() for o in before.offers if o.id!=offer.id]
    result=service.decline(offer.id,group.token,"decline-full-journey")
    assert set(result)=={"offer","version","replayed","simulated"}
    assert result["offer"]["status"]=="declined" and not result["replayed"]
    assert result["version"]==before.version+1
    after=service.store.read()
    reservations=[r for r in after.reservations if r.offer_id==offer.id]
    assert len(reservations)==len(offer.itinerary.legs)
    assert all(r.status=="released" for r in reservations)
    assert next(g for g in after.groups if g.id==group.id).status=="waiting"
    assert [r.model_dump() for r in after.reservations if r.offer_id!=offer.id]==other_reservations
    assert [o.model_dump() for o in after.offers if o.id!=offer.id]==other_offers
    passenger=service.passenger(group.token)
    assert passenger["offer"] is None and passenger["last_offer"]["status"]=="declined"

    reopened=RecoveryService(service.settings,Store(service.settings.database_path))
    replay=reopened.decline(offer.id,group.token,"decline-full-journey")
    assert replay["replayed"] and replay["offer"]==result["offer"]
    assert reopened.store.read().version==after.version
    assert sum(e.type=="offer_declined" and e.target_id==offer.id for e in after.events)==1


def test_decline_rejects_another_passenger_before_cached_replay(service):
    _,offer,group=offered_journey(service)
    other=next(g for g in service.store.read().groups if g.id!=group.id)
    service.decline(offer.id,group.token,"decline-own-offer")
    before=service.store.read()
    with pytest.raises(DomainError) as error:
        service.decline(offer.id,other.token,"decline-own-offer")
    assert error.value.status==403
    assert service.store.read()==before


def test_confirmed_offer_cannot_be_declined(service):
    _,offer,group=offered_journey(service)
    service.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="confirm-before-decline"))
    before=service.store.read()
    with pytest.raises(DomainError) as error:
        service.decline(offer.id,group.token,"decline-confirmed-offer")
    assert error.value.status==409 and error.value.details=={"status":"confirmed"}
    assert service.store.read()==before


def test_invalidated_offer_cannot_be_declined(service):
    before,offer,group=offered_journey(service)
    resource_id=offer.itinerary.legs[0].resource_id
    service.event(EventRequest(expected_version=before.version,idempotency_key="decline-fixture-incident",type="resource_unavailable",target_id=resource_id))
    before=service.store.read()
    with pytest.raises(DomainError) as error:
        service.decline(offer.id,group.token,"decline-invalidated-offer")
    assert error.value.status==409 and error.value.details=={"status":"invalidated"}
    assert service.store.read()==before


def test_reset_revokes_authorization_before_decline_replay(service):
    _,offer,group=offered_journey(service)
    service.decline(offer.id,group.token,"decline-before-reset")
    service.reset(Mutation(expected_version=service.store.read().version,idempotency_key="decline-fixture-reset"))
    before=service.store.read()
    with pytest.raises(DomainError) as error:
        service.decline(offer.id,group.token,"decline-before-reset")
    assert error.value.status==404
    assert service.store.read()==before


def test_same_decline_key_cannot_release_a_replacement_offer(service):
    _,offer,group=offered_journey(service)
    service.decline(offer.id,group.token,"decline-reused-key")
    asyncio.run(service.replan(ReplanRequest(expected_version=service.store.read().version,idempotency_key="decline-replacement-plan")))
    before=service.store.read()
    replacement=next(o for o in before.offers if o.group_id==group.id and o.status=="held")
    with pytest.raises(DomainError) as error:
        service.decline(replacement.id,group.token,"decline-reused-key")
    assert error.value.status==409
    assert service.store.read()==before


def test_concurrent_accept_and_decline_have_only_one_winner(service):
    before,offer,group=offered_journey(service)
    barrier=Barrier(2)
    def act(action):
        independent=RecoveryService(service.settings,Store(service.settings.database_path))
        barrier.wait()
        try:
            if action=="accept":
                result=independent.accept(offer.id,AcceptRequest(token=group.token,idempotency_key="race-accept-offer"))
            else:
                result=independent.decline(offer.id,group.token,"race-decline-offer")
            return action,result["offer"]["status"]
        except DomainError as error:
            return action,error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=dict(pool.map(act,["accept","decline"]))
    after=service.store.read()
    final=next(o for o in after.offers if o.id==offer.id)
    assert final.status in ("confirmed","declined")
    winner="accept" if final.status=="confirmed" else "decline"
    loser="decline" if winner=="accept" else "accept"
    assert outcomes[winner]==final.status and outcomes[loser]==409
    assert after.version==before.version+1
    expected="confirmed" if winner=="accept" else "released"
    assert all(r.status==expected for r in after.reservations if r.offer_id==offer.id)
    assert sum(e.target_id==offer.id and e.type in ("offer_confirmed","offer_declined") for e in after.events)==1
