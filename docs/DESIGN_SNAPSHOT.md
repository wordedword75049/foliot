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
| 5 | The queue: deadlines, remaining, granularity |
| 6 | Suspension and activity groups |
| 7 | Actions: objects, protocols, `BaseAction` |
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
what permits out-of-order processing, retries, and sharding. Optimise for
isolation and determinism, not raw throughput.

### 1.6 Stack — **DECIDED**

Python, PostgreSQL, and Flask-or-FastAPI (undecided and irrelevant to the
library). The core library has **zero dependencies**. Anything
Postgres-shaped lives in an extras group or a separate distribution.

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
   actions in any order, on any number of workers, and the world lands in
   the same state.
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
| `ActionState`: active / suspended / done / cancelled, carrying `remaining` | encounter rolls, and what `p` depends on |
| Opaque `group_id` for suspending a *set* of actions | what an "activity" means; which suspend which |
| `Action` protocol + `BaseAction` bookkeeping | HP, mana, `entity_state`, damage |
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
sim.run(RealtimeDriver(tick_seconds=1.0))      # production
sim.run(ManualDriver(until_tick=10_000_000))   # tests, fast-forward
```

Same code path, different sense of time. This single split is what lets
ten million ticks run in a unit test. Hardcoding `time.sleep` into the
loop makes the library untestable, and that is felt immediately.

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

## 5. The queue: deadlines, remaining, granularity

### 5.1 `due_tick` is the queue primitive — **DECIDED**

The queue is `due_tick -> [actions]` — a timing wheel — not a single
next-tick list. Any action may schedule into any future tick.

The owner initially proposed that an action carry `ticks_remaining` and
re-enqueue itself each tick at `remaining - 1`. That is a *scheduling
policy*, not a storage format, and the two were separated:

- **`due_tick` is the primitive.** A per-tick action is simply one whose
  handler always reschedules at `now + 1`.
- **`ticks_remaining` rides along as a field** (§5.3), because it is
  genuinely useful — especially for suspension.

**Why this asymmetry matters.** With `due_tick` as the primitive, per-tick
behaviour costs nothing (`due_tick = now + 1`). With `ticks_remaining` as
the *only* mechanism, the engine cannot express "wake me in eight hours"
at all. A Char sleeping until dawn is 28,800 ticks: under decrement-and-
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
        super().__init__(entity_id)
        self.interval = interval
        self.expires_at = expires_at

    def process(self, sim, tick):
        sim.emit(Damage(self.entity_id, amount=3))
        if tick + self.interval < self.expires_at:
            sim.schedule(self, due_tick=tick + self.interval)
        # else: poison wears off, simply by not rescheduling
```

Miss a tick with a countdown and it is silently wrong forever. Miss a
tick with a deadline and nothing happens, because the deadline was never
being maintained in the first place.

### 5.3 `remaining` as a field on `ActionState` — **DECIDED (owner's call)**

`remaining` lives on the action's state object and is meaningful in both
the active and suspended states:

- **Active** — it ticks down alongside the deadline. Currently derived
  and not load-bearing; kept because it is cheap and likely to find a use.
- **Suspended** — it is the *only* record of how much is left, since the
  deadline was dropped when the action left the queue.

Resume is then `due_tick = current_tick + remaining`. No conversion, no
need to consult when the action began.

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

### 5.5 Tombstoning, not removal — **DECIDED**

Invalidated actions are marked cancelled and skipped on pop, rather than
removed. Simpler than removing from a heap, and it preserves an audit
trail of what *would* have happened.

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

---

## 6. Suspension and activity groups — **DECIDED**

### 6.1 The problem

"The walk stands suspended until the battle is over" sounds like one
action, but it is not. Walking through a forest has several pending
actions — the arrival, and whatever produces encounters. Suspending the
walk means suspending all of them. Meanwhile poison should keep ticking
during the fight, and so should hunger.

So `suspend(action_id)` is too narrow and `suspend_all_for(entity)` is
too broad.

### 6.2 The rule

- **Effects** — poison, hunger, cooldowns — belong to no group and keep
  firing through any suspension.
- **Activities** — walking, crafting, travelling — are bundles of actions
  sharing an opaque **`group_id`**, minted when the activity starts.
  Suspension operates on the bundle.

The engine never interprets `group_id`. It is a string it can group by,
nothing more. *Which* activities suspend under *which* circumstances is
game policy and lives entirely in game code.

### 6.3 Consequence for action granularity

v1 listed "is one action one narrative beat?" as an open question. It is
effectively answered by the above: **an activity is the narrative beat;
the actions inside it are machinery.** The observer reads "Ivan wanders
through the pines" — one activity. The engine sees an arrival action and
an encounter-roll action — two actions, one `group_id`.

### 6.4 `BaseAction` carries the bookkeeping

Every action that inherits `BaseAction` gets suspend/resume for free. The
game developer never writes it, and therefore cannot get `remaining`
wrong on suspend — which is exactly the class of silent bug this design
exists to prevent.

```python
class BaseAction:
    def __init__(self, entity_id, remaining=None, group_id=None):
        self.entity_id = entity_id
        self.remaining = remaining
        self.group_id = group_id
        self.state = ActionState.ACTIVE

    def suspend(self, tick):
        self.remaining = self.due_tick - tick
        self.state = ActionState.SUSPENDED

    def resume(self, tick):
        self.due_tick = tick + self.remaining
        self.state = ActionState.ACTIVE
```

---

## 7. Actions: objects, protocols, `BaseAction`

### 7.1 An action is an object with `process()` — **DECIDED (reverses v1)**

v1 specified `Action` as **data only**, with behaviour looked up by
`kind` in a registry, on the grounds that this is what lets an action be
a Postgres row.

**Reversed.** The library is a set of interfaces; the natural shape is an
object the consumer implements. The serialisation concern is real but it
is the **store's** problem, not the action's (§7.3).

### 7.2 Protocol for the contract, base class for the bookkeeping — **DECIDED**

Two mechanisms, used for different jobs:

```python
class Action(Protocol):        # the contract the engine requires
    def process(self, sim, tick) -> None: ...

class BaseAction:              # optional convenience; satisfies the contract
    """Handles remaining / state / group_id so you don't have to."""
```

For most of foliot — `Store`, `Driver`, `Rng` — a **Protocol** is right:
pure contracts with no state of their own, so structural typing keeps the
game from importing the library merely to satisfy a type, while
`mypy --strict` still catches a missing or wrong-signatured method at the
point of use.

`Action` is the exception, because it carries bookkeeping the *engine*
owns: `remaining`, active/suspended state, `group_id`, `due_tick`, `seq`.
A bare Protocol would force every game to reimplement that correctly.

The engine only ever type-checks against the **Protocol**, so it never
depends on anyone inheriting. In practice nearly everyone inherits
`BaseAction`; someone with an unusual model can implement the protocol
directly and foliot still accepts it.

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
| `walk` | `{"destination": "oakvale", "remaining": 340}` | 5340 |

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
- **For durability, the consumer supplies two functions**, and the
  library states the contract without implementing it:

```python
def encode(action) -> tuple[str, dict]: ...   # object -> (kind, payload)
def decode(kind, payload) -> Action: ...      # (kind, payload) -> object
```

The Postgres store asks for that pair; the memory store does not. So
`kind` is part of the *storage* design, appearing only when a durable
store is plugged in — not a tax the core charges everyone.

### 7.4 Handler contract conventions — **DECIDED**

Two conventions carry most of the testability:

- Handlers **return or emit** scheduling requests rather than reaching
  into the queue directly.
- Handlers get randomness from **`ctx.rng` / `sim.rng`**, never from the
  `random` module.

Together these make a handler a plain function callable in a test with a
fake context and asserted on by return value, rather than something
observable only by running the world. If a handler cannot be tested that
way, the contract has been violated somewhere.

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

At 1 tick/second that is 86,400 transactions a day, which is nothing for
Postgres. Under `ManualDriver` with the in-memory store it costs zero,
because the transaction is a no-op.

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
class Store(Protocol):
    def current_tick(self) -> int: ...
    def due(self, tick: int) -> Iterable[Action]: ...
    def tick_transaction(self, tick: int) -> ContextManager[Txn]: ...
```

The engine runs each tick inside `tick_transaction` and routes everything
it changes through the handle it gets back. The engine never calls
"save" — it does its work inside a boundary the store defines. For the
in-memory store that context manager does nothing; for Postgres it is
`BEGIN` / `COMMIT`. That is precisely why the interface asks for a
context manager rather than a `save()` method: `BEGIN`/`COMMIT` is a shape
a dictionary can ignore for free.

Caching is the store's business too. The near-horizon window — pull the
next few minutes into memory rather than querying every tick — is an
optimisation a Postgres store makes internally. The engine just asks
`due(tick)` and does not care whether that hit RAM or disk.

### 8.6 The shape, end to end

A deliberately minimal sketch — no ordering, no RNG, no statuses — showing
only who owns what. This ran; the output is in the comments.

```python
# ---------- what foliot ships ----------
class Simulation:
    def __init__(self, store):
        self.store = store                            # game hands it in here

    def run(self, until):
        tick = self.store.current_tick()
        while tick < until:
            with self.store.tick_transaction(tick):   # library opens boundary
                for action in self.store.due(tick):
                    action.process(self, tick)        # your object, your method
            tick += 1


# ---------- what the game developer writes ----------
class Walk:
    def __init__(self, entity_id, remaining):
        self.entity_id, self.remaining = entity_id, remaining

    def process(self, sim, tick):
        self.remaining -= 1
        if self.remaining > 0:
            sim.store.schedule(self, due_tick=tick + 1)
        else:
            print(f"tick {tick}: {self.entity_id} ARRIVES")


class MyStore:
    def __init__(self):
        self.queue, self.tick = {}, 0
    def current_tick(self):  return self.tick
    def due(self, tick):     return self.queue.pop(tick, [])
    def schedule(self, action, due_tick):
        self.queue.setdefault(due_tick, []).append(action)
    def tick_transaction(self, tick):
        ...  # no-op in memory; BEGIN/COMMIT against Postgres


Simulation(store=MyStore()).run(until=3)
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

`world_seed` is drawn from the clock **once**, when the world is created.
So the world is born unpredictable and is only reproducible in the sense
that, having happened, it can be re-derived.

**Determinism is not predictability.** Because `tick` is part of the
seed, walking the same forest path at tick 500 and at tick 10,000,000
draws from completely unrelated streams. Nothing repeats and nothing is
guessable. This was the original misunderstanding and it is worth
restating whenever it comes up.

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
- **The queue can never be reordered.** No parallel workers, no
  `SKIP LOCKED` with two processes, no out-of-order batches — all of them
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
| **counter-based (splitmix64), no state** | **0.32 µs** | **3.1M** |
| keyed `blake2b(8)` per draw | 0.49 µs | 2.0M |

The naive version is **118× slower** than the global stream, and the
reason is mechanical: `random.Random(seed)` builds a Mersenne Twister —
624 words of internal state initialised from the seed — and then throws
all of it away to take one float. You pay for a 19937-bit generator to
get 53 bits out.

**The fix is to stop constructing a generator and start computing the
answer.** A counter-based PRNG hashes
`(world_seed, entity_id, tick, seq, draw_index)` straight into a number:
a handful of integer multiplies and shifts, no state, no setup. This is
the standard approach (Random123 / Philox in the numerics world) and it
is designed for exactly this situation — enormous numbers of independent,
addressable streams.

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
  `process(sim, tick)`, emitting schedules, cancels and effects. No
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
  apply effects, enqueue schedules, tombstone cancels
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
- **`Effect`** — a game-defined mutation object with `apply(world)`.
  Effects are the only thing permitted to write.
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
guarantee of §1.5, obtained for free — and it is what makes sharding and
out-of-order processing valid rather than merely tempting. The core
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
│   └── reference/
│       └── protocols-draft.py  # v1 reference only; never imported
├── src/
│   └── foliot/
│       ├── __init__.py         # curated public API surface
│       ├── py.typed            # required; uv init --lib creates it
│       ├── protocols.py        # Action, Store, Driver, Rng
│       ├── actions.py          # BaseAction, ActionState
│       ├── rng.py              # counter-based streams
│       ├── engine.py           # Simulation: the tick loop
│       ├── drivers.py          # ManualDriver, RealtimeDriver
│       ├── stores/
│       │   ├── __init__.py     # Store protocol
│       │   └── memory.py       # in-memory reference implementation
│       └── events/             # LAYER 2 — optional, added after layer 1
├── examples/
│   └── tinyworld/              # smallest possible ZPG proving the API
└── tests/
```

- **`src/` layout**, not flat. Prevents accidentally importing from the
  working directory instead of the installed package — a classic source
  of "works on my machine" test results.
- **`stores/postgres.py` deliberately absent.** It belongs in an optional
  extra (`foliot[postgres]`) or a separate distribution, so the base
  install stays dependency-free.
- **`examples/tinyworld` is load-bearing, not decoration.** It is the only
  honest test of whether the public API is pleasant, and it should be
  written early enough to still change the API.

### 12.2 Tooling — **DECIDED**

**`uv` for everything.** No bare `pip`, no hand-rolled venv. `uv sync`,
`uv add`, `uv run`, `uv build`, `uv publish`. `uv.lock` is committed. uv
moves fast — verify syntax against its docs rather than trusting this
paragraph.

Python 3.11+, `pytest`, `ruff`, `mypy --strict` on `src/`. Strict typing
is not optional: the design is Protocol-based, and Protocols without a
type checker are just comments.

**Verified during this session** (uv 0.8.17; current is 0.12.x, and
nothing relevant changed):

```
uv init --lib
uv add --dev pytest ruff mypy
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

- **`requires-python`** is set from whichever Python uv found. On a 3.13
  machine it writes `>=3.13`, which would lock out 3.11 and 3.12 users.
  We want `>=3.11`.
- **`authors`** is guessed from git config.

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
- Dependencies list stays empty. Postgres support goes in an extras group
  or a separate distribution.
- Persistence, clock driving, and randomness are all **injected
  interfaces**. The in-memory implementations that ship exist primarily
  so tests and quickstarts work without a database.

---

## 13. Build order

Sequenced so each milestone is independently testable.

| # | Milestone | Depends on | Notes |
|---|---|---|---|
| M0 | Repo scaffold, `pyproject.toml`, lint/type/test config | — | `uv init --lib`; verify toolchain on the empty package. |
| M1 | `protocols.py` — `Action`, `Store`, `Driver`, `Rng` | §3, §7 | Written fresh. The v1 draft is reference only. |
| M2 | `actions.py` — `BaseAction`, `ActionState`, suspend/resume | M1, §6 | `remaining` bookkeeping lives here so games never write it. |
| M3 | `rng.py` — counter-based streams, stable hashing | §9 | Watch `PYTHONHASHSEED` (§9.4). |
| M4 | `stores/memory.py` — queue semantics | M1, M2 | `due_tick` buckets, tombstoning, `seq` ordering, no-op transaction. |
| M5 | `engine.py` + `ManualDriver` | M1–M4, §8 | The tick loop and the transaction boundary. |
| M6 | `RealtimeDriver` | §4.3 catch-up policy | Manual first — it is what makes M5 testable. |
| M7 | `examples/tinyworld` | M5 | Written while the API can still change. |
| M8 | Layer 2: `events/` — intents, grouping, resolvers | M5, §10 | Optional module. Layer 1 must work without it. |
| M9 | Postgres store as an extra | M4, §4.4, §8 | `encode`/`decode`, `SKIP LOCKED`, `current_tick`. |

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
4. With the Postgres store, killing the process mid-tick and restarting
   loses no pending events and duplicates no applied effects.

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

**3. Suspend/resume fidelity.** Suspend an activity group mid-flight,
advance many ticks, resume, and assert the remaining duration is
preserved exactly — while ungrouped effects (poison, hunger) fired
throughout. This is the test for §6.

**4. Crash recovery.** With the Postgres store, interrupt between "the
handler ran" and "the tick committed," restart, and assert no event is
lost and no effect is applied twice. This validates §8.

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
    due_tick    BIGINT      NOT NULL,
    group_id    TEXT,                   -- activity bundle; §6.2
    status      TEXT        NOT NULL DEFAULT 'active',
    remaining   BIGINT,                 -- meaningful when suspended; §5.3
    seq         INTEGER     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The scheduler's hot path: everything due at or before tick N.
CREATE INDEX scheduled_action_due
    ON scheduled_action (due_tick, seq, id)
    WHERE status = 'active';

-- Suspending or cancelling a whole activity at once (§6.2).
CREATE INDEX scheduled_action_group
    ON scheduled_action (group_id)
    WHERE status = 'active';

-- World clock. Single row. Must survive restart (§4.4).
CREATE TABLE world_state (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    current_tick BIGINT      NOT NULL,
    world_seed   BIGINT      NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Claim pattern for a worker:

```sql
SELECT * FROM scheduled_action
WHERE status = 'active' AND due_tick <= $1
ORDER BY due_tick, seq, id
FOR UPDATE SKIP LOCKED
LIMIT $2;
```

`SKIP LOCKED` is unnecessary for a single worker but costs nothing, and
adopting it now makes a second worker a config change rather than a
redesign.

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
| **Action** | A scheduled unit of work owned by one entity, with a `process()` method. Stored in the queue. |
| **`due_tick`** | The absolute tick at which an action becomes due. The queue's primitive. |
| **`remaining`** | Ticks left in an action. Rides along while active; the sole record while suspended. |
| **`group_id`** | Opaque activity bundle. Suspension operates on it. The engine never interprets it. |
| **Activity** | A bundle of actions sharing a `group_id` — one narrative beat. Game concept. |
| **Effect (game sense)** | Poison, hunger, cooldowns — actions belonging to no group, which tick through suspension. |
| **Effect (layer 2)** | A mutation object with `apply(world)`. The only thing permitted to write. |
| **Driver** | The thing that advances the clock. `RealtimeDriver` sleeps; `ManualDriver` does not. |
| **Store** | The consumer's persistence adapter: `current_tick`, `due`, `tick_transaction`. |
| **Tick transaction** | The atomicity boundary. Everything in tick N lands together or not at all. |
| **Tombstone** | Marking an action cancelled rather than deleting it. |
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
- The core/game line, with the "does the engine read it?" test (§3).
  `target` is game payload, not a core field.
- Two layers; layer 1 must be usable without layer 2 (§10.1).
- Absolute-deadline clock (§4.1); pluggable drivers (§4.2).

**Queue**

- `due_tick` is the primitive; per-tick is `due_tick = now + 1` (§5.1).
- Deadlines, not countdowns; `expires_at` in payload (§5.2).
- `remaining` on `ActionState`, meaningful in both active and suspended
  (§5.3).
- Per-tick rolling is the default; sampled waits are available but not
  the pattern (§5.4).
- Tombstoning, not removal (§5.5).

**Suspension**

- Ungrouped effects tick through suspension; activities suspend as a
  bundle via opaque `group_id` (§6.2).
- `BaseAction` owns suspend/resume so games never write it (§6.4).
- Action granularity: the activity is the narrative beat (§6.3).

**Actions and persistence**

- `Action` is an object with `process()`; Protocol for the contract,
  `BaseAction` for bookkeeping (§7.1, §7.2).
- Serialisation is the store's problem; `encode`/`decode` supplied by the
  consumer only when durability is wanted (§7.3).
- The tick is the transaction; the library owns *when*, the game owns
  *how* and *where* (§8).
- Idempotency question closed by tick-atomicity, with the
  effects-outside-the-transaction caveat (§8.4).

**Randomness**

- Per-entity, counter-based streams seeded on
  `(world_seed, entity_id, tick, seq)`; `world_seed` from the clock at
  world creation (§9.1).
- Stable hashing, never `hash()` on strings (§9.4).
- Deterministic ids for ephemeral entities (§9.5).

**Layer 2**

- Rendezvous resolved: environments open events; `Event` is persisted
  (§10.2).
- No reaction pass needed (§10.3).
- Effects as objects; `log` as a first-class `Outcome` field (§10.4).

**Project**

- Library named `foliot`; `uv` for everything; Python 3.11+;
  `mypy --strict` on `src/`.

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

M0: `uv init --lib` and `uv add --dev pytest ruff mypy`, then metadata
and tool config, then verify the toolchain on the empty package. Then M1.

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
| `rng = Random(hash((world_seed, entity_id, tick, seq)))` | Stable hash / integer mixing; **never `hash()` on a string.** | `hash()` is salted per process (`PYTHONHASHSEED`), so v1's snippet silently breaks replay across a restart. |
| **Idempotency strategy is open**: idempotent handlers vs. transactional application. | **Closed.** The tick is the transaction. | With tick-atomicity there is never a half-applied tick, so idempotency is not required — except for effects that reach outside the transaction, which their authors must handle. |
| **Action granularity is open**: is one action one narrative beat? | **Effectively answered.** The *activity* is the narrative beat; the actions inside it are machinery. | Fell out of needing a bundle handle (`group_id`) for suspension. |
| A **reaction pass** may be needed so targeted entities can act. | **Not needed.** | The environment rolls inside the walking Char's own decide, and schedules the resulting event for the next tick. The pipeline stays single-pass. |
| Intents/events/resolvers are **the framework layer** (core, central). | **Layer 2 — a separate, optional module.** | Layer 1 must be complete and useful alone, or foliot is a framework with a mandatory opinion rather than a library. Layer 2 still earns inclusion because it adds simultaneity. |
| `docs/reference/protocols-draft.py` is the reference shape. | **Superseded.** Vocabulary still useful; signatures are not. | `Action` changed shape entirely (§7.1), `target` is out, and the two-layer split changes what belongs in `protocols.py`. |
