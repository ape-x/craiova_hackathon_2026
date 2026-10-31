"""Pure ECB reference-rate parsing and EUR estimates. No network or persistence.

ECB rates are informational, not transaction or settlement prices:
https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html

The integrating caller must retrieve SOURCE_URL and provide the retrieval time.
Age tolerance and any budget cushion are explicit operator policies. Neither is
an ECB freshness guarantee, a supplier charge, or an automatic fallback rate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_EVEN, localcontext
from types import MappingProxyType
from typing import Mapping
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


SOURCE = "European Central Bank"
SOURCE_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
NOTICE = "ECB reference estimate only; the supplier charges the original currency. Payment-provider rates and fees may differ."
ECB_ZONE = ZoneInfo("Europe/Berlin")
ECB_NS = "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"
GESMES_NS = "http://www.gesmes.org/xml/2002-08-01"
MAX_XML_BYTES = 65_536


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone-aware timestamp is required")
    return value


def _currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("Currency must be an explicit uppercase three-letter code")
    return value


def _decimal(value, *, maximum=Decimal("1000000"), positive=False):
    # Floats cannot preserve an exact supplier decimal price. The caller must
    # keep the original JSON/string value instead of rounding a binary float.
    if not isinstance(value, (str, Decimal)) or len(str(value)) > 64:
        raise ValueError("Supply an exact decimal string or Decimal")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal value") from exc
    if not amount.is_finite() or not Decimal(0) <= amount <= maximum or not -12 <= amount.as_tuple().exponent <= 12:
        raise ValueError("Decimal amount is outside supported bounds")
    if positive and amount == 0:
        raise ValueError("Reference rates must be positive")
    return amount


@dataclass(frozen=True, slots=True)
class EcbRateTable:
    rate_date: date
    retrieved_at: datetime
    # The ECB quote direction is foreign currency units per ONE euro.
    source_units_per_eur: Mapping[str, Decimal]
    source: str = SOURCE
    source_url: str = SOURCE_URL


def parse_ecb_daily_xml(payload: bytes | str, *, retrieved_at: datetime) -> EcbRateTable:
    """Parse the single-date daily feed; historical/multiple-date XML is rejected."""
    _aware(retrieved_at)
    if not isinstance(payload, (str, bytes)):
        raise ValueError("ECB response must be XML text or bytes")
    if len(payload) > MAX_XML_BYTES:
        raise ValueError("ECB XML exceeds the bounded response size")
    try:
        text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError as exc:
        raise ValueError("ECB XML must be UTF-8") from exc
    if len(text.encode("utf-8")) > MAX_XML_BYTES or "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entities and oversized XML are not allowed")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid ECB XML") from exc
    if root.tag != f"{{{GESMES_NS}}}Envelope":
        raise ValueError("Unexpected ECB XML namespace")
    containers = root.findall(f"{{{ECB_NS}}}Cube")
    if len(containers) != 1:
        raise ValueError("Expected one ECB daily rate container")
    dates = containers[0].findall(f"{{{ECB_NS}}}Cube")
    if len(dates) != 1 or set(dates[0].attrib) != {"time"}:
        raise ValueError("Expected exactly one dated ECB rate set")
    day = dates[0].get("time", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("Missing ECB reference date")
    try:
        rate_date = date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError("Invalid ECB reference date") from exc
    if rate_date > retrieved_at.astimezone(ECB_ZONE).date():
        raise ValueError("ECB reference date is later than retrieval")
    rows = list(dates[0])
    if not 1 <= len(rows) <= 100:
        raise ValueError("Missing or unbounded ECB currency list")
    rates = {"EUR": Decimal("1")}
    for row in rows:
        if row.tag != f"{{{ECB_NS}}}Cube" or set(row.attrib) != {"currency", "rate"} or list(row):
            raise ValueError("Malformed ECB currency row")
        currency = _currency(row.get("currency"))
        if currency in rates:
            raise ValueError("Duplicate or conflicting ECB base currency")
        rates[currency] = _decimal(row.get("rate"), positive=True)
    return EcbRateTable(rate_date, retrieved_at, MappingProxyType(rates))


def convert_to_eur(
    amount: str | Decimal,
    currency: str,
    table: EcbRateTable,
    *,
    as_of: datetime,
    max_rate_age_days: int,
    operator_buffer_percent: str | Decimal | None = None,
):
    """Reference estimate only; stale/missing rates yield no converted/budget value.

    max_rate_age_days is an operator's calendar-day limit in the ECB time zone,
    not a claim that a feed is the latest working-day publication. Weekends and
    TARGET holidays receive no hidden exception. Explicit buffer zero is valid;
    omitted buffer means no budget allowance is calculated.
    """
    original = _decimal(amount)
    currency = _currency(currency)
    _aware(as_of)
    _aware(table.retrieved_at)
    if table.source != SOURCE or table.source_url != SOURCE_URL:
        raise ValueError("Conversion requires a sourced ECB rate table")
    if table.retrieved_at > as_of:
        raise ValueError("Cannot use a rate table retrieved after the evaluation time")
    if type(max_rate_age_days) is not int or not 0 <= max_rate_age_days <= 366:
        raise ValueError("Provide an explicit maximum calendar-day rate age")
    buffer = None if operator_buffer_percent is None else _decimal(operator_buffer_percent, maximum=Decimal("100"))
    age = (as_of.astimezone(ECB_ZONE).date() - table.rate_date).days
    if age < 0:
        raise ValueError("Cannot use a future-dated reference rate")
    rate = table.source_units_per_eur.get(currency)
    if rate is not None:
        rate = _decimal(rate, positive=True)
    state = "missing_rate" if rate is None else "stale_rate" if age > max_rate_age_days else "ready"
    result = {
        "state": state,
        "original_money": {"amount": format(original, "f"), "currency": currency},
        "reference": {"source": SOURCE, "source_url": SOURCE_URL, "base_currency": "EUR",
            "rate_date": table.rate_date.isoformat(), "retrieved_at": table.retrieved_at.isoformat(),
            "as_of": as_of.isoformat(), "age_calendar_days": age, "age_timezone": ECB_ZONE.key,
            "max_age_days": max_rate_age_days, "source_units_per_eur": format(rate, "f") if rate is not None else None},
        "eur_reference_estimate": None,
        "operator_budget_allowance": None,
        "operator_buffer_percent": format(buffer, "f") if buffer is not None else None,
        "settlement_guaranteed": False,
        "notice": NOTICE,
    }
    if state != "ready":
        return result
    # Isolate precision/rounding from unrelated Decimal use in the application.
    # Divide GBP by GBP-per-EUR; multiplying would invert the ECB quote.
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        converted = original / rate
        result["eur_reference_estimate"] = {
            "amount": format(converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), "f"),
            "currency": "EUR", "kind": "ecb_reference_estimate", "rounding": "half_even_to_cent",
        }
        if buffer is not None:
            # Apply the cushion BEFORE rounding, then round the allowance up.
            # This policy amount must never replace the supplier's GBP quote.
            allowance = (converted * (Decimal(1) + buffer / 100)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
            result["operator_budget_allowance"] = {
                "amount": format(allowance, "f"), "currency": "EUR", "kind": "operator_policy_allowance",
                "buffer_percent": format(buffer, "f"), "rounding": "ceiling_to_cent", "supplier_fee": False,
            }
    return result
