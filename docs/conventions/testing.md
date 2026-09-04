# Testing

## What it is

How foliot is tested, and what may not be used to test it. The four tests in
`DESIGN_SNAPSHOT.md` §14 carry the design's weight: each fails for an
*architectural* reason rather than a coding one, and each is the only real
evidence for one of the §2 guarantees. Everything else is ordinary unit
testing.

Run with `uv run pytest`.

## The two tiers

**Architectural tests** — replay determinism, order independence,
suspend/resume fidelity, crash recovery (§14). These are the product. They
are worth writing before the code they test, because they are the ones most
likely to expose a design error rather than a coding error (§13). Write
order-independence first.

**Unit tests** — handlers as plain functions with a recording context;
queue mechanics; the RNG. §7.4 exists precisely so that a handler is callable
in a test without running a world. **If a handler cannot be tested that way,
the contract has been violated somewhere — fix the contract, not the test.**

A handler returns `None` and tells a `TickContext` instead (§7.4), so the
assertion is on what the context *collected*:

```python
def test_poison_should_reschedule_until_it_expires():
    ctx = FakeContext(tick=100, rng=FixedRng(0.5))
    poison = Poison(EntityId("ivan"), interval=3, expires_at=110)
    poison.process(ctx)

    assert ctx.effects == [Damage(EntityId("ivan"), 3)]
    assert ctx.schedules == [(poison, 103)]
    assert not ctx.finished
```

No engine, no store, no database. `FakeContext` is about ten lines and is
written once — that ten lines is the entire cost of the collecting-context
design over one that returned a value. This direct handler test deliberately
does not admit the new action to a store, so `poison.binding` remains
`Unbound()`. A successful first `txn.schedule()` replaces it with
`Bound(seq, Active(due_tick))` (§6.4).

Engine tests for a raising handler must also capture the `foliot.engine`
logger and prove that an `ERROR` record contains the tick, action class,
`entity_id`, permanent `seq`, and exception traceback. Continuing the tick
must never mean silently swallowing the developer's error, and the technical
record must not enter the deterministic story journal.

The same test must advance another tick and prove that failure retained
exactly one pending action with the same `seq`, binding, and deadline. Retrying
is the original queue entry becoming due again, never creation of a copied
action or a second queue entry.

Context validation tests must distinguish identity from intent: finishing and
rescheduling the owning action is reported and discarded; finishing while
scheduling a different successor is valid; and rescheduling the owner without
finishing is valid. None of these checks adds a method to the public
`TickContext` protocol.

Scheduling validation must reject two requests for the same object in one
tick, whether they came from one context or separate contexts, before applying
any collected work. Two different action objects of the same class and entity
remain valid. The normal scheduled chain is tested across ticks: the action
due at 100 reschedules itself for 120 with the same permanent `seq`. For a
cross-context duplicate, assert that every conflicting context is discarded,
their source actions remain pending without duplication, the target remains
unbound or unchanged, unrelated contexts still commit, and the retry uses
fresh contexts rather than preserving decisions from the previous tick.

An effect that raises is not treated like a handler that raises. Assert that
the exception escapes `process_tick()`, the tick does not advance, and no
foliot-owned queue, binding, sequence, or journal change commits. A durable
adapter's own suite must additionally prove that its game-world writes roll
back. `MemoryStore` cannot make that last assertion for an arbitrary mutable
Python world and must not pretend otherwise (§8.5).

## Fakes, not mocks

**`unittest.mock` is forbidden in this repository.** So are `pytest-mock` /
`mocker.patch`, `monkeypatch.setattr` on production objects, and `freezegun`.

A fake is a hand-written class implementing the Protocol, holding seed state
and recording what happened as ordinary attributes:

```python
class FakeStore[W]:
    """In-memory Store for tests. Records what the engine did to it."""

    def __init__(self, queue: dict[Tick, list[BaseAction[W]]] | None = None) -> None:
        self._queue = queue or {}
        self.committed: list[Tick] = []

    @property
    def world_seed(self) -> int: ...
    def current_tick(self) -> Tick: ...
    def due(self, tick: Tick) -> Iterable[BaseAction[W]]: ...
    def tick_transaction(self, tick: Tick) -> AbstractContextManager[Txn[W]]: ...
```

Tests then assert on `store.committed == [0, 1, 2]` — ordinary Python data,
not `.assert_called_once_with(...)`.

Three reasons, in ascending order of how much they would hurt here:

1. **A mock accepts any attribute.** `create_autospec(Store)` answers to
   `store.due_actions()` as happily as `store.due()`. Rename a method on the
   `Store` Protocol and every mock-based test stays green while production is
   broken. A hand-written fake stops type-checking the moment the Protocol
   changes — which is why `tests/` joins the basedpyright `include` as soon
   as the test tree is created after M2.
2. **A mock returns a mock.** `due(tick)` on a `MagicMock` returns a
   `MagicMock`, which is iterable-ish and truthy. The order-independence test
   would shuffle nothing, process nothing, compare two empty worlds, and
   pass — reporting that foliot's central guarantee holds when nothing ran.
   This is the specific reason the ban is absolute rather than a preference.
3. **Patching hides the injection you already have.** foliot injects the
   store, the driver and the RNG on purpose (§12.3). Reaching for
   `monkeypatch` means the seam was not used.

**Never `freezegun`, and never a global time patch.** `ManualDriver` *is* the
consumer-facing time control (§4.2), and building it before `RealtimeDriver`
is a decided build-order rule (§13). Test `RealtimeDriver`'s own pacing maths
with a private fake-time subclass that replaces its private clock and sleeper
methods; do not expose those seams as public constructor arguments. Freezing
the clock globally to test a clock is testing the patch.

`ManualDriver(until_tick=n)` includes tick `n`. Starting with tick 90 as the
next unfinished tick and running until 100 must process ticks 90 through 100,
including an action that rescheduled itself from 90 to 100, and leave the
store's `current_tick()` at 101. Test this boundary explicitly; it is where an
exclusive loop would silently skip the requested deadline.

Test `MemoryStore(initial_actions=...)` as construction, not as a hidden tick:
ordered unbound actions receive ordered permanent sequence numbers, recurring
and scheduled queue shapes are preserved, a deadline equal to `current_tick`
is due immediately, a past deadline is rejected, and the clock does not move.
The convenience belongs only to the concrete in-memory implementation and must
not appear on the `Store` protocol.

## The four architectural tests

**Replay determinism.** Run N ticks from seed S, hash the ordered log, re-run,
assert the hashes match. **Run it in a subprocess with a different
`PYTHONHASHSEED`** — that is the only thing that catches §9.4, and it is
invisible in-process because a single run is self-consistent.

**Order independence.** Take a tick with several due actions, shuffle the
processing order, assert the world state *and* the log are identical. This is
the only real test of guarantee 2, and it is what a global RNG breaks (§9.2).
Shuffle with a seeded `random.Random` local to the test so a failure is
reproducible.

**Suspend/resume fidelity.** Suspend a suspendable action mid-flight,
advance many ticks, resume, and assert **every** deadline moved forward by
exactly the pause — the core's `due_tick` and the game's own `arrives_at`,
shifted by the same `paused_for` (§5.3, §6.4) — while non-suspendable effects
(poison, hunger) fired throughout (§6.2). The failure this catches is silent:
a Char who arrives as though the fight never happened.

**Crash recovery.** Every consumer-owned durable store tests this in its own
adapter suite: interrupt between "the handler ran" and "the tick committed,"
restart, assert nothing is lost and nothing is applied twice (§8). Foliot owns
no database adapter. Its `MemoryStore` instead tests that an exceptional exit
publishes none of its staged queue, binding, log, or clock changes.

## Conventions

- Mirror the `src/` layout: `tests/test_rng.py` for `src/foliot/rng.py`.
- Name tests as sentences: `test_resume_should_shift_every_deadline_by_the_pause`.
- Fakes live in `tests/fakes.py` and are **never** published to `src/`. The
  in-memory `Store` that ships (§12.1) is a reference implementation for
  consumers, not a test double — do not merge the two.
- Shared fixtures in `conftest.py`; construction helpers in `tests/factories.py`.
- Cover negative paths. An action that is cancelled, suspended twice, or
  resumed after its deadline passed is where the bookkeeping breaks.
- Lock deterministic RNG output with golden vectors. Updating those expected
  values is a replay-format change, never routine test maintenance (§9.3).
- Run RNG stability in real subprocesses with different `PYTHONHASHSEED`
  values; two instances inside one interpreter cannot expose salted hashing.
- `examples/tinyworld` is a test in disguise (§12.1): it is the only honest
  check that the public API is pleasant, and it must be written while the API
  can still change.

## Cross-references

- `DESIGN_SNAPSHOT.md` §14 — the strategy this file implements.
- `DESIGN_SNAPSHOT.md` §9.2, §9.4 — what tests 1 and 2 actually defend.
- `docs/conventions/python-style.md` — the code-level half.

## Don'ts

- Do not import anything from `unittest.mock`.
- Do not use `pytest-mock` / `mocker.patch(...)`.
- Do not `monkeypatch.setattr` a production object.
- Do not use `freezegun` or otherwise patch time. Use `ManualDriver`.
- Do not run the determinism test in-process only. It must cross a
  `PYTHONHASHSEED` boundary.
- Do not shuffle with an unseeded RNG — a failure you cannot reproduce is
  not a test result.
- Do not publish fakes to `src/`.
- Do not skip negative paths.
- Do not unit-test by running the whole world when §7.4 makes a plain
  function call possible.
