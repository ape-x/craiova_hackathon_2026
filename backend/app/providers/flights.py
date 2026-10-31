"""Bounded FR24 observations, never schedules or seat availability."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time as day_time, timedelta, timezone
import hashlib
import math
import re
import time

from ..airport_catalog import lookup_airport
from ..models import FlightAirportReference, FlightSelection
from .base import HTTPProvider, ProviderError, secret_value


class FlightProvider(HTTPProvider):
    def __init__(self, settings, *, client=None, usage_store=None):
        super().__init__(settings, client=client)
        self.requests_used = 0
        self.estimated_credits_used = 0
        self.last_request_at: float | None = None
        self._lock = asyncio.Lock()
        self.usage_store = usage_store

    @property
    def configured(self) -> bool:
        return bool(secret_value(getattr(self.settings, "fr24_api_key", "")))

    async def _reserve(self, cost: int):
        interval=max(15,int(getattr(self.settings,"flight_poll_seconds",30)))
        maximum=max(0,int(getattr(self.settings,"fr24_max_requests",80)))
        credits=max(0,int(getattr(self.settings,"fr24_max_credits",4000)))
        if self.usage_store is None and self.last_request_at is not None and time.monotonic()-self.last_request_at<interval:
            raise ProviderError("FR24 polling is limited to one shared request per configured interval.",provider="Flightradar24",code="poll_interval")
        if self.requests_used>=maximum or self.estimated_credits_used+cost>credits:
            raise ProviderError("Shared FR24 request/credit budget exhausted. No further request was made.",provider="Flightradar24",code="budget_exhausted")
        if self.usage_store is not None:
            failure=await asyncio.to_thread(self.usage_store.reserve_fr24_request,interval,maximum,credits,cost)
            if failure:
                message="Shared FR24 request/credit budget exhausted." if failure=="budget_exhausted" else "Another FR24 request used the shared polling interval. Try again after the configured interval."
                raise ProviderError(message,provider="Flightradar24",code=failure)
        self.last_request_at=time.monotonic()
        self.requests_used+=1
        self.estimated_credits_used+=cost

    async def lookup(self,flight_number: str,day: date) -> list[dict]:
        if not self.configured:
            raise ProviderError("FR24_API_KEY is missing; flight lookup is unavailable.",provider="Flightradar24",code="not_configured")
        if getattr(self.settings,"fr24_environment","live")!="live":
            raise ProviderError("A live FR24 connection is required to identify a real flight.",provider="Flightradar24",code="sandbox")
        if self._lock.locked():
            raise ProviderError("Another FR24 request is in progress. Retry this lookup after it finishes.",provider="Flightradar24",code="lookup_in_progress")
        start=datetime.combine(day,day_time.min,tzinfo=timezone.utc)
        end=start+timedelta(days=1)
        # Full summaries: live2, historical<30days3, older6 credits/result.
        rate=6 if (datetime.now(timezone.utc).date()-day).days>=30 else 3
        async with self._lock:
            await self._reserve(5*rate)
            async with asyncio.timeout(getattr(self.settings,"request_timeout_seconds",45)):
                payload=await self.request("GET",getattr(self.settings,"fr24_base_url","https://fr24api.flightradar24.com/api").rstrip("/")+"/flight-summary/full",provider="Flightradar24",headers={"Authorization":f"Bearer {secret_value(self.settings.fr24_api_key)}","Accept":"application/json","Accept-Version":"v1"},params={"flights":flight_number,"flight_datetime_from":start.strftime("%Y-%m-%dT%H:%M:%S"),"flight_datetime_to":(end-timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S"),"sort":"desc","limit":5})
        try:
            rows=payload.get("data")
            if not isinstance(rows,list) or len(rows)>5:
                raise ValueError("Unbounded summary")
            result=[]
            seen=set()
            for row in rows:
                identity=_summary_text(row.get("fr24_id"),32)
                if not identity or not re.fullmatch(r"[0-9a-fA-F]+",identity) or identity.lower() in seen:
                    raise ValueError("Invalid or duplicate flight identity")
                seen.add(identity.lower())
                number=_summary_text(row.get("flight"),16)
                if number and re.sub(r"[\s-]","",number).upper()!=flight_number:
                    raise ValueError("Summary does not match requested flight")
                first,last=_summary_stamp(row.get("first_seen")),_summary_stamp(row.get("last_seen"))
                if first and not start<=datetime.fromisoformat(first)<end:
                    raise ValueError("Summary does not match requested UTC day")
                ended=row.get("flight_ended")
                if ended in ("true","false"):
                    ended=ended=="true"
                if ended is not None and type(ended) is not bool:
                    raise ValueError("Invalid flight-ended status")
                selection=FlightSelection(selection_id="flight_"+hashlib.sha256(identity.lower().encode()).hexdigest()[:32],fr24_id=identity,
                    flight_number=number,callsign=_summary_text(row.get("callsign"),16),registration=_summary_text(row.get("reg"),32),aircraft_type=_summary_text(row.get("type"),16),
                    origin=_summary_airport(row.get("orig_iata"),_summary_alias(row,"orig_icao","origin_icao")),intended_destination=_summary_airport(row.get("dest_iata"),_summary_alias(row,"dest_icao","destination_icao")),actual_destination=_summary_airport(row.get("dest_iata_actual"),_summary_alias(row,"dest_icao_actual","destination_icao_actual")),
                    first_seen=first,last_seen=last,takeoff_at=_summary_stamp(row.get("datetime_takeoff")),landed_at=_summary_stamp(row.get("datetime_landed")),flight_ended=ended,status="ended" if ended is True else "tracking" if ended is False else "unknown")
                result.append(selection.model_dump(mode="json"))
            return result
        except (ValueError,TypeError,AttributeError,KeyError) as exc:
            raise ProviderError("FR24 returned invalid or mismatched flight-summary data; no flight was selected.",provider="Flightradar24",code="invalid_response") from exc

    async def fetch(self) -> list[dict]:
        if not self.configured:
            raise ProviderError("FR24_API_KEY is missing; observed traffic is unavailable.", provider="Flightradar24", code="not_configured")
        environment = getattr(self.settings, "fr24_environment", "live")
        if environment not in ("live", "sandbox"):
            raise ProviderError("FR24_ENVIRONMENT must be live or sandbox.", provider="Flightradar24", code="configuration")
        async with self._lock:
            limit = max(1, min(20, int(getattr(self.settings, "fr24_result_limit", 5))))
            bounds = getattr(self.settings, "fr24_bounds", "46.0,43.8,23.0,26.5")
            try:
                north, south, west, east = (float(n) for n in bounds.split(","))
                if not (-90 <= south < north <= 90 and -180 <= west < east <= 180) or (north-south)*(east-west) > 25:
                    raise ValueError("invalid bounds")
            except (ValueError, TypeError, AttributeError) as exc:
                raise ProviderError("FR24 bounds must be north,south,west,east and cover at most 25 square degrees.", provider="Flightradar24", code="configuration") from exc
            # Reserve the worst possible cost before I/O. Failed/unknown responses
            # retain this conservative charge; no automatic retry spends again.
            await self._reserve(limit*8)
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


def _summary_alias(row,primary,alias):
    # The official full example uses orig_/dest_; the shared field table uses
    # origin_/destination_. Accept both documented forms without hiding conflict.
    if primary in row and alias in row and row[primary]!=row[alias]:
        raise ValueError("Conflicting source airport aliases")
    return row[primary] if primary in row else row.get(alias)


def _summary_text(value,maximum):
    if value is None:
        return None
    if not isinstance(value,str) or not value.strip() or len(value)>maximum or any(ord(char)<32 for char in value):
        raise ValueError("Invalid summary identifier")
    return value.strip()


def _summary_stamp(value):
    if value is None:
        return None
    value=_summary_text(value,64)
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",value):
        raise ValueError("A source timestamp must include its time")
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    # FR24 documents these summary fields as UTC, including offset-free examples.
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _summary_airport(iata,icao):
    iata,icao=_summary_text(iata,3),_summary_text(icao,4)
    if iata is None and icao is None:
        return None
    if (iata is not None and not re.fullmatch(r"[A-Z]{3}",iata)) or (icao is not None and not re.fullmatch(r"[A-Z0-9]{4}",icao)):
        raise ValueError("Invalid source airport code")
    by_iata=lookup_airport(iata) if iata else None
    by_icao=lookup_airport(icao) if icao else None
    if by_iata and by_icao and by_iata["code"]!=by_icao["code"]:
        raise ValueError("Conflicting source airport codes")
    airport=by_iata or by_icao
    return FlightAirportReference(iata=iata,icao=icao,catalog_code=airport["code"] if airport else None)
