"""The external-data boundary is read-only, authenticated and explicit."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def query():
    today = datetime.now(ZoneInfo("Europe/Bucharest")).date()
    return {
        "airport_code": "CRA", "destination_code": "OTP",
        "checkin": today.isoformat(), "checkout": (today + timedelta(days=1)).isoformat(),
        "adults": 2, "rooms": 1,
    }


def test_search_is_operator_only_and_does_not_change_scenario(tmp_path, monkeypatch):
    app = create_app(Settings(database_path=str(tmp_path / "real-data.sqlite3"), operator_token="x" * 40, _env_file=None))
    calls = []

    async def search(body):
        calls.append(body.model_dump(mode="json"))
        return {"criteria": body.model_dump(mode="json"), "test_marker": "external-only"}

    monkeypatch.setattr(app.state.real_data, "search", search)
    with TestClient(app) as client:
        assert client.post("/api/real-data/search", json=query()).status_code == 401
        assert calls == []
        headers = {"Authorization": "Bearer " + "x" * 40}
        before = client.get("/api/world", headers=headers).json()
        response = client.post("/api/real-data/search", json=query(), headers=headers)
        assert response.status_code == 200
        assert response.json()["test_marker"] == "external-only"
        assert len(calls) == 1
        after = client.get("/api/world", headers=headers).json()
        for key in ("version", "resources", "reservations", "offers", "events"):
            assert after[key] == before[key]


def test_search_validation_runs_before_external_calls(tmp_path, monkeypatch):
    app = create_app(Settings(database_path=str(tmp_path / "validation.sqlite3"), _env_file=None))
    calls = []

    async def search(body):
        calls.append(body)
        return {}

    monkeypatch.setattr(app.state.real_data, "search", search)
    invalid = [
        {"adults": 0}, {"rooms": 0}, {"rooms": 3},
        {"destination_code": "CRA"}, {"checkin": "2020-01-01"},
        {"checkout": query()["checkin"]}, {"airport_code": "https://example.com"},
        {"provider_url": "http://127.0.0.1"},
    ]
    with TestClient(app) as client:
        for override in invalid:
            response = client.post("/api/real-data/search", json={**query(), **override})
            assert response.status_code == 422, response.text
        assert calls == []


def test_provider_secrets_are_not_public_configuration(tmp_path):
    app = create_app(Settings(
        database_path=str(tmp_path / "secrets.sqlite3"), _env_file=None,
        google_maps_server_api_key="server-key-must-stay-private",
        booking_demand_api_token="booking-token-must-stay-private",
        booking_affiliate_id="12345",
        liteapi_api_key="sand-liteapi-token-must-stay-private",
        hotel_offers_provider="liteapi",
    ))
    with TestClient(app) as client:
        response = client.get("/api/config")
        assert response.status_code == 200
        assert "server-key-must-stay-private" not in response.text
        assert "booking-token-must-stay-private" not in response.text
        assert "sand-liteapi-token-must-stay-private" not in response.text
        assert response.json()["real_data"]["hotel_offers_source"] == "Nuitee / LiteAPI"
        assert response.json()["real_data"]["hotel_offers_environment"] == "sandbox"
        assert response.json()["real_data"]["hotel_offers_configured"] is False
