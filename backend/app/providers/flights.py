"""Bounded FR24 observations, never schedules or seat availability."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import time

from .base import HTTPProvider, ProviderError, secret_value


class FlightProvider(HTTPProvider):
    def __init__(self, settings, *, client=None):
        super().__init__(settings, client=client)
        self.requests_used = 0
        self.estimated_credits_used = 0
        self.last_request_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "fr24_api_key", "")))

    async def fetch(self) -> list[dict]:
        if not self.configured:
            raise ProviderError("FR24_API_KEY is missing; observed traffic is unavailable.", provider="Flightradar24", code="not_configured")
        environment = getattr(self.settings, "fr24_environment", "live")
        if environment not in ("live", "sandbox"):
            raise ProviderError("FR24_ENVIRONMENT must be live or sandbox.", provider="Flightradar24", code="configuration")
        async with self._lock:
            interval = max(15, int(getattr(self.settings, "flight_poll_seconds", 30)))
            if self.last_request_at is not None and time.monotonic() - self.last_request_at < interval:
                raise ProviderError("FR24 polling is limited to one shared request per configured interval.", provider="Flightradar24", code="poll_interval")
            limit = max(1, min(20, int(getattr(self.settings, "fr24_result_limit", 5))))
            maximum = max(0, int(getattr(self.settings, "fr24_max_requests", 80)))
            credits = max(0, int(getattr(self.settings, "fr24_max_credits", 4000)))
            if self.requests_used >= maximum or self.estimated_credits_used + limit * 8 > credits:
                raise ProviderError("FR24 local request/credit budget reached; polling must remain paused.", provider="Flightradar24", code="budget_exhausted")
            bounds = getattr(self.settings, "fr24_bounds", "46.0,43.8,23.0,26.5")
            try:
                north, south, west, east = (float(n) for n in bounds.split(","))
                if not (-90 <= south < north <= 90 and -180 <= west < east <= 180) or (north-south)*(east-west) > 25:
                    raise ValueError("invalid bounds")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ProviderError("FR24 bounds must be north,south,west,east and cover at most 25 square degrees.", provider="Flightradar24", code="configuration") from exc
            self.last_request_at = time.monotonic()
            self.requests_used += 1
            # Reserve the worst possible cost before I/O. Failed/unknown responses
            # retain this conservative charge; no automatic retry spends again.
            self.estimated_credits_used += limit * 8
            data = await self.request("GET", getattr(self.settings, "fr24_base_url", "https://fr24api.flightradar24.com/api").rstrip("/") + "/live/flight-positions/full", provider="Flightradar24", headers={"Authorization": f"Bearer {secret_value(self.settings.fr24_api_key)}", "Accept": "application/json", "Accept-Version": "v1"}, params={"bounds": bounds, "limit": limit})
            rows = data.get("data")
            if not isinstance(rows, list) or len(rows) > limit:
                raise ProviderError("FR24 returned an invalid or unbounded observation list.", provider="Flightradar24", code="invalid_response")
            mode = "SANDBOX" if environment == "sandbox" else "LIVE"
            received = datetime.now(timezone.utc).isoformat()
            observations = []
            try:
                for row in rows:
                    stamp = row["timestamp"]
                    if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
                        raise ValueError("source timestamp missing timezone")
                    lat, lon = float(row["lat"]), float(row["lon"])
                    if not math.isfinite(lat + lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError("invalid coordinates")
                    # Sandbox keys ignore bounds. Reject an obvious static/global
                    # sample instead of accidentally presenting it as local LIVE.
                    if mode == "LIVE" and not (south <= lat <= north and west <= lon <= east):
                        raise ValueError("observation outside requested region; check sandbox key")
                    observations.append({
                        "id": str(row["fr24_id"]), "callsign": row.get("callsign") or row.get("flight") or str(row["fr24_id"]),
                        "flight": row.get("flight"), "lat": lat, "lon": lon,
                        "altitude_ft": row.get("alt"), "groundspeed_knots": row.get("gspeed"), "heading": row.get("track"),
                        "registration": row.get("reg"), "aircraft_type": row.get("type"),
                        "origin": row.get("orig_iata"), "destination": row.get("dest_iata"),
                        "observed_at": stamp, "received_at": received, "source": "Flightradar24",
                        "position_source": row.get("source"), "mode": mode,
                    })
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError("FR24 observation is missing a valid source timestamp or position.", provider="Flightradar24", code="invalid_response") from exc
            self.estimated_credits_used -= limit * 8 - max(1, len(rows) * 8)
            return observations
