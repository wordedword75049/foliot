"""What a handler is handed.

`TickContext` is deliberately narrow: two things to read, four things to say.
It is the one object every handler touches, which makes it the one object most
likely to rot into a service locator -- a `ctx` that can reach everything, used
by handlers to reach anything. Treat any proposal to add `ctx.store`,
`ctx.world` or `ctx.entity` as the alarm rather than the feature.

The discipline that keeps it contained: **pass the capability, not the
context.** A handler that needs a die roll downstream passes `ctx.rng`, never
`ctx`. Then nothing below `process()` can reach the scheduler, so nothing below
`process()` can schedule.
"""

from typing import TYPE_CHECKING, Protocol

from foliot.effects import Effect
from foliot.ids import Tick
from foliot.rng import Rng

if TYPE_CHECKING:
    from foliot.actions import BaseAction

__all__ = ["TickContext"]


class TickContext[W](Protocol):
    """Reads the tick; collects what the handler wants to happen.

    Nothing said to a `TickContext` takes effect when it is said. The engine
    gives each action a fresh context, and:

    - the handler returns normally -> its collected work joins the tick's pile
    - the handler raises           -> its context is discarded whole, nothing
      it said happens, and the engine skips it and carries on

    Only once every due action has been asked does the engine drain the pile:
    schedules into the queue, effects applied, log lines written. So no action
    can observe another's changes within a tick, which is what makes a tick's
    actions safe to process in any iteration order (§2).

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
        """Stage a change to the world. Applied after the whole loop."""
        ...

    def schedule(self, action: "BaseAction[W]", due_tick: Tick | None, /) -> None:
        """Queue an action. `None` makes it recurring -- it runs every tick and
        stores no deadline, because for it the next due tick is always `now + 1`
        (§5.7).

        A concrete `due_tick` must be strictly in the future. Scheduling into
        the current tick lands in a bucket the engine has already read, so
        whether anything else sees it depends on who ran first (§5.6).
        Implementations reject it.
        """
        ...

    def log(self, line: str, /) -> None:
        """Write one line of narrative for the observer.

        A first-class channel rather than something scraped out of effects: in
        a zero-player game the log *is* the product (§10.4). Lines are written
        in `seq` order, not processing order, so two runs of one seed produce
        the same story as well as the same world.
        """
        ...

    def finish(self) -> None:
        """This action is done; remove it from the queue.

        A scheduled action can stop simply by not rescheduling. A recurring one
        has no deadline to decline, so it must say so.
        """
        ...
