# Writing a durable store

Foliot owns transaction timing. Your application owns persistence technology,
schema, serialization, and domain queries.

The included `MemoryStore` is ideal for tests and examples, but it disappears
when the process exits. A real application implements the structural `Store`
and `Txn` protocols without inheriting from a foliot base class.

## Store: reads and transaction creation

```python
class Store[W](Protocol):
    @property
    def world_seed(self) -> int: ...

    def current_tick(self) -> int: ...
    def due(self, tick: int) -> Iterable[BaseAction[W]]: ...
    def tick_transaction(self, tick: int) -> AbstractContextManager[Txn[W]]: ...
```

- `world_seed` is the one persisted unsigned 128-bit seed for the world.
- `current_tick()` returns the next unfinished logical tick.
- `due(tick)` returns every active action whose deadline is at or before the
  tick, plus every recurring action whose deadline is `None`.
- `tick_transaction(tick)` opens the single transaction in which that tick is
  applied.

Every returned action is already bound. Its permanent `seq` is restored from
storage. Result order does not affect correctness, although stable sequence
order is convenient.

## Txn: writes valid inside one tick

The transaction exposes the application state used by effects and operations for
scheduling, deletion, suspension, resumption, and narrative logging.

On clean context-manager exit, the adapter must atomically:

1. apply all world writes;
2. apply all queue and suspension writes;
3. append journal lines;
4. advance `current_tick` by one;
5. commit once.

On exceptional exit, none of those changes may survive.

## PostgreSQL-shaped structure

The exact schema belongs to the application, but the transaction boundary usually
looks like this:

```python
class PostgresStore:
    def tick_transaction(self, tick: int):
        return PostgresTickTransaction(self.pool, tick)


class PostgresTickTransaction:
    def __enter__(self):
        self.connection = self.pool.acquire()
        self.connection.execute("BEGIN")
        self.txn = PostgresTxn(self.connection, self.tick)
        return self.txn

    def __exit__(self, error_type, error, traceback):
        if error_type is None:
            self.txn.flush_batches()
            self.connection.execute(
                "UPDATE worlds SET current_tick = current_tick + 1 WHERE id = %s",
                (self.world_id,),
            )
            self.connection.execute("COMMIT")
        else:
            self.connection.execute("ROLLBACK")
```

This is illustrative pseudocode, not a required database API. In practice,
`Txn.world` can be an application repository or unit-of-work object bound to that same
connection, allowing `effect.apply(txn.world)` to write through the active
transaction.

## Batch writes

Protocol methods may be called many times during one tick. Collect their
requests and flush them with set-based statements at commit instead of issuing
one database round-trip per method call. This matters when thousands of
actions reschedule simultaneously.

## Action serialization

Foliot does not prescribe JSON, pickle, ORM models, or a `kind` registry. The
application knows its concrete action classes and payloads, so it owns the codec.
Hydration must reconstruct the original subclass and then restore its
`Bound(seq, state)` lifecycle data.

Avoid Python pickle for untrusted or long-lived data. Explicit versioned application
payloads are easier to migrate safely.

## Domain queries stay outside the protocol

Your concrete store may expose application methods such as:

- `character(character_id)`;
- `nearest_point_of_interest(place_id)`;
- `active_actions_for(entity_id)`;
- `journal_page(before_tick, limit)`.

The engine-facing `Store` remains narrow because many simulations have no
characters, locations, or inventories.

## Event-capable storage

When using `foliot.events`, the same concrete adapter also satisfies
`EventStore` and its transaction satisfies `EventTxn`:

```python
class EventStore(Store[W], Protocol):
    def event(self, event_id: EventId) -> BaseEvent[W] | None: ...


class EventTxn(Txn[W], Protocol):
    def event_open(self, event, due_tick): ...
    def event_continue(self, event, children, due_tick): ...
    def event_end(self, event_id): ...
```

These are not a second database transaction. Event rows, temporary payload,
child actions, suspension changes, effects, logs, and clock advancement must
commit through the same physical transaction.

## Single runner

Foliot intentionally supports one active simulation runner per world. A
production deployment should enforce that ownership with its process model,
database advisory lock, lease, or another application-level mechanism. The
library does not coordinate concurrent tick workers.

## Adapter tests that matter

A durable adapter should prove:

- a crash or exception leaves the tick unfinished and applies nothing twice;
- due actions include overdue and recurring work exactly once;
- permanent sequence numbers survive hydration and rescheduling;
- suspension and deadline shifting survive restart;
- owner-wide deletion catches newly scheduled work in the same transaction;
- Event state and queue state cannot commit separately.
