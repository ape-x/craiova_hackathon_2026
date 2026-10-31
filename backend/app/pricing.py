"""Typed APL/ECB planning estimates; original GBP remains the supplier price.

All arithmetic and comparisons are local. This module neither fetches a rate nor
certifies settlement, supplier stock, a booking, or a road journey.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ECB_SOURCE = "European Central Bank"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
APL_SOURCE = "Airport Pickups London"
APL_DOCS = "https://mcp.airport-pickups-london.com/docs"
POLICY_ID = "ecb_reference_v1"
RECEIPT_MAX_AGE_SECONDS = 120
MAX_RATE_AGE_DAYS = 3
ECB_ZONE = ZoneInfo("Europe/Berlin")


def exact_amount(value: str, *, positive=False) -> Decimal:
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,12})?", value):
        raise ValueError("A bounded exact decimal string is required")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal amount") from exc
    if not 0 <= amount <= 1_000_000 or (positive and amount == 0):
        raise ValueError("Amount outside supported bounds")
    return amount


def aware_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("An explicit timezone-aware timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("An explicit timezone-aware timestamp is required")
    return parsed


def eur_estimate(amount: str, rate: str) -> str:
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        converted = exact_amount(amount) / exact_amount(rate, positive=True)
        result = format(converted.quantize(Decimal("0.01")), "f")
    exact_amount(result)
    return result


class PriceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True, allow_inf_nan=False)

    @field_validator("settlement_guaranteed", "revalidation_required", mode="before", check_fields=False)
    @classmethod
    def explicit_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("Price evidence flags must be explicit booleans")
        return value


class GbpMoney(PriceModel):
    amount: str
    currency: Literal["GBP"]

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        exact_amount(value)
        return value


class EurMoney(PriceModel):
    amount: str
    currency: Literal["EUR"]

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        exact_amount(value)
        if not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
            raise ValueError("EUR planning estimates must be rounded to cents")
        return value


class TransferFxReference(PriceModel):
    source: Literal["European Central Bank"]
    source_url: Literal["https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"]
    base_currency: Literal["EUR"]
    rate_date: str
    retrieved_at: str
    as_of: str
    age_calendar_days: int = Field(ge=0, le=MAX_RATE_AGE_DAYS)
    age_timezone: Literal["Europe/Berlin"]
    max_age_days: Literal[3]
    source_units_per_eur: str

    @model_validator(mode="after")
    def verify_reference(self):
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.rate_date):
            raise ValueError("Invalid ECB reference date")
        day = date.fromisoformat(self.rate_date)
        retrieved, as_of = aware_timestamp(self.retrieved_at), aware_timestamp(self.as_of)
        if retrieved > as_of or day > retrieved.astimezone(ECB_ZONE).date() or (as_of.astimezone(ECB_ZONE).date() - day).days != self.age_calendar_days:
            raise ValueError("ECB reference chronology or age is inconsistent")
        exact_amount(self.source_units_per_eur, positive=True)
        return self


class AplPriceEvidence(PriceModel):
    kind: Literal["apl_ecb_reference"]
    original: GbpMoney
    estimated_eur: EurMoney
    price_scope: Literal["per_vehicle"]
    fx_reference: TransferFxReference
    policy_id: Literal["ecb_reference_v1"]
    settlement_guaranteed: Literal[False]
    revalidation_required: Literal[True]

    @model_validator(mode="after")
    def verify_conversion(self):
        if self.settlement_guaranteed is not False or self.revalidation_required is not True:
            raise ValueError("Reference prices require revalidation and are not guaranteed")
        if self.estimated_eur.amount != eur_estimate(self.original.amount, self.fx_reference.source_units_per_eur):
            raise ValueError("EUR estimate does not match the exact GBP price and ECB rate")
        return self


class AplQuoteFreshness(PriceModel):
    basis: Literal["app_receipt"]
    received_at: str
    max_age_seconds: Literal[120]

    @field_validator("received_at")
    @classmethod
    def valid_timestamp(cls, value):
        aware_timestamp(value)
        return value


class CostEvidence(PriceModel):
    kind: Literal["includes_ecb_reference"]
    total_eur: str
    estimated_eur: str
    original_gbp: str
    policy_id: Literal["ecb_reference_v1"]
    settlement_guaranteed: Literal[False]
    revalidation_required: Literal[True]

    @field_validator("total_eur", "estimated_eur", "original_gbp")
    @classmethod
    def valid_amount(cls, value):
        # Aggregated plans may contain several individually bounded prices.
        if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,12})?", value):
            raise ValueError("Invalid exact cost summary")
        return value

    @field_validator("total_eur", "estimated_eur")
    @classmethod
    def euro_cents(cls, value):
        if not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
            raise ValueError("EUR summaries must be expressed in cents")
        return value


def _plain(value):
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def build_apl_price_evidence(quote: dict, option_id: str, *, now=None, require_current=True) -> AplPriceEvidence:
    """Validate the server-held normalized quote and its matching ECB conversion."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or quote.get("provider") != APL_SOURCE or quote.get("source_url") != APL_DOCS or quote.get("state") != "ready" or quote.get("transport") != "private_transfer":
        raise ValueError("A sourced APL private-transfer quote is required")
    if quote.get("source_quoted_at") is not None or quote.get("expires_at") is not None:
        raise ValueError("This APL receipt has no verified supplier timestamp or expiry")
    received = aware_timestamp(quote.get("received_at"))
    if received > now or (require_current and (now - received).total_seconds() > RECEIPT_MAX_AGE_SECONDS):
        raise ValueError("APL app receipt is stale or future-dated")
    options = quote.get("options")
    if not isinstance(options, list):
        raise ValueError("Missing APL options")
    matching = [item for item in options if isinstance(item, dict) and item.get("id") == option_id]
    if len(matching) != 1:
        raise ValueError("APL option identity is ambiguous or absent")
    option, fx = matching[0], quote.get("fx")
    request = quote.get("request")
    if not isinstance(request, dict) or type(request.get("passengers")) is not int or type(request.get("suitcases")) is not int:
        raise ValueError("APL party and luggage scope must be explicit")
    if type(option.get("max_passengers")) is not int or type(option.get("max_suitcases")) is not int or not 1 <= request["passengers"] <= option["max_passengers"] or not 0 <= request["suitcases"] <= option["max_suitcases"]:
        raise ValueError("APL vehicle does not cover the requested party and luggage")
    if not isinstance(fx, dict) or fx.get("state") != "ready" or fx.get("source") != ECB_SOURCE or fx.get("source_url") != ECB_URL or fx.get("settlement_guaranteed") is not False or fx.get("revalidation_required") is not True:
        raise ValueError("A verified matching ECB reference is required")
    reference = TransferFxReference.model_validate(fx.get("reference"))
    if aware_timestamp(reference.as_of) > now or (require_current and (now.astimezone(ECB_ZONE).date() - date.fromisoformat(reference.rate_date)).days > MAX_RATE_AGE_DAYS):
        raise ValueError("ECB assessment is future-dated or its rate is stale")
    converted = option.get("eur_reference_estimate")
    if option.get("price_scope") != "per_vehicle" or option.get("fits_request") is not True or not isinstance(converted, dict) or converted.get("kind") != "ecb_reference_estimate" or converted.get("rounding") != "half_even_to_cent":
        raise ValueError("APL option has no complete per-vehicle reference estimate")
    return AplPriceEvidence(kind="apl_ecb_reference", original=option.get("price"),
        estimated_eur={"amount": converted.get("amount"), "currency": converted.get("currency")},
        price_scope="per_vehicle", fx_reference=reference, policy_id=POLICY_ID,
        settlement_guaranteed=False, revalidation_required=True)


def valid_apl_resource_price(resource, group, *, now=None, require_current=True) -> bool:
    """Check typed evidence against source facts, party scope and legacy numbers."""
    try:
        quote = resource.provenance["apl_quote"]
        evidence = build_apl_price_evidence(quote, resource.provenance["apl_option_id"], now=now, require_current=require_current)
        freshness = AplQuoteFreshness.model_validate(_plain(resource.quote_freshness))
        request = quote["request"]
        return (resource.source == APL_SOURCE and resource.kind == "bus" and resource.transport_mode == "private_transfer"
            and resource.capacity_scope == "private_vehicle_quote" and resource.capacity == group.size
            and resource.eligible_group_ids == [group.id] and request.get("passengers") == group.size
            and type(request.get("passengers")) is int and resource.quote_total_cost is None and resource.cost_per_person == 0
            and resource.valid_until is None and resource.provenance.get("quoted_at") is None and resource.requires_revalidation
            and freshness.received_at == quote["received_at"] and AplPriceEvidence.model_validate(_plain(resource.price_evidence)) == evidence
            and resource.budget_total_cost is not None and Decimal(str(resource.budget_total_cost)) == exact_amount(evidence.estimated_eur.amount))
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        return False


def app_receipt_deadline(resource) -> datetime | None:
    if resource.quote_freshness is None:
        return None
    freshness = AplQuoteFreshness.model_validate(_plain(resource.quote_freshness))
    deadline = aware_timestamp(freshness.received_at) + timedelta(seconds=freshness.max_age_seconds)
    if getattr(resource, "airport_arrival_assumption", None) is not None:
        route = resource.provenance.get("google_route")
        if not isinstance(route, dict):
            return None
        route_receipt = aware_timestamp(route.get("fetched_at"))
        deadline = min(deadline, route_receipt + timedelta(seconds=RECEIPT_MAX_AGE_SECONDS))
    return deadline


def cost_summary(legs) -> CostEvidence | None:
    """Sum exact GBP and estimated EUR once per selected private vehicle."""
    entries = [_plain(item) for item in legs]
    estimated = [item["price_evidence"] for item in entries if item.get("price_evidence") is not None]
    if not estimated:
        return None
    prices = [AplPriceEvidence.model_validate(item) for item in estimated]
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        total = sum((Decimal(str(item["cost"])) for item in entries), Decimal(0))
        eur = sum((exact_amount(item.estimated_eur.amount) for item in prices), Decimal(0))
        gbp = sum((exact_amount(item.original.amount) for item in prices), Decimal(0))
        if not total.is_finite() or total < eur or total != total.quantize(Decimal("0.01")):
            raise ValueError("Leg totals are inconsistent with reference-estimate costs")
        return CostEvidence(kind="includes_ecb_reference", total_eur=format(total.quantize(Decimal("0.01")), "f"),
            estimated_eur=format(eur.quantize(Decimal("0.01")), "f"), original_gbp=format(gbp, "f"),
            policy_id=POLICY_ID, settlement_guaranteed=False, revalidation_required=True)


def matching_cost_summary(legs, evidence, total) -> bool:
    try:
        expected = cost_summary(legs)
        actual = CostEvidence.model_validate(_plain(evidence)) if evidence is not None else None
        if actual != expected:
            return False
        return expected is None or Decimal(str(total)) == Decimal(expected.total_eur)
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        return False
