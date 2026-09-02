"""Persistence: the tick is the transaction (§8).

foliot owns *when* to save; the game owns *how* and *where*. The library ships
no database and no dependency, but it does not leave the timing to the game,
because the timing is the hard part and only the engine knows it.

Two protocols, because reading is always legal and writing is not. The only way
to obtain a `Txn` is to be inside a tick, so there is no write method anyone
*could* call at the wrong moment: the rule becomes unsayable rather than
remembered.
"""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Protocol

from foliot.actions import BaseAction
from foliot.ids import EntityId, SuspensionId, Tick

__all__ = ["Store", "Txn"]


class Txn[W](Protocol):
    """Writing. Valid only inside a tick.

    **Implementations must batch.** Measured against Postgres 16 with one tick
    per transaction and 10,000 actions rescheduling: flushing set-based costs
    196 ms/tick, while issuing a statement per call costs 2,962 ms -- 296% of a
    one-second tick, so the world falls permanently behind. Same rows, same
    single commit, 15x apart. `schedule` and `delete` should therefore collect,
    and flush once as `INSERT ... SELECT unnest(...)` and
    `DELETE ... WHERE id = ANY(...)`.

    Nothing here is a game verb. There is no `damage`, no `hp`. Game state
    changes travel as `Effect` objects the engine only knows how to `apply`.
    """

    @property
    def world(self) -> W:
        """The game's world, reached through this tick's transaction.

        The engine calls `effect.apply(txn.world)`, so an effect's write lands
        in the same transaction as the queue by construction. That turns the
        caveat in §8.4 -- atomicity holds only while effects share the queue's
        transaction -- from something the game must honour into a property of
        the shape. foliot never looks inside `W`.
        """
        ...

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        """Schedule an action, binding it on first successful admission.

        `due_tick=None` makes it recurring (§5.7). A new action receives its
        one stable `seq`; an already-bound action keeps its existing `seq` and
        replaces only its active state (§6.4, §9.4b).
        """
        ...

    def delete(self, action: BaseAction[W], /) -> None:
        """Finished or invalidated actions are removed, not tombstoned (§5.5)."""
        ...

    def suspend(self, entity_id: EntityId, by: SuspensionId, /) -> None:
        """Suspend every suspendable action this entity owns, tagged with `by`."""
        ...

    def resume(self, by: SuspensionId, /) -> None:
        """Wake everything tagged with `by`, shifting deadlines by the pause."""
        ...

    def log(self, tick: Tick, line: str, /) -> None: ...


class Store[W](Protocol):
    """Reading, and opening a tick. The consumer's persistence adapter.

    The engine never calls "save". It does its work inside a boundary the store
    defines, which is why this asks for a context manager rather than a `save()`
    method: `BEGIN`/`COMMIT` is a shape a dictionary can ignore for free.

    Two obligations that are contracts, not suggestions:

    - **A clean exit from `tick_transaction(n)` records tick n as finished**, so
      that `current_tick()` then returns `n + 1`. There is deliberately no
      `advance_to`: the work and the clock are one write, so no implementation
      *can* separate them and leave the queue disagreeing with the world (§8.3).
    - **Cancellation must reach whoever already holds an action**, not merely be
      absent from storage. A near-horizon cache or a second worker may hold a
      copy that a delete cannot reach, and then the wolf you cancelled still
      bites (§5.5).
    """

    @property
    def world_seed(self) -> int:
        """Drawn from the clock once, when the world is created (§9.1).

        Persisted, and therefore read from here rather than passed in: replay
        after a restart needs the same seed, and a seed that lives only in a
        config file or a constructor argument is one deploy away from being
        lost or changed. The world is born unpredictable and is reproducible
        only in the sense that, having happened, it can be re-derived.
        """
        ...

    def current_tick(self) -> Tick: ...

    def due(self, tick: Tick, /) -> Iterable[BaseAction[W]]:
        """Everything that should run now: rows due at `tick`, plus every
        recurring action (§5.7).

        Every returned action must be `Bound`. Order is not significant, and
        callers may process the result in any order.
        """
        ...

    def tick_transaction(self, tick: Tick, /) -> AbstractContextManager[Txn[W]]: ...
