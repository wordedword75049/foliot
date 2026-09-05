# Actions and effects

## Action ownership

Every `BaseAction` has an `entity_id`: the entity that owns the work. Foliot
uses it for random-stream isolation, suspension, and owner-wide deletion. It
does not mean “target.” Directed simulations can add a mandatory `target_id`
in their own shared action base.

## Admission and identity

A newly constructed action has `Unbound()` as its complete binding. The first
successful `Txn.schedule(...)` changes it to `Bound(seq, Active(due_tick))`.

`seq` is permanent. Rescheduling, suspending, and resuming replace the state
without changing the sequence number. A durable adapter must persist and
restore both the binding and every game-defined subclass field.

## Scheduled and recurring actions

- `due_tick=50` means the action becomes due at logical tick 50.
- `due_tick=None` means the action is due on every tick.
- A deadline must be strictly later than the tick that schedules it.

A scheduled action disappears after it runs unless it reschedules itself. A
recurring action remains until it calls `ctx.finish()`.

```python
@override
def process(self, ctx: TickContext[World], /) -> None:
    ctx.emit(Damage(self.entity_id, 2))
    next_strike = ctx.tick + self.interval
    if next_strike < self.expires_at:
        ctx.schedule(self, next_strike)
```

The same poison object—and therefore the same `seq` and game payload—moves to
the new deadline.

## Collect first, apply later

Each action receives its own `TickContext`. The context collects effects,
schedules, suspension requests, completion, and log lines. If the handler
raises, that context is discarded and the error is written to ordinary Python
operational logging. Other valid actions may still commit.

After all due actions have decided, valid contexts apply in stable sequence
order. An effect that raises is different: effects execute inside the tick
transaction, so the exception aborts the entire tick.

## Suspension

```python
ctx.suspend(lira_id, by=fight_id)
```

This pauses every suspendable action owned by Lira. Non-suspendable work such
as hunger or poison continues.

A suspended action remembers when it paused, who owes the wake-up, and its
old deadline. Resuming by the same handle shifts the stored deadline forward
by the exact pause duration. Override `on_resume(paused_for)` when the action
also has a game-owned deadline:

```python
@override
def on_resume(self, paused_for: int, /) -> None:
    self.arrives_at += paused_for
```

## Effects

An effect needs only this structural shape:

```python
class Effect[W](Protocol):
    def apply(self, world: W, /) -> None: ...
```

Foliot does not know whether the effect represents damage, healing, ownership,
weather, construction, or economics. Effects should normally carry all data
needed to apply their mutation.

## Post-effect finalization

An optional `TickFinalizer` sees the world after ordinary and Event effects.
It is the place for game rules such as death:

```python
class DeathFinalizer:
    def finalize(self, world: World, ctx: FinalizationContext[World], /) -> None:
        for character in world.characters.values():
            if character.hp == 0:
                ctx.delete_owned_by(character.entity_id)
```

Foliot provides the timing and cleanup tools; it never defines what death
means.
