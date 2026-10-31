"""Public consumer-fare boundary checks; HTTP is mocked, never checkout."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.providers.base import ProviderError
from app.providers.bileteria import (
    BileteriaProvider, BileteriaSearchRequest, MAX_BODY, MAX_OPTIONS,
    SEARCH_URL, parse_search_html,
)


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures/bileteria_cra_otp.html"


def request(**changes):
    return BileteriaSearchRequest(**({"origin_airport": "CRA", "destination_airport": "OTP", "travel_date": "2026-11-02", "adults": 2} | changes))


def page():
    return FIXTURE.read_text()


def parsed(text=None):
    return parse_search_html((text if text is not None else page()).encode(), request(), received_at=NOW)


def one_row():
    text = page()
    start = text.index('<div class="result ')
    end = text.index('<div class="result ', start + 1)
    return text[start:end]


def with_rows(rows):
    return page().split('<div class="result ', 1)[0] + "\n".join(rows) + "</body></html>"


async def fetch(text=None, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=text if text is not None else page(), headers={"content-type": "text/html; charset=utf-8"}))) as client:
        return await BileteriaProvider(SimpleNamespace(request_timeout_seconds=1), client=client, now=lambda: NOW).search(request(**kwargs))


@pytest.mark.asyncio
async def test_exact_public_search_facts_and_unknown_capacity_are_preserved():
    calls = []
    def respond(outbound):
        calls.append(outbound)
        assert outbound.method == "GET"
        assert str(outbound.url) == SEARCH_URL + "?zi=2026-11-02&pas=2-0-0-0-0"
        assert "authorization" not in outbound.headers and "x-api-key" not in outbound.headers
        assert not outbound.content
        return httpx.Response(200, text=page(), headers={"content-type": "text/html"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW)
        result = await provider.search(request())
        await provider.aclose()
        assert not client.is_closed and not provider.configured
    assert len(calls) == 1
    assert result["provider"] == "BILETERIA" and result["booking_supported"] is False
    assert result["request"] == request().model_dump(mode="json")
    assert result["received_at"] == NOW.isoformat()
    assert len(result["options"]) == 2
    assert result["rejected_count"] == 1
    assert result["rejection_reasons"] == [{"code": "unsupported_connection", "count": 1}]
    first, overnight = result["options"]
    assert first["operator"] == "Union Business & Travel"
    assert first["price"] == {"amount": "190", "currency": "RON"}
    assert first["price_scope"] == "requested_party" and first["adults"] == 2
    assert first["departure_time"] == "00:15" and first["arrival_time"] == "03:20"
    assert first["departure_date"] == first["arrival_date"] == "2026-11-02"
    assert first["duration_text"] == "3h 05m"
    assert first["departure_stop"]["name"] == "Terminal Plecari/Sosiri"
    assert first["arrival_stop"]["name"] == "Terminal PLECARI/ DEPARTURES"
    assert first["seat_availability"] is first["supplier_valid_until"] is None
    assert first["purchase_action_enabled"] is True
    assert "Se poate călători doar cu rezervare anticipată." in first["notices"]
    assert overnight["departure_time"] == "22:10" and overnight["arrival_time"] == "01:10"
    assert overnight["arrival_date"] == "2026-11-03"
    assert "Checkout" not in str(result) and "capacity" not in first
    assert not any(key in first for key in ("departure_at", "arrival_at", "timezone", "eur_reference_estimate"))


@pytest.mark.parametrize("old,new", [
    ("const zi = '2026-11-02'", "const zi = '2026-11-03'"),
    ('id="adulti" value="2"', 'id="adulti" value="1"'),
    ('id="copii" value="0"', 'id="copii" value="1"'),
    ('id="localitate-sosire-url" value="Otopeni"', 'id="localitate-sosire-url" value="Bucuresti"'),
    ('id="localitate-plecare" value="Aeroport Craiova"', 'id="localitate-plecare" value="Craiova"'),
])
@pytest.mark.asyncio
async def test_mismatched_page_context_fails_closed(old, new):
    assert old in page()
    with pytest.raises(ProviderError) as failure:
        await fetch(page().replace(old, new))
    assert failure.value.code == "invalid_response"


@pytest.mark.parametrize("change", [
    lambda s: s.replace('value="190"', 'value=""'),
    lambda s: s.replace('content="190"', 'content="95"'),
    lambda s: s.replace('content="RON"', 'content="EUR"'),
    lambda s: s.replace('>190<sup>', '>95<sup>'),
    lambda s: s.replace('pas=2-0-0-0-0', 'pas=1-0-0-0-0'),
    lambda s: s.replace('dtPlecare=2026-11-02', 'dtPlecare=2026-11-01'),
    lambda s: s.replace('departureTime">00:15', 'departureTime">25:15'),
    lambda s: s.replace('3h 05m', '3h 06m'),
    lambda s: s.replace('arrivalTime">03:20', 'arrivalTime">00:05'),
    lambda s: s.replace('https://bileteria.ro/AeroportCraiova/', 'https://evil.invalid/AeroportCraiova/'),
    lambda s: s.replace('https://bileteria.ro/AeroportCraiova/', 'https://bileteria.ro@evil.invalid/AeroportCraiova/'),
    lambda s: s.replace('https://bileteria.ro/AeroportCraiova/', 'javascript:alert(1)/AeroportCraiova/'),
    lambda s: s.replace('Autogara35196', 'Autogara123'),
    lambda s: s.replace(' /Checkout?', ' https://evil.invalid/Checkout?'),
    lambda s: s.replace(' /Checkout?', ' //bileteria.ro/Checkout?'),
    lambda s: s.replace('cursa=281450', 'cursa=281450&amp;cursa=999'),
    lambda s: s.replace("&#x27;00:15&#x27;)", "&#x27;00:15&#x27;); fetch(&#x27;https://evil.invalid&#x27;)"),
    lambda s: s.replace("&#x27;00:15&#x27;)", "&#x27;01:15&#x27;)"),
    lambda s: s.replace('class="traseuInfo"', 'class="unrecognized_leg"'),
])
def test_missing_conflicting_or_unscoped_row_never_becomes_a_fare(change):
    changed = change(one_row())
    assert changed != one_row()
    result = parsed(with_rows([changed]))
    assert result["options"] == [] and result["rejected_count"] == 1


def test_decimal_fare_is_not_binary_rounded_or_multiplied_again():
    row = one_row().replace('value="190"', 'value="190.25"').replace('content="190"', 'content="190.25"').replace('>190<sup>', '>190,25<sup>')
    option = parsed(with_rows([row]))["options"][0]
    assert option["price"]["amount"] == "190.25" and option["adults"] == 2


@pytest.mark.parametrize("attribute", ['disabled', 'aria-disabled="true"', 'class="disabled"'])
def test_disabled_buy_action_never_means_available_seats(attribute):
    row = one_row()
    if attribute.startswith("class"):
        row = row.replace('class="btn btn-primary buy"', 'class="btn btn-primary buy disabled"')
    else:
        row = row.replace('<button ', '<button ' + attribute + ' ')
    option = parsed(with_rows([row]))["options"][0]
    assert option["purchase_action_enabled"] is False
    assert option["seat_availability"] is None


def test_overnight_needs_consistent_visible_day_and_duration():
    result = parsed(page().replace('+1 zi', ''))
    assert len(result["options"]) == 1 and result["options"][0]["departure_time"] == "00:15"
    assert result["rejected_count"] == 2


@pytest.mark.parametrize("day", ["2026-03-29", "2026-10-25"])
def test_dst_wall_clocks_remain_unverified_source_text_never_utc_instants(day):
    # 03:15 is nonexistent/repeated under the inferred Romanian DST rules.
    # The source supplies neither that zone nor an offset. Retain its raw
    # claim only, without choosing a fold, inventing UTC or asserting elapsed
    # travel time/capacity. Such references never enter planning resources.
    row = one_row().replace('orap="001500"', 'orap="031500"')
    row = row.replace('departureTime">00:15', 'departureTime">03:15')
    row = row.replace('arrivalTime">03:20', 'arrivalTime">06:20')
    row = row.replace('&#x27;00:15&#x27;)', '&#x27;03:15&#x27;)')
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    html = with_rows([row]).replace('2026-11-03', next_day).replace('2026-11-02', day)
    result = parse_search_html(html.encode(), request(travel_date=day), received_at=NOW)
    option = result["options"][0]
    assert option["departure_date"] == day and option["departure_time"] == "03:15"
    assert not any(field in option for field in ("departure_at", "arrival_at", "timezone", "duration_seconds"))
    assert any("does not supply UTC offsets" in notice for notice in result["notices"])
    assert option["seat_availability"] is None


def test_identical_duplicate_deduplicates_but_conflicting_trip_fails_closed():
    row = one_row()
    result = parsed(with_rows([row, row]))
    assert len(result["options"]) == 1 and result["rejected_count"] == 1
    changed = row.replace('value="190"', 'value="195"').replace('content="190"', 'content="195"').replace('>190<sup>', '>195<sup>')
    with pytest.raises(ValueError, match="Conflicting repeated trip"):
        parsed(with_rows([row, changed]))


def test_option_limit_is_explicit_not_an_inventory_total():
    rows = [one_row().replace('cursa=281450', f'cursa={300000 + i}') for i in range(MAX_OPTIONS + 2)]
    result = parsed(with_rows(rows))
    assert len(result["options"]) == MAX_OPTIONS
    assert result["rejection_reasons"] == [{"code": "result_limit", "count": 2}]


def test_stable_fact_identity_excludes_receipt_time_but_includes_price():
    before = parsed(with_rows([one_row()]))
    later = parse_search_html(with_rows([one_row()]).encode(), request(), received_at=NOW + timedelta(minutes=1))
    assert before["options"][0]["id"] == later["options"][0]["id"]
    row = one_row().replace('value="190"', 'value="195"').replace('content="190"', 'content="195"').replace('>190<sup>', '>195<sup>')
    assert before["options"][0]["id"] != parsed(with_rows([row]))["options"][0]["id"]


@pytest.mark.parametrize("text", [
    "<html>Service unavailable</html>",
    "x" * (MAX_BODY + 1),
    "<div>" * 70 + "</div>" * 70,
    "<span></span>" * 20001,
    '<input id="adulti" id="adulti" value="2">',
    page() + "\x00",
    page().replace('</head>', "<script>const zi = '2026-11-02';</script></head>"),
    page().replace('</body>', '<input id="adulti" value="2"></body>'),
])
def test_unrecognized_or_unbounded_structure_is_not_successful_empty_inventory(text):
    with pytest.raises(ValueError):
        parsed(text)


@pytest.mark.parametrize("changes", [
    {"origin_airport": "SBZ"}, {"destination_airport": "CRA"}, {"adults": True}, {"adults": "2"},
    {"adults": 0}, {"adults": 9}, {"travel_date": 1793577600}, {"travel_date": True},
    {"travel_date": "2026-11-02T00:00:00Z"}, {"travel_date": NOW}, {"travel_date": "2026-02-30"},
    {"extra": "ignored"},
])
def test_request_is_exact_supported_adult_only_calendar_search(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("day", ["2026-10-31", "2026-12-03"])
@pytest.mark.asyncio
async def test_out_of_horizon_dates_do_not_dispatch(day):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        provider = BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW)
        with pytest.raises(ProviderError) as failure:
            await provider.search(request(travel_date=day))
    assert failure.value.code == "invalid_request" and calls == []


def test_horizon_uses_explicit_bucharest_day_not_utc_midnight():
    provider = BileteriaProvider(SimpleNamespace(), client=object(), now=lambda: datetime(2026, 10, 31, 23, tzinfo=timezone.utc))
    assert provider.validate_request(request(travel_date=date(2026, 11, 1))).travel_date == date(2026, 11, 1)
    assert provider.validate_request(request(travel_date=date(2026, 12, 2))).travel_date == date(2026, 12, 2)
    with pytest.raises(ProviderError):
        provider.validate_request(request(travel_date="2026-10-31"))


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
@pytest.mark.asyncio
async def test_http_failures_do_not_retry_follow_redirect_or_echo_payload(status):
    calls = []
    def respond(outbound):
        calls.append(outbound)
        return httpx.Response(status, text="private-upstream-marker", headers={"location": "https://evil.invalid/Checkout"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(ProviderError) as failure:
            await BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW).search(request())
    assert len(calls) == 1 and failure.value.code == f"http_{status}"
    assert "private-upstream-marker" not in str(failure.value) and "evil.invalid" not in str(failure.value)


@pytest.mark.parametrize("headers", [
    {"content-type": "application/json"},
    {"content-type": "text/html", "content-length": str(MAX_BODY + 1)},
    {"content-type": "text/html", "content-length": "unknown"},
])
@pytest.mark.asyncio
async def test_response_contract_is_bounded_before_parsing(headers):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=page().encode(), headers=headers))) as client:
        with pytest.raises(ProviderError) as failure:
            await BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW).search(request())
    assert failure.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_chunked_response_is_bounded_without_content_length():
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 500001
            yield b"y" * 500001
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/html"}, stream=Chunks()))) as client:
        with pytest.raises(ProviderError) as failure:
            await BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW).search(request())
    assert failure.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_total_deadline_covers_slow_response_stream():
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<html>"
            await asyncio.sleep(2)
            yield b"</html>"
    calls = []
    def respond(outbound):
        calls.append(outbound)
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=Slow())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError) as failure:
            await BileteriaProvider(SimpleNamespace(request_timeout_seconds=1), client=client, now=lambda: NOW).search(request())
    assert failure.value.code == "timeout" and len(calls) == 1


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection")])
@pytest.mark.asyncio
async def test_transport_errors_are_sanitized_without_retry(error, code):
    calls = []
    def respond(outbound):
        calls.append(outbound)
        raise error("private-upstream-marker", request=outbound)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError) as failure:
            await BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW).search(request())
    assert len(calls) == 1 and failure.value.code == code
    assert "private-upstream-marker" not in str(failure.value)


@pytest.mark.asyncio
async def test_closed_provider_never_requests_or_recreates_client():
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        provider = BileteriaProvider(SimpleNamespace(), client=client, now=lambda: NOW)
        await provider.aclose()
        with pytest.raises(ProviderError) as failure:
            await provider.search(request())
    assert failure.value.code == "not_configured" and not calls
