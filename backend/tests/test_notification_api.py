"""Notification API wiring checks. All provider traffic uses MockTransport."""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.main import create_app


OPERATOR_TOKEN = "notification-operator-test-token-" + "x" * 40
PROVIDER_KEY = "re_test-only-private-resend-key-do-not-echo"
BREVO_KEY = "test-only-private-brevo-key-do-not-echo"
EMAIL = "private.passenger@example.test"
PHONE = "+40700123456"
AUTH = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}


@pytest.fixture
def notification_api(tmp_path, request):
    configuration = dict(
        database_path=str(tmp_path / "notification-api.sqlite3"),
        operation_mode="simulation",
        operator_token=OPERATOR_TOKEN,
        public_base_url="https://air.example.com",
        brevo_api_key=BREVO_KEY,
        resend_api_key=PROVIDER_KEY,
        notification_email_provider="resend",
        notification_email_from="recovery@example.test",
        notification_email_from_name="AIR Recovery",
        notification_sms_sender="AIR",
        notifications_allow_simulation=True,
        request_timeout_seconds=1,
        _env_file=None,
    )
    configuration.update(getattr(request, "param", {}))
    app = create_app(Settings(**configuration))
    calls = []
    mode = {"kind": "accepted"}

    def handle(request):
        calls.append(request)
        assert request.method == "POST"
        is_email = request.url.host == "api.resend.com"
        if is_email:
            assert request.url.path == "/emails"
            assert request.headers["authorization"] == f"Bearer {PROVIDER_KEY}"
            assert "api-key" not in request.headers
            assert request.headers.get("idempotency-key")
            assert BREVO_KEY not in str(request.headers)
        else:
            assert request.url.host == "api.brevo.com"
            assert request.url.path == "/v3/transactionalSMS/send"
            assert request.headers["api-key"] == BREVO_KEY
            assert "authorization" not in request.headers
            assert PROVIDER_KEY not in str(request.headers)
        success_status = 200 if is_email else 201
        if mode["kind"] == "timeout":
            raise httpx.ReadTimeout(f"Sensitive upstream context: {EMAIL} {PROVIDER_KEY} {BREVO_KEY}", request=request)
        if mode["kind"] == "malformed_success":
            return httpx.Response(success_status, json={"unexpected": EMAIL, "debug": f"{PROVIDER_KEY} {BREVO_KEY}"})
        if mode["kind"] == "invalid_json":
            return httpx.Response(success_status, content=f"not JSON {EMAIL} {PROVIDER_KEY} {BREVO_KEY}".encode())
        if mode["kind"] == "http_error":
            return httpx.Response(mode["status"], json={"code": "invalid_parameter", "message": f"Recipient {EMAIL}; keys {PROVIDER_KEY} {BREVO_KEY}"})
        if is_email:
            return httpx.Response(200, json={"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"})
        return httpx.Response(201, json={"messageId": 1511882900176220})

    # Replace the constructor's unused client before app lifespan starts. This
    # also makes a future accidental startup send observable and entirely local.
    asyncio.run(app.state.notifications.client.aclose())
    mocked = httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        follow_redirects=False,
        timeout=1,
    )
    app.state.notifications.client = mocked
    try:
        with TestClient(app) as client:
            yield client, calls, mode
    finally:
        if not mocked.is_closed:
            asyncio.run(mocked.aclose())


def planned_offer(client):
    response = client.get("/api/world", headers=AUTH)
    assert response.status_code == 200, response.text
    world = response.json()
    response = client.post("/api/replan", headers=AUTH, json={
        "expected_version": world["version"],
        "idempotency_key": "notification-api-plan-0001",
        "mode": "solver",
    })
    assert response.status_code == 200, response.text
    offers = response.json()["world"]["offers"]
    assert offers
    return next((offer for offer in offers if offer["group_id"] == "G001"), offers[0])


def payload(offer, channel="email", key="notification-api-send-0001"):
    return {
        "channel": channel,
        "recipient": EMAIL if channel == "email" else PHONE,
        "offer_id": offer["id"],
        "idempotency_key": key,
    }


def endpoint(offer):
    return f"/api/groups/{offer['group_id']}/notifications"


def test_notification_config_redacts_secrets_and_startup_does_not_send(notification_api):
    client, calls, _ = notification_api
    response = client.get("/api/config", headers=AUTH)
    assert response.status_code == 200
    config = response.json()
    assert config["notification_email_provider"] == "resend"
    assert config["notification_email_configured"] is True
    assert config["notification_sms_configured"] is True
    assert config["notification_available"] is True
    assert not config["notification_unavailable_reason"]
    assert PROVIDER_KEY not in response.text
    assert BREVO_KEY not in response.text
    assert "brevo_api_key" not in response.text
    assert "resend_api_key" not in response.text
    assert OPERATOR_TOKEN not in response.text
    assert client.get("/api/world", headers=AUTH).status_code == 200
    response = client.get("/api/groups/G001/notifications", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"notifications": []}
    assert calls == []


def test_notification_get_and_post_require_operator_and_accept_cookie_auth(notification_api):
    client, calls, _ = notification_api
    offer = planned_offer(client)
    route = endpoint(offer)
    assert client.get(route).status_code == 401
    assert client.post(route, json=payload(offer)).status_code == 401
    assert calls == []
    assert client.post("/api/auth/login", json={"token": OPERATOR_TOKEN}).status_code == 200
    assert client.get(route).status_code == 200
    response = client.post(route, json=payload(offer, key="notification-cookie-auth-0001"))
    assert response.status_code in (200, 201), response.text
    assert response.json()["status"] == "accepted"
    assert len(calls) == 1
    assert client.post("/api/auth/logout").status_code in (200, 204)
    assert client.get(route).status_code == 401
    assert client.post(route, json=payload(offer, key="notification-after-logout-0001")).status_code == 401
    assert len(calls) == 1


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_notification_routes_resend_email_and_brevo_sms_with_safe_replay(notification_api, channel):
    client, calls, _ = notification_api
    offer = planned_offer(client)
    body = payload(offer, channel)
    route = endpoint(offer)
    response = client.post(route, headers=AUTH, json=body)
    assert response.status_code in (200, 201), response.text
    delivery = response.json()
    assert delivery["group_id"] == offer["group_id"]
    assert delivery["offer_id"] == offer["id"]
    assert delivery["channel"] == channel
    assert delivery["provider"] == ("resend" if channel == "email" else "brevo")
    assert delivery["status"] == "accepted"
    assert delivery["provider_message_id"]
    assert delivery["recipient_masked"] != body["recipient"]
    assert body["recipient"] not in response.text
    assert PROVIDER_KEY not in response.text
    assert BREVO_KEY not in response.text
    assert len(calls) == 1
    outbound = json.loads(calls[0].content)
    if channel == "email":
        assert calls[0].url.host == "api.resend.com"
        assert calls[0].url.path == "/emails"
        assert outbound["from"] == "AIR Recovery <recovery@example.test>"
        assert outbound["to"] == [EMAIL]
        assert outbound["subject"]
        content = outbound.get("text", outbound.get("html", ""))
        assert content
    else:
        assert calls[0].url.path == "/v3/transactionalSMS/send"
        assert outbound["sender"] == "AIR"
        assert outbound["recipient"].lstrip("+") == PHONE.lstrip("+")
        assert outbound["type"] == "transactional"
        content = outbound["content"]
    assert "https://air.example.com/boarding#" in content
    repeated = client.post(route, headers=AUTH, json=body)
    assert repeated.status_code in (200, 201), repeated.text
    assert repeated.json()["id"] == delivery["id"]
    assert len(calls) == 1
    history = client.get(route, headers=AUTH)
    assert history.status_code == 200
    records = history.json()["notifications"]
    assert len(records) == 1 and records[0]["id"] == delivery["id"]
    assert body["recipient"] not in history.text
    assert PROVIDER_KEY not in history.text
    assert BREVO_KEY not in history.text


def test_notification_same_key_changed_recipient_conflicts_without_second_send(notification_api):
    client, calls, _ = notification_api
    offer = planned_offer(client)
    body = payload(offer)
    assert client.post(endpoint(offer), headers=AUTH, json=body).status_code in (200, 201)
    changed = {**body, "recipient": "different.passenger@example.test"}
    response = client.post(endpoint(offer), headers=AUTH, json=changed)
    assert response.status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("kind,status,expected", [
    ("http_error", 400, "failed"),
    ("http_error", 500, "unknown"),
    ("timeout", None, "unknown"),
    ("malformed_success", None, "unknown"),
    ("invalid_json", None, "unknown"),
])
@pytest.mark.parametrize("channel", ["email", "sms"])
def test_notification_failures_are_redacted_and_same_key_never_reissues(notification_api, kind, status, expected, channel):
    client, calls, mode = notification_api
    offer = planned_offer(client)
    mode.update(kind=kind, status=status)
    body = payload(offer, channel)
    response = client.post(endpoint(offer), headers=AUTH, json=body)
    assert response.status_code in (200, 201), response.text
    record = response.json()
    assert record["status"] == expected
    assert record["provider_message_id"] is None
    assert record["error_code"]
    assert EMAIL not in response.text
    assert PROVIDER_KEY not in response.text
    assert BREVO_KEY not in response.text
    assert len(record.get("error_message") or "") <= 1000
    repeated = client.post(endpoint(offer), headers=AUTH, json=body)
    assert repeated.status_code in (200, 201), repeated.text
    assert repeated.json()["id"] == record["id"]
    assert repeated.json()["status"] == expected
    assert len(calls) == 1


def test_notification_validation_and_origin_fail_before_provider_without_echo(notification_api):
    client, calls, _ = notification_api
    offer = planned_offer(client)
    secret_recipient = "private-recipient-" + "z" * 1800 + "@example.test"
    response = client.post(endpoint(offer), headers=AUTH, json={**payload(offer), "recipient": secret_recipient})
    assert response.status_code == 422
    assert secret_recipient not in response.text
    assert "private-recipient-" not in response.text
    assert PROVIDER_KEY not in response.text
    assert BREVO_KEY not in response.text
    assert len(response.content) < 2000
    response = client.post(endpoint(offer), headers={**AUTH, "Origin": "https://untrusted.example"}, json=payload(offer))
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("notification_api,missing_channel,working_channel", [
    ({"resend_api_key": ""}, "email", "sms"),
    ({"brevo_api_key": ""}, "sms", "email"),
], indirect=["notification_api"])
def test_notification_selected_providers_do_not_fall_back_or_share_credentials(notification_api, missing_channel, working_channel):
    client, calls, _ = notification_api
    config = client.get("/api/config", headers=AUTH).json()
    assert config["notification_email_provider"] == "resend"
    assert config[f"notification_{missing_channel}_configured"] is False
    assert config[f"notification_{working_channel}_configured"] is True
    offer = planned_offer(client)
    blocked = client.post(endpoint(offer), headers=AUTH, json=payload(offer, missing_channel, "notification-missing-channel-0001"))
    assert blocked.status_code == 503, blocked.text
    assert calls == []
    working = client.post(endpoint(offer), headers=AUTH, json=payload(offer, working_channel, "notification-working-channel-0001"))
    assert working.status_code in (200, 201), working.text
    assert working.json()["status"] == "accepted"
    assert working.json()["provider"] == ("resend" if working_channel == "email" else "brevo")
    assert len(calls) == 1
