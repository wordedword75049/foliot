# foliot — Design Snapshot (v2)

> **What this document is.** Version-controlled memory for a project that is
> designed across sessions. It records decisions *and their reasoning*, the
> alternatives rejected, and the questions still genuinely open.
>
> **v2 supersedes v1.** Several v1 recommendations were reversed in the
> session that produced this revision. §19 lists every reversal with its
> reason, so the old reasoning is not lost — but §19 is history, and
> everything before it is current. Where they disagree, this document wins.
>
> Items are marked **DECIDED**, **RECOMMENDED** (argued but not accepted),
> or **OPEN**. A recommendation is an argument, not a decision.

## Start here

If you are a fresh session picking this up, read in this order:

1. **§2 What foliot is** — the guarantees. Everything else exists to hold
   these up.
2. **§3 Core vs game** — the line. The single most common failure mode on
   this project is putting game concepts in the library.
3. **§18 Status summary** — what is settled, what is open.
4. **§13 Build order** — what to write first.
5. **§17 Working notes** — how the owner works. Read before proposing
   anything.

**Three things that will otherwise trip you up:**

- **The rendezvous problem from v1 §8.1 is resolved (§10.2).** Don't
  re-litigate it.
- **The RNG question is resolved (§9).** Per-entity, counter-based. Don't
  propose a global stream.
- **Sampled/geometric waits were reversed (§5.4, §19).** The default is
  per-tick rolling. The *ability* to schedule far into the future stays.

---

## Contents

| § | Section |
|---|---|
| 1 | Project context |
| 2 | What foliot is: the guarantees |
| 3 | Core vs game: the line |
| 4 | The clock |
| 5 | The queue: deadlines, shapes, granularity |
| 6 | Suspension |
| 7 | Actions: objects and the mandatory `BaseAction` |
| 8 | Persistence: the tick is the transaction |
| 9 | Randomness |
| 10 | Layer 2: intents, events, resolvers |
| 11 | The game model (recorded here, built elsewhere) |
| 12 | Repository layout and tooling |
| 13 | Build order |
| 14 | Testing strategy |
| 15 | Postgres notes |
| 16 | Glossary |
| 17 | Working notes |
| 18 | Status summary |
| 19 | Superseded: what changed from v1, and why |

---

## 1. Project context

### 1.1 What is being built — **DECIDED**

Two separate projects, deliberately split:

1. **`foliot`** — a general-purpose, domain-agnostic tick/queue engine.
   This is the primary interest. The owner has not authored a library
   before, so library-shaped concerns (packaging, public API surface,
   testability, no import-time side effects) are part of the learning
   goal, not incidental.

2. **The game** — a zero-player game (ZPG) consuming the library.
   Reference points: **Godville** and **The Tale (Сказка)**. A player
   creates a character and then *observes*; the character and the world
   live on autonomously on a server. The player is a spectator.

The library is the deliverable. The game is the motivating use case and
the source of requirements, but the library must not know it exists.

### 1.2 Why the split matters — **DECIDED**

In a ZPG the simulation **is** the product. There is no player input to
hide latency behind and no interaction loop to paper over gaps in the
world model. The log of what happened is the thing players consume. Three
properties that would be minor in a conventional game become central:

- **Durability** — the world runs for months. Losing pending state is
  unrecoverable, because the history is the content.
- **Explicability** — players will ask "why did my character die here?"
  and the system must be able to answer.
- **Continuous operation** — the world runs whether or not anyone is
  watching (§1.3).

### 1.3 Continuous simulation, not lazy materialisation — **DECIDED**

The alternative was *lazy* simulation: store `last_simulated_at` per
character and materialise intervening events only when someone opens the
page. Free for idle characters.

**Rejected** because cross-character interaction becomes genuinely hard.
Two characters meeting, a shared economy, or world-scale events would all
require force-materialising every participant, and the complexity
compounds. Continuous is what Godville does and it is the easier long-run
bet for any shared-world layer, even though it burns cycles on behalf of
nobody.

**Consequence:** the engine is a real service with real uptime concerns.

### 1.4 Tick-based, ~1 second per tick — **DECIDED**

The world advances in discrete ticks of roughly one second. Tick duration
is configuration, never a constant baked into logic.

### 1.5 Load profile — **ESTABLISHED**

Throughput is a non-issue and should not drive design. Godville generates
roughly one event per minute per hero. Ten thousand characters is under
200 events/second, which is nothing for Postgres.

What *does* matter is **per-character isolation**: character A's
simulation must never affect character B's outcomes. That property is
what permits safe retries and makes ordinary iteration-order changes harmless.
Optimise for isolation and determinism, not raw throughput.

### 1.6 Stack — **DECIDED**

Python, PostgreSQL, and Flask-or-FastAPI (undecided and irrelevant to the
library). The core library has **zero dependencies**. Anything
Postgres-, MariaDB-, MySQL-, or file-shaped belongs to the consuming game (or
an independent integration package), not to foliot. Foliot supplies the
storage protocols and one built-in in-memory implementation.

### 1.7 Non-technical context

Personal, non-commercial hobby project, unrelated to the owner's
employment. Personal use of company tooling is sanctioned by their
employer. Flagged once and worth keeping in mind: employment agreements
sometimes contain invention-assignment clauses covering work created
"using company resources or systems," which can create IP ambiguity for
side projects built through employer-provided tooling. Not a concern for
a hobby repo; potentially relevant if this ever becomes something. Not
legal advice.

---

## 2. What foliot is: the guarantees — **DECIDED**

This section is the answer to "isn't a queue ticker too shallow to be a
library?" The depth is not in the object graph. It is in the promises,
and every promise below is something a consumer would otherwise discover
the hard way, in production, at tick four million.

**foliot promises:**

1. **Same seed, same history.** Run N ticks twice from the same world
   seed; get byte-identical output.
2. **Reordering the queue cannot change outcomes.** Process a tick's due
   actions in any iteration order and the world lands in the same state.
3. **The clock does not drift.** Tick *n* happens at
   `start + n · duration`, not `start + n · duration + nε`.
4. **Nothing pending is lost, and nothing is applied twice.** The tick is
   the unit of atomicity (§8).
5. **Time is injectable.** Ten million ticks run in a unit test in under
   a second.

Compare against what already exists — APScheduler, Celery beat, and
friends give you "run this later." None give determinism, replay, or
order-independence, because none of them are simulating a world. That gap
is foliot's reason to exist.

**The inclusion test.** For any candidate feature, ask: *does it add a
guarantee the consumer cannot easily provide themselves?* If yes, it may
belong in the library. If it is merely vocabulary or convenience, it
belongs in the game. This test decided §10 (intents/events are in,
because they add simultaneity) and §3 (targets are out, because the
engine never reads them).

**Small surface, strong promises.** That is a library. Large surface,
vague promises, is a framework nobody can test.

---

## 3. Core vs game: the line — **DECIDED**

**foliot is: a clock, a queue of scheduled things, a way to look up what
to run, and a source of addressable randomness.** It knows about ticks,
due times, statuses, ordering, and seeds. It has never heard of a target,
a location, an environment, an activity, or combat.

**The membership test for a field:** *does the engine read it?* If the
engine never reads it, it goes in the game's payload, not in a core type.

| Core (`foliot`) | Game |
|---|---|
| `Tick` (monotonic int) | `Char`, `EventfulEnvironment`, wolves |
| Drivers: `ManualDriver`, `RealtimeDriver` | first-order vs second-order producers |
| Queue keyed on `due_tick`, total order via `seq` | environments being entities at all |
| `ActionState`: active or suspended; scheduled actions preserve a `due_tick`, recurring actions have none | encounter rolls, and what `p` depends on |
| `suspendable` — one bit: does this action stop when its owner is interrupted | which actions are suspendable, and what interrupts them |
| Mandatory `BaseAction` + engine-owned binding bookkeeping | HP, mana, `entity_state`, damage |
| Counter-based RNG on `(world_seed, entity_id, tick, seq)` | targets — who an action is aimed at |
| `Store` protocol + in-memory implementation | narrative log text |
| The tick-transaction boundary | the `encode`/`decode` pair, and the database |
| Layer 2 (optional): intents, event grouping, resolvers | what a resolver decides |

**Notable exclusion — `target`.** An earlier position in this session
argued for `target` as a first-class core field, on the grounds that it
would drive event-key derivation and cascading cancellation. **Reversed.**
The engine never needs to read it: in this design the environment opens
events explicitly (§10.2), so no key derivation is required, and
cancellation is something the game requests. `entity_id` (the owner)
stays in core, because the queue indexes on it and the RNG seeds on it.

---

## 4. The clock

### 4.1 Absolute deadlines, not relative sleeps — **DECIDED**

`sleep(tick_duration - processing_time)` accumulates drift. Between
waking, measuring, and sleeping again there is overhead — OS scheduler,
instrumentation, interpreter. The real period is `1.000 + ε`, not
`1.000`. At ε = 2 ms that is ~173 seconds lost per day; after a month the
world clock is roughly ninety minutes behind wall time, silently.

Target fixed wall-clock moments so errors do not compound:

```python
start = time.monotonic()
while True:
    process_tick(n)
    n += 1
    deadline = start + n * TICK_DURATION
    time.sleep(max(0, deadline - time.monotonic()))
```

Use `time.monotonic()`, never `time.time()` — the latter jumps on NTP
correction and can move backwards.

This same principle recurs in the queue (§5.2): **store deadlines, not
countdowns.** A deadline is true whether or not anyone is awake to
maintain it; a countdown is only correct if you were woken every single
tick to decrement it.

### 4.2 Pluggable drivers — **DECIDED**

The advancement mechanism is an injected dependency:

- **`RealtimeDriver`** — sleeps to absolute deadlines. Production.
- **`ManualDriver`** — advances instantly. Tests, fast-forward, replay.

```python
sim.run(RealtimeDriver(tick_seconds=1.0))  # production
sim.run(ManualDriver(until_tick=10_000_000))  # tests, fast-forward
```

`ManualDriver.until_tick` is **inclusive**. If the next unfinished tick is 90,
running with `until_tick=100` processes ticks 90 through 100 and leaves
`current_tick()` at 101. Therefore an action processed at tick 90 that
schedules itself for `ctx.tick + 10` does fire at tick 100 during that run.
If tick 100 was already completed, the driver has nothing to do. The inclusive
rule matches the ordinary meaning of "run through tick 100" and keeps the
action's deadline visible rather than stopping immediately before it.

Same code path, different sense of time. This single split is what lets
ten million ticks run in a unit test. Hardcoding `time.sleep` into the
loop makes the library untestable, and that is felt immediately.

**The division of labour: `process_tick()` never waits.** It does one
tick's work and returns. Deciding *when* the next call happens belongs
entirely to the driver — immediately for `ManualDriver`, at
`start + n * tick_duration` for `RealtimeDriver` (§4.1, absolute targets,
never "sleep the rest of the second"). If waiting lives inside the work,
the two drivers stop being interchangeable and a test cannot outrun the
clock, which is the one thing this split exists to allow.

### 4.3 Catch-up policy — **OPEN**

When a tick takes longer than a tick, `max(0, ...)` returns zero and the
system is behind. Two policies, both defensible:

| Policy | Behaviour | Failure mode |
|---|---|---|
| **Catch up** | Run ticks back-to-back until caught up; world time stays pinned to wall time. | Under sustained overload the catch-up work causes more lag — a death spiral. |
| **Let it lag** | World time falls permanently behind real time. Smooth, no spikes. | "5 minutes ago" in the log stops meaning 5 minutes ago in reality. Diverges slowly and invisibly. |

A hybrid is possible: catch up with a bounded budget (never more than N
ticks back-to-back) plus an alert when lag exceeds a threshold. This
leaks into the loop structure, so it needs deciding before
`RealtimeDriver` is finalised. **Not blocking M1–M4**, since `ManualDriver`
has no such notion.

### 4.4 Downtime handling — **OPEN**

After four hours of downtime, what happens?

- **Fast-forward** — replay every missed tick. Expensive but faithful.
- **Compress** — summarise the gap into a digest event.
- **Shift the world clock** — declare no in-fiction time passed.

Related and settled: **`current_tick` must be persisted**, since on
restart the engine needs to know whether it is resuming or
fast-forwarding. That falls out of §8 for free.

---

## 5. The queue: deadlines, shapes, granularity

### 5.1 `due_tick` is the scheduled-queue primitive — **DECIDED**

The scheduled side of the queue is `due_tick -> [actions]` — a timing
wheel — not a single next-tick list. Any scheduled action may target any
future tick. Recurring actions are the second shape and carry no deadline
at all (§5.7).

The owner initially proposed that an action carry `ticks_remaining` and
re-enqueue itself each tick at `remaining - 1`. That is a *scheduling
policy*, not a storage format, and the two were separated:

- **`due_tick` is the primitive for anything that waits.** A scheduled
  action that wants to run on the next tick may target `now + 1`.
- **A duration is never stored as a countdown.** Per-tick behaviour uses
  §5.7's *recurring* shape — "runs every tick" as a property of the
  action, rather than a deadline rewritten on every tick.

**Why this asymmetry matters.** With `ticks_remaining` as the *only*
mechanism, the engine cannot express "wake me in eight hours" efficiently.
A Char sleeping until dawn is 28,800 ticks: under decrement-and-
re-enqueue that is 28,800 wakeups, each loading a row, calling a handler
that says "still asleep," and writing a row back. Under `due_tick` it is
one row that sits still for eight hours. At 10,000 Chars the difference is
roughly 10,000 row-writes per second forever versus writes only when
something happens — and the pain shows up as autovacuum pressure on the
one table whose index must stay fast, not as CPU.

### 5.2 Deadlines, not countdowns — **DECIDED**

Anything with a duration is expressed as absolute tick numbers, compared
rather than decremented. Poison has two clocks and needs no countdown for
either:

```python
class Poison(BaseAction):
    """Damages every `interval` ticks until `expires_at`."""

    def __init__(self, entity_id, interval, expires_at):
        super().__init__(entity_id, suspendable=False)
        self.interval = interval
        self.expires_at = expires_at

    def process(self, ctx: TickContext[World]) -> None:
        ctx.emit(Damage(self.entity_id, amount=3))
        ctx.log(f"{self.entity_id} is racked by poison")
        if ctx.tick + self.interval < self.expires_at:
            ctx.schedule(self, due_tick=ctx.tick + self.interval)
        else:
            ctx.log("the poison passes")
        # wears off simply by not rescheduling
```

Miss a tick with a countdown and it is silently wrong forever. Miss a
tick with a deadline and nothing happens, because the deadline was never
being maintained in the first place.

### 5.3 Store what changes; derive the rest — **DECIDED**

Three numbers describe a pending action — `current_tick`, its deadline,
and how much is left — and any two give the third. Storing all three
stores a fact that can disagree with itself.

- **Active scheduled** carries a concrete `due_tick`. "How much is left" is
  `due_tick - now`: derived on demand, never stored. **Active recurring**
  carries `due_tick=None`, because it runs every tick and has no wait to
  measure (§5.7).
- **Suspended** carries `suspended_at` — *when the pause began* — and the
  `due_tick` it was waiting for, if it had one (§6.4).

**The rule: suspension shifts every deadline forward by the length of the
pause.**

```
paused_for = resumed_at - suspended_at
due_tick   = due_tick + paused_for
```

That sentence is deliberately larger than the core, because the deadlines
that matter most are not the core's. A walk's `arrives_at` is game
payload the engine must never touch (§3), so the engine cannot repair it.
It is shifted by the *same* number, handed to the action through
`on_resume(paused_for)` (§6.4).

**Worked, because the off-by-one here is silent.** Ivan starts walking at
tick 0 with `arrives_at = 1000`. A wolf appears at 400; the fight ends at
450. He still owes 600 ticks of walking, so he must arrive at **1050**.
`paused_for = 450 - 400 = 50`, and `1000 + 50 = 1050`.

Storing `remaining` instead was tried and reversed. `due = now +
remaining` fixes the *core's* deadline correctly, but yields no pause
length, so game payload cannot be shifted at all — a suspended Char
would arrive as though the fight never happened. And a recurring action
(§5.7) has no `due_tick`, so `remaining` never meant anything for it.
§19.

### 5.4 Granularity: per-tick rolling is the default — **DECIDED (reverses v1)**

v1 recommended replacing per-tick probability rolls with **sampled waits**
— sampling how many ticks until the next success from a geometric
distribution and scheduling that directly, so one queue entry replaces
thousands. The claim that it is *exact* rather than approximate is
correct: geometric is by definition the distribution of "trials until
first success."

**Reversed, for a reason v1 did not weigh.** Sampled waits are valid only
while `p` is constant. In this game `p` depends on the Char's stats *and*
the environment's stats — the Char is wounded, night falls, they are
carrying more. Every change invalidates the pending sample and forces a
resample, and if `p` moves often the resampling churn eats the savings
the sampling was meant to buy. Rolling each tick lets `p` vary
continuously and is far easier to reason about.

**What is kept:** the ability to schedule arbitrarily far into the
future (§5.1). Sleep-until-dawn needs it and per-tick cannot express it.
`geometric` may still ship as an available sampled wait; it is simply not
the default pattern.

**Not lost, for future reference:** the geometric sample is *memoryless*,
so resampling from the moment `p` changes is exactly equivalent to having
rolled per-tick through the change. No bookkeeping of "how far into the
old roll we were" is ever required. If sampled waits are revisited, this
is the fact that makes them safe.

### 5.5 Removal, not tombstoning — **DECIDED (reverses the earlier call)**

A finished or invalidated action is **deleted**. There are only two
states an action can be in — active or suspended (§6.4) — because "done"
and "cancelled" are not states an action is *in*; they are the action
ceasing to exist. When a Char dies, everything it owns goes, suspended
rows included: one `DELETE ... WHERE entity_id = $1`.

The earlier position kept tombstones for the audit trail. That argument
does not survive §15's own conclusion: history lives in the journal, and
the queue stays small and hot. A tombstone duplicates a journal entry
inside the one table whose index must stay fast.

**The deletion rule under the supported single-runner model.** `due(tick)`
returns a snapshot for the one active simulation runner. After deletion
commits, the action must not appear in any later `due(...)` snapshot. A stale
Python reference that user code deliberately retained is just a reference;
the store cannot erase it and foliot never promises to. There is no cancelled
state, claim wrapper, or `is_still_pending()` check.

**Consequence:** an entity routinely has several pending actions at once —
an arrival, a cooldown expiry, a hunger tick, a poison effect. The queue
is therefore its own table keyed by `entity_id`, never a `current_action`
field on the entity. The scheduler's hot query ("everything due at or
before tick N") then hits one index on one table instead of scanning
every entity in the world.

There is still a good reason to keep something action-shaped on the
entity's state: the observer needs to read *"Ivan wanders through the
pines."* That is a **denormalised display cache** of current narrative
activity — explicitly not the scheduling source of truth. Keeping the two
roles distinct is what stops them silently drifting apart.

### 5.6 Scheduling is strictly into the future — **DECIDED**

An action schedules its own next action, and the engine **rejects**
`due_tick <= current_tick`. The earliest legal target is `now + 1`.

**Why this is a rule and not a convention.** The engine takes the due
list for a tick once, then runs it. If an action running at tick 5 adds
another action to tick 5, that action lands in a bucket already read, and
whether anything else in the tick observes it depends on who ran first:

```
step 1   ask the store what is due at tick 5   -> [Hunger, Poison]
step 2   run Hunger   -> schedules Starve at tick 5
step 3   run Poison   -> does Poison see Starve?
```

Run Hunger first and Poison sees it; run Poison first and it does not.
Same tick, same starting state, different outcome by processing order —
guarantee 2 (§2) lost through a different door than §9.2's global RNG.

Forbidding same-tick scheduling closes it by construction: nothing done
during a tick is observable within that tick, so the tick's actions can
be shuffled or retried without changing the result.

The rule costs nothing, because the game already works this way. An
effect belongs to the interval it covers, not the instant it fires:
damage landing at tick 6 *is* the hunger from tick 5 to tick 6.

### 5.7 Two shapes: scheduled and recurring — **DECIDED**

`due_tick` is the primitive for anything that waits. It is *noise* for
anything that acts every tick, so `None` represents the recurring shape.
There is no separate `recurring` boolean: it would store the same fact twice
and permit the two copies to disagree.

- **Scheduled** — carries a `due_tick`, sleeps in the queue until it
  arrives, wakes once. Sleeping till dawn, cooldowns, an arrival with
  nothing to check on the way. Poison or hunger that strikes every N > 1
  ticks is also scheduled: each strike schedules the next one at `now + N`
  until the effect ends. Nothing needs to run between strikes. If it truly
  strikes every single tick, it is recurring instead.
- **Recurring** — runs every tick until it stops. Carries no `due_tick`,
  because there is nothing to record: for a per-tick action the next due
  tick is always `now + 1`, and rewriting a row every tick to say `now +
  1` spends a delete and an insert to store a constant.

**The game's mapping** (recorded, not required): an action against a
*stale* environment — Night, weather — can produce nothing on the way, so
it is scheduled. An action against an *eventful* environment — a forest,
a city — gives that environment a chance to react on every step, so it is
recurring (§11.2).

**Measured**, 10,000 Chars acting every tick against Postgres 16, one
tick per transaction:

| | ms/tick | dead tuples after 20 ticks |
|---|---|---|
| rewrite `due_tick` every tick | 158.5 | 170,000 |
| recurring, no queue writes | 43.1 | **0** |

The 3.7x on time is the small part. The dead-tuple column is the
autovacuum pressure §5.1 warned about, present or absent: at one tick per
second the first row is ~1.7 billion row versions a day, the second is
zero. **A Char walking quietly through the pines for 400 ticks touches
the queue zero times** — one write when the walk starts, one when it
ends.

**The scheduled/recurring shape is core** by §3's test: the engine reads the
optional deadline to decide what runs. **The `Store` protocol does not
change** — `due(tick)` means every active action for which
`due_tick is None or due_tick <= tick`. Suspended actions are stored but are
not returned.

**There is nothing to checkpoint, and that is the point.** On a quiet
tick a recurring walk has no mutable state anywhere: progress is
`arrives_at - now` (derived), the RNG is counter-based with no cursor
(§9.1), and anything that actually happened is an effect that committed
in that tick. So the temptation to "save the active set every 30 ticks"
has nothing to save — and taking it would resurrect §8.2's incoherence in
miniature: a wolf's damage commits at tick 5000, the process dies at
5020, and the queue rewinds to 4990 while the damage does not.

**Cache is not checkpoint.** Memory may hold a *copy* of committed rows —
discard it on restart, rebuild from the store, lose nothing. That is the
near-horizon cache §8.5 already permits, and it is where the recurring
set belongs in a consumer's durable store. Memory must never hold state the
store does not have.

---

## 6. Suspension — **DECIDED**

### 6.1 The problem

When a battle interrupts Ivan's walk, the walk must stop. Poison, hunger,
and cooldowns must continue. Suspending one arbitrary action is therefore
too narrow, while suspending everything Ivan owns is too broad.

A walk is one action: it rolls for the environment's response inside its
own `process()` (§10.3). The remaining distinction is not a bundle of walk
machinery; it is whether an action is an interruptible activity or an
effect that continues through interruption.

### 6.2 The rule — **DECIDED (reverses the `group_id` design)**

Every action carries one bit:

- **`suspendable = False`** — effects. Poison, hunger, cooldowns. They
  keep firing through any suspension.
- **`suspendable = True`** — activities. Walking, crafting, travelling.
  They stop when their owner is interrupted.

"Suspend Ivan" therefore means *suspend every suspendable action Ivan
owns, tagged with the id of the event that interrupted him*, and resume
means *re-enqueue everything tagged with that event*. The waking handle
is `suspended_by` on the suspended state (§6.4) — not a group minted in
advance by the game.

**Why the bundle went away.** The earlier motivation was that a walk was
several pending actions — an arrival *and* an encounter roll — so
suspension needed a handle on the set. §10.3 dissolved that:
the environment's roll happens **inside the walking Char's own
`process()`**, not as a separate queued action. A walk is one action. The
group existed to solve a problem that another decision had already
removed.

**The constraint this accepts:** a Char has **one suspendable action at a
time**. Walking *and* crafting at once, with only the walk suspending, is
not expressible. That is the game's model, and it is what buys a boolean
instead of an opaque string threaded through every activity in game code.

The engine never interprets `suspended_by` beyond grouping by it. *Which*
actions are suspendable, and what interrupts them, is game policy.

The current game intends an interrupted scheduled action to be removed
rather than resumed. That is game policy, not a restriction on foliot. If a
consumer asks the library to suspend a scheduled action, foliot preserves and
shifts its `due_tick`; if the action is recurring and therefore has no
deadline, foliot simply restores it to the recurring active set on resume.

**One suspender, guaranteed by shape.** Suspending an already-suspended
action is a no-op — replacing its original `suspended_at` and
`suspended_by` would lose who owes the wake-up and when the pause began.
That is only safe because a Char can
have at most one suspender, which follows from the game invariant that
**events do not nest** (§11.6). The engine cannot enforce it; it relies on
it, so it is written down.

### 6.3 Consequence for action granularity

"Is one action one narrative beat?" is answered, and more simply than
before: **a suspendable action is the narrative beat.** The observer
reads "Ivan wanders through the pines"; the engine holds one walk action,
which rolls for encounters inside its own `process()` (§10.3). There is
no bundle underneath, because there is nothing left to bundle.

### 6.4 Two state classes, and `BaseAction` carries the bookkeeping — **DECIDED**

`ActionState` is a discriminated union, not an enum. Each state carries
exactly the fields that state has, so the fields cannot be wrong:

```python
@dataclass(frozen=True, slots=True)
class Active:
    due_tick: Tick | None  # None exactly when recurring (§5.7)
    status: Literal["active"] = field(default="active", init=False)


@dataclass(frozen=True, slots=True)
class Suspended:
    suspended_at: Tick  # when the pause began; §5.3
    suspended_by: SuspensionId
    due_tick: Tick | None  # what it was waiting for, if anything
    status: Literal["suspended"] = field(default="suspended", init=False)


type ActionState = Active | Suspended
```

`due_tick` is `None` exactly when the action is recurring (§5.7).
Scheduled versus recurring is a queue shape, not another action state, so it
does not multiply the union into four classes. There are exactly two state
classes: `Active` and `Suspended`; both carry the optional deadline needed to
restore the same queue shape after a pause.

**Binding is explicit — DECIDED.** Every `BaseAction` always contains one
complete binding value:

```python
@dataclass(frozen=True, slots=True)
class Unbound:
    pass


@dataclass(frozen=True, slots=True)
class Bound:
    seq: int
    state: ActionState


type ActionBinding = Unbound | Bound
```

A game constructs `Poison(...)` with `binding=Unbound()` and may hand that
same object to `ctx.schedule(...)`. The requested deadline exists in the
collecting context until the handler succeeds. On first admission,
`txn.schedule(action, due_tick)` replaces its binding with
`Bound(store_assigned_seq, Active(due_tick))`. If the handler raises, its
context is discarded and the untouched game object remains unbound.

Every later operation keeps the same game object and the same `seq`.
Rescheduling replaces only the bound state with a new `Active(due_tick)`;
suspension and resumption replace it with `Suspended(...)` and `Active(...)`
respectively. Game-owned fields such as poison damage, tick interval, and a
walk's `arrives_at` remain on the original subclass instance throughout.

`Unbound` is not a third `ActionState`. `ActionBinding` answers whether the
object has entered the queue; `ActionState` answers what state an admitted
object is in. Keeping `seq` and `state` together inside `Bound` prevents the
invalid halfway cases allowed by separate `seq: int | None` and
`state: ActionState | None` fields.

A separate queued wrapper was reconsidered and rejected. It would preserve a
complete inner game object, but `Poison.process()` would receive that inner
object as `self`; `ctx.schedule(self, ...)` would then lose the wrapper and its
stable `seq` unless the context added identity-mapping machinery. Binding the
same mandatory `BaseAction` in place preserves both the pleasant handler API
and the action's replay identity. M2 names the two store-facing operations
`bind(seq, state)` for first admission or hydration and
`reschedule(due_tick)` for an already-bound active action.

This shape makes four important mistakes impossible:

- A half-bound action — `seq` without state, or state without `seq` — cannot
  be represented.
- A suspended action without `suspended_at` or `suspended_by` does not
  type-check. The pause length and the owner of the wake-up cannot be lost.
- Done and cancelled cannot appear as live states; the action is removed
  instead (§5.5).
- A transition replaces one frozen state object with another. A scheduled
  deadline preserved while suspended is an intentional snapshot, not a
  second mutable field that can drift from the state.

`Active(due_tick=None)` is not an incomplete scheduled action; it is the
complete representation of an active recurring action. The deadline value
itself defines which queue shape the action belongs to.

`status` is a `Literal` rather than an enum member so the value in Python
*is* the value in the `status` column (§15) and on any wire — no mapping
layer. Exhaustive `match` over the union is checked by basedpyright, and
catch-all `case _` branches are banned precisely because they disable
that check (`docs/conventions/python-style.md`, §12.4).

`BaseAction` owns binding and the two state transitions, so a game developer
never writes them:

The sketch below begins after store admission and shows only the state
transitions. Replacing `Bound` always carries its existing `seq` forward.

```python
class BaseAction:
    def suspend(self, tick: Tick, by: SuspensionId) -> None:
        if not self.suspendable:
            return

        match self._binding:
            case Bound(seq=seq, state=state):
                match state:
                    case Active(due_tick=due):
                        self._binding = Bound(
                            seq,
                            Suspended(suspended_at=tick, suspended_by=by, due_tick=due),
                        )
                    case Suspended():
                        pass  # already asleep; suspending twice is a no-op
            case Unbound():
                raise RuntimeError("an unbound action cannot be suspended")

    def resume(self, tick: Tick) -> None:
        match self._binding:
            case Bound(seq=seq, state=state):
                match state:
                    case Suspended(suspended_at=at, due_tick=due):
                        paused_for = tick - at
                        self._binding = Bound(
                            seq,
                            Active(due_tick=None if due is None else due + paused_for),
                        )
                        self.on_resume(paused_for)
                    case Active():
                        pass
            case Unbound():
                raise RuntimeError("an unbound action cannot be resumed")

    def on_resume(self, paused_for: int) -> None:
        """Override to shift game-owned deadlines (`arrives_at`, ...) by the pause."""
```

**Why `on_resume` exists.** The engine can shift its own `due_tick`, but a
walk's `arrives_at` is payload it must not read (§3). Handing the game the
pause length is the only way both sides can shift by the same number —
and forgetting to shift is silent: Ivan simply arrives as though the
fight never happened.

---

## 7. Actions: objects and the mandatory `BaseAction`

### 7.1 An action is an object with `process()` — **DECIDED (reverses v1)**

v1 specified `Action` as **data only**, with behaviour looked up by
`kind` in a registry, on the grounds that this is what lets an action be
a Postgres row.

**Reversed.** The library is a set of interfaces; the natural shape is an
object the consumer implements. The serialisation concern is real but it
is the **store's** problem, not the action's (§7.3).

### 7.2 `BaseAction` is the mandatory blueprint — **DECIDED (reverses M1)**

Every game-defined action that enters foliot's queue inherits the library
class:

```python
class Poison(BaseAction[World]):
    def __init__(self, entity_id: EntityId, damage: int, tick_interval: int):
        super().__init__(entity_id, suspendable=False)
        self.damage = damage
        self.tick_interval = tick_interval

    def process(self, ctx: TickContext[World]) -> None:
        ...
```

`BaseAction` owns `entity_id`, `suspendable`, the `Unbound | Bound` binding,
stable `seq`, and all suspension bookkeeping. The subclass owns its behaviour
and every game-specific field. Binding replaces only the library-owned binding
value, never the subclass object, so `damage`, `tick_interval`, `arrives_at`,
and `process()` survive admission and every later transition unchanged.

This is intentionally different from the other foliot boundaries. `Store`,
`Driver`, `Rng`, `Effect`, and `TickContext` remain structural Protocols because
they are ports with no engine-owned state. Actions carry invariants that every
game needs and must implement identically; allowing a structurally compatible
action to bypass `BaseAction` would make the game responsible for stable
identity and suspension correctness.

The M1 `Action` Protocol is therefore superseded at M2. Queue-facing
signatures use `BaseAction[W]`, and a store must never return an action whose
binding is `Unbound`. The name remains useful as the ordinary concept — “an
action” — but there is no second `Action(BaseAction)` object and no conversion
that could discard subclass fields (§6.4).

### 7.3 Serialisation belongs to the store — **DECIDED**

An object in RAM does not survive a restart, and the world runs for
months, so pending actions must reach disk. A row is columns — text,
numbers, JSON — and cannot hold a Python object. Something must convert.
That conversion is all "serialise" means.

**Why not `pickle`.** Pickle stores a *pointer to the class by name*.
Demonstrated concretely during this session: pickle a `WalkAction`,
rename the class to `Walk` in a routine refactor, and the next load
fails —

```
AttributeError: Can't get attribute 'WalkAction' on <module 'game.actions'>
```

Ivan is stuck mid-forest forever, and the only fix is renaming the class
back. Same breakage on moving the file, or adding a required field that
old rows lack.

**The `kind` + `payload` alternative.** Store plain data and a label:

| kind | payload | due_tick |
|---|---|---|
| `walk` | `{"destination": "oakvale", "arrives_at": 5340}` | 5340 |

Nothing in that row is Python, so nothing in it can break. Rebuilding is
a dictionary lookup:

```python
REGISTRY = {"walk": Walk, "poison": Poison}


def decode(kind, payload):
    return REGISTRY[kind](**payload)
```

That dictionary is the whole of "the registry."

**Where foliot stands.** It does not impose `kind` on anyone.

- **In memory, no conversion happens at all.** The in-memory store holds
  the objects. No `kind`, no payload, no registry. Layer 1 never mentions
  any of it.
- **For durability, the consumer's store owns conversion.** Two plain
  functions are one possible shape:

```python
def encode(action) -> tuple[str, dict]: ...  # object -> (kind, payload)
def decode(kind, payload) -> BaseAction: ...  # (kind, payload) -> game object
```

A consumer's Postgres, MariaDB, MySQL, or file store may use that pair; foliot
does not require it and `MemoryStore` needs no conversion at all. So `kind` is
part of the consumer's *storage* design, appearing only when that consumer
chooses it — not a tax the core charges everyone.

### 7.4 The handler contract: a collecting context — **DECIDED**

A handler is one method, and it returns nothing:

```python
def process(self, ctx: TickContext[W]) -> None: ...
```

It *does* things — `ctx.emit(...)`, `ctx.schedule(...)`, `ctx.finish()` —
but **nothing it says takes effect when it says it.** `TickContext`
collects.

- Two things to read: `ctx.tick`, and `ctx.rng` — already bound to
  `(world_seed, entity_id, tick, seq)`, so the handler seeds nothing and
  never imports `random` (§9.2).
- Four things to say: `emit` an effect, `schedule` an action
  (`due_tick=None` makes it recurring, §5.7), `log` a line of narrative,
  `finish` to leave the queue.
- `ctx.finish()` exists because a recurring action has no deadline to
  decline. A successfully processed scheduled occurrence is consumed by the
  tick transaction; scheduling a successor gives it another deadline, while
  not rescheduling ends it. A handler that raises is not successful, so its
  occurrence remains pending along with the rest of its discarded context.

**Why not return an `Outcome`?** Returning a value was the first design,
and it is equivalent in every guarantee. The imperative spelling was
preferred because it reads the way the owner thinks about the game — the
action does its own thing and queues its own successor — and the
guarantees survive intact as long as the context collects rather than
writes. `Outcome` remains layer 2's vocabulary (§10.4), where a resolver
genuinely does return one.

**The engine gives each action a fresh context, and drains after the
whole loop, not during it.**

- The handler returns normally → its collected work joins the tick's pile.
- The handler **raises** → its context is discarded whole. Nothing it said
  happens — not the effect it emitted before the exception, not the
  successor it queued — and the engine skips it and carries on rather than
  aborting a tick because one action has a bug.
Every due action is asked what it wants; only then is anything written.

**A skipped failure is never a silent failure.** The engine reports it through
Python's operational logging at `ERROR` level, with the full traceback plus the
tick, action class, `entity_id`, and permanent `seq`. It is deliberately not a
`ctx.log(...)` story line: technical failures must not become deterministic
game history, and the failing context is discarded. Foliot does not install or
replace the consuming application's logging configuration, but under Python's
default behaviour an unconfigured `ERROR` record is still written to stderr;
an application may route it to its own console, file, or monitoring service.

**A context may not both finish and reschedule its owning action.** The private
concrete context remembers the action for which the engine created it. After
`process()` returns and before any collected work reaches the transaction, the
engine validates that `ctx.finish()` was not combined with
`ctx.schedule(the_same_action, ...)`, comparing the object by identity. That
combination says both "delete me" and "keep me" and is reported and discarded
as a developer error. Finishing while scheduling a *different* successor is
valid, as is rescheduling the owning action without finishing. Validation is
after `process()` so a handler cannot bypass it by catching an exception from
one of its own context calls; this is private engine logic and adds nothing to
the public `TickContext` protocol.

**One action object may be scheduled at most once in a tick.** Its binding has
one permanent `seq` and one active deadline, so two schedule requests for the
same object cannot mean two future occurrences. The engine validates all
collected schedules by object identity before application and reports a
duplicate as a developer error rather than silently choosing a deadline or
creating a second queue entry. Every context that requested that duplicated
object is discarded whole and reported, while unrelated contexts continue.
The source actions remain pending in their original states and receive fresh
contexts on the next tick; contexts themselves are one-attempt values and are
never persisted or resumed. A duplicated unbound target remains unbound, and
an already-admitted target remains unchanged. Two separately constructed
actions remain valid even when they share a class and entity. To run one
scheduled action at ticks 100 and 120, schedule only its next occurrence at
100; when it processes at 100, it reschedules the same object for 120,
preserving its `seq`.

**Retry means retention, never duplication.** A handler failure does not
enqueue anything: its collecting context is discarded before the transaction
is drained. The one existing action remains in its one queue position, with
the same permanent `seq`, binding, and `due_tick`, and may therefore be returned
again by `due(...)` on the next tick. A failed recurring action likewise
remains one member of the recurring set. No retry counter, copied action, new
row, or second queue entry is created. This preserves pending work without
turning repeated failures into duplicate work.

**Effect failures are different and abort the tick.** A handler fails while
describing work, before any mutation, so its context can be isolated. An
`Effect.apply(txn.world)` failure happens after application has begun and may
follow earlier successful effects. The engine must not catch it and continue:
the exception escapes the tick transaction, the durable adapter rolls back the
world, queue, journal, sequence allocator, and clock together, and the same
unfinished tick may be retried later. The escaping exception and traceback
make the failure visible without converting it into deterministic story text.
`MemoryStore` rolls back only its foliot-owned state here; it cannot generically
undo mutations an arbitrary Python world object already received (§8.5).

Two consequences, and they are the reason for the whole shape:

1. **Order-independence stops being a rule and becomes a property.** No
   action can observe another's changes within a tick, because there are
   no changes yet. Guarantee 2 (§2) is structural rather than something
   handler authors must respect, and §5.6's ban on same-tick scheduling
   falls out for free. A priority order would restore *determinism* while
   destroying *order-independence* — it fixes guarantee 1 by giving up
   guarantee 2, which is the more valuable of the two, so it is not the
   answer.
2. **A handler that crashes changes nothing.** Under a context that wrote
   as it went, a handler failing on its second line would have already
   half-mutated the world inside the transaction that commits.

**Testing costs one small fake.** A recording `TickContext` is about ten
lines in `tests/fakes.py`, written once, and a handler is then callable
with no engine and no store — assert on what it collected
(`docs/conventions/testing.md`).

**Keep `TickContext` narrow.** It is the one object every handler touches
and therefore the one most likely to rot into a service locator. Two
reads and four verbs; a proposal to add `ctx.store` or `ctx.world` is the
alarm, not the feature. The discipline that contains it: **pass the
capability, not the context** — a handler hands `ctx.rng` downstream,
never `ctx`, so nothing below `process()` can reach the scheduler.

**Effects are game-defined and opaque to the engine.** The engine calls
`effect.apply(txn.world)` and knows nothing else — not `hp`, not
`entity_state`, not `damage`. The world comes through the tick's transaction
so the mutation and queue changes commit together (§8.4, §10.4).

---

## 8. Persistence: the tick is the transaction — **DECIDED**

### 8.1 The division of labour

**foliot owns *when* to save. The game owns *how* and *where*.** The
library ships no database and no dependency, but it does not leave the
timing to the game, because the timing is the hard part and only the
engine knows it.

This follows from §2: two of foliot's guarantees — nothing pending is
lost, nothing is applied twice — are properties of the boundary between
memory and disk. A library that says "persistence is your problem" cannot
make them, and is then just a scheduler.

### 8.2 Not periodic checkpointing

Saving "from time to time" was considered and rejected. Checkpoint every
60 seconds, crash at 59, and:

- Ivan's walk shows 340 ticks remaining on disk, but the wolf that killed
  him was resolved 40 seconds ago and *that* effect already reached the
  world tables.
- The queue and the world now disagree, and nothing in the system can say
  which is right.

The world and the queue must move together or the state is incoherent,
and there is exactly one moment when they are guaranteed coherent: the
tick boundary.

### 8.3 What lands atomically

Everything tick N did — actions marked done, new ones enqueued,
cancellations, suspensions, effects applied, `current_tick` advanced —
lands as one atomic write, or none of it does.

Consequently, an exception from `Effect.apply(...)` is tick-fatal. Once world
application has started, skipping one failed effect and committing the rest
would make the tick partially applied. The exception leaves the transaction
and forces rollback; only handler exceptions can be isolated and skipped,
because handlers run before writes and their private contexts can be discarded
whole (§7.4).

At 1 tick/second that is 86,400 transactions a day, which is nothing for
Postgres. Under `ManualDriver`, the in-memory store performs no I/O but its
transaction is not a no-op: it stages foliot-owned queue changes, bindings,
log lines, and the clock advance, then publishes them together on a clean
exit. An exceptional exit discards the staged work.

### 8.4 This closes the idempotency question

v1 listed "idempotent handlers vs. fully transactional application" as
open. If the tick is the transaction, the question dissolves: there is
never a half-applied tick, so on restart you read `current_tick` and
continue. The wolf cannot be killed twice, because "the wolf was killed"
and "that action was marked done" are the same write.

**The one honest caveat.** This holds while effects land in the same
transactional store as the queue. If an effect reaches outside — a
different database, an HTTP call, a notification email — atomicity is
impossible and *those specific effects* must be made idempotent by
whoever writes them. foliot should say so in the docstring rather than
pretending otherwise.

### 8.5 The `Store` protocol

Small, and it names the boundary rather than the storage:

```python
class Store[W](Protocol):  # the cabinet: reading, and opening a tick
    @property
    def world_seed(self) -> int: ...

    def current_tick(self) -> Tick: ...
    def due(self, tick: Tick) -> Iterable[BaseAction[W]]: ...
    def tick_transaction(self, tick: Tick) -> AbstractContextManager[Txn[W]]: ...


class Txn[W](Protocol):  # the tray: writing, valid only inside a tick
    @property
    def world(self) -> W: ...

    def schedule(self, action: BaseAction[W], due_tick: Tick | None) -> None: ...
    def delete(self, action: BaseAction[W]) -> None: ...
    def suspend(self, entity_id: EntityId, by: SuspensionId) -> None: ...
    def resume(self, by: SuspensionId) -> None: ...
    def log(self, tick: Tick, line: str) -> None: ...
```

**This is the engine's view of persistence, not a complete application
repository API.** A concrete adapter may also expose operations for its own
domain — initializing a model, admitting an external input, creating a market,
loading a report, or, in the motivating game, creating a world or character.
Those operations are deliberately absent from `Store`: another simulation may
have none of those nouns. The core test still applies (§3): if the engine does
not call an operation while advancing ticks, it does not belong in this
protocol. The consuming application owns its setup and external-input
transactions alongside its database schema and serialization.

`MemoryStore` nevertheless needs a domain-neutral way to seed a new in-memory
simulation, because there is no database setup transaction to pre-populate it.
Its constructor therefore accepts an optional ordered `initial_actions`
iterable of `(action, due_tick)` pairs. Construction admits each unbound action
and assigns permanent `seq` values in iterable order without processing or
advancing a tick. `None` creates a recurring action; a concrete initial
deadline may equal `current_tick` so it can run on the first tick, but may not
be in the past. This is a convenience on the concrete `MemoryStore`, not a
method on `Store` and not a model for domain-specific production setup.

`Txn.schedule(...)` accepts the mandatory game object in either binding phase.
For `Unbound`, the store allocates the action's one permanent `seq` and calls
`action.bind(seq, Active(due_tick))` after successful admission. For an
already `Bound` action rescheduling itself, it calls
`action.reschedule(due_tick)`, which preserves `seq` and replaces only the
active state. `Store.due(...)` returns admitted objects only; seeing `Unbound`
there is a store-contract violation (§6.4, §9.4b).

**Two objects, because reading is always legal and writing is not.** The
only way to obtain a `Txn` is to be inside a tick, so there is no
`store.schedule(...)` anyone *could* call at the wrong moment. The rule
stops being something to remember and becomes something unsayable.

**Nothing on the `Txn` is a game verb.** No `damage`, no `hp`. Game state
changes travel as `Effect` objects the engine only knows how to `apply`
(§7.4, §10.4). A `txn.damage(...)` method would put the game inside the
library, which is §3's line.

**The clock advance is implicit, and that is deliberate.** There is no
`advance_to`. `tick_transaction(5)` already knows it is tick 5, so a
clean exit records tick 5 as finished and `current_tick()` then returns
6 — in the same commit as the work. §8.3 says the world and the clock
move together; making them one operation means no store *can* implement
it otherwise.

The engine never calls "save". It does its work inside a boundary the store
defines. The in-memory context manager stages changes and publishes them on
success; Postgres uses `BEGIN` / `COMMIT`. That is why the interface asks for
a context manager rather than a `save()` method: both implementations can
provide the same clean-exit commit and exceptional-exit rollback shape.

The in-memory transaction can make only **foliot-owned state** atomic. It
cannot generically undo an `Effect` that has already mutated an arbitrary
game-owned Python object supplied as `W`. M4 therefore exposes that object
directly as `txn.world` and guarantees rollback only for its queue, action
bindings, log, sequence allocator, and clock. This is an explicit limitation
of the dependency-free test/quickstart store, which cannot survive a process
exit anyway; a consumer-owned durable adapter puts its game-world writes in
the same real transaction as its queue writes.

**`Txn` must batch.** Measured against Postgres 16 with one tick per
transaction: 10,000 actions rescheduling themselves costs **196 ms/tick
(20% of a 1s tick)** when writes are flushed as set-based statements, and
**2,962 ms/tick — 296%, permanently behind** when each call issues its own
statement. Same rows, same single commit; 15x apart. So `txn.schedule(...)`
**collects**, and the flush at commit is one `INSERT ... SELECT unnest(...)`
and one `DELETE ... WHERE id = ANY(...)`. The declarative handler contract
(§7.4) hands this over for free: outcomes are already gathered before
anything is applied, so the batch exists before the writing starts.

(Sleeping actions cost nothing in the same measurement: 10,000 rows due
far in the future never appeared in a statement, because the partial index
skips them. That is §5.1's argument, measured.)

Caching is the store's business too. The near-horizon window — pull the
next few minutes into memory rather than querying every tick — is an
optimisation a consumer's Postgres store may make internally. The engine just asks
`due(tick)` and does not care whether that hit RAM or disk.

### 8.6 The shape, end to end

A deliberately minimal sketch — no ordering, no RNG, no statuses — showing
only who owns what. This ran; the output is in the comments.

```python
# ---------- what foliot ships ----------
class Simulation:
    def __init__(self, store):
        self.store = store  # game hands it in here

    @property
    def tick(self):
        return self.store.current_tick()  # read-only: nobody sets the clock

    def process_tick(self):  # never sleeps; the driver paces
        tick = self.tick
        with self.store.tick_transaction(tick) as txn:
            pile = []
            for action in self.store.due(tick):  # 1. ask everyone
                ctx = Context(tick, rng_for(action, tick))
                try:
                    action.process(ctx)
                except Exception:
                    continue  # this context is discarded whole
                pile.append((action, ctx))

            for action, ctx in sorted(pile, key=lambda p: p[0].seq):  # 2. then apply
                # A successful tick consumes this action's current scheduled
                # occurrence. Re-scheduling below creates its next occurrence;
                # recurring actions remain until ctx.finish(). The concrete
                # store mechanism is deliberately omitted from this tiny sketch.
                for new_action, due in ctx.schedules:
                    txn.schedule(new_action, due)
                for effect in ctx.effects:
                    effect.apply(txn.world)  # game-defined; engine never looks in
                for line in ctx.lines:
                    txn.log(tick, line)
                if ctx.finished:
                    txn.delete(action)
        # commit, and the clock advance, happened when the `with` closed


# ---------- what the game developer writes ----------
class Walk(BaseAction):  # recurring: runs every tick
    def process(self, ctx):
        if ctx.tick >= self.arrives_at:  # arrives_at is game payload
            ctx.log(f"{self.entity_id} ARRIVES")
            ctx.finish()


class Damage:  # an Effect. foliot never reads it.
    def __init__(self, entity_id, amount):
        self.entity_id, self.amount = entity_id, amount

    def apply(self, world):
        world.entity_state(self.entity_id).hp -= self.amount


class MyStore:  # three methods + a Txn
    ...
```

| foliot | the game |
|---|---|
| `Simulation` — holds the clock, owns the loop | `Walk` — an object with `process()` |
| opens and closes the tick boundary | `MyStore` — three methods over its own database |
| decides *when* to save | decides *where* and *how* to save |
| defines the `Store` protocol | implements it |

The developer's entire persistence obligation is writing `MyStore` once.
After that they write `process()` methods and never think about saving.

---

## 9. Randomness — **DECIDED**

### 9.1 Per-entity, counter-based streams

```python
rng = counter_rng(world_seed, entity_id, tick, seq)
```

The public seed value is an unsigned 128-bit Python `int`:

```python
0 <= world_seed < 2**128
```

Foliot provides `new_world_seed() -> int`, backed by cryptographically secure
operating-system randomness (for example, `secrets.randbits(128)`). The game
explicitly calls it **once** when it creates the world, persists the returned
value permanently, and passes that value back to foliot. Seed generation is
never an import side effect and is never inferred from the clock.

The helper is the safe production default, not a mandatory source. A game may
provide any integer in the valid range, including `0` or `1`; simple fixed
seeds are useful for tests, examples, deliberate seeded simulations, and bug
reproduction. Foliot validates the representation, not its entropy: non-ints,
`bool`, negative integers, and integers at least `2**128` are rejected. It
cannot and should not try to decide whether a valid number is "random enough."

The public `Rng` contract has exactly two draws:

```python
rng.random()   # float in [0.0, 1.0)
rng.below(n)   # uniform integer in [0, n)
```

There is no `chance(probability)` convenience method. The game owns the
probability formula and writes `rng.random() < probability` itself; foliot
would add vocabulary but no guarantee by wrapping that one comparison.

`below(n)` accepts exactly `1 <= n <= 2**64`. Supporting larger Python
integers would require joining multiple 64-bit draws for no realistic queue or
game selection. Non-ints, `bool`, zero, negative values, and values greater
than `2**64` are rejected. Every valid call consumes at least one draw,
including `below(1)`, so the draw index continues to mean the number of public
draw calls/attempts already made. Rejection sampling may consume additional
draws when needed to avoid modulo bias.

For ZPG, this means the one long-lived shared world: many players put
characters into that same world and watch them live. The seed is created
once for that world and remains fixed for its entire lifetime; it does not
imply that the game creates a separate world per player or playthrough.

**Determinism is not predictability when the seed is securely generated and
kept private.** Because `tick` is part of the stream identity, walking the same
forest path at tick 500 and at tick 10,000,000 draws from unrelated positions.
A deliberately public or guessable seed such as `1` remains statistically
useful and exactly reproducible, but a sufficiently determined player could
calculate future results. The permanent production world's seed therefore
stays outside player-facing state.

### 9.2 Why a global stream fails: the Ivan/Petra example

The objection to a shared global RNG is not philosophical. Consider tick
5000, with two fights that have nothing to do with each other:

- **Event A** — Ivan vs a wolf in the forest
- **Event B** — Petra vs a bear, a thousand miles away

A global RNG is one stream with one cursor, about to produce
`0.91, 0.12, 0.44, 0.03`. A hit needs a roll under 0.5.

- Resolve **A first**: Ivan takes `0.91` → miss. The wolf takes `0.12` →
  hit. Then Petra takes `0.44`, the bear `0.03`.
- Resolve **B first**: Petra takes `0.91`, the bear `0.12`. Then **Ivan
  takes `0.44` → hit**, and the wolf `0.03`.

Same tick, same state, same intents, same resolver. Ivan lives or dies
depending on whether Petra's fight was processed first. **Petra changed
Ivan's outcome and she is on another continent** — which is exactly the
per-character isolation property (§1.5) that everything else is built to
protect. The coupling is the cursor position, and the cursor appears in
no table.

Two direct consequences:

- **Crash recovery becomes lossy.** Re-running the unfinished events of a
  partially-written tick draws from a different cursor position, so the
  world after recovery is not the world that was interrupted.
- **The queue can never be safely reordered.** A database returning the same
  rows in a different order, a refactor, or a deliberately shuffled test can
  change outcomes.

Note also that a global RNG silently cancels the order-independence won
by the two-phase design (§10.1). It reintroduces order-dependence through
the back door.

### 9.3 Cost, measured

The owner's concern — that per-entity seeding would be expensive — is
correct about the naive implementation. Measured on Python 3.11,
200,000 draws each:

| Approach | per draw | draws/sec |
|---|---|---|
| shared global `random.random()` | 0.054 µs | 18.5M |
| new `Random(seed)` for every draw | 6.37 µs | 157k |
| one `Random` per (entity, tick), 4 draws | 1.59 µs | 631k |
| **counter-based (splitmix64), one counter** | **0.32 µs** | **3.1M** |
| keyed `blake2b(8)` per draw | 0.49 µs | 2.0M |

The naive version is **118× slower** than the global stream, and the
reason is mechanical: `random.Random(seed)` builds a Mersenne Twister —
624 words of internal state initialised from the seed — and then throws
all of it away to take one float. You pay for a 19937-bit generator to
get 53 bits out.

**The fix is to stop constructing a large generator and start computing the
answer.** M3 uses keyed BLAKE2b once to turn
`(world_seed, entity_id, tick, seq)` into a stable 64-bit stream seed. The
three identity parts are length-prefixed before hashing, so values cannot
become ambiguous through concatenation. The world seed is encoded as exactly
16 bytes and used as the BLAKE2b key; the digest is personalised with
`foliot-rng-v1`.

Each `Rng` then owns only a 64-bit `draw_index`. A draw adds the next counter
step to the stream seed and applies the SplitMix64 integer mixer. `random()`
takes the upper 53 bits and scales them into `[0.0, 1.0)`. `below(n)` uses
rejection sampling, so the uneven remainder of the 64-bit range cannot make
some choices microscopically more likely than others. There is no global
cursor and no Mersenne Twister setup.

This exact encoding and mixing sequence is part of the replay contract, not a
private optimisation: changing it changes history for every existing seed.
`tests/test_rng.py` therefore contains golden output vectors. A future
algorithm change needs an explicit compatibility/versioning decision rather
than a regenerated expected value.

At 10,000 Chars making five draws each per tick — 50k draws/second —
splitmix64 costs about **1.6% of one core**. Not a cost worth designing
around. And it is *better* than a Mersenne Twister here, because every
draw is independently addressable: you can ask "what was Ivan's third
roll in tick 5000?" a year later without replaying anything.

### 9.4 A real bug in the v1 snippet

v1 §5.2 wrote `hash((world_seed, entity_id, tick, seq))`. **Python's
`hash()` on strings is salted per process** (`PYTHONHASHSEED`), so
`hash("ivan")` differs between runs of the same program. That snippet
silently breaks replay across a restart, in a way that looks like a rare
mysterious bug rather than a configuration problem.

Use a stable hash — `blake2b`, or pure integer mixing on integer ids.
Cheap to get right, nasty to diagnose later.

### 9.4b `seq` must never come from processing order — **DECIDED**

`seq` is the action's stable identity for its entire queued lifetime, assigned
by the store on first admission and preserved through every reschedule,
suspension, and resume (§6.4, §15's column). It distinguishes two actions of
one entity within a tick, but it is **not** their position in the due list, and
the distinction is not cosmetic.

Written positionally, two of one entity's actions rolling in the same tick
simply swap results when the tick is shuffled:

```
seq from position:  ['walk','forage'] -> walk=0.7675  forage=0.3195
                    ['forage','walk'] -> walk=0.3195  forage=0.7675
seq as a stable id: ['forage','walk'] -> walk=0.7675  forage=0.3195
```

So a positional `seq` breaks guarantee 1 *and* guarantee 2 at once, and it
does so silently — every individual run is self-consistent. Found by
writing the first worked example, which is what §14's order-independence
test exists to catch.

The same identity fixes the order the journal is written in: a tick may be
**processed** in any order, but its story is **told** in `seq` order.
Otherwise two runs of the same seed produce the same world and different
logs — verified, and in a ZPG the log is the product.

### 9.5 Deterministic ids for spawned entities — **DECIDED**

Ephemeral entities (a wolf, §11.2) still need an identity while they
exist, because the RNG seeds on `entity_id`. If that id comes from
`uuid4()`, replay is dead.

The game's design makes this easy: an ephemeral entity never exists
outside its event, so its id can be derived — `(event_id, 0)`,
`(event_id, 1)` — from an event that already has an identity. Stable and
reproducible for free. No database row, no UUID.

---

## 10. Layer 2: intents, events, resolvers

### 10.1 The two-layer split — **DECIDED**

foliot is built as two layers, and **layer 1 must be complete and useful
without layer 2**.

- **Layer 1** — clock, queue, drivers, RNG, store, actions. A handler is
  `process(ctx) -> None`, telling a collecting context about schedules,
  effects, log lines, and completion. No
  intents, no grouping. This alone runs walking, poison, cooldowns,
  travel — most of a world.
- **Layer 2** — intents, event grouping, resolvers. A separate importable
  module, added after layer 1 is real.

If layer 1 cannot be used without layer 2, we have built a framework with
a mandatory opinion. If it can, we have built a library.

**Why layer 2 is in at all**, by the §2 test: it adds a guarantee the
consumer cannot bolt on — **simultaneity.** N participants each decide
against the same frozen state, and no participant observes another's
mutations. That requires controlling the decide/apply split inside the
tick, which is the engine's job.

The pipeline it provides:

```
queue.due(tick)                -> [Action]
  for each action: decide()    -> [Intent]     (reads frozen state)
  group intents by event_key   -> [Event]
  for each event: resolve()    -> Outcome      (the only writer)
  apply effects, enqueue schedules, delete cancels
```

The decide phase never mutates. The resolve phase never reads anything it
was not handed. That split is what makes ticks order-independent.

**A resolver must re-validate its participants** at resolution time.
Between decide and resolve, a participant may have died in a different
event during the same tick. Degrade gracefully; never assume both parties
still exist.

### 10.2 The rendezvous problem is resolved — **DECIDED (closes v1 §8.1)**

v1's blocking question was: how do two parties independently arrive at
the same `event_key`? Option A was symmetric derivation (both compute
`combat:` plus the sorted pair of ids); Option B was an explicit event
entity that one party opens and the other references.

**The game's design answers it: Option B, and the rendezvous largely
disappears.** The environment *opens* the event and enrols participants,
so nobody has to guess a key. Consequences:

- `Event` **is persisted**: it has an identity, a lifetime, and something
  must close it.
- Option A remains available for incidental interactions where nobody
  needs to open anything. The two coexist.
- This unblocks the registry and the schema, which v1 flagged as gated.

### 10.3 No reaction pass is needed — **DECIDED**

A concern raised and then dissolved. "The forest decides whether a wolf
appears" implies the environment acts *because it was targeted*, not
because it had something due — which would require a second pass in the
tick pipeline that the design does not have.

It is not needed, because of one detail in the game's design: the
forest's roll happens **inside the walking Char's own decide**, and the
resulting event is **scheduled for the next tick** rather than created
mid-tick. So the pipeline stays single-pass: decide, group, resolve. No
ordering subtlety about who reacts to whom.

### 10.4 The layer-2 vocabulary

Carried forward from v1, which got this part right:

- **`Intent`** — what an entity wants to do, before anyone knows whether
  it succeeds. Emitted by deciders.
- **`Event`** — a bundle of intents sharing an `event_key`, resolved
  together as one unit.
- **`Outcome`** — everything a resolver produces: `effects`, `schedules`,
  `cancels`, `log`. Purely descriptive; no side effects.
- **`Effect`** — a game-defined mutation object with `apply(world)`. The
  only thing permitted to write game state. **The engine calls `apply`
  and knows nothing else** — not `hp`, not `entity_state`, not `amount`.
  Anything richer in the signature puts a game noun in the library (§3).
  The engine obtains that world as `txn.world`, so the write lands in the
  same transaction as the queue *by construction*. The game defines the
  world type and its own `Txn` implementation; foliot never looks inside
  either one.
- **`World`** — read access, required to present a *stable* view for the
  duration of a tick's decide phase. How (snapshot, copy-on-write, MVCC
  transaction) is the implementation's problem.

Two deliberate calls worth remembering:

**Effects as objects rather than direct mutation.** Costs a layer of
indirection. Buys: resolvers stay pure and unit-testable (call with a
fake world, assert on the return value), effects can be logged as an
audit trail of *why* state changed, and application order becomes
explicit rather than incidental.

**`log` as a first-class `Outcome` field** rather than derived from
effects. In a ZPG the log *is* the product, so observer-facing narrative
deserves its own channel rather than being scraped out of mutations.

### 10.5 Two mechanisms rescued from the v1 draft — **RECORDED for M8**

`docs/reference/protocols-draft.py` was deleted at M1 (§19). Everything in
it was superseded except these two, which are not written down anywhere
else and are worth having when layer 2 is built.

**A solo intent needs no special case.** An intent with no `event_key`
derives a unique one — `solo:{event_kind}:{actor_id}:{kind}` — which makes
it a one-participant event. The engine then has exactly one code path:
group intents by key, resolve each group. There is no branch for "only one
participant", and therefore no branch that can be wrong. Worth keeping
because the obvious implementation special-cases the common case and then
diverges from it.

**An intent should remember the action that produced it.** The draft
carried `source_action_id` so a resolver could cancel the losing branch:
the encounter fired this tick, so the arrival that would otherwise have
happened is cancelled. The need survives even though the mechanism has
moved on — cancellation is now deletion rather than tombstoning (§5.5),
and layer 1 hands the engine the action object rather than an id — but a
resolver still has to be able to say "and that other thing is not going to
happen now."

---

## 11. The game model (recorded here, built elsewhere)

Recorded so the library's requirements are traceable to something. **None
of this belongs in `src/foliot/`.**

### 11.1 Actions are directed

Every action in the game is defined by two things: who is doing it, and
what it is done against.

- walking in the forest — `walk(Char, Forest)`
- being poisoned while walking — `damage_effect(Forest, Char)`

An action is a directed edge. In library terms only the *owner* matters
(§3); the target is game payload.

### 11.2 The entity ontology

- **Char** — a player's character. The **only** entity that persists
  independently of its environment, and therefore the only
  self-sufficient actor: a **first-order action producer**. Chars
  schedule their own next action.
- **EventfulEnvironment** — a place (a forest). It is an entity, so it
  has somewhere to stand when it acts. It acts **only when targeted** —
  a Char walking through it gives it its chance to roll — and it can
  *spawn events*, which is what makes it "eventful."
- **Second-order action producers** — a wolf. Spawned by an environment,
  produces actions only inside an event, and does not exist outside that
  event at all.

**Why this matters to the library.** If only Chars schedule their own
next action, the queue is a forest of independent per-Char timelines that
touch only inside Events. That is exactly the per-character isolation
guarantee of §1.5, obtained for free — and it is what makes out-of-order
iteration harmless rather than a source of invisible coupling. The core
cannot enforce this rule, but it should be recorded as a game invariant.

### 11.3 Suspension in game terms

If an event involving another entity is produced — a wolf appears — the
walking activity is **suspended** and resumes when the battle event is
over. Poison, hunger and cooldowns continue throughout (§6.2).

### 11.4 Battle, in the layer-2 vocabulary

On the tick, every participant issues an **intent** against the other's
`entity_state`. The resolver then looks at both participants' stats and
decides which intents go through: the wolf bites, the Char misses, and
the Char's miss did not affect the wolf's bite at all — because both were
decided from the same frozen tick-start state.

### 11.5 Two-tier entity state

- **`entity`** — slow-changing: level, max HP, skill points, learned
  skills.
- **`entity_state`** — fast-changing: current HP, current mana,
  cooldowns, inventory.

Two cautions recorded from v1 and still valid:

**The "static" tier is not static.** Level, max HP and learned skills all
change on level-up or training. It is a *slow/fast* distinction, not
*immutable/mutable*. Internalising "entity is immutable" leads to
aggressive caching and stale max-HP after a level-up.

**Inventory as a field will strain — deliberately deferred.** An item
dropped in a clearing, or traded between characters, has no home in an
inventory blob; a transfer becomes two blob rewrites with nothing
enforcing that the sword exists in exactly one place. If items ever have
identity (durability, provenance), they want their own table with a
nullable owner (`NULL` = on the ground). Recorded only so the constraint
is known when it eventually matters. **Not core-library work.**
### 11.6 Events do not nest — **GAME INVARIANT**

An event cannot arise from inside an event. An action running as part of an
event does no encounter rolls of its own, so nothing an event contains can
open another one.

**Why the library cares.** It is the reason a Char is inside at most one
event at a time, therefore has at most one suspender, therefore can treat a
second `suspend()` as a no-op without losing time (§6.2). The core cannot
check this — it never sees an event in layer 1 — so it is recorded here as
a requirement the game owes the engine.

### 11.7 Goals: the long-running process

A **goal** is a multi-stage process — go to another town, then do something
there — held on the Char's `entity_state`, as a list of stages plus a
progress counter. It belongs to the Char because the Char is the only
first-order action producer (§11.2).

The game urges the Char forward with it: **when a Char has no active queued
action, take the current goal stage and derive the next action from it.**
That is what makes a stuck Char impossible without the core needing a
watchdog — a lost wake-up shows up as an idle Char, and idle Chars get work.
Mid-event Chars are not idle, because an event puts a per-tick action on
each participant (§11.4).

**None of this is core**, and it is worth being explicit about why, since
it is the closest a game concept has come to the line. Goals are read by
nobody in the engine, so by the §3 test they are payload. The one thing
the game needs in order to run them — *"does this entity have any active
queued actions?"* — is a question it asks **its own store
implementation**, not something the `Store` protocol must require of
everyone. The shipped in-memory store may offer it as a convenience for
`examples/` and tests; the protocol stays at three methods (§8.5).

---

## 12. Repository layout and tooling

### 12.1 Layout

```
foliot/
├── pyproject.toml              # uv-managed
├── uv.lock                     # committed
├── README.md
├── CLAUDE.md
├── docs/
│   ├── DESIGN_SNAPSHOT.md      <- this document
│   └── conventions/
│       ├── python-style.md
│       └── testing.md
├── src/
│   └── foliot/
│       ├── __init__.py         # curated public API surface
│       ├── py.typed            # required; uv init --lib creates it
│       ├── ids.py              # Tick, EntityId, SuspensionId
│       ├── rng.py              # Rng + counter-based streams
│       ├── effects.py          # Effect
│       ├── context.py          # TickContext
│       ├── actions.py          # BaseAction, ActionBinding, ActionState
│       ├── engine.py           # Simulation: the tick loop
│       ├── drivers.py          # Driver, ManualDriver, RealtimeDriver
│       ├── stores/
│       │   ├── __init__.py     # Store, Txn, MemoryStore export
│       │   └── memory.py       # in-memory reference implementation
│       └── events/             # LAYER 2 — optional, added after layer 1
├── examples/
│   └── tinyworld/              # smallest possible ZPG proving the API
└── tests/
```

- **One module per concept, protocol beside its implementation.** A single
  `protocols.py` was the earlier plan and was dropped at M1: in a library
  whose product *is* its protocols, collecting them all in one file
  separates each contract from the reasoning that produced it, and
  `rng.py` opening with the Ivan/Petra coupling is worth more than a
  tidy list of signatures. Each module's docstring carries the *why*.
- **`src/` layout**, not flat. Prevents accidentally importing from the
  working directory instead of the installed package — a classic source
  of "works on my machine" test results.
- **`stores/postgres.py` deliberately absent.** It belongs in an optional
  adapter owned by the consuming game (or an independent integration package),
  not in foliot. The same `Store` / `Txn` protocols cover PostgreSQL, MariaDB,
  MySQL, files, or another backend without adding dependencies to this library.
- **`examples/tinyworld` is load-bearing, not decoration.** It is the only
  honest test of whether the public API is pleasant, and it should be
  written early enough to still change the API.

### 12.2 Tooling — **DECIDED**

**`uv` for everything.** No bare `pip`, no hand-rolled venv. `uv sync`,
`uv add`, `uv run`, `uv build`, `uv publish`. `uv.lock` is committed. uv
moves fast — verify syntax against its docs rather than trusting this
paragraph.

Python 3.12+, `pytest`, `ruff`, `basedpyright` in strict mode on `src/` and
`tests/`.
Strict typing is not optional: the design combines structural Protocol ports
with a mandatory stateful `BaseAction`, and neither contract protects anything
without a type checker.

**Verified during this session** (uv 0.8.17; current is 0.12.x, and
nothing relevant changed):

```
uv init --lib --python 3.14
uv add --dev pytest ruff basedpyright
```

`uv init --lib` in a non-empty git repo leaves `README.md`, `LICENSE`,
`CLAUDE.md` and `docs/` untouched, and adds exactly:

```
pyproject.toml
.python-version
src/foliot/__init__.py     (a stub hello() to be replaced)
src/foliot/py.typed        (empty, required, free)
```

Two things to check in the generated `pyproject.toml`:

- **`requires-python`** is set from whichever Python uv found, i.e. the
  *dev* interpreter — but this field is the floor imposed on
  **consumers**, and the two are unrelated. Developing on 3.14 is right;
  demanding 3.14 of consumers is not. We want `>=3.12` (§12.4).
- **`authors`** is guessed from git config.
- **`description`** is the stub `"Add your description here"`, and it
  ships to PyPI.
- **`license`** is absent although `LICENSE` exists.

`uv add --dev` puts tools in a dependency group, **not** in
`dependencies`, so `dependencies = []` stays empty. Someone doing
`pip install foliot` gets zero transitive packages; only contributors
running `uv sync` get pytest and friends. That is the zero-dependency
promise, mechanically enforced.

Ship `src/foliot/py.typed` or consumers get no type information at all.

### 12.3 Library hygiene

Collected because the owner has not shipped a library before. Small
conventions, but they are the difference between a library and a project
folder:

- No `logging.basicConfig`. Use `logging.getLogger(__name__)` and let the
  consuming application configure handlers.
- No side effects at import time.
- No reading config files from disk. Configuration is passed in.
- Dependencies list stays empty. Database adapters belong to the consuming
  game or an independent integration package; foliot ships only the protocols
  and its dependency-free `MemoryStore`.
- Persistence, clock driving, and randomness are all **injected
  interfaces**. The in-memory implementations that ship exist primarily
  so tests and quickstarts work without a database.

### 12.4 The toolchain as built — **DECIDED (M0, verified)**

Not a plan; what is on disk and what was demonstrated rather than assumed.

```
uv init --lib --python 3.14
uv add --dev pytest ruff basedpyright
```

Pinned at M0: uv 0.12.x, basedpyright 1.39.10 (pyright 1.1.412), ruff
0.16.5, pytest 9.1.1. `.python-version` is `3.14`; `requires-python` is
`>=3.12`. **These are different knobs and it is worth restating why:**
`.python-version` is the interpreter this project is developed on;
`requires-python` is the floor imposed on consumers. Confusing them is
the mistake §12.2 warns about, in whichever direction.

Three things were verified by running them, not by reading docs:

1. **`# type: ignore` is inert.** With `enableTypeIgnoreComments = false`,
   a deliberately wrong return still errors on the line carrying the
   comment. `reportUnnecessaryTypeIgnoreComment` catches the leftovers.
2. **The floor polices itself.** `class Later[A = int]` (PEP 696, 3.13+)
   fails on a 3.14 dev machine under both tools — basedpyright *"Type
   variable default types require Python 3.13 or newer"*, ruff *"Cannot
   set default type for a type parameter on Python 3.12"*. Ruff infers
   this from `requires-python`, so `target-version` is deliberately
   **unset**: setting it would let the two drift apart.
3. **`match` exhaustiveness bites, and `case _` disables it.** A synthetic
   four-member state probe with two members handled reports the two missing
   members by name. Adding
   `case _: raise AssertionError(...)` — a branch that looks defensive —
   reduces that to **zero errors**. Hence the ban on catch-alls
   (`docs/conventions/python-style.md`). This is what makes adding an
   `ActionState` member a compile error at every call site instead of a
   silent fallthrough at tick four million.

`basedpyright.include` is `["src"]` today; `tests` joins it as soon as the
test tree is created after M2, and `examples` at M7. A fake that drifts from
its Protocol is exactly what strict checking should catch
(`docs/conventions/testing.md`).

Metadata fixed at M0, all of it library-specific and all of it shipped to
consumers: `description` (was the uv stub), `license = "MIT"` with
`license-files` (PEP 639 — `uv build` produces sdist and wheel
successfully), `keywords`, and `classifiers` including **`Typing ::
Typed`**, which is how an installer learns `py.typed` is meaningful.

`[tool.pytest.ini_options]` sets `testpaths = ["tests"]` before `tests/`
exists; pytest 9 warns and collects nothing rather than erroring, so this
is safe until M1.

**Conventions live in `docs/conventions/`** — `python-style.md` and
`testing.md`. They are subordinate to this document: where they
disagree, this document wins and the convention file is what gets fixed.

---

## 13. Build order

Sequenced so each milestone is independently testable.

| # | Milestone | Depends on | Notes |
|---|---|---|---|
| M0 | Repo scaffold, `pyproject.toml`, lint/type/test config | — | `uv init --lib`; verify toolchain on the empty package. |
| M1 | **Done.** `ids`, `rng`, `effects`, `context`, `actions`, `drivers`, `stores` — the initial contracts, one module per concept | §3, §7 | Written fresh; the v1 draft was reference only. Its structural `Action` Protocol was useful for exposing the missing-`seq` problem, but the M2 binding decision supersedes that one contract (§7.2). |
| M2 | **Done; Ruff and basedpyright verified.** `actions.py` — mandatory `BaseAction`, `ActionBinding`, `ActionState`, suspend/resume | M1, §6 | `Unbound | Bound` keeps admission complete; the store assigns `seq` once, and every reschedule/transition preserves it. `suspended_at`, deadline shifting, and `on_resume(paused_for)` live here so games never reimplement them. |
| M3 | **Done; 36 tests, Ruff, and basedpyright verified.** `rng.py` — secure seed helper, counter-based streams, stable hashing | §9 | Manual unsigned 128-bit seeds; golden vectors and a real cross-`PYTHONHASHSEED` subprocess test lock replay. |
| M4 | **Done; 50 tests, Ruff, and basedpyright verified.** `stores/memory.py` — the built-in reference/test implementation of the storage chassis | M1, M2 | `Store` / `Txn` protocols already define the chassis. M4 proves them with `due_tick` buckets, deletion, `seq` ordering, single-runner enforcement, and staged commit/rollback for foliot-owned state. |
| M5 | **Done; 64 tests, Ruff, and basedpyright verified.** `engine.py` + `ManualDriver`; `MemoryStore(initial_actions=...)` bootstrap convenience | M1–M4, §8 | The two-phase tick loop, deterministic apply order, isolated visible handler failures, tick-fatal effect failures, context validation, and inclusive manual fast-forward. Initial in-memory actions are admitted without consuming an artificial tick; this does not widen the `Store` protocol. |
| M6 | `RealtimeDriver` | §4.3 catch-up policy | Manual first — it is what makes M5 testable. |
| M7 | `examples/tinyworld` | M5 | Written while the API can still change. |
| M8 | Layer 2: `events/` — intents, grouping, resolvers | M5, §10 | Optional module. Layer 1 must work without it. |

**Build `ManualDriver` before `RealtimeDriver`.** The manual driver is
not a testing afterthought; it is what lets the whole pipeline be
exercised at millions of ticks per second in CI. Writing the realtime
driver first tends to produce a design where time is implicit, and that
is very hard to back out of.

**Definition of done for v0.1:**

1. `examples/tinyworld` runs one million ticks under `ManualDriver` and
   produces a byte-identical log across two runs with the same seed.
2. Shuffling the processing order of same-tick actions does not change
   the outcome.
3. `RealtimeDriver` sustains a stable tick rate for an hour with no
   measurable drift.
4. `MemoryStore` publishes no foliot-owned state from a failed tick. Each
   consumer-owned durable adapter separately proves crash recovery against
   its chosen database or file format (§14).

Items 2 and 4 are worth writing first and are the ones most likely to
expose a design error rather than a coding error.

---

## 14. Testing strategy

Four tests carry most of the design's weight. Each fails for
*architectural* rather than implementation reasons.

**1. Replay determinism.** Run N ticks from seed S, hash the ordered log.
Re-run identically. Assert the hashes match. This protects the ability to
answer "why did my character die at tick 4.2 million." **Run it in a
subprocess with a different `PYTHONHASHSEED`** — that is what catches
§9.4.

**2. Order independence.** Take a tick with several due actions, process
them in a deliberately shuffled order, and assert the resulting world
state and log are identical. This is the only real test of the guarantee
in §2, and it is what a global RNG would break. If this cannot pass, the
design has a hole in it.

**3. Suspend/resume fidelity.** Suspend a suspendable action mid-flight,
advance many ticks, resume, and assert every deadline moved forward by
exactly the pause — the core's scheduled `due_tick`, if it has one, and
the game's own deadline through `on_resume(paused_for)`. Non-suspendable
effects such as poison and hunger must fire throughout. This is the test
for §6.

**4. Crash recovery.** For every consumer-owned durable adapter, interrupt
between "the handler ran" and "the tick committed," restart, and assert no
event is lost and no effect is applied twice. This validates §8 but lives in
the adapter's test suite, because foliot deliberately owns no database
implementation. Within this repository, `MemoryStore` tests the corresponding
exceptional-exit rollback for its own queue, binding, log, and clock state.

Beyond those: unit-test handlers as plain functions with a fake context
and a stub world. That is the entire point of §7.4 — if a handler cannot
be tested that way, the contract has been violated.

For the clock, inject a fake time source rather than sleeping. Drift
(§4.1) is testable by asserting that tick *n* targets
`start + n * duration`, with no real time elapsed.

If sampled waits are ever revisited, add: **sampled-wait distribution** —
property-test `geometric(p)` against a naive per-tick Bernoulli loop,
comparing the mean against `1/p` and ideally running a KS or chi-square
comparison.

---

## 15. Postgres notes

A sketch to argue with, not a migration. Deliberately minimal and
deliberately not game-shaped. Lives in the extra, not the core.

```sql
-- The queue. Source of truth; memory is a near-horizon cache.
CREATE TABLE scheduled_action (
    id          BIGSERIAL PRIMARY KEY,
    entity_id   TEXT        NOT NULL,
    kind        TEXT        NOT NULL,   -- from encode(); see §7.3
    payload     JSONB       NOT NULL DEFAULT '{}',
    suspendable  BOOLEAN    NOT NULL,  -- activity vs effect;      §6.2
    status       TEXT       NOT NULL DEFAULT 'active',  -- 'active' | 'suspended'
    due_tick     BIGINT,               -- NULL means recurring;    §5.7
    suspended_at BIGINT,               -- set iff suspended;       §5.3
    suspended_by TEXT,                 -- owes the wake-up;        §6.2
    seq          INTEGER    NOT NULL,           -- stable, store-assigned; §9.4b
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The state union of §6.4, enforced by the database rather than by care.
    CONSTRAINT action_state CHECK (
           (status = 'active'    AND suspended_at IS NULL AND suspended_by IS NULL)
        OR (status = 'suspended' AND suspended_at IS NOT NULL
                                  AND suspended_by IS NOT NULL)
    )
);

-- Recurring actions: read every tick, written only when one starts or ends (§5.7).
CREATE INDEX scheduled_action_recurring
    ON scheduled_action (entity_id)
    WHERE status = 'active' AND due_tick IS NULL;

-- The scheduler's hot path: everything due at or before tick N.
CREATE INDEX scheduled_action_due
    ON scheduled_action (due_tick, seq, id)
    WHERE status = 'active' AND due_tick IS NOT NULL;

-- Waking everything one event put to sleep (§6.2).
CREATE INDEX scheduled_action_suspended_by
    ON scheduled_action (suspended_by)
    WHERE status = 'suspended';

-- World clock. Single row. Must survive restart (§4.4).
CREATE TABLE world_state (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    current_tick BIGINT      NOT NULL,
    world_seed   NUMERIC(39) NOT NULL
        CHECK (world_seed >= 0 AND world_seed < 340282366920938463463374607431768211456),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Due query for the single simulation runner. The recurring active set
(`due_tick IS NULL`) is read separately or served from the store's cache, and
`Store.due(tick)` unions the two (§5.7):

```sql
SELECT * FROM scheduled_action
WHERE status = 'active' AND due_tick <= $1
ORDER BY due_tick, seq, id;
```

The supported architecture has exactly one active simulation runner per
world. Many API processes may serve players, and a standby runner may take
over after failure, but two runners never execute ticks simultaneously. The
global decision/apply barrier and single atomic tick would make concurrent
runners a different architecture, not a configuration switch.

**An `event` table is needed** — §10.2 settled that events are persisted,
with identity and lifetime. Something must close them.

**Still not designed: the log/journal table.** It matters more than usual
here because in a ZPG the log is the product, and it will be the largest
table by a wide margin. Partitioning by tick range is the obvious move,
but it should be designed deliberately rather than grown. **OPEN.**

One note carried from this session's discussion: intents and applied
effects should **not** share a table with `scheduled_action`. The queue's
hot query runs every tick forever and its pending set stays roughly
proportional to the number of live Chars; history grows without bound. Put
history in the journal, keep the queue small and hot.

---

## 16. Glossary

| Term | Meaning here |
|---|---|
| **Tick** | One discrete step of world time. ~1 real second, configurable. Monotonic integer. |
| **Action** | A game object inheriting `BaseAction`, owned by one entity and carrying `process()`. It starts `Unbound` and becomes `Bound(seq, state)` on first queue admission. |
| **`due_tick`** | The absolute tick at which an action becomes due. The queue's primitive. |
| **`suspended_at`** | The tick a pause began. Resume shifts every deadline — the core's and the game's — forward by `now - suspended_at`. |
| **Recurring** | An action that runs every tick and carries no `due_tick`, because for it the next due tick is always `now + 1`. |
| **`suspendable`** | One bit per action: does it stop when its owner is interrupted? Effects are not; activities are. |
| **`suspended_by`** | Id of the event that suspended an action, and therefore owes it a wake-up. |
| **Activity** | A suspendable action — one narrative beat. Game concept. |
| **Effect (game sense)** | Poison, hunger, cooldowns — actions belonging to no group, which tick through suspension. |
| **Effect (layer 2)** | A mutation object with `apply(world)`. The only thing permitted to write. |
| **Driver** | The thing that advances the clock. `RealtimeDriver` sleeps; `ManualDriver` does not. |
| **Store** | The consumer's persistence adapter: `current_tick`, `due`, `tick_transaction`. |
| **Tick transaction** | The atomicity boundary. Everything in tick N lands together or not at all. |
| **Intent** | *(Layer 2)* What an entity wants to do, before anyone knows whether it succeeds. |
| **Event** | *(Layer 2)* A bundle of intents resolved as one unit. Persisted; has a lifetime. |
| **Resolver** | *(Layer 2)* `(ctx, event) -> Outcome`. Decides what actually happened. |
| **First-order producer** | *(Game)* A Char. Persists independently; schedules its own actions. |
| **Second-order producer** | *(Game)* A wolf. Spawned by an environment; exists only inside an event. |
| **Observer** | The player. Reads the log; cannot act. |

---

## 17. Working notes

How the owner works. Recorded because it materially affects how to be
useful here.

- **Expects to be argued with, and argues back.** Several of the better
  decisions came out of push-back — the per-tick-rolling reversal (§5.4)
  is the clearest example, where the owner's objection overturned a
  recommendation this document had previously made. Do not simply defer;
  do not steamroll either.
- **Wants mechanisms explained, not just recommended.** "I didn't
  understand" is a normal and useful response here, and the right answer
  to it is a concrete worked example — a benchmark table, a two-fight
  RNG trace, a `pickle` failure demonstrated rather than described — not
  a restatement. Abstract arguments have repeatedly failed where a
  ten-line runnable example landed immediately.
- **Enforces scope.** Game-domain modelling (item identity, enchantments)
  was explicitly cut off. Respect it.
- **Strong on Python and Postgres; new to authoring libraries.** The
  library-shaped concerns in §12.3 are the genuinely unfamiliar part.
  The Python and SQL are not.
- **Runs the commands themselves.** For `uv init`, `uv add` and similar,
  say what to run and why; do not run it for them. File edits are fine to
  do directly.
- **Building this for enjoyment.** Optimise for the design being
  interesting and the code being pleasant to write, not for shipping
  speed.

---

## 18. Status summary

### Decided

**Simulation model**

- Continuous simulation; tick-based; ~1s ticks; duration configurable.
- Per-character isolation is the property to optimise for, not throughput.
- Two-tier entity state (slow `entity` / fast `entity_state`).

**Core shape**

- foliot is defined by its five guarantees (§2), not by its object graph.
- Exactly one active simulation runner advances a world. Many API processes
  and standby/failover runners are compatible, but simultaneous tick runners
  are outside the architecture; order-independence protects replay and
  iteration stability, not parallel execution (§2, §8.3).
- The core/game line, with the "does the engine read it?" test (§3).
  `target` is game payload, not a core field.
- Two layers; layer 1 must be usable without layer 2 (§10.1).
- Absolute-deadline clock (§4.1); pluggable drivers (§4.2).

**Queue**

- `due_tick` is the primitive for scheduled work; recurring work carries
  no deadline and runs every tick (§5.1, §5.7).
- Deadlines, not countdowns; `expires_at` in payload (§5.2).
- A suspended action records `suspended_at`; resume computes
  `paused_for = resumed_at - suspended_at` and shifts deadlines by that
  amount (§5.3).
- Per-tick rolling is the default; sampled waits are available but not
  the pattern (§5.4).
- Removal, not tombstoning; two states only, and cancellation must reach
  whoever already holds the action (§5.5).
- Scheduling is strictly into the future; same-tick is rejected (§5.6).
- Two shapes: **scheduled** (`due_tick`, sleeps) and **recurring** (runs
  every tick, no `due_tick`). Measured: 0 queue writes/tick vs ~1.7bn row
  versions/day (§5.7).
- Suspension shifts every deadline by the pause length; `suspended_at`,
  not `remaining`, and `on_resume(paused_for)` lets the game shift its own
  (§5.3, §6.4).
- Handlers tell a collecting `TickContext` and return `None`; the engine
  drains after the whole loop, and discards the context of any handler
  that raised. Every such failure is reported through operational logging at
  `ERROR` with its traceback and action/tick identity; it is never silently
  swallowed or written into the deterministic story journal (§7.4).
- `seq` is a stable identity from the store, never positional — it seeds
  the RNG and orders the journal (§9.4b).
- `Store` reads and opens; `Txn` writes and must batch; the clock advances
  implicitly on commit (§8.5).
- `Store` is only the narrow persistence view needed by the engine, not a full
  application repository. Domain-specific setup and external-input operations
  belong to each consumer's concrete adapter and never become foliot methods
  merely because the motivating game needs them (§3, §8.5).
- `Effect.apply(txn.world)` — game-defined and opaque to the engine; the
  world is reached through the tick transaction (§10.4).

**Suspension**

- One `suspendable` bit: effects tick through suspension, activities
  stop; the interrupting event's id is the waking handle (§6.2).
- One suspendable action per Char at a time (§6.2).
- `ActionState` is a discriminated union — active or suspended — not an
  enum. Suspended state records `suspended_at`, `suspended_by`, and the
  scheduled deadline when one exists (§6.4).
- `ActionBinding` is a separate discriminated union: `Unbound` before first
  admission, then `Bound(seq, state)`. It prevents half-bound objects without
  adding a third `ActionState` (§6.4).
- `BaseAction` is mandatory for every game action entering the queue. It owns
  binding and both transitions so games never reimplement them (§7.2).
- First successful `txn.schedule()` assigns `seq` once. Rescheduling,
  suspension, and resumption preserve it on the same game object, including
  all subclass-defined payload fields (§6.4, §9.4b).
- There is no queue-entry wrapper and no `Action(BaseAction)` conversion that
  could discard the subclass's fields (§6.4).
- Action granularity: the suspendable action is the narrative beat (§6.3).

**Actions and persistence**

- An action is an object with `process()` and must inherit the library's
  `BaseAction`; the M1 structural `Action` Protocol is superseded (§7.1,
  §7.2).
- Serialisation is the store's problem; `encode`/`decode` supplied by the
  consumer only when durability is wanted (§7.3).
- The tick is the transaction; the library owns *when*, the game owns
  *how* and *where* (§8).
- Idempotency question closed by tick-atomicity, with the
  effects-outside-the-transaction caveat (§8.4).

**Randomness**

- Per-entity, counter-based streams seeded on
  `(world_seed, entity_id, tick, seq)`; the one shared world's 128-bit
  `world_seed` is generated once from secure OS randomness and persisted
  permanently (§9.1).
- Foliot provides the explicit `new_world_seed()` safe default, while the game
  owns when to create the world and how to persist its seed. Manually supplied
  unsigned 128-bit integers, including `0` and `1`, remain valid; foliot
  validates range and type but never judges entropy (§9.1).
- `Rng` exposes only `random()` and `below(n)`. Probability formulas and the
  comparison for whether an event happens remain game logic; there is no
  `chance()` convenience method (§9.1).
- `below(n)` accepts `1 <= n <= 2**64`, rejects `bool`, and uses rejection
  sampling rather than biased modulo reduction. Even `below(1)` consumes a
  draw (§9.1).
- Stable hashing, never `hash()` on strings (§9.4).
- Deterministic ids for ephemeral entities (§9.5).

**Layer 2**

- Rendezvous resolved: environments open events; `Event` is persisted
  (§10.2).
- No reaction pass needed (§10.3).
- Effects as objects; `log` as a first-class `Outcome` field (§10.4).

**Project**

- Library named `foliot`; `uv` for everything; Python 3.12+ floor with a
  3.14 dev interpreter (§12.4); `basedpyright` strict on `src/` and `tests/`.

### Open, needs deciding

1. **Catch-up policy** (§4.3) — pin world time to wall time vs. allow
   lag. Not blocking until `RealtimeDriver` (M6).
2. **Downtime handling** (§4.4) — fast-forward, compress, or shift the
   world clock.
3. **Log/journal table design** (§15) — the largest table, and it *is*
   the product. Wants deliberate design.
4. **Flask vs. FastAPI** — deferrable indefinitely; irrelevant to the
   library.

### Immediate next work

M0 through M5 are **done**. M2 provides mandatory `BaseAction`,
`Unbound | Bound`, `Active | Suspended`, stable binding/rescheduling, and the
pause shift with `on_resume(paused_for)` (§6.4). M3 provides explicit secure
world-seed creation, stable per-action counter streams, unbiased bounded
draws, and the first test tree. M4 supplies the single-runner `MemoryStore`,
which keeps game objects directly, implements scheduled/recurring queue
semantics, and stages all foliot-owned changes until clean commit.

The M1 and state-shape questions are settled: handlers use
`process(ctx) -> None`; the context collects and the engine drains after the
loop; `ActionState` is a discriminated state object rather than an enum;
`due_tick` belongs to the
action's state; `due_tick=None` means recurring; there are exactly two state
classes, active and suspended; new actions carry `Unbound()` until a successful
`txn.schedule()` replaces it with `Bound(seq, Active(due_tick))`; and the
convention documents exist.

The M2 admission boundary is also settled: `BaseAction` is mandatory and
always carries `Unbound | Bound`. The store replaces `Unbound()` with
`Bound(seq, Active(due_tick))` on first admission and never changes that
action's `seq` afterwards. No wrapper or second action class is involved. M2
exposes `bind(seq, state)` for first admission or hydration and
`reschedule(due_tick)` for later scheduling of the same active object.

The M3 public surface is settled: `new_world_seed()` produces the secure
128-bit default, `counter_rng(world_seed, entity_id, tick, seq)` binds a
stream, and `Rng` exposes only `random()` and `below(n)`. Manual seeds remain
valid, `below(n)` is limited to `1 <= n <= 2**64`, and golden vectors make the
algorithm itself a replay compatibility promise.

The M4 storage division is settled too: foliot owns the `Store` / `Txn`
protocol chassis and its dependency-free `MemoryStore`; each game owns any
Postgres, MariaDB, MySQL, JSON, or other durable adapter. `MemoryStore` supports
one active simulation runner, returns due snapshots in stable `seq` order, and
rolls back its own state on exceptional exit. It exposes the supplied mutable
world directly and does not pretend it can roll back arbitrary game mutations.
M5 added its optional ordered `initial_actions` constructor input so an
in-memory simulation can begin with admitted actions without a fake setup tick;
real application initialization remains outside the narrow `Store` protocol.

M5 also supplies `Simulation` and the inclusive `ManualDriver`. A tick first
collects every due action's context, isolates and visibly reports handler or
context failures, rejects duplicate scheduling by object identity, and only
then applies valid contexts in permanent `seq` order inside the store's one
transaction. Scheduled occurrences are consumed unless they reschedule;
recurring actions remain until `finish()`. An effect failure escapes and aborts
the tick. Ruff, basedpyright strict over `src/` and `tests/`, and all 64 tests
pass, including reversed due-order, retry-without-duplication, and inclusive
tick-100 boundary tests.

Next is M6, `RealtimeDriver`, but its catch-up policy remains deliberately open
and must be discussed before implementation (§4.3).

---

## 19. Superseded: what changed from v1, and why

Recorded so the old reasoning is available without being authoritative.
Losing the reasoning is the only real cost of overwriting a document.

| v1 said | v2 says | Why |
|---|---|---|
| **Sampled/geometric waits** replace per-tick rolls; one queue entry instead of thousands. | **Per-tick rolling is the default.** Sampled waits available, not the pattern. | Sampling is valid only while `p` is constant. `p` depends on Char *and* environment stats, which change often; resampling churn eats the savings. Per-tick is also far easier to reason about. The *ability* to schedule far ahead is kept — sleep-until-dawn needs it. |
| **`Action` is data only**, behaviour looked up by `kind` in a registry, so it can be a Postgres row. | **`Action` is an object with `process()`.** | The library is a set of interfaces; an object is the natural shape. The serialisation concern is real but belongs to the *store*, via a consumer-supplied `encode`/`decode` pair, and does not exist at all for the in-memory store. |
| **`target` should be a first-class core field** (argued mid-session), driving event-key derivation and cascade cancels. | **`target` is game payload.** | The engine never reads it. Environments open events explicitly, so no key derivation is needed. Fails the "does the engine read it?" test. |
| **Rendezvous (§8.1) is unresolved and blocks the registry.** | **Resolved: Option B.** Environments open events and enrol participants; `Event` is persisted. | The game's environment-as-event-spawner design answers it directly. Option A (symmetric derivation) remains available for incidental interactions. |
| **Per-entity RNG recommended but unconfirmed**; owner preferred a global RNG. | **Decided: per-entity, counter-based.** | The Ivan/Petra example (§9.2): a global stream couples unrelated fights through a hidden cursor, breaking per-character isolation, crash recovery and any reordering. Cost objection answered by measurement (§9.3) — 1.6% of a core. |
| **`world_seed` comes from the clock at world creation.** | **Generate one 128-bit seed from secure OS randomness and persist it for the lifetime of the one shared world.** | A clock value is guessable from the approximate creation time. That contradicts the intended unpredictability of future outcomes, especially in a permanent shared world. Deterministic replay needs a stable stored seed, not a predictable one. |
| **The in-memory tick transaction is a no-op.** | **Stage foliot-owned queue changes, bindings, logs, and the clock; publish them only on clean exit.** | Immediate writes survive a later exception while the clock stays put, so retrying the same tick can duplicate work. A memory store cannot generically roll back arbitrary mutations inside game-owned `W`; that separate boundary remains explicit rather than being hidden by the queue fix. |
| **Foliot will ship a Postgres adapter as an extra at M9.** | **Foliot ships only `Store` / `Txn` protocols and the built-in `MemoryStore`; each game owns its durable adapter.** | Action payloads, schemas, transactions, codecs, and database libraries are game-specific. The same protocol supports Postgres, MariaDB, MySQL, a JSON file, or something else without pulling any of them into the core. `MemoryStore` is the dependency-free reference and testing bonus, not the production recommendation. |
| `rng = Random(hash((world_seed, entity_id, tick, seq)))` | Stable hash / integer mixing; **never `hash()` on a string.** | `hash()` is salted per process (`PYTHONHASHSEED`), so v1's snippet silently breaks replay across a restart. |
| **Idempotency strategy is open**: idempotent handlers vs. transactional application. | **Closed.** The tick is the transaction. | With tick-atomicity there is never a half-applied tick, so idempotency is not required — except for effects that reach outside the transaction, which their authors must handle. |
| **Action granularity is open**: is one action one narrative beat? | **Intermediate decision:** the *activity* is the narrative beat and its actions are machinery; later superseded by the no-groups decision below. | This answer produced the proposed `group_id`. §10.3 later established that a walk is one queued action, removing the machinery and the bundle together; the current answer is §6.3: the suspendable action itself is the narrative beat. |
| A **reaction pass** may be needed so targeted entities can act. | **Not needed.** | The environment rolls inside the walking Char's own decide, and schedules the resulting event for the next tick. The pipeline stays single-pass. |
| Intents/events/resolvers are **the framework layer** (core, central). | **Layer 2 — a separate, optional module.** | Layer 1 must be complete and useful alone, or foliot is a framework with a mandatory opinion rather than a library. Layer 2 still earns inclusion because it adds simultaneity. |
| `docs/reference/protocols-draft.py` is the reference shape. | **Superseded, then deleted at M1.** | `Action` changed shape entirely (§7.1), `target` is out, and the two-layer split changed what belongs where. Keeping it cost a lint exclusion and left a wrong shape in the tree for someone to copy. Its two surviving ideas — solo-intent keys and `source_action_id` — are now in §10.5, and `git show 1d22a00:docs/reference/protocols-draft.py` has the rest. Deleting a file is not losing it; failing to extract its reasoning first would have been. |
| **`mypy --strict`** is the type checker (§12.2). | **`basedpyright` in strict mode.** | Two behaviours mypy does not offer, both demonstrated during M0. `enableTypeIgnoreComments = false` makes `# type: ignore` stop silencing anything, so a suppression cannot be smuggled in. And `reportMatchNotExhaustive` names the unhandled member when a `match` misses a case — which is what turns adding an `ActionState` member into a compile error at every call site instead of a silent fallthrough. Corollary rule: a `case _:` branch, *even one that raises*, disables that check completely — verified, zero errors reported — so catch-alls are banned. |
| **`requires-python = ">=3.11"`** — widest reach; PEP 695 buys foliot little because its protocols are not generic. | **`>=3.12`.** | Reversed after reading a mature in-house Python codebase whose style the owner wants to match. PEP 695 (`type Tick = int`, `class Store[A](Protocol):`) and `@typing.override` are the visible part of that style, and `override` cannot be had on 3.11 without `typing_extensions` — a dependency, which is forbidden. Cost: Debian 12's system Python (3.11) is excluded; Ubuntu 24.04 LTS (3.12) is not. The floor is enforced mechanically rather than by care — ruff infers its target from `requires-python`, so 3.13+ syntax fails to lint on a 3.14 dev machine (verified with PEP 696 defaults). |
| **Tombstoning, not removal** — mark cancelled, skip on pop, keep the audit trail. | **Removal.** A done or cancelled action is deleted; only `active` and `suspended` exist. | The audit argument does not survive §15's conclusion that history belongs in the journal and the queue stays small and hot. Under the later single-runner decision, a committed deletion need only exclude the action from subsequent `due(...)` snapshots; foliot does not promise to invalidate arbitrary Python references retained by user code. |
| **Design now for multiple simultaneous simulation workers** with `SKIP LOCKED`, stale-copy cancellation, and sharding. | **Exactly one active simulation runner per world.** | A tick has one global decision/apply barrier and commits atomically. Splitting it across runners requires distributed coordination and failure semantics the ZPG does not need. Many API workers and a standby runner are still fine; only one process advances ticks at a time. |
| **`ActionState` is four statuses** — active / suspended / done / cancelled — with `remaining` alongside. | **Intermediate decision:** two states as a discriminated union, initially `Active(due_tick)` \| `Suspended(remaining, suspended_by)`; the suspended fields were later superseded by the `suspended_at` row below. | Done and cancelled are not states an action is *in*; they are it ceasing to exist. `status` is a `Literal`, so the Python value *is* the `status` column value. |
| **`remaining` rides along while active too** — cheap, and likely to find a use. | **Intermediate decision:** keep it only while suspended; later superseded by `suspended_at`. | Three numbers — `current`, `due`, `remaining` — any two give the third. Keeping the active copy would create a second, silently divergent record of a derived value. The later recurring-action decision then made `remaining` meaningless for some suspended actions, leading to the shift rule below. |
| **Opaque `group_id`** minted when an activity starts; suspension operates on the bundle. | **One `suspendable` bit per action**, plus `suspended_by` on the suspended state. | §6.1's motivation was that a walk is several pending actions needing one handle. §10.3 had already dissolved that — the environment's roll happens inside the walking Char's own `process()`, so a walk is *one* action and there is nothing left to bundle. Accepts the constraint that a Char has one suspendable action at a time. Waking is pushed by the event that did the suspending, never polled: ordering the queue by status to let a suspended action notice its own release would make processing order significant, which is guarantee 2 gone. |
| *(new — not a reversal)* Scheduling had no stated bound. | **Same-tick scheduling is rejected**; earliest legal target is `now + 1` (§5.6). | The engine reads a tick's due list once. An action added to the current tick lands in a bucket already read, so whether anything else observes it depends on who ran first — order-dependence through a different door than §9.2. Costs nothing: an effect belongs to the interval it covers, so damage at tick 6 *is* the hunger from 5 to 6. |
| **Handlers "return or emit"** scheduling requests (§7.4), while §5.2 called `sim.schedule(...)`, §8.6 called `sim.store.schedule(...)`, and `sim` was never defined anywhere. | **Intermediate decision:** `process(ctx) -> Outcome \| None`; later superseded by the collecting-context row below. | The "or" was the real defect — the doc never chose, and three examples drifted apart. Applying only after the loop makes order-independence structural. The later collecting-context spelling keeps that property while matching how the owner wants actions to read. |
| The `Store` protocol had three read-ish methods and **no way to write anything**, while §8.6 called `store.schedule(...)`. | **`Store` reads and opens; `Txn` writes.** No `advance_to` — the clock moves implicitly on a clean commit. | Writing is legal only inside a tick, so the only way to get a writer is to be inside one; the rule becomes unsayable rather than remembered. Making the clock bump part of the commit means no store *can* separate the work from the clock, which is what §8.3 asserts. `Txn` must also batch: measured, 15x between set-based flushing and one statement per call. |
| **`due_tick` is the sole queue primitive** (§5.1); per-tick behaviour is just `due_tick = now + 1`. | **Two shapes: scheduled *or* recurring.** A recurring action carries no `due_tick`. | For a per-tick action `due_tick` is always `now + 1` — a constant. Rewriting the row every tick spends a delete and an insert to store nothing, and §5.4 made per-tick rolling the default, so this is the common case, not an edge one. Measured on Postgres 16 at 10,000 active Chars: 158.5 ms/tick and 170,000 dead tuples per 20 ticks, versus 43.1 ms and **zero**. §5.1's argument survives intact — it was always about *idle* actions, and it never covered active ones. |
| **`Suspended` carries `remaining`**; resume is `due = now + remaining`. | **`Suspended` carries `suspended_at`**; resume shifts every deadline by `now - suspended_at`, and `on_resume(paused_for)` hands the same number to the game. | `remaining` fixes the core's deadline but yields no pause length, and a walk's `arrives_at` is game payload the engine must not touch — so under `remaining` a suspended Char arrives as though the fight never happened. A recurring action has no `due_tick`, so `remaining` never meant anything for it either. The owner proposed storing `suspended_on`; the formula offered (`arrives_at = suspended_on + remaining`) drops the pause, but the instinct behind it was right and produced the shift rule. |
| Handlers **return an `Outcome`**, which the engine applies after the loop. | Handlers **tell a collecting `TickContext`** — `ctx.emit`, `ctx.schedule`, `ctx.finish` — and return `None`. | Equivalent in every guarantee: the context collects rather than writes, the engine still drains only after every action has been asked, and a raising handler still changes nothing because its context is discarded whole. Chosen for the spelling: it reads the way the owner thinks about the game — an action does its own thing and queues its own successor — without buying back the ordering problem that direct mutation would. `Outcome` stays layer 2's vocabulary, where a resolver really does return one. Cost: testing a handler needs a ten-line recording fake instead of asserting on a return value. |
| *(new — found while writing the first worked example)* `seq` was unspecified; the obvious implementation is the position in the due list. | **`seq` is a stable identity assigned by the store**, never positional. | It seeds the RNG. Positionally, shuffling a tick swaps the draws of two actions owned by the same entity, breaking replay *and* order-independence together, silently. It also fixes journal order: process in any order, tell the story in `seq` order. |
| **`Action` is a structural Protocol; `BaseAction` is optional.** A new object lacks `seq` and state until the store admits it. | **Every queued game action must inherit `BaseAction`, which always carries `Unbound | Bound(seq, state)`.** The store changes the binding on the same subclass object and assigns `seq` only once. | The structural contract could describe an admitted action or a new action, but not both without missing attributes or making the game implement engine bookkeeping. A wrapper kept both phases complete but broke the natural `ctx.schedule(self, ...)` path: `self` would be the inner game object and would lose the wrapper's stable `seq`. The binding union makes both lifecycle phases complete, preserves every game field and method, and keeps active/suspended as the only two queue states. |
| A single `protocols.py` holds `Action`, `Store`, `Driver`, `Rng` (§12.1). | **One module per concept**, protocol beside where its implementation will live. | In a library whose product is its protocols, one file separates each contract from the reasoning that produced it. `rng.py` opening with the Ivan/Petra coupling is worth more than a tidy list of signatures. |
