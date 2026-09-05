"""Structural contract for game-defined world mutations."""

from typing import Protocol

__all__ = ["Effect"]


class Effect[W](Protocol):
    """Something that changes the world, described now and applied later.

    An effect is *staged* during a handler and applied only after every due
    action has been asked (see `TickContext`). Anything that writes to the world
    during the loop is visible to whatever runs next, and processing order
    starts deciding outcomes again.

    It receives the world reached through the tick's own transaction
    (`Txn.world`), allowing a durable adapter to make world and queue writes
    atomic.

    An action may be its own effect -- `ctx.emit(self)` -- when what it does and
    what happens are the same thing. Nothing here requires a separate class.
    """

    def apply(self, world: W, /) -> None:
        """Apply this mutation to the transaction's game-owned world."""
        ...
