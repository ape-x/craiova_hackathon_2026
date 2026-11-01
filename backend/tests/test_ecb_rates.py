"""All ECB and APL requests below use authored fixtures and mock transports."""
import asyncio
import copy
from datetime import timedelta
import json
from types import SimpleNamespace

import httpx
import pytest

from app.ground_transfers import GroundTransfers
from app.hotel_recovery import _rates as recovery_rates
from app.providers.apl import APLQuoteRequest
from app.providers.base import ProviderError
from app.providers.ecb_rates import EcbRates
from app.providers.reference_fx import MAX_XML_BYTES, SOURCE_URL
from test_ground_transfers import query
from test_reference_fx import RETRIEVED, xml


def quote():
    return {"provider": "Airport Pickups London", "expires_at": None, "source_quoted_at": None,
        "received_at": RETRIEVED.isoformat(), "request": query(),
        "options": [{"id": "fixture-car", "price": {"amount": "80.00", "currency": "GBP"},
                     "price_scope": "per_vehicle", "max_passengers": 3, "max_suitcases": 3, "fits_request": True}]}


@pytest.mark.asyncio
async def test_ecb_estimate_is_optional_exact_sourced_and_never_a_supplier_eur_charge():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and str(request.url) == SOURCE_URL
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        return httpx.Response(200, content=xml())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        original = quote()
        before = copy.deepcopy(original)
        result = fx.enrich(original, await fx.rates())
        assert original == before and len(calls) == 1
        assert result["options"][0]["price"] == {"amount": "80.00", "currency": "GBP"}
        assert result["options"][0]["eur_reference_estimate"] == {"amount": "100.00", "currency": "EUR",
            "kind": "ecb_reference_estimate", "rounding": "half_even_to_cent"}
        assert result["fx"]["state"] == "ready" and result["fx"]["cache"] == "fresh"
        assert result["fx"]["reference"]["source_units_per_eur"] == "0.8"
        assert result["fx"]["reference"]["rate_date"] == "2030-01-04" and result["fx"]["reference"]["max_age_days"] == 3
        assert result["fx"]["settlement_guaranteed"] is False and result["fx"]["revalidation_required"] is True
        assert result["expires_at"] is None and result["source_quoted_at"] is None
        assert "budget_total_cost" not in json.dumps(result) and "operator_budget_allowance" not in json.dumps(result)
        await fx.aclose()
        assert not client.is_closed  # Injected transport remains caller-owned.


@pytest.mark.asyncio
async def test_concurrent_consumers_share_one_bounded_fetch_and_cache_expires():
    calls, clock = [], [0.0]
    started, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        calls.append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, content=xml())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED, monotonic=lambda: clock[0])
        tasks = [asyncio.create_task(fx.rates()) for _ in range(8)]
        await started.wait()
        assert len(calls) == 1
        release.set()
        results = await asyncio.gather(*tasks)
        assert len(calls) == 1 and [item["cache"] for item in results].count("fresh") == 1
        assert sum(item["cache"] == "cached" for item in results) == 7
        clock[0] = 901
        assert (await fx.rates())["cache"] == "fresh" and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(503, text="do-not-echo-server-detail"),
    httpx.Response(302, headers={"Location": "https://untrusted.invalid/rates"}),
    httpx.Response(200, text="<html>not a reference feed</html>"),
    httpx.Response(200, content=xml(day="2030-01-05")),
    httpx.Response(200, headers={"Content-Length": str(MAX_XML_BYTES + 1)}, content=xml()),
    httpx.Response(200, content=b"x" * (MAX_XML_BYTES + 1)),
    httpx.Response(200, content='<!DOCTYPE rates [<!ENTITY x "unsafe">]>' + xml()),
])
async def test_bad_feeds_never_redirect_retry_fabricate_zero_or_hide_gbp(response):
    calls, clock = [], [0.0]
    def handler(request):
        calls.append(request)
        return response
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED, monotonic=lambda: clock[0])
        result = fx.enrich(quote(), await fx.rates())
        assert len(calls) == 1 and result["fx"]["state"] == "unavailable"
        assert result["options"][0]["price"]["amount"] == "80.00" and result["options"][0]["eur_reference_estimate"] is None
        assert result["fx"]["reference"] is None and "do-not-echo" not in json.dumps(result)
        assert (await fx.rates())["cache"] == "cached" and len(calls) == 1
        clock[0] = 31
        await fx.rates()
        assert len(calls) == 2  # One explicit later refresh; no retry of a request.


@pytest.mark.asyncio
async def test_total_timeout_includes_streaming_and_is_negatively_cached():
    closed, calls = [], []
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<"
            await asyncio.sleep(10)
        async def aclose(self):
            closed.append(True)
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=SlowStream())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        fx.TIMEOUT_SECONDS = .01
        result = fx.enrich(quote(), await fx.rates())
        assert result["fx"]["code"] == "fx_timeout" and closed == [True]
        assert (await fx.rates())["cache"] == "cached" and len(calls) == 1


@pytest.mark.asyncio
async def test_single_slow_reference_fetch_can_finish_after_previous_three_second_deadline():
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(3.05)
        return httpx.Response(200, content=xml())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        assert fx.TIMEOUT_SECONDS == 10
        result = await recovery_rates(fx)
        assert result["table"]["source_units_per_eur"]["GBP"] == "0.8"
        assert result["table"]["retrieved_at"] == RETRIEVED.isoformat()
        assert len(calls) == 1
        assert (await recovery_rates(fx))["cache"] == "cached" and len(calls) == 1


@pytest.mark.asyncio
async def test_shorter_enclosing_job_deadline_cancels_fetch_without_cache_or_retry():
    calls, closed = [], []
    class PendingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<"
            await asyncio.Event().wait()
        async def aclose(self):
            closed.append(True)
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=PendingStream())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(.01):
                await recovery_rates(fx)
        assert len(calls) == 1 and closed == [True]
        assert fx._cached is None and not fx._gate.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code,reason", [
    (httpx.ReadTimeout("private upstream diagnostic"), "timeout", "fx_timeout"),
    (httpx.ConnectError("private upstream diagnostic"), "connection", "fx_transport_error"),
    (httpx.Response(503, text="private upstream diagnostic"), "fx_http_error", "fx_http_error"),
    (httpx.Response(200, text="private upstream diagnostic"), "fx_invalid_response", "fx_invalid_response"),
])
async def test_required_fx_failure_retains_safe_cause_and_never_retries(failure, code, reason):
    calls = []
    def handler(request):
        calls.append(request)
        if isinstance(failure, Exception):
            raise failure
        return failure
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        for _ in range(2):
            with pytest.raises(ProviderError) as error:
                await recovery_rates(fx)
            assert error.value.code == code and reason in str(error.value)
            assert "private upstream diagnostic" not in str(error.value)
        assert len(calls) == 1 and fx._cached["table"] is None


@pytest.mark.asyncio
async def test_unrecognized_reference_failure_code_is_not_echoed_as_diagnostic():
    class Unavailable:
        async def rates(self):
            return {"table": None, "code": "private upstream diagnostic"}
    with pytest.raises(ProviderError) as error:
        await recovery_rates(Unavailable())
    assert error.value.code == "fx_unavailable"
    assert "private upstream diagnostic" not in str(error.value)


@pytest.mark.asyncio
async def test_stale_and_missing_gbp_references_keep_dated_evidence_but_no_estimate():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=xml()))) as client:
        now = [RETRIEVED]
        fx = EcbRates(client=client, now=lambda: now[0])
        rates = await fx.rates()
        now[0] += timedelta(days=3)
        assert fx.enrich(quote(), rates)["fx"]["state"] == "ready"
        now[0] += timedelta(days=1)
        result = fx.enrich(quote(), rates)
        assert result["fx"]["state"] == "stale_rate" and result["fx"]["reference"]["age_calendar_days"] == 4
        assert result["options"][0]["eur_reference_estimate"] is None and "app's" in result["fx"]["message"]
    missing_gbp = xml(rows='<Cube currency="USD" rate="1.25"/>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=missing_gbp))) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        result = fx.enrich(quote(), await fx.rates())
        assert result["fx"]["state"] == "missing_rate" and result["fx"]["reference"]["source_units_per_eur"] is None
        assert result["options"][0]["eur_reference_estimate"] is None


@pytest.mark.asyncio
async def test_failed_refresh_does_not_silently_reuse_old_reference():
    responses = iter([httpx.Response(200, content=xml()), httpx.Response(503)])
    clock = [0.0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses))) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED, monotonic=lambda: clock[0])
        assert fx.enrich(quote(), await fx.rates())["fx"]["state"] == "ready"
        clock[0] = 901
        result = fx.enrich(quote(), await fx.rates())
        assert result["fx"]["state"] == "unavailable" and result["options"][0]["eur_reference_estimate"] is None


@pytest.mark.asyncio
async def test_ecb_and_apl_run_concurrently_and_successful_gbp_survives_fx_failure():
    apl_started, fx_started = asyncio.Event(), asyncio.Event()
    async def apl(request):
        apl_started.set()
        await fx_started.wait()
        return quote()
    async def feed(request):
        fx_started.set()
        await apl_started.wait()
        return httpx.Response(503)
    closed = []
    async def close():
        closed.append(True)
    provider = SimpleNamespace(configured=True, quote=apl, aclose=close)
    async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as client:
        service = GroundTransfers(SimpleNamespace(request_timeout_seconds=1), provider=provider, fx=EcbRates(client=client, now=lambda: RETRIEVED))
        result = await asyncio.wait_for(service.quote(APLQuoteRequest(**query())), 1)
        assert result["options"][0]["price"] == {"amount": "80.00", "currency": "GBP"}
        assert result["fx"]["state"] == "unavailable" and result["fx"]["code"] == "fx_http_error"
        await service.aclose()
        assert closed == [True] and (await service.fx.rates())["code"] == "fx_closed"


@pytest.mark.asyncio
async def test_supplier_failure_cancels_and_drains_reference_fetch():
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def feed(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def apl(request):
        await entered.wait()
        raise ProviderError("Fixture APL failure", code="http_503")
    async def close():
        pass
    async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as client:
        fx = EcbRates(client=client, now=lambda: RETRIEVED)
        service = GroundTransfers(SimpleNamespace(request_timeout_seconds=1), provider=SimpleNamespace(configured=True, quote=apl, aclose=close), fx=fx)
        with pytest.raises(ProviderError):
            await service.quote(APLQuoteRequest(**query()))
        assert cancelled.is_set() and not fx._gate.locked()
        await service.aclose()
