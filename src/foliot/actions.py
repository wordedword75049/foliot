"""The unit of work.

`BaseAction` and the `ActionState` union arrive at M2; this module defines the
contract the engine requires.
"""

from typing import Protocol

from foliot.context import TickContext
from foliot.ids import EntityId

__all__ = ["Action"]


class Action[W](Protocol):
    """One scheduled unit of work, owned by exactly one entity.

    A Protocol rather than a base class: the engine only ever type-checks
    against this, so nothing forces a consumer to inherit. `BaseAction` will
    ship as a convenience that satisfies it, not as a requirement.
    """

    @property
    def entity_id(self) -> EntityId: ...

    @property
    def seq(self) -> int:
        """Stable identity within a tick. Assigned by the store when the action
        is queued; never derived from processing order.

        This seeds the RNG together with `(world_seed, entity_id, tick)`, so if
        `seq` came from an action's position in the due list, shuffling a tick
        would change every draw and both guarantee 1 and guarantee 2 would fall
        over at once. Demonstrated: two of one entity's actions rolling in the
        same tick swap their results under positional `seq`, and do not under a
        stable one.

        It also fixes the order the journal is written in. A tick may be
        *processed* in any order; its story is *told* in `seq` order.
        """
        ...

    @property
    def suspendable(self) -> bool:
        """Does this stop when its owner is interrupted?

        True for activities -- walking, crafting. False for effects -- poison,
        hunger, cooldowns -- which tick through any suspension (§6.2). One bit
        replaces the activity bundle an earlier design threaded through game
        code as an opaque `group_id`.
        """
        ...

    def process(self, ctx: TickContext[W], /) -> None:
        """Say what should happen, by telling `ctx`.

        Nothing said here takes effect here. Read `ctx.tick` and `ctx.rng`; call
        `ctx.emit`, `ctx.schedule`, `ctx.finish`. Never touch the world
        directly: a handler that writes during the loop is visible to whatever
        runs next, and then processing order decides outcomes.
        """
        ...
