"""Dependency-free reference storage for tests and quickstarts (§8, M4).

`MemoryStore` implements the same `Store` / `Txn` behavior a consumer-owned
durable adapter must provide, but keeps the actual game action objects in
Python memory. It is intentionally single-runner and disappears with the
process; it is a reference and testing bonus, not a production database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType

from foliot.actions import (
    Active,
    BaseAction,
    Bound,
    Suspended,
    Unbound,
    restore_action_binding,
)
from foliot.ids import EntityId, SuspensionId, Tick

__all__ = ["MemoryStore"]

_UINT128_SIZE = 1 << 128


@dataclass(frozen=True, slots=True)
class _Schedule[W]:
    action: BaseAction[W]
    due_tick: Tick | None
    new_seq: int | None


@dataclass(frozen=True, slots=True)
class _Delete[W]:
    action: BaseAction[W]


@dataclass(frozen=True, slots=True)
class _Suspend:
    entity_id: EntityId
    by: SuspensionId


@dataclass(frozen=True, slots=True)
class _Resume:
    by: SuspensionId


@dataclass(frozen=True, slots=True)
class _Log:
    tick: Tick
    line: str


type _Command[W] = _Schedule[W] | _Delete[W] | _Suspend | _Resume | _Log


@dataclass(slots=True)
class _MemoryState[W]:
    world: W
    world_seed: int
    current_tick: Tick
    next_seq: int = 1
    actions: dict[int, BaseAction[W]] = field(default_factory=dict)
    scheduled: dict[Tick, dict[int, BaseAction[W]]] = field(default_factory=dict)
    recurring: dict[int, BaseAction[W]] = field(default_factory=dict)
    logs: list[tuple[Tick, str]] = field(default_factory=list)
    transaction_open: bool = False


class MemoryStore[W]:
    """A single-runner, in-memory implementation of `Store[W]`.

    Actions remain their original game subclass objects; no codec, registry,
    or serialization is involved. Tick transactions stage foliot-owned state
    and publish it only on a clean exit. The supplied game-owned `world` is
    exposed directly through `Txn.world` and cannot be generically rolled back.
    """

    __slots__ = ("_state",)

    def __init__(
        self,
        world: W,
        world_seed: int,
        *,
        current_tick: Tick = 0,
    ) -> None:
        _validate_world_seed(world_seed)
        _validate_tick(current_tick, name="current_tick")
        self._state = _MemoryState(world, world_seed, current_tick)

    @property
    def world_seed(self) -> int:
        return self._state.world_seed

    @property
    def logs(self) -> tuple[tuple[Tick, str], ...]:
        """Committed journal lines in insertion order."""
        return tuple(self._state.logs)

    def current_tick(self) -> Tick:
        return self._state.current_tick

    def due(self, tick: Tick, /) -> tuple[BaseAction[W], ...]:
        """Return a stable snapshot of active recurring and due actions."""
        _validate_tick(tick, name="tick")
        actions = list(self._state.recurring.values())
        for due_tick, bucket in self._state.scheduled.items():
            if due_tick <= tick:
                actions.extend(bucket.values())
        actions.sort(key=lambda action: action.seq)
        return tuple(actions)

    def tick_transaction(self, tick: Tick, /) -> _MemoryTransactionContext[W]:
        """Open the one transaction allowed for the store's current tick."""
        _validate_tick(tick, name="tick")
        return _MemoryTransactionContext(self._state, tick)


class _MemoryTxn[W]:
    __slots__ = (
        "_closed",
        "_commands",
        "_known_actions",
        "_next_seq",
        "_state",
        "_tick",
        "_touched_actions",
    )

    def __init__(self, state: _MemoryState[W], tick: Tick) -> None:
        self._state = state
        self._tick = tick
        self._next_seq = state.next_seq
        self._commands: list[_Command[W]] = []
        self._known_actions = {id(action) for action in state.actions.values()}
        self._touched_actions: dict[int, BaseAction[W]] = {}
        self._closed = False

    @property
    def world(self) -> W:
        self._ensure_open()
        return self._state.world

    @property
    def tick(self) -> Tick:
        return self._tick

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @property
    def commands(self) -> tuple[_Command[W], ...]:
        return tuple(self._commands)

    @property
    def touched_actions(self) -> tuple[BaseAction[W], ...]:
        return tuple(self._touched_actions.values())

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        self._ensure_open()
        if due_tick is not None:
            _validate_tick(due_tick, name="due_tick")
            if due_tick <= self._tick:
                raise ValueError("due_tick must be later than the transaction tick")

        action_key = id(action)
        new_seq: int | None = None
        if action_key not in self._known_actions:
            match action.binding:
                case Unbound():
                    new_seq = self._next_seq
                    self._next_seq += 1
                    self._known_actions.add(action_key)
                case Bound():
                    raise RuntimeError("a bound action does not belong to this store")

        self._touched_actions[action_key] = action
        self._commands.append(_Schedule(action, due_tick, new_seq))

    def delete(self, action: BaseAction[W], /) -> None:
        self._ensure_open()
        action_key = id(action)
        if action_key not in self._known_actions:
            raise RuntimeError("the action does not belong to this store")
        self._touched_actions[action_key] = action
        self._commands.append(_Delete(action))

    def suspend(self, entity_id: EntityId, by: SuspensionId, /) -> None:
        self._ensure_open()
        self._commands.append(_Suspend(entity_id, by))

    def resume(self, by: SuspensionId, /) -> None:
        self._ensure_open()
        self._commands.append(_Resume(by))

    def log(self, tick: Tick, line: str, /) -> None:
        self._ensure_open()
        if tick != self._tick:
            raise ValueError("a log entry must use the transaction tick")
        self._commands.append(_Log(tick, line))

    def commit(self) -> None:
        self._ensure_open()
        _commit(self._state, self)

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("the memory transaction is closed")


class _MemoryTransactionContext[W]:
    __slots__ = ("_entered", "_state", "_tick", "_txn")

    def __init__(self, state: _MemoryState[W], tick: Tick) -> None:
        self._state = state
        self._tick = tick
        self._txn: _MemoryTxn[W] | None = None
        self._entered = False

    def __enter__(self) -> _MemoryTxn[W]:
        if self._entered:
            raise RuntimeError("a memory transaction context cannot be entered twice")
        if self._state.transaction_open:
            raise RuntimeError("MemoryStore allows only one open transaction")
        if self._tick != self._state.current_tick:
            raise ValueError(
                f"transaction tick {self._tick} does not match "
                f"current tick {self._state.current_tick}"
            )

        self._entered = True
        self._state.transaction_open = True
        self._txn = _MemoryTxn(self._state, self._tick)
        return self._txn

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_value, traceback
        txn = self._txn
        if txn is None:
            raise RuntimeError("the memory transaction context was not entered")

        try:
            if exc_type is None:
                txn.commit()
        finally:
            txn.close()
            self._state.transaction_open = False
        return False


def _commit[W](state: _MemoryState[W], txn: _MemoryTxn[W]) -> None:
    actions_before = state.actions.copy()
    scheduled_before = {due_tick: bucket.copy() for due_tick, bucket in state.scheduled.items()}
    recurring_before = state.recurring.copy()
    logs_before = state.logs.copy()
    bindings_before = {
        id(action): (action, action.binding)
        for action in (*state.actions.values(), *txn.touched_actions)
    }
    next_seq_before = state.next_seq
    tick_before = state.current_tick

    try:
        for command in txn.commands:
            match command:
                case _Schedule(action=action, due_tick=due_tick, new_seq=new_seq):
                    _apply_schedule(state, action, due_tick, new_seq)
                case _Delete(action=action):
                    _apply_delete(state, action)
                case _Suspend(entity_id=entity_id, by=by):
                    _apply_suspend(state, entity_id, by, txn.tick)
                case _Resume(by=by):
                    _apply_resume(state, by, txn.tick)
                case _Log(tick=tick, line=line):
                    state.logs.append((tick, line))

        state.next_seq = txn.next_seq
        state.current_tick = txn.tick + 1
    except BaseException:
        state.actions = actions_before
        state.scheduled = scheduled_before
        state.recurring = recurring_before
        state.logs = logs_before
        state.next_seq = next_seq_before
        state.current_tick = tick_before
        for action, binding in bindings_before.values():
            restore_action_binding(action, binding)
        raise


def _apply_schedule[W](
    state: _MemoryState[W],
    action: BaseAction[W],
    due_tick: Tick | None,
    new_seq: int | None,
) -> None:
    match action.binding:
        case Unbound():
            if new_seq is None:
                raise RuntimeError("a new action has no reserved seq")
            if new_seq in state.actions:
                raise RuntimeError(f"seq {new_seq} already belongs to another action")
            action.bind(new_seq, Active(due_tick))
        case Bound(seq=seq, state=action_state):
            existing = state.actions.get(seq)
            if existing is not None and existing is not action:
                raise RuntimeError(f"seq {seq} already belongs to another action")
            match action_state:
                case Active():
                    if existing is action:
                        _remove_active(state, action)
                    action.reschedule(due_tick)
                case Suspended():
                    raise RuntimeError("a suspended action cannot be scheduled")

    state.actions[action.seq] = action
    _add_active(state, action)


def _apply_delete[W](state: _MemoryState[W], action: BaseAction[W]) -> None:
    match action.binding:
        case Unbound():
            raise RuntimeError("an unbound action does not belong to the store")
        case Bound(seq=seq, state=action_state):
            existing = state.actions.get(seq)
            if existing is not action:
                raise RuntimeError("the action does not belong to this store")
            match action_state:
                case Active():
                    _remove_active(state, action)
                case Suspended():
                    pass
            del state.actions[seq]


def _apply_suspend[W](
    state: _MemoryState[W],
    entity_id: EntityId,
    by: SuspensionId,
    tick: Tick,
) -> None:
    for action in tuple(state.actions.values()):
        if action.entity_id != entity_id or not action.suspendable:
            continue
        match action.state:
            case Active():
                _remove_active(state, action)
                action.suspend(tick, by)
            case Suspended():
                pass


def _apply_resume[W](state: _MemoryState[W], by: SuspensionId, tick: Tick) -> None:
    for action in tuple(state.actions.values()):
        match action.state:
            case Suspended(suspended_by=suspended_by) if suspended_by == by:
                action.resume(tick)
                _add_active(state, action)
            case Suspended() | Active():
                pass


def _add_active[W](state: _MemoryState[W], action: BaseAction[W]) -> None:
    match action.state:
        case Active(due_tick=due_tick):
            if due_tick is None:
                state.recurring[action.seq] = action
            else:
                state.scheduled.setdefault(due_tick, {})[action.seq] = action
        case Suspended():
            raise RuntimeError("a suspended action cannot enter the active queue")


def _remove_active[W](state: _MemoryState[W], action: BaseAction[W]) -> None:
    match action.state:
        case Active(due_tick=due_tick):
            if due_tick is None:
                state.recurring.pop(action.seq, None)
            else:
                bucket = state.scheduled.get(due_tick)
                if bucket is None:
                    return
                bucket.pop(action.seq, None)
                if not bucket:
                    del state.scheduled[due_tick]
        case Suspended():
            pass


def _validate_world_seed(world_seed: object) -> None:
    if isinstance(world_seed, bool) or not isinstance(world_seed, int):
        raise TypeError("world_seed must be an int, not bool")
    if not 0 <= world_seed < _UINT128_SIZE:
        raise ValueError("world_seed must satisfy 0 <= world_seed < 2**128")


def _validate_tick(tick: object, *, name: str) -> None:
    if isinstance(tick, bool) or not isinstance(tick, int):
        raise TypeError(f"{name} must be an int, not bool")
    if tick < 0:
        raise ValueError(f"{name} must be non-negative")
