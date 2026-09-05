"""Capabilities supplied to action handlers and tick finalizers.

`TickContext` is deliberately narrow: two things to read and five things to say.
It is the one object every handler touches, which makes it the one object most
likely to rot into a service locator -- a `ctx` that can reach everything, used
by handlers to reach anything. Treat any proposal to add `ctx.store`,
`ctx.world` or `ctx.entity` as the alarm rather than the feature.

The discipline that keeps it contained: **pass the capability, not the
context.** A handler that needs a die roll downstream passes `ctx.rng`, never
`ctx`. Then nothing below `process()` can reach the scheduler, so nothing below
`process()` can schedule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from foliot.effects import Effect
from foliot.ids import EntityId, SuspensionId, Tick
from foliot.rng import Rng

if TYPE_CHECKING:
    from foliot.actions import BaseAction
    from foliot.events import EventId

__all__ = ["FinalizationContext", "TickContext", "TickFinalizer"]


class TickContext[W](Protocol):
    """Reads the tick; collects what the handler wants to happen.

    Nothing said to a `TickContext` takes effect when it is said. The engine
    gives each action a fresh context, and:

    - the handler returns normally -> its collected work joins the tick's pile
    - the handler raises           -> its context is discarded whole, nothing
      it said happens, and the engine skips it and carries on

    Only once every due action has been asked does the engine drain the pile:
    schedules and suspension requests into the queue, effects applied, log
    lines written. No action can therefore observe another action's effects
    from the same tick.

    `tick` and `rng` are read-only properties rather than plain annotations: a
    Protocol written `tick: Tick` demands something *settable*, which a real
    `Simulation` -- whose tick is computed from the store so that nothing can
    assign the world's clock -- would fail to satisfy.
    """

    @property
    def tick(self) -> Tick: ...

    @property
    def rng(self) -> Rng:
        """Already bound to `(world_seed, entity_id, tick, seq)`. Never seed."""
        ...

    def emit(self, effect: Effect[W], /) -> None:
        """Stage an application-defined state mutation for the apply phase.

        Args:
            effect: Object whose `apply(world)` method performs the mutation.
        """
        ...

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        """Queue a new action or reschedule an active one.

        Args:
            action: Action object to admit or reschedule.
            due_tick: Strictly future deadline, or `None` for execution on
                every tick.

        Raises:
            TypeError: If a deadline is not an integer or `None`.
            ValueError: If a concrete deadline is not later than `tick`.
        """
        ...

    def log(self, line: str, /) -> None:
        """Write one line of narrative for the observer.

        Lines are written in permanent action-sequence order rather than store
        iteration order.

        Args:
            line: Complete application-facing journal line. Foliot does not format
                or localize it.
        """
        ...

    def suspend(
        self,
        entity_id: EntityId,
        /,
        *,
        by: SuspensionId | EventId,
    ) -> None:
        """Pause an entity's suspendable actions under one waking handle.

        Args:
            entity_id: Owner whose suspendable actions should pause.
            by: Stable handle used for the later matching resume.
        """
        ...

    def finish(self) -> None:
        """This action is done; remove it from the queue.

        A scheduled action can stop simply by not rescheduling. A recurring one
        has no deadline to decline, so it must say so.
        """
        ...


class FinalizationContext[W](Protocol):
    """Collect lifecycle work after all normal effects have been applied.

    It deliberately has no RNG or world property. The application receives the
    post-effect world as the other argument to `TickFinalizer.finalize`, while
    every requested write still travels through this collecting boundary.
    """

    @property
    def tick(self) -> Tick:
        """Logical tick currently being finalized."""
        ...

    def emit(self, effect: Effect[W], /) -> None:
        """Stage one final effect for application in this transaction."""
        ...

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        """Stage an action for a future tick, or as recurring work."""
        ...

    def delete(self, action: BaseAction[W], /) -> None:
        """Remove one action from all future due snapshots."""
        ...

    def delete_owned_by(self, entity_id: EntityId, /) -> None:
        """Remove all actions owned by one entity."""
        ...

    def log(self, line: str, /) -> None:
        """Append one deterministic journal line for this tick."""
        ...


class TickFinalizer[W](Protocol):
    """Optional application-owned policy run after a tick's normal effects.

    Use a finalizer for rules that depend on the combined post-effect world,
    such as death or cleanup. A raised exception aborts the tick transaction.
    """

    def finalize(self, world: W, ctx: FinalizationContext[W], /) -> None:
        """Inspect `world` and stage any final lifecycle work in `ctx`."""
        ...
