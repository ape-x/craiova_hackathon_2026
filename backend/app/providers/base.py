"""HTTP boundaries: no implicit retry, credential leakage, or simulated success."""
from __future__ import annotations

from typing import Any
import httpx


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str = "provider", code: str = "provider_error"):
        super().__init__(message)
        self.provider, self.code = provider, code


class HTTPProvider:
    def __init__(self, settings: Any, *, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.timeout = float(getattr(settings, "request_timeout_seconds", 45))
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def request(self, method: str, url: str, *, provider: str, **kwargs: Any) -> dict:
        from ..provider_budget import consume_call
        consume_call(provider)
        try:
            response = await self.client.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{provider} timed out; no result was applied.", provider=provider, code="timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # Do not return upstream body: it can echo credentials or private input.
            reason = {401: "credential rejected", 403: "account access denied", 429: "rate or account budget limit"}.get(status, "request failed")
            raise ProviderError(f"{provider}: {reason} (HTTP {status}).", provider=provider, code=f"http_{status}") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"{provider} connection failed.", provider=provider, code="connection") from exc
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("object required")
            return payload
        except (ValueError, TypeError) as exc:
            raise ProviderError(f"{provider} returned invalid JSON.", provider=provider, code="invalid_response") from exc


def secret_value(value: Any) -> str:
    """Support plain settings and SecretStr without ever returning a masked key."""
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")
