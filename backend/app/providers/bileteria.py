"""Bounded public consumer fares; never seat inventory, checkout or bookings.

The supported HTML shape was observed on the public CRA–OTP dated search.
Only the search URL is requested. Checkout metadata is parsed as inert text to
bind a displayed fare to its date and party; no checkout URL is returned.
The page has no independent mapping from checkout occurrence/trip references to
its station IDs. Matching visible stops and header scope verifies only the
displayed reference, not a checkout itinerary or ticket that could be bought.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import re
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import ProviderError


SOURCE = "BILETERIA"
SEARCH_URL = "https://www.bileteria.ro/Transport/AeroportCraiova-Otopeni"
MAX_BODY = 1_000_000
MAX_NODES = 20_000
MAX_DEPTH = 64
MAX_OPTIONS = 30
MAX_ADVANCE_DAYS = 31
LOCAL_ZONE = ZoneInfo("Europe/Bucharest")
NOTICE = "Displayed consumer fares do not verify seats for the requested party. No hold or booking was made."
TIME_NOTICE = "Times are displayed local clocks; the source does not supply UTC offsets or a timezone."
VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"))


class BileteriaSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    origin_airport: Literal["CRA"]
    destination_airport: Literal["OTP"]
    travel_date: date
    adults: int = Field(ge=1, le=8, strict=True)

    @field_validator("travel_date", mode="before")
    @classmethod
    def explicit_date(cls, value):
        if isinstance(value, datetime) or not isinstance(value, (str, date)):
            raise ValueError("Supply an explicit departure calendar date")
        if isinstance(value, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Use a YYYY-MM-DD departure date")
        return value


@dataclass
class _Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def text(self):
        return " ".join(child.text() if isinstance(child, _Node) else child for child in self.children)


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > MAX_NODES or len(self.stack) > MAX_DEPTH or len(attrs) > 100:
            raise ValueError("HTML structure exceeds bounds")
        attributes = {}
        for key, value in attrs:
            if key in attributes and key in {"id", "class", "itemprop", "itemtype", "co", "stp", "sts", "orap", "value", "content", "href", "onclick", "disabled", "aria-disabled", "title"}:
                raise ValueError("Duplicate source-bearing HTML attribute")
            # The public page duplicates unrelated styling attributes. Preserve
            # the browser's first-value rule without using styling as evidence.
            attributes.setdefault(key, value)
        node = _Node(tag, attributes)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.count += 1
        if self.count > MAX_NODES:
            raise ValueError("HTML structure exceeds bounds")
        self.stack[-1].children.append(value)


class _Rejected(ValueError):
    def __init__(self, code):
        self.code = code


def _text(value, maximum=500):
    if not isinstance(value, str):
        raise ValueError("Missing text")
    value = " ".join(value.split())
    if not value or len(value) > maximum or any(ord(character) < 32 for character in value):
        raise ValueError("Invalid text")
    return value


def _nodes(root, *, prop=None, css=None, identity=None, tag=None):
    return [node for node in root.walk() if
            (prop is None or node.attrs.get("itemprop") == prop) and
            (css is None or css in (node.attrs.get("class") or "").split()) and
            (identity is None or node.attrs.get("id") == identity) and
            (tag is None or node.tag == tag)]


def _one(items):
    if len(items) != 1:
        raise ValueError("Missing or ambiguous field")
    return items[0]


def _search_url(request):
    return SEARCH_URL + "?" + urlencode({"zi": request.travel_date.isoformat(), "pas": f"{request.adults}-0-0-0-0"})


def _money(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,7}(?:[.,][0-9]{1,2})?", value):
        raise ValueError("Missing decimal fare")
    amount = Decimal(value.replace(",", "."))
    if not 0 < amount <= 1_000_000:
        raise ValueError("Unsupported fare amount")
    return amount


def _clock(value):
    value = _text(value, 5)
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Invalid displayed time")
    return value


def _station(row, prop, slug, locality, reference_attr):
    stop = _one(_nodes(row, prop=prop))
    name = _one(_nodes(stop, prop="name", tag="a"))
    city = _text(_one(_nodes(stop, css="title")).text())
    url = _text(name.attrs.get("href"), 2000)
    parsed = urlsplit(url)
    reference = row.attrs.get(reference_attr, "")
    if (city != locality or not re.fullmatch(r"[0-9]{1,12}", reference)
            or parsed.scheme != "https" or parsed.hostname not in ("bileteria.ro", "www.bileteria.ro")
            or parsed.username or parsed.password or parsed.port is not None or parsed.query or parsed.fragment
            or not re.fullmatch(r"/" + re.escape(slug) + r"/[A-Za-z0-9-]+/Autogara" + reference, parsed.path)):
        raise ValueError("Stop differs from the supported airport route")
    return {"name": _text(name.text()), "locality": city, "source_url": url}


def _checkout_metadata(value):
    # Accept only the two observed source handlers as inert text. Never eval JS,
    # follow the checkout link, return it, or accept a concatenated/external URL.
    value = _text(value, 2500)
    regular = re.fullmatch(r"window\.location\s*=\s*'([^']+)'\s*;\s*event\.stopPropagation\(\)\s*;?", value)
    night = re.fullmatch(r"avertizeazaPlecareNoapte\(event,\s*'([^']+)',\s*'([^']+)',\s*'([0-9]{4}-[0-9]{2}-[0-9]{2})',\s*'([0-9]{2}:[0-9]{2})'\)\s*;?", value)
    if not regular and not night:
        raise ValueError("Unknown purchase metadata")
    links = [regular[1]] if regular else [night[1], night[2]]
    queries = []
    for link in links:
        parsed = urlsplit(link.strip())
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=10)
        query = dict(pairs)
        if (parsed.scheme or parsed.netloc or parsed.path != "/Checkout" or parsed.fragment
                or len(query) != len(pairs) or set(query) != {"plecareRef", "sosireRef", "dtPlecare", "pas", "cursa", "lang"}
                or query["lang"] != "ro" or any(not re.fullmatch(r"[0-9]{1,12}", query[key]) for key in ("plecareRef", "sosireRef", "cursa"))):
            raise ValueError("Unverified purchase metadata")
        queries.append(query)
    return queries, night


def _option(row, request):
    if len(_nodes(row, css="traseuInfo")) != 1:
        raise _Rejected("unsupported_connection")
    origin = _station(row, "departureBusStop", "AeroportCraiova", "Aeroport Craiova", "stp")
    destination = _station(row, "arrivalBusStop", "Otopeni", "Aeroport Otopeni", "sts")
    provider = _one(_nodes(row, prop="provider"))
    operator = _text(_one(_nodes(provider, prop="name", tag="meta")).attrs.get("content"))
    if operator != _text(row.attrs.get("co")):
        raise ValueError("Conflicting carrier")
    departure = _clock(_one(_nodes(row, prop="departureTime")).text())
    arrival = _clock(_one(_nodes(row, prop="arrivalTime")).text())
    if row.attrs.get("orap") != departure.replace(":", "") + "00":
        raise ValueError("Conflicting departure")
    duration_text = _text(_one(_nodes(row, css="durata2")).text(), 50)
    duration = re.fullmatch(r"([0-9]{1,2})h\s+([0-5][0-9])m", duration_text)
    if not duration or not 0 < int(duration[1]) * 60 + int(duration[2]) <= 24 * 60:
        raise ValueError("Unsupported duration")
    arrival_column = _text(_one(_nodes(row, css="ora-s")).text(), 100)
    rollovers = re.findall(r"\+\s*([0-9]+)\s*zi(?:le)?\b", arrival_column)
    if len(rollovers) > 1 or ("zi" in arrival_column and not rollovers):
        raise ValueError("Unverified arrival day")
    days = int(rollovers[0]) if rollovers else 0
    if days not in (0, 1):
        raise ValueError("Unsupported arrival day")
    departure_minutes = int(departure[:2]) * 60 + int(departure[3:])
    arrival_minutes = days * 1440 + int(arrival[:2]) * 60 + int(arrival[3:])
    if arrival_minutes - departure_minutes != int(duration[1]) * 60 + int(duration[2]):
        raise ValueError("Display clocks, day and duration disagree")
    offer = _one(_nodes(row, prop="offers"))
    amount = _money(offer.attrs.get("value"))
    low = _money(_one(_nodes(offer, prop="lowPrice", tag="meta")).attrs.get("content"))
    currency = _one(_nodes(offer, prop="priceCurrency", tag="meta")).attrs.get("content")
    visible = re.fullmatch(r"([0-9]+(?:[.,][0-9]{1,2})?)\s*lei", _text(offer.text(), 100))
    if currency != "RON" or not visible or low != amount or _money(visible[1]) != amount:
        raise ValueError("Conflicting fare")
    button = _one(_nodes(row, css="buy", tag="button"))
    queries, night = _checkout_metadata(button.attrs.get("onclick"))
    selected = queries[0]
    day = request.travel_date.isoformat()
    party = f"{request.adults}-0-0-0-0"
    if selected["dtPlecare"] != day or selected["pas"] != party:
        raise _Rejected("fare_scope_mismatch")
    if night:
        expected_next = {**selected, "dtPlecare": (request.travel_date + timedelta(days=1)).isoformat()}
        if queries[1] != expected_next or night[3] != day or night[4] != departure:
            raise _Rejected("fare_scope_mismatch")
    notices = []
    for node in _nodes(row, css="cursa-speciala"):
        notices.append(_text(node.attrs.get("title"), 1000))
    for node in _nodes(row, css="note"):
        notices.append(_text(node.text(), 1000))
    if len(notices) > 10:
        raise ValueError("Unbounded service notices")
    enabled = not ("disabled" in button.attrs or button.attrs.get("aria-disabled") == "true" or "disabled" in (button.attrs.get("class") or "").split())
    result = {
        "operator": operator, "departure_date": day, "departure_time": departure,
        "arrival_date": (request.travel_date + timedelta(days=days)).isoformat(),
        "arrival_time": arrival, "duration_text": duration_text,
        "departure_stop": origin, "arrival_stop": destination,
        "price": {"amount": format(amount, "f"), "currency": "RON"}, "price_scope": "requested_party",
        "adults": request.adults, "seat_availability": None, "supplier_valid_until": None,
        "purchase_action_enabled": enabled, "source_url": _search_url(request), "notices": list(dict.fromkeys(notices)),
    }
    fingerprint = json.dumps({**result, "source_trip": selected["cursa"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["id"] = "BIL-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24].upper()
    return (day, selected["cursa"]), result


def parse_search_html(payload: bytes, request: BileteriaSearchRequest, *, received_at: datetime):
    """Pure bounded parsing; timestamps attest observation, never fare expiry."""
    request = BileteriaSearchRequest.model_validate(request.model_dump())
    if not isinstance(payload, bytes) or len(payload) > MAX_BODY or b"\x00" in payload:
        raise ValueError("Invalid HTML body")
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("Observation time requires an offset")
    text = payload.decode("utf-8-sig", errors="strict")
    tree = _Tree()
    tree.feed(text)
    tree.close()
    root = tree.root
    expected_inputs = {"localitate-plecare-url": "AeroportCraiova", "localitate-sosire-url": "Otopeni",
        "localitate-plecare": "Aeroport Craiova", "localitate-sosire": "Aeroport Otopeni",
        "adulti": str(request.adults), "pensionari": "0", "studenti": "0", "elevi": "0", "copii": "0"}
    for name, value in expected_inputs.items():
        if _one(_nodes(root, identity=name, tag="input")).attrs.get("value") != value:
            raise ValueError("Returned route or party differs from request")
    dates = [match for node in _nodes(root, tag="script") for match in re.findall(r"\bconst\s+zi\s*=\s*'([0-9]{4}-[0-9]{2}-[0-9]{2})'\s*;", node.text())]
    if dates != [request.travel_date.isoformat()]:
        raise ValueError("Returned date differs from request")
    rows = _nodes(root, prop="busTrip")
    # An unrelated/error page must not look like a successful empty search.
    if not rows:
        raise ValueError("No recognized source result structure")
    rejected = Counter()
    options = []
    identities = {}
    for row in rows:
        try:
            if row.attrs.get("itemtype") != "http://schema.org/BusTrip" or "result" not in (row.attrs.get("class") or "").split():
                raise ValueError("Unknown journey row")
            identity, option = _option(row, request)
        except _Rejected as exc:
            rejected[exc.code] += 1
            continue
        except (ValueError, TypeError, KeyError, ArithmeticError):
            rejected["unverified_row"] += 1
            continue
        if identity in identities:
            if identities[identity] != option:
                # Neither conflicting revision is authoritative. Do not retain
                # whichever duplicate happened to occur first in the HTML.
                raise ValueError("Conflicting repeated trip")
            rejected["duplicate_row"] += 1
            continue
        identities[identity] = option
        if len(options) < MAX_OPTIONS:
            options.append(option)
        else:
            rejected["result_limit"] += 1
    return {"provider": SOURCE, "source_url": _search_url(request),
        "received_at": received_at.astimezone(timezone.utc).isoformat(), "request": request.model_dump(mode="json"),
        "options": options, "rejected_count": sum(rejected.values()),
        "rejection_reasons": [{"code": code, "count": count} for code, count in sorted(rejected.items())],
        "notices": [NOTICE, TIME_NOTICE, "Only direct services with matching dated party fares are included; connections and unverifiable rows are excluded."],
        "booking_supported": False}


class BileteriaProvider:
    def __init__(self, settings, *, client=None, now=None):
        self.timeout = min(15, max(1, float(getattr(settings, "request_timeout_seconds", 15))))
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._closed = False

    @property
    def configured(self):
        return not self._closed

    async def aclose(self):
        self._closed = True
        if self._owns_client:
            await self.client.aclose()

    def validate_request(self, request):
        request = BileteriaSearchRequest.model_validate(request.model_dump() if isinstance(request, BileteriaSearchRequest) else request)
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Provider clock requires an offset")
        if not 0 <= (request.travel_date - now.astimezone(LOCAL_ZONE).date()).days <= MAX_ADVANCE_DAYS:
            raise ProviderError("Choose a local departure date from today through the next 31 days; no source request was sent.", provider=SOURCE, code="invalid_request")
        return request

    async def search(self, request: BileteriaSearchRequest):
        request = self.validate_request(request)
        if self._closed:
            raise ProviderError("The consumer fare service is closed.", provider=SOURCE, code="not_configured")
        from ..provider_budget import consume_call
        consume_call(SOURCE)
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("GET", _search_url(request), timeout=self.timeout, follow_redirects=False,
                        headers={"Accept": "text/html", "User-Agent": "AirportRecoveryLive/0.1 consumer-fare-search"}) as response:
                    if response.status_code != 200:
                        raise ProviderError(f"BILETERIA search failed (HTTP {response.status_code}); no automatic retry was made.", provider=SOURCE, code=f"http_{response.status_code}")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/html":
                        raise ValueError("Expected HTML")
                    length = response.headers.get("content-length")
                    if length and (not length.isdigit() or int(length) > MAX_BODY):
                        raise ValueError("Body exceeds bounds")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16_384):
                        body.extend(chunk)
                        if len(body) > MAX_BODY:
                            raise ValueError("Body exceeds bounds")
            return parse_search_html(bytes(body), request, received_at=self._now())
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError("BILETERIA did not return a bounded fare result before the deadline; no retry was made.", provider=SOURCE, code="timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("BILETERIA could not be reached; no substitute fares were created.", provider=SOURCE, code="connection") from exc
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise ProviderError("BILETERIA returned an unverifiable dated fare page; no substitute fares were created.", provider=SOURCE, code="invalid_response") from exc
