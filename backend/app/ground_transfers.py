"""Authenticated, read-only transfer quotes. This service cannot book transport."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from .providers.apl import APLProvider, APLQuoteRequest, DOCS_URL, SOURCE
from .providers.base import ProviderError
from .providers.ecb_rates import EcbRates
from .store import DomainError


class GroundTransfers:
    def __init__(self, settings, *, provider=None, fx=None):
        self.provider = provider or APLProvider(settings)
        self.fx = fx or EcbRates()
        self.timeout = min(22, max(1, float(settings.request_timeout_seconds) + 1))
        self._active = asyncio.Lock()
        self._closed = False

    def status(self):
        return {"provider": SOURCE, "source_url": DOCS_URL, "configured": self.provider.configured and not self._closed,
            "transport": "private_transfer", "currency": "GBP", "price_scope": "per_vehicle",
            "timezone": "Europe/London", "booking_supported": False, "duration_supported": False}

    async def aclose(self):
        self._closed = True
        await asyncio.gather(self.provider.aclose(), self.fx.aclose())

    async def quote(self, request: APLQuoteRequest):
        if self._closed:
            raise DomainError(503, "The transfer quote service is shutting down.")
        # Do not silently queue duplicate client submissions behind a supplier
        # call. An explicit later search is a new call, never a saved fare.
        if self._active.locked():
            raise DomainError(409, "An APL quote search is still running. Wait for it to finish before searching again.")
        async with self._active:
            # The public reference fetch is independent of the supplier quote.
            # Its optional failure must never hide a successful GBP response.
            fx_task = asyncio.create_task(self.fx.rates()) if self.provider.configured else None
            try:
                async with asyncio.timeout(self.timeout):
                    result = await self.provider.quote(request)
            except TimeoutError as exc:
                raise ProviderError("APL did not return a quote before the deadline. No automatic retry was made.", provider=SOURCE, code="timeout") from exc
            except ProviderError as exc:
                if exc.code == "invalid_request":
                    raise DomainError(422, str(exc)) from exc
                raise
            else:
                try:
                    rates = await fx_task if fx_task is not None else {"table": None, "cache": None, "code": "fx_unavailable"}
                except Exception:
                    rates = {"table": None, "cache": None, "code": "fx_unavailable"}
                return self.fx.enrich(result, rates)
            finally:
                if fx_task is not None:
                    if not fx_task.done():
                        fx_task.cancel()
                    await asyncio.gather(fx_task, return_exceptions=True)


def create_transfer_router(service: GroundTransfers, operator_dependency):
    router = APIRouter(prefix="/api/transfers", dependencies=[Depends(operator_dependency)])

    @router.get("/status")
    async def status():
        return service.status()

    @router.post("/quote")
    async def quote(request: APLQuoteRequest):
        return await service.quote(request)

    return router
