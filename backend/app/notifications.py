"""Explicit operator-initiated passenger notifications, with no automatic retries.

Provider contracts: https://resend.com/docs/api-reference/emails/send-email,
https://resend.com/docs/dashboard/emails/idempotency-keys and
https://developers.brevo.com/reference/send-async-transactional-sms . A provider
acceptance is not delivery. This private ledger never stores contact details,
message bodies, passenger bearer links, or API keys in world/audit snapshots.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import re
import secrets
from datetime import datetime
from email.utils import formataddr
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends
from pydantic import ConfigDict, Field, model_validator

from .models import Model, World
from .store import DomainError, utcnow


_EMAIL = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")
_SMS = re.compile(r"^\+[1-9][0-9]{5,14}$")
_CONTROLS = re.compile(r"[\x00-\x20\x7f]")
_ACTIVE = {"held", "proposed", "accepted", "confirmed"}
_COLUMNS = "id,group_id,offer_id,channel,recipient_masked,status,provider,provider_message_id,created_at,updated_at,error_code,error_message"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS passenger_notifications (
    id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('email','sms')),
    recipient_hash TEXT NOT NULL,
    recipient_masked TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('sending','accepted','failed','unknown')),
    provider TEXT NOT NULL DEFAULT 'brevo',
    provider_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deadline REAL NOT NULL,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS passenger_notifications_group
ON passenger_notifications(scenario_id,group_id,created_at DESC);
"""


def _email(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or _CONTROLS.search(value) or not _EMAIL.fullmatch(value):
        raise ValueError("Enter a valid email address without a display name.")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in value:
        raise ValueError("Enter a valid email address.")
    if any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in domain.split(".")):
        raise ValueError("Enter a valid email address.")
    return local + "@" + domain.lower()


def _secret(value) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")


def _public_url(value: str) -> str | None:
    try:
        if not value or _CONTROLS.search(value) or "\\" in value:
            return None
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan", ".test", ".invalid", ".example", ".onion")):
                return None
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")):
                return None
        return value.rstrip("/")
    except (ValueError, TypeError):
        return None


def _masked(channel: str, recipient: str) -> str:
    if channel == "sms":
        return "+" + "*" * (len(recipient) - 5) + recipient[-4:]
    local, domain = recipient.rsplit("@", 1)
    return local[0] + "***@" + domain[0] + "***." + domain.rsplit(".", 1)[1]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class NotificationRequest(Model):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    channel: Literal["email", "sms"]
    recipient: str = Field(min_length=1, max_length=254)
    offer_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$")
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def valid_recipient(self):
        if self.channel == "email":
            object.__setattr__(self, "recipient", _email(self.recipient))
        elif not _SMS.fullmatch(self.recipient):
            raise ValueError("Enter an international phone number beginning with + and a country code.")
        return self


class NotificationService:
    def __init__(self, settings, store, *, client: httpx.AsyncClient | None = None, now=utcnow):
        self.settings, self.store, self.now = settings, store, now
        self.timeout = max(1.0, min(180.0, float(getattr(settings, "request_timeout_seconds", 45))))
        self.client = client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        with store.connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def email_provider(self) -> str:
        return getattr(self.settings, "notification_email_provider", "resend")

    @property
    def email_configured(self) -> bool:
        try:
            _email(getattr(self.settings, "notification_email_from", ""))
            name = getattr(self.settings, "notification_email_from_name", "AIR")
            return self.email_provider in {"resend", "brevo"} and bool(_secret(getattr(self.settings, self.email_provider + "_api_key", ""))) and isinstance(name, str) and 0 < len(name) <= 128 and not re.search(r"[\x00-\x1f\x7f]", name)
        except (TypeError, ValueError):
            return False

    @property
    def sms_configured(self) -> bool:
        sender = getattr(self.settings, "notification_sms_sender", "AIR")
        return bool(_secret(getattr(self.settings, "brevo_api_key", ""))) and isinstance(sender, str) and bool(re.fullmatch(r"(?:[A-Za-z0-9]{1,11}|[0-9]{12,15})", sender))

    def _availability_reason(self, mode: str) -> str | None:
        if getattr(self.settings, "operation_mode", "live") == "demo":
            return "Fictional demo passengers do not receive real notifications."
        if _public_url(getattr(self.settings, "public_base_url", "")) is None:
            return "Passenger notifications require a public HTTPS passenger URL."
        if mode == "simulation" and not getattr(self.settings, "notifications_allow_simulation", False):
            return "Sending real messages from a simulation is disabled."
        return None

    @property
    def unavailable_reason(self) -> str | None:
        return self._availability_reason(self.store.read().mode)

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    def status(self) -> dict:
        reason = self.unavailable_reason
        return {"provider": self.email_provider, "email_provider": self.email_provider, "sms_provider": "brevo", "email_configured": self.email_configured,
                "sms_configured": self.sms_configured, "available": reason is None,
                "unavailable_reason": reason}

    async def aclose(self):
        await self.client.aclose()

    @staticmethod
    def _world(conn) -> World:
        return World.model_validate_json(conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0])

    @staticmethod
    def _group(world, group_id):
        group = next((item for item in world.groups if item.id == group_id), None)
        if group is None:
            raise DomainError(404, "Passenger group not found")
        return group

    @staticmethod
    def _receipt(row) -> dict:
        return dict(zip(_COLUMNS.split(","), row))

    def _recover(self, conn, scenario_id: str):
        now = self.now()
        conn.execute("UPDATE passenger_notifications SET status='unknown',updated_at=?,error_code='interrupted',error_message=? WHERE scenario_id=? AND status='sending' AND deadline<=?",
                     (now.isoformat(), "The send outcome is unknown. Check the provider before sending another message.", scenario_id, now.timestamp()))

    def history(self, group_id: str) -> dict:
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world = self._world(conn)
            self._group(world, group_id)
            self._recover(conn, world.scenario_id)
            rows = conn.execute(f"SELECT {_COLUMNS} FROM passenger_notifications WHERE scenario_id=? AND group_id=? ORDER BY created_at DESC,id DESC LIMIT 100", (world.scenario_id, group_id)).fetchall()
            return {"notifications": [self._receipt(row) for row in rows]}

    def _claim(self, group_id: str, request: NotificationRequest):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world = self._world(conn)
            group = self._group(world, group_id)
            if world.demo:
                raise DomainError(409, "Fictional demo passengers do not receive real notifications.")
            key_hash = _digest(request.idempotency_key)
            payload_hash = _digest(json.dumps({"scenario_id": world.scenario_id, "group_id": group_id,
                "channel": request.channel, "recipient": request.recipient, "offer_id": request.offer_id}, sort_keys=True, separators=(",", ":")))
            self._recover(conn, world.scenario_id)
            previous = conn.execute(f"SELECT payload_hash,{_COLUMNS} FROM passenger_notifications WHERE key_hash=?", (key_hash,)).fetchone()
            if previous:
                if previous[0] != payload_hash:
                    raise DomainError(409, "This notification request key was already used for different details.")
                return self._receipt(previous[1:]), None
            reason = self._availability_reason(world.mode)
            if reason:
                raise DomainError(503, reason)
            if not (self.email_configured if request.channel == "email" else self.sms_configured):
                raise DomainError(503, f"{request.channel.title()} notifications are not configured.")
            active = [offer for offer in world.offers if offer.group_id == group_id and offer.status in _ACTIVE]
            if len(active) != 1 or active[0].id != request.offer_id:
                raise DomainError(409, "The passenger offer changed. Refresh the current itinerary before sending.")
            offer = active[0]
            now = self.now()
            if offer.status != "confirmed":
                try:
                    expires = datetime.fromisoformat(offer.expires_at.replace("Z", "+00:00"))
                    current = expires.tzinfo is not None and expires > now
                except (ValueError, TypeError):
                    current = False
                if not current:
                    raise DomainError(409, "The passenger offer expired. Refresh the itinerary before sending.")
            url = _public_url(self.settings.public_base_url) + "/boarding#" + quote(group.token, safe="")
            prefix = "SIMULATION ONLY. No real booking. " if world.mode == "simulation" else ""
            text = prefix + "AIR: Review your recovery journey: " + url + " This private link is for your group."
            provider = self.email_provider if request.channel == "email" else "brevo"
            if request.channel == "email":
                subject = ("Simulation: " if world.mode == "simulation" else "") + "Your AIR recovery journey"
                html_content = "<p>" + html.escape(prefix + "Review your AIR recovery journey.") + '</p><p><a href="' + html.escape(url, quote=True) + '">Open your passenger page</a></p><p>This private link is for your group. Availability can change.</p>'
                sender = _email(self.settings.notification_email_from)
                name = self.settings.notification_email_from_name
                if provider == "resend":
                    provider_payload = {"from": formataddr((name, sender)), "to": [request.recipient],
                                        "subject": subject, "text": text, "html": html_content}
                else:
                    provider_payload = {"sender": {"email": sender, "name": name},
                        "to": [{"email": request.recipient}], "subject": subject,
                        "textContent": text, "htmlContent": html_content}
            else:
                provider_payload = {"sender": self.settings.notification_sms_sender, "recipient": request.recipient,
                                    "content": text, "type": "transactional", "unicodeEnabled": False}
            delivery_id = secrets.token_hex(16)
            stamp = now.isoformat()
            conn.execute("INSERT INTO passenger_notifications (id,scenario_id,group_id,offer_id,channel,recipient_hash,recipient_masked,key_hash,payload_hash,status,provider,created_at,updated_at,deadline) VALUES(?,?,?,?,?,?,?,?,?,'sending',?,?,?,?)",
                (delivery_id, world.scenario_id, group_id, offer.id, request.channel, _digest(request.channel + ":" + request.recipient), _masked(request.channel, request.recipient), key_hash, payload_hash, provider, stamp, stamp, now.timestamp() + self.timeout + 30))
            row = conn.execute(f"SELECT {_COLUMNS} FROM passenger_notifications WHERE id=?", (delivery_id,)).fetchone()
            return self._receipt(row), (provider_payload, group.token)

    def _finish(self, delivery_id, status, *, message_id=None, error_code=None, error_message=None):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE passenger_notifications SET status=?,provider_message_id=?,updated_at=?,error_code=?,error_message=? WHERE id=? AND status='sending'",
                         (status, message_id, self.now().isoformat(), error_code, error_message, delivery_id))
            return self._receipt(conn.execute(f"SELECT {_COLUMNS} FROM passenger_notifications WHERE id=?", (delivery_id,)).fetchone())

    def _unknown(self, delivery_id, code):
        return self._finish(delivery_id, "unknown", error_code=code,
            error_message="The send outcome is unknown. Check the provider before sending another message.")

    async def send(self, group_id: str, request: NotificationRequest) -> dict:
        delivery, message = self._claim(group_id, request)
        if message is None:
            return delivery
        payload, token = message
        provider = delivery["provider"]
        key = _secret(getattr(self.settings, provider + "_api_key", ""))
        if provider == "resend":
            endpoint = "https://api.resend.com/emails"
            # The provider retains this key for 24 hours. Our durable ledger also
            # prevents retries after that window, including ambiguous outcomes.
            headers = {"Authorization": "Bearer " + key, "Accept": "application/json",
                       "Idempotency-Key": delivery["id"]}
        else:
            endpoint = "https://api.brevo.com/v3/" + ("smtp/email" if request.channel == "email" else "transactionalSMS/send")
            headers = {"api-key": key, "Accept": "application/json"}
        from .provider_budget import consume_call
        from .providers.base import ProviderError
        try:
            consume_call(provider)
        except ProviderError:
            return self._finish(delivery["id"], "failed", error_code="command_budget_exhausted",
                error_message="The command's external-call allowance is exhausted; no message was sent.")
        try:
            response = await asyncio.wait_for(self.client.post(endpoint,
                headers=headers, json=payload), timeout=self.timeout)
        except asyncio.CancelledError:
            self._unknown(delivery["id"], "interrupted")
            raise
        except (httpx.RequestError, TimeoutError):
            return self._unknown(delivery["id"], "transport_unknown")
        except Exception:
            # Neither exception strings nor provider response bodies are exposed.
            return self._unknown(delivery["id"], "provider_unknown")
        if 400 <= response.status_code < 500:
            return self._finish(delivery["id"], "failed", error_code=f"provider_http_{response.status_code}",
                error_message="The provider rejected this notification. Check notification configuration and the recipient before a new send.")
        if response.status_code != (200 if provider == "resend" else 201):
            return self._unknown(delivery["id"], f"provider_http_{response.status_code}")
        try:
            body = response.json()
            message_id = body.get("id" if provider == "resend" else "messageId") if isinstance(body, dict) else None
            if request.channel == "sms":
                valid = not isinstance(message_id, bool) and isinstance(message_id, (str, int)) and str(message_id).isdigit() and 0 < int(message_id) < 10**30
            else:
                valid = isinstance(message_id, str) and bool(re.fullmatch(r"[A-Za-z0-9<][A-Za-z0-9_.:@<>-]{0,254}", message_id))
            if not valid or any(sensitive and sensitive in str(message_id) for sensitive in (request.recipient, token, key)):
                return self._unknown(delivery["id"], "invalid_provider_response")
        except (ValueError, TypeError):
            return self._unknown(delivery["id"], "invalid_provider_response")
        return self._finish(delivery["id"], "accepted", message_id=str(message_id))


def create_notification_router(service: NotificationService, operator):
    router = APIRouter(prefix="/api/groups/{group_id}/notifications", dependencies=[Depends(operator)])

    @router.get("")
    def history(group_id: str):
        return service.history(group_id)

    @router.post("")
    async def send(group_id: str, body: NotificationRequest):
        return await service.send(group_id, body)

    return router
