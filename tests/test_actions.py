from typing import override

import pytest

from foliot import Active, BaseAction, Bound, EntityId, Suspended, SuspensionId, Unbound
from foliot.context import TickContext


class ExampleAction(BaseAction[object]):
    __slots__ = ("last_pause",)

    def __init__(self, *, suspendable: bool = True) -> None:
        super().__init__(EntityId("ivan"), suspendable=suspendable)
        self.last_pause: int | None = None

    @override
    def process(self, ctx: TickContext[object], /) -> None:
        del ctx

    @override
    def on_resume(self, paused_for: int, /) -> None:
        self.last_pause = paused_for


def test_new_action_should_have_a_complete_unbound_state() -> None:
    action = ExampleAction()

    assert action.entity_id == EntityId("ivan")
    assert action.binding == Unbound()
    with pytest.raises(RuntimeError, match="unbound action has no seq"):
        _ = action.seq
    with pytest.raises(RuntimeError, match="unbound action has no state"):
        _ = action.state


def test_binding_should_assign_seq_and_state_exactly_once() -> None:
    action = ExampleAction()

    action.bind(17, Active(20))

    assert action.binding == Bound(17, Active(20))
    with pytest.raises(RuntimeError, match="only be bound once"):
        action.bind(18, Active(30))


def test_reschedule_should_change_only_the_deadline() -> None:
    action = ExampleAction()
    action.bind(17, Active(20))

    action.reschedule(40)

    assert action.binding == Bound(17, Active(40))


def test_resume_should_shift_deadline_and_notify_game_by_same_pause() -> None:
    action = ExampleAction()
    action.bind(17, Active(20))

    action.suspend(12, SuspensionId("fight-3"))
    assert action.binding == Bound(17, Suspended(12, SuspensionId("fight-3"), 20))

    action.resume(19)

    assert action.binding == Bound(17, Active(27))
    assert action.last_pause == 7


def test_resume_should_preserve_recurring_shape() -> None:
    action = ExampleAction()
    action.bind(17, Active(None))

    action.suspend(12, SuspensionId("fight-3"))
    action.resume(19)

    assert action.binding == Bound(17, Active(None))
    assert action.last_pause == 7


def test_non_suspendable_action_should_ignore_suspension() -> None:
    action = ExampleAction(suspendable=False)
    action.bind(17, Active(20))

    action.suspend(12, SuspensionId("fight-3"))

    assert action.binding == Bound(17, Active(20))
