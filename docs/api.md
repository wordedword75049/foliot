# Public API

This page is a map of foliot's supported import surface. The docstrings on
each object contain the detailed parameter and error contracts.

## Core

Import these names directly from `foliot`:

- **Actions:** `BaseAction`, `ActionBinding`, `Unbound`, `Bound`, `ActionState`,
  `Active`, and `Suspended`.
- **Processing:** `TickContext`, `Effect`, `Simulation`, `TickFinalizer`, and
  `FinalizationContext`.
- **Time:** `Driver`, `ManualDriver`, and `RealtimeDriver`.
- **Storage:** `Store`, `Txn`, and `MemoryStore`.
- **Randomness:** `Rng`, `counter_rng`, and `new_world_seed`.
- **Identifiers:** `Tick`, `EntityId`, and `SuspensionId`.

`BaseAction` is the one mandatory base class. The remaining action-state
classes are useful when a store adapter serializes or inspects queue state.

## Optional Events

Import these names from `foliot.events` only when simultaneous decisions are
needed:

- **Actions and intents:** `EventAction` and `IntentRecord`.
- **Events and outcomes:** `BaseEvent`, `Outcome`, `DecisionContext`, and
  `ResolutionContext`.
- **Integration:** `Events`, `EventStore`, `EventTxn`, and `EventMemoryStore`.
- **Commands:** `open_event` and `end_event`.
- **Stable identifiers:** `EventId`, `EventIdTemplate`, and `EntityIdTemplate`.
- **Configuration errors:** `EventConfigurationError`.

See [Simultaneous Events](events.md) before implementing this layer. Ordinary
actions, effects, stores, and drivers do not depend on it.

## Import stability

Names listed in `foliot.__all__` and `foliot.events.__all__` are the intended
public API. Modules and names beginning with an underscore are internal and may
change without notice while the project is pre-alpha.
