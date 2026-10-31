"""Authenticated, read-only transfer quotes. This service cannot book transport."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from .providers.apl import APLProvider, APLQuoteRequest, DOCS_URL, SOURCE
from .providers.base import ProviderError
from .store import DomainError


class GroundTransfers:
    def __init__(self, settings, *, provider=None):
        self.provider = provider or APLProvider(settings)
        self.timeout = min(22, max(1, float(settings.request_timeout_seconds) + 1))
        self._active = asyncio.Lock()
        self._closed = False

    def status(self):
        return {"provider": SOURCE, "source_url": DOCS_URL, "configured": self.provider.configured and not self._closed,
            "transport": "private_transfer", "currency": "GBP", "price_scope": "per_vehicle",
            "timezone": "Europe/London", "booking_supported": False, "duration_supported": False}

    async def aclose(self):
        self._closed = True
        await self.provider.aclose()

    async def quote(self, request: APLQuoteRequest):
        if self._closed:
            raise DomainError(503, "The transfer quote service is shutting down.")
        # Do not silently queue duplicate client submissions behind a supplier
        # call. An explicit later search is a new call, never a saved fare.
        if self._active.locked():
            raise DomainError(409, "An APL quote search is still running. Wait for it to finish before searching again.")
        async with self._active:
            try:
                async with asyncio.timeout(self.timeout):
                    return await self.provider.quote(request)
            except TimeoutError as exc:
                raise ProviderError("APL did not return a quote before the deadline. No automatic retry was made.", provider=SOURCE, code="timeout") from exc
            except ProviderError as exc:
                if exc.code == "invalid_request":
                    raise DomainError(422, str(exc)) from exc
                raise


def create_transfer_router(service: GroundTransfers, operator_dependency):
    router = APIRouter(prefix="/api/transfers", dependencies=[Depends(operator_dependency)])

    @router.get("/status")
    async def status():
        return service.status()

    @router.post("/quote")
    async def quote(request: APLQuoteRequest):
        return await service.quote(request)

    return router
