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

Test optional tick finalization at the boundary rather than through one game's
idea of death. The finalizer must observe the world after all valid
ordinary-Action and Event Outcome effects; its effects, schedules, deletions,
owner-wide deletions, and logs must land before the same commit. A raising
finalizer must leave the tick unfinished
and roll back every foliot-owned change. `delete_owned_by(entity_id)` must
remove active, suspended, and newly scheduled actions owned by that entity,
while preserving actions owned by someone else even when their game payload
targets it. Target cleanup belongs to the game or its explicit Event, not to
the generic store.

With Layer 2 enabled, exercise an explicit
`from foliot.events import end_event` call from finalization. It must use the
same cleanup path as `Outcome.end(...)`, including removal of next-round
children staged earlier in that tick and wake-up by the exact Event id. Prove
that `delete_owned_by(entity_id)` alone does not close an Event, that the game
must supply the Event id, and that `end_event(...)` without configured Event
support raises a clear configuration error.

For M8, make a two-participant Event where one EventAction raises after the
other successfully produces an intent. Assert that the Event does not resolve,
neither current-tick intent is retained, both original actions remain pending
exactly once with their permanent sequence numbers, and the clock still
commits the otherwise valid tick. On the next tick both actions must run again
with fresh contexts and fresh RNG streams, and only that same-tick pair may
resolve. This prevents accidental mixtures of decisions made against different
world snapshots.

Contrast that retry with a successfully resolved continuing Event. Its current
children must be consumed exactly once, a fresh deterministic-order set must be
scheduled for a future tick, and those new objects must receive new permanent
sequence numbers. On that future tick their decisions must observe the prior
round's applied state. This catches the tempting but incorrect optimization of
rescheduling one combat-turn object across multiple resolved rounds.

Test an EventAction's `decide()` directly with a tiny fake
`DecisionContext` containing only `tick` and a fixed `Rng`. Assert on the one
game Intent it returns. At the integration boundary, prove foliot attaches the
correct `event_id` and permanent source `seq`, and treats an exception or a
missing Intent as an incomplete Event attempt. Do not give the fake effect,
scheduling, lifecycle, log, world, store, or registry methods; their absence is
the capability boundary under test.

Give resolvers a separate fake `ResolutionContext` with only `tick` and `rng`.
Golden tests must prove an Event-resolution stream replays for the same world
seed, Event id, and tick; changes when Event id or tick changes; and is domain-
separated from every participant action stream. Resolve independent Events in
reversed order and assert identical per-Event outcomes. Combat probability
tests belong to the game and should inject a fixed RNG into the game formula;
foliot must not grow agility, HP, hit-chance, or `chance()` concepts.

Call each concrete Event's `resolve()` directly in unit tests with a fake
`ResolutionContext` and explicit Intent records, then assert on its returned
Outcome. No resolver registry fake, kind lookup, or registration fixture should
exist. A game that delegates to a rules service tests that service as ordinary
game code.

Test `BaseEvent` through a concrete game subclass and keep every game field on
that same object across opening and child replacement. Opening exposes its
exact admitted children; a resolved continuing round replaces them; closing
removes the Event instead of setting a status. Tests must not invent an Event
deadline, suspension state, tombstone, or wrapper object.

Test lifecycle through the two explicit Outcome forms. A continuing fight must
use `Outcome.continue_with(..., due_tick=...)` and admit every supplied fresh
child at that one shared deadline; a wolf escape must use `Outcome.end(...)`
and remove the Event. The continuation deadline must be strictly future. An
empty or forgotten next-child collection must never be interpreted as an
implicit request to end.
Successful ending must also resume every action suspended by that exact Event
id using the normal pause shift, remove its current children and Event-owned
ephemeral payload, and leave actions held by other ids alone. Committed effects
and journal history remain. A non-interrupting Event must close as a harmless
no-op for suspension. Prove that an application or finalization failure rolls
back cleanup and wake-up, and that same-transaction owner deletion leaves no
resumed action behind.

If a game wants data derived from an ephemeral Event entity to outlive the
Event, its ending Outcome must explicitly create that permanent game state.
Closing must never leave the Event's temporary payload behind merely because a
game still holds an old Python reference to it.

In the game-level simultaneity test, start Lira at 6 HP, contribute Poison
damage in the same tick as a dodge calculation, and run the due actions in both
orders. The resolver must use 6 HP in both runs; Poison applies afterward and
changes the state observed by the next round. This test must fail if effects
are applied while due actions or Events are still deciding.

Make one resolver raise after constructing part of an Outcome. Assert that an
operational `ERROR` contains tick, Event id, resolver identity, and traceback;
that no part of its Outcome or story log survives; that its same Event and
current children remain pending exactly once; and that unrelated actions and
Events commit. The next tick must use fresh participant contexts. Contrast
this with a raising Effect from a valid Outcome: it must escape, leave the tick
unfinished, and roll back every foliot-owned change.

The event-capable in-memory store must prove one physical commit boundary:
inject a failure after staging an Event and ephemeral payload, suspension, and
child schedules, then assert that none survives and the clock does not move.
Verify the successful inverse too. The ordinary `MemoryStore` test
surface must remain unchanged and contain no Event methods. A durable adapter's
own suite must run the equivalent rollback test against its one database
transaction rather than coordinating two repositories.

Construct a BaseEvent with a deterministic id before opening it, and assert
that its ephemeral entity ids, child `event_id` fields, and suspension handle
can all derive from that value while the children remain unbound. Opening must
preserve the Event id, bind the children normally, and reject a duplicate id
atomically. Derivation tests must use the reusable typed templates;
do not bless handwritten `"fight:..."` or `"wolf:..."` strings as public API.
Golden vectors must cover `EventIdTemplate.from_action(...)` and
`EntityIdTemplate.from_event(...)`, namespace separation, different parent
identities and ordinals, non-ASCII length framing, cross-process stability, and
the distinct `EventId` / `EntityId` result types. No test may derive a
namespace from a Python class or module name.

Test `open_event(event, due_tick)` as one admission request. Success must
persist the concrete Event and payload, bind every declared child in
deterministic order, schedule all at the same future tick, and establish those
exact children as the expected set. A non-future tick and duplicate Event id
must fail without leaving any child bound or Event persisted. Opening alone
must not suspend anything; test suspension as a separate command in the same
source context. The defensive admission boundary also rejects an empty child
set, the same child object twice, a child carrying another Event id, or an
already-bound child; these checks only prevent an Event that could never form
the agreed exact intent set.

Exercise Event opening through the explicit
`from foliot.events import open_event` boundary and a normal `TickContext`.
Prove that the ordinary context protocol exposes no Event method, that the
function reaches the configured Event collaborator, and that calling it
without Event support fails with a clear configuration error. Do not create a
second Event-specific context or action-processing signature for tests.

Run the same ordinary-action scenario through `Simulation(store)` and through
the event-enabled configuration with no EventActions due. Assert identical
effects, schedules, deletions, journal order, finalization, clock advancement,
and final world. Also prove that the configuration without an events
collaborator never calls event persistence or resolution. This guards the
promise that Layer 2 adds one path rather than changing Layer 1.

Opening a non-interrupting Event must leave existing suspendable actions
active. A FightEvent test must explicitly request suspension, prove that only
the chosen entity's suspendable actions pause under the Event id, and prove
that non-suspendable poison or hunger continues. Its environment response must
create the Event-owned ephemeral opponent and both exact child EventActions in
the same committed tick; rollback must leave none of them behind.

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
  check that the public API is pleasant. Run it as an external subprocess
  rather than adding the repository root to pytest's import path; the `src/`
  layout must keep protecting against accidental local imports.

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
