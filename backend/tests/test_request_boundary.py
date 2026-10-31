import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import VoiceProvider


@pytest.fixture
def client(tmp_path):
    application = create_app(Settings(database_path=str(tmp_path / "request-boundary.sqlite3"), _env_file=None))
    with TestClient(application, raise_server_exceptions=False) as connection:
        yield connection


@pytest.mark.parametrize("sdp", ["not-an-sdp-offer", "v=0not-a-version-line", "v=0\r\n" + "x" * 65536])
def test_invalid_voice_sdp_is_rejected_before_provider_creation(client, monkeypatch, sdp):
    calls = []
    async def forbidden(self, offer):
        calls.append(offer)
        return {"session_id": "unexpected", "sdp": "v=0\r\n", "max_seconds": 180}
    monkeypatch.setattr(VoiceProvider, "create", forbidden)
    response = client.post("/api/voice/session", json={"sdp": sdp})
    assert response.status_code == 422
    assert calls == []


def test_valid_voice_offer_is_forwarded_without_client_model_override(client, monkeypatch):
    calls = []
    async def created(self, offer):
        calls.append(offer)
        return {"session_id": "mock-session", "sdp": "v=0\r\no=mock-answer", "max_seconds": 180}
    monkeypatch.setattr(VoiceProvider, "create", created)
    sdp = "v=0\r\no=mock-offer"
    assert client.post("/api/voice/session", json={"sdp": sdp, "model": "client-model"}).status_code == 422
    assert calls == []
    response = client.post("/api/voice/session", json={"sdp": sdp})
    assert response.status_code == 200 and calls == [sdp]


def test_validation_errors_do_not_echo_credentials_or_sdp(client):
    sentinel = "private-input-sentinel-"
    responses = [
        client.post("/api/auth/login", json={"token": sentinel * 40}),
        client.post("/api/offers/unknown/accept", json={"token": sentinel * 20, "idempotency_key": "invalid-accept-boundary"}),
        client.post("/api/voice/session", json={"sdp": sentinel * 5000}),
        client.post("/api/voice/session", json={"sdp": "v=0\r\no=mock-offer", sentinel: "private-value"}),
    ]
    for response in responses:
        assert response.status_code == 422
        assert sentinel not in response.text
        assert response.json()["applied"] is False
        assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


def test_nonfinite_invalid_input_produces_json_validation_error_instead_of_server_error(client):
    response = client.post("/api/passenger/unused-token/constraints", content='{"idempotency_key":"nonfinite-budget","budget":NaN}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["applied"] is False
    assert "NaN" not in response.text
