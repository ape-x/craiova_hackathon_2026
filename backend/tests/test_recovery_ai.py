import json
from types import SimpleNamespace

import httpx
import pytest

from app.providers.ai import AIProvider
from app.providers.base import ProviderError


def response(**changes):
    return {"query_ids":["query-known"],"strategies":[{"id":"balanced","label":"Balanced","objective":"balanced","preferred_airport":None,
        "weights":{"recovery":1,"arrival":1,"cost":1,"stability":1}}],"explanation":"Search the allowed dated route.",**changes}


@pytest.mark.asyncio
async def test_query_planning_uses_configured_sol_and_strict_grounded_schema():
    def handler(request):
        body=json.loads(request.content)
        assert request.url=="https://api.openai.com/v1/responses"
        assert body["model"]=="gpt-6.1-sol" and body["service_tier"]=="ultrafast" and body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert "PRIVATE-TOKEN" not in body["input"] and "RAW-OBSERVATION" not in body["input"]
        return httpx.Response(200,json={"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":json.dumps(response())}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider=AIProvider(SimpleNamespace(openai_api_key="fixture-key"),client=client)
        result=await provider.plan_recovery_queries({"mode":"live","groups":[{"id":"G1","token":"PRIVATE-TOKEN","members":["PRIVATE-TOKEN"]}],"observations":[{"id":"RAW-OBSERVATION"}]},[{"id":"query-known"}],{"max_search_calls":1})
    assert result["query_ids"]==["query-known"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes",[{"query_ids":["invented"]},{"query_ids":["query-known","query-known"]},{"query_ids":["query-known","query-two"]},
    {"strategies":[{"id":"bad","label":"Bad","objective":"fastest","preferred_airport":"OTP","weights":{"recovery":1,"arrival":1,"cost":1,"stability":1}}]}])
async def test_ai_cannot_expand_search_allowance_or_invent_airport_choices(changes):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":json.dumps(response(**changes))}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider=AIProvider(SimpleNamespace(openai_api_key="fixture-key"),client=client)
        with pytest.raises(ProviderError) as failure:
            await provider.plan_recovery_queries({"mode":"live"},[{"id":"query-known"},{"id":"query-two"}],{"max_search_calls":1})
    assert failure.value.code=="invalid_response" and len(calls)==1
