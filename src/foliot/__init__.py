"""foliot -- a deterministic tick-driven simulation core.

A clock, a queue of scheduled things, a way to look up what to run, and a
source of addressable randomness. It has never heard of a target, location,
environment, activity, or combat rule.

The value is in five guarantees, not in the object graph:

1. Same seed, same history.
2. Reordering the queue cannot change outcomes.
3. The clock does not drift.
4. Nothing pending is lost, and nothing is applied twice.
5. Time is injectable -- ten million ticks run in a unit test.

Everything re-exported here is public API: a moved name is a breaking change.
Anything not listed in `__all__` is internal and free to move.
"""

from foliot.actions import (
    ActionBinding,
    ActionState,
    Active,
    BaseAction,
    Bound,
    Suspended,
    Unbound,
)
from foliot.context import TickContext
from foliot.drivers import Driver, ManualDriver, RealtimeDriver
from foliot.effects import Effect
from foliot.engine import Simulation
from foliot.ids import EntityId, SuspensionId, Tick
from foliot.rng import Rng, counter_rng, new_world_seed
from foliot.stores import MemoryStore, Store, Txn

__all__ = [
    "ActionBinding",
    "ActionState",
    "Active",
    "BaseAction",
    "Bound",
    "Driver",
    "Effect",
    "EntityId",
    "ManualDriver",
    "MemoryStore",
    "RealtimeDriver",
    "Rng",
    "Simulation",
    "Store",
    "Suspended",
    "SuspensionId",
    "Tick",
    "TickContext",
    "Txn",
    "Unbound",
    "counter_rng",
    "new_world_seed",
]
