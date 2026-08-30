"""Core contracts for the simulation engine.

Nothing in this module knows what a character, a forest, or hit points are.
It knows about ticks, scheduled actions, intents, and resolution. A game
supplies meaning by registering deciders and resolvers and by implementing
Effect / World.

Pipeline for a single tick:

    queue.due(tick)                -> [Action]
      for each action: decide()    -> [Intent]        (reads frozen state)
      group intents by event_key   -> [Event]
      for each event: resolve()    -> Outcome         (the only writer)
      apply effects, enqueue schedules, tombstone cancels

The decide phase never mutates. The resolve phase never reads anything it
was not handed. That split is what makes ticks order-independent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

__all__ = [
    "Tick",
    "EntityId",
    "ActionId",
    "Entity",
    "World",
    "ActionStatus",
    "Action",
    "Intent",
    "Event",
    "Effect",
    "Schedule",
    "Outcome",
    "DecideContext",
    "ResolveContext",
    "Decider",
    "Resolver",
    "Rng",
]

Tick = int
EntityId = str
ActionId = str


# --------------------------------------------------------------------------
# World
# --------------------------------------------------------------------------

@runtime_checkable
class Entity(Protocol):
    """Deliberately almost empty.

    The engine only ever needs to identify an entity, never to interpret it.
    Games subclass or ignore this as they like; level, hp, inventory and the
    rest are none of the engine's business.
    """

    @property
    def id(self) -> EntityId: ...


class World(Protocol):
    """Read access to game state, as seen during a single tick.

    Implementations are expected to present a *stable* view for the duration
    of a tick's decide phase: two deciders in the same tick must observe the
    same world, regardless of processing order. How that is achieved
    (snapshot, copy-on-write, MVCC transaction) is the implementation's
    problem.
    """

    def get(self, entity_id: EntityId, /) -> Entity | None: ...


class Effect(Protocol):
    """A game-defined mutation, produced by a resolver.

    Effects are the only thing permitted to write. Keeping them as objects
    rather than inline mutation buys three things: resolvers stay pure and
    unit-testable, effects can be logged as an audit trail of why state
    changed, and application order is explicit rather than incidental.
    """

    def apply(self, world: Any, /) -> None: ...


# --------------------------------------------------------------------------
# Actions -- the unit the queue stores
# --------------------------------------------------------------------------

class ActionStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"  # tombstone; skipped on pop


@dataclass(frozen=True, slots=True)
class Action:
    """A scheduled intention-to-decide, owned by one entity.

    Data only, no behaviour: `kind` is looked up in the decider registry.
    That keeps actions trivially serialisable, which is what lets the queue
    live in Postgres instead of in process memory.

    An entity may have many pending actions at once -- a sampled encounter,
    an arrival, a cooldown expiry -- so this is a separate record keyed by
    entity, never a field on the entity itself.
    """

    id: ActionId
    entity_id: EntityId
    kind: str
    due_tick: Tick
    payload: Mapping[str, Any] = field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING

    # Tie-break within a tick. Ordering must be total and deterministic even
    # though resolution is order-independent, so that replays match exactly.
    seq: int = 0

    def sort_key(self) -> tuple[Tick, int, ActionId]:
        return (self.due_tick, self.seq, self.id)


# --------------------------------------------------------------------------
# Intents and events -- the rendezvous layer
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Intent:
    """What an entity wants to do, before anyone knows if it succeeds.

    `event_key` is the whole grouping mechanism. Intents emitted in the same
    tick sharing a key are bundled into one Event and resolved together;
    that is how a character's "swing at the beast" and the beast's "bite the
    character" become a single fight rather than two independent
    resolutions.

    Solo intents leave `event_key` unset and get a unique one, which makes a
    single-participant event -- no special case in the engine.
    """

    actor_id: EntityId
    kind: str
    event_kind: str
    event_key: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    # The action this intent came from, so a resolver can tombstone the
    # losing branch (encounter fired first, so cancel the arrival).
    source_action_id: ActionId | None = None

    def resolved_key(self) -> str:
        if self.event_key is not None:
            return self.event_key
        return f"solo:{self.event_kind}:{self.actor_id}:{self.kind}"


@dataclass(frozen=True, slots=True)
class Event:
    """A group of intents to be resolved as one unit."""

    key: str
    kind: str
    tick: Tick
    intents: tuple[Intent, ...]

    @property
    def participants(self) -> tuple[EntityId, ...]:
        seen: dict[EntityId, None] = {}
        for i in self.intents:
            seen.setdefault(i.actor_id, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class Schedule:
    """A request to enqueue a future action."""

    entity_id: EntityId
    kind: str
    due_tick: Tick
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Outcome:
    """Everything a resolver produces. No side effects, just a description.

    `log` carries observer-facing narrative. In a zero-player game the log
    *is* the product, so it is a first-class output rather than something
    scraped out of effects afterwards.
    """

    effects: tuple[Effect, ...] = ()
    schedules: tuple[Schedule, ...] = ()
    cancels: tuple[ActionId, ...] = ()
    log: tuple[Any, ...] = ()


# --------------------------------------------------------------------------
# Contexts and handler signatures
# --------------------------------------------------------------------------

class Rng(Protocol):
    """Per-entity, per-tick randomness. Never the global `random` module.

    Seeded from (world_seed, entity_id, tick, seq) so that outcomes do not
    depend on processing order -- which is what permits parallel workers,
    handler retries, and replaying a single character's tick years later to
    answer "why did my hero die here".

    The sampled waits matter as much as the raw draws: `geometric` turns
    "roll 1% every tick" into one scheduled event instead of thousands of
    no-op wakeups, with an identical distribution.
    """

    def random(self) -> float: ...
    def randint(self, lo: int, hi: int) -> int: ...
    def choice(self, seq: Iterable[Any], /) -> Any: ...
    def geometric(self, p: float) -> int: ...


class DecideContext(Protocol):
    """Handed to a decider. Read-only by construction."""

    @property
    def tick(self) -> Tick: ...
    @property
    def world(self) -> World: ...
    @property
    def rng(self) -> Rng: ...


class ResolveContext(Protocol):
    """Handed to a resolver.

    Still read-only: resolvers describe mutations as Effects rather than
    performing them, so they can be called in a test with a fake world and
    asserted on by return value.
    """

    @property
    def tick(self) -> Tick: ...
    @property
    def world(self) -> World: ...
    @property
    def rng(self) -> Rng: ...


class Decider(Protocol):
    """Action -> intents. Registered against `Action.kind`."""

    def __call__(
        self, ctx: DecideContext, action: Action, /
    ) -> Iterable[Intent]: ...


class Resolver(Protocol):
    """Event -> outcome. Registered against `Intent.event_kind`.

    Resolvers must re-validate participants: between decide and resolve, a
    participant may have died in another event this same tick. Degrade,
    do not assume.
    """

    def __call__(self, ctx: ResolveContext, event: Event, /) -> Outcome: ...
