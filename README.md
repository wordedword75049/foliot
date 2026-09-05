# foliot

A deterministic tick-driven simulation core: durable queue, absolute deadlines, per-entity randomness. Domain-agnostic.

## Tinyworld

Run the bundled one-million-tick example:

```console
uv run python -m examples.tinyworld
```

Lira continuously crosses a haunted forest. Encounters may poison her, poison
reschedules its own strikes, and a walk targets the forest while carrying a
separate destination clearing. Arrival queues `Rest` against that clearing;
resting in the moonlit clearing may emit healing, chooses the next destination,
asks the game's pathing service for both the nearest POI and connecting
environment, and queues the next walk. The example uses only foliot's public
Layer 1 API and a fixed seed, so its journal hash is reproducible.

## Optional Events

Layer 1 remains complete on its own. A simulation that needs simultaneous
participant decisions opts into Layer 2 explicitly:

```python
from foliot import Simulation
from foliot.events import EventMemoryStore, Events

store = EventMemoryStore(world, world_seed)
simulation = Simulation(store, events=Events(store))
```

Game EventActions return one Intent each; their persisted `BaseEvent` resolves
the complete same-tick set into an explicit `Outcome.continue_with(...)` or
`Outcome.end(...)`. Actions that create Events import `open_event`; a
post-effect finalizer that must force one closed imports `end_event`. Ordinary
`TickContext` contains no Event methods, and plain `MemoryStore` remains the
small Layer-1-only reference store.

Run the separate Layer-2 example:

```console
uv run python -m examples.eventworld
```

Its forest creates a Fight Event and an Event-owned wolf, explicitly suspends
Lira's walk, and gives both combatants one fresh EventAction per round. Combat
rules and hit probability stay in the example. A game-owned finalizer handles
death after effects; ending the Event removes its children and resumes Lira's
walk with its arrival deadline shifted by the pause.
