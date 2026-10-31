"""Consumer references preserve source money and never allocate unknown seats."""
import asyncio
import copy
from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.coach_fares import CoachFares
from app.config import Settings
from app.main import create_app
from app.providers.base import ProviderError
from app.providers.bileteria import BileteriaSearchRequest
from app.providers.reference_fx import EcbRateTable
from app.store import DomainError


NOW = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)


def query():
    return {"origin_airport": "CRA", "destination_airport": "OTP", "travel_date": "2026-11-02", "adults": 2}


def reference():
    return {"provider": "BILETERIA", "request": query(), "received_at": NOW.isoformat(),
        "source_url": "https://www.bileteria.ro/Transport/AeroportCraiova-Otopeni?zi=2026-11-02&pas=2-0-0-0-0",
        "booking_supported": False, "rejected_count": 0, "notices": ["Seat availability is unverified."],
        "options": [{"id": "fixture-reference", "operator": "Fixture operator", "adults": 2,
            "departure_date": "2026-11-02", "arrival_date": "2026-11-02",
            "departure_time": "00:15", "arrival_time": "03:20", "duration_text": "3h 05m",
            "departure_stop": {"name": "Terminal Plecari/Sosiri", "locality": "Aeroport Craiova"},
            "arrival_stop": {"name": "Terminal PLECARI/ DEPARTURES", "locality": "Aeroport Otopeni"},
            "price": {"amount": "190.00", "currency": "RON"}, "price_scope": "requested_party",
            "seat_availability": None, "supplier_valid_until": None, "purchase_action_enabled": True}]}


async def no_close():
    pass


def service(*, search=None, rates=None):
    async def default_search(request):
        return reference()
    async def default_rates():
        return {"table": None, "code": "fx_unavailable"}
    return CoachFares(SimpleNamespace(request_timeout_seconds=1),
        provider=SimpleNamespace(configured=True, search=search or default_search, aclose=no_close),
        fx=SimpleNamespace(MAX_RATE_AGE_DAYS=3, rates=rates or default_rates, aclose=no_close), now=lambda: NOW)


def rate_table(*, day=date(2026, 10, 30), rates=None):
    return EcbRateTable(day, NOW, MappingProxyType(rates if rates is not None else {"RON": Decimal("5.00")}))


def test_exact_ron_is_preserved_with_separate_eur_reference_not_inventory():
    original = reference()
    before = copy.deepcopy(original)
    result = service().enrich(original, {"table": rate_table(), "cache": "fresh"})
    option = result["options"][0]
    assert original == before
    assert option["price"] == {"amount": "190.00", "currency": "RON"}
    assert option["eur_reference_estimate"] == {"amount": "38.00", "currency": "EUR",
        "kind": "ecb_reference_estimate", "rounding": "half_even_to_cent"}
    assert result["fx"]["source_currency"] == "RON"
    assert result["fx"]["reference"]["source_units_per_eur"] == "5.00"
    assert result["fx"]["reference"]["rate_date"] == "2026-10-30"
    assert result["fx"]["reference"]["max_age_days"] == 3
    assert result["fx"]["settlement_guaranteed"] is False
    assert option["seat_availability"] is None and option["supplier_valid_until"] is None
    assert result["booking_supported"] is False
    assert not {"capacity", "valid_until", "budget_total_cost", "cost_evidence"}.intersection(option)


@pytest.mark.parametrize("table,state", [
    (None, "unavailable"),
    (rate_table(day=date(2026, 10, 27)), "stale_rate"),
    (rate_table(rates={"GBP": Decimal("0.83")}), "missing_rate"),
])
def test_missing_or_stale_ron_reference_never_hides_original_fare(table, state):
    result = service().enrich(reference(), {"table": table, "cache": "cached"})
    assert result["fx"]["state"] == state
    assert result["fx"]["source_currency"] == "RON"
    assert result["options"][0]["eur_reference_estimate"] is None
    assert result["options"][0]["price"] == {"amount": "190.00", "currency": "RON"}


def test_wrong_currency_never_uses_ron_exchange_rate():
    source = reference()
    source["options"][0]["price"]["currency"] = "GBP"
    result = service().enrich(source, {"table": rate_table()})
    assert result["fx"]["state"] == "unavailable"
    assert result["fx"]["code"] == "fx_conversion_unavailable"
    assert result["options"][0]["eur_reference_estimate"] is None


def test_authenticated_search_is_read_only_and_has_no_booking_endpoint(tmp_path, monkeypatch):
    app = create_app(Settings(_env_file=None, database_path=str(tmp_path / "coach.sqlite3"),
        operator_token="c" * 40, operation_mode="live"))
    calls = []
    async def search(body):
        calls.append(body.model_dump(mode="json"))
        return reference()
    async def rates():
        return {"table": None, "code": "fx_http_error"}
    monkeypatch.setattr(app.state.coach_fares.provider, "search", search)
    monkeypatch.setattr(app.state.coach_fares.fx, "rates", rates)
    with TestClient(app) as client:
        assert client.get("/api/coaches/status").status_code == 401
        assert client.post("/api/coaches/search", json=query()).status_code == 401
        assert calls == []
        headers = {"Authorization": "Bearer " + "c" * 40}
        status = client.get("/api/coaches/status", headers=headers).json()
        assert status["configured"] and not status["booking_supported"] and not status["inventory_verified"]
        before = client.get("/api/world", headers=headers).json()
        response = client.post("/api/coaches/search", json=query(), headers=headers)
        assert response.status_code == 200 and calls == [query()]
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["options"][0]["price"]["currency"] == "RON"
        after = client.get("/api/world", headers=headers).json()
        for world in (before, after):
            world.pop("sim_time", None)
        assert before == after
        assert client.post("/api/coaches/book", json=query(), headers=headers).status_code == 404
        for change in ({"adults": True}, {"adults": 9}, {"travel_date": 42},
                       {"passenger_email": "not-to-be-forwarded@example.org"}, {"origin_airport": "LHR"}):
            rejected = client.post("/api/coaches/search", json=query() | change, headers=headers)
            assert rejected.status_code == 422
            assert "not-to-be-forwarded@example.org" not in rejected.text
        assert len(calls) == 1
    assert app.state.coach_fares.status()["configured"] is False


@pytest.mark.asyncio
async def test_invalid_source_request_and_no_results_skip_fx():
    fx_calls = []
    async def rates():
        fx_calls.append(True)
        raise AssertionError("No FX fetch is justified")
    async def invalid(request):
        raise ProviderError("Choose a supported future departure.", provider="BILETERIA", code="invalid_request")
    subject = service(search=invalid, rates=rates)
    with pytest.raises(DomainError) as error:
        await subject.search(BileteriaSearchRequest(**query()))
    assert error.value.status == 422 and fx_calls == []
    async def empty(request):
        return reference() | {"options": []}
    subject.provider.search = empty
    result = await subject.search(BileteriaSearchRequest(**query()))
    assert result["fx"]["state"] == "not_requested" and fx_calls == []


@pytest.mark.asyncio
async def test_fx_failure_preserves_fare_and_does_not_retry_source():
    calls = []
    async def search(request):
        calls.append("source")
        return reference()
    async def rates():
        calls.append("fx")
        raise RuntimeError("Fixture reference failure")
    result = await service(search=search, rates=rates).search(BileteriaSearchRequest(**query()))
    assert calls == ["source", "fx"]
    assert result["options"][0]["price"]["amount"] == "190.00"
    assert result["options"][0]["eur_reference_estimate"] is None


@pytest.mark.asyncio
async def test_concurrent_search_timeout_and_shutdown_do_not_retry():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def search(request):
        calls.append(True)
        entered.set()
        await release.wait()
        return reference() | {"options": []}
    subject = service(search=search)
    request = BileteriaSearchRequest(**query())
    running = asyncio.create_task(subject.search(request))
    await entered.wait()
    with pytest.raises(DomainError) as error:
        await subject.search(request)
    assert error.value.status == 409 and len(calls) == 1
    release.set()
    await running
    release.clear()
    subject.timeout = 0.01
    with pytest.raises(ProviderError) as error:
        await subject.search(request)
    assert error.value.code == "timeout" and len(calls) == 2
    await subject.aclose()
    with pytest.raises(DomainError) as error:
        await subject.search(request)
    assert error.value.status == 503 and len(calls) == 2
