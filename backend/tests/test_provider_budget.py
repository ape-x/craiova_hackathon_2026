import asyncio
from types import SimpleNamespace
import httpx
import pytest
from app.provider_budget import consume_call, limit_calls
from app.providers.base import HTTPProvider, ProviderError


@pytest.mark.asyncio
async def test_fanout_and_threads_share_the_exact_remaining_allowance():
    with limit_calls(3) as budget:
        results = await asyncio.gather(*(asyncio.to_thread(consume_call, 'test') for _ in range(8)), return_exceptions=True)
        assert sum(result is None for result in results) == 3
        assert sum(isinstance(result, ProviderError) for result in results) == 5
        assert budget.used == 3
    consume_call('outside-command')


@pytest.mark.asyncio
async def test_blocked_request_never_reaches_transport_and_failure_still_costs_one_call():
    dispatched = []
    def handler(request):
        dispatched.append(request.url.path)
        return httpx.Response(503, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HTTPProvider(SimpleNamespace(), client=client)
        with limit_calls(1) as budget:
            with pytest.raises(ProviderError, match='HTTP 503'):
                await provider.request('GET', 'https://example.test/first', provider='test')
            with pytest.raises(ProviderError) as error:
                await provider.request('GET', 'https://example.test/second', provider='test')
            assert error.value.code == 'command_budget_exhausted'
            assert budget.used == 1
        assert dispatched == ['/first']


@pytest.mark.asyncio
async def test_independent_concurrent_commands_do_not_share_allowances():
    async def command(count):
        with limit_calls(count) as budget:
            await asyncio.sleep(0)
            for _ in range(count):
                consume_call('test')
            return budget.used
    assert await asyncio.gather(command(1), command(4)) == [1, 4]
