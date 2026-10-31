"""Replace a synthetic scene inside the caller's atomic transaction."""
import secrets

from .models import World
from .seed import seed_world
from .store import DomainError, audit


def reset_scenario(world: World, kind: str) -> None:
    if world.mode != "simulation":
        raise DomainError(409, "Synthetic reset is disabled in live mode.")
    if kind not in ("empty", "seed"):
        raise DomainError(422, "Reset requires 'empty' or 'seed'.")
    replacement = seed_world() if kind == "seed" else World(
        scenario_id=secrets.token_hex(8), epoch=world.epoch,
        sim_time=world.sim_time, airports=[], resources=[], groups=[],
    )
    # Versions stay monotonic; observed traffic is independent of scene inventory.
    for field in World.model_fields:
        if field not in ("version", "observations", "provider_status"):
            setattr(world, field, getattr(replacement, field))
    audit(world, "scenario_reset", f"Started {'an empty synthetic scene' if kind == 'empty' else 'the initial synthetic scene'}; previous passenger links are invalidated.", data={"value": kind, "source": "simulation"})
