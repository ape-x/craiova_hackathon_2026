"""Bounded public ECB reference retrieval; never a settlement or budget quote."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import time

import httpx

from .reference_fx import MAX_XML_BYTES, NOTICE, SOURCE, SOURCE_URL, convert_to_eur, parse_ecb_daily_xml


class EcbRates:
    TIMEOUT_SECONDS = 3
    CACHE_SECONDS = 900
    FAILURE_CACHE_SECONDS = 30
    MAX_RATE_AGE_DAYS = 3  # Application policy, not an ECB publication guarantee.

    def __init__(self, *, client=None, now=None, monotonic=None):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(follow_redirects=False)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._gate = asyncio.Lock()
        self._cached = None
        self._until = 0
        self._closed = False

    async def aclose(self):
        self._closed = True
        self._cached = None
        if self._owns_client:
            await self.client.aclose()

    async def rates(self):
        async with self._gate:
            if self._closed:
                return {"table": None, "cache": None, "code": "fx_closed"}
            if self._cached is not None and self._until > self._monotonic():
                return {**self._cached, "cache": "cached"}
            result = await self._fetch()
            self._cached = result
            self._until = self._monotonic() + (self.CACHE_SECONDS if result["table"] is not None else self.FAILURE_CACHE_SECONDS)
            return {**result, "cache": "fresh"}

    async def _fetch(self):
        try:
            # Only this constant official HTTPS origin is queried. HTTP redirects
            # and transport retries are disabled; the deadline includes streaming.
            async with asyncio.timeout(self.TIMEOUT_SECONDS):
                async with self.client.stream("GET", SOURCE_URL, follow_redirects=False, timeout=self.TIMEOUT_SECONDS,
                                              headers={"Accept": "application/xml,text/xml"}) as response:
                    if response.status_code != 200:
                        return {"table": None, "code": "fx_http_error"}
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > MAX_XML_BYTES):
                        return {"table": None, "code": "fx_invalid_response"}
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16_384):
                        body.extend(chunk)
                        if len(body) > MAX_XML_BYTES:
                            return {"table": None, "code": "fx_invalid_response"}
            table = parse_ecb_daily_xml(bytes(body), retrieved_at=self._now())
            return {"table": table}
        except (TimeoutError, httpx.TimeoutException):
            return {"table": None, "code": "fx_timeout"}
        except httpx.HTTPError:
            return {"table": None, "code": "fx_transport_error"}
        except (ValueError, TypeError):
            return {"table": None, "code": "fx_invalid_response"}

    def enrich(self, quote, rates):
        result = copy.deepcopy(quote)
        options = result.get("options", [])
        for option in options:
            option["eur_reference_estimate"] = None
        fx = {"state": "not_requested" if not options else "unavailable", "source": SOURCE, "source_url": SOURCE_URL,
              "reference": None, "cache": rates.get("cache") if options else None,
              "settlement_guaranteed": False, "revalidation_required": True,
              "message": "No transfer option requires conversion." if not options else
                  "ECB reference unavailable; the original GBP quote remains available. Revalidate before booking."}
        result["fx"] = fx
        if not options:
            return result
        if rates.get("table") is None:
            fx["code"] = rates.get("code", "fx_unavailable")
            return result
        try:
            as_of = self._now()
            converted = []
            for option in options:
                price = option["price"]
                if price["currency"] != "GBP":
                    raise ValueError("APL quote currency changed")
                converted.append(convert_to_eur(price["amount"], "GBP", rates["table"],
                                               as_of=as_of, max_rate_age_days=self.MAX_RATE_AGE_DAYS))
            fx.update(state=converted[0]["state"], reference=converted[0]["reference"])
            if fx["state"] == "ready":
                for option, conversion in zip(options, converted):
                    option["eur_reference_estimate"] = conversion["eur_reference_estimate"]
                fx["message"] = NOTICE + " Revalidate the supplier quote and payment conversion before booking. This is not a firm EUR budget amount."
            elif fx["state"] == "stale_rate":
                fx["message"] = f"The ECB reference exceeds this app's {self.MAX_RATE_AGE_DAYS}-calendar-day freshness policy; no EUR estimate is shown. The GBP quote remains available."
            else:
                fx["message"] = "The ECB feed has no GBP reference rate; no EUR estimate is shown. The GBP quote remains available."
        except (KeyError, ValueError, TypeError):
            fx["code"] = "fx_conversion_unavailable"
        return result
