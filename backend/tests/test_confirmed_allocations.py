"""Shared fail-closed booking protection, with isolated SQLite and no source calls."""
from datetime import datetime, timedelta

import pytest
import httpx

from app.confirmed_allocations import (
    confirmation_accounting_issues, confirmed_group_ids, has_confirmed_groups, require_consistent_confirmations,
)
from app.models import Itinerary, Leg, Offer, Reservation, ReplanRequest, Resource, Strategy
from app.operational_reports import ArrivalReportInput, OperationalReports, _confirmed_groups
from app.solver import build_candidate
from app.store import DomainError, utcnow
from test_recovery_jobs import setup, request, run_next
from test_live_service import ops
import test_live_service as live_fixture


def resource(identity="BOOKED", *, booked=True):
    return Resource(id=identity, kind="bus", name="Recorded operator vehicle",
        origin="CRA", destination="OTP", departure=100, arrival=200, capacity=4,
        cost_per_person=50, source="operator_confirmed", booking_status="confirmed" if booked else "unbooked")


def offer(status="proposed", *, group_id="G1", resource_id="BOOKED", external=False):
    return Offer(id="OFFER-"+group_id, group_id=group_id, status=status,
        itinerary=Itinerary(legs=[Leg(resource_id=resource_id, kind="bus", origin="CRA", destination="OTP",
            departure=100, arrival=200, cost=50)], arrival=200, total_cost=50),
        expires_at=(utcnow()+timedelta(hours=1)).isoformat(), created_version=1,
        external_bookings=[{"resource_id": resource_id, "reference": "offline-confirmation"}] if external else [])


def reservation(*, group_id="G1", status="confirmed", offer_id="MISSING-OFFER"):
    return Reservation(id="RESERVATION-"+group_id, group_id=group_id, resource_id="BOOKED",
        offer_id=offer_id, quantity=1, status=status, expires_at=(utcnow()+timedelta(hours=1)).isoformat())


def evidence(world, kind):
    if kind == "group":
        world.groups[0].status = "confirmed"
    elif kind == "offer":
        world.offers.append(offer("confirmed"))
    elif kind == "external_history":
        world.offers.append(offer("declined", external=True))
    elif kind == "orphan_reservation":
        world.reservations.append(reservation())
    elif kind == "active_booked_resource":
        world.resources.append(resource())
        world.offers.append(offer("accepted"))
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["group", "offer", "external_history", "orphan_reservation", "active_booked_resource"])
def test_each_retained_confirmation_protects_only_its_group_without_mutation(setup, kind):
    _, store, _, _, _ = setup
    world = store.read()
    evidence(world, kind)
    before = world.model_dump(mode="json", exclude={"sim_time"})
    assert confirmed_group_ids(world) == {"G1"}
    assert has_confirmed_groups(world, ["G1"]) and _confirmed_groups(world, {"G1"})
    assert not has_confirmed_groups(world, ["G2"]) and not _confirmed_groups(world, {"G2"})
    assert world.model_dump(mode="json", exclude={"sim_time"}) == before


@pytest.mark.parametrize("status", ["held", "proposed", "accepted", "confirmed"])
def test_active_offer_leg_associates_booked_resource_with_its_group(setup, status):
    _, store, _, _, _ = setup
    world = store.read()
    world.resources.append(resource())
    world.offers.append(offer(status))
    assert confirmed_group_ids(world) == {"G1"}


@pytest.mark.parametrize("status", ["expired", "declined", "superseded", "invalidated"])
def test_historical_leg_alone_does_not_create_a_current_booking_association(setup, status):
    _, store, _, _, _ = setup
    world = store.read()
    world.resources.append(resource())
    world.offers.extend([offer(status), offer("proposed", group_id="G2")])
    assert confirmed_group_ids(world) == {"G2"}


@pytest.mark.parametrize("status", ["held", "proposed", "accepted", "confirmed", "expired", "declined", "superseded", "invalidated"])
def test_external_booking_evidence_survives_any_contradictory_offer_status(setup, status):
    _, store, _, _, _ = setup
    world = store.read()
    world.offers.append(offer(status, external=True))
    assert confirmed_group_ids(world) == {"G1"}


@pytest.mark.parametrize("status", ["held", "proposed", "accepted", "released"])
def test_nonconfirmed_orphan_reservation_is_not_a_booking_association(setup, status):
    _, store, _, _, _ = setup
    world = store.read()
    world.resources.append(resource())
    world.reservations.append(reservation(status=status))
    assert not confirmed_group_ids(world)


def test_booked_resource_eligibility_or_other_group_allocation_does_not_block_selected_group(setup):
    _, store, jobs, ai, travel = setup
    def seed(world):
        booked = resource()
        booked.eligible_group_ids = ["G1"]  # Eligibility is not an allocation.
        world.resources.extend([booked, resource("UNBOOKED", booked=False)])
        world.offers.append(offer("proposed", resource_id="UNBOOKED"))
    store.mutate("fixture-unrelated-booking", {}, store.read().version, seed)
    assert not confirmed_group_ids(store.read())
    started = jobs.start("CASE", request(store))
    assert started["job"]["status"] == "queued" and started["job"]["calls_dispatched"] == 0
    assert not ai.plans and not ai.decisions and not travel.calls


@pytest.mark.parametrize("kind", ["group", "offer", "external_history", "orphan_reservation", "active_booked_resource"])
@pytest.mark.parametrize("transactional", [False, True])
def test_job_start_refuses_inconsistent_confirmation_before_ledger_creation(setup, kind, transactional):
    _, store, jobs, ai, travel = setup
    store.mutate("fixture-confirmed-evidence", {}, store.read().version, lambda world: evidence(world, kind))
    before = store.read().model_dump(mode="json", exclude={"sim_time"})
    req = request(store)
    with pytest.raises(DomainError) as rejected:
        if transactional:
            with store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                jobs.start_in_transaction(conn, store._load(conn), "CASE", req, supersede_active=True)
        else:
            jobs.start("CASE", req)
    assert rejected.value.status == 409 and "confirmed" in rejected.value.message
    assert jobs.list("CASE")["jobs"] == [] and store.read().model_dump(mode="json", exclude={"sim_time"}) == before
    assert not ai.plans and not ai.decisions and not travel.calls


@pytest.mark.parametrize("kind", ["external_history", "orphan_reservation", "active_booked_resource"])
def test_arrival_report_shares_the_guard_and_preserves_all_evidence_on_refusal(setup, kind):
    _, store, jobs, ai, travel = setup
    store.mutate("fixture-report-confirmation", {}, store.read().version, lambda world: evidence(world, kind))
    world = store.read()
    body = ArrivalReportInput(expected_version=world.version, idempotency_key="offline-protected-arrival",
        description="Operator reports passengers at CRA", group_ids=["G1"], airport_code="CRA",
        ready_at=datetime.fromisoformat(world.epoch)+timedelta(minutes=world.groups[0].ready_at))
    with pytest.raises(DomainError) as rejected:
        OperationalReports(store, jobs).arrival("CASE", body)
    assert rejected.value.status == 409
    assert store.read().model_dump(mode="json", exclude={"sim_time"}) == world.model_dump(mode="json", exclude={"sim_time"})
    assert not ai.plans and not ai.decisions and not travel.calls


def canonical_shared(world, *, reservations=True):
    """One actual fixed itinerary accounts for one unit of a shared service."""
    booked = resource(booked=False)
    booked.departure = world.groups[1].ready_at+60
    booked.arrival = booked.departure+60
    booked.capacity = 2
    booked.valid_until = (utcnow()+timedelta(hours=1)).isoformat()
    world.resources.append(booked)
    fixed = offer("confirmed", group_id="G2", external=True)
    fixed.itinerary.legs[0].departure = booked.departure
    fixed.itinerary.legs[0].arrival = booked.arrival
    fixed.itinerary.arrival = booked.arrival
    world.offers.append(fixed)
    world.groups[1].status = "confirmed"
    if reservations:
        world.reservations.append(reservation(group_id="G2", offer_id=fixed.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["solver", "ai"])
async def test_global_replan_refuses_orphan_confirmation_before_model_or_proposal_changes(setup, mode):
    _, store, jobs, ai, travel = setup
    def seed(world):
        canonical_shared(world)
        world.offers.clear()  # Existing confirmation now has no fixed allocation.
        world.groups[1].status = "waiting"
    store.mutate("fixture-orphan-global", {}, store.read().version, seed)
    before = store.read().model_dump(mode="json", exclude={"sim_time"})
    with pytest.raises(DomainError) as rejected:
        await jobs.service.replan(ReplanRequest(expected_version=before["version"],
            idempotency_key="offline-global-confirmation", mode=mode))
    assert rejected.value.status == 409
    assert rejected.value.details["code"] == "confirmed_allocation_review_required"
    assert store.read().model_dump(mode="json", exclude={"sim_time"}) == before
    assert not ai.decisions and not travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["before_start", "after_start"])
async def test_orphan_other_group_booking_stops_scoped_job_before_any_dispatch(setup, timing):
    _, store, jobs, ai, travel = setup
    def seed(world):
        canonical_shared(world)
        world.offers.clear()
        world.groups[1].status = "waiting"
    if timing == "after_start":
        jobs.start("CASE", request(store))
    store.mutate("fixture-orphan-scoped", {}, store.read().version, seed)
    before = store.read().model_dump(mode="json", exclude={"sim_time"})
    if timing == "before_start":
        jobs.start("CASE", request(store))
    completed = await run_next(jobs)
    assert completed["status"] == "needs_review", completed
    assert completed["calls_dispatched"] == 0
    assert not ai.plans and not ai.decisions and not travel.calls
    assert store.read().model_dump(mode="json", exclude={"sim_time"}) == before


def test_final_hold_guard_catches_orphan_capacity_even_when_candidate_validation_passes(setup):
    _, store, jobs, ai, travel = setup
    world = store.read()
    canonical_shared(world)
    world.offers.clear()
    world.groups[1].status = "waiting"
    plan = build_candidate(world, Strategy(), scope_group_ids={"G1"})
    assert plan.validation["valid"] and plan.recovered_passengers == 1
    before = world.model_dump(mode="json", exclude={"sim_time"})
    with pytest.raises(DomainError) as rejected:
        jobs.service._hold_plan(world, plan, "solver", "Offline final guard", "local", "Candidate", [plan], 0,
            scope_group_ids={"G1"})
    assert rejected.value.details["code"] == "confirmed_allocation_review_required"
    assert world.model_dump(mode="json", exclude={"sim_time"}) == before
    assert not ai.plans and not ai.decisions and not travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("reservations", [False, True])
async def test_complete_confirmed_shared_itinerary_stays_usable_without_inventing_missing_rows(setup, reservations):
    _, store, jobs, ai, travel = setup
    store.mutate("fixture-canonical-shared", {}, store.read().version,
        lambda world: canonical_shared(world, reservations=reservations))
    before = store.read()
    result = await jobs.service.replan(ReplanRequest(expected_version=before.version,
        idempotency_key="offline-canonical-shared", mode="solver"))
    after = store.read()
    assert result["world"]["version"] == before.version+1
    assert next(item for item in after.offers if item.group_id == "G2") == before.offers[0]
    assert len([item for item in after.reservations if item.group_id == "G2"]) == int(reservations)
    assert any(item.group_id == "G1" and item.status == "proposed" for item in after.offers)
    assert after.resources == before.resources and not ai.decisions and not travel.calls


@pytest.mark.parametrize("kind,expected", [
    ("missing_offer", "confirmed_reservation_without_fixed_allocation"),
    ("wrong_group", "confirmed_reservation_without_fixed_allocation"),
    ("wrong_quantity", "confirmed_reservation_quantity_mismatch"),
    ("duplicate_reservation", "duplicate_confirmed_reservation"),
    ("missing_resource", "missing_confirmed_resource"),
    ("changed_resource", "confirmed_itinerary_resource_mismatch"),
    ("duplicate_resource", "duplicate_confirmed_resource"),
    ("empty_itinerary", "incomplete_confirmed_itinerary"),
    ("partial_itinerary", "incomplete_confirmed_itinerary"),
    ("group_not_confirmed", "confirmed_offer_group_mismatch"),
    ("group_without_offer", "confirmed_group_without_fixed_allocation"),
    ("duplicate_offer", "duplicate_confirmed_offer"),
    ("capacity", "confirmed_capacity_exceeded"),
    ("external_history", "external_booking_without_fixed_allocation"),
    ("external_unknown_resource", "external_booking_resource_mismatch"),
    ("external_missing_resource", "external_booking_resource_mismatch"),
    ("external_malformed_resource", "external_booking_resource_mismatch"),
    ("external_duplicate_resource", "external_booking_resource_mismatch"),
    ("booked_unallocated", "booked_resource_without_fixed_allocation"),
])
def test_inconsistent_confirmations_need_review_without_reconstructing_or_leaking_bookings(setup, kind, expected):
    _, store, _, _, _ = setup
    world = store.read()
    canonical_shared(world)
    if kind == "missing_offer":
        world.reservations[0].offer_id = "GONE"
    elif kind == "wrong_group":
        world.reservations[0].group_id = "G1"
    elif kind == "wrong_quantity":
        world.reservations[0].quantity = 2
    elif kind == "duplicate_reservation":
        world.reservations.append(world.reservations[0].model_copy(deep=True))
    elif kind == "missing_resource":
        world.resources.clear()
    elif kind == "changed_resource":
        world.resources[0].departure += 1
    elif kind == "duplicate_resource":
        world.resources.append(world.resources[0].model_copy(deep=True))
    elif kind == "empty_itinerary":
        world.offers[0].itinerary.legs.clear()
    elif kind == "partial_itinerary":
        world.groups[1].destination = "SBZ"
    elif kind == "group_not_confirmed":
        world.groups[1].status = "waiting"
    elif kind == "group_without_offer":
        world.offers.clear()
    elif kind == "duplicate_offer":
        world.offers.append(world.offers[0].model_copy(deep=True))
    elif kind == "capacity":
        world.resources[0].capacity = 0
    elif kind == "external_history":
        world.offers[0].status = "declined"
    elif kind == "external_unknown_resource":
        world.offers[0].external_bookings[0]["resource_id"] = "OTHER"
    elif kind == "external_missing_resource":
        del world.offers[0].external_bookings[0]["resource_id"]
    elif kind == "external_malformed_resource":
        world.offers[0].external_bookings[0]["resource_id"] = {"unsafe": "secret"}
    elif kind == "external_duplicate_resource":
        world.offers[0].external_bookings *= 2
    elif kind == "booked_unallocated":
        world.resources.append(resource("UNALLOCATED"))
    before = world.model_dump(mode="json", exclude={"sim_time"})
    assert expected in confirmation_accounting_issues(world)
    with pytest.raises(DomainError) as rejected:
        require_consistent_confirmations(world)
    assert rejected.value.status == 409
    assert rejected.value.details["code"] == "confirmed_allocation_review_required"
    assert "offline-confirmation" not in str(rejected.value.details)
    assert "secret" not in str(rejected.value.details)
    assert world.model_dump(mode="json", exclude={"sim_time"}) == before


def test_unrelated_unbooked_and_historical_inventory_does_not_need_confirmation_review(setup):
    _, store, _, _, _ = setup
    world = store.read()
    canonical_shared(world)
    world.resources.append(resource("UNBOOKED", booked=False))
    world.offers.append(offer("declined", resource_id="UNBOOKED"))
    assert confirmation_accounting_issues(world) == []
    require_consistent_confirmations(world)


def test_shared_fixed_capacity_counts_both_groups_once_and_preserves_simulation_contract(setup):
    _, store, _, _, _ = setup
    world = store.read()
    canonical_shared(world)
    second = world.offers[0].model_copy(deep=True)
    second.id, second.group_id = "OFFER-G1", "G1"
    world.offers.append(second)
    world.groups[0].status = "confirmed"
    assert confirmation_accounting_issues(world) == []
    world.resources[0].capacity = 1
    assert confirmation_accounting_issues(world) == ["confirmed_capacity_exceeded"]
    world.mode = "simulation"
    assert confirmation_accounting_issues(world) == []
    require_consistent_confirmations(world)


def test_actual_operator_confirmation_records_satisfy_the_accounting_guard(ops):
    ops.manifest(live_fixture.manifest_request(ops))
    ops.resource(live_fixture.resource_request(ops))
    live_fixture.attach_proposal(ops)
    recorded = ops.confirm("PROPOSAL-1", live_fixture.confirmation(ops))
    assert recorded["offer"]["status"] == "confirmed"
    assert confirmation_accounting_issues(ops.store.read()) == []
    assert confirmed_group_ids(ops.store.read()) == {"REAL-GROUP"}
    assert ops.real_data.provider.calls == 0


@pytest.mark.asyncio
async def test_http_global_replan_returns_sanitized_manual_review_and_preserves_world(setup):
    from app.main import create_app

    settings, store, _, ai, travel = setup
    def seed(world):
        canonical_shared(world)
        world.reservations[0].offer_id = "MISSING"
    store.mutate("http-orphan-confirmation", {}, store.read().version, seed)
    before = store.read().model_dump(mode="json", exclude={"sim_time"})
    app = create_app(settings.model_copy(update={"operator_token": "x"*40}))
    app.state.service.ai = ai
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        body = {"expected_version": before["version"], "idempotency_key": "offline-api-global-review", "mode": "ai"}
        denied = await client.post("/api/replan", json=body)
        assert denied.status_code == 401
        result = await client.post("/api/replan", json=body, headers={"Authorization": "Bearer "+"x"*40})
    assert result.status_code == 409
    assert result.json()["details"]["code"] == "confirmed_allocation_review_required"
    assert result.json()["applied"] is False
    assert "offline-confirmation" not in result.text and "private-token" not in result.text
    assert store.read().model_dump(mode="json", exclude={"sim_time"}) == before
    assert not ai.plans and not ai.decisions and not travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_status", ["held", "proposed"])
async def test_accounted_shared_booking_does_not_invent_confirmation_for_pending_group(setup, pending_status):
    _, store, jobs, ai, travel = setup
    def seed(world):
        canonical_shared(world)
        fixed = world.offers[0]
        fixed.id, fixed.group_id = "OFFER-G1", "G1"
        world.groups[0].status, world.groups[1].status = "confirmed", "offered"
        world.reservations[0].group_id, world.reservations[0].offer_id = "G1", fixed.id
        world.resources[0].booking_status = "confirmed"
        pending = fixed.model_copy(deep=True)
        pending.id, pending.group_id, pending.status = "OFFER-G2", "G2", pending_status
        pending.external_bookings = []
        world.offers.append(pending)
        world.reservations.append(reservation(group_id="G2", status=pending_status, offer_id=pending.id))
    store.mutate("fixture-shared-partial-confirmation", {}, store.read().version, seed)
    before = store.read()
    # Navigation is conservative; global accounting does not turn G2 into a booking.
    assert confirmed_group_ids(before) == {"G1", "G2"}
    assert confirmation_accounting_issues(before) == []
    await jobs.service.replan(ReplanRequest(expected_version=before.version,
        idempotency_key="offline-shared-partial-replan", mode="solver"))
    after = store.read()
    assert next(item for item in after.offers if item.id == "OFFER-G1") == before.offers[0]
    assert [item for item in after.reservations if item.status == "confirmed"] == [before.reservations[0]]
    assert next(item for item in after.offers if item.id == "OFFER-G2").status == "superseded"
    assert any(item.group_id == "G2" and item.status == "proposed" for item in after.offers)
    assert confirmation_accounting_issues(after) == []
    assert not ai.decisions and not travel.calls


def third_group(world):
    group = world.groups[0].model_copy(deep=True)
    group.id, group.name, group.token = "G3", "G3", "offline-third-group"
    world.groups.append(group)
    world.incidents[0].group_ids.append(group.id)


@pytest.mark.parametrize("reservations", [False, True])
@pytest.mark.parametrize("status", ["confirmed", "held", "proposed", "accepted"])
def test_three_groups_count_fixed_itinerary_capacity_once_even_without_reservation_rows(setup, reservations, status):
    _, store, _, _, _ = setup
    world = store.read()
    canonical_shared(world, reservations=reservations)
    third_group(world)
    scope = None
    if status != "confirmed":
        world.groups[1].status = "offered"
        world.offers[0].status = status
        world.offers[0].external_bookings = []
        for item in world.reservations:
            item.status = status
        scope = {"G1", "G3"}  # The untouched pending group remains fixed.
    before = world.model_dump(mode="json", exclude={"sim_time"})
    plan = build_candidate(world, Strategy(), scope_group_ids=scope)
    assert plan.validation["valid"], plan.validation
    assert plan.recovered_passengers == 1
    assert len(plan.allocations) == len(plan.exceptions) == 1
    assert {plan.allocations[0].group_id, plan.exceptions[0]["group_id"]} == {"G1", "G3"}
    assert plan.validation["total_committed_cost"] == 100
    assert world.model_dump(mode="json", exclude={"sim_time"}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reservations", [False, True])
async def test_global_replan_allocates_one_remaining_seat_without_reconstructing_fixed_reservations(setup, reservations):
    _, store, jobs, ai, travel = setup
    def seed(world):
        canonical_shared(world, reservations=reservations)
        third_group(world)
    store.mutate("fixture-three-group-capacity", {}, store.read().version, seed)
    before = store.read()
    await jobs.service.replan(ReplanRequest(expected_version=before.version,
        idempotency_key="offline-three-group-replan", mode="solver"))
    after = store.read()
    assert next(item for item in after.offers if item.group_id == "G2") == before.offers[0]
    assert [item for item in after.reservations if item.group_id == "G2"] == before.reservations
    assert len([item for item in after.offers if item.status == "proposed"]) == 1
    assert len([item for item in after.groups if item.status == "exception"]) == 1
    assert after.decisions[-1].candidates[0].validation["valid"]
    assert after.resources == before.resources and not ai.decisions and not travel.calls


@pytest.mark.parametrize("reservations", [False, True])
def test_fixed_vehicle_occupancy_without_reservations_preserves_the_valid_shared_service(setup, reservations):
    _, store, _, _, _ = setup
    world = store.read()
    canonical_shared(world, reservations=reservations)
    world.resources[0].vehicle_id = "SHARED-VEHICLE"
    conflicting = world.resources[0].model_copy(deep=True)
    conflicting.id = "CHEAPER-OVERLAPPING-SERVICE"
    conflicting.departure += 10
    conflicting.arrival += 10
    conflicting.cost_per_person = 10
    world.resources.append(conflicting)
    before = world.model_dump(mode="json", exclude={"sim_time"})
    plan = build_candidate(world, Strategy(objective="lowest_cost"))
    assert plan.validation["valid"], plan.validation
    assert plan.recovered_passengers == 1
    assert [leg.resource_id for leg in plan.allocations[0].itinerary.legs] == ["BOOKED"]
    assert world.model_dump(mode="json", exclude={"sim_time"}) == before
