"""Provider schema/reference checks, not a substitute for live-model evaluation."""
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from app.providers.ai import AIProvider
from app.providers.base import ProviderError
from app.seed import seed_world


def weather(**overrides):
    return {"condition": "storm", "visibility_m": 900, "wind_kts": 35.0,
            "landing_allowed": False, "departure_allowed": True, **overrides}


def command(events, focus=None):
    return {"events": events, "strategies": [], "explanation": "Condiții demonstrative pentru scenariul solicitat.", "focus_id": focus}


async def interpret(value, world=None, inspect=None):
    world = world or seed_world().model_dump(mode="json")
    before = copy.deepcopy(world)
    def handler(request):
        if inspect:
            inspect(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AIProvider(SimpleNamespace(openai_api_key="fixture", request_timeout_seconds=1), client=client).interpret("Scenariu nou de la zero: furtună la Lisabona, alege aeroportul de deviere", world)
    assert world == before
    return result


@pytest.mark.asyncio
async def test_reset_clears_references_before_recreating_a_known_airport_and_weather():
    result = await interpret(command([
        {"type": "scenario_reset", "target_id": None, "value": "empty"},
        {"type": "airport_added", "target_id": "CRA", "value": None},
        {"type": "airport_added", "target_id": "LPPT", "value": None},
        {"type": "airport_weather", "target_id": "LPPT", "value": weather()},
    ], "LIS"))
    assert result["events"][2]["target_id"] == result["events"][3]["target_id"] == "LIS"


@pytest.mark.parametrize("events", [
    [{"type": "scenario_reset", "target_id": None, "value": "empty"}, {"type": "resource_unavailable", "target_id": "B12", "value": None}],
    [{"type": "airport_closed", "target_id": "CRA", "value": None}, {"type": "scenario_reset", "target_id": None, "value": "empty"}],
    [{"type": "scenario_reset", "target_id": "CRA", "value": "empty"}],
    [{"type": "scenario_reset", "target_id": None, "value": "other"}],
    [{"type": "scenario_reset", "target_id": None, "value": "empty"}, {"type": "scenario_reset", "target_id": None, "value": "seed"}],
])
@pytest.mark.asyncio
async def test_invalid_reset_or_reference_to_erased_inventory_is_rejected(events):
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(command(events))


@pytest.mark.asyncio
async def test_seed_reset_uses_seed_references_even_when_current_inventory_is_empty():
    world = seed_world().model_dump(mode="json")
    world.update(airports=[], resources=[], groups=[])
    result = await interpret(command([
        {"type": "scenario_reset", "target_id": None, "value": "seed"},
        {"type": "resource_unavailable", "target_id": "B12", "value": None},
    ], "B12"), world)
    assert result["events"][1]["target_id"] == "B12"


@pytest.mark.asyncio
async def test_seed_reset_does_not_keep_custom_airport_references():
    world = seed_world().model_dump(mode="json")
    world["airports"].append({"id": "LIS", "available": True})
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(command([
            {"type": "scenario_reset", "target_id": None, "value": "seed"},
            {"type": "airport_weather", "target_id": "LIS", "value": weather()},
        ]), world)


@pytest.mark.asyncio
async def test_live_inventory_cannot_be_reset_by_generated_command():
    world = seed_world().model_dump(mode="json")
    world["mode"] = "live"
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(command([{"type": "scenario_reset", "target_id": None, "value": "empty"}]), world)


@pytest.mark.asyncio
async def test_weather_projection_blocks_diversion_until_weather_is_cleared():
    events = [
        {"type": "airport_weather", "target_id": "OTP", "value": weather()},
        {"type": "airport_restored", "target_id": "OTP", "value": None},
        {"type": "flight_diverted", "target_id": "F201", "value": "OTP"},
    ]
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(command(events))
    events.insert(2, {"type": "airport_weather", "target_id": "OTP", "value": weather(condition="clear", visibility_m=None, wind_kts=None, landing_allowed=True)})
    assert (await interpret(command(events)))["events"][-1]["value"] == "OTP"


@pytest.mark.parametrize("payload", [weather(visibility_m=-1), weather(wind_kts=251), weather(landing_allowed="false"), {"condition": "fog"}])
@pytest.mark.asyncio
async def test_weather_requires_explicit_typed_operational_constraints(payload):
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(command([{"type": "airport_weather", "target_id": "CRA", "value": payload}]))


@pytest.mark.asyncio
async def test_model_receives_weather_and_grounded_nearby_airports_without_operational_claims():
    world = seed_world().model_dump(mode="json")
    world["airports"][0]["weather"] = weather()
    def inspect(body):
        data = json.loads(body["input"])
        assert data["world"]["airports"][0]["weather"] == weather()
        options = next(item["options"] for item in data["nearby_airport_options"] if item["airport_id"] == "LIS")
        assert {"OPO", "FAO"} <= {option["id"] for option in options}
        assert all("available" not in option and "landing_allowed" not in option for option in options)
        assert len(data["nearby_airport_options"]) <= 4
        assert "scenario_reset" in body["instructions"] and "airport_weather" in body["instructions"]
    await interpret(command([]), world, inspect)


@pytest.mark.asyncio
async def test_decision_evidence_includes_airport_weather_constraints():
    world = seed_world().model_dump(mode="json")
    world["airports"][0]["weather"] = weather()
    candidates = [{"id": "valid-option", "validation": {"valid": True}, "recovered_passengers": 10, "total_cost": 100, "exceptions": []}]
    def handler(request):
        body = json.loads(request.content)
        assert body["input"][0]["content"][0]["data"]["world"]["airports"][0]["weather"] == weather()
        return httpx.Response(200, json={"answers": [{"type": "choice", "name": "approve_recovery_plan", "choice": True}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AIProvider(SimpleNamespace(openai_api_key="fixture", request_timeout_seconds=1), client=client).choose(world, candidates, "Alege devierea")
    assert result["plan_id"] == "valid-option"
