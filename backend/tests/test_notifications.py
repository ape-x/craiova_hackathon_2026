"""Offline notification delivery guarantees; no provider contact or real messages."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.models import Itinerary, Leg, Offer
from app.notifications import NotificationRequest, NotificationService
from app.seed import seed_world
from app.store import DomainError, Store, utcnow


def fixture_service(tmp_path, handler=None, **overrides):
    clock = [utcnow()]
    settings = SimpleNamespace(brevo_api_key=SecretStr("fixture-brevo-secret"),
        resend_api_key=SecretStr("fixture-resend-secret"), notification_email_provider="resend",
        notification_email_from="operations@airport-demo.com", notification_email_from_name="AIR",
        notification_sms_sender="AIR", notifications_allow_simulation=True,
        public_base_url="https://recovery.airport-demo.com", request_timeout_seconds=1,
        **{key: value for key, value in overrides.items() if key not in {
            "brevo_api_key", "resend_api_key", "notification_email_provider", "notification_email_from", "notification_email_from_name", "notification_sms_sender",
            "notifications_allow_simulation", "public_base_url", "request_timeout_seconds"}})
    for key, value in overrides.items():
        setattr(settings, key, value)
    store = Store(str(tmp_path / "notifications.sqlite3"), operation_mode="simulation")
    world = store.read()
    def add_offer(current):
        resource = next(item for item in current.resources if item.id == "F110")
        current.offers = [Offer(id="offer-notification", group_id="G001", status="held",
            expires_at=(clock[0] + timedelta(hours=1)).isoformat(), created_version=current.version + 1,
            itinerary=Itinerary(legs=[Leg(resource_id=resource.id, kind=resource.kind,
                origin=resource.origin, destination=resource.destination, departure=resource.departure,
                arrival=resource.arrival, cost=720)], arrival=resource.arrival, total_cost=720))]
        current.groups[0].status = "offered"
    store.mutate("fixture-offer", {}, world.version, add_offer)
    sent = []
    async def transport(request):
        sent.append(request)
        if handler:
            result = handler(request)
            return await result if hasattr(result, "__await__") else result
        if request.url.host == "api.resend.com":
            return httpx.Response(200, json={"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"})
        return httpx.Response(201, json={"messageId": 12345 if request.url.path.endswith("/send") else "<fixture-message@relay.brevo.com>"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    service = NotificationService(settings, store, client=client, now=lambda: clock[0])
    return service, store, sent, clock


def request(channel="email", recipient=None, key="notification-fixture-1", offer_id="offer-notification"):
    return NotificationRequest(channel=channel, recipient=recipient or ("alice@example.com" if channel == "email" else "+40712345678"),
                               offer_id=offer_id, idempotency_key=key)


def mutate(store, update, key="fixture-change"):
    world = store.read()
    return store.mutate(key, {}, world.version, update)


@pytest.mark.parametrize("channel", ["email", "sms"])
async def test_provider_contract_and_only_masked_contact_persisted(tmp_path, channel):
    service, store, sent, _ = fixture_service(tmp_path)
    token = store.read().groups[0].token
    body = request(channel)
    before = store.read().model_dump_json()
    result = await service.send("G001", body)
    assert result["status"] == "accepted" and result["provider"] == ("resend" if channel == "email" else "brevo")
    assert result["provider_message_id"] and "delivered" not in json.dumps(result)
    assert len(sent) == 1
    payload = json.loads(sent[0].content)
    url = "https://recovery.airport-demo.com/boarding#" + token
    if channel == "email":
        assert sent[0].url == "https://api.resend.com/emails"
        assert sent[0].headers["authorization"] == "Bearer fixture-resend-secret"
        assert sent[0].headers["idempotency-key"] == result["id"]
        assert "api-key" not in sent[0].headers
        assert payload["from"] == "AIR <operations@airport-demo.com>"
        assert payload["to"] == ["alice@example.com"]
        assert url in payload["text"] and url in payload["html"]
        assert payload["subject"] == "Simulation: Your AIR recovery journey"
    else:
        assert sent[0].url == "https://api.brevo.com/v3/transactionalSMS/send"
        assert sent[0].headers["api-key"] == "fixture-brevo-secret"
        assert "authorization" not in sent[0].headers and "idempotency-key" not in sent[0].headers
        assert payload["recipient"] == "+40712345678" and payload["sender"] == "AIR"
        assert payload["type"] == "transactional" and payload["unicodeEnabled"] is False
        assert url in payload["content"] and "SIMULATION ONLY" in payload["content"]
    assert service.history("G001")["notifications"] == [result]
    with store.connect() as conn:
        raw = json.dumps(conn.execute("SELECT * FROM passenger_notifications").fetchall())
    for sensitive in (body.recipient, token, "fixture-brevo-secret", "fixture-resend-secret", body.idempotency_key, url):
        assert sensitive not in raw and sensitive not in json.dumps(result)
    assert store.read().model_dump_json() == before
    await service.aclose()


async def test_email_uses_fixed_subject_and_escapes_link_without_group_label(tmp_path):
    service, store, sent, _ = fixture_service(tmp_path)
    def malicious_labels(world):
        world.groups[0].name = '<img src=x onerror="sentinel">\r\nBcc: victim@example.com'
        world.groups[0].token = 'private/<script>?"&#token'
    mutate(store, malicious_labels)
    await service.send("G001", request())
    payload = json.loads(sent[0].content)
    assert "sentinel" not in json.dumps(payload) and "victim@example.com" not in json.dumps(payload)
    assert "private%2F%3Cscript%3E%3F%22%26%23token" in payload["html"]
    assert "<script>" not in payload["html"]
    await service.aclose()


async def test_duplicate_after_restart_replays_without_another_request(tmp_path):
    service, store, sent, clock = fixture_service(tmp_path)
    original = await service.send("G001", request())
    await service.aclose()
    def must_not_send(_):
        raise AssertionError("A replay must never call the provider")
    fresh = NotificationService(service.settings, Store(store.path, operation_mode="simulation"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(must_not_send)), now=lambda: clock[0])
    assert await fresh.send("G001", request()) == original
    assert len(sent) == 1
    with pytest.raises(DomainError) as error:
        await fresh.send("G001", request(recipient="different@example.com"))
    assert error.value.status == 409
    await fresh.aclose()


async def test_two_services_concurrently_claim_one_send(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"id": "one-message"})
    service, store, sent, clock = fixture_service(tmp_path, delayed)
    second = NotificationService(service.settings, Store(store.path), client=httpx.AsyncClient(transport=httpx.MockTransport(delayed)), now=lambda: clock[0])
    task = asyncio.create_task(service.send("G001", request()))
    await entered.wait()
    duplicate = await second.send("G001", request())
    assert duplicate["status"] == "sending"
    release.set()
    completed = await task
    assert completed["status"] == "accepted" and completed["id"] == duplicate["id"]
    assert len(sent) == 1
    assert await second.send("G001", request()) == completed
    await service.aclose()
    await second.aclose()


async def test_orphan_send_becomes_unknown_after_deadline_and_never_retried(tmp_path):
    service, store, sent, clock = fixture_service(tmp_path)
    claimed, _ = service._claim("G001", request())
    assert claimed["status"] == "sending"
    await service.aclose()
    clock[0] += timedelta(seconds=32)
    fresh = NotificationService(service.settings, Store(store.path),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(AssertionError("No retry")))), now=lambda: clock[0])
    history = fresh.history("G001")["notifications"]
    assert history[0]["status"] == "unknown" and history[0]["error_code"] == "interrupted"
    assert await fresh.send("G001", request()) == history[0]
    assert sent == []
    await fresh.aclose()


@pytest.mark.parametrize("channel,recipient", [
    ("email", "Name <alice@example.com>"), ("email", "alice@example.com\r\nBcc:x@example.com"),
    ("email", "a..b@example.com"), ("email", "a@-example.com"), ("email", "a@localhost"),
    ("sms", "0712345678"), ("sms", "+40 712345678"), ("sms", "+0123456"),
    ("sms", "+40712345678901234"), ("sms", "+40712345678\n"),
])
def test_invalid_recipient_is_rejected_without_reflecting_input(channel, recipient):
    with pytest.raises(ValidationError) as error:
        request(channel, recipient)
    assert recipient not in str(error.value)


@pytest.mark.parametrize("change", ["expired", "invalidated", "wrong_offer", "ambiguous"])
async def test_stale_or_noncurrent_offer_is_rejected_without_send(tmp_path, change):
    service, store, sent, clock = fixture_service(tmp_path)
    def update(world):
        if change == "expired":
            world.offers[0].expires_at = (clock[0] - timedelta(seconds=1)).isoformat()
        elif change == "invalidated":
            world.offers[0].status = "invalidated"
        elif change == "ambiguous":
            other = world.offers[0].model_copy(deep=True)
            other.id = "second-active-offer"
            world.offers.append(other)
    mutate(store, update)
    with pytest.raises(DomainError) as error:
        await service.send("G001", request(offer_id="old-offer" if change == "wrong_offer" else "offer-notification"))
    assert error.value.status == 409 and sent == []
    assert service.history("G001") == {"notifications": []}
    await service.aclose()


async def test_confirmed_itinerary_does_not_expire_with_old_hold_deadline(tmp_path):
    service, store, sent, clock = fixture_service(tmp_path)
    def confirmed(world):
        world.offers[0].status = "confirmed"
        world.offers[0].expires_at = (clock[0] - timedelta(days=1)).isoformat()
        world.groups[0].status = "confirmed"
    mutate(store, confirmed)
    assert (await service.send("G001", request()))["status"] == "accepted"
    assert len(sent) == 1
    await service.aclose()


@pytest.mark.parametrize("changes", [
    {"resend_api_key": SecretStr("")}, {"notification_email_from": ""},
    {"notification_email_from_name": "AIR\r\nBcc: x@example.com"},
    {"notifications_allow_simulation": False},
])
async def test_missing_configuration_or_simulation_gate_is_explicit(tmp_path, changes):
    service, _, sent, _ = fixture_service(tmp_path, **changes)
    with pytest.raises(DomainError) as error:
        await service.send("G001", request())
    assert error.value.status == 503 and sent == []
    await service.aclose()


@pytest.mark.parametrize("url", ["http://recovery.airport-demo.com", "https://localhost", "https://127.0.0.1", "https://[::1]", "https://10.0.0.1", "https://service.local", "https://service.internal", "https://service.test", "https://user:secret@recovery.airport-demo.com", "https://recovery.airport-demo.com#token", "https://recovery.airport-demo.com?secret=1", "https://recovery.airport-demo.com\n", "https://recovery.airport-demo.com:bad"])
async def test_nonpublic_or_ambiguous_passenger_url_never_sends(tmp_path, url):
    service, _, sent, _ = fixture_service(tmp_path, public_base_url=url)
    assert not service.available
    with pytest.raises(DomainError) as error:
        await service.send("G001", request())
    assert error.value.status == 503 and sent == []
    assert url not in error.value.message
    await service.aclose()


@pytest.mark.parametrize("status,expected", [(400,"failed"),(401,"failed"),(403,"failed"),(429,"failed"),(500,"unknown"),(503,"unknown"),(302,"unknown")])
async def test_provider_errors_are_redacted_and_never_retried(tmp_path, status, expected):
    sentinel = "alice@example.com /boarding#private-token fixture-brevo-secret fixture-resend-secret"
    service, store, sent, _ = fixture_service(tmp_path, lambda _: httpx.Response(status, json={"message": sentinel}, headers={"Location": "https://evil.invalid"}))
    result = await service.send("G001", request())
    assert result["status"] == expected and result["provider_message_id"] is None
    assert sentinel not in json.dumps(result)
    assert await service.send("G001", request()) == result and len(sent) == 1
    with store.connect() as conn:
        assert sentinel not in json.dumps(conn.execute("SELECT * FROM passenger_notifications").fetchall())
    await service.aclose()


@pytest.mark.parametrize("payload", [{}, {"id": ""}, {"id": True}, {"id": "alice@example.com"}, {"id": "bad\nmessage"}, {"id": {"raw": "private"}}, [], {"messageId": "wrong-provider-shape"}])
async def test_malformed_success_is_unknown_not_false_acceptance(tmp_path, payload):
    service, _, sent, _ = fixture_service(tmp_path, lambda _: httpx.Response(200, json=payload))
    result = await service.send("G001", request())
    assert result["status"] == "unknown" and result["error_code"] == "invalid_provider_response"
    assert result["provider_message_id"] is None and len(sent) == 1
    await service.aclose()


async def test_timeout_is_unknown_and_does_not_expose_transport_exception(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("alice@example.com /boarding#secret APIKEY", request=request)
    service, _, sent, _ = fixture_service(tmp_path, timeout)
    result = await service.send("G001", request())
    assert result["status"] == "unknown" and result["error_code"] == "transport_unknown"
    assert "alice@example.com" not in json.dumps(result) and "APIKEY" not in json.dumps(result)
    assert await service.send("G001", request()) == result and len(sent) == 1
    await service.aclose()


async def test_cancelled_outbound_request_is_permanently_unknown(tmp_path):
    entered = asyncio.Event()
    async def hanging(_):
        entered.set()
        await asyncio.Event().wait()
    service, _, sent, _ = fixture_service(tmp_path, hanging)
    task = asyncio.create_task(service.send("G001", request()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = service.history("G001")["notifications"][0]
    assert result["status"] == "unknown" and result["error_code"] == "interrupted"
    assert await service.send("G001", request()) == result and len(sent) == 1
    await service.aclose()


async def test_reset_does_not_mix_reused_group_ids_or_replay_old_sends(tmp_path):
    service, store, sent, _ = fixture_service(tmp_path)
    original = await service.send("G001", request())
    def reset(world):
        world.scenario_id = seed_world().scenario_id
        world.groups[0].token = "new-private-token"
    mutate(store, reset)
    assert service.history("G001") == {"notifications": []}
    with pytest.raises(DomainError) as error:
        await service.send("G001", request())
    assert error.value.status == 409 and len(sent) == 1
    newer = await service.send("G001", request(key="new-world-notification"))
    assert newer["id"] != original["id"] and len(sent) == 2
    assert service.history("G001")["notifications"] == [newer]
    await service.aclose()


async def test_status_contains_configuration_booleans_not_credentials(tmp_path):
    service, _, sent, _ = fixture_service(tmp_path)
    assert service.status() == {"provider": "resend", "email_provider": "resend", "sms_provider": "brevo", "email_configured": True, "sms_configured": True, "available": True, "unavailable_reason": None}
    assert "fixture-brevo-secret" not in json.dumps(service.status()) and sent == []
    await service.aclose()


async def test_explicit_brevo_email_compatibility_uses_only_brevo_credentials(tmp_path):
    service, _, sent, _ = fixture_service(tmp_path, notification_email_provider="brevo")
    result = await service.send("G001", request())
    assert result["provider"] == "brevo" and result["status"] == "accepted"
    assert sent[0].url == "https://api.brevo.com/v3/smtp/email"
    assert sent[0].headers["api-key"] == "fixture-brevo-secret"
    assert "authorization" not in sent[0].headers and "idempotency-key" not in sent[0].headers
    payload = json.loads(sent[0].content)
    assert payload["sender"] == {"name": "AIR", "email": "operations@airport-demo.com"}
    assert payload["to"] == [{"email": "alice@example.com"}]
    assert payload["textContent"] and payload["htmlContent"]
    assert "from" not in payload and "fixture-resend-secret" not in str(sent[0].headers)
    await service.aclose()


@pytest.mark.parametrize("provider,channel,missing_key", [
    ("resend", "email", "resend_api_key"),
    ("brevo", "email", "brevo_api_key"),
    ("resend", "sms", "brevo_api_key"),
])
async def test_missing_selected_provider_key_never_falls_back(tmp_path, provider, channel, missing_key):
    service, _, sent, _ = fixture_service(tmp_path, notification_email_provider=provider,
                                         **{missing_key: SecretStr("")})
    with pytest.raises(DomainError) as error:
        await service.send("G001", request(channel))
    assert error.value.status == 503 and sent == []
    if channel == "email":
        assert not service.email_configured
    else:
        assert service.email_configured and not service.sms_configured
    await service.aclose()


async def test_resend_email_does_not_require_brevo_key_and_sms_does_not_require_resend(tmp_path):
    service, _, sent, _ = fixture_service(tmp_path, brevo_api_key=SecretStr(""))
    assert service.email_configured and not service.sms_configured
    assert (await service.send("G001", request()))["provider"] == "resend"
    service.settings.resend_api_key = SecretStr("")
    service.settings.brevo_api_key = SecretStr("fixture-brevo-secret")
    assert not service.email_configured and service.sms_configured
    sms = await service.send("G001", request("sms", key="sms-independent-key"))
    assert sms["status"] == "accepted" and sms["provider"] == "brevo"
    assert len(sent) == 2
    await service.aclose()


async def test_provider_change_preserves_old_receipts_without_resending(tmp_path):
    service, _, sent, _ = fixture_service(tmp_path, notification_email_provider="brevo")
    previous = await service.send("G001", request())
    service.settings.notification_email_provider = "resend"
    assert await service.send("G001", request()) == previous
    assert previous["provider"] == "brevo" and len(sent) == 1
    latest = await service.send("G001", request(key="different-notification-key"))
    assert latest["provider"] == "resend" and latest["status"] == "accepted"
    assert len(sent) == 2
    assert {item["provider"] for item in service.history("G001")["notifications"]} == {"brevo", "resend"}
    await service.aclose()


async def test_resend_sender_display_name_is_safely_quoted(tmp_path):
    service, _, sent, _ = fixture_service(tmp_path, notification_email_from_name='AIR, operations <desk>')
    assert service.email_configured
    await service.send("G001", request())
    assert json.loads(sent[0].content)["from"] == '"AIR, operations <desk>" <operations@airport-demo.com>'
    await service.aclose()


@pytest.mark.parametrize("provider,status,body", [
    ("resend", 201, {"id": "fixture-message"}),
    ("brevo", 200, {"messageId": "fixture-message"}),
])
async def test_unexpected_success_status_is_unknown(tmp_path, provider, status, body):
    service, _, sent, _ = fixture_service(tmp_path, lambda _: httpx.Response(status, json=body),
                                         notification_email_provider=provider)
    result = await service.send("G001", request())
    assert result["status"] == "unknown" and result["provider_message_id"] is None
    assert await service.send("G001", request()) == result and len(sent) == 1
    await service.aclose()
