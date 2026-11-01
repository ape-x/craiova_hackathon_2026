"""Confirmed case conflicts stop ordinary recovery before paid/source dispatch."""
from datetime import timedelta

import pytest

import app.recovery_jobs as recovery_module
from app.airport_restrictions import AirportRestrictionReportInput, AirportRestrictions
from app.live_commands import LiveCommands
from app.models import CommandRequest, Itinerary, Leg, Offer, Reservation, Resource
from app.recovery_jobs import (RecoveryJobRequest, fixed_confirmed_airport_conflicts,
                               scope_fingerprint)
from app.store import utcnow
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup, intent, interpretation
from test_recovery_jobs import request, run_next, setup


def seed_confirmed(store, *, other_case=False, kind="flight", connection=False):
    def seed(world):
        original = world.incidents[0]
        original.unavailable_airport_code = None
        case = original
        if other_case:
            case = original.model_copy(deep=True)
            case.id, case.group_ids, case.requested_flight_number = "OTHER", [], "RO999"
            world.incidents.append(case)
        group = world.groups[0].model_copy(deep=True)
        group.id, group.name, group.token = "CONF", "Confirmed fixture", "offline-confirmed-token"
        group.incident_id, group.original_flight_id = case.id, case.requested_flight_number
        group.status, group.destination = "confirmed", "OTP" if connection else "SBZ"
        world.groups.append(group)
        case.group_ids.append(group.id)
        resource = Resource(id="F-CONF", name="Recorded supplier-confirmed journey", kind=kind,
            origin=group.origin, destination=group.destination, departure=group.ready_at + 60,
            arrival=group.ready_at + 120, capacity=2, cost_per_person=25, source="operator_confirmed",
            valid_until=(utcnow() + timedelta(hours=2)).isoformat(), booking_status="confirmed",
            provenance={"segments": [{"origin": group.origin, "destination": "LRSB"},
                {"origin": "LRSB", "destination": "OTP"}]} if connection else {})
        world.resources.append(resource)
        world.offers.append(Offer(id="O-CONF", group_id=group.id, status="confirmed",
            expires_at=(utcnow() + timedelta(hours=2)).isoformat(), created_version=world.version + 1,
            external_bookings=[{"resource_id": resource.id, "confirmation_reference": "OFFLINE-BOOKED"}],
            itinerary=Itinerary(legs=[Leg(resource_id=resource.id, kind=resource.kind,
                origin=resource.origin, destination=resource.destination, departure=resource.departure,
                arrival=resource.arrival, cost=25)], arrival=resource.arrival, total_cost=25)))
        world.reservations.append(Reservation(id="R-CONF", offer_id="O-CONF", group_id=group.id,
            resource_id=resource.id, quantity=1, status="confirmed", expires_at=world.offers[-1].expires_at))
    store.mutate("seed-confirmed-preflight", {}, store.read().version, seed)
    return "OTHER" if other_case else store.read().incidents[0].id


def restrict(store, incident_id, *, operation="arrivals", action="restrict", key="preflight-report"):
    return AirportRestrictions(store).report(incident_id, AirportRestrictionReportInput(
        expected_version=store.read().version, idempotency_key=key, airport_code="SBZ",
        operations=[operation], action=action, description="Operator reports this case's SBZ operation restricted."))


def stable(store):
    return store.read().model_dump(exclude={"sim_time"})


def assert_blocked_without_calls(result, ai, travel):
    assert result["status"] == "blocked", result
    assert result["calls_dispatched"] == 0 and result["steps"] == []
    assert result["gaps"][0]["code"] == "confirmed_airport_restriction_conflict"
    assert result["gaps"][0]["group_ids"] == ["CONF"]
    assert "Manual review" in result["message"]
    assert not ai.plans and not ai.decisions and not travel.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("other_case", "voice", "connection"), [
    (False, False, False), (False, True, False), (True, False, False), (True, True, True),
])
async def test_report_only_then_ordinary_typed_or_voice_recovery_stops_before_first_call(setup, other_case, voice, connection):
    _, store, jobs, ai, travel = setup
    case_id = seed_confirmed(store, other_case=other_case, connection=connection)
    recorded = restrict(store, case_id)
    assert recorded["applied"] and recorded["confirmed_conflicts"] and "recovery_job" not in recorded
    before = stable(store)
    if voice:
        world = store.read()
        response = LiveCommands(store, jobs).route(CommandRequest(expected_version=world.version,
            idempotency_key="ordinary-voice-after-report", text="Recover RO321 for G1 using flights with maximum 4 calls"),
            world, interpretation(recovery=intent(incident_id="CASE", group_ids=["G1"], all_linked_groups=False)))
        job = response["recovery_job"]
        assert response["command_interpretation_calls"] == 1
    else:
        job = jobs.start("CASE", request(store))["job"]
    assert job["status"] == "queued"  # Ordinary start response/API remains unchanged.
    result = await run_next(jobs)
    assert_blocked_without_calls(result, ai, travel)
    assert stable(store) == before


@pytest.mark.asyncio
async def test_other_case_change_before_claim_is_checked_despite_unchanged_selected_scope_hash(setup):
    _, store, jobs, ai, travel = setup
    other = seed_confirmed(store, other_case=True)
    digest = scope_fingerprint(store.read(), "CASE", ["G1"])
    jobs.start("CASE", request(store))
    restrict(store, other)
    assert scope_fingerprint(store.read(), "CASE", ["G1"]) == digest
    before = stable(store)
    result = await run_next(jobs)
    assert_blocked_without_calls(result, ai, travel)
    assert stable(store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["after_preflight", "inside_dispatch"])
async def test_other_case_change_after_preflight_cannot_dispatch_first_call(setup, monkeypatch, timing):
    _, store, jobs, ai, travel = setup
    other = seed_confirmed(store, other_case=True)
    original_confirmed = next(offer for offer in store.read().offers if offer.id == "O-CONF").model_dump()
    jobs.start("CASE", request(store))
    changed = False
    def change_once():
        nonlocal changed
        if not changed:
            changed = True
            restrict(store, other)
    if timing == "after_preflight":
        original = recovery_module.flight_query_options
        def after_scan(*args, **kwargs):
            result = original(*args, **kwargs)
            change_once()
            return result
        monkeypatch.setattr(recovery_module, "flight_query_options", after_scan)
    else:
        original = jobs.ledger.dispatch
        def during_dispatch(*args, **kwargs):
            change_once()
            return original(*args, **kwargs)
        monkeypatch.setattr(jobs.ledger, "dispatch", during_dispatch)
    result = await run_next(jobs)
    assert changed and result["status"] == "needs_review", result
    assert result["calls_dispatched"] == 0 and result["steps"] == []
    assert not ai.plans and not ai.decisions and not travel.calls
    assert next(offer for offer in store.read().offers if offer.id == "O-CONF").model_dump() == original_confirmed


@pytest.mark.asyncio
async def test_other_case_change_during_inflight_model_call_prevents_supplier_or_decision_call(setup, monkeypatch):
    _, store, jobs, ai, travel = setup
    other = seed_confirmed(store, other_case=True)
    original = ai.plan_recovery_queries
    async def changed_during_call(*args, **kwargs):
        result = await original(*args, **kwargs)
        restrict(store, other)
        return result
    monkeypatch.setattr(ai, "plan_recovery_queries", changed_during_call)
    jobs.start("CASE", request(store))
    result = await run_next(jobs)
    assert result["status"] == "needs_review" and result["calls_dispatched"] == 1
    assert len(ai.plans) == 1 and not travel.calls and not ai.decisions
    assert next(offer for offer in store.read().offers if offer.id == "O-CONF").status == "confirmed"


@pytest.mark.asyncio
async def test_retained_confirmed_itinerary_endpoints_suffice_without_reconstructing_missing_source(setup):
    _, store, jobs, ai, travel = setup
    case_id = seed_confirmed(store, other_case=True)
    restrict(store, case_id)
    store.mutate("missing-confirmed-source", {}, store.read().version,
        lambda world: setattr(world, "resources", [item for item in world.resources if item.id != "F-CONF"]))
    before = stable(store)
    jobs.start("CASE", request(store))
    result = await run_next(jobs)
    assert_blocked_without_calls(result, ai, travel)
    assert stable(store) == before and not store.read().resources


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["opposite_operation", "cleared", "ground_journey"])
async def test_nonconflicting_fixed_journey_does_not_block_valid_recovery(setup, variant):
    _, store, jobs, ai, travel = setup
    other = seed_confirmed(store, other_case=True, kind="bus" if variant == "ground_journey" else "flight")
    restrict(store, other, operation="departures" if variant == "opposite_operation" else "arrivals")
    if variant == "cleared":
        restrict(store, other, action="clear", key="clear-before-ordinary-recovery")
    assert fixed_confirmed_airport_conflicts(store.read()) == []
    before = next(offer for offer in store.read().offers if offer.id == "O-CONF").model_dump()
    jobs.start("CASE", request(store))
    result = await run_next(jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 3, result
    assert len(ai.plans) == len(ai.decisions) == len(travel.calls) == 1
    assert next(offer for offer in store.read().offers if offer.id == "O-CONF").model_dump() == before


@pytest.mark.parametrize("variant", ["historical_offer", "orphan_reservation"])
def test_only_fixed_confirmed_offers_prove_this_preflight_blocker(setup, variant):
    _, store, _, _, _ = setup
    other = seed_confirmed(store, other_case=True)
    restrict(store, other)
    def remove_fixed(world):
        if variant == "historical_offer":
            world.offers[0].status = "declined"
        else:
            world.offers = []
    store.mutate("fixture-no-fixed-offer", {}, store.read().version, remove_fixed)
    assert fixed_confirmed_airport_conflicts(store.read()) == []


@pytest.mark.asyncio
async def test_same_preflight_runs_before_hotel_provider_work(hotel_command_setup, monkeypatch):
    _, store, _, jobs, _, inputs = hotel_command_setup
    other = seed_confirmed(store, other_case=True)
    restrict(store, other)
    async def forbidden_hotel(*args, **kwargs):
        raise AssertionError("Hotel sourcing must not run for a fixed confirmed conflict")
    monkeypatch.setattr(recovery_module, "run_hotel_recovery", forbidden_hotel)
    jobs.start("RC-ONE", RecoveryJobRequest(expected_version=store.read().version,
        idempotency_key="hotel-fixed-conflict-job", group_ids=["G1"], modes=["flight", "hotel", "bus"],
        hotel_recovery=inputs, max_provider_calls=10))
    result = await run_next(jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == 0
    assert result["gaps"][0]["code"] == "confirmed_airport_restriction_conflict"
