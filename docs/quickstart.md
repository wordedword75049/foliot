# Quickstart

## Install

```console
pip install foliot
```

Foliot requires Python 3.12 or newer and has no runtime dependencies.

## Define the world

The world is an ordinary application-owned Python object:

```python
from dataclasses import dataclass


@dataclass(slots=True)
class World:
    food: int
```

Foliot never inspects its fields.

## Define an effect

An effect describes a world mutation that will happen after all actions for
the tick have decided:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConsumeFood:
    amount: int

    def apply(self, world: World, /) -> None:
        world.food -= self.amount
```

No inheritance is required. Matching the `Effect` protocol is enough.

## Define an action

Every queued action inherits `BaseAction` because foliot owns its admission,
sequence number, deadline, and suspension state:

```python
from typing import override

from foliot import BaseAction, EntityId, TickContext


class Eat(BaseAction[World]):
    def __init__(self, entity_id: EntityId) -> None:
        super().__init__(entity_id, suspendable=False)

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.emit(ConsumeFood(1))
        ctx.log("One ration is eaten.")
```

`process` changes nothing directly. It tells the collecting context what the
action wants to happen.

## Run ten ticks

```python
from foliot import ManualDriver, MemoryStore, Simulation

world = World(food=20)
eat = Eat(EntityId("lira"))
store = MemoryStore(world, world_seed=1, initial_actions=((eat, None),))

Simulation(store).run(ManualDriver(until_tick=9))

assert world.food == 10
assert store.current_tick() == 10
```

The target tick is inclusive: ticks 0 through 9 are ten ticks. `None` made the
action recurring. A concrete integer would make it scheduled for that logical
tick instead.

## Choose a production seed

```python
from foliot import new_world_seed

world_seed = new_world_seed()
```

Generate it once when the world is created and persist it for the lifetime of
that world. A manual integer such as `1` is valid for tests and deliberate
replays.

Continue with [Actions and effects](actions.md) or run:

```console
uv run python -m examples.tinyworld 100
```
