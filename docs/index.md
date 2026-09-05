# Documentation

Foliot has one job: advance application-owned state through deterministic,
transactional ticks. Start with the quickstart, then read only the concepts
your simulation needs.

- [Quickstart](quickstart.md) — build and run a tiny simulation.
- [Actions and effects](actions.md) — scheduling, recurring work, state, and
  suspension.
- [Simultaneous Events](events.md) — coordinate decisions without leaking one
  participant's result into another's choice.
- [Writing a durable store](stores.md) — connect foliot to your database.
- [Determinism and randomness](determinism.md) — seeds, streams, and replay.
- [Drivers](drivers.md) — fast-forward and real-time pacing.
- [Architecture](architecture.md) — boundaries and guarantees.
- [Public API](api.md) — every supported import in one place.

Runnable consumers live in [`examples/tinyworld`](../examples/tinyworld/) and
[`examples/eventworld`](../examples/eventworld/).
