import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.passwords import hash_password, verify_password


PASSWORD = "test-only-password-7Jv!"


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def settings(tmp_path, password_hash):
    return Settings(database_path=str(tmp_path / "auth.sqlite3"), operator_username="operator",
                    operator_password_hash=password_hash, _env_file=None)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def login(client, **overrides):
    return client.post("/api/auth/login", json={"username": "operator", "password": PASSWORD, **overrides})


def test_salted_password_hashes_and_unicode():
    password = "parolă-🛫-lungă-123"
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password(password + "x", first)
    assert not verify_password(password, "invalid")
    with pytest.raises(ValueError):
        hash_password("short")


@pytest.mark.parametrize("path", ["/api/world", "/api/audit", "/api/stream", "/api/recovery-jobs/private"])
def test_operator_reads_require_authentication(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer "}).status_code == 401


def test_operator_mutations_require_authentication(client):
    response = client.post("/api/replan", json={"expected_version": 1, "idempotency_key": "unauthorized-plan", "mode": "solver"})
    assert response.status_code == 401


def test_login_cookie_reload_and_logout_revocation(client, settings):
    config = client.get("/api/config").json()
    assert config["auth_required"] and not config["authenticated"]
    assert config["login_method"] == "password"
    assert settings.operator_password_hash.get_secret_value() not in str(config)
    response = login(client)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "Path=/api" in response.headers["set-cookie"]
    cookie = client.cookies.get("recovery_session")
    assert PASSWORD not in cookie and "operator" not in cookie
    assert client.get("/api/config").json()["authenticated"]
    token = client.get("/api/world").json()["groups"][0]["token"]
    # Sessions survive workers/restarts with the same configuration and DB.
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/world", headers={"Cookie": f"recovery_session={cookie}"}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/world").status_code == 401
    assert client.get("/api/world", headers={"Cookie": f"recovery_session={cookie}"}).status_code == 401
    assert client.get("/api/passenger/" + token).status_code == 200


@pytest.mark.parametrize("credentials", [
    {"username": "unknown", "password": PASSWORD},
    {"username": "operator", "password": "incorrect"},
    {"username": "OPERATOR", "password": PASSWORD},
    {"token": "legacy-token"},
])
def test_invalid_credentials_have_same_error(client, credentials):
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
    assert "set-cookie" not in response.headers
    assert client.get("/api/world").status_code == 401


def test_validation_does_not_reflect_password(client):
    secret = "do-not-reflect-this-value" * 80
    response = login(client, password=secret)
    assert response.status_code == 422
    assert secret not in response.text
    assert client.post("/api/auth/login", json={"username": "operator"}).status_code == 422
    assert client.post("/api/auth/login", json={"username": "operator", "password": PASSWORD, "token": "other"}).status_code == 422


def test_disallowed_origin_cannot_login_or_logout(client):
    headers = {"Origin": "https://evil.example"}
    assert client.post("/api/auth/login", json={"username": "operator", "password": PASSWORD}, headers=headers).status_code == 403
    assert login(client).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    assert client.get("/api/world").status_code == 200


def test_session_expiration_and_tampering(client, monkeypatch):
    assert login(client).status_code == 200
    cookie = client.cookies.get("recovery_session")
    assert client.get("/api/world", headers={"Cookie": f"recovery_session={cookie}tampered"}).status_code == 401
    issued = int(cookie.split(".")[0])
    monkeypatch.setattr("app.main.time", SimpleNamespace(time=lambda: issued + 86401))
    assert client.get("/api/world").status_code == 401


def test_password_rotation_invalidates_existing_sessions(client, settings):
    assert login(client).status_code == 200
    cookie = client.cookies.get("recovery_session")
    updated = settings.model_copy(update={"operator_username": "another-operator"})
    with TestClient(create_app(updated)) as rotated:
        assert rotated.get("/api/world", headers={"Cookie": f"recovery_session={cookie}"}).status_code == 401


def test_rate_limit_is_shared_across_workers_and_expires(client, settings, monkeypatch):
    import time
    now = time.time()
    for _ in range(10):
        assert login(client, password="incorrect").status_code == 401
    assert login(client).status_code == 429
    with TestClient(create_app(settings)) as worker:
        assert login(worker).status_code == 429
        monkeypatch.setattr("app.main.time", SimpleNamespace(time=lambda: now + 61))
        assert login(worker).status_code == 200


def test_production_uses_secure_cookie(settings):
    production = Settings(**{**settings.model_dump(), "environment": "production",
                            "public_base_url": "https://recovery.example.com",
                            "cors_origins": "https://recovery.example.com",
                            "allowed_hosts": "recovery.example.com"}, _env_file=None)
    with TestClient(create_app(production), base_url="https://recovery.example.com") as client:
        assert "Secure" in login(client).headers["set-cookie"]
        assert client.get("/api/world").status_code == 200
        assert "Secure" in client.post("/api/auth/logout").headers["set-cookie"]


@pytest.mark.parametrize("values", [
    {"operator_username": "operator"},
    {"operator_password_hash": "invalid"},
    {"operator_username": "operator", "operator_password_hash": "plaintext-password"},
])
def test_incomplete_or_invalid_config_fails_closed(values):
    with pytest.raises(ValueError) as exc:
        Settings(**values, _env_file=None)
    assert "plaintext-password" not in str(exc.value)


def test_credential_setup_preserves_existing_config_and_hashes_password(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts" / "configure_operator.py"
    spec = importlib.util.spec_from_file_location("configure_operator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env_file = tmp_path / ".env"
    env_file.write_text("# preserve this\nOPENAI_API_KEY=test-provider-secret\nOPERATOR_USERNAME=old\nOPERATOR_PASSWORD_HASH=old\n")
    module.configure(env_file, "operator", PASSWORD)
    contents = env_file.read_text()
    assert "# preserve this\nOPENAI_API_KEY=test-provider-secret" in contents
    assert contents.count("OPERATOR_USERNAME=") == 1
    assert PASSWORD not in contents
    assert env_file.stat().st_mode & 0o777 == 0o600
    monkeypatch.delenv("OPERATOR_USERNAME")
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH")
    configured = Settings(_env_file=env_file)
    assert configured.operator_username == "operator"
    assert verify_password(PASSWORD, configured.operator_password_hash.get_secret_value())
