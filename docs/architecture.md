# Architecture

## Purpose

Foliot is a deterministic tick engine, not a game framework. It coordinates
logical time, queued work, randomness, and persistence while leaving the world
model and every domain rule to the application.

## The boundary

Foliot owns:

- the logical clock;
- action admission, sequence identity, and queue state;
- scheduled and recurring execution;
- suspension and resumption;
- deterministic random-stream derivation;
- collection and deterministic application order;
- the tick transaction boundary;
- optional simultaneous Event coordination;
- manual and real-time pacing.

The application owns:

- entities, targets, locations, and maps;
- attributes, health, combat, and death;
- goals, quests, inventories, and economies;
- probability formulas;
- action and Event payload serialization;
- database schema and domain repositories;
- API and user-interface technology.

The practical test is simple: if the engine never needs to read a field, the
field belongs to the application.

## Tick pipeline

```text
Store.due(tick)
    │
    ├─ process every ordinary Action ──► collected effects/schedules/logs
    │
    ├─ process every EventAction ──────► one Intent per participant
    │
    ├─ resolve complete Events ────────► collected Outcomes
    │
    ├─ apply valid work in stable order inside Txn
    │
    ├─ run optional game TickFinalizer
    │
    └─ commit world + queue + journal + next tick atomically
```

Actions are all processed before any effect applies. This gives every decision
the same tick-start view and prevents queue iteration order from changing the
world.

## Deterministic identity

The store assigns each admitted action one permanent integer `seq`. Together
with `world_seed`, `entity_id`, and `tick`, that identity addresses the action's
random stream. Rescheduling changes the deadline, not the sequence number.

Events use stable typed ids derived from a source action and explicit ordinal.
Temporary Event-owned entity ids derive from the Event id. These templates
avoid process-randomized hashes and handwritten string conventions.

## Persistence

`Store` and `Txn` are structural protocols. Foliot calls them; it does not
provide a database driver. The built-in memory stores are reference adapters
for tests and examples.

The transaction is the correctness boundary. A completed tick must never leave
world state, queue state, the journal, and `current_tick` disagreeing. Durable
adapters are responsible for putting all four in one real database transaction.

## Two layers

Layer 1 is complete by itself: actions, effects, queues, stores, randomness,
drivers, and optional finalization.

Layer 2 is imported from `foliot.events` only when a simulation needs
simultaneous Intent resolution. It adds one path to the same `Simulation`; it
does not create a parallel engine or change ordinary action behavior.

## Deliberate limitations

- One active simulation runner owns a world.
- Foliot does not provide a database adapter.
- Memory stores cannot generically roll back arbitrary mutations made directly
  to a game-owned Python object. Use staged effects; durable adapters must use
  transactional world writes.
- External side effects such as email or HTTP calls cannot be rolled back by a
  database transaction. Applications should use an outbox or idempotency.
- Wall-clock downtime pauses simulation time. Logical ticks advance only when
  processed.
