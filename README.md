# foliot

`foliot` is a small, deterministic, transactional tick engine for persistent
simulations.

It gives you a durable action queue, scheduled and recurring work, reproducible
randomness, suspension and resumption, atomic ticks, real-time pacing, and an
optional layer for simultaneous multi-entity events. It deliberately does not
define a domain model or its rules. Applications can use it for games,
agent-based models, virtual worlds, economies, ecosystems, logistics, and
other stateful simulations while owning all domain state and behavior.

> **Status:** pre-alpha. The core is tested and usable, but the public API may
> still change before 1.0.

## Why foliot?

- **Reproducible:** the same world seed and inputs produce the same history.
- **Order-independent:** unrelated actions have independent random streams.
- **Transactional:** queue changes, effects, journal lines, and the tick commit
  through one store transaction.
- **Fast-forwardable:** use logical time to run millions of ticks without
  sleeping.
- **Domain-agnostic:** your application owns its state model and rules.
- **Storage-agnostic:** implement two small protocols for PostgreSQL, MySQL,
  MariaDB, a file, or another backend.
- **Dependency-free:** the installed library uses only the Python standard
  library.

## Installation

```console
pip install foliot
```

Python 3.12 or newer is required.

## Five-minute example

```python
from dataclasses import dataclass
from typing import override

from foliot import BaseAction, EntityId, ManualDriver, MemoryStore, Simulation, TickContext


@dataclass(slots=True)
class World:
    energy: int = 100


@dataclass(frozen=True, slots=True)
class LoseEnergy:
    amount: int

    def apply(self, world: World, /) -> None:
        world.energy -= self.amount


class Hunger(BaseAction[World]):
    def __init__(self, entity_id: EntityId) -> None:
        super().__init__(entity_id, suspendable=False)

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.emit(LoseEnergy(1))
        ctx.log("Lira grows hungry.")


world = World()
store = MemoryStore(
    world,
    world_seed=1,
    initial_actions=((Hunger(EntityId("lira")), None),),
)

Simulation(store).run(ManualDriver(until_tick=9))

assert world.energy == 90
assert len(store.logs) == 10
```

`due_tick=None` makes `Hunger` recurring, so it runs once per logical tick.
Effects and journal lines are collected first and applied only after every due
action has made its decision.

## The model

An action begins **unbound**. Its first successful store admission assigns one
permanent sequence number and an active state. Scheduling the same object again
changes its deadline without changing that identity.

| Request | Meaning |
|---|---|
| `ctx.schedule(action, 100)` | Run at tick 100. |
| `ctx.schedule(action, None)` | Run every tick until `ctx.finish()`. |
| `ctx.emit(effect)` | Apply an application-defined state change after decisions. |
| `ctx.log(line)` | Append one deterministic journal line. |
| `ctx.suspend(entity_id, by=handle)` | Pause that entity's suspendable actions. |
| `ctx.finish()` | Remove the current action. |

Concrete deadlines must always be later than the current tick. An action due
at tick 90 can reschedule itself for tick 100; it keeps the same sequence
number.

## Optional simultaneous Events

Ordinary actions are enough for walking, hunger, poison, construction, growth,
and most other simulation work. Import `foliot.events` only when several
entities must decide from the same tick-start state before any result applies.

```python
from foliot import Simulation
from foliot.events import EventMemoryStore, Events

store = EventMemoryStore(world, world_seed=1)
simulation = Simulation(store, events=Events(store))
```

Each `EventAction` returns one application-defined Intent. Once every expected
participant has answered in the same tick, the concrete `BaseEvent` resolves
the complete set into either `Outcome.continue_with(...)` or `Outcome.end(...)`.
Domain formulas and lifecycle rules remain application code.

Run the complete example:

```console
uv run python -m examples.eventworld
```

## Storage

`MemoryStore` and `EventMemoryStore` are ready-made implementations for tests,
examples, and temporary simulations. They disappear with the process.

A durable application implements `Store` and `Txn` (plus `EventStore` and
`EventTxn` when Events are enabled). Foliot decides *when* the transaction
begins and commits; your adapter decides *how* actions and world state are
encoded in its database.

See [Writing a store](https://github.com/wordedword75049/foliot/blob/main/docs/stores.md)
for the complete contract and a
PostgreSQL-shaped example.

## Documentation

- [Documentation index](https://github.com/wordedword75049/foliot/blob/main/docs/index.md)
- [Quickstart](https://github.com/wordedword75049/foliot/blob/main/docs/quickstart.md)
- [Actions and effects](https://github.com/wordedword75049/foliot/blob/main/docs/actions.md)
- [Simultaneous Events](https://github.com/wordedword75049/foliot/blob/main/docs/events.md)
- [Writing a durable store](https://github.com/wordedword75049/foliot/blob/main/docs/stores.md)
- [Determinism and randomness](https://github.com/wordedword75049/foliot/blob/main/docs/determinism.md)
- [Manual and real-time drivers](https://github.com/wordedword75049/foliot/blob/main/docs/drivers.md)
- [Architecture](https://github.com/wordedword75049/foliot/blob/main/docs/architecture.md)
- [Public API map](https://github.com/wordedword75049/foliot/blob/main/docs/api.md)
- [Contributing](https://github.com/wordedword75049/foliot/blob/main/CONTRIBUTING.md)

## Development

```console
uv sync
uv run ruff format --check src tests examples
uv run ruff check src tests examples
uv run basedpyright
uv run pytest
```

## License

MIT
