"""Read historical source evidence without returning it to planning inventory."""
from fastapi import APIRouter, Depends, Path, Query

from .inventory_lifecycle import resource_detail
from .store import DomainError


def create_inventory_history_router(store, operator_dependency):
    router = APIRouter(prefix="/api/inventory", dependencies=[Depends(operator_dependency)])

    @router.get("/resources/{resource_id}")
    def source_record(
        resource_id: str = Path(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$"),
        scenario_id: str = Query(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$"),
    ):
        world = store.read()
        if scenario_id != world.scenario_id:
            raise DomainError(409, "The operation changed; reopen the source record in the current operation")
        detail = resource_detail(world, resource_id)
        if detail is None:
            raise DomainError(404, "No active or archived source record exists for this service")
        return {
            "scenario_id": world.scenario_id,
            **detail,
            "resource": detail["resource"].model_dump(mode="json"),
            "locations": [node.model_dump(mode="json") for node in detail["locations"]],
        }

    return router
