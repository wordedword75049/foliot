# Determinism and randomness

## One seed per world

Call `new_world_seed()` once when a production world is created and persist
the returned unsigned 128-bit integer. Reusing that seed with the same stored
state and inputs reproduces the same simulation.

Manually chosen values, including `1`, are valid. They are convenient for
tests, examples, and intentionally shared replays.

## Addressable streams

An action does not consume randomness from one global generator. Its stream is
derived from:

```text
(world_seed, entity_id, logical_tick, permanent_action_seq)
```

This means an unrelated action cannot change another entity's result merely by
running first. Restarting the process also reproduces the stream because there
is no hidden global cursor to restore.

Handlers receive the already-bound stream as `ctx.rng`:

```python
if ctx.rng.random() < encounter_probability:
    ...

index = ctx.rng.below(len(candidates))
```

- `random()` returns a float in `[0.0, 1.0)`.
- `below(n)` returns an unbiased integer in `[0, n)` for
  `1 <= n <= 2**64`.

Do not seed the stream and do not use Python's process-randomized `hash()` to
derive persistent identities.

## Event-resolution streams

Event participants each use their action stream while choosing an Intent. The
shared resolver receives a separate stream derived from the world seed, Event
id, and tick. This keeps participant choices and resolution rolls isolated
from one another.

## What replay requires

A durable simulation must preserve:

- the world seed;
- the current logical tick;
- every action's permanent `seq`, state, owner, and domain payload;
- every open Event and its exact current children;
- external inputs in deterministic order.

Wall-clock time is not part of simulation state.
