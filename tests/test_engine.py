import logging
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import override

import pytest

from foliot import (
    Active,
    BaseAction,
    Bound,
    Driver,
    EntityId,
    ManualDriver,
    MemoryStore,
    Simulation,
    Store,
    Tick,
    TickContext,
    Txn,
    Unbound,
)

type World = dict[str, float]


@dataclass(frozen=True, slots=True)
class SetValue:
    key: str
    value: float

    def apply(self, world: World, /) -> None:
        world[self.key] = self.value


@dataclass(frozen=True, slots=True)
class Increment:
    key: str

    def apply(self, world: World, /) -> None:
        world[self.key] = world.get(self.key, 0.0) + 1


@dataclass(frozen=True, slots=True)
class RecordRoll:
    key: str
    value: float
    position: int

    def apply(self, world: World, /) -> None:
        world[self.key] = self.value
        world["effect_order"] = world.get("effect_order", 0.0) * 10 + self.position


class RaisingEffect:
    def apply(self, world: World, /) -> None:
        del world
        raise RuntimeError("effect failed")


class ScheduledChain(BaseAction[World]):
    __slots__ = ("processed_at",)

    def __init__(self) -> None:
        super().__init__(EntityId("chain"), suspendable=False)
        self.processed_at: list[Tick] = []

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        self.processed_at.append(ctx.tick)
        if ctx.tick == 90:
            ctx.schedule(self, 100)


class RecurringAction(BaseAction[World]):
    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.emit(Increment("recurring_runs"))


class RollOnce(BaseAction[World]):
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        super().__init__(EntityId("same-entity"), suspendable=False)
        self.name = name

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        value = ctx.rng.random()
        ctx.emit(RecordRoll(self.name, value, 1 if self.name == "first" else 2))
        ctx.log(f"{self.name}:{value:.17g}")
        ctx.finish()


class FailingHandler(BaseAction[World]):
    __slots__ = ("successor",)

    def __init__(self, successor: BaseAction[World]) -> None:
        super().__init__(EntityId("broken"), suspendable=False)
        self.successor = successor

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.emit(SetValue("discarded", 1.0))
        ctx.schedule(self.successor, ctx.tick + 1)
        ctx.log("discarded")
        raise RuntimeError("handler failed")


class CompleteAction(BaseAction[World]):
    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.emit(SetValue("completed", 1.0))
        ctx.log("completed")
        ctx.finish()


class FinishWithSuccessor(BaseAction[World]):
    __slots__ = ("successor",)

    def __init__(self, successor: BaseAction[World]) -> None:
        super().__init__(EntityId("source"), suspendable=False)
        self.successor = successor

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.schedule(self.successor, ctx.tick + 1)
        ctx.finish()


class FinishAndReschedule(BaseAction[World]):
    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.schedule(self, ctx.tick + 1)
        ctx.finish()


class ScheduleTarget(BaseAction[World]):
    __slots__ = ("target", "twice")

    def __init__(self, name: str, target: BaseAction[World], *, twice: bool = False) -> None:
        super().__init__(EntityId(name), suspendable=False)
        self.target = target
        self.twice = twice

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.schedule(self.target, ctx.tick + 1)
        if self.twice:
            ctx.schedule(self.target, ctx.tick + 2)


class EffectFailure(BaseAction[World]):
    __slots__ = ("successor",)

    def __init__(self, successor: BaseAction[World]) -> None:
        super().__init__(EntityId("effect-source"), suspendable=False)
        self.successor = successor

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.schedule(self.successor, ctx.tick + 1)
        ctx.log("must roll back")
        ctx.emit(RaisingEffect())


class ReverseDueStore:
    """Same store with a deliberately reversed due snapshot."""

    __slots__ = ("inner",)

    def __init__(self, inner: MemoryStore[World]) -> None:
        self.inner = inner

    @property
    def world_seed(self) -> int:
        return self.inner.world_seed

    def current_tick(self) -> Tick:
        return self.inner.current_tick()

    def due(self, tick: Tick, /) -> Iterable[BaseAction[World]]:
        return reversed(self.inner.due(tick))

    def tick_transaction(self, tick: Tick, /) -> AbstractContextManager[Txn[World]]:
        return self.inner.tick_transaction(tick)


def accept_driver(driver: Driver) -> None:
    """Compile-time assertion that `ManualDriver` satisfies the protocol."""
    del driver


def accept_store(store: Store[World]) -> None:
    """Compile-time assertion that the reversing store satisfies the protocol."""
    del store


def test_manual_driver_should_satisfy_driver_protocol() -> None:
    accept_driver(ManualDriver(until_tick=0))


def test_manual_driver_should_reject_invalid_target() -> None:
    with pytest.raises(TypeError, match="int, not bool"):
        ManualDriver(until_tick=True)
    with pytest.raises(ValueError, match="non-negative"):
        ManualDriver(until_tick=-1)


def test_manual_driver_should_include_target_tick_and_fire_its_deadline() -> None:
    chain = ScheduledChain()
    store = MemoryStore[World](
        {},
        1,
        current_tick=90,
        initial_actions=((chain, 90),),
    )

    Simulation(store).run(ManualDriver(until_tick=100))

    assert chain.processed_at == [90, 100]
    assert chain.binding == Bound(1, Active(100))
    assert store.due(100) == ()
    assert store.current_tick() == 101


def test_recurring_action_should_run_each_tick_without_rescheduling() -> None:
    action = RecurringAction(
        EntityId("recurring"),
        suspendable=False,
    )
    world: World = {}
    store = MemoryStore(world, 1, initial_actions=((action, None),))

    Simulation(store).run(ManualDriver(until_tick=2))

    assert world == {"recurring_runs": 3.0}
    assert store.due(3) == (action,)


def test_processing_order_should_not_change_world_rng_or_story_order() -> None:
    normal_world: World = {}
    reversed_world: World = {}
    normal_store = MemoryStore[World](
        normal_world,
        987654321,
        initial_actions=((RollOnce("first"), None), (RollOnce("second"), None)),
    )
    reversed_inner = MemoryStore[World](
        reversed_world,
        987654321,
        initial_actions=((RollOnce("first"), None), (RollOnce("second"), None)),
    )
    reversed_store = ReverseDueStore(reversed_inner)
    accept_store(reversed_store)

    Simulation(normal_store).process_tick()
    Simulation(reversed_store).process_tick()

    assert normal_store.logs == reversed_inner.logs
    assert normal_store.logs[0][1].startswith("first:")
    assert normal_store.logs[1][1].startswith("second:")
    assert normal_world == reversed_world


def test_handler_failure_should_be_visible_retry_without_duplication_and_continue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    successor = CompleteAction(
        EntityId("successor"),
        suspendable=False,
    )
    failing = FailingHandler(successor)
    successful = CompleteAction(
        EntityId("working"),
        suspendable=False,
    )
    world: World = {}
    store = MemoryStore(
        world,
        1,
        initial_actions=((failing, None), (successful, 0)),
    )
    simulation = Simulation(store)
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    simulation.process_tick()

    assert world == {"completed": 1.0}
    assert store.logs == ((0, "completed"),)
    assert successor.binding == Unbound()
    assert store.due(1) == (failing,)
    assert failing.binding == Bound(1, Active(None))
    assert store.current_tick() == 1
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert "tick=0" in record.getMessage()
    assert "action=FailingHandler" in record.getMessage()
    assert "entity_id=broken" in record.getMessage()
    assert "seq=1" in record.getMessage()
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
    assert record.exc_info[2] is not None

    caplog.clear()
    simulation.process_tick()

    assert store.due(2) == (failing,)
    assert failing.binding == Bound(1, Active(None))
    assert successor.binding == Unbound()
    assert len(caplog.records) == 1


def test_finish_should_allow_a_different_successor() -> None:
    successor = CompleteAction(
        EntityId("successor"),
        suspendable=False,
    )
    source = FinishWithSuccessor(successor)
    store = MemoryStore[World]({}, 1, initial_actions=((source, None),))

    Simulation(store).process_tick()

    assert store.due(1) == (successor,)
    assert successor.binding == Bound(2, Active(1))


def test_finish_and_self_reschedule_should_discard_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    action = FinishAndReschedule(
        EntityId("contradiction"),
        suspendable=False,
    )
    store = MemoryStore[World]({}, 1, initial_actions=((action, None),))
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    Simulation(store).process_tick()

    assert action.binding == Bound(1, Active(None))
    assert store.due(1) == (action,)
    assert "cannot finish and reschedule itself" in caplog.text


def test_duplicate_schedule_in_one_context_should_discard_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = CompleteAction(
        EntityId("target"),
        suspendable=False,
    )
    source = ScheduleTarget("source", target, twice=True)
    store = MemoryStore[World]({}, 1, initial_actions=((source, None),))
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    Simulation(store).process_tick()

    assert source.binding == Bound(1, Active(None))
    assert store.due(1) == (source,)
    assert target.binding == Unbound()
    assert "scheduled more than once" in caplog.text


def test_duplicate_schedule_across_contexts_should_discard_both_and_continue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = CompleteAction(
        EntityId("target"),
        suspendable=False,
    )
    first = ScheduleTarget("first", target)
    second = ScheduleTarget("second", target)
    unrelated = CompleteAction(
        EntityId("unrelated"),
        suspendable=False,
    )
    world: World = {}
    store = MemoryStore(
        world,
        1,
        initial_actions=((first, None), (second, None), (unrelated, 0)),
    )
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    Simulation(store).process_tick()

    assert world == {"completed": 1.0}
    assert store.logs == ((0, "completed"),)
    assert store.due(1) == (first, second)
    assert target.binding == Unbound()
    assert len(caplog.records) == 2


def test_effect_failure_should_escape_and_roll_back_foliot_state() -> None:
    successor = CompleteAction(
        EntityId("successor"),
        suspendable=False,
    )
    source = EffectFailure(successor)
    store = MemoryStore[World]({}, 1, initial_actions=((source, None),))
    simulation = Simulation(store)

    with pytest.raises(RuntimeError, match="effect failed"):
        simulation.process_tick()

    assert store.current_tick() == 0
    assert store.logs == ()
    assert store.due(0) == (source,)
    assert source.binding == Bound(1, Active(None))
    assert successor.binding == Unbound()
