"""Free-form authoring contracts, using HTTP fixtures (no provider calls)."""
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from app.providers.ai import AIProvider, Interpretation
from app.providers.base import ProviderError
from app.models import ScenarioChange
from app.seed import seed_world
from app.service import apply_change


def flight():
    return {"kind": "flight", "name": "Charter Lisabona", "origin": "CRA", "destination": "LIS", "departure": 800, "arrival": 1000, "capacity": 36, "accessible": True, "cost_per_person": 120.0, "room_capacity": 1, "vehicle_id": None, "available": True}


def passengers():
    return {"name": "Grup Lisabona", "passenger_count": 12, "group_size": 3, "origin": "CRA", "destination": "LIS", "ready_at": 600, "deadline": 1200, "accessible": False, "budget_per_person": 200.0, "original_flight_id": None, "priority": 2}


def authored():
    return {"events": [
        {"type": "airport_added", "target_id": "LPPT", "value": None},
        {"type": "resource_added", "target_id": "CH901", "value": flight()},
        {"type": "passengers_added", "target_id": None, "value": passengers()},
        {"type": "resource_capacity", "target_id": "CH901", "value": 42},
    ], "strategies": [{"id": "fast", "label": "Rapid", "objective": "fastest", "preferred_airport": "LIS", "weights": {"recovery": 10, "arrival": 5, "cost": 2, "stability": 1}}], "explanation": "Propun cursa și pasagerii pentru Lisabona.", "focus_id": "LIS"}


async def interpret(value, inspect=None, *, text="Creează scenariul la Lisabona", context=""):
    world = seed_world().model_dump(mode="json")
    original = copy.deepcopy(world)
    def handler(request):
        if inspect:
            inspect(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "model": "fixture", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AIProvider(SimpleNamespace(openai_api_key="fixture", request_timeout_seconds=1), client=client).interpret(text, world, context)
    assert world == original
    return result


@pytest.mark.asyncio
async def test_compound_creation_resolves_new_entities_and_catalog_icao_alias():
    result = await interpret(authored())
    assert result["events"][0]["target_id"] == "LIS"
    assert result["events"][1]["value"]["destination"] == "LIS"
    assert result["events"][2]["target_id"] is None
    assert result["events"][3]["target_id"] == "CH901"


@pytest.mark.asyncio
async def test_followup_context_separate_from_new_request_and_contains_real_airport_reference():
    context = 'User: Creează un charter spre Lisabona. Backend: Ce oră și capacitate?'
    def inspect(body):
        data = json.loads(body["input"])
        assert data["current_request"] == "36 de locuri, la 13:20"
        assert data["conversation_context"] == context
        lis = next(a for a in data["airport_catalog_matches"] if a["id"] == "LIS")
        assert lis["lat"] == pytest.approx(38.7813)
        assert "available" not in lis
        assert all("token" not in g and "members" not in g for g in data["world"]["groups"])
    await interpret(authored(), inspect, text="36 de locuri, la 13:20", context=context)


@pytest.mark.parametrize("mutation", [
    lambda obj: obj["events"].reverse(),
    lambda obj: obj["events"][1].update(target_id="B12"),
    lambda obj: obj["events"][2].update(target_id="G999"),
    lambda obj: obj["events"][1]["value"].update(source="Flightradar24"),
    lambda obj: obj["events"][1]["value"].update(capacity=12.5),
    lambda obj: obj["events"][2]["value"].update(original_flight_id="UNKNOWN"),
    lambda obj: obj["events"][1]["value"].update(destination="UNKNOWN"),
    lambda obj: obj["events"][0].update(target_id="UNKNOWN"),
])
@pytest.mark.asyncio
async def test_invalid_or_out_of_order_authoring_rejected(mutation):
    obj = authored()
    mutation(obj)
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(obj)


@pytest.mark.asyncio
async def test_airport_reopened_then_diverted_is_checked_against_projected_state():
    obj = authored()
    obj["events"] = [
        {"type": "airport_closed", "target_id": "OTP", "value": None},
        {"type": "airport_restored", "target_id": "OTP", "value": None},
        {"type": "flight_diverted", "target_id": "F201", "value": "OTP"},
    ]
    obj["strategies"] = []
    obj["focus_id"] = "F201"
    assert len((await interpret(obj))["events"]) == 3
    obj["events"].pop(1)
    with pytest.raises(ProviderError):
        await interpret(obj)


@pytest.mark.asyncio
@pytest.mark.parametrize(("target", "value", "expected"), [
    ("F201", {"departure": None, "arrival": 780}, (600, 780)),
    ("F110", {"departure": 900, "arrival": None}, (900, 1080)),
    ("F110", {"departure": 900, "arrival": 1110}, (900, 1110)),
])
async def test_schedule_patch_survives_structured_output_and_applies(target, value, expected):
    response = {"events": [{"type": "resource_schedule", "target_id": target, "value": value}],
                "strategies": [], "explanation": "Actualizare orar.", "focus_id": target}
    parsed = await interpret(response, text="Întârzie zborul cu 30 de minute")
    assert parsed["events"][0]["value"] == value
    world = seed_world()
    apply_change(world, ScenarioChange.model_validate(parsed["events"][0]))
    resource = next(resource for resource in world.resources if resource.id == target)
    assert (resource.departure, resource.arrival) == expected


@pytest.mark.asyncio
async def test_empty_schedule_patch_from_model_is_rejected():
    response = {"events": [{"type": "resource_schedule", "target_id": "F201",
                             "value": {"departure": None, "arrival": None}}],
                "strategies": [], "explanation": "Actualizare orar.", "focus_id": "F201"}
    with pytest.raises(ProviderError, match="invalid command"):
        await interpret(response)


def test_structured_output_contract_requires_every_object_property():
    schema = Interpretation.model_json_schema()
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)
    assert schema["properties"]["events"]["maxItems"] == 64
