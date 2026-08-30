# foliot

A deterministic tick-driven simulation core: durable event queue,
two-phase resolution, sampled scheduling. Domain-agnostic.

## Read this first

**`docs/DESIGN_SNAPSHOT.md` is the design.** Read it before proposing or
writing anything. Start with §18 (status summary), then §7 (the
framework layer), then §11 (build order).

It marks every item as **decided**, **recommended but unconfirmed**, or
**open**. Honour those labels. A recommendation in that document is an
argument I have not yet accepted, not a decision I have made.

## Scope

**This repo is the library only.** It is consumed by a separate
zero-player game project, but it must not know that game exists — no
characters, no HP, no items, no combat rules, no inventory. If a design
question can only be answered by reference to a specific game, it is out
of scope here.

The game is the motivating use case and a source of requirements. It is
not a dependency and not a concern.

## `docs/reference/protocols-draft.py` is reference, not code

It is a sketch of one possible shape, deliberately hyphenated so it
cannot be imported. **Do not copy it into `src/`.** I have revisions to
it that I will describe at the start of the session. What is worth
taking from it is the vocabulary and the reasoning in the docstrings,
not the signatures.

## Open questions — ask, do not decide

Four things are genuinely undecided and I want to decide them in
conversation, not find them settled in a commit:

1. **Rendezvous mechanism** (§8.1) — how intents from two participants
   find each other. Blocks the registry and determines whether `Event`
   is persisted.
2. **Per-entity vs global RNG** (§5.2) — I initially wanted a global
   RNG; the doc argues for per-entity streams. Unresolved.
3. **Catch-up policy** (§3.3) and **downtime handling** (§3.4).
4. **Action granularity** — whether one action is one narrative beat.

If you hit one of these, stop and raise it.

## Tooling

**`uv` for everything.** No bare `pip`, no hand-rolled venv.
`uv sync`, `uv add`, `uv run`, `uv build`, `uv publish`. `uv.lock` is
committed. `uv` changes fast — check current syntax against its docs
rather than assuming.

Python 3.11+, `src/` layout, `pytest`, `ruff`, `mypy --strict` on
`src/`. Strict typing is not optional: the design is Protocol-based, and
Protocols without a type checker are just comments.

Ship `src/foliot/py.typed` or consumers get no type information at all.

## How I like to work

- **Argue with me.** Several of the better decisions in the design came
  out of push-back. If something I say is wrong, say so and say why.
- **Explain mechanisms, not just conclusions.** If a recommendation
  rests on a fact — drift accumulating at 2ms/tick, geometric sampling
  being exact rather than approximate — show the reasoning. I would
  rather understand it than accept it.
- **Small, reviewable steps.** I am learning library authoring; a large
  correct diff teaches me less than a small one I can follow.
- **Ask before scope grows.** New files, new dependencies, new
  abstractions: check first. Dependencies especially — the core should
  install with none.

## Keeping the design doc alive

`docs/DESIGN_SNAPSHOT.md` is version-controlled memory, not an
archive. When an open question gets resolved, update it in the same
commit as the code, and move the item from **open** to **decided** in
§18 with a one-line note on why. If we contradict the document, the
document is what gets fixed.
