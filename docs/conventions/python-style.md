# Python style

## What it is

House rules for code under `src/foliot/`. Every rule exists to protect one
of the five guarantees in `DESIGN_SNAPSHOT.md` §2; a rule that cannot name
the guarantee it protects, or the failure it prevents, does not belong here.

Enforcement is `uv run ruff format`, `uv run ruff check`, `uv run
basedpyright`. Anything not mechanically enforced is marked **(convention)**
and relies on review. Anything asserted before the code exists to justify it
is marked **(provisional)** — re-argue or delete it when the milestone lands.

## Type discipline

**Ports are `typing.Protocol`, never ABC.** An ABC forces the game to
import foliot merely to satisfy a type, which points the dependency arrow
the wrong way; structural typing keeps `Store`, `Driver` and `Rng`
implementable by someone who never subclasses anything. `BaseAction` is the
single exception, and it is a *convenience* class, not a required base — the
engine only ever type-checks against the `Action` Protocol (§7.2).

**PEP 695 syntax throughout.** `type Tick = int`, `class Store[A](Protocol):`.
No `TypeVar(...)` declarations, no `Generic[T]` base. The floor is 3.12
precisely to allow this (§12.4).

**`type X = Y` is an alias, not a new type.** It buys readability and zero
safety. Where swapping two values would be catastrophic and both are `str` —
`entity_id` and `suspended_by` are the live example — use `NewType`:

```python
type Tick = int  # alias: Tick IS int, freely swappable
EntityId = NewType("EntityId", str)  # distinct: str is not an EntityId
EventId = NewType("EventId", str)
```

Failure mode without it: `resume_all(entity_id)` type-checks, matches no
event, silently wakes nobody, and Ivan stays suspended in the forest
forever. Nothing crashes; the world is just quietly wrong.

**`# type: ignore` does not work here.** `enableTypeIgnoreComments = false`
makes it inert, and `reportUnnecessaryTypeIgnoreComment` errors on the
leftovers. If basedpyright is genuinely wrong, the escape is
`# pyright: ignore[reportSpecificRule]` — the rule must be named
(`reportIgnoreCommentWithoutRule = "error"`) and a comment must say why.
A checker you can silence is a checker you will silence at 2am.

**No `Any` in a public signature.** A `dict[str, Any]` payload crossing the
core/game line is how game concepts leak into the library (§3). Inside a
store implementation, at the serialisation boundary, `Any` is honest.

## Making illegal states unrepresentable

**Dispatch on a closed set, and never write `case _`.** A catch-all disables
exhaustiveness checking completely — *including* one that raises:

```python
def describe(state: ActionState) -> str:
    match state:
        case Active():
            return "active"
        # add Suspended, or this is an error naming the missing member
```

Verified: with one member of the union unhandled, basedpyright reports
`Cases within match statement do not exhaustively handle all values` and
**names the type it is missing**. Adding `case _: raise AssertionError(...)`
— a branch that *looks* defensive — reduces that to **zero errors**. So the
defensive-looking version is the dangerous one: it converts a compile-time
error at every call site into a runtime crash at tick four million.

**States carry their own fields.** `ActionState` is a discriminated union of
frozen dataclasses, not an enum, because each state has different data:
`Active(due_tick)` and `Suspended(suspended_at, suspended_by, due_tick)`
(§6.4). That makes the target bug unwritable — a suspended action with no
record of when it paused, or an active one with no deadline, will not
type-check. `status` is a `Literal` so the value in Python *is* the value in
the `status` column, with no mapping layer.

## Class shape

**No Pydantic, no attrs, no anything.** `dependencies` stays empty (§1.6),
so the stdlib `@dataclass` is the modelling tool. This inverts the rule in
the reference codebase we borrowed this format from, and the inversion is
the point: their base class is a dependency they already pay for.

**`frozen=True, slots=True` by default.** Mutability is the exception and
must be justified in a comment. The state classes are frozen: a transition
*replaces* the state object rather than editing it, which is why a suspended
action cannot hold a stale `due_tick` — the object that held one is gone.
`BaseAction` itself is the justified exception, since `suspend()` and
`resume()` rebind `self.state` (§6.4).

**`@override` on every overriding method.** `reportImplicitOverride` is an
error. Failure mode: rename a method on `BaseAction` and a subclass's
override silently becomes dead code that never runs again.

**Injected, never constructed.** Persistence, clock and randomness arrive
through the constructor (§12.3). A class that reaches for a global is a
class that cannot be tested at ten million ticks.

## Public API surface

**`__init__.py` is a curated export list with an explicit `__all__`.**
Anything importable is something a consumer will import and then depend on,
and in a library a moved module path is a breaking change (§12.1). Internals
are not re-exported; they are free to move.

**No import-time side effects.** `import foliot` must not read a file, open
a socket, call `logging.basicConfig`, or consume entropy. The sharpest case
is §9.1: a `world_seed` drawn at import time would make replay depend on
import order, and the bug would surface as "determinism test fails on CI
only."

**`logging.getLogger(__name__)`, never `basicConfig`.** Configuring handlers
is the consuming application's decision. No `print` anywhere in `src/`.

## foliot-specific prohibitions

These three are the guarantees written as lint rules. Each has a worked
demonstration in `DESIGN_SNAPSHOT.md`.

**Never `import random` outside `rng.py`.** Handlers take randomness from
`ctx.rng` / `sim.rng` (§7.4). A module-level `random.random()` is a global
stream, and §9.2 traces exactly how that lets Petra's fight change whether
Ivan lives — the coupling is a cursor position that appears in no table.

**Never `hash()` on a `str`.** Salted per process by `PYTHONHASHSEED`, so
`hash("ivan")` differs between runs and replay breaks across a restart
(§9.4). Use `blake2b` or integer mixing.

**Never `time.time()`, and never `time.sleep` outside a driver.** Wall time
jumps backwards on NTP correction; `time.monotonic()` does not (§4.1). Time
enters the engine only through a `Driver`, which is what makes ten million
ticks possible in a unit test (§4.2).

## Cross-references

- `DESIGN_SNAPSHOT.md` §2 — the guarantees every rule here defends.
- `DESIGN_SNAPSHOT.md` §3 — the core/game line; what `Any` at a boundary erodes.
- `DESIGN_SNAPSHOT.md` §12.3 — library hygiene, of which this file is the code-level half.
- `docs/conventions/testing.md` — the testing half, including why mocks are banned.

## Don'ts

- Do not use `# type: ignore`. It is disabled; it silences nothing.
- Do not write `# pyright: ignore` without a named rule and a reason.
- Do not add `case _` to a `match` over a closed set, even one that raises.
- Do not put `Any` in a public signature.
- Do not use `TypeVar`/`Generic` where PEP 695 syntax works.
- Do not treat `type X = Y` as type safety. It is an alias.
- Do not add a dependency to `dependencies`. Ask first (`CLAUDE.md`).
- Do not import `random` outside `rng.py`.
- Do not call `hash()` on a string.
- Do not call `time.time()` or `time.sleep()` outside a driver.
- Do not call `logging.basicConfig()` or `print()` in `src/`.
- Do not do work at import time.
- Do not re-export an internal from `__init__.py` "for convenience."
- Do not resurrect the v1 draft's shapes from git. It is history, not a
  starting point (§19).
