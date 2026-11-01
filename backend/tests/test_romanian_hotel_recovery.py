"""Romanian complete packages use isolated SQLite and fixture source transports only."""
import copy
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import importlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.hotel_policy import source_stay_policy
from app.providers.base import ProviderError
from app.providers.ecb_rates import EcbRates
from app.providers.real_data import RealDataProvider
from app.providers.reference_fx import EcbRateTable, ECB_ZONE
from app.providers.transfero import ZONE, parse_search_response
from app.recovery_jobs import RecoveryCancellation, RecoveryJobRequest
from app.romanian_hotel_recovery import RomanianHotelRecoveryInputs, _exact_property
from app.stay_requirements import REQUIREMENT_KEY
from app.store import DomainError, utcnow
from test_hotel_recovery_jobs import world_snapshot
from test_real_data import liteapi_data, search, settings
from test_recovery_jobs import setup, run_next
from test_transfero import fixture_source_terms


@pytest.mark.asyncio
async def test_exact_property_rates_send_only_hotel_ids_and_preserve_complete_pricing():
    payload = liteapi_data("114.48", rooms=1)
    payload["data"][0]["hotelId"] = payload["hotels"][0]["id"] = "lpce923"
    payload["data"][0]["roomTypes"][0]["rates"][0]["adultCount"] = 2
    payload["data"][0]["roomTypes"][0]["rates"][0]["retailRate"]["taxesAndFees"] = [
        {"included": False, "amount": "1.86", "currency": "EUR", "description": "Fixture mandatory fee"}]
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealDataProvider(settings(liteapi_api_key="prod_fixture", liteapi_environment="live"), client=client)
        result = await provider.hotel_offers("lpce923", search())
    assert len(requests) == 1
    assert requests[0]["hotelIds"] == ["lpce923"] and requests[0]["limit"] == 1
    assert not {"latitude", "longitude", "radius"}.intersection(requests[0])
    assert result[0]["id"] == "lpce923" and result[0]["total_price"] == "114.48"
    assert result[0]["pricing"]["mandatory_total"] == {"amount": "116.34", "currency": "EUR"}


@pytest.mark.asyncio
async def test_exact_property_rates_refuse_supplier_substitution_without_second_request():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=liteapi_data(rooms=1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealDataProvider(settings(liteapi_api_key="prod_fixture", liteapi_environment="live"), client=client)
        with pytest.raises(ProviderError):
            await provider.hotel_offers("lpce923", search(adults=3))
    assert len(calls) == 1


class Coach:
    configured = True
    def __init__(self, clock):
        self.clock, self.calls, self.handler = clock, [], None
        self.seats, self.extra_options = 3, True
    def validate_request(self, request):
        return request
    async def search(self, request):
        self.calls.append(request)
        if self.handler:
            await self.handler(request)
        fixtures = Path(__file__).parent / "fixtures"
        data = json.loads((fixtures / "transfero_direct_services.json").read_text())
        key = "outbound" if request.direction == "otp_to_hotel" else "return"
        row = copy.deepcopy(data[key])
        # Keep the exact observed schema, stops and attached source terms, but
        # explicitly replace dates/times/IDs for this offline scheduling case.
        row["segments"][0]["terms"] = (fixtures / "transfero_terms_57392.txt").read_text()
        departures = [time(8), time(10)] if key == "outbound" else [time(13), time(15)]
        rows = []
        for index, clock in enumerate(departures[:2 if self.extra_options else 1]):
            item = copy.deepcopy(row)
            departure = datetime.combine(request.travel_date, clock, ZONE)
            arrival = departure + timedelta(hours=3)
            for endpoint, instant in (("departure", departure), ("arrival", arrival)):
                item["segments"][0][endpoint].update(datetime=instant.strftime("%d.%m.%Y %H:%M"), date=instant.strftime("%d.%m.%Y"), time=instant.strftime("%H:%M"))
            item["id"] = str(100+index if key == "outbound" else 200+index)
            item["segments"][0]["linkid"] = item["id"]
            item["oraplecare"], item["orasosire"] = departure.strftime("%H:%M"), arrival.strftime("%H:%M")
            item["durata"] = item["segments"][0]["duration"] = "03:00"
            item["locuri"] = self.seats
            item["tickets"]["adulti"]["oneway"].update(rate=request.adults, pret_ron="260", pret="260", pret_u="260")
            rows.append(item)
        return parse_search_response(json.dumps({"d": rows, "i": []}).encode(), request, received_at=self.clock["now"])


class Hotel:
    hotel_offer_provider = "liteapi"
    liteapi_configured = True
    liteapi_sandbox = False
    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.complete, self.identity, self.empty = True, True, False
    async def hotel_offers(self, identity, query):
        self.calls.append(("rates", identity, query))
        if self.empty:
            return []
        return [{"id": identity, "name": "Hotel Aro Palace", "provider": "liteapi", "provider_offer_id": "fixture-room-offer",
            "currency": "EUR", "total_price": "114.48", "adults": query.adults, "rooms": query.rooms,
            "checkin": query.checkin.isoformat(), "checkout": query.checkout.isoformat(), "quoted_at": self.clock["now"].isoformat(), "expires_at": None,
            "pricing": {"status": "complete" if self.complete else "incomplete", "booking_total": {"amount": "114.48", "currency": "EUR"},
                "mandatory_total": {"amount": "116.34", "currency": "EUR"} if self.complete else None,
                "excluded_fee_totals": [{"amount": "1.86", "currency": "EUR"}], "unresolved_reasons": [] if self.complete else ["fixture missing tax"]}}]
    async def hotel_location(self, identity):
        self.calls.append(("metadata", identity))
        stamp = self.clock["now"].isoformat()
        return {"latitude": 45.645322, "longitude": 25.590917, "location_source": "Nuitee / LiteAPI", "location_retrieved_at": stamp,
            "postal_address": {"hotel_id": identity, "name": "Hotel Aro Palace" if self.identity else "Other hotel", "address": "Eroilor Boulevard Nr 27 - 29",
                "city": "Braşov", "country": "RO", "source": "Nuitee / LiteAPI", "retrieved_at": stamp},
            "stay_policy": source_stay_policy(identity, {"checkin_start": "03:00 PM", "checkout": "12:00 PM"}, stamp)}


class FX:
    MAX_RATE_AGE_DAYS = 3
    enrich = EcbRates.enrich
    def __init__(self, clock):
        self.clock, self.calls = clock, 0
        self._now = lambda: self.clock["now"]
    async def rates(self):
        self.calls += 1
        return {"table": EcbRateTable(rate_date=self.clock["now"].astimezone(ECB_ZONE).date(), retrieved_at=self.clock["now"], source_units_per_eur={"RON": Decimal("5.3447")}), "cache": "fresh"}


@pytest.fixture
def romania(setup, monkeypatch):
    _, store, jobs, ai, travel = setup
    clock = {"now": utcnow()}
    for module in ("store", "solver", "service", "live_service", "live_travel", "recovery_jobs", "recovery_ledger", "romanian_hotel_recovery", "inventory_lifecycle"):
        imported = importlib.import_module("app." + module)
        if hasattr(imported, "utcnow"):
            monkeypatch.setattr(imported, "utcnow", lambda: clock["now"])
    class SourceClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)
    monkeypatch.setattr(importlib.import_module("app.live_travel"), "datetime", SourceClock)
    monkeypatch.setattr(importlib.import_module("app.solver"), "datetime", SourceClock)
    day = clock["now"].astimezone(ZONE).date() + timedelta(days=2)
    checkin, checkout = datetime.combine(day, time(15), ZONE), datetime.combine(day+timedelta(days=1), time(12), ZONE)
    def configure(world):
        group = world.groups[0]
        group.origin, group.destination = "OTP", "CRA"
        group.size = group.adult_count = 2
        group.members = ["Fixture adult A", "Fixture adult B"]
        epoch = datetime.fromisoformat(world.epoch)
        group.ready_at = int((datetime.combine(day, time(6), ZONE)-epoch).total_seconds()/60)
        group.deadline = int((checkout+timedelta(hours=15)-epoch).total_seconds()/60)
        group.budget = 1000
        world.budget = 3000
    store.mutate("romanian-fixture", {}, store.read().version, configure)
    coach, hotel, fx = Coach(clock), Hotel(clock), FX(clock)
    jobs.transfero_provider, jobs.hotel_provider, jobs.fx_provider = coach, hotel, fx
    async def flight(query):
        departure = datetime.combine(query.departure_date, time(19), ZONE).astimezone(timezone.utc)
        arrival = departure+timedelta(hours=2)
        return {"state": "ready", "source": "Nuitee", "items": [{"id": "fixture-flight", "provider": "nuitee", "provider_offer_id": "fixture-flight",
            "name": "Fixture sourced flight", "origin": "OTP", "destination": "CRA", "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(),
            "total_price": "100.00", "currency": "EUR", "passengers": query.adults, "quoted_at": clock["now"].isoformat(), "expires_at": None,
            "mode": "live", "accessibility_confirmed": False, "segments": [{"origin": "OTP", "destination": "CRA", "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat()}]}]}
    travel.handler = flight
    inputs = {"source": "transfero_direct_aeroport", "hotel_id": "lpce923", "checkin_at": checkin.isoformat(), "checkout_at": checkout.isoformat(),
        "rooms": 1, "minimum_rest_minutes": 480, "suitcases": 2, "handbags": 0, "standard_baggage_confirmed": True,
        "airport_processing_minutes": 180, "hotel_connection_minutes": 20}
    return store, jobs, ai, travel, coach, hotel, fx, inputs, clock


def request(env, **changes):
    store, *_, inputs, clock = env
    return RecoveryJobRequest(**({"group_ids": ["G1"], "expected_version": store.read().version, "idempotency_key": "romanian-package-fixture",
        "modes": ["flight", "hotel", "bus"], "max_provider_calls": 8, "romanian_hotel_recovery": inputs} | changes))


@pytest.mark.asyncio
async def test_complete_chain_uses_eight_calls_and_commits_only_chosen_sources_once(romania):
    store, jobs, ai, travel, coach, hotel, fx, inputs, _ = romania
    before = world_snapshot(store)
    started = jobs.start("CASE", request(romania))
    assert started["job"]["romanian_hotel_recovery"] == RomanianHotelRecoveryInputs.model_validate(inputs).model_dump(mode="json")
    assert world_snapshot(store) == before and not coach.calls
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    assert result["calls_dispatched"] == 8 and len(result["steps"]) == 8
    assert all(step["status"] == "succeeded" for step in result["steps"])
    assert len(coach.calls) == 2 and len(hotel.calls) == 2 and fx.calls == 1
    assert len(travel.calls) == len(ai.plans) == len(ai.decisions) == 1
    world = store.read()
    assert world.version == before["version"]+1 and len(world.resources) == 4
    assert [leg.kind for leg in world.offers[0].itinerary.legs] == ["bus", "hotel", "bus", "flight"]
    assert world.groups[0].origin == "OTP" and world.groups[1].model_dump(mode="json") == before["groups"][1]
    assert world.groups[0].provenance[REQUIREMENT_KEY]["rooms"] == 1
    assert all(item.booking_status == "unbooked" for item in world.resources)
    assert world.offers[0].itinerary.total_cost == 313.64
    for item in world.resources:
        if item.source == "Transfero":
            assert item.quote_total_cost is None and item.budget_total_cost == 48.65
            assert item.price_evidence.original.currency == "RON"
    assert "12-hour" in ai.plans[0][2]["context"] and "not held" in ai.decisions[0][2]["context"]
    assert result["result"]["romanian_hotel_recovery"]["policy"]["facility_level_only"] is True
    assert jobs.start("CASE", request(romania, expected_version=before["version"]))["replayed"] is True
    assert len(coach.calls) == 2


@pytest.mark.parametrize("change", [{"max_provider_calls": 7}, {"group_ids": ["G1", "G2"]}, {"modes": ["hotel", "bus"]}])
def test_scope_and_allowance_preflight_has_no_source_or_world_change(romania, change):
    store, jobs, _, _, coach, *_ = romania
    before = world_snapshot(store)
    with pytest.raises((DomainError, ValidationError)):
        jobs.start("CASE", request(romania, **change))
    assert not coach.calls and world_snapshot(store) == before


@pytest.mark.parametrize("groups", [["g1"], ["G1", "G1"], ["G1!"], [None]])
def test_exact_package_group_scope_never_normalizes(romania, groups):
    with pytest.raises(ValidationError):
        request(romania, group_ids=groups)


@pytest.mark.parametrize("change", [{"suitcases": True}, {"standard_baggage_confirmed": 1}, {"hotel_id": "nearby"}, {"airport_processing_minutes": 179}, {"hotel_connection_minutes": 19}, {"checkin_at": "2030-11-01T15:00:00"}])
def test_typed_package_rejects_invented_or_ambiguous_inputs(romania, change):
    inputs = romania[-2]
    with pytest.raises(ValidationError):
        request(romania, romanian_hotel_recovery=inputs | change)


@pytest.mark.asyncio
@pytest.mark.parametrize("address,valid", [("27 Eroilor Boulevard", True), ("Eroilor Boulevard Nr 27 - 29", True), ("Eroilor 127", False), ("Eroilor 29", False), ("Eroilor 27-290", False)])
async def test_independently_verified_property_address_aliases_preserve_house_number(romania, address, valid):
    metadata = await romania[5].hotel_location("lpce923")
    metadata["postal_address"]["address"] = address
    if valid:
        _exact_property(metadata)
    else:
        with pytest.raises(DomainError):
            _exact_property(metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["no_seats", "incomplete_price", "hotel_identity", "late_checkin", "expired_during_ai", "receipt_rollback", "unknown_source"])
async def test_failure_preserves_world_and_never_retries(romania, monkeypatch, problem):
    store, jobs, ai, travel, coach, hotel, fx, inputs, clock = romania
    if problem == "no_seats": coach.seats = 1
    if problem == "incomplete_price": hotel.complete = False
    if problem == "hotel_identity": hotel.identity = False
    if problem == "late_checkin": inputs["checkin_at"] = (datetime.fromisoformat(inputs["checkin_at"])+timedelta(minutes=30)).isoformat()
    if problem == "expired_during_ai":
        original = ai.plan_recovery_queries
        async def delayed(*args):
            response = await original(*args)
            clock["now"] += timedelta(seconds=121)
            return response
        ai.plan_recovery_queries = delayed
    if problem == "receipt_rollback":
        def fail(*args): raise DomainError(409, "Fixture receipt insert failure")
        monkeypatch.setattr(jobs.ledger, "complete_in_transaction", fail)
    if problem == "unknown_source":
        async def failure(query):
            raise ProviderError("Fixture unknown source result", provider="Transfero", code="http_507")
        coach.handler = failure
    before = world_snapshot(store)
    req = request(romania)
    jobs.start("CASE", req)
    result = await run_next(jobs)
    assert result["status"] in ("blocked", "needs_review", "unknown_outcome"), result
    assert world_snapshot(store) == before
    calls = len(coach.calls)
    assert jobs.start("CASE", req)["replayed"] and len(coach.calls) == calls
    if problem in ("no_seats", "unknown_source"):
        assert result["calls_dispatched"] == 1 and not hotel.calls and not travel.calls
    if problem == "expired_during_ai":
        assert result["calls_dispatched"] == 6 and not travel.calls and not ai.decisions
    if problem == "late_checkin":
        assert result["status"] == "blocked" and "policy" in result["gaps"][0]["code"] and not travel.calls
    if problem == "unknown_source":
        assert result["status"] == "unknown_outcome" and result["steps"][0]["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "version"])
async def test_interruption_between_coach_requests_dispatches_only_once(romania, change):
    store, jobs, _, travel, coach, hotel, _, _, _ = romania
    job = jobs.start("CASE", request(romania))["job"]
    async def interrupt(query):
        if change == "cancel":
            jobs.cancel(job["id"], RecoveryCancellation(idempotency_key="romanian-cancel-fixture"))
        else:
            store.mutate("romanian-independent-change", {}, store.read().version, lambda world: setattr(world.groups[1], "budget", 499))
    coach.handler = interrupt
    result = await run_next(jobs)
    assert result["status"] == ("cancelled" if change == "cancel" else "needs_review"), result
    assert result["calls_dispatched"] == 1 and len(coach.calls) == 1 and not hotel.calls and not travel.calls
    assert not store.read().offers and not store.read().resources


@pytest.mark.asyncio
async def test_fixed_other_group_uses_same_service_stock_across_new_quote_receipt(romania):
    store, jobs, _, _, coach, _, _, _, clock = romania
    jobs.start("CASE", request(romania))
    first = await run_next(jobs)
    assert first["status"] == "completed", first
    fixed_offer = store.read().offers[0].model_dump(mode="json")
    def prepare(world):
        target = world.groups[1]
        source = world.groups[0]
        for field in ("origin", "destination", "size", "adult_count", "ready_at", "deadline", "budget"):
            setattr(target, field, getattr(source, field))
        target.members = ["Fixture adult C", "Fixture adult D"]
    store.mutate("romanian-second-party", {}, store.read().version, prepare)
    clock["now"] += timedelta(seconds=1)
    before = world_snapshot(store)
    jobs.start("CASE", request(romania, group_ids=["G2"], idempotency_key="romanian-second-group-job"))
    result = await run_next(jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == 7, result
    assert world_snapshot(store) == before and store.read().offers[0].model_dump(mode="json") == fixed_offer
    assert len(coach.calls) == 4


@pytest.mark.asyncio
async def test_replacement_at_capacity_archives_only_explicit_selected_revisions(romania, monkeypatch):
    import app.inventory_lifecycle as inventory
    store, jobs, _, _, _, _, _, _, clock = romania
    monkeypatch.setattr(inventory, "MAX_RESOURCES", 4)
    jobs.start("CASE", request(romania))
    first = await run_next(jobs)
    assert first["status"] == "completed", first
    old_ids = {resource.id for resource in store.read().resources}
    foreign = store.read().groups[1].model_dump(mode="json")
    clock["now"] += timedelta(seconds=1)
    jobs.start("CASE", request(romania, idempotency_key="romanian-replacement-job"))
    second = await run_next(jobs)
    assert second["status"] == "completed", (second["message"], second["gaps"], second["calls_dispatched"])
    world = store.read()
    assert len(world.resources) == 4 and {resource.id for resource in world.resources}.isdisjoint(old_ids)
    assert world.groups[1].model_dump(mode="json") == foreign
    assert next(offer for offer in world.offers if offer.id in first["result"]["offer_ids"]).status == "invalidated"
