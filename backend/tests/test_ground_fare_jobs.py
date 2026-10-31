"""Offline job/ledger checks for consumer fare references, never seat inventory."""
import copy
from datetime import datetime, timedelta
from decimal import Decimal
import json

import pytest

from app.coach_fares import CoachFares
from app.hotel_location import property_zone
from app.providers.base import ProviderError
from app.providers.bileteria import BileteriaProvider, SEARCH_URL
from app.providers.reference_fx import EcbRateTable
from app.recovery_jobs import RecoveryCancellation, RecoveryJobs
from app.store import utcnow
from test_recovery_jobs import request, run_next, setup


class FakeCoach:
    configured = True
    _now = staticmethod(utcnow)
    validate_request = BileteriaProvider.validate_request

    def __init__(self):
        self.calls = []
        self.handler = None
        self.empty = False

    async def search(self, query):
        self.calls.append(query)
        if self.handler:
            await self.handler(query)
        day = query.travel_date.isoformat()
        url = SEARCH_URL + f"?zi={day}&pas={query.adults}-0-0-0-0"
        option = {
            "id": "coach-reference-" + str(query.adults), "operator": "Explicit fake coach",
            "departure_date": day, "departure_time": "13:00", "arrival_date": day, "arrival_time": "16:05",
            "duration_text": "3h 05min", "departure_stop": {"name": "Terminal Plecari/Sosiri", "locality": "Aeroport Craiova", "source_url": url},
            "arrival_stop": {"name": "Terminal PLECARI/DEPARTURES", "locality": "Otopeni", "source_url": url},
            "price": {"amount": f"{95 * query.adults}.00", "currency": "RON"}, "price_scope": "requested_party", "adults": query.adults,
            "seat_availability": None, "supplier_valid_until": None, "purchase_action_enabled": True,
            "source_url": url, "notices": ["Fixture public fare; no inventory or hold verified."],
        }
        return {"provider": "BILETERIA", "source_url": url, "received_at": utcnow().isoformat(), "request": query.model_dump(mode="json"),
            "options": [] if self.empty else [option], "rejected_count": 0, "rejection_reasons": [], "booking_supported": False, "notices": []}

    async def aclose(self):
        pass


class FakeFx:
    MAX_RATE_AGE_DAYS = 3

    def __init__(self):
        self.calls = 0
        self.error = None

    async def rates(self):
        self.calls += 1
        if self.error:
            return {"table": None, "cache": "fresh", "code": self.error}
        now = utcnow()
        return {"table": EcbRateTable(rate_date=now.date(), retrieved_at=now, source_units_per_eur={"RON": Decimal("5.00")}), "cache": "fresh"}

    async def aclose(self):
        pass


@pytest.fixture
def grounded(setup):
    settings, store, jobs, ai, travel = setup
    coach, fx = FakeCoach(), FakeFx()
    jobs.coach_fares = CoachFares(settings, provider=coach, fx=fx)
    return settings, store, jobs, ai, travel, coach, fx


def unchanged(store, before):
    assert store.read().model_dump(exclude={"sim_time"}) == before.model_dump(exclude={"sim_time"})


def codes(job):
    return {item["code"] for item in job["gaps"]}


@pytest.mark.asyncio
async def test_bus_only_dated_fare_and_counted_fx_remain_unallocated(grounded):
    settings, store, jobs, ai, travel, coach, fx = grounded
    before = store.read()
    req = request(store, modes=["bus"], max_provider_calls=3)
    jobs.start("CASE", req)
    result = await run_next(jobs)
    assert result["status"] == "blocked" and result["result"] is None
    assert result["calls_dispatched"] == 2 and len(coach.calls) == fx.calls == 1
    assert [step["stage"] for step in result["steps"]] == ["searching_ground", "reference_fx"]
    assert all(step["status"] == "succeeded" for step in result["steps"])
    assert not ai.plans and not ai.decisions and not travel.calls
    reference = result["ground_references"][0]
    assert reference["group_ids"] == ["G1"]
    query = reference["result"]["request"]
    group = before.groups[0]
    day = (datetime.fromisoformat(before.epoch) + timedelta(minutes=group.ready_at)).astimezone(property_zone("Europe/Bucharest")).date()
    assert query == {"origin_airport": "CRA", "destination_airport": "OTP", "travel_date": day.isoformat(), "adults": 1}
    option = reference["result"]["options"][0]
    assert option["price"] == {"amount": "95.00", "currency": "RON"}
    assert option["eur_reference_estimate"]["amount"] == "19.00"
    assert option["seat_availability"] is option["supplier_valid_until"] is None
    assert reference["result"]["booking_supported"] is False
    assert {"ground_availability_unverified", "no_complete_journey"} <= codes(result)
    assert "private-token" not in json.dumps(result) and "Private fixture name" not in json.dumps(result)
    unchanged(store, before)
    restarted = RecoveryJobs(settings, store, jobs.service, travel, ai, coach_fares=jobs.coach_fares)
    assert restarted.start("CASE", req)["replayed"]
    assert restarted.get(result["id"])["job"]["ground_references"] == result["ground_references"]
    assert restarted.ledger.claim_next() is None
    assert len(coach.calls) == fx.calls == 1


@pytest.mark.asyncio
async def test_identical_party_queries_share_one_observation_without_combining_groups(grounded):
    _, store, jobs, _, _, coach, fx = grounded
    jobs.start("CASE", request(store, modes=["bus"], group_ids=["G1", "G2"], max_provider_calls=3))
    result = await run_next(jobs)
    assert result["calls_dispatched"] == 2 and len(coach.calls) == fx.calls == 1
    assert result["ground_references"][0]["group_ids"] == ["G1", "G2"]
    assert coach.calls[0].adults == 1
    assert result["ground_references"][0]["result"]["options"][0]["price"]["amount"] == "95.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,coach_calls,fx_calls", [(3, 0, 0), (4, 1, 0), (5, 1, 1)])
async def test_ground_references_cannot_starve_a_valid_flight_plan(grounded, limit, coach_calls, fx_calls):
    _, store, jobs, ai, travel, coach, fx = grounded
    other = copy.deepcopy(store.read().groups[1])
    jobs.start("CASE", request(store, modes=["flight", "bus"], max_provider_calls=limit))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    assert result["calls_dispatched"] == 3 + coach_calls + fx_calls == limit
    assert len(coach.calls) == coach_calls and fx.calls == fx_calls
    assert len(ai.plans) == len(ai.decisions) == len(travel.calls) == 1
    context = ai.decisions[0][2]["context"]
    assert f"Consumer coach observations: {coach_calls}; displayed fare options: {coach_calls}" in context
    assert "seat availability and physical connections are unverified" in context
    assert "excluded from validated candidates" in context and "not resources, allocations or bookings" in context
    assert "Explicit fake coach" not in context and SEARCH_URL not in context and "95.00" not in context
    assert result["result"]["source_counts"] == {"Nuitee": 1}
    assert result["result"]["recovered_passengers"] == 1
    after = store.read()
    assert after.groups[1] == other
    assert all(item.source == "nuitee" for item in after.resources)
    assert all(item.kind == "flight" for offer in after.offers for item in offer.itinerary.legs)
    if limit == 3:
        assert not result["ground_references"]
        assert "ground_fare_allowance_uncovered" in codes(result)
    else:
        option = result["ground_references"][0]["result"]["options"][0]
        assert bool(option["eur_reference_estimate"]) is bool(fx_calls)
        assert "ground_availability_unverified" in codes(result)
        if not fx_calls:
            assert "ground_fare_fx_allowance_uncovered" in codes(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,step_status", [("timeout", "unknown"), ("http_503", "unknown"), ("invalid_response", "failed")])
async def test_optional_coach_failure_is_visible_but_does_not_cancel_independent_flight(grounded, failure, step_status):
    _, store, jobs, ai, _, coach, fx = grounded
    async def fail(_):
        raise ProviderError("Explicit mocked source failure", provider="BILETERIA", code=failure)
    coach.handler = fail
    jobs.start("CASE", request(store, modes=["flight", "bus"], max_provider_calls=5))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    assert result["calls_dispatched"] == 4 and len(coach.calls) == 1 and fx.calls == 0
    assert result["steps"][-2]["stage"] == "searching_ground" and result["steps"][-2]["status"] == step_status
    assert len(ai.decisions) == 1 and not result["ground_references"]
    assert "ground_fare_source_failed" in codes(result)


@pytest.mark.asyncio
async def test_optional_fx_failure_keeps_original_ron_and_valid_flight_plan(grounded):
    _, store, jobs, _, _, coach, fx = grounded
    fx.error = "fx_timeout"
    jobs.start("CASE", request(store, modes=["flight", "bus"], max_provider_calls=5))
    result = await run_next(jobs)
    assert result["status"] == "completed" and result["calls_dispatched"] == 5
    assert len(coach.calls) == fx.calls == 1
    option = result["ground_references"][0]["result"]["options"][0]
    assert option["price"] == {"amount": "95.00", "currency": "RON"} and option["eur_reference_estimate"] is None
    assert result["steps"][-2]["stage"] == "reference_fx" and result["steps"][-2]["status"] == "unknown"
    assert "ground_fare_fx_unavailable" in codes(result)


@pytest.mark.asyncio
async def test_budget_covers_fares_before_optional_fx_and_records_uncovered_groups(grounded):
    _, store, jobs, _, _, coach, fx = grounded
    def parties(world):
        for size in (2, 3, 4):
            identity = "G" + str(size)
            group = next((item for item in world.groups if item.id == identity), None)
            if group is None:
                group = world.groups[0].model_copy(deep=True)
                group.id, group.name, group.token = identity, identity, identity + "-private-token-" + "x" * 24
                world.groups.append(group)
                world.incidents[0].group_ids.append(identity)
            group.size = group.adult_count = size
            group.members = ["Fixture person"] * size
    store.mutate("distinct-parties", {}, store.read().version, parties)
    before = store.read()
    jobs.start("CASE", request(store, modes=["bus"], group_ids=["G1", "G2", "G3", "G4"], max_provider_calls=3))
    result = await run_next(jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == len(coach.calls) == 3
    assert fx.calls == 0 and len(result["ground_references"]) == 3
    assert {"ground_fare_allowance_uncovered", "ground_fare_fx_allowance_uncovered"} <= codes(result)
    assert next(item for item in result["gaps"] if item["code"] == "ground_fare_allowance_uncovered")["group_ids"] == ["G4"]
    unchanged(store, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("change,expected", [
    ({"origin": "SBZ"}, "ground_fare_route_unsupported"),
    ({"adult_count": 0}, "ground_fare_party_unsupported"),
    ({"size": 9, "adult_count": 9}, "ground_fare_party_unsupported"),
    ({"deadline": 1}, "ground_fare_window_closed"),
    ({"ready_at": 60 * 24 * 40, "deadline": 60 * 24 * 41}, "ground_fare_date_unsupported"),
])
async def test_unsupported_exact_scope_never_uses_a_substitute_query(grounded, change, expected):
    _, store, jobs, ai, travel, coach, fx = grounded
    def constraints(world):
        for name, value in change.items():
            setattr(world.groups[0], name, value)
    store.mutate("unsupported-ground-scope", {}, store.read().version, constraints)
    before = store.read()
    jobs.start("CASE", request(store, modes=["bus"]))
    result = await run_next(jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == 0
    assert not coach.calls and not fx.calls and not ai.decisions and not travel.calls
    assert expected in codes(result)
    unchanged(store, before)


@pytest.mark.asyncio
async def test_empty_fare_result_does_not_spend_an_fx_call(grounded):
    _, store, jobs, _, _, coach, fx = grounded
    coach.empty = True
    jobs.start("CASE", request(store, modes=["bus"]))
    result = await run_next(jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == 1
    assert len(coach.calls) == 1 and fx.calls == 0
    assert result["ground_references"][0]["result"]["options"] == []
    assert "ground_fare_not_found" in codes(result)


@pytest.mark.asyncio
async def test_cancellation_after_fare_stops_fx_and_decision_without_losing_received_facts(grounded):
    _, store, jobs, ai, _, coach, fx = grounded
    before = store.read()
    created = jobs.start("CASE", request(store, modes=["flight", "bus"], max_provider_calls=5))
    async def cancel(_):
        jobs.cancel(created["job"]["id"], RecoveryCancellation(idempotency_key="cancel-after-ground"))
    coach.handler = cancel
    result = await run_next(jobs)
    assert result["status"] == "cancelled" and result["calls_dispatched"] == 3
    assert len(coach.calls) == 1 and fx.calls == 0 and not ai.decisions
    assert len(result["ground_references"]) == 1
    unchanged(store, before)


@pytest.mark.asyncio
async def test_scope_drift_after_fare_prevents_fx_and_any_application(grounded):
    _, store, jobs, _, _, coach, fx = grounded
    def edit(world):
        world.groups[0].budget = 400
    async def drift(_):
        store.mutate("operator-edited-budget", {}, store.read().version, edit)
    coach.handler = drift
    jobs.start("CASE", request(store, modes=["bus"]))
    result = await run_next(jobs)
    assert result["status"] == "needs_review" and result["calls_dispatched"] == 1
    assert len(coach.calls) == 1 and fx.calls == 0
    assert len(result["ground_references"]) == 1
    assert not store.read().resources and not store.read().offers
    assert store.read().groups[0].budget == 400


@pytest.mark.asyncio
async def test_reference_search_date_uses_actual_readiness_in_bucharest_not_utc_day(grounded):
    _, store, jobs, _, _, coach, _ = grounded
    def evening(world):
        world.groups[0].ready_at = 2 * 1440 + 23 * 60 + 30
        world.groups[0].deadline = world.groups[0].ready_at + 600
    store.mutate("late-utc-readiness", {}, store.read().version, evening)
    before = store.read()
    ready = datetime.fromisoformat(before.epoch) + timedelta(minutes=before.groups[0].ready_at)
    jobs.start("CASE", request(store, modes=["bus"]))
    result = await run_next(jobs)
    assert result["status"] == "blocked" and coach.calls[0].travel_date == ready.astimezone(property_zone("Europe/Bucharest")).date()
    assert coach.calls[0].travel_date != ready.date()
    unchanged(store, before)
