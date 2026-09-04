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
addressable randomness. It has never heard of a target, a location, an
environment, an activity, or combat.

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
characters, no HP, no items, no combat rules, no inventory, no targets.
If a design question can only be answered by reference to a specific
game, it is out of scope here.

**The membership test for anything in `src/foliot/`:** *does the engine
read it?* If not, it is game payload. This is how `target` was ruled out
even though every game action has one (§3).

**The membership test for a feature:** *does it add a guarantee the
consumer cannot easily provide themselves?* If it is merely vocabulary
or convenience, it belongs in the game.

The game is recorded in §11 as the source of requirements. It is not a
dependency and not a concern.

## Two layers

- **Layer 1** — clock, queue, drivers, RNG, store, actions. Must be
  complete and useful **on its own**.
- **Layer 2** — intents, event grouping, resolvers. A separate importable
  module, built after layer 1 is real.

If layer 1 cannot be used without layer 2, we have built a framework with
a mandatory opinion. Do not let layer 2 concepts leak downward.

## The v1 draft is gone

`docs/reference/protocols-draft.py` was deleted at M1. Its shape was
wrong in every load-bearing way — data-only actions with a `kind`
registry, `target` as a core field, `Any` where the world type belongs —
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
