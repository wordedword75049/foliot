"""Addressable randomness (§9).

A global stream would couple unrelated entities through a hidden cursor: with
one shared generator, whether Ivan's blow lands depends on whether Petra's
fight -- a thousand miles away -- was processed first (§9.2). That coupling
appears in no table, survives no restart, and forbids reordering the queue.
"""

from typing import Protocol

__all__ = ["Rng"]


class Rng(Protocol):
    """A stream already bound to `(world_seed, entity_id, tick, seq)`.

    Handlers never seed anything and never import `random`. Each draw advances
    only a local counter, so every value stays addressable: "Ivan's third roll
    in tick 5000" can be recomputed a year later without replaying anything.

    Implementations must hash stably -- never `hash()` on a `str`, which
    `PYTHONHASHSEED` salts per process and which therefore breaks replay across
    a restart in a way that looks like a rare, mysterious bug (§9.4).
    """

    def random(self) -> float:
        """Uniform in [0.0, 1.0)."""
        ...

    def below(self, n: int, /) -> int:
        """Uniform integer in [0, n)."""
        ...
