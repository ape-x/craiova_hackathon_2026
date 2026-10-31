"""Authored ECB-shaped fixtures; no network, feed cache or supplier credentials."""
from datetime import datetime, timezone
from decimal import Decimal, Inexact, localcontext

import pytest

from app.providers.reference_fx import SOURCE_URL, convert_to_eur, parse_ecb_daily_xml


RETRIEVED = datetime(2030, 1, 4, 17, tzinfo=timezone.utc)


def xml(day="2030-01-04", rows='<Cube currency="GBP" rate="0.8"/><Cube currency="USD" rate="1.25"/>'):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
      <gesmes:subject>Reference rates</gesmes:subject>
      <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
      <Cube><Cube time="{day}">{rows}</Cube></Cube>
    </gesmes:Envelope>'''


def table(**changes):
    return parse_ecb_daily_xml(xml(**changes), retrieved_at=RETRIEVED)


def convert(amount="80.00", currency="GBP", rates=None, **changes):
    return convert_to_eur(amount, currency, rates or table(), **({"as_of": RETRIEVED, "max_rate_age_days": 0} | changes))


def test_divides_currency_per_euro_and_preserves_original_supplier_money():
    result = convert()
    assert result["state"] == "ready"
    assert result["original_money"] == {"amount": "80.00", "currency": "GBP"}
    assert result["eur_reference_estimate"] == {"amount": "100.00", "currency": "EUR", "kind": "ecb_reference_estimate", "rounding": "half_even_to_cent"}
    assert result["reference"]["source_units_per_eur"] == "0.8"
    assert result["reference"]["source_url"] == SOURCE_URL
    assert result["reference"]["rate_date"] == "2030-01-04"
    assert result["reference"]["retrieved_at"] == RETRIEVED.isoformat()
    assert result["operator_budget_allowance"] is None and result["operator_buffer_percent"] is None
    assert result["settlement_guaranteed"] is False


def test_stale_date_is_explicit_and_never_creates_a_budget_or_converted_price():
    sunday = datetime(2030, 1, 6, 18, tzinfo=timezone.utc)
    result = convert(as_of=sunday, max_rate_age_days=1, operator_buffer_percent="5")
    assert result["state"] == "stale_rate" and result["reference"]["age_calendar_days"] == 2
    assert result["eur_reference_estimate"] is result["operator_budget_allowance"] is None
    # Weekend tolerance must be deliberately supplied, never guessed by adapter.
    assert convert(as_of=sunday, max_rate_age_days=2)["state"] == "ready"


def test_rate_age_uses_ecb_calendar_not_caller_timezone_or_retrieval_age():
    # Midnight in Berlin has passed, although the UTC date has not changed.
    result = convert(as_of=datetime(2030, 1, 4, 23, 30, tzinfo=timezone.utc))
    assert result["state"] == "stale_rate" and result["reference"]["age_calendar_days"] == 1
    assert result["reference"]["age_timezone"] == "Europe/Berlin"


def test_missing_currency_does_not_use_another_rate_or_assume_parity():
    result = convert(currency="RON")
    assert result["state"] == "missing_rate"
    assert result["reference"]["source_units_per_eur"] is None
    assert result["eur_reference_estimate"] is result["operator_budget_allowance"] is None


def test_explicit_budget_buffer_is_separate_and_rounded_after_conversion():
    result = convert(amount="1.01", operator_buffer_percent="2.5")
    assert result["eur_reference_estimate"]["amount"] == "1.26"
    assert result["operator_budget_allowance"] == {"amount": "1.30", "currency": "EUR", "kind": "operator_policy_allowance", "buffer_percent": "2.5", "rounding": "ceiling_to_cent", "supplier_fee": False}
    assert result["original_money"]["amount"] == "1.01"
    # An explicit 0% policy still rounds the allowance up, separately from the display.
    assert convert(amount="1.01", operator_buffer_percent="0")["operator_budget_allowance"]["amount"] == "1.27"


def test_decimal_math_is_independent_of_global_context_and_never_rounds_gbp():
    with localcontext() as context:
        context.prec = 4
        result = convert(amount=Decimal("12345.67"))
    assert result["original_money"]["amount"] == "12345.67"
    assert result["eur_reference_estimate"]["amount"] == "15432.09"
    assert convert(amount="0.00")["eur_reference_estimate"]["amount"] == "0.00"
    rates = table(rows='<Cube currency="GBP" rate="0.3"/>')
    with localcontext() as context:
        context.traps[Inexact] = True
        result = convert(amount="1", rates=rates)
    assert result["eur_reference_estimate"]["amount"] == "3.33"


@pytest.mark.parametrize("amount", [80.0, 80, True, "NaN", "Infinity", "-1", "1e100", "1e-100"])
def test_inexact_or_invalid_prices_are_rejected(amount):
    with pytest.raises(ValueError):
        convert(amount=amount)


@pytest.mark.parametrize("changes", [
    {"max_rate_age_days": True}, {"max_rate_age_days": "2"}, {"max_rate_age_days": -1},
    {"operator_buffer_percent": "-1"}, {"operator_buffer_percent": 0.1}, {"operator_buffer_percent": "NaN"},
    {"as_of": datetime(2030, 1, 4, 18)}, {"as_of": datetime(2030, 1, 4, 16, tzinfo=timezone.utc)},
])
def test_ambiguous_policy_or_evaluation_time_is_rejected(changes):
    with pytest.raises(ValueError):
        convert(**changes)


@pytest.mark.parametrize("rows", [
    '', '<Cube currency="GBP" rate="0"/>', '<Cube currency="GBP" rate="-1"/>',
    '<Cube currency="GBP" rate="NaN"/>', '<Cube currency="GBP" rate="1e99"/>',
    '<Cube currency="GBP" rate="0.8"/><Cube currency="GBP" rate="0.9"/>',
    '<Cube currency="EUR" rate="2"/>', '<Cube currency="gbp" rate="0.8"/>',
    '<Cube currency="GBP"/>', '<Cube currency="GBP" rate="0.8"><Cube/></Cube>',
])
def test_invalid_missing_duplicate_or_conflicting_rates_rejected(rows):
    with pytest.raises(ValueError):
        table(rows=rows)


def test_date_namespace_historical_feed_and_xml_entities_are_rejected():
    for payload in (
        xml(day="2030-01-05"), xml(day="2030-02-31"), xml(day=""),
        xml().replace("http://www.ecb.int/vocabulary/2002-08-01/eurofxref", "urn:unexpected"),
        xml().replace('</Cube></Cube>', '</Cube><Cube time="2030-01-03"><Cube currency="GBP" rate="0.9"/></Cube></Cube>'),
        '<!DOCTYPE test [<!ENTITY x "expanded">]>' + xml(),
        xml().replace('rate="0.8"', 'rate="&external;"'),
        'x' * 65_537,
        xml().encode("utf-16"),
    ):
        with pytest.raises(ValueError):
            parse_ecb_daily_xml(payload, retrieved_at=RETRIEVED)


def test_parser_rate_map_is_immutable_and_eur_base_is_exact():
    rates = parse_ecb_daily_xml(xml().encode(), retrieved_at=RETRIEVED)
    assert rates.source_units_per_eur["EUR"] == Decimal("1")
    with pytest.raises(TypeError):
        rates.source_units_per_eur["GBP"] = Decimal("2")
    assert convert(amount="12.34", currency="EUR", rates=rates)["eur_reference_estimate"]["amount"] == "12.34"
