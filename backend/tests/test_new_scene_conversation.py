"""Conversation plumbing with real adapters and offline provider responses.

These verify context, non-mutation during clarification, and atomic completion;
they do not claim that fixture output evaluates a model's language behavior.
"""
import json

import httpx
import pytest

from app.config import Settings
from app.models import CommandRequest
from app.providers.ai import AIProvider, LIVE_COMMAND_PROMPT, SYSTEM_PROMPT
from app.providers.base import ProviderError
from app.seed import seed_world
from app.service import RecoveryService
from app.store import Store


FIRST_REQUEST = "Creează un scenariu nou la Lisabona în care se strică autobuzul de transfer."
ESSENTIALS = "Sunt 12 pasageri care trebuie să ajungă la Porto."
DEFAULTS_QUESTION = "Ai ore, capacități și buget de folosit sau le completez eu cu valori demonstrative?"


def clarification(text):
    return {"events": [], "strategies": [], "focus_id": None, "explanation": text}


def authored_scene():
    def bus(name, departure, arrival, cost):
        return {"kind": "bus", "name": name, "origin": "LIS", "destination": "OPO",
                "departure": departure, "arrival": arrival, "capacity": 12,
                "accessible": True, "cost_per_person": cost, "room_capacity": 1,
                "vehicle_id": None, "available": True}
    return {
        "events": [
            {"type": "scenario_reset", "target_id": None, "value": "empty"},
            {"type": "airport_added", "target_id": "LIS", "value": None},
            {"type": "airport_added", "target_id": "OPO", "value": None},
            {"type": "resource_added", "target_id": "BROKEN", "value": bus("Autocar defect", 630, 870, 40.0)},
            {"type": "resource_added", "target_id": "BACKUP", "value": bus("Autocar de rezervă", 660, 900, 50.0)},
            {"type": "passengers_added", "target_id": None, "value": {
                "name": "Pasageri Lisabona", "passenger_count": 12, "group_size": 3,
                "origin": "LIS", "destination": "OPO", "ready_at": 600, "deadline": 1200,
                "accessible": False, "budget_per_person": 100.0, "original_flight_id": None,
                "priority": 1}},
            {"type": "resource_unavailable", "target_id": "BROKEN", "value": None},
        ],
        "strategies": [], "focus_id": "LIS",
        "explanation": "Valori demonstrative: 12 locuri, autocar de rezervă la 11:00, sosire la 15:00 și buget de 100 EUR/pasager.",
    }


def completed(value):
    return {"status": "completed", "model": "fixture", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]}


class ConversationTransport:
    def __init__(self, replies, *, fail_decision=False):
        self.replies = list(replies)
        self.inputs = []
        self.instructions = []
        self.decisions = []
        self.fail_decision = fail_decision

    def __call__(self, request):
        body = json.loads(request.content)
        if request.url.path == "/v1/responses":
            self.inputs.append(json.loads(body["input"]))
            self.instructions.append(body["instructions"])
            return httpx.Response(200, json=completed(self.replies.pop(0)))
        assert request.url.path == "/v1/decisions"
        self.decisions.append(body)
        if self.fail_decision:
            return httpx.Response(503, json={"error": "fixture unavailable"})
        question = body["questions"][0]
        return httpx.Response(200, json={"answers": [{"type": "choice", "name": question["name"],
                                                      "choice": question["choices"][0]["value"]}]})


def request(service, text, sequence, context=""):
    return CommandRequest(text=text, context=context, expected_version=service.store.read().version,
                          idempotency_key=f"scene-conversation-{sequence}")


def context_after(exchanges):
    return "\n".join(json.dumps({"user": text, "verified_backend": reply["explanation"]},
                                 ensure_ascii=False) for text, reply in exchanges)


@pytest.mark.asyncio
async def test_essential_and_optional_answers_keep_scene_until_atomic_completion(tmp_path):
    transport = ConversationTransport([
        clarification("Câți pasageri sunt și unde trebuie să ajungă?"),
        clarification(DEFAULTS_QUESTION),
        authored_scene(),
    ])
    settings = Settings(_env_file=None, openai_api_key="fixture", operation_mode="simulation",
                        database_path=str(tmp_path / "scene.sqlite3"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = RecoveryService(settings, Store(settings.database_path), AIProvider(settings, client=client))
        before = service.store.read().model_dump()
        first_request = request(service, FIRST_REQUEST, 1)
        first = await service.command(first_request)
        assert first["applied"] is False and first["focus_id"] is None
        assert service.store.read().model_dump() == before and transport.decisions == []
        # An exact retry of a clarification must not consume another interpretation.
        assert (await service.command(first_request))["replayed"]
        assert len(transport.inputs) == 1

        first_context = context_after([(FIRST_REQUEST, first)])
        second = await service.command(request(service, ESSENTIALS, 2, first_context))
        assert second["applied"] is False and second["explanation"] == DEFAULTS_QUESTION
        assert service.store.read().model_dump() == before and transport.decisions == []
        assert transport.inputs[1]["current_request"] == ESSENTIALS
        assert transport.inputs[1]["conversation_context"] == first_context

        full_context = context_after([(FIRST_REQUEST, first), (ESSENTIALS, second)])
        final_request = request(service, "Completează tu restul.", 3, full_context)
        result = await service.command(final_request)
        after = service.store.read()
        assert transport.inputs[2]["current_request"] == "Completează tu restul."
        assert transport.inputs[2]["conversation_context"] == full_context
        assert after.scenario_id != before["scenario_id"] and after.version == before["version"] + 1
        assert {airport.id for airport in after.airports} == {"LIS", "OPO"}
        assert sum(group.size for group in after.groups) == 12
        assert all(group.destination == "OPO" and group.budget == group.size * 100 for group in after.groups)
        assert len(after.offers) == 4 and all(offer.itinerary.legs[0].resource_id == "BACKUP" for offer in after.offers)
        assert result["decision"]["mode"] == "ai" and len(transport.decisions) == 1
        assert "Valori demonstrative" in result["decision"]["explanation"]
        assert (await service.command(final_request))["replayed"]
        assert len(transport.inputs) == 3 and len(transport.decisions) == 1
        assert all(instructions == SYSTEM_PROMPT for instructions in transport.instructions)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    FIRST_REQUEST + " Sunt 12 pasageri spre Porto; completează tu restul cu valori demonstrative.",
    "Creează un scenariu nou: 12 pasageri în familii de 3 merg din LIS în OPO; autocarul de 12 locuri "
    "de la 10:30 la 14:30, 40 EUR/persoană, se defectează. Adaugă un autocar accesibil de rezervă "
    "cu 12 locuri la 11:00, sosire 15:00, 50 EUR/persoană. Pasagerii sunt pregătiți la 10:00, "
    "cu termen 20:00, fără cerințe de accesibilitate, prioritate normală și 100 EUR/persoană.",
])
async def test_ready_request_can_commit_in_one_turn_without_an_extra_confirmation(tmp_path, text):
    transport = ConversationTransport([authored_scene()])
    settings = Settings(_env_file=None, openai_api_key="fixture", operation_mode="simulation",
                        database_path=str(tmp_path / "ready.sqlite3"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = RecoveryService(settings, Store(settings.database_path), AIProvider(settings, client=client))
        result = await service.command(request(service, text, 1))
    assert len(transport.inputs) == 1 and len(transport.decisions) == 1
    assert result["world"]["version"] == 2
    assert {airport["id"] for airport in result["world"]["airports"]} == {"LIS", "OPO"}


@pytest.mark.asyncio
async def test_failed_completion_preserves_previous_scene_after_clarification(tmp_path):
    transport = ConversationTransport([clarification(DEFAULTS_QUESTION), authored_scene()], fail_decision=True)
    settings = Settings(_env_file=None, openai_api_key="fixture", operation_mode="simulation",
                        database_path=str(tmp_path / "rollback.sqlite3"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = RecoveryService(settings, Store(settings.database_path), AIProvider(settings, client=client))
        before = service.store.read().model_dump()
        first = await service.command(request(service, FIRST_REQUEST + ESSENTIALS, 1))
        with pytest.raises(ProviderError):
            await service.command(request(service, "Alege tu.", 2, context_after([(FIRST_REQUEST + ESSENTIALS, first)])))
        assert service.store.read().model_dump() == before
        assert len(transport.decisions) == 1


@pytest.mark.asyncio
async def test_simulation_authoring_conversation_does_not_change_live_interpretation():
    transport = ConversationTransport([{**clarification("Datele operaționale trebuie documentate."),
                                         "recovery": None, "operational_report": None}])
    settings = Settings(_env_file=None, openai_api_key="fixture", operation_mode="live")
    world = seed_world().model_dump(mode="json")
    world["mode"] = "live"
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        result = await AIProvider(settings, client=client).interpret(FIRST_REQUEST, world)
    assert transport.instructions == [LIVE_COMMAND_PROMPT]
    assert result["events"] == [] and result["recovery"] is None
