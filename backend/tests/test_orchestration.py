import asyncio

import pytest
from app.config import Settings
from app.models import CommandRequest, EventRequest
from app.service import RecoveryService
from app.store import DomainError, Store


def test_visual_only_command_is_idempotent_and_does_not_replan(tmp_path):
    class AI:
        calls=0
        async def interpret(self,text,world):
            self.calls+=1
            return {"events":[],"strategies":[],"explanation":"Focused on B12; no operational state changed.","focus_id":"B12"}
        async def choose(self,*args):
            pytest.fail("A visual-only command must not call the decision model")
    settings=Settings(database_path=str(tmp_path/"ai.sqlite3"),_env_file=None)
    ai=AI(); service=RecoveryService(settings,Store(settings.database_path),ai)
    before=service.store.read()
    request=CommandRequest(expected_version=before.version,idempotency_key="visual-command",text="Show B12")
    result=asyncio.run(service.command(request))
    assert result["world"]["version"]==before.version
    assert result["focus_id"]=="B12" and result["applied"] is False
    assert not service.store.read().offers
    assert asyncio.run(service.command(request))["replayed"]
    assert ai.calls==1


def test_world_change_during_model_call_cannot_apply_stale_decision(tmp_path):
    settings=Settings(database_path=str(tmp_path/"stale.sqlite3"),_env_file=None)
    store=Store(settings.database_path)
    concurrent=RecoveryService(settings,store)
    class AI:
        async def interpret(self,text,world):
            return {"events":[{"type":"resource_unavailable","target_id":"B12","value":None}],"strategies":[],"explanation":"Break B12","focus_id":"B12"}
        async def choose(self,world,candidates,request):
            concurrent.event(EventRequest(expected_version=world["version"],idempotency_key="concurrent-change",type="resource_capacity",target_id="B04",value=5))
            return {"plan_id":candidates[0]["id"],"explanation":"Choose plan","provider":"test-double"}
    service=RecoveryService(settings,store,AI())
    with pytest.raises(DomainError) as exc:
        asyncio.run(service.command(CommandRequest(expected_version=1,idempotency_key="stale-ai-command",text="Break B12")))
    assert exc.value.status==409
    world=store.read()
    assert next(r for r in world.resources if r.id=="B12").available
    assert next(r for r in world.resources if r.id=="B04").capacity==5
    assert not world.offers and not world.decisions
