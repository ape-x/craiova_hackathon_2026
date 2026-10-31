"""Bound future live planning without moving stored timestamps or identities."""

from .scenario_schema import HORIZON_MINUTES


def planning_horizon_end(world) -> int:
    # Store derives the live clock from UTC on every read/transaction. Keeping
    # the epoch stable preserves source schedules, profiles, receipts and audit
    # history; only the upper bound moves with the current planning minute.
    current = max(0, world.sim_time) if world.mode == "live" else 0
    return current + HORIZON_MINUTES
