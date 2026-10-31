"""Authenticated consumer fare references; no seat inventory or booking methods."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from .providers.base import ProviderError
from .providers.bileteria import BileteriaProvider, BileteriaSearchRequest
from .providers.ecb_rates import EcbRates
from .providers.reference_fx import SOURCE, SOURCE_URL, convert_to_eur
from .store import DomainError


class CoachFares:
    def __init__(self, settings, *, provider=None, fx=None, now=None):
        self.provider = provider or BileteriaProvider(settings)
        self.fx = fx or EcbRates()
        self._owns_fx = fx is None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.timeout = min(20, max(1, float(settings.request_timeout_seconds) + 1))
        self._active = asyncio.Lock()
        self._closed = False

    def status(self):
        return {
            "provider": "BILETERIA", "configured": self.provider.configured and not self._closed,
            "source_url": "https://www.bileteria.ro/", "adult_limit": 8, "max_advance_days": 31,
            "supported_routes": [{"origin_airport": "CRA", "origin_name": "Craiova Airport",
                "destination_airport": "OTP", "destination_name": "Bucharest Henri Coanda Airport"}],
            "booking_supported": False, "inventory_verified": False,
        }

    async def aclose(self):
        self._closed = True
        await self.provider.aclose()
        if self._owns_fx:
            await self.fx.aclose()

    def enrich(self, reference, rates):
        """Pure RON conversion, independent of APL's GBP-only planning contract."""
        result = copy.deepcopy(reference)
        options = result.get("options", [])
        for option in options:
            option["eur_reference_estimate"] = None
        fx = {"state": "unavailable" if options else "not_requested", "source_currency": "RON",
            "source": SOURCE, "source_url": SOURCE_URL, "reference": None,
            "cache": rates.get("cache") if options else None,
            "settlement_guaranteed": False, "revalidation_required": True,
            "message": "ECB reference unavailable; original RON fares remain available." if options else
                "No priced coach reference requires conversion."}
        result["fx"] = fx
        if not options:
            return result
        if rates.get("table") is None:
            fx["code"] = rates.get("code", "fx_unavailable")
            return result
        try:
            now = self._now()
            conversions = []
            for option in options:
                price = option["price"]
                if price["currency"] != "RON":
                    raise ValueError("Consumer fare currency changed")
                conversions.append(convert_to_eur(price["amount"], "RON", rates["table"],
                    as_of=now, max_rate_age_days=self.fx.MAX_RATE_AGE_DAYS))
            fx.update(state=conversions[0]["state"], reference=conversions[0]["reference"])
            if fx["state"] == "ready":
                for option, conversion in zip(options, conversions):
                    option["eur_reference_estimate"] = conversion["eur_reference_estimate"]
                fx["message"] = "EUR reference estimate only, not a supplier EUR fare. Seats and supplier expiry are unverified. Revalidate the fare and payment conversion before booking; EUR settlement is not guaranteed."
            elif fx["state"] == "stale_rate":
                fx["message"] = f"The ECB reference exceeds this app's {self.fx.MAX_RATE_AGE_DAYS}-calendar-day freshness policy. Original RON fares remain available; no EUR estimate is shown."
            else:
                fx["message"] = "The ECB feed has no RON rate. Original RON fares remain available; no EUR estimate is shown."
        except (KeyError, ValueError, TypeError):
            fx.update(state="unavailable", code="fx_conversion_unavailable", reference=None)
        return result

    async def search(self, request: BileteriaSearchRequest):
        if self._closed:
            raise DomainError(503, "The coach fare service is shutting down.")
        if self._active.locked():
            raise DomainError(409, "A coach fare search is still running. Wait for it to finish before searching again.")
        async with self._active:
            try:
                async with asyncio.timeout(self.timeout):
                    reference = await self.provider.search(request)
            except TimeoutError as exc:
                raise ProviderError("The coach source did not finish before the deadline. No automatic retry was made.",
                    provider="BILETERIA", code="timeout") from exc
            except ProviderError as exc:
                if exc.code == "invalid_request":
                    raise DomainError(422, str(exc)) from exc
                raise
            rates = {"table": None, "cache": None, "code": "fx_not_requested"}
            # Validate and fetch the public fare before making an optional FX
            # request. Missing/invalid fares never cause extra reference traffic.
            if reference.get("options"):
                try:
                    rates = await asyncio.wait_for(self.fx.rates(), timeout=4)
                except Exception:
                    rates = {"table": None, "cache": None, "code": "fx_unavailable"}
            return self.enrich(reference, rates)


def create_coach_router(service: CoachFares, operator_dependency):
    router = APIRouter(prefix="/api/coaches", dependencies=[Depends(operator_dependency)])

    @router.get("/status")
    async def status():
        return service.status()

    @router.post("/search")
    async def search(request: BileteriaSearchRequest):
        return await service.search(request)

    return router
