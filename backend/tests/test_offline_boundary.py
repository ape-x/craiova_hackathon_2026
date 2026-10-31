import os
import socket
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import app


def test_collection_default_app_uses_temporary_database_and_no_live_credentials(tmp_path, monkeypatch):
    assert app.state.settings.environment == "test"
    assert not app.state.settings.openai_api_key
    assert not app.state.settings.fr24_api_key
    assert not app.state.settings.google_maps_api_key
    assert app.state.store.path == os.environ["DATABASE_PATH"]
    assert Path(app.state.store.path).parent.name.startswith("airport-recovery-tests-")
    # Empty environment overrides must win over a developer's configured .env.
    (tmp_path / ".env").write_text("OPENAI_API_KEY=fake-dotenv-secret\nFR24_API_KEY=fake-fr24-secret\nGOOGLE_MAPS_API_KEY=fake-maps-secret\n")
    monkeypatch.chdir(tmp_path)
    configured = Settings()
    assert not configured.openai_api_key and not configured.fr24_api_key
    assert not configured.google_maps_api_key


def test_real_network_is_blocked_even_with_explicit_dummy_credentials():
    with pytest.raises(AssertionError, match="Live network access is disabled"):
        socket.getaddrinfo("api.openai.com", 443)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        with pytest.raises(AssertionError, match="Live network access is disabled"):
            connection.connect(("127.0.0.1", 443))
    with pytest.raises(AssertionError, match="Live network access is disabled"):
        httpx.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer dummy-only"})


def test_mock_http_remains_available_offline():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"mock": True}))) as client:
        assert client.get("https://api.openai.com/v1/models").json() == {"mock": True}
