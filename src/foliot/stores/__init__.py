"""Structural persistence contracts and the in-memory reference store.

foliot owns *when* to save; the game owns *how* and *where*. The library ships
no database and no dependency, but it does not leave the timing to the game,
because the timing is the hard part and only the engine knows it.

Two protocols, because reading is always legal and writing is not. The only way
to obtain a `Txn` is to be inside a tick, so there is no write method anyone
*could* call at the wrong moment: the rule becomes unsayable rather than
remembered. Even an in-memory implementation stages foliot-owned changes until
clean exit; a context manager is a transaction boundary, not decorative syntax.
"""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Protocol

from foliot.actions import BaseAction
from foliot.ids import EntityId, SuspensionId, Tick
from foliot.stores.memory import MemoryStore

__all__ = ["MemoryStore", "Store", "Txn"]


class Txn[W](Protocol):
    """Writing. Valid only inside a tick.

    Implementations should collect these calls and flush set-based batches at
    commit rather than issuing one database round-trip per operation.

    Nothing here is a game verb. There is no `damage`, no `hp`. Game state
    changes travel as `Effect` objects the engine only knows how to `apply`.
    """

    @property
    def world(self) -> W:
        """The game's world, reached through this tick's transaction.

        The engine calls `effect.apply(txn.world)`, so an effect's write lands
        in the same transaction as the queue by construction. That turns the
        queue transaction. Foliot never looks inside `W`.
        """
        ...

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        """Schedule an action, binding it on first successful admission.

        `due_tick=None` makes it recurring. A new action receives its
        one stable `seq`; an already-bound action keeps its existing `seq` and
        replaces only its active state.

        Args:
            action: New unbound action or active action already owned by this
                store.
            due_tick: Future deadline, or `None` for recurring execution.
        """
        ...

    def delete(self, action: BaseAction[W], /) -> None:
        """Remove an action from every future due snapshot."""
        ...

    def delete_owned_by(self, entity_id: EntityId, /) -> None:
        """Remove every action owned by one entity in this transaction.

        This includes active, suspended, and newly scheduled actions. It never
        follows game-owned target fields and never closes an Event implicitly.
        """
        ...

    def suspend(self, entity_id: EntityId, by: SuspensionId, /) -> None:
        """Suspend every suspendable action owned by `entity_id`."""
        ...

    def resume(self, by: SuspensionId, /) -> None:
        """Wake everything tagged with `by`, shifting deadlines by the pause."""
        ...

    def log(self, tick: Tick, line: str, /) -> None:
        """Append one deterministic journal line for `tick`."""
        ...


class Store[W](Protocol):
    """Reading, and opening a tick. The consumer's persistence adapter.

    The engine never calls "save". It does its work inside a boundary the store
    defines, which is why this asks for a context manager rather than a `save()`
    method: Postgres uses `BEGIN`/`COMMIT`, while an in-memory implementation
    stages its own queue, log, binding, and clock changes until clean exit.

    Two obligations that are contracts, not suggestions:

    - **A clean exit from `tick_transaction(n)` records tick n as finished**, so
      that `current_tick()` then returns `n + 1`. There is deliberately no
      `advance_to`: the work and the clock are one write, so no implementation
      *can* separate them and leave the queue disagreeing with the world.
    - **A committed deletion excludes the action from every later `due(...)`
      snapshot.** The supported architecture has one active simulation runner
      per world; foliot does not promise to invalidate Python references that
      user code retained from an older snapshot.
    """

    @property
    def world_seed(self) -> int:
        """The one unsigned 128-bit seed persisted for this world.

        `new_world_seed()` is the secure production default, while deliberately
        chosen values such as `1` remain valid. The store owns persistence:
        replay after a restart needs the same value, and a seed that lives only
        in configuration is one deploy away from being lost or changed.
        """
        ...

    def current_tick(self) -> Tick:
        """Return the next unfinished logical tick."""
        ...

    def due(self, tick: Tick, /) -> Iterable[BaseAction[W]]:
        """Everything that should run now: rows due at `tick`, plus every
        recurring action.

        Args:
            tick: Current logical tick. Implementations include overdue work.

        Every returned action must be `Bound`. Order is not significant, and
        callers may process the result in any order.
        """
        ...

    def tick_transaction(self, tick: Tick, /) -> AbstractContextManager[Txn[W]]:
        """Open the atomic write boundary for exactly `tick`."""
        ...
