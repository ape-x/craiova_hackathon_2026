"""Quote refresh and decision latency use isolated SQLite and fake suppliers."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from app import live_travel
from app.inventory_lifecycle import resource_history
from app.models import Strategy
from app.solver import build_candidate
from test_recovery_jobs import FakeTravel, request, run_next, setup


async def reply(query, clock, offers):
    result = await FakeTravel().search(query)
    template = result["items"][0]
    result["items"] = []
    for identity, price, age, expiry in offers:
        quote = copy.deepcopy(template)
        quote.update(id=identity, provider_offer_id=identity, total_price=price,
                     quoted_at=(clock[0] - timedelta(seconds=age)).isoformat(),
                     expires_at=(clock[0] + timedelta(seconds=expiry)).isoformat())
        result["items"].append(quote)
    return result


@pytest.fixture
def clock(monkeypatch):
    value = [datetime.now(timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return value[0]

    monkeypatch.setattr(live_travel, "datetime", Clock)
    return value


def protected_rows(world, group_id):
    return [item.model_dump_json() for collection in (world.groups, world.offers, world.reservations)
            for item in collection if getattr(item, "group_id", item.id) == group_id] + [
                item.model_dump_json() for item in world.resources if item.eligible_group_ids == [group_id]]


@pytest.mark.asyncio
@pytest.mark.parametrize("other_status", ["proposed", "accepted", "confirmed"])
async def test_fresh_same_id_repricing_never_reuses_old_fare_or_changes_other_groups(setup, clock, other_status):
    _, store, jobs, ai, travel = setup
    travel.handler = lambda query: reply(query, clock, [("SAME-OFFER", "100.00", 0, 3600)])
    jobs.start("CASE", request(store, group_ids=["G2"], idempotency_key="other-group-original-quote"))
    assert (await run_next(jobs))["status"] == "completed"

    def set_other_status(world):
        offer = world.offers[0]
        offer.status = other_status
        world.reservations[0].status = other_status
        if other_status == "confirmed":
            world.groups[1].status = "confirmed"
            world.resources[0].booking_status = "confirmed"
            offer.confirmed_at = datetime.now(timezone.utc).isoformat()
            offer.external_bookings = [{"confirmation_reference": "FIXTURE-OTHER-BOOKING"}]

    store.mutate("other-group-state", {}, store.read().version, set_other_status)
    jobs.start("CASE", request(store))
    assert (await run_next(jobs))["status"] == "completed"
    before = store.read()
    old = next(resource for resource in before.resources if resource.eligible_group_ids == ["G1"])
    clock[0] += timedelta(seconds=1)
    travel.handler = lambda query: reply(query, clock, [("SAME-OFFER", "150.00", 0, 3600)])
    req = request(store, idempotency_key="same-id-explicit-refresh")
    jobs.start("CASE", req)
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    after = store.read()
    assert protected_rows(after, "G2") == protected_rows(before, "G2")
    active = next(offer for offer in after.offers if offer.group_id == "G1" and offer.status == "proposed")
    assert active.itinerary.total_cost == 150
    selected = next(resource for resource in after.resources if resource.id == active.itinerary.legs[0].resource_id)
    assert selected.id != old.id and selected.external_offer_id == old.external_offer_id
    assert selected.provenance["total_price"] == "150.00" and selected.available
    assert old.id not in {resource.id for resource in after.resources}
    retired = resource_history(after, resource_id=old.id)[0]
    assert not retired.available and retired.model_dump(exclude={"available"}) == old.model_dump(exclude={"available"})
    old_offer = next(offer for offer in after.offers if offer.group_id == "G1" and offer.id != active.id)
    assert old_offer.status == "invalidated"
    assert all(item.status == "released" for item in after.reservations if item.offer_id == old_offer.id)
    calls = len(travel.calls)
    assert jobs.start("CASE", req)["replayed"] and len(travel.calls) == calls


@pytest.mark.asyncio
async def test_unused_stale_revision_does_not_abort_fresh_plan_and_still_retires_old_fare(setup, clock):
    _, store, jobs, ai, travel = setup
    instant = clock[0]
    clock[0] = instant - timedelta(seconds=200)
    travel.handler = lambda query: reply(query, clock, [("OFFER-A", "100.00", 0, 3600)])
    jobs.start("CASE", request(store))
    assert (await run_next(jobs))["status"] == "completed"
    old_id = store.read().resources[0].id
    clock[0] = instant
    travel.handler = lambda query: reply(query, clock, [
        ("OFFER-A", "499.00", 110, 3600), ("OFFER-B", "150.00", 0, 3600)])

    async def decision_delay():
        clock[0] += timedelta(seconds=20)

    ai.before_choose = decision_delay
    jobs.start("CASE", request(store, idempotency_key="refresh-a-choose-fresh-b"))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    after = store.read()
    assert len(after.resources) == 1  # Selected B only; original A is retained outside planning.
    assert resource_history(after, resource_id=old_id)[0].available is False
    assert len({item.id for item in resource_history(after)}) == 2  # Never imported the expired unused new A revision.
    active = next(offer for offer in after.offers if offer.status == "proposed")
    assert active.itinerary.total_cost == 150
    assert result["result"]["resource_ids"] == [active.itinerary.legs[0].resource_id]
    assert next(resource for resource in after.resources if resource.id == active.itinerary.legs[0].resource_id).external_offer_id == "OFFER-B"
    future = build_candidate(after, Strategy(objective="lowest_cost"), scope_group_ids={"G1"})
    assert future.validation["valid"] and future.total_cost == 150


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_by", ["source_expiry", "receipt_age"])
async def test_selected_stale_revision_blocks_atomically_including_old_quote_retirement(setup, clock, stale_by):
    _, store, jobs, ai, travel = setup
    instant = clock[0]
    clock[0] = instant - timedelta(seconds=200)
    travel.handler = lambda query: reply(query, clock, [("OFFER-A", "100.00", 0, 3600)])
    jobs.start("CASE", request(store))
    assert (await run_next(jobs))["status"] == "completed"
    clock[0] = instant
    travel.handler = lambda query: reply(query, clock, [
        ("OFFER-A", "150.00", 110 if stale_by == "receipt_age" else 0, 10 if stale_by == "source_expiry" else 3600)])

    async def decision_delay():
        clock[0] += timedelta(seconds=20)

    ai.before_choose = decision_delay
    before = store.read().model_dump_json()
    jobs.start("CASE", request(store, idempotency_key="selected-revision-expiry"))
    result = await run_next(jobs)
    assert result["status"] == "needs_review" and "stale" in result["message"], result
    assert store.read().model_dump_json() == before
    assert len(travel.calls) == 2 and result["calls_dispatched"] == 3


@pytest.mark.asyncio
async def test_older_supplier_revision_never_restores_previous_cheap_fare(setup, clock):
    _, store, jobs, ai, travel = setup
    travel.handler = lambda query: reply(query, clock, [("OFFER-A", "150.00", 0, 3600)])
    jobs.start("CASE", request(store))
    assert (await run_next(jobs))["status"] == "completed"
    before = store.read().model_dump_json()
    travel.handler = lambda query: reply(query, clock, [("OFFER-A", "100.00", 30, 3600)])
    jobs.start("CASE", request(store, idempotency_key="older-supplier-revision"))
    result = await run_next(jobs)
    assert result["status"] == "needs_review" and "older revision" in result["message"], result
    assert len(ai.decisions) == 1 and store.read().model_dump_json() == before
