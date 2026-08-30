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
    Poison(EntityId("ivan"), seq=0, interval=3, expires_at=110).process(ctx)

    assert ctx.effects == [Damage(EntityId("ivan"), 3)]
    assert ctx.schedules == [(action, 103)]
    assert not ctx.finished
```

No engine, no store, no database. `FakeContext` is about ten lines and is
written once — that ten lines is the entire cost of the collecting-context
design over one that returned a value.

## Fakes, not mocks

**`unittest.mock` is forbidden in this repository.** So are `pytest-mock` /
`mocker.patch`, `monkeypatch.setattr` on production objects, and `freezegun`.

A fake is a hand-written class implementing the Protocol, holding seed state
and recording what happened as ordinary attributes:

```python
class FakeStore:
    """In-memory Store for tests. Records what the engine did to it."""

    def __init__(self, queue: dict[Tick, list[Action]] | None = None) -> None:
        self._queue = queue or {}
        self.committed: list[Tick] = []

    @property
    def world_seed(self) -> int: ...
    def current_tick(self) -> Tick: ...
    def due(self, tick: Tick) -> Iterable[Action[W]]: ...
    def tick_transaction(self, tick: Tick) -> ContextManager[Txn[W]]: ...
```

Tests then assert on `store.committed == [0, 1, 2]` — ordinary Python data,
not `.assert_called_once_with(...)`.

Three reasons, in ascending order of how much they would hurt here:

1. **A mock accepts any attribute.** `create_autospec(Store)` answers to
   `store.due_actions()` as happily as `store.due()`. Rename a method on the
   `Store` Protocol and every mock-based test stays green while production is
   broken. A hand-written fake stops type-checking the moment the Protocol
   changes — which is why `tests/` joins the basedpyright `include` at M1.
2. **A mock returns a mock.** `due(tick)` on a `MagicMock` returns a
   `MagicMock`, which is iterable-ish and truthy. The order-independence test
   would shuffle nothing, process nothing, compare two empty worlds, and
   pass — reporting that foliot's central guarantee holds when nothing ran.
   This is the specific reason the ban is absolute rather than a preference.
3. **Patching hides the injection you already have.** foliot injects the
   store, the driver and the RNG on purpose (§12.3). Reaching for
   `monkeypatch` means the seam was not used.

**Never `freezegun`, and never a global time patch.** `ManualDriver` *is* the
time control (§4.2), and building it before `RealtimeDriver` is a decided
build-order rule (§13). Freezing the clock globally to test a clock is
testing the patch.

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

**Crash recovery.** Postgres store only, at M9: interrupt between "the handler
ran" and "the tick committed," restart, assert nothing is lost and nothing is
applied twice (§8).

## Conventions

- Mirror the `src/` layout: `tests/test_rng.py` for `src/foliot/rng.py`.
- Name tests as sentences: `test_resume_should_shift_every_deadline_by_the_pause`.
- Fakes live in `tests/fakes.py` and are **never** published to `src/`. The
  in-memory `Store` that ships (§12.1) is a reference implementation for
  consumers, not a test double — do not merge the two.
- Shared fixtures in `conftest.py`; construction helpers in `tests/factories.py`.
- Cover negative paths. An action that is cancelled, suspended twice, or
  resumed after its deadline passed is where the bookkeeping breaks.
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
