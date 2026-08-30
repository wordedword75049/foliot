# foliot — Design Snapshot

> **What this document is.** A record of a design conversation, intended as
> handoff memory for a fresh session where actual repository work begins.
> It captures not just conclusions but the reasoning behind them, the
> alternatives considered and rejected, and — importantly — the questions
> that are still genuinely open. Where something was proposed but not
> confirmed by the project owner, that is marked explicitly. Do not treat
> recommendations in this document as settled decisions.

## Start here

If you are a fresh session picking this up, read in this order:

1. **§18 Status summary** — what is decided, what is merely recommended,
   what is open. Sixty seconds, and it prevents the most likely failure
   mode: treating a recommendation as a decision.
2. **§7 The framework layer** and **§16 the protocols appendix** — the
   actual contracts. This is the design.
3. **§11 Build order** — what to write first.
4. **§15 Working notes** — how the owner prefers to work. Worth reading
   before proposing anything.

**Three things that will otherwise trip you up:**

- The **rendezvous problem (§8.1)** is unresolved and **blocks the
  registry**. It determines whether `Event` is persisted or ephemeral,
  which propagates into the schema. Settle it before writing the loop.
- **Scope is the core library only (§6.3).** The owner explicitly
  deprioritised game-domain modelling — items, inventory, combat rules,
  enchantments. Do not drift into it.
- The **per-entity RNG recommendation (§5.2)** was argued for but not
  confirmed, and the owner's stated preference was a global RNG. Do not
  silently implement either one; raise it.

## Contents

| § | Section |
|---|---|
| 1 | Project context |
| 2 | Fundamental simulation decisions |
| 3 | The clock |
| 4 | The queue |
| 5 | Determinism and randomness |
| 6 | Entity and state model |
| 7 | The framework layer |
| 8 | The open question the conversation paused on |
| 9 | Naming |
| 10 | Repository layout |
| 11 | Build order |
| 12 | Testing strategy |
| 13 | Postgres schema sketch |
| 14 | Glossary |
| 15 | Working notes |
| 16 | Appendix: `foliot/protocols.py` |
| 17 | Library hygiene notes |
| 18 | Status summary |

---

## 1. Project context

### 1.1 What is being built

Two separate projects, deliberately split:

1. **The core library** — a general-purpose, domain-agnostic clock /
   queue / event-processing engine. This is the primary interest. The
   owner has not written a library before, so library-shaped concerns
   (packaging, public API surface, testability, no import-time side
   effects) are part of the learning goal, not incidental.

2. **The game** — a zero-player game (ZPG) that consumes the library.
   Reference points: **Godville** and **The Tale (Сказка)**. A player
   creates a character and then *observes*; the character and the world
   live on autonomously on a server. The player is a spectator, not an
   agent.

The library is the deliverable being designed here. The game is the
motivating use case and the source of requirements, but the library must
not know anything about it.

### 1.2 Why the split matters architecturally

In a ZPG the simulation **is** the entire product. There is no player
input to hide latency behind, no interaction loop to paper over gaps in
the world model, and no way to defer content generation to the user. The
log of what happened is the thing players consume. This raises the
stakes on three properties that would be minor in a conventional game:

- **Durability** — the world runs for months. Losing pending state is
  unrecoverable, because the history is the content.
- **Explicability** — players will ask "why did my character die here?"
  and the system must be able to answer. This drives the determinism and
  replay requirements.
- **Continuous operation** — see §2.1.

### 1.3 Stack

Chosen for owner familiarity and enjoyment, which is the right criterion
for a hobby project:

- **Python**
- **PostgreSQL**
- **Flask or FastAPI** (undecided, and doesn't matter yet — the web layer
  is downstream of everything in this document)

The core library should have effectively **zero dependencies**. Anything
Postgres-shaped belongs in an extras group or a separate package. This
is what makes it a library rather than a game with a tidy folder layout.

### 1.4 Non-technical context (brief)

Personal, non-commercial hobby project, entirely unrelated to the
owner's employment. Personal use of company tooling is sanctioned by
their employer. One thing flagged and worth keeping in mind: employment
agreements sometimes contain invention-assignment clauses covering work
created "using company resources or systems," which can create IP
ambiguity for side projects built through employer-provided tooling. Not
a concern for a hobby repo, potentially relevant if the project ever
becomes something. Not legal advice; worth a look at the actual
agreement if it ever matters.

---

## 2. Fundamental simulation decisions

### 2.1 Continuous simulation, not lazy materialisation — **DECIDED**

The alternative considered was *lazy* simulation: store `last_simulated_at`
per character and materialise the intervening events only when someone
opens the page. That costs nothing for idle characters.

**Rejected** because it makes cross-character interaction genuinely hard.
Two characters meeting, a shared economy, or world-scale events would all
require force-materialising every participant, and the complexity
compounds. Continuous is what Godville does, and it is the easier
long-run bet for any shared-world or social layer — even though it burns
cycles on behalf of nobody.

**Consequence:** the engine must run whether or not anyone is watching,
which makes the clock a real service with real uptime concerns.

### 2.2 Tick-based, ~1 second per tick — **DECIDED (owner's design)**

The world advances in discrete ticks of roughly one second. Tick
duration is a configuration value, not a constant baked into logic.

### 2.3 Load profile — **ESTABLISHED**

Throughput is a non-issue and should not drive design. Godville
generates roughly one event per minute per hero. Ten thousand characters
is under 200 events/second, which is nothing for Postgres.

What *does* matter is **per-character isolation**: character A's
simulation must never affect character B's outcomes. That property is
what permits out-of-order processing, retries, and sharding. Optimise
for isolation and determinism, not for raw throughput.

---

## 3. The clock

### 3.1 Owner's original design

An ever-running clock. Each tick:

1. Process every action in the current tick's queue.
2. Each action optionally produces the next action, pushed into the next
   tick's queue.
3. When the current queue is exhausted, sleep for
   `tick_duration - processing_time`.
4. Promote the next-tick queue to current. Repeat.

This is sound as a skeleton. Three refinements follow.

### 3.2 Absolute deadlines, not relative sleeps — **RECOMMENDED, EXPLAINED, NOT EXPLICITLY CONFIRMED**

`sleep(tick_duration - processing_time)` accumulates drift. Between
waking, measuring, and sleeping again there is overhead — OS scheduler,
instrumentation, interpreter. The real period is `1.000 + ε`, not
`1.000`. At ε = 2 ms that is ~173 seconds lost per day; after a month
the world clock is roughly ninety minutes behind wall time, silently.

Fix — target fixed wall-clock moments so errors don't compound:

```python
start = time.monotonic()
while True:
    process_tick(n)
    n += 1
    deadline = start + n * TICK_DURATION
    time.sleep(max(0, deadline - time.monotonic()))
```

Use `time.monotonic()`, never `time.time()` — the latter jumps on NTP
correction and can go backwards.

### 3.3 Catch-up policy — **OPEN QUESTION, MUST BE DECIDED**

When `process_tick` takes longer than a tick, `max(0, ...)` returns zero
and the system is behind. Two policies, both defensible, with different
failure modes:

| Policy | Behaviour | Failure mode |
|---|---|---|
| **Catch up** | Run ticks back-to-back until caught up. World time stays pinned to wall time. | Under sustained overload, the catch-up work itself causes more lag — a death spiral. |
| **Let it lag** | World time falls permanently behind real time. Smooth, no spikes. | "5 minutes ago" in the log stops corresponding to 5 minutes ago in reality. Diverges slowly and invisibly. |

A hybrid is possible: catch up, but with a bounded budget (e.g. never
run more than N ticks back-to-back) and an alert when the lag exceeds a
threshold. This needs deciding because it leaks into the loop structure.

### 3.4 Downtime handling — **OPEN QUESTION**

After four hours of downtime, what happens? Three options, all
defensible:

- **Fast-forward** — replay every missed tick. Expensive but faithful.
- **Compress** — summarise the gap into a digest event.
- **Shift the world clock** — declare that no in-fiction time passed.

The choice affects the core's design, so it should not be deferred.
Related requirement: **`current_tick` must be persisted**, since on
restart the engine needs to know whether it is resuming or
fast-forwarding.

### 3.5 Pluggable drivers — **RECOMMENDED**

The clock's *advancement mechanism* should be an injected dependency:

- **`RealtimeDriver`** — sleeps to absolute deadlines. Production.
- **`ManualDriver`** — advances instantly. Tests, fast-forward, replay.

This single split is what lets ten million ticks run in a unit test in
under a second. Hardcoding `time.sleep` into the loop makes the library
untestable, and that is felt immediately.

---

## 4. The queue

### 4.1 Scheduling into arbitrary future ticks, not just N+1 — **RECOMMENDED, PARTIALLY CONTESTED**

The owner's original design has each action scheduling only into tick
N+1. The objection: most activities take far longer than a second —
walking is minutes, sleeping is hours, a journey is days. Next-tick-only
means an entity wakes every tick just to report "still walking," and at
scale ~99% of processed actions are no-ops.

**Owner's counter-argument (valid and important):** action duration is
governed by external logic — the map knows where the forest ends — and
more critically, *putting an action to sleep appears to eliminate the
per-step random encounter roll*, which is rolled every step-tick.

The second half of that objection is answered in §4.2; it turns out to
be a false trade-off. But the first half stands: durations come from
domain logic, and the engine should accept whatever duration it is
given.

**Resolution:** let an action schedule into any future tick. The queue
becomes `due_tick -> [actions]` (a timing wheel) rather than a single
next-tick list. This is the existing design plus a `due_tick` field. In
Postgres it falls out as:

```sql
WHERE due_tick <= :current_tick AND status = 'pending'
```

A character sleeping until dawn costs one queue row for eight hours of
world time instead of 28,800.

### 4.2 Sampled waits instead of per-tick rolls — **KEY INSIGHT, ACCEPTED IMPLICITLY**

This is the piece that dissolves the objection above, and it is probably
the single most load-relevant idea in the whole design.

You do not need to wake up every tick in order to roll dice every tick.
Instead of rolling probability `p` each tick, **sample how many ticks
until the next success** and schedule that directly. The waiting time is
geometrically distributed:

```python
k = math.floor(math.log(random.random()) / math.log(1 - p)) + 1
```

This is **not an approximation**. Geometric *is* the distribution of
"number of Bernoulli trials until first success," so the outcome
distribution is identical to rolling every tick. One queue entry
replaces thousands.

So walking through a forest schedules two events:

- `encounter` at tick `N + k` (sampled)
- `arrive` at tick `N + m` (from map/domain logic)

Whichever fires first wins; the other is tombstoned (§4.3).

**Critical caveat:** this is only valid while `p` is constant. If
conditions change — dusk falls and encounter rates rise — the pending
roll must be invalidated and resampled under the new `p`. That is fine:
condition changes are themselves scheduled events, and they are rare
compared to ticks.

**Generalisation:** the library should expose a family of sampled waits
(`after_geometric(p)`, `after_uniform(lo, hi)`, `after(n)`, `at(tick)`)
as the vocabulary handlers use to express *when* the next thing happens.
This is where the "chances and durations" framework the owner wants
actually lives.

### 4.3 Tombstoning, not removal — **RECOMMENDED**

Events get invalidated: the encounter fires, so the arrival is moot; the
target dies in another event first. Marking the row dead and skipping it
on pop is simpler than removing from a heap, and it preserves an audit
trail of what *would* have happened. `ActionStatus.CANCELLED` serves
this.

An important consequence of §4.2 + §4.3 together: **an entity routinely
has multiple pending actions at once.** This directly contradicts
storing "current action" as a single field on entity state (see §6.2).

### 4.4 Durable queue, memory as cache — **RECOMMENDED**

The world runs for months. An in-process heap dies with the process, and
"server restarted, all pending events vanished" is unrecoverable when
the history is the content.

Shape:

- **Postgres table is the source of truth** — `(id, entity_id, kind,
  due_tick, payload, status, seq)`, indexed on `(due_tick, status)`.
- **In-memory heap/buckets as a read-through cache** of the near horizon
  — pull the next N minutes, work from memory, write results back
  transactionally.
- **`SELECT ... FOR UPDATE SKIP LOCKED`** for claiming work. This
  handles the single-worker case fine and permits adding a second worker
  later without redesign.

**Consequence — idempotency.** Durability means crash-between-"handler
ran"-and-"event marked done" is a normal occurrence. At-least-once
delivery means handlers must be idempotent, or processing must be
transactional (effects and status flip in one transaction). Otherwise
the hero occasionally kills the same rat twice. This must be designed
in, not patched later.

**Library boundary:** persistence is an *interface* in the core, not an
implementation. The library ships an in-memory store; the game plugs in
a Postgres one. That boundary is what keeps the library reusable.

---

## 5. Determinism and randomness

### 5.1 Two-phase tick: decide then apply — **ACCEPTED ENTHUSIASTICALLY**

**Problem.** If a beast and a character both act in tick 5000, whoever
is processed first sees a clean world and the second sees the first's
mutations. Outcomes then depend on arbitrary queue ordering.

**Solution.** Split the tick:

1. **Decide phase** — every due action reads a *frozen snapshot* of
   tick-start state and emits **intents**. No mutation whatsoever.
2. **Apply phase** — all intents are resolved together; only this phase
   writes.

This makes ticks order-independent, which in turn permits
parallelisation, retries, and replay without changing results.

**Owner's extension (good, and adopted):** intents involving multiple
parties should link to a shared **Event** — e.g. a fight with two
participants — where intents are locked in first and then resolved
*inside* the Event. This is the basis of the framework in §7.

**Additional requirement identified:** a resolver must **re-validate its
participants** at resolution time. Between decide and resolve, a
participant may have died in a different event during the same tick.
Degrade gracefully; never assume both parties still exist.

### 5.2 Per-entity RNG streams — **RECOMMENDED, OWNER INITIALLY DISAGREED, LIKELY A MISUNDERSTANDING**

**Owner's position:** use a global, fully random RNG, so that walking
through the forest now and in ten million ticks produces completely
different outcomes.

**The misunderstanding:** per-entity seeding does *not* mean repeated
situations produce repeated results. The tick is part of the seed:

```python
rng = random.Random(hash((world_seed, entity_id, tick, event_seq)))
```

Tick 500 and tick 10,000,000 give completely different streams. The same
path walked a million ticks later yields a different outcome — exactly
what the owner wants. Unpredictability across time is fully preserved.

**What per-entity seeding buys:** outcomes stop depending on *processing
order*. With a shared global RNG, whichever entity is processed first
consumes the next value, so:

- Tick processing **cannot be parallelised** without changing outcomes.
- A failed handler **cannot be retried** — it consumes different values
  on retry.
- Bugs **cannot be reproduced**. "My character died strangely around
  tick 4.2 million" becomes uninvestigable.

The last point is the real cost, and it bites hardest in exactly this
genre: when the log is the product, players *will* ask why something
happened, and the system should be able to replay it and answer.

Note also that this directly undermines the order-independence won in
§5.1 — a global RNG reintroduces order-dependence through the back door.

**Mundane but real:** Python's module-level `random` is not safe to
share across threads or workers. This becomes a correctness bug the
moment the system scales past one process.

**Status:** explained but not explicitly agreed. Worth revisiting early,
because retrofitting deterministic RNG is painful and the whole replay
story depends on it.

---

## 6. Entity and state model

### 6.1 Owner's model — **DECIDED (owner's call)**

- **`entity`** — the character's slow-changing characteristics: level,
  max HP, skill points, learned skills.
- **`entity_state`** — current HP, current mana, cooldowns, inventory,
  and the current Action.

The two-tier split is reasonable and the table boundary is sensible.

### 6.2 Corrections and cautions

**(a) "Current Action" as a single field cannot hold the design.**
Per §4.2 and §4.3, an entity routinely has several pending events at
once: a sampled encounter, an arrival, a cooldown expiry, a hunger tick,
a poison effect. A single field holds one.

The queue therefore wants to be **its own table** with `entity_id` as a
foreign key, not a field on the entity. The scheduler's hot query
("everything due at or before tick N") then hits one index on one table
instead of scanning every entity in the world.

There is still a good reason to keep something action-shaped on
`entity_state`: the observer needs to read *"Ivan wanders through the
pines."* But that is a **denormalised display cache** of current
narrative activity — explicitly not the scheduling source of truth.
Keeping the two roles distinct is what stops them silently drifting
apart.

**(b) The "static" tier is not static.** Level, max HP, skill points and
learned skills all change on level-up or training. That is fine as a
table split — they change on the order of days, not seconds — but it is
a *slow/fast* distinction, not *immutable/mutable*. Naming it honestly
matters: internalising "entity is immutable" leads to aggressive caching
and stale max-HP after a level-up.

**(c) Inventory as a field will strain — DEFERRED BY OWNER.**
An item dropped in a clearing, or traded between characters, has no home
in an inventory blob; a transfer becomes two blob rewrites with nothing
enforcing that the sword exists in exactly one place. If items ever have
identity (durability, provenance), they want their own table with a
nullable owner (`NULL` = on the ground / in a container).

**Owner explicitly deprioritised this**, and rightly so — it is
game-modelling, not core-library work. If inventory stays
`{"herbs": 3}`, a JSON column is fine. Recorded only so the constraint
is known when it eventually matters.

### 6.3 Scope discipline — **EXPLICIT OWNER INSTRUCTION**

The owner pushed back on discussion of enchantments, item identity, and
similar game-domain modelling. The instruction is clear and should be
respected in the next session:

> Build the **core** — the clock, the queue, the calling and resolving
> mechanism, and a framework for events with chances and durations —
> **with nothing particularly tied** to any specific game.

Game-domain modelling is out of scope until the core exists.

---

## 7. The framework layer (entity / action / intent / event / resolver)

This is the layer that makes the project a framework rather than a loop.
A protocols module was drafted; see §9 for the full source.

### 7.1 The governing discipline

**The core never knows what an entity is.** No HP, no position, no
Postgres. It knows about ticks, scheduled actions, intents, resolution,
and randomness. The game supplies meaning by registering deciders and
resolvers and by implementing `Effect` and `World`.

### 7.2 Pipeline

```
queue.due(tick)                -> [Action]
  for each action: decide()    -> [Intent]     (reads frozen state)
  group intents by event_key   -> [Event]
  for each event: resolve()    -> Outcome      (the only writer)
  apply effects, enqueue schedules, tombstone cancels
```

The decide phase never mutates. The resolve phase never reads anything
it was not handed. That split is what makes ticks order-independent.

### 7.3 The pieces

**`Action`** — the unit the queue stores. **Data only, no behaviour**:
`kind` is looked up in a decider registry. This keeps actions trivially
serialisable, which is precisely what lets the queue live in Postgres
instead of process memory. Carries a `seq` field for deterministic
tie-breaking within a tick — ordering must be *total and deterministic*
even though resolution is order-*independent*, so that replays match
exactly.

**`Intent`** — what an entity wants to do, before anyone knows whether
it succeeds. Emitted by deciders.

**`Event`** — a bundle of intents sharing an `event_key`, resolved
together as one unit.

**`Outcome`** — everything a resolver produces: `effects`, `schedules`,
`cancels`, `log`. Purely descriptive; no side effects.

**`Effect`** — a game-defined mutation object with `apply(world)`.
Effects are the only thing permitted to write.

**`Decider`** — `(ctx, action) -> Iterable[Intent]`, registered against
`Action.kind`.

**`Resolver`** — `(ctx, event) -> Outcome`, registered against
`Intent.event_kind`.

**`Rng`** — per-entity, per-tick randomness, including the sampled waits
from §4.2.

**`Entity`** — deliberately almost empty (just `id`). The engine only
ever needs to *identify* an entity, never to interpret it.

**`World`** — read access, required to present a *stable* view for the
duration of a tick's decide phase. How (snapshot, copy-on-write, MVCC
transaction) is the implementation's problem, not the interface's.

### 7.4 Two deliberate design calls that may want reversing

**Effects as objects rather than direct mutation.** Costs a layer of
indirection. Buys: resolvers stay pure and unit-testable (call with a
fake world, assert on the return value), effects can be logged as an
audit trail of *why* state changed, and application order becomes
explicit rather than incidental.

**`log` as a first-class `Outcome` field** rather than derived from
effects. Justification: in a ZPG the log *is* the product, so
observer-facing narrative deserves its own channel rather than being
scraped out of mutations afterwards.

### 7.5 Handler contract conventions

Two conventions carry most of the testability:

- Handlers **return** scheduling requests rather than reaching into the
  queue.
- Handlers get randomness from **`ctx.rng`**, never the `random` module.

Together these make a handler a plain function that can be called in a
test with a fake context and asserted on by return value, rather than
something observable only by running the world.

### 7.6 Sketch of the intended public API

```python
sim = Simulation(seed=42, store=InMemoryStore())

@sim.handler("wander")
def wander(ctx, payload):
    return [
        ctx.schedule("encounter", after=ctx.rng.geometric(p=0.01)),
        ctx.schedule("arrive", after=payload["distance"]),
    ]

sim.run(RealtimeDriver(tick_seconds=1.0))     # production
sim.run(ManualDriver(until_tick=10_000_000))  # tests, fast-forward
```

---

## 8. The open question the conversation paused on

### 8.1 The rendezvous problem — **UNRESOLVED, BLOCKS THE REGISTRY**

`event_key` is the entire grouping mechanism. Intents emitted in the
same tick sharing a key are bundled into one Event and resolved
together; that is how a character's "swing at the beast" and the beast's
"bite the character" become a single fight instead of two independent
resolutions. Solo intents get a synthesised unique key, so a lone action
is just a one-participant event — no special case in the engine.

**But: how does the beast know which key to use?** Two viable answers,
and they are materially different systems:

**Option A — symmetric derivation.** Both parties independently compute
the same key from shared facts, e.g. `combat:` plus the sorted pair of
entity IDs. No coordination needed. But it only works when both sides
genuinely decide to engage each other *in the same tick*; near-misses
produce two solo events instead of one fight.

**Option B — explicit event entity.** One party opens a combat, receives
an id, writes it somewhere the other can observe it, and subsequent
intents reference it. This matches the owner's earlier instinct, handles
multi-tick fights and bystanders joining mid-fight, but the engine now
needs a notion of **event lifetime** and something must close it.

**Assessment offered:** likely Option B for fights, Option A for
incidental interactions.

**Why this must be decided before the registry is written:** it
determines whether `Event` needs to be **persisted at all**, or can
remain a per-tick ephemeral bundle. That is a load-bearing distinction.

### 8.2 The immediate next piece of work

The **registry plus the tick loop** — the code that walks
decide → group → resolve → apply. The owner was offered the choice of
building this or settling §8.1 first, and paused the conversation here to
capture this document.

---

## 9. Naming — **DECIDED**

The library is called **`foliot`**.

A foliot is the oscillating crossbar in the earliest mechanical clocks —
the bar that swings back and forth and lets the gear train advance one
step at a time. It is, literally, a tick generator, which is what this
library is. Six letters, unambiguous spelling, no ecosystem collisions,
reads cleanly as `import foliot`, and obscure enough that it belongs to
this project on first use.

PyPI name was free at time of writing. Claiming it is cheap insurance
even if nothing is ever published; the GitHub namespace is separate and
should be checked independently.

**Rejected, recorded so they are not re-litigated:**

- `escapement` — best metaphor available (converts continuous energy
  into discrete ticks), but **taken on PyPI**.
- `mainspring` — good metaphor and free, but "spring" reads as Spring
  Framework / Spring Boot to anyone from the JVM world. **Rejected by
  the owner on those grounds.**
- `cadence` — free on PyPI, but Uber's Cadence is a well-known durable
  workflow-and-timer engine in the same problem domain (now Temporal).
  Namespace confusion.
- `clepsydra` — lovely (water clock), but you would be spelling it aloud
  forever.
- `fusee`, `remontoire` — good horological metaphors, both free;
  runners-up if `foliot` ever needs replacing.
- Also taken: `systole`, `pendulum`, `metronome`, `chronon`, `cogwheel`,
  `tourbillon`, `vivarium`, `clotho`, `moirai`, `kairos`.

The game project is unnamed and does not need a name yet.

---

## 10. Repository layout

Proposal, not gospel. Single repo for the library; the game lives
separately and depends on it.

```
foliot/
├── pyproject.toml              # uv-managed
├── uv.lock
├── README.md
├── CLAUDE.md
├── docs/
│   ├── DESIGN_SNAPSHOT.md      <- this document
│   └── reference/
│       └── protocols-draft.py  # §16 — REFERENCE ONLY, never imported
├── src/
│   └── foliot/
│       ├── __init__.py         # curated public API surface
│       ├── py.typed            # required; see §17
│       ├── protocols.py        # the real contracts, written fresh
│       ├── rng.py              # per-entity streams + sampled waits
│       ├── registry.py         # kind -> decider / resolver
│       ├── engine.py           # the tick pipeline
│       ├── drivers.py          # ManualDriver, RealtimeDriver
│       └── stores/
│           ├── __init__.py     # Store protocol
│           └── memory.py       # in-memory reference implementation
├── examples/
│   └── tinyworld/              # smallest possible ZPG proving the API
└── tests/
```

Notes on the layout:

- **`src/` layout**, not a flat package. It prevents accidentally
  importing from the working directory instead of the installed
  package, which is a classic source of "works on my machine" test
  results.
- **`stores/postgres.py` deliberately absent from the core.** It belongs
  in an optional extra (`foliot[postgres]`) or a separate distribution,
  so the base install stays dependency-free. See §17.
- **`examples/tinyworld`** is load-bearing, not decoration. It is the
  only honest test of whether the public API is pleasant to use, and it
  should be written early enough to still change the API.

**Tooling: `uv` for everything — DECIDED.** Environment, dependencies,
running, building and publishing all go through `uv`; no bare `pip`, no
manually managed venv. `uv init --lib` produces the `src/` layout above.
Day-to-day: `uv sync`, `uv add --dev pytest ruff mypy`, `uv run pytest`.
Packaging: `uv build` then `uv publish` (see §17 on Trusted Publishing —
prefer the GitHub Action over publishing from a laptop). `uv.lock` is
committed. `uv` moves quickly, so verify current command syntax against
its docs rather than trusting this paragraph.

Python 3.11+ (the drafted protocols use `slots=True` and PEP 604
unions), plus `pytest`, `ruff`, and `mypy --strict` on `src/`. Strict
typing is worth it here specifically because the whole design is
Protocol-based, and Protocols with no type checker are just comments.

---

## 11. Build order

Sequenced so that each milestone is independently testable and nothing
is blocked on an unresolved question until it has to be.

| # | Milestone | Depends on | Notes |
|---|---|---|---|
| M0 | Repo scaffold, `pyproject.toml`, lint/type/test config | — | |
| M1 | `protocols.py` | owner's new ideas | **Do not copy the draft.** §16 is reference only; the owner has revisions to discuss first. |
| M2 | `rng.py` — per-entity streams, `geometric`, sampled waits | §5.2 decision | Argue the RNG question first. |
| M3 | In-memory store + queue semantics | M1 | `due_tick` buckets, tombstoning, `seq` ordering. |
| M4 | Registry + tick pipeline (decide → group → resolve → apply) | **§8.1 rendezvous** | The core of the thing. Blocked. |
| M5 | `ManualDriver`, then `RealtimeDriver` | §3.3 catch-up policy | Manual first — it is what makes M4 testable. |
| M6 | `examples/tinyworld` | M4, M5 | Written while the API can still change. |
| M7 | Postgres store as an extra | M3, §3.4, §4.4 | `SKIP LOCKED`, idempotency, `current_tick` persistence. |

**Build M5's `ManualDriver` before `RealtimeDriver`.** The manual driver
is not a testing afterthought; it is the thing that lets the whole
pipeline be exercised at millions of ticks per second in CI. Writing the
realtime driver first tends to produce a design where time is implicit,
and that is very hard to back out of.

**Definition of done for v0.1:**

1. `examples/tinyworld` runs one million ticks under `ManualDriver` and
   produces a byte-identical log across two runs with the same seed.
2. Shuffling the processing order of same-tick actions does not change
   the outcome (see §12).
3. `RealtimeDriver` sustains a stable tick rate for an hour with no
   measurable drift.
4. With the Postgres store, killing the process mid-tick and restarting
   loses no pending events and duplicates no applied effects.

Items 2 and 4 are the ones worth writing first and the ones most likely
to expose a design error rather than a coding error.

---

## 12. Testing strategy

Four tests carry most of the design's weight. They are worth writing
early, because each one fails for *architectural* reasons rather than
implementation reasons.

**1. Replay determinism.** Run N ticks from seed S, hash the ordered
log. Re-run identically. Assert the hashes match. This is the test that
protects the ability to answer "why did my character die at tick 4.2
million."

**2. Order independence.** Take a tick with several due actions, process
them in a deliberately shuffled order, and assert the resulting world
state and log are identical. This is the *only* real test of the
decide/apply split (§5.1), and it is what a global RNG (§5.2) would
break. If this test cannot pass, the design has a hole in it.

**3. Sampled-wait distribution.** Property test: `geometric(p)` sampled
many times should match a naive per-tick Bernoulli simulation. Compare
mean against `1/p` within tolerance, and ideally run a KS or chi-square
comparison against the naive loop. This is what justifies the claim in
§4.2 that the optimisation is exact rather than approximate — worth
proving rather than asserting.

**4. Crash recovery.** With the Postgres store, interrupt between "the
resolver ran" and "the action was marked done," restart, and assert no
event is lost and no effect is applied twice. This validates the
idempotency strategy from §4.4.

Beyond those: unit-test deciders and resolvers as plain functions with a
fake context and a stub world, asserting on returned `Intent`s and
`Outcome`s. That is the entire point of the handler contract in §7.5 —
if a handler cannot be tested that way, the contract has been violated
somewhere.

For the clock, inject a fake time source rather than sleeping. Drift
(§3.2) is testable by asserting that tick *n* targets
`start + n * duration`, with no real time elapsed.

---

## 13. Postgres schema sketch

A sketch to argue with, not a migration. Deliberately minimal, and
deliberately not including anything game-shaped.

```sql
-- The queue. Source of truth; memory is a near-horizon cache (§4.4).
CREATE TABLE scheduled_action (
    id          BIGSERIAL PRIMARY KEY,
    entity_id   TEXT        NOT NULL,
    kind        TEXT        NOT NULL,
    due_tick    BIGINT      NOT NULL,
    payload     JSONB       NOT NULL DEFAULT '{}',
    status      TEXT        NOT NULL DEFAULT 'pending',
    seq         INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The scheduler's hot path: everything due at or before tick N.
CREATE INDEX scheduled_action_due
    ON scheduled_action (due_tick, seq, id)
    WHERE status = 'pending';

-- Cancelling every pending action for one entity (§4.3 tombstoning).
CREATE INDEX scheduled_action_entity
    ON scheduled_action (entity_id)
    WHERE status = 'pending';

-- World clock. Single row. Must survive restart (§3.4).
CREATE TABLE world_state (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    current_tick BIGINT      NOT NULL,
    world_seed   BIGINT      NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Claim pattern for the worker:

```sql
SELECT * FROM scheduled_action
WHERE status = 'pending' AND due_tick <= $1
ORDER BY due_tick, seq, id
FOR UPDATE SKIP LOCKED
LIMIT $2;
```

`SKIP LOCKED` is not needed for a single worker but costs nothing, and
adopting it now means adding a second worker later is a config change
rather than a redesign.

**Open, tied to §8.1:** whether an `event` table is needed at all. If
the rendezvous mechanism is symmetric derivation, events are ephemeral
per-tick bundles and never touch the database. If it is an explicit
event entity, they need persistence, a lifetime, and something that
closes them.

**Not yet designed:** the log/journal table. It matters more than usual
here because in a ZPG the log is the product, and it will be the largest
table by a wide margin. Partitioning by tick range is the obvious move,
but it should be designed deliberately rather than grown.

---

## 14. Glossary

Terms as used throughout, since several are overloaded elsewhere.

| Term | Meaning here |
|---|---|
| **Tick** | One discrete step of world time. ~1 real second, configurable. Monotonic integer. |
| **Action** | A scheduled intention-to-decide, owned by one entity, stored in the queue. Pure data; behaviour is looked up by `kind`. |
| **Decider** | `(ctx, action) -> [Intent]`. Reads a frozen world. Never mutates. |
| **Intent** | What an entity wants to do, before anyone knows whether it succeeds. |
| **`event_key`** | The grouping key. Intents sharing one in the same tick are resolved together. The rendezvous mechanism (§8.1). |
| **Event** | A bundle of intents sharing an `event_key`, resolved as one unit. |
| **Resolver** | `(ctx, event) -> Outcome`. Decides what actually happened. Describes mutations; does not perform them. |
| **Effect** | A game-defined mutation object with `apply(world)`. The only thing permitted to write. |
| **Outcome** | A resolver's full product: effects, schedules, cancels, log. |
| **Tombstone** | Marking a pending action cancelled rather than deleting it (§4.3). |
| **Driver** | The thing that advances the clock. Realtime (sleeps) or manual (instant). |
| **Observer** | The player. Reads the log; cannot act. |

---

## 15. Working notes

Observations about how the owner works, recorded because they materially
affect how to be useful here.

- **Expects to be argued with, and argues back.** Several of the better
  decisions in this document came out of push-back — the forest
  encounter objection in §4.1 is a good example, where the objection was
  half-right and forced a better answer than either starting position.
  Do not simply defer; do not steamroll either.
- **Wants mechanisms explained, not just recommended.** When something
  was asserted without a reason (the clock drift point in §3.3), the
  response was "I didn't understand" rather than acceptance. Explain the
  *why*, concretely, with numbers where numbers exist.
- **Enforces scope.** Explicitly cut off a tangent into item identity
  and enchantments (§6.3). The instruction was to build the core with
  nothing tied to a specific game. Respect it — game-domain modelling is
  a later project.
- **Strong on Python and Postgres; new to authoring libraries.** The
  library-shaped concerns in §17 are the genuinely unfamiliar part and
  worth being explicit about. The Python and SQL are not.
- **Building this for enjoyment.** It is a hobby project, unrelated to
  work, with no commercial goal. Optimise for the design being
  interesting and the code being pleasant to write, not for shipping
  speed.

---

## 16. Appendix: `protocols.py` — REFERENCE DRAFT, NOT THE PLAN

> **Read this as a sketch of one possible shape, not as the design.** The
> owner has explicitly said this should be *reference material* and has
> new ideas that supersede parts of it. Do not copy this file into
> `src/foliot/`. Discuss the revisions first, then write the real thing.
> It lives at `docs/reference/protocols-draft.py`, deliberately
> hyphenated so it cannot be imported by accident.

What it is useful for: the *vocabulary* (§14), the pipeline shape
(§7.2), and the rationale in the docstrings — especially why effects are
objects, why actions carry `seq`, and why `World` must present a stable
per-tick view. Those arguments survive even if every signature changes.

The package name in the drafted file was `simcore`, a placeholder now
superseded by `foliot` (§9). Python 3.11+ (uses `slots=True` on
dataclasses and PEP 604 unions).

```python
"""Core contracts for the simulation engine.

Nothing in this module knows what a character, a forest, or hit points are.
It knows about ticks, scheduled actions, intents, and resolution. A game
supplies meaning by registering deciders and resolvers and by implementing
Effect / World.

Pipeline for a single tick:

    queue.due(tick)                -> [Action]
      for each action: decide()    -> [Intent]        (reads frozen state)
      group intents by event_key   -> [Event]
      for each event: resolve()    -> Outcome         (the only writer)
      apply effects, enqueue schedules, tombstone cancels

The decide phase never mutates. The resolve phase never reads anything it
was not handed. That split is what makes ticks order-independent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

__all__ = [
    "Tick",
    "EntityId",
    "ActionId",
    "Entity",
    "World",
    "ActionStatus",
    "Action",
    "Intent",
    "Event",
    "Effect",
    "Schedule",
    "Outcome",
    "DecideContext",
    "ResolveContext",
    "Decider",
    "Resolver",
    "Rng",
]

Tick = int
EntityId = str
ActionId = str


# --------------------------------------------------------------------------
# World
# --------------------------------------------------------------------------

@runtime_checkable
class Entity(Protocol):
    """Deliberately almost empty.

    The engine only ever needs to identify an entity, never to interpret it.
    Games subclass or ignore this as they like; level, hp, inventory and the
    rest are none of the engine's business.
    """

    @property
    def id(self) -> EntityId: ...


class World(Protocol):
    """Read access to game state, as seen during a single tick.

    Implementations are expected to present a *stable* view for the duration
    of a tick's decide phase: two deciders in the same tick must observe the
    same world, regardless of processing order. How that is achieved
    (snapshot, copy-on-write, MVCC transaction) is the implementation's
    problem.
    """

    def get(self, entity_id: EntityId, /) -> Entity | None: ...


class Effect(Protocol):
    """A game-defined mutation, produced by a resolver.

    Effects are the only thing permitted to write. Keeping them as objects
    rather than inline mutation buys three things: resolvers stay pure and
    unit-testable, effects can be logged as an audit trail of why state
    changed, and application order is explicit rather than incidental.
    """

    def apply(self, world: Any, /) -> None: ...


# --------------------------------------------------------------------------
# Actions -- the unit the queue stores
# --------------------------------------------------------------------------

class ActionStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"  # tombstone; skipped on pop


@dataclass(frozen=True, slots=True)
class Action:
    """A scheduled intention-to-decide, owned by one entity.

    Data only, no behaviour: `kind` is looked up in the decider registry.
    That keeps actions trivially serialisable, which is what lets the queue
    live in Postgres instead of in process memory.

    An entity may have many pending actions at once -- a sampled encounter,
    an arrival, a cooldown expiry -- so this is a separate record keyed by
    entity, never a field on the entity itself.
    """

    id: ActionId
    entity_id: EntityId
    kind: str
    due_tick: Tick
    payload: Mapping[str, Any] = field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING

    # Tie-break within a tick. Ordering must be total and deterministic even
    # though resolution is order-independent, so that replays match exactly.
    seq: int = 0

    def sort_key(self) -> tuple[Tick, int, ActionId]:
        return (self.due_tick, self.seq, self.id)


# --------------------------------------------------------------------------
# Intents and events -- the rendezvous layer
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Intent:
    """What an entity wants to do, before anyone knows if it succeeds.

    `event_key` is the whole grouping mechanism. Intents emitted in the same
    tick sharing a key are bundled into one Event and resolved together;
    that is how a character's "swing at the beast" and the beast's "bite the
    character" become a single fight rather than two independent
    resolutions.

    Solo intents leave `event_key` unset and get a unique one, which makes a
    single-participant event -- no special case in the engine.
    """

    actor_id: EntityId
    kind: str
    event_kind: str
    event_key: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    # The action this intent came from, so a resolver can tombstone the
    # losing branch (encounter fired first, so cancel the arrival).
    source_action_id: ActionId | None = None

    def resolved_key(self) -> str:
        if self.event_key is not None:
            return self.event_key
        return f"solo:{self.event_kind}:{self.actor_id}:{self.kind}"


@dataclass(frozen=True, slots=True)
class Event:
    """A group of intents to be resolved as one unit."""

    key: str
    kind: str
    tick: Tick
    intents: tuple[Intent, ...]

    @property
    def participants(self) -> tuple[EntityId, ...]:
        seen: dict[EntityId, None] = {}
        for i in self.intents:
            seen.setdefault(i.actor_id, None)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class Schedule:
    """A request to enqueue a future action."""

    entity_id: EntityId
    kind: str
    due_tick: Tick
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Outcome:
    """Everything a resolver produces. No side effects, just a description.

    `log` carries observer-facing narrative. In a zero-player game the log
    *is* the product, so it is a first-class output rather than something
    scraped out of effects afterwards.
    """

    effects: tuple[Effect, ...] = ()
    schedules: tuple[Schedule, ...] = ()
    cancels: tuple[ActionId, ...] = ()
    log: tuple[Any, ...] = ()


# --------------------------------------------------------------------------
# Contexts and handler signatures
# --------------------------------------------------------------------------

class Rng(Protocol):
    """Per-entity, per-tick randomness. Never the global `random` module.

    Seeded from (world_seed, entity_id, tick, seq) so that outcomes do not
    depend on processing order -- which is what permits parallel workers,
    handler retries, and replaying a single character's tick years later to
    answer "why did my hero die here".

    The sampled waits matter as much as the raw draws: `geometric` turns
    "roll 1% every tick" into one scheduled event instead of thousands of
    no-op wakeups, with an identical distribution.
    """

    def random(self) -> float: ...
    def randint(self, lo: int, hi: int) -> int: ...
    def choice(self, seq: Iterable[Any], /) -> Any: ...
    def geometric(self, p: float) -> int: ...


class DecideContext(Protocol):
    """Handed to a decider. Read-only by construction."""

    @property
    def tick(self) -> Tick: ...
    @property
    def world(self) -> World: ...
    @property
    def rng(self) -> Rng: ...


class ResolveContext(Protocol):
    """Handed to a resolver.

    Still read-only: resolvers describe mutations as Effects rather than
    performing them, so they can be called in a test with a fake world and
    asserted on by return value.
    """

    @property
    def tick(self) -> Tick: ...
    @property
    def world(self) -> World: ...
    @property
    def rng(self) -> Rng: ...


class Decider(Protocol):
    """Action -> intents. Registered against `Action.kind`."""

    def __call__(
        self, ctx: DecideContext, action: Action, /
    ) -> Iterable[Intent]: ...


class Resolver(Protocol):
    """Event -> outcome. Registered against `Intent.event_kind`.

    Resolvers must re-validate participants: between decide and resolve, a
    participant may have died in another event this same tick. Degrade,
    do not assume.
    """

    def __call__(self, ctx: ResolveContext, event: Event, /) -> Outcome: ...
```

---

## 17. Library hygiene notes

Collected because the owner has not shipped a library before. Small
conventions, but they are the difference between a library and a project
folder:

- No `logging.basicConfig`. Use `logging.getLogger(__name__)` and let the
  consuming application configure handlers.
- No side effects at import time.
- No reading config files from disk. Configuration is passed in.
- Dependencies list should be essentially empty. Postgres support goes
  in an extras group or a separate distribution.
- Persistence, clock driving, and randomness are all **injected
  interfaces**, not concrete implementations. The in-memory
  implementations that ship with the library exist primarily so tests
  and quickstarts work without a database.
- Test the engine with `ManualDriver` and an in-memory store; those two
  together should let the full pipeline run millions of ticks in a
  test suite.

---

## 18. Status summary

### Decided

- Two projects: domain-agnostic core library + ZPG game consuming it.
- Continuous simulation (not lazy materialisation).
- Tick-based, ~1s ticks, tick duration configurable.
- Python / PostgreSQL / Flask-or-FastAPI.
- Two-tier entity model (`entity` slow-changing, `entity_state`
  fast-changing).
- Two-phase tick: decide (pure, frozen snapshot) then resolve/apply.
- Multi-participant interactions resolve inside a shared Event with
  intents locked in first.
- Core library scope only for now; no game-domain modelling.
- Library is named **`foliot`** (§9).

### Recommended and explained, not explicitly confirmed

- Absolute-deadline clock rather than relative sleeps.
- Arbitrary-future-tick scheduling (timing wheel) rather than N+1 only.
- Sampled waits (geometric) replacing per-tick rolls.
- Durable Postgres queue with in-memory near-horizon cache;
  `FOR UPDATE SKIP LOCKED`; tombstoning.
- Queue as its own table rather than a `current_action` field.
- Per-entity RNG streams seeded on `(world_seed, entity_id, tick, seq)`.
- Effects as objects; `log` as a first-class `Outcome` field.
- Pluggable clock drivers (`RealtimeDriver` / `ManualDriver`).

### Open, needs deciding

1. **Rendezvous mechanism** (§8.1) — symmetric key derivation vs.
   explicit persisted event entity. **Blocks the registry.** Determines
   whether `Event` is persisted or ephemeral.
2. **Catch-up policy** (§3.3) — pin world time to wall time vs. allow
   lag. Leaks into the loop structure.
3. **Downtime handling** (§3.4) — fast-forward, compress, or shift the
   world clock.
4. **Idempotency strategy** (§4.4) — idempotent handlers vs. fully
   transactional effect+status application.
5. **Per-entity RNG** (§5.2) — recommendation stands, owner's initial
   preference was a global RNG; likely resolved by clarification but not
   confirmed.
6. **Action granularity** — is one action roughly one narrative beat the
   observer reads, or finer-grained than what gets surfaced? Asked but
   not answered.
7. **Log/journal table design** (§13) — not yet designed. It will be the
   largest table by a wide margin and it *is* the product, so it wants
   deliberate design (partitioning by tick range) rather than organic
   growth.
8. Flask vs. FastAPI. Deferrable indefinitely, and irrelevant to the
   library.

### Immediate next work

Full sequence in **§11 Build order**. The short version: scaffold the
repo (M0), then design the real protocols with the owner (M1) — the §16
draft is reference material, not code to copy, and the owner has
revisions to it. Then settle the
**rendezvous question (§8.1)** before writing the registry and tick loop
(M4), since it determines whether `Event` needs persistence and
therefore what the schema looks like.

Settle the **RNG question (§5.2)** before M2, since it is a one-line
change now and a rewrite later.
