"""Authenticated read-only APL boundary. All supplier traffic is mocked."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.ground_transfers import GroundTransfers
from app.main import create_app
from app.providers.apl import APLQuoteRequest
from app.providers.base import ProviderError
from app.store import DomainError


def query():
    return {"origin": "Heathrow Terminal 5", "destination": "W1K 1LN", "passengers": 2, "suitcases": 2,
        "pickup_at": "2090-11-02T14:00:00Z"}


def test_quote_and_status_require_operator_and_do_not_mutate_world(tmp_path, monkeypatch):
    app = create_app(Settings(_env_file=None, database_path=str(tmp_path / "transfers.sqlite3"), operator_token="x" * 40,
        apl_api_key="fixture-apl-key"))
    calls = []

    async def quote(body):
        calls.append(body.model_dump(mode="json"))
        return {"provider": "Airport Pickups London", "options": [{"price": {"amount": "76", "currency": "GBP"}}],
            "expires_at": None, "source_quoted_at": None, "request": body.model_dump(mode="json")}

    monkeypatch.setattr(app.state.ground_transfers.provider, "quote", quote)
    async def unavailable_fx():
        return {"table": None, "code": "fx_http_error"}
    monkeypatch.setattr(app.state.ground_transfers.fx, "_fetch", unavailable_fx)
    with TestClient(app) as client:
        assert client.get("/api/transfers/status").status_code == 401
        assert client.post("/api/transfers/quote", json=query()).status_code == 401
        assert calls == []
        headers = {"Authorization": "Bearer " + "x" * 40}
        status = client.get("/api/transfers/status", headers=headers)
        assert status.status_code == 200 and status.json()["configured"] is True
        assert status.json()["currency"] == "GBP" and status.json()["booking_supported"] is False
        assert "fixture-apl-key" not in status.text
        before = client.get("/api/world", headers=headers).json()
        response = client.post("/api/transfers/quote", json=query(), headers=headers)
        assert response.status_code == 200 and len(calls) == 1
        assert response.json()["options"][0]["price"] == {"amount": "76", "currency": "GBP"}
        assert response.json()["options"][0]["eur_reference_estimate"] is None
        assert response.json()["fx"]["state"] == "unavailable" and response.json()["fx"]["settlement_guaranteed"] is False
        assert response.headers["cache-control"] == "no-store"
        after = client.get("/api/world", headers=headers).json()
        for field in ("version", "resources", "groups", "offers", "reservations", "events", "decisions"):
            assert after[field] == before[field]
        assert client.post("/api/transfers/book", json=query(), headers=headers).status_code == 404
    assert app.state.ground_transfers.status()["configured"] is False
    assert app.state.ground_transfers.provider._http.client.is_closed
    assert app.state.ground_transfers.fx.client.is_closed


def test_quote_schema_and_missing_configuration_fail_without_external_calls(tmp_path, monkeypatch):
    app = create_app(Settings(_env_file=None, database_path=str(tmp_path / "missing.sqlite3")))
    calls = []

    async def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError("External I/O is not expected")

    monkeypatch.setattr(app.state.ground_transfers.provider._http, "_request", unexpected)
    with TestClient(app) as client:
        assert client.get("/api/transfers/status").json()["configured"] is False
        for changed in ({"suitcases": -1}, {"passengers": True}, {"pickup_at": "2090-11-02T14:00:00"}, {"passenger_email": "private@example.org"}):
            response = client.post("/api/transfers/quote", json=query() | changed)
            assert response.status_code == 422
            assert "private@example.org" not in response.text
        response = client.post("/api/transfers/quote", json=query())
        assert response.status_code == 503 and response.json()["code"] == "not_configured"
        assert calls == []


@pytest.mark.asyncio
async def test_service_rejects_concurrent_calls_without_queuing_and_releases_after_timeout():
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def quote(body):
        calls.append(body)
        started.set()
        await release.wait()
        return {"options": []}

    async def close():
        calls.append("closed")

    provider = SimpleNamespace(configured=True, quote=quote, aclose=close)
    service = GroundTransfers(SimpleNamespace(request_timeout_seconds=1), provider=provider)
    async def unavailable_fx():
        return {"table": None, "code": "fx_http_error"}
    service.fx._fetch = unavailable_fx
    first = asyncio.create_task(service.quote(APLQuoteRequest(**query())))
    await started.wait()
    with pytest.raises(DomainError) as failure:
        await service.quote(APLQuoteRequest(**query()))
    assert failure.value.status == 409 and len(calls) == 1
    release.set()
    assert (await first)["options"] == []
    release.clear()
    service.timeout = 0.01
    with pytest.raises(ProviderError) as failure:
        await service.quote(APLQuoteRequest(**query()))
    assert failure.value.code == "timeout" and len(calls) == 2
    release.set()
    assert (await service.quote(APLQuoteRequest(**query())))["options"] == []
    await service.aclose()
    assert calls[-1] == "closed" and service.status()["configured"] is False
    with pytest.raises(DomainError) as failure:
        await service.quote(APLQuoteRequest(**query()))
    assert failure.value.status == 503
