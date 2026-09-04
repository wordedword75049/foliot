from typing import override

import pytest

from foliot import (
    Active,
    BaseAction,
    Bound,
    EntityId,
    MemoryStore,
    Store,
    Suspended,
    SuspensionId,
    TickContext,
    Unbound,
)

type World = dict[str, int]


class ExampleAction(BaseAction[World]):
    __slots__ = ("last_pause", "name")

    def __init__(
        self,
        name: str,
        *,
        entity_id: EntityId | None = None,
        suspendable: bool = True,
    ) -> None:
        super().__init__(
            EntityId("ivan") if entity_id is None else entity_id,
            suspendable=suspendable,
        )
        self.name = name
        self.last_pause: int | None = None

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        del ctx

    @override
    def on_resume(self, paused_for: int, /) -> None:
        self.last_pause = paused_for


def accept_store(store: Store[World]) -> None:
    """Compile-time assertion that `MemoryStore` satisfies the protocol."""
    del store


def fail_after_staging(
    store: MemoryStore[World],
    action: ExampleAction,
    *,
    tick: int,
) -> None:
    with store.tick_transaction(tick) as txn:
        txn.schedule(action, 10)
        txn.log(tick, "not committed")
        raise RuntimeError("tick failed")


def schedule_at(
    store: MemoryStore[World],
    action: ExampleAction,
    *,
    tick: int,
    due_tick: int,
) -> None:
    with store.tick_transaction(tick) as txn:
        txn.schedule(action, due_tick)


def open_transaction(store: MemoryStore[World], tick: int) -> None:
    with store.tick_transaction(tick):
        pass


def open_nested_transaction(store: MemoryStore[World]) -> None:
    with store.tick_transaction(0), store.tick_transaction(0):
        pass


def fail_suspended_reschedule(
    store: MemoryStore[World],
    action: ExampleAction,
    *,
    tick: int,
) -> None:
    with store.tick_transaction(tick) as txn:
        txn.log(tick, "must roll back")
        txn.schedule(action, 20)


def test_memory_store_should_satisfy_store_protocol() -> None:
    accept_store(MemoryStore[World]({}, 1))


def test_initial_actions_should_bind_in_order_without_advancing_tick() -> None:
    recurring = ExampleAction("recurring")
    immediate = ExampleAction("immediate")
    store = MemoryStore[World](
        {},
        1,
        current_tick=5,
        initial_actions=((recurring, None), (immediate, 5)),
    )

    assert recurring.binding == Bound(1, Active(None))
    assert immediate.binding == Bound(2, Active(5))
    assert store.due(5) == (recurring, immediate)
    assert store.current_tick() == 5


def test_initial_actions_should_reject_past_deadlines_before_binding_anything() -> None:
    valid = ExampleAction("valid")
    past = ExampleAction("past")

    with pytest.raises(ValueError, match="cannot be before current_tick"):
        MemoryStore[World](
            {},
            1,
            current_tick=5,
            initial_actions=((valid, 5), (past, 4)),
        )

    assert valid.binding == Unbound()
    assert past.binding == Unbound()


def test_initial_actions_should_reject_duplicate_or_bound_objects() -> None:
    duplicate = ExampleAction("duplicate")
    with pytest.raises(ValueError, match="cannot be scheduled twice"):
        MemoryStore[World](
            {},
            1,
            initial_actions=((duplicate, None), (duplicate, 1)),
        )

    bound = ExampleAction("bound")
    MemoryStore[World]({}, 1, initial_actions=((bound, None),))
    with pytest.raises(RuntimeError, match="must be unbound"):
        MemoryStore[World]({}, 1, initial_actions=((bound, None),))


def test_new_action_should_bind_only_after_transaction_commits() -> None:
    store = MemoryStore[World]({}, 1)
    walk = ExampleAction("walk")

    with store.tick_transaction(0) as txn:
        txn.schedule(walk, 10)
        assert walk.binding == Unbound()
        assert store.due(10) == ()

    assert walk.binding == Bound(1, Active(10))
    assert store.due(10) == (walk,)
    assert store.current_tick() == 1


def test_failed_transaction_should_publish_nothing_or_consume_seq() -> None:
    store = MemoryStore[World]({}, 1)
    abandoned = ExampleAction("abandoned")

    with pytest.raises(RuntimeError, match="tick failed"):
        fail_after_staging(store, abandoned, tick=0)

    admitted = ExampleAction("admitted")
    with store.tick_transaction(0) as txn:
        txn.schedule(admitted, 10)

    assert abandoned.binding == Unbound()
    assert admitted.binding == Bound(1, Active(10))
    assert store.logs == ()
    assert store.current_tick() == 1


def test_due_should_include_recurring_due_and_overdue_actions_in_seq_order() -> None:
    store = MemoryStore[World]({}, 1)
    future = ExampleAction("future")
    recurring = ExampleAction("recurring", suspendable=False)

    with store.tick_transaction(0) as txn:
        txn.schedule(future, 10)
        txn.schedule(recurring, None)

    assert store.due(9) == (recurring,)
    assert store.due(10) == (future, recurring)
    assert store.due(50) == (future, recurring)


def test_reschedule_should_preserve_seq_and_remove_the_old_deadline() -> None:
    store = MemoryStore[World]({}, 1)
    poison = ExampleAction("poison", suspendable=False)

    with store.tick_transaction(0) as txn:
        txn.schedule(poison, 5)
    with store.tick_transaction(1) as txn:
        txn.schedule(poison, 8)

    assert poison.binding == Bound(1, Active(8))
    assert store.due(5) == ()
    assert store.due(8) == (poison,)


def test_reschedule_should_move_between_scheduled_and_recurring_shapes() -> None:
    store = MemoryStore[World]({}, 1)
    action = ExampleAction("weather")

    with store.tick_transaction(0) as txn:
        txn.schedule(action, 5)
    with store.tick_transaction(1) as txn:
        txn.schedule(action, None)

    assert action.binding == Bound(1, Active(None))
    assert store.due(1) == (action,)
    assert store.due(5) == (action,)


def test_suspension_should_hide_only_suspendable_actions() -> None:
    store = MemoryStore[World]({}, 1)
    walk = ExampleAction("walk")
    poison = ExampleAction("poison", suspendable=False)
    fight = SuspensionId("fight-7")

    with store.tick_transaction(0) as txn:
        txn.schedule(walk, 10)
        txn.schedule(poison, None)
    with store.tick_transaction(1) as txn:
        txn.suspend(EntityId("ivan"), fight)

    assert walk.binding == Bound(1, Suspended(1, fight, 10))
    assert poison.binding == Bound(2, Active(None))
    assert store.due(10) == (poison,)


def test_resume_should_shift_deadline_and_restore_action_to_queue() -> None:
    store = MemoryStore[World]({}, 1)
    walk = ExampleAction("walk")
    fight = SuspensionId("fight-7")

    with store.tick_transaction(0) as txn:
        txn.schedule(walk, 10)
    with store.tick_transaction(1) as txn:
        txn.suspend(EntityId("ivan"), fight)
    for tick in range(2, 5):
        with store.tick_transaction(tick):
            pass
    with store.tick_transaction(5) as txn:
        txn.resume(fight)

    assert walk.binding == Bound(1, Active(14))
    assert walk.last_pause == 4
    assert store.due(13) == ()
    assert store.due(14) == (walk,)


def test_delete_should_remove_active_and_suspended_actions() -> None:
    store = MemoryStore[World]({}, 1)
    recurring = ExampleAction("recurring")
    suspended = ExampleAction("suspended")
    fight = SuspensionId("fight-7")

    with store.tick_transaction(0) as txn:
        txn.schedule(recurring, None)
        txn.schedule(suspended, 10)
    with store.tick_transaction(1) as txn:
        txn.suspend(EntityId("ivan"), fight)
    with store.tick_transaction(2) as txn:
        txn.delete(recurring)
        txn.delete(suspended)

    assert store.due(100) == ()


def test_schedule_should_reject_current_or_past_deadline() -> None:
    store = MemoryStore[World]({}, 1, current_tick=5)

    with pytest.raises(ValueError, match="later than the transaction tick"):
        schedule_at(store, ExampleAction("walk"), tick=5, due_tick=5)

    with pytest.raises(ValueError, match="later than the transaction tick"):
        schedule_at(store, ExampleAction("walk"), tick=5, due_tick=4)


def test_transaction_should_reject_a_tick_other_than_current() -> None:
    store = MemoryStore[World]({}, 1, current_tick=5)

    with pytest.raises(ValueError, match="does not match current tick 5"):
        open_transaction(store, 4)
    with pytest.raises(ValueError, match="does not match current tick 5"):
        open_transaction(store, 6)


def test_store_should_reject_a_second_open_transaction() -> None:
    store = MemoryStore[World]({}, 1)

    with pytest.raises(RuntimeError, match="only one open transaction"):
        open_nested_transaction(store)


def test_transaction_should_expose_supplied_world_and_commit_logs_in_order() -> None:
    world = {"ivan_hp": 10}
    store = MemoryStore[World](world, 1)

    with store.tick_transaction(0) as txn:
        assert txn.world is world
        txn.log(0, "Ivan enters the forest")
        txn.log(0, "The forest watches")

    assert store.logs == (
        (0, "Ivan enters the forest"),
        (0, "The forest watches"),
    )


def test_failed_reschedule_commit_should_restore_store_owned_state() -> None:
    store = MemoryStore[World]({}, 1)
    walk = ExampleAction("walk")
    fight = SuspensionId("fight-7")

    with store.tick_transaction(0) as txn:
        txn.schedule(walk, 10)
        txn.log(0, "committed before later failure")
    with store.tick_transaction(1) as txn:
        txn.suspend(EntityId("ivan"), fight)

    with pytest.raises(RuntimeError, match="suspended action cannot be scheduled"):
        fail_suspended_reschedule(store, walk, tick=2)

    assert walk.binding == Bound(1, Suspended(1, fight, 10))
    assert store.logs == ((0, "committed before later failure"),)
    assert store.current_tick() == 2
    assert store.due(100) == ()
