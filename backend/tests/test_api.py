import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.passwords import hash_password


@pytest.fixture
def client(tmp_path):
    app=create_app(Settings(database_path=str(tmp_path/"api.sqlite3"),_env_file=None))
    with TestClient(app) as client:
        yield client


def test_api_full_recovery_passenger_flow_and_scope(client):
    world=client.get("/api/world").json()
    result=client.post("/api/replan",json={"expected_version":world["version"],"idempotency_key":"api-plan-123","mode":"solver"})
    assert result.status_code==200,result.text
    data=result.json(); world=data["world"]
    offer=world["offers"][0]
    group=next(g for g in world["groups"] if g["id"]==offer["group_id"])
    passenger=client.get("/api/passenger/"+group["token"]).json()
    assert passenger["offer"]["id"]==offer["id"]
    assert "groups" not in passenger and "token" not in passenger["group"]
    accepted=client.post(f"/api/offers/{offer['id']}/accept",json={"token":group["token"],"idempotency_key":"api-accept-123"})
    assert accepted.status_code==200
    assert "world" not in accepted.json()
    assert accepted.json()["offer"]["status"]=="confirmed"


def test_missing_ai_is_explicit_and_does_not_apply_command(client):
    initial=client.get("/api/world").json()
    response=client.post("/api/commands",json={"text":"Break bus B12","expected_version":initial["version"],"idempotency_key":"no-ai-command"})
    assert response.status_code==503
    assert response.json()["applied"] is False
    assert client.get("/api/world").json()["version"]==initial["version"]
    config=client.get("/api/config").json()
    assert not config["ai_configured"] and not config["traffic_running"]
    assert "openai_api_key" not in config


def test_auth_cookie_origin_and_token_scoped_passenger(tmp_path):
    app=create_app(Settings(database_path=str(tmp_path/"secure.sqlite3"),operator_token="x"*40,_env_file=None))
    with TestClient(app) as client:
        assert client.get("/api/world").status_code==401
        assert client.post("/api/auth/login",json={"token":"wrong"}).status_code==401
        login=client.post("/api/auth/login",json={"token":"x"*40})
        assert login.status_code==200
        assert "HttpOnly" in login.headers["set-cookie"]
        world=client.get("/api/world").json()
        assert client.post("/api/events",headers={"Origin":"https://evil.example"},json={"expected_version":world["version"],"idempotency_key":"bad-origin-1","type":"resource_unavailable","target_id":"B12"}).status_code==403
        token=world["groups"][0]["token"]
        client.post("/api/auth/logout")
        assert client.get("/api/world").status_code==401
        assert client.get("/api/passenger/"+token).status_code==200


def test_production_cannot_start_with_unsafe_configuration(tmp_path):
    with pytest.raises(ValueError):
        Settings(environment="production",operator_token="short",_env_file=None)
    safe={"environment":"production","operator_username":"operator","operator_password_hash":hash_password("test-only-password"),"operator_token":"x"*40,"public_base_url":"https://recovery.example.com","cors_origins":"https://recovery.example.com","allowed_hosts":"recovery.example.com,127.0.0.1","_env_file":None}
    assert Settings(**safe).production
    for override in ({"public_base_url":"https://"},{"public_base_url":"https://user:secret@recovery.example.com"},{"cors_origins":"http://recovery.example.com"},{"allowed_hosts":"localhost"}):
        with pytest.raises(ValueError) as error:
            Settings(**{**safe,**override})
        assert safe["operator_token"] not in str(error.value)


def test_reset_revokes_old_passenger_links_and_versions_do_not_go_backwards(client):
    world=client.get("/api/world").json()
    token=world["groups"][0]["token"]
    reset=client.post("/api/reset",json={"expected_version":world["version"],"idempotency_key":"api-reset-123"})
    assert reset.status_code==200
    assert reset.json()["world"]["version"]==world["version"]+1
    assert client.get("/api/passenger/"+token).status_code==404


def test_invalid_numeric_event_rolls_back(client):
    world=client.get("/api/world").json()
    response=client.post("/api/events",json={"expected_version":world["version"],"idempotency_key":"invalid-capacity","type":"resource_capacity","target_id":"B12","value":-1})
    assert response.status_code==422
    assert client.get("/api/world").json()["version"]==world["version"]


def test_chunked_oversize_request_is_rejected(client):
    def chunks():
        for _ in range(11):
            yield b"x"*100000
    response=client.post("/api/auth/login",content=chunks(),headers={"Content-Type":"application/json"})
    assert response.status_code==413


def test_readiness_checks_database_without_requiring_provider_keys(client):
    response=client.get("/api/ready")
    assert response.status_code==200 and response.json()["database"]=="connected"
    with client.app.state.store.connect() as conn:
        conn.execute("UPDATE world SET data='invalid-json' WHERE id=1")
    unavailable=client.get("/api/ready")
    assert unavailable.status_code==503
    assert unavailable.json()=={"status":"unavailable","database":"unavailable"}
