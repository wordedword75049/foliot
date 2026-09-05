# Simultaneous Events

Events are optional. Do not use them merely because something interesting
happens; ordinary actions already handle most simulation work.

Use an Event when multiple entities must choose from the same tick-start state
and no participant may observe another participant's result before deciding.
A fight is the standard example: Lira chooses attack while the wolf chooses
dodge, then the pair resolves together.

## Enable the layer

```python
from foliot import Simulation
from foliot.events import EventMemoryStore, Events

store = EventMemoryStore(world, world_seed=1)
simulation = Simulation(store, events=Events(store))
```

The `Events` collaborator must use the exact store passed to `Simulation`.
That makes Event state and ordinary queue state part of one transaction.
Without this explicit collaborator, normal actions still work and foliot does
not attempt Event resolution.

## 1. Define game Intents

Intents are ordinary game objects:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Attack:
    damage: int


@dataclass(frozen=True, slots=True)
class Dodge:
    agility: int
```

Foliot never interprets them.

## 2. Define one EventAction per participant

```python
from typing import override

from foliot import EntityId
from foliot.events import DecisionContext, EventAction, EventId


class CombatTurn(EventAction[World]):
    def __init__(self, fighter: Fighter, event_id: EventId) -> None:
        super().__init__(fighter.entity_id, event_id)
        self.fighter = fighter

    @override
    def decide(self, ctx: DecisionContext, /) -> Attack | Dodge:
        if self.fighter.hp < 5 and ctx.rng.random() < 0.5:
            return Dodge(self.fighter.agility)
        return Attack(self.fighter.damage)
```

Do not override `process()`. `EventAction` provides the final bridge that calls
`decide()` and registers exactly one Intent with the correct Event id and
permanent source sequence number.

`DecisionContext` exposes only `tick` and the participant action's RNG. Game
objects referenced by the action may supply the tick-start statistics needed
for the choice.

## 3. Define the Event resolver

```python
from typing import override

from foliot.events import BaseEvent, IntentRecord, Outcome, ResolutionContext


class Fight(BaseEvent[World]):
    def __init__(self, event_id, children, lira, wolf) -> None:
        super().__init__(event_id, children)
        self.lira = lira
        self.wolf = wolf

    @override
    def resolve(
        self,
        ctx: ResolutionContext,
        intents: tuple[IntentRecord, ...],
        /,
    ) -> Outcome[World]:
        effects = resolve_combat(intents, ctx.rng)
        return Outcome.continue_with(
            CombatTurn(self.lira, self.event_id),
            CombatTurn(self.wolf, self.event_id),
            due_tick=ctx.tick + 1,
            effects=effects,
        )
```

The concrete Event owns game rules and any temporary payload, such as the wolf.
`ResolutionContext` contains only the current tick and an Event-specific RNG.

A continuing Outcome must provide at least one fresh, unbound child and one
strictly future deadline. The old round's children disappear. The new objects
receive new sequence numbers when committed.

End explicitly when the interaction is complete:

```python
return Outcome.end(
    effects=(RecordVictory(lira_id),),
    log=("The wolf escapes into the trees.",),
)
```

Closing removes the Event and its current children and resumes actions
suspended by that Event id.

## 4. Open the Event

Create stable typed identifiers before constructing temporary entities and
children:

```python
from foliot.events import EntityIdTemplate, EventIdTemplate, open_event

FIGHT_IDS = EventIdTemplate("mygame.fight")
WOLF_IDS = EntityIdTemplate("mygame.wolf")

event_id = FIGHT_IDS.from_action(source_action, tick=ctx.tick, ordinal=0)
wolf_id = WOLF_IDS.from_event(event_id, ordinal=0)

fight = Fight(event_id, children, lira, Wolf(wolf_id))
open_event(ctx, fight, ctx.tick + 1)
ctx.suspend(lira.entity_id, by=event_id)
```

Opening and suspension are separate on purpose. Some Events should not
interrupt existing activities.

Every opening child must be unbound, distinct by object identity, and carry
the Event's id. All children are admitted at the same future tick.

## Incomplete rounds and failures

The Event resolves only when every exact current child produces an Intent in
the same tick. If one participant fails:

- that Event attempt produces no result;
- partial Intents are discarded;
- the same child actions remain pending;
- all participants decide again from fresh contexts next tick;
- unrelated ordinary actions and Events may still commit.

Resolver exceptions are reported through Python operational logging and leave
the Event pending. An exception from an Outcome effect is tick-fatal because
application has already begun inside the transaction.

## Ending after effects

Sometimes the Event cannot know it has ended until effects apply—for example,
damage reduces a participant to zero HP. A game-owned `TickFinalizer` can call
the explicit helper afterward:

```python
from foliot.events import end_event

if fighter.hp == 0:
    ctx.delete_owned_by(fighter.entity_id)
    end_event(ctx, fight.event_id)
```

Foliot supplies the cleanup mechanism but has no concept of HP or death.

See [`examples/eventworld`](../examples/eventworld/) for a complete runnable
fight.
