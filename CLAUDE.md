# foliot

A deterministic tick-driven simulation core: durable queue, absolute
deadlines, per-entity randomness. Domain-agnostic.

## Read this first

**`docs/DESIGN_SNAPSHOT.md` (v2) is the design.** Read it before
proposing or writing anything. Start with §2 (the guarantees), then §3
(core vs game), then §18 (status) and §13 (build order).

It marks every item **decided**, **recommended**, or **open**. Honour
those labels. A recommendation is an argument I have not yet accepted.

**§19 lists what changed from v1 and why.** If you have seen an older
version of this project's design, several things were reversed — some of
them twice. Check §19 before repeating an argument I have already
settled.

## What foliot is

A clock, a queue, a way to look up what to run, and a source of
addressable randomness. It has never heard of a target, location,
environment, activity, or combat rule.

The library's value is in five guarantees, not in its object graph:

1. Same seed, same history.
2. Reordering the queue cannot change outcomes.
3. The clock does not drift.
4. Nothing pending is lost; nothing is applied twice.
5. Time is injectable — ten million ticks in a unit test.

Every design question ultimately reduces to whether it protects these.

Exactly one active simulation runner advances a world. Many API processes and
a standby/failover runner are fine, but simultaneous tick runners, sharding,
worker claims, and `SKIP LOCKED` coordination are outside the supported
architecture. Order-independence protects replay and iteration stability, not
parallel execution.

## Scope

**This repo is the library only.** It is consumed by a separate
zero-player game project, but it must not know that game exists — no
characters, no HP, no items, no combat rules, no inventory, and no targets.
If a design question can only be answered by reference to a specific
game, it is out of scope here.

**The membership test for anything in `src/foliot/`:** *does the engine
read or enforce it?* If not, it is game payload. A game can make `target_id`
mandatory once in its own `GameAction(BaseAction)` without putting target
semantics into every simulation (§3, §7.2).

**The membership test for a feature:** *does it add a guarantee the
consumer cannot easily provide themselves?* If it is merely vocabulary
or convenience, it belongs in the game.

The game is recorded in §11 as the source of requirements. It is not a
dependency and not a concern.

## Two layers

- **Layer 1** — clock, queue, drivers, RNG, store, actions. Must be
  complete and useful **on its own**.
- **Layer 2** — event-bound intents and resolvers. A separate importable
  module, built after layer 1 is real.

If layer 1 cannot be used without layer 2, we have built a framework with
a mandatory opinion. There is one explicit, narrow connection: the single
`Simulation` may receive an optional events collaborator. With none supplied,
it imports no concrete event implementation and performs no event work; the
ordinary action/effect/scheduling path and both drivers remain unchanged. Do
not grow this into a generic module or plugin framework.

## The v1 draft is gone

`docs/reference/protocols-draft.py` was deleted at M1. Its shape was
wrong in every load-bearing way — data-only actions with a `kind`
registry, core-owned target behaviour, `Any` where the world type belongs —
and keeping it meant keeping a wrong shape in the tree for someone to
copy. The two ideas worth having are in §10.5; the rest is in git
(`git show 1d22a00:docs/reference/protocols-draft.py`).

## Open questions — ask, do not decide

Two architectural questions remain genuinely undecided. Raise them; do not
settle them in a commit:

1. **Log/journal table design** (§15) — the largest table, and in a ZPG
   the log *is* the product.
2. **Flask vs FastAPI** — irrelevant to the library; deferrable forever.

M6 closed realtime timing: the first tick is immediate; targets stay on a
fixed monotonic cadence; an overrun skips missed wall slots but never logical
ticks; every overrun emits one operational warning; downtime pauses simulation
time; and `RealtimeDriver` runs until the application interrupts it. Its public
constructor exposes only `tick_seconds`; clock and sleep seams stay private.

M7 is the in-repository `examples/tinyworld` public-API proof. Its game-owned
`GameAction(BaseAction)` makes non-null `target_id` mandatory for every
Tinyworld action. `Walk` targets the forest while carrying a separate
destination clearing; arrival queues `Rest` against that clearing, and `Rest`
owns healing and asks game-specific `Pathing` for both the nearest POI and its
connecting environment before the next walk. Clearing entities contain no
navigation policy. Only a moonlit-clearing rest may heal. The fixed-seed
example has completed two independent one-million-tick runs with identical
results. M8's optional Event layer is now implemented; the same million-tick
Layer-1 result and journal digest remain unchanged.

M9 is the separate `examples/eventworld` public-API proof for Layer 2. The
forest's game-owned response creates the Fight Event and its temporary wolf,
then explicitly suspends Lira's walk. Lira and the wolf choose fresh Intents
each round; the concrete FightEvent owns hit/dodge/heal/escape rules and emits
the next children. A game-owned finalizer observes post-effect HP, deletes a
dead entity's actions, and explicitly ends the Event. The default fixed-seed
story kills the wolf, resumes the same walk, shifts its arrival deadline by
the pause, and reaches the moonlit clearing at tick 16. No foliot API was
added for the example.

M8 also adds one small optional Layer-1 lifecycle seam. A
game-supplied `TickFinalizer` runs after all valid ordinary-Action and Event
Outcome effects and before commit, sees the post-effect world through a narrow
collecting context, and may emit
effects, schedule or delete actions, delete every action owned by an entity,
and log. `Txn.delete_owned_by(entity_id)` covers active, suspended, and newly
scheduled actions in the same transaction; it never follows game-owned
targets. Foliot does not define death or HP. A finalizer exception aborts and
rolls back the whole tick.

When finalization must end an Event—for example, post-effect state says Lira
died—the game explicitly imports `end_event` from `foliot.events` and calls
`end_event(ctx, event_id)`. It uses the same atomic cleanup path as
`Outcome.end()`: remove the Event, current or newly staged children, and its
ephemeral payload, then resume work suspended by that Event. Generic
`delete_owned_by(entity_id)` never closes an Event secretly.

An M8 Event attempt is atomic across its expected participants. If one
EventAction fails or supplies no intent, none of that Event's current-tick
contexts or intents survives, every participant action stays queued, and all
participants decide again with fresh contexts on the next tick. The persisted
Event stays open; partial intents never bridge two tick snapshots.

In the motivating game an EventfulEnvironment is the only Event producer. Its
fight response creates both an explicit Event and its ephemeral wolf, then
explicitly suspends the interrupted entity's suspendable actions using that
Event id and schedules the exact child EventActions for both parties. Those
children—not a second list of entity ids—define which intents the Event
expects. The wolf's identity derives from the Event and its state lives with
that persisted Event, not as an independent permanent-world entity. Event
opening does not automatically suspend anything; that request is explicit
FightEvent policy. The producer restriction is also game policy—foliot's
optional layer must not require every simulation to have environments.

The Event persists across combat rounds; its child EventActions are one-shot.
Each child decides one current-round Intent from current state. After a
successful resolution, a continuing Event consumes those children and
schedules fresh child objects for the one strictly future tick named by its
Outcome, giving the next decisions new permanent `seq` values and access to the
prior round's applied state. Only an incomplete attempt retains and retries the
same pending children. Do not reuse one EventAction object across successfully
resolved rounds merely to reduce queue churn.

Game EventActions implement `decide(DecisionContext) -> Intent`; the library
base supplies their required `process` bridge. `DecisionContext` exposes only
`tick` and the EventAction-bound `rng`—no effects, scheduling, finish, log,
world, store, or Event registry. The returned game Intent is exactly one
choice; foliot attaches `event_id` and source action `seq`. Game state needed
for the decision remains explicit action payload, following the same rule as
Layer 1.

Shared resolver randomness belongs to the Event, not either participant. A
minimal `ResolutionContext` exposes `tick` and a counter-based `rng` derived
from `(world_seed, event_id, tick)` under an explicit event-resolution domain
tag. Participant RNGs choose their intents; the Event RNG resolves shared
uncertainty such as hit versus dodge. Agility, speed, HP, and every probability
formula remain game code, followed by the explicit `rng.random() < chance`
comparison.

Each concrete game Event owns `resolve(ctx, intents) -> Outcome`. Foliot calls
that method directly and has no resolver registry, kind lookup, or required
resolver object. A complex Event method may delegate to an explicit game rules
service, just as an Action may delegate ordinary game logic.

An Outcome states Event lifecycle explicitly. A continuing round returns
`Outcome.continue_with(...)` and supplies the fresh child EventActions for the
next round together with one required, strictly future `due_tick` shared by
all of them. A finished interaction returns `Outcome.end(...)`; wolf escape
is the motivating example. Never infer closure merely from an empty child list.
After a successful ending Outcome is applied, foliot removes the Event and
automatically resumes every action whose `suspended_by` is that Event id. Event
opening still never suspends automatically; closing only settles suspensions
that the same Event actually created. The Event, its current child actions, and
all Event-owned ephemeral payload disappear together, just as a finished
Action disappears. Committed effects and journal history remain. A game that
wants an ephemeral entity to persist must explicitly create permanent game
state before ending.

`BaseEvent` is mandatory, following the architectural pattern of
`BaseAction`: the library base owns only the stable Event id, exact current
child set, replacement after a continuing round, and removal lifecycle; game
subclasses keep all payload and `resolve(...)` behaviour on the same object.
Events are persisted but not queued, so do not copy `due_tick`, suspension, or
`ActionState`. Presence means open and removal means closed; no status enum or
tombstone.

A BaseEvent is constructed with its final deterministic `event_id`; the store
does not allocate or replace it and rejects duplicates. The id must pre-exist
so the game can derive event-owned entity ids, construct child EventActions,
and request suspension by that handle before commit. Children still receive
their action `seq`s on admission. Literal `"fight:..."` / `"wolf:..."`
f-strings are examples only, not the API. Games declare stable
`EventIdTemplate(namespace)` and `EntityIdTemplate(namespace)` constants once,
then call `from_action(action, tick=..., ordinal=...)` and
`from_event(event_id, ordinal=...)`. Namespaces are game schema identifiers,
never inferred class names. Outputs are opaque typed strings from a versioned,
length-framed stable digest, never `hash()`; the implementation needs golden
vectors because the encoding is a replay promise.

Opening is one Layer-2 request: `open_event(event, due_tick)` persists the
BaseEvent and event-owned payload, admits every declared child at the same
strictly future tick, binds them normally, and makes those exact children the
expected set. Do not make games schedule children or maintain expected ids
separately. Suspension remains an independent explicit request; opening an
Event never implies it. `TickContext` remains free of Event methods. A game
opts into the feature explicitly with `from foliot.events import open_event`
and calls `open_event(ctx, event, due_tick=...)`; the function uses the Event
collaborator attached to that simulation. Calling it without Event support
configured raises a clear configuration error. Do not add `ctx.open_event()`,
an `EventContext`, or an Event-producing action base class.

Every participant decision and resolver formula reads tick-start state. If
Poison contributes damage in the same tick as a fight round, that damage does
not alter the round's HP-dependent probabilities; it applies afterward and is
visible to the next round. This is the concrete consequence of the frozen
decision/resolution phase and must not be replaced by effect priority or
mid-tick mutation.

Resolver failures are isolated per Event because resolution is still pure
decision work. Report an operational `ERROR` with tick, Event id, resolver,
and traceback; discard the whole Event attempt and any partial Outcome; retain
the Event and its exact current children; let unrelated work commit; and retry
fresh next tick. If an Effect from a valid Outcome raises during application,
the failure is instead tick-fatal and the transaction rolls back.

Layer-2 persistence extends the same physical tick transaction. Optional
`EventStore` / `EventTxn` protocols add Event capabilities structurally; one
event-enabled game adapter implements them together with ordinary `Store` /
`Txn`, so Event state, ephemeral payload, queue changes, world effects,
journal, and clock commit together. No-event adapters remain unchanged, and
normal `MemoryStore` stays event-unaware; an event-capable in-memory variant
belongs under `foliot.events` as `EventMemoryStore`. Its `EventStore.event()`
read and `EventTxn.event_open()`, `event_continue()`, and `event_end()` writes
extend the same core store and transaction protocols. Games opt in with
`Events(store)` passed to the existing `Simulation`; the collaborator rejects
a different store because that would break the one-transaction guarantee.

There is one simulation engine, not a parallel `EventSimulation`. A consumer
that does not import or supply `foliot.events` uses `Simulation(store)` and the
engine never attempts resolution. A consumer that opts in supplies the event
collaborator to the same `Simulation`; ordinary actions, effects, schedules,
logs, finalization, drivers, and tick atomicity behave exactly as before.
Plain `import foliot` does not import `foliot.events`; the boundary is real as
well as absent from the root public API.

Everything else in v1's open list is now settled. Immediate milestone-level
interface choices may still be called out under §18's "Immediate next work";
discuss those with the owner too. See §18 and §19.

## Tooling

**`uv` for everything.** No bare `pip`, no hand-rolled venv. `uv sync`,
`uv add`, `uv run`, `uv build`, `uv publish`. `uv.lock` is committed.
`uv` changes fast — check current syntax against its docs rather than
assuming.

Python 3.12+ floor, 3.14 dev interpreter, `src/` layout, `pytest`,
`ruff`, `basedpyright` in strict mode on `src/` and `tests/`. Strict typing is not
optional: the design uses structural ports plus a mandatory `BaseAction`,
and neither contract is useful without a type checker. `# type: ignore` is
disabled outright
(`enableTypeIgnoreComments = false`) — a checker you can silence is a
checker you will silence.

Ship `src/foliot/py.typed` or consumers get no type information at all.

`dependencies` must stay empty. Database adapters belong to the consuming
game (or an independent integration package), not to foliot: the library
supplies the `Store` / `Txn` protocols and its built-in `MemoryStore` only.

## Code conventions

Two files under `docs/conventions/`, subordinate to the design doc:

- **`python-style.md`** — type discipline, exhaustive `match` with no
  catch-alls, class shape without Pydantic, public-API surface, and the
  three foliot-specific bans (`random` outside `rng.py`, `hash()` on a
  string, `time.time()` outside a driver).
- **`testing.md`** — the four architectural tests, and why
  `unittest.mock`, `pytest-mock`, `monkeypatch` and `freezegun` are
  forbidden outright.

Every rule there names the failure it prevents. A rule that cannot name
one should be deleted rather than obeyed.

## How I like to work

- **Argue with me.** Several of the better decisions came out of
  push-back, in both directions — I overturned the sampled-waits
  recommendation, and I was talked out of a global RNG. If something I
  say is wrong, say so and say why.
- **Show me, don't tell me.** When I say I don't understand, the answer
  is a runnable example — a benchmark, a traced failure, ten lines I can
  read — not a restatement in different words. Abstract arguments have
  repeatedly failed here where a concrete demonstration landed at once.
- **Explain mechanisms, not just conclusions.** If a recommendation rests
  on a fact — drift at 2 ms/tick, `PYTHONHASHSEED` salting string
  hashes, a Mersenne Twister costing 118× a counter-based draw — show the
  reasoning with numbers.
- **Small, reviewable steps.** I am learning library authoring; a large
  correct diff teaches me less than a small one I can follow.
- **Verification can be delegated.** After I approve an implementation, you
  may run its `uv` formatting, lint, type-check, and test commands and report
  the results. Ask before dependency changes, publishing, or other commands
  that expand scope.
- **Ask before scope grows.** New files, new dependencies, new
  abstractions: check first. Dependencies especially.

## Keeping the design doc alive

`docs/DESIGN_SNAPSHOT.md` is version-controlled memory, not an archive.
When an open question is resolved, update it in the same commit as the
code and move the item to **decided** in §18 with a one-line note on why.

When a decision is *reversed*, add a row to §19 with the old position,
the new one, and the reason. Losing the reasoning is the only real cost
of overwriting a document.

If we contradict the document, the document is what gets fixed.
