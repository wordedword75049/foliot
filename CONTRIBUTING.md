# Contributing to foliot

Thank you for helping improve foliot. The project favors small, explicit
changes that preserve deterministic behavior and keep game concepts outside
the engine.

## Setup

Install [uv](https://docs.astral.sh/uv/), then run:

```console
uv sync
```

The project supports Python 3.12 and newer. Runtime dependencies must remain
empty unless a new dependency has been discussed and justified first.

## Required checks

```console
uv run ruff format --check src tests examples
uv run ruff check src tests examples
uv run basedpyright
uv run pytest
```

Use `uv run ruff format` to apply formatting.

## Design rules

- Every queued game action inherits `BaseAction`.
- External ports such as stores, effects, contexts, RNGs, and drivers use
  structural `Protocol` typing.
- Keep public contexts narrow. Pass `ctx.rng` to a helper instead of passing
  the whole context.
- Game nouns do not belong in foliot. The engine has no built-in HP, character,
  target, location, or combat model.
- Use PEP 695 generic syntax; the supported floor is Python 3.12.
- Do not use `Any` in public signatures.
- Use exhaustive `match` statements for closed state unions; do not add a
  catch-all branch that disables type-checker exhaustiveness.
- Use `logging.getLogger(__name__)`; library code must never call
  `logging.basicConfig()` or print.
- Do not use Python's `random` module outside `rng.py`, `hash()` for persistent
  string identity, or wall-clock time outside a driver.
- Keep the root and `foliot.events` export lists curated. A public import path
  is a compatibility promise.

## Tests

Tests use handwritten fakes rather than `unittest.mock`, `pytest-mock`,
monkeypatching production objects, or time-freezing libraries.

Important changes should prove the architectural property they affect:

- replay produces the same output from the same seed;
- reversing due-action order does not change results;
- suspension shifts deadlines by the exact pause duration;
- exceptional transaction exit publishes no foliot-owned state;
- an incomplete Event round retains exactly one copy of every current child;
- Layer 1 behaves identically when the optional Event layer is unused.

Test game actions directly with a tiny recording context whenever possible.
Use a full `Simulation` only when testing engine coordination.

## Documentation

Public docstrings explain behavior, parameters, return values, and meaningful
exceptions. Examples should show normal usage. Internal comments should explain
why a non-obvious mechanism exists rather than narrating the next line of code.

Update the relevant guide whenever a public contract changes.

Maintainers should follow [RELEASING.md](RELEASING.md) for versioning, artifact
checks, and trusted publication.
