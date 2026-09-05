"""Dependency-free Event-capable storage built around `MemoryStore`."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from types import TracebackType

from foliot.actions import BaseAction
from foliot.events._api import (
    BaseEvent,
    EventAction,
    EventId,
    replace_event_children,
)
from foliot.ids import EntityId, SuspensionId, Tick
from foliot.stores import Txn
from foliot.stores.memory import MemoryStore

__all__ = ["EventMemoryStore"]


class EventMemoryStore[W]:
    """Dependency-free in-memory Store with Event capabilities.

    This is the Event-enabled counterpart to `MemoryStore`, intended for tests,
    examples, and temporary simulations. Event changes share the core memory
    transaction and publish only after a clean exit.

    Args:
        world: Mutable game-owned world exposed through each transaction.
        world_seed: Persisted unsigned 128-bit seed.
        current_tick: Next unfinished logical tick.
        initial_actions: Ordered `(action, due_tick)` pairs admitted without a
            setup tick.

    Note:
        Like `MemoryStore`, this adapter cannot generically roll back direct
        mutations of an arbitrary game-owned Python object. Stage mutations as
        effects.
    """

    __slots__ = ("_events", "_store")

    def __init__(
        self,
        world: W,
        world_seed: int,
        *,
        current_tick: Tick = 0,
        initial_actions: Iterable[tuple[BaseAction[W], Tick | None]] = (),
    ) -> None:
        self._store = MemoryStore(
            world,
            world_seed,
            current_tick=current_tick,
            initial_actions=initial_actions,
        )
        self._events: dict[EventId, BaseEvent[W]] = {}

    @property
    def world_seed(self) -> int:
        """Persisted unsigned 128-bit seed for this world."""
        return self._store.world_seed

    @property
    def logs(self) -> tuple[tuple[Tick, str], ...]:
        """Committed deterministic journal entries in insertion order."""
        return self._store.logs

    def current_tick(self) -> Tick:
        """Return the next unfinished logical tick."""
        return self._store.current_tick()

    def due(self, tick: Tick, /) -> tuple[BaseAction[W], ...]:
        """Return bound actions due at or before `tick`, plus recurring work."""
        return self._store.due(tick)

    def event(self, event_id: EventId, /) -> BaseEvent[W] | None:
        """Return the open Event with `event_id`, or `None`."""
        return self._events.get(event_id)

    def event_snapshot(self) -> dict[EventId, BaseEvent[W]]:
        """Return transaction-local Event references for the adapter itself."""
        return self._events.copy()

    def tick_transaction(self, tick: Tick, /) -> AbstractContextManager[Txn[W]]:
        """Open an atomic in-memory boundary for one logical tick."""
        return _EventMemoryTransactionContext(self, self._store.tick_transaction(tick))

    def publish_events(
        self,
        events: dict[EventId, BaseEvent[W]],
        children: dict[EventId, tuple[EventAction[W], ...]],
        /,
    ) -> None:
        for event_id, event in events.items():
            next_children = children[event_id]
            if event.children != next_children:
                replace_event_children(event, next_children)
        self._events = events


class _EventMemoryTxn[W]:
    __slots__ = ("_children", "_events", "_inner")

    def __init__(
        self,
        inner: Txn[W],
        events: dict[EventId, BaseEvent[W]],
    ) -> None:
        self._inner = inner
        self._events = events.copy()
        self._children = {event_id: event.children for event_id, event in self._events.items()}

    @property
    def world(self) -> W:
        return self._inner.world

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        self._inner.schedule(action, due_tick)

    def delete(self, action: BaseAction[W], /) -> None:
        self._inner.delete(action)

    def delete_owned_by(self, entity_id: EntityId, /) -> None:
        self._inner.delete_owned_by(entity_id)

    def suspend(self, entity_id: EntityId, by: SuspensionId, /) -> None:
        self._inner.suspend(entity_id, by)

    def resume(self, by: SuspensionId, /) -> None:
        self._inner.resume(by)

    def log(self, tick: Tick, line: str, /) -> None:
        self._inner.log(tick, line)

    def event_open(self, event: BaseEvent[W], due_tick: Tick, /) -> None:
        if event.event_id in self._events:
            raise ValueError(f"Event id already exists: {event.event_id}")
        for child in event.children:
            self._inner.schedule(child, due_tick)
        self._events[event.event_id] = event
        self._children[event.event_id] = event.children

    def event_continue(
        self,
        event: BaseEvent[W],
        children: tuple[EventAction[W], ...],
        due_tick: Tick,
        /,
    ) -> None:
        stored = self._events.get(event.event_id)
        if stored is not event:
            raise RuntimeError("the Event does not belong to this store")
        current_children = self._children[event.event_id]
        for child in current_children:
            self._inner.delete(child)
        for child in children:
            self._inner.schedule(child, due_tick)
        self._children[event.event_id] = children

    def event_end(self, event_id: EventId, /) -> None:
        event = self._events.get(event_id)
        if event is None:
            raise RuntimeError(f"Event does not exist: {event_id}")
        for child in self._children[event_id]:
            self._inner.delete(child)
        self._inner.resume(SuspensionId(str(event_id)))
        del self._children[event_id]
        del self._events[event_id]

    @property
    def projected_events(self) -> dict[EventId, BaseEvent[W]]:
        return self._events

    @property
    def projected_children(self) -> dict[EventId, tuple[EventAction[W], ...]]:
        return self._children


class _EventMemoryTransactionContext[W]:
    __slots__ = ("_entered", "_inner_context", "_store", "_txn")

    def __init__(
        self,
        store: EventMemoryStore[W],
        inner_context: AbstractContextManager[Txn[W]],
    ) -> None:
        self._store = store
        self._inner_context = inner_context
        self._txn: _EventMemoryTxn[W] | None = None
        self._entered = False

    def __enter__(self) -> _EventMemoryTxn[W]:
        if self._entered:
            raise RuntimeError("an Event memory transaction cannot be entered twice")
        self._entered = True
        self._txn = _EventMemoryTxn(
            self._inner_context.__enter__(),
            self._store.event_snapshot(),
        )
        return self._txn

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        txn = self._txn
        if txn is None:
            raise RuntimeError("the Event memory transaction was not entered")

        handled = self._inner_context.__exit__(exc_type, exc_value, traceback)
        if exc_type is None:
            self._store.publish_events(txn.projected_events, txn.projected_children)
        return handled is True
