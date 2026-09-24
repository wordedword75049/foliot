"""External action admission at a logical tick boundary."""

from collections.abc import Callable
from threading import Barrier, Event, Thread
from typing import cast, override

import pytest

from foliot import (
    ActionAdmission,
    ActionState,
    Active,
    BaseAction,
    Bound,
    EntityId,
    MemoryStore,
    Simulation,
    StaleSubmissionError,
    TickContext,
    Unbound,
)
from foliot.events import EventMemoryStore, Events
from foliot.stores import memory as memory_module

type World = dict[str, int]


class RecordOnce(BaseAction[World]):
    def __init__(self, name: str) -> None:
        super().__init__(EntityId(name), suspendable=False)
        self.name = name

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.log(self.name)
        ctx.finish()


class RecordEveryTick(RecordOnce):
    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.log(self.name)


class RollOnce(RecordOnce):
    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.log(f"{self.name}:{ctx.rng.random():.17g}")
        ctx.finish()


class FailAfterBinding(RecordOnce):
    __slots__ = ("fail",)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.fail = True

    @override
    def bind(self, seq: int, state: ActionState, /) -> None:
        super().bind(seq, state)
        if self.fail:
            self.fail = False
            raise RuntimeError("binding failed")


class AdmitDuringBinding(RecordOnce):
    __slots__ = ("nested_error", "store")

    def __init__(self, store: MemoryStore[World]) -> None:
        super().__init__("outer")
        self.store = store
        self.nested_error: BaseException | None = None

    @override
    def bind(self, seq: int, state: ActionState, /) -> None:
        try:
            self.store.admit(RecordOnce("nested"), 0, expected_tick=0)
        except BaseException as error:
            self.nested_error = error
        super().bind(seq, state)


class AttemptReentry(RecordOnce):
    __slots__ = ("simulation", "submission_error")

    def __init__(self) -> None:
        super().__init__("reentry")
        self.simulation: Simulation[World] | None = None
        self.submission_error: BaseException | None = None

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        if self.simulation is None:
            raise RuntimeError("simulation was not attached")
        try:
            self.simulation.submit(RecordOnce("nested"), ctx.tick, expected_tick=ctx.tick)
        except BaseException as error:
            self.submission_error = error
        ctx.finish()


def test_submit_admits_for_current_boundary_without_advancing_it() -> None:
    store = MemoryStore[World]({}, 1, current_tick=7)
    simulation = Simulation(store)
    action = RecordOnce("arrival")

    receipt = simulation.submit(action, 7, expected_tick=7)

    assert receipt.seq == 1
    assert receipt.boundary_tick == 7
    assert action.seq == 1
    assert store.current_tick() == 7
    assert store.due(7) == (action,)
    assert store.logs == ()

    simulation.process_tick()

    assert store.logs == ((7, "arrival"),)
    assert store.due(8) == ()


def test_submit_preserves_future_and_recurring_deadline_semantics() -> None:
    store = MemoryStore[World]({}, 1, current_tick=4)
    simulation = Simulation(store)
    future = RecordOnce("future")
    recurring = RecordEveryTick("recurring")

    assert simulation.submit(future, 6, expected_tick=4) == ActionAdmission(1, 4)
    assert simulation.submit(recurring, None, expected_tick=4) == ActionAdmission(2, 4)
    assert future.binding == Bound(1, Active(6))
    assert recurring.binding == Bound(2, Active(None))
    simulation.process_tick()
    simulation.process_tick()
    simulation.process_tick()

    assert store.logs == ((4, "recurring"), (5, "recurring"), (6, "future"), (6, "recurring"))


def test_submit_continues_sequence_after_initial_and_internal_scheduling() -> None:
    initial = RecordOnce("initial")
    internal = RecordOnce("internal")
    store = MemoryStore[World]({}, 1, initial_actions=((initial, 10),))
    with store.tick_transaction(0) as txn:
        txn.schedule(internal, 10)
    external = RecordOnce("external")

    receipt = Simulation(store).submit(external, 10, expected_tick=1)

    assert receipt == ActionAdmission(3, 1)
    assert store.due(10) == (initial, internal, external)


def test_stale_submission_rejects_action_without_consuming_sequence() -> None:
    world: World = {}
    store = MemoryStore(world, 1, current_tick=5)
    simulation = Simulation(store)
    stale = RecordOnce("stale")

    with pytest.raises(StaleSubmissionError) as caught:
        simulation.submit(stale, 4, expected_tick=4)

    assert caught.value.expected_tick == 4
    assert caught.value.actual_tick == 5
    assert stale.binding == Unbound()
    assert store.due(10) == ()
    assert store.logs == ()
    assert world == {}
    assert store.current_tick() == 5
    assert simulation.submit(RecordOnce("next"), 5, expected_tick=5).seq == 1


@pytest.mark.parametrize("bad_tick", [True, -1, 1.5, "5"])
def test_submit_rejects_invalid_expected_tick(bad_tick: object) -> None:
    store = MemoryStore[World]({}, 1)
    action = RecordOnce("bad")

    with pytest.raises((TypeError, ValueError)):
        store.admit(action, 0, expected_tick=cast(int, bad_tick))

    assert action.binding == Unbound()
    assert store.due(0) == ()


@pytest.mark.parametrize("bad_due", [True, -1, 1.5, "5"])
def test_submit_rejects_invalid_due_tick(bad_due: object) -> None:
    store = MemoryStore[World]({}, 1)
    action = RecordOnce("bad")

    with pytest.raises((TypeError, ValueError)):
        store.admit(action, cast(int, bad_due), expected_tick=0)

    assert action.binding == Unbound()
    assert store.due(0) == ()


def test_submit_rejects_past_due_and_bound_actions() -> None:
    store = MemoryStore[World]({}, 1, current_tick=5)
    past = RecordOnce("past")
    with pytest.raises(ValueError, match="at or after expected_tick"):
        store.admit(past, 4, expected_tick=5)
    assert past.binding == Unbound()

    bound = RecordOnce("bound")
    store.admit(bound, 5, expected_tick=5)
    with pytest.raises(RuntimeError, match="must be unbound"):
        store.admit(bound, 6, expected_tick=5)
    assert store.due(6) == (bound,)
    assert store.admit(RecordOnce("next"), 6, expected_tick=5).seq == 2


def test_failed_binding_restores_unbound_action_and_sequence() -> None:
    store = MemoryStore[World]({}, 1)
    failing = FailAfterBinding("failing")

    with pytest.raises(RuntimeError, match="binding failed"):
        store.admit(failing, 0, expected_tick=0)

    assert failing.binding == Unbound()
    assert store.due(0) == ()
    assert store.admit(RecordOnce("next"), 0, expected_tick=0).seq == 1


def test_failed_queue_insertion_rolls_back_binding_and_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore[World]({}, 1)
    existing = RecordOnce("existing")
    store.admit(existing, 0, expected_tick=0)
    failing = RecordOnce("failing")
    add_active = cast(
        Callable[[object, BaseAction[World]], None],
        vars(memory_module)["_add_active"],
    )

    def fail_after_add(state: object, action: BaseAction[World]) -> None:
        add_active(state, action)
        raise RuntimeError("queue insertion failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(memory_module, "_add_active", fail_after_add)
        with pytest.raises(RuntimeError, match="queue insertion failed"):
            store.admit(failing, 1, expected_tick=0)

    assert failing.binding == Unbound()
    assert store.due(1) == (existing,)
    assert store.admit(RecordOnce("next"), 1, expected_tick=0).seq == 2


def test_binding_cannot_reenter_admission_and_reuse_its_sequence() -> None:
    store = MemoryStore[World]({}, 1)
    outer = AdmitDuringBinding(store)

    receipt = store.admit(outer, 0, expected_tick=0)

    assert isinstance(outer.nested_error, RuntimeError)
    assert store.due(0) == (outer,)
    assert receipt.seq == 1
    assert store.admit(RecordOnce("next"), 0, expected_tick=0).seq == 2


def test_submit_called_during_action_processing_is_rejected_without_deadlock() -> None:
    action = AttemptReentry()
    store = MemoryStore[World]({}, 1, initial_actions=((action, 0),))
    simulation = Simulation(store)
    action.simulation = simulation

    simulation.process_tick()

    assert isinstance(action.submission_error, RuntimeError)
    assert "inside another store transaction" in str(action.submission_error)
    assert store.due(1) == ()
    assert simulation.submit(RecordOnce("next"), 1, expected_tick=1).seq == 2


def test_tick_commit_wins_over_waiting_submission() -> None:
    store = MemoryStore[World]({}, 1)
    action = RecordOnce("late")
    started = Event()
    finished = Event()
    errors: list[BaseException] = []

    def submit_in_thread() -> None:
        started.set()
        try:
            store.admit(action, 0, expected_tick=0)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    worker = Thread(target=submit_in_thread)
    with store.tick_transaction(0):
        worker.start()
        assert started.wait(1)
        assert not finished.wait(0.05)
    worker.join(1)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], StaleSubmissionError)
    assert action.binding == Unbound()


def test_tick_rollback_allows_waiting_submission_at_same_boundary() -> None:
    store = MemoryStore[World]({}, 1)
    action = RecordOnce("after rollback")
    started = Event()
    result: list[ActionAdmission] = []

    def submit_in_thread() -> None:
        started.set()
        result.append(store.admit(action, 0, expected_tick=0))

    worker = Thread(target=submit_in_thread)

    def fail_tick() -> None:
        with store.tick_transaction(0):
            worker.start()
            assert started.wait(1)
            raise RuntimeError("tick failed")

    with pytest.raises(RuntimeError, match="tick failed"):
        fail_tick()
    worker.join(1)

    assert not worker.is_alive()
    assert result == [ActionAdmission(1, 0)]
    assert store.due(0) == (action,)
    assert store.current_tick() == 0


def test_concurrent_submissions_receive_distinct_sequences() -> None:
    store = MemoryStore[World]({}, 1)
    barrier = Barrier(3)
    receipts: list[ActionAdmission] = []
    actions = [RecordOnce("first"), RecordOnce("second")]

    def submit_in_thread(action: RecordOnce) -> None:
        barrier.wait()
        receipts.append(store.admit(action, 0, expected_tick=0))

    workers = [Thread(target=submit_in_thread, args=(action,)) for action in actions]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(1)

    assert all(not worker.is_alive() for worker in workers)
    assert {receipt.seq for receipt in receipts} == {1, 2}
    assert {action.seq for action in actions} == {1, 2}
    assert set(store.due(0)) == set(actions)


def test_concurrent_submission_of_same_object_admits_it_only_once() -> None:
    store = MemoryStore[World]({}, 1)
    action = RecordOnce("shared")
    barrier = Barrier(3)
    receipts: list[ActionAdmission] = []
    errors: list[BaseException] = []

    def submit_in_thread() -> None:
        barrier.wait()
        try:
            receipts.append(store.admit(action, 0, expected_tick=0))
        except BaseException as error:
            errors.append(error)

    workers = [Thread(target=submit_in_thread) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(1)

    assert all(not worker.is_alive() for worker in workers)
    assert receipts == [ActionAdmission(1, 0)]
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert store.due(0) == (action,)
    assert store.admit(RecordOnce("next"), 0, expected_tick=0).seq == 2


def test_same_ordered_external_inputs_replay_same_history() -> None:
    def replay() -> tuple[tuple[int, ...], tuple[tuple[int, str], ...]]:
        store = MemoryStore[World]({}, 12345)
        simulation = Simulation(store)
        first = RollOnce("first")
        second = RollOnce("second")
        simulation.submit(first, 0, expected_tick=0)
        simulation.submit(second, 0, expected_tick=0)
        simulation.process_tick()
        return (first.seq, second.seq), store.logs

    assert replay() == replay()
    seqs, logs = replay()
    assert seqs == (1, 2)
    assert len(logs) == 2


def test_event_memory_store_accepts_ordinary_external_action() -> None:
    store = EventMemoryStore[World]({}, 1)
    simulation = Simulation(store, events=Events(store))
    action = RecordOnce("ordinary")

    receipt = simulation.submit(action, 0, expected_tick=0)
    simulation.process_tick()

    assert receipt == ActionAdmission(1, 0)
    assert store.logs == ((0, "ordinary"),)


def test_event_store_enter_failure_does_not_leave_tick_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EventMemoryStore[World]({}, 1)

    def fail_snapshot(self: EventMemoryStore[World]) -> object:
        del self
        raise RuntimeError("snapshot failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(EventMemoryStore, "event_snapshot", fail_snapshot)
        with pytest.raises(RuntimeError, match="snapshot failed"), store.tick_transaction(0):
            pass

    action = RecordOnce("after failure")
    assert store.admit(action, 0, expected_tick=0) == ActionAdmission(1, 0)
    assert store.event_snapshot() == {}


def test_event_store_admission_waits_for_event_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EventMemoryStore[World]({}, 1)
    entered_publish = Event()
    release_publish = Event()
    admission_finished = Event()
    receipts: list[ActionAdmission] = []

    def pause_publication(
        self: EventMemoryStore[World],
        events: object,
        children: object,
        /,
    ) -> None:
        del self, events, children
        entered_publish.set()
        if not release_publish.wait(1):
            raise RuntimeError("publication was not released")

    def submit_after_tick() -> None:
        try:
            receipts.append(store.admit(RecordOnce("later"), 1, expected_tick=1))
        finally:
            admission_finished.set()

    with monkeypatch.context() as patcher:
        patcher.setattr(EventMemoryStore, "publish_events", pause_publication)
        ticker = Thread(target=Simulation(store, events=Events(store)).process_tick)
        ticker.start()
        assert entered_publish.wait(1)
        submitter = Thread(target=submit_after_tick)
        submitter.start()
        try:
            assert not admission_finished.wait(0.05)
        finally:
            release_publish.set()
        ticker.join(1)
        submitter.join(1)

    assert not ticker.is_alive()
    assert not submitter.is_alive()
    assert receipts == [ActionAdmission(1, 1)]
