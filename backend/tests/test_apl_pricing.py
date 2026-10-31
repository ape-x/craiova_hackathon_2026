"""Authored supplier/ECB fixtures only; conftest blocks all real transports."""
import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import localcontext
from types import SimpleNamespace

import httpx
import pytest
import app.pricing as pricing
import app.solver as solver
from app.models import Airport, Offer, PassengerGroup, Resource, Strategy, World
from app.providers.ai import AIProvider, compact_world
from app.providers.base import ProviderError
from app.service import RecoveryService
from app.store import DomainError, refresh_stats


NOW = datetime(2030, 1, 4, 17, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(pricing, "datetime", Clock)
    monkeypatch.setattr(solver, "datetime", Clock)


def quote():
    return {
        "provider": pricing.APL_SOURCE, "source_url": pricing.APL_DOCS, "state": "ready", "transport": "private_transfer",
        "received_at": NOW.isoformat(), "source_quoted_at": None, "expires_at": None,
        "request": {"passengers": 2, "suitcases": 2, "origin": "Declared terminal", "destination": "Sourced property"},
        "options": [{"id": "APLQ-fixture", "price": {"amount": "76.00", "currency": "GBP"}, "price_scope": "per_vehicle",
            "max_passengers": 3, "max_suitcases": 3, "fits_request": True,
            "eur_reference_estimate": {"amount": "95.00", "currency": "EUR", "kind": "ecb_reference_estimate", "rounding": "half_even_to_cent"}}],
        "fx": {"state": "ready", "source": pricing.ECB_SOURCE, "source_url": pricing.ECB_URL,
            "settlement_guaranteed": False, "revalidation_required": True,
            "reference": {"source": pricing.ECB_SOURCE, "source_url": pricing.ECB_URL, "base_currency": "EUR",
                "rate_date": "2030-01-04", "retrieved_at": NOW.isoformat(), "as_of": NOW.isoformat(),
                "age_calendar_days": 0, "age_timezone": "Europe/Berlin", "max_age_days": 3, "source_units_per_eur": "0.8"}},
    }


def state():
    world = World(scenario_id="price-fixture", epoch="2030-01-04T00:00:00Z", mode="live", sim_time=600, budget=200,
        airports=[Airport(id="LHR", code="LHR", name="Airport", lat=51.47, lon=-0.46),
            Airport(id="HP", code="HP", name="Sourced property", lat=51.52, lon=-0.29, kind="hotel"),
            Airport(id="OTP", code="OTP", name="Destination", lat=44.57, lon=26.09)],
        groups=[PassengerGroup(id="G1", name="Planning group", size=2, origin="LHR", destination="OTP",
            ready_at=600, deadline=900, budget=200, original_flight_id="arrived", token="private-fixture", source="operator_input")], resources=[])
    source = quote()
    world.resources = [Resource(id="APL1", name="Private transfer", kind="bus", transport_mode="private_transfer",
        origin="LHR", destination="HP", departure=620, arrival=660, capacity=2, accessible=False,
        cost_per_person=0, budget_total_cost=95, source=pricing.APL_SOURCE, capacity_scope="private_vehicle_quote",
        eligible_group_ids=["G1"], requires_revalidation=True,
        provenance={"apl_quote": source, "apl_option_id": "APLQ-fixture"},
        price_evidence=pricing.build_apl_price_evidence(source, "APLQ-fixture"),
        quote_freshness=pricing.AplQuoteFreshness(basis="app_receipt", received_at=source["received_at"], max_age_seconds=120)),
        Resource(id="EUR1", name="Documented service", kind="bus", origin="HP", destination="OTP", departure=680,
            arrival=740, capacity=2, cost_per_person=10, source="documented_operator_service",
            valid_until=(NOW + timedelta(hours=1)).isoformat())]
    return world


def test_exact_gbp_and_approximate_eur_propagate_once_per_vehicle_through_plan():
    world = state()
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2 and plan.total_cost == 115
    itinerary = plan.allocations[0].itinerary
    assert itinerary.legs[0].cost == 95  # not multiplied by two passengers
    assert itinerary.legs[0].price_evidence == world.resources[0].price_evidence
    assert itinerary.legs[0].transport_mode == "private_transfer"
    assert itinerary.legs[1].price_evidence is None
    expected = {"kind": "includes_ecb_reference", "total_eur": "115.00", "estimated_eur": "95.00",
        "original_gbp": "76.00", "policy_id": "ecb_reference_v1", "settlement_guaranteed": False, "revalidation_required": True}
    assert itinerary.cost_evidence.model_dump() == plan.cost_evidence.model_dump() == expected
    assert world.resources[0].valid_until is None and world.resources[0].quote_total_cost is None
    assert world.resources[0].provenance.get("quoted_at") is None


@pytest.mark.parametrize("budget,expected", [(80, 0), (114.99, 0), (115, 2)])
@pytest.mark.parametrize("scope", ["group", "world"])
def test_approved_eur_estimate_not_gbp_is_used_for_each_budget(scope, budget, expected):
    world = state()
    if scope == "group":
        world.groups[0].budget = budget
    else:
        world.budget = budget
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == expected


def test_multiple_private_vehicle_legs_keep_each_rate_and_exact_gbp_total():
    world = state()
    second_quote = quote()
    second_quote["options"][0].update(id="APLQ-second")
    second_quote["options"][0]["price"]["amount"] = "18.00"
    second_quote["options"][0]["eur_reference_estimate"]["amount"] = "20.00"
    second_quote["fx"]["reference"].update(rate_date="2030-01-03", age_calendar_days=1, source_units_per_eur="0.9")
    second = world.resources[0].model_copy(deep=True)
    second.id, second.origin, second.destination, second.departure, second.arrival = "APL2", "HP", "OTP", 680, 740
    second.provenance = {"apl_quote": second_quote, "apl_option_id": "APLQ-second"}
    second.price_evidence = pricing.build_apl_price_evidence(second_quote, "APLQ-second")
    second.budget_total_cost = 20
    world.resources[1] = second
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2
    assert plan.cost_evidence.total_eur == plan.cost_evidence.estimated_eur == "115.00"
    assert plan.cost_evidence.original_gbp == "94.00"
    assert [leg.price_evidence.fx_reference.source_units_per_eur for leg in plan.allocations[0].itinerary.legs] == ["0.8", "0.9"]


def test_conversion_keeps_original_decimal_and_uses_half_even_independent_context():
    data = quote()
    data["options"][0]["price"]["amount"] = "1.01"
    data["options"][0]["eur_reference_estimate"]["amount"] = "1.26"
    with localcontext() as context:
        context.prec = 4
        result = pricing.build_apl_price_evidence(data, "APLQ-fixture")
    assert result.original.amount == "1.01" and result.estimated_eur.amount == "1.26"


@pytest.mark.parametrize("amount", ["٧٦.٠٠", "７６.００", "76.٠٠", "1e2", "NaN", 76.0, True])
def test_money_evidence_requires_canonical_ascii_decimal_strings(amount):
    with pytest.raises(ValueError):
        pricing.exact_amount(amount)


@pytest.mark.parametrize("change", [
    lambda q: q["options"][0]["price"].update(currency="EUR"),
    lambda q: q["options"][0]["eur_reference_estimate"].update(amount="94.99"),
    lambda q: q["options"][0].update(max_passengers=1),
    lambda q: q["options"][0].update(max_suitcases=1),
    lambda q: q["fx"].update(state="stale_rate"),
    lambda q: q["fx"]["reference"].update(age_calendar_days=1),
    lambda q: q["fx"]["reference"].update(source_units_per_eur="0.9"),
    lambda q: q["fx"]["reference"].update(retrieved_at=(NOW - timedelta(days=1)).isoformat()),
    lambda q: q["fx"]["reference"].update(source_url="https://example.invalid/rate"),
    lambda q: q["fx"].update(settlement_guaranteed=0),
    lambda q: q.update(expires_at=(NOW + timedelta(hours=1)).isoformat()),
    lambda q: q.update(received_at=(NOW + timedelta(seconds=1)).isoformat()),
    lambda q: q["options"].append(copy.deepcopy(q["options"][0])),
])
def test_mismatching_or_incomplete_source_evidence_cannot_create_planning_price(change):
    data = quote()
    change(data)
    with pytest.raises(ValueError):
        pricing.build_apl_price_evidence(data, "APLQ-fixture")


@pytest.mark.parametrize("change", [
    lambda r: setattr(r, "price_evidence", None),
    lambda r: setattr(r, "quote_freshness", None),
    lambda r: setattr(r, "budget_total_cost", 94.999),
    lambda r: setattr(r, "quote_total_cost", 95),
    lambda r: setattr(r, "cost_per_person", 47.5),
    lambda r: setattr(r, "eligible_group_ids", []),
    lambda r: setattr(r, "capacity", 3),
    lambda r: setattr(r, "transport_mode", None),
    lambda r: r.provenance["apl_quote"]["options"][0]["price"].update(amount="75.00"),
    lambda r: r.provenance.update(quoted_at=NOW.isoformat()),
])
def test_resource_cannot_silently_lose_or_change_estimate_scope(change):
    world = state()
    change(world.resources[0])
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and not plan.allocations


@pytest.mark.parametrize("change", [
    lambda p: setattr(p.allocations[0].itinerary.legs[0], "price_evidence", None),
    lambda p: setattr(p.allocations[0].itinerary.legs[0], "cost", 95.001),
    lambda p: setattr(p.allocations[0].itinerary, "cost_evidence", None),
    lambda p: setattr(p, "cost_evidence", None),
    lambda p: setattr(p.cost_evidence, "original_gbp", "75.00"),
    lambda p: setattr(p.allocations[0].itinerary.cost_evidence, "estimated_eur", "94.00"),
    lambda p: setattr(p, "total_cost", 115.001),
])
def test_independent_plan_validator_rejects_missing_or_tampered_money(change):
    world = state()
    plan = solver.build_candidate(world, Strategy())
    change(plan)
    assert not solver.validate_plan(world, plan)["valid"]
    service = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=60), None)
    before = world.model_dump_json()
    with pytest.raises(DomainError, match="authoritative validation"):
        service._hold_plan(world, plan, "solver", "Recover", "fixture", "fixture", [plan], 1)
    assert world.model_dump_json() == before


def test_receipt_boundary_and_rate_age_are_required_for_new_proposals():
    world = state()
    resource = world.resources[0]
    assert solver.live_resource_current(resource)
    assert pricing.valid_apl_resource_price(resource, world.groups[0], now=NOW + timedelta(seconds=120))
    assert not pricing.valid_apl_resource_price(resource, world.groups[0], now=NOW + timedelta(seconds=120, microseconds=1))
    assert not pricing.valid_apl_resource_price(resource, world.groups[0], now=NOW - timedelta(microseconds=1))
    old = quote()
    old["fx"]["reference"].update(rate_date="2029-12-31", age_calendar_days=3,
        retrieved_at=(NOW - timedelta(days=1)).isoformat(), as_of=(NOW - timedelta(days=1)).isoformat())
    with pytest.raises(ValueError, match="stale"):
        pricing.build_apl_price_evidence(old, "APLQ-fixture")


def test_confirmed_historical_evidence_survives_age_but_not_tampering():
    world = state()
    plan = solver.build_candidate(world, Strategy())
    resource = world.resources[0]
    old = NOW - timedelta(days=10)
    resource.provenance["apl_quote"]["received_at"] = old.isoformat()
    resource.provenance["apl_quote"]["fx"]["reference"].update(rate_date=old.date().isoformat(),
        retrieved_at=old.isoformat(), as_of=old.isoformat())
    resource.quote_freshness.received_at = old.isoformat()
    resource.price_evidence = pricing.build_apl_price_evidence(resource.provenance["apl_quote"], "APLQ-fixture", require_current=False)
    plan.allocations[0].itinerary.legs[0].price_evidence = resource.price_evidence.model_copy(deep=True)
    world.groups[0].status = "confirmed"
    resource.booking_status = "confirmed"
    assert not solver.live_resource_current(resource)
    assert solver.validate_plan(world, plan)["valid"]
    resource.budget_total_cost = 90
    assert not solver.validate_plan(world, plan)["valid"]


def test_pure_eur_legacy_itineraries_remain_without_estimate_metadata():
    world = state()
    world.groups[0].origin = "HP"
    world.resources = world.resources[1:]
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.total_cost == 20
    assert plan.cost_evidence is None and plan.allocations[0].itinerary.cost_evidence is None


def test_proposal_deadline_uses_app_receipt_without_inventing_source_expiry():
    world = state()
    plan = solver.build_candidate(world, Strategy())
    service = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=10**9), None)
    service._hold_plan(world, plan, "solver", "Recover", "fixture", "fixture", [plan], 1)
    offer = world.offers[0]
    assert datetime.fromisoformat(offer.expires_at) == NOW + timedelta(seconds=120)
    assert offer.itinerary.cost_evidence == plan.cost_evidence
    assert world.resources[0].valid_until is None and world.resources[0].provenance.get("quoted_at") is None


def test_stats_keep_numeric_compatibility_but_disclose_estimated_commitments():
    world = state()
    plan = solver.build_candidate(world, Strategy())
    world.offers = [Offer(id="O1", group_id="G1", itinerary=plan.allocations[0].itinerary,
        status="proposed", expires_at=(NOW + timedelta(seconds=120)).isoformat(), created_version=1)]
    refresh_stats(world)
    assert world.stats["committed_cost"] == 115 and world.stats["budget_remaining"] == 85
    assert world.stats["committed_cost_evidence"]["total_eur"] == "115.00"
    assert world.stats["committed_cost_evidence"]["original_gbp"] == "76.00"
    assert world.stats["confirmed_cost_evidence"] is None and world.stats["budget_remaining_is_estimate"] is True
    world.offers[0].status = "confirmed"
    refresh_stats(world)
    assert world.stats["confirmed_cost"] == 115
    assert world.stats["confirmed_cost_evidence"]["settlement_guaranteed"] is False
    world.offers[0].status = "expired"
    refresh_stats(world)
    assert world.stats["committed_cost"] == 0
    assert world.stats["committed_cost_evidence"] is world.stats["confirmed_cost_evidence"] is None
    assert world.stats["budget_remaining_is_estimate"] is False


@pytest.mark.asyncio
async def test_decisions_receives_price_evidence_and_describes_reference_estimate():
    world = state()
    plan = solver.build_candidate(world, Strategy())
    seen = []
    def handler(request):
        payload = json.loads(request.content)
        evidence = payload["input"][0]["content"][0]["data"]
        seen.append(evidence)
        return httpx.Response(200, json={"answers": [{"name": "approve_recovery_plan", "type": "choice", "choice": True}]})
    provider = AIProvider(SimpleNamespace(openai_api_key="mock-only", request_timeout_seconds=1), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = await provider.choose(world.model_dump(mode="json"), [plan.model_dump(mode="json")], "Recover")
        assert "estimat" in result["explanation"]
        assert seen[0]["validated_candidates"][0]["cost_evidence"]["original_gbp"] == "76.00"
        assert seen[0]["world"]["resources"][0]["price_evidence"]["estimated_eur"]["amount"] == "95.00"
        assert compact_world(world.model_dump(mode="json"))["resources"][0]["price_evidence"]["original"]["currency"] == "GBP"
        plan.cost_evidence = None
        with pytest.raises(ProviderError, match="monetary evidence"):
            await provider.choose(world.model_dump(mode="json"), [plan.model_dump(mode="json")], "Recover")
        assert len(seen) == 1
    finally:
        await provider.client.aclose()
