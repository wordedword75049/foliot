"""Optional Event layer: simultaneous intents resolved as one interaction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from hashlib import blake2b
from typing import Literal, NewType, Protocol, cast, final, override, runtime_checkable

from foliot._event_bridge import EventConfigurationError
from foliot.actions import BaseAction, Bound, Unbound
from foliot.context import FinalizationContext, TickContext
from foliot.effects import Effect
from foliot.ids import EntityId, Tick
from foliot.rng import Rng, event_resolution_rng
from foliot.stores import Store, Txn

EventId = NewType("EventId", str)
"""Stable identity of one persisted Event."""

__all__ = [
    "BaseEvent",
    "DecisionContext",
    "EntityIdTemplate",
    "EventAction",
    "EventConfigurationError",
    "EventId",
    "EventIdTemplate",
    "EventStore",
    "EventTxn",
    "Events",
    "IntentRecord",
    "Outcome",
    "ResolutionContext",
    "end_event",
    "open_event",
]

_ID_PERSONALIZATION = b"foliot-id-v1"


class DecisionContext(Protocol):
    """Read-only capabilities available while choosing one participant Intent."""

    @property
    def tick(self) -> Tick:
        """Logical tick shared by every participant in this Event attempt."""
        ...

    @property
    def rng(self) -> Rng:
        """Deterministic stream belonging to this participant action."""
        ...


class ResolutionContext(Protocol):
    """Read-only capabilities available to one Event resolver."""

    @property
    def tick(self) -> Tick:
        """Logical tick in which the complete Intent set was produced."""
        ...

    @property
    def rng(self) -> Rng:
        """Deterministic stream isolated from all participant streams."""
        ...


@dataclass(frozen=True, slots=True)
class _ReadContext:
    tick: Tick
    rng: Rng


@dataclass(frozen=True, slots=True)
class IntentRecord:
    """One game Intent with routing metadata attached by foliot.

    Attributes:
        event_id: Event receiving the Intent.
        source_seq: Permanent sequence number of the producing EventAction.
        entity_id: Entity that made the decision.
        intent: Opaque game-defined Intent object.
    """

    event_id: EventId
    source_seq: int
    entity_id: EntityId
    intent: object


class EventAction[W](BaseAction[W], ABC):
    """One participant's one-shot decision for one Event round.

    Args:
        entity_id: Entity making the decision.
        event_id: Existing Event that expects this child action.

    Event actions are non-suspendable. Do not override `process`; implement
    `decide` and return exactly one non-`None` game Intent.
    """

    __slots__ = ("_event_id",)

    def __init__(self, entity_id: EntityId, event_id: EventId) -> None:
        super().__init__(entity_id, suspendable=False)
        self._event_id = event_id

    @property
    def event_id(self) -> EventId:
        """Return the Event that owns this round action."""
        return self._event_id

    @final
    @override
    def process(self, ctx: TickContext[W], /) -> None:
        intent = self.decide(_ReadContext(ctx.tick, ctx.rng))
        if intent is None:
            raise RuntimeError("an EventAction must return one Intent, not None")
        _record_intent(
            ctx,
            IntentRecord(
                event_id=self._event_id,
                source_seq=self.seq,
                entity_id=self.entity_id,
                intent=intent,
            ),
        )

    @abstractmethod
    def decide(self, ctx: DecisionContext, /) -> object:
        """Return exactly one game-defined Intent for the current round.

        Args:
            ctx: Tick and participant-specific deterministic RNG.

        Returns:
            Any non-`None` game-defined Intent object.
        """
        ...


class BaseEvent[W](ABC):
    """Base class for one persisted simultaneous interaction.

    Args:
        event_id: Stable identity chosen before the Event opens.
        children: Complete non-empty set of fresh EventActions expected in the
            current round.

    Raises:
        ValueError: If children are empty, repeat one object, or carry a
            different Event id.
    """

    __slots__ = ("_children", "_event_id")

    def __init__(self, event_id: EventId, children: Iterable[EventAction[W]], /) -> None:
        children_tuple = tuple(children)
        _validate_children(event_id, children_tuple)
        self._event_id = event_id
        self._children = children_tuple

    @property
    def event_id(self) -> EventId:
        """Return this Event's stable identity."""
        return self._event_id

    @property
    def children(self) -> tuple[EventAction[W], ...]:
        """Return the exact EventActions expected for the current round."""
        return self._children

    @abstractmethod
    def resolve(
        self,
        ctx: ResolutionContext,
        intents: tuple[IntentRecord, ...],
        /,
    ) -> Outcome[W]:
        """Describe the result of one complete simultaneous round.

        Args:
            ctx: Tick and Event-specific deterministic RNG.
            intents: Records ordered to match `children`.

        Returns:
            An explicit continuing or ending Outcome.
        """
        ...


@dataclass(frozen=True, slots=True)
class _Continue[W]:
    children: tuple[EventAction[W], ...]
    due_tick: Tick
    status: Literal["continue"] = field(default="continue", init=False)


@dataclass(frozen=True, slots=True)
class _End:
    status: Literal["end"] = field(default="end", init=False)


type _Lifecycle[W] = _Continue[W] | _End


@dataclass(frozen=True, slots=True)
class Outcome[W]:
    """Pure work description returned by a successful Event resolution.

    Construct Outcomes through `continue_with` or `end` rather than invoking
    the dataclass constructor directly.

    Attributes:
        effects: World mutations applied during this tick.
        schedules: Additional ordinary actions to schedule.
        deletes: Existing actions to remove.
        lines: Deterministic narrative lines.
    """

    effects: tuple[Effect[W], ...]
    schedules: tuple[tuple[BaseAction[W], Tick | None], ...]
    deletes: tuple[BaseAction[W], ...]
    lines: tuple[str, ...]
    _lifecycle: _Lifecycle[W]

    @staticmethod
    def continue_with[X](
        *children: EventAction[X],
        due_tick: Tick,
        effects: Iterable[Effect[X]] = (),
        schedules: Iterable[tuple[BaseAction[X], Tick | None]] = (),
        deletes: Iterable[BaseAction[X]] = (),
        log: Iterable[str] = (),
    ) -> Outcome[X]:
        """Continue with fresh participant actions at one shared deadline.

        Args:
            *children: Non-empty set of fresh, unbound EventActions.
            due_tick: Strictly future tick shared by all next-round children.
            effects: Game-defined mutations for the current round.
            schedules: Additional `(action, due_tick)` requests.
            deletes: Existing actions to remove.
            log: Narrative lines to append in order.

        Returns:
            An immutable continuing Outcome.

        Raises:
            TypeError: If a deadline has an invalid runtime type.
            ValueError: If the child set or schedules are structurally invalid.
        """
        _validate_tick(due_tick, name="due_tick")
        children_tuple = tuple(children)
        if not children_tuple:
            raise ValueError("a continuing Outcome needs at least one next child")
        _validate_distinct_actions(children_tuple, name="next child")
        schedules_tuple = tuple(schedules)
        _validate_schedule_shapes(schedules_tuple)
        _validate_distinct_schedule_targets(children_tuple, schedules_tuple)
        return Outcome[X](
            effects=tuple(effects),
            schedules=schedules_tuple,
            deletes=tuple(deletes),
            lines=_lines(log),
            _lifecycle=_Continue(children_tuple, due_tick),
        )

    @staticmethod
    def end[X](
        *,
        effects: Iterable[Effect[X]] = (),
        schedules: Iterable[tuple[BaseAction[X], Tick | None]] = (),
        deletes: Iterable[BaseAction[X]] = (),
        log: Iterable[str] = (),
    ) -> Outcome[X]:
        """End the Event after applying the described work.

        Args:
            effects: Game-defined mutations for the final round.
            schedules: Additional `(action, due_tick)` requests.
            deletes: Existing actions to remove.
            log: Narrative lines to append in order.

        Returns:
            An immutable ending Outcome.
        """
        schedules_tuple = tuple(schedules)
        _validate_schedule_shapes(schedules_tuple)
        _validate_distinct_schedule_targets((), schedules_tuple)
        return Outcome[X](
            effects=tuple(effects),
            schedules=schedules_tuple,
            deletes=tuple(deletes),
            lines=_lines(log),
            _lifecycle=_End(),
        )

    @property
    def is_ending(self) -> bool:
        match self._lifecycle:
            case _Continue():
                return False
            case _End():
                return True

    @property
    def next_children(self) -> tuple[EventAction[W], ...]:
        match self._lifecycle:
            case _Continue(children=children):
                return children
            case _End():
                return ()

    @property
    def next_due_tick(self) -> Tick | None:
        match self._lifecycle:
            case _Continue(due_tick=due_tick):
                return due_tick
            case _End():
                return None

    @property
    def scheduled_actions(self) -> tuple[BaseAction[W], ...]:
        return (*self.next_children, *(action for action, _ in self.schedules))


class EventIdTemplate:
    """Stable game-declared namespace for Event identifiers.

    Args:
        namespace: Non-empty, version-stable application namespace such as
            `"mygame.fight"`.
    """

    __slots__ = ("_namespace",)

    def __init__(self, namespace: str, /) -> None:
        self._namespace = _validate_namespace(namespace)

    @property
    def namespace(self) -> str:
        """The stable application namespace used by this template."""
        return self._namespace

    def from_action[W](
        self,
        action: BaseAction[W],
        /,
        *,
        tick: Tick,
        ordinal: int,
    ) -> EventId:
        """Derive an Event id from a bound source action.

        Args:
            action: Bound action that caused the Event.
            tick: Tick in which it caused the Event.
            ordinal: Zero-based discriminator when one occurrence creates more
                than one Event in this namespace.

        Raises:
            RuntimeError: If `action` is unbound.
            TypeError: If tick or ordinal has the wrong runtime type.
            ValueError: If tick or ordinal is negative.
        """
        _validate_tick(tick, name="tick")
        _validate_ordinal(ordinal)
        value = _stable_id(
            b"event-from-action",
            self._namespace.encode("utf-8"),
            str(action.seq).encode("ascii"),
            str(tick).encode("ascii"),
            str(ordinal).encode("ascii"),
        )
        return EventId(f"event-v1:{value}")


class EntityIdTemplate:
    """Stable game-declared namespace for Event-owned entity identifiers.

    Args:
        namespace: Non-empty, version-stable application namespace such as
            `"mygame.wolf"`.
    """

    __slots__ = ("_namespace",)

    def __init__(self, namespace: str, /) -> None:
        self._namespace = _validate_namespace(namespace)

    @property
    def namespace(self) -> str:
        """The stable application namespace used by this template."""
        return self._namespace

    def from_event(self, event_id: EventId, /, *, ordinal: int) -> EntityId:
        """Derive one temporary entity id from its owning Event.

        Args:
            event_id: Stable identity of the owning Event.
            ordinal: Zero-based discriminator for multiple owned entities.
        """
        _validate_ordinal(ordinal)
        value = _stable_id(
            b"entity-from-event",
            self._namespace.encode("utf-8"),
            str(event_id).encode("utf-8"),
            str(ordinal).encode("ascii"),
        )
        return EntityId(f"entity-v1:{value}")


class EventStore[W](Store[W], Protocol):
    """Core Store plus lookup of one persisted Event."""

    def event(self, event_id: EventId, /) -> BaseEvent[W] | None:
        """Return an open Event by id, or `None` when it is absent."""
        ...


@runtime_checkable
class EventTxn[W](Txn[W], Protocol):
    """Core transaction plus Event writes on the same physical commit."""

    def event_open(self, event: BaseEvent[W], due_tick: Tick, /) -> None:
        """Persist a new Event and schedule its initial child actions."""
        ...

    def event_continue(
        self,
        event: BaseEvent[W],
        children: tuple[EventAction[W], ...],
        due_tick: Tick,
        /,
    ) -> None:
        """Replace an Event's children and schedule its next resolution."""
        ...

    def event_end(self, event_id: EventId, /) -> None:
        """End an Event, remove its children, and resume its participants."""
        ...


class Events[W]:
    """Explicit opt-in that connects Event behavior to one `Simulation`.

    Args:
        store: Event-capable store also passed directly to `Simulation`.
    """

    __slots__ = ("_store",)

    def __init__(self, store: EventStore[W], /) -> None:
        self._store = store

    @property
    def store(self) -> EventStore[W]:
        """Event-capable store attached to this collaborator."""
        return self._store

    def event(self, event_id: EventId, /) -> BaseEvent[W] | None:
        """Return an open Event by id, or `None` when it is absent."""
        return self._store.event(event_id)

    def resolution_context(
        self,
        world_seed: int,
        event_id: EventId,
        tick: Tick,
        /,
    ) -> ResolutionContext:
        """Build the deterministic read context used to resolve an Event."""
        return _ReadContext(tick, event_resolution_rng(world_seed, str(event_id), tick))

    def validate_open(self, event: BaseEvent[W], due_tick: Tick, tick: Tick, /) -> None:
        """Validate an Event before any opening writes are staged."""
        if due_tick <= tick:
            raise ValueError("an Event's due_tick must be later than the current tick")
        if self.event(event.event_id) is not None:
            raise ValueError(f"Event id already exists: {event.event_id}")
        for child in event.children:
            match child.binding:
                case Unbound():
                    pass
                case Bound():
                    raise RuntimeError("an Event must open with unbound child actions")

    def validate_outcome(
        self,
        event: BaseEvent[W],
        outcome: Outcome[W],
        tick: Tick,
        /,
    ) -> None:
        """Validate a resolver's outcome before staging its writes."""
        for _, due_tick in outcome.schedules:
            if due_tick is not None and due_tick <= tick:
                raise ValueError("an Outcome schedule must target a future tick")
        if outcome.is_ending:
            return
        due_tick = outcome.next_due_tick
        if due_tick is None or due_tick <= tick:
            raise ValueError("a continuing Outcome must target a future tick")
        _validate_children(event.event_id, outcome.next_children)
        for child in outcome.next_children:
            match child.binding:
                case Unbound():
                    pass
                case Bound():
                    raise RuntimeError("a continuing Outcome needs fresh unbound children")

    def uses_store(self, store: object, /) -> bool:
        """Whether this collaborator is attached to the supplied core store."""
        return self._store is store

    def open(self, txn: Txn[W], event: BaseEvent[W], due_tick: Tick, /) -> None:
        """Stage a validated Event opening in the active transaction."""
        _event_txn(txn).event_open(event, due_tick)

    def continue_event(
        self,
        txn: Txn[W],
        event: BaseEvent[W],
        outcome: Outcome[W],
        /,
    ) -> None:
        """Stage a continuing Event's fresh children and next due tick."""
        due_tick = outcome.next_due_tick
        if due_tick is None:
            raise RuntimeError("an ending Outcome cannot continue an Event")
        _event_txn(txn).event_continue(event, outcome.next_children, due_tick)

    def end(self, txn: Txn[W], event_id: EventId, /) -> None:
        """Stage the explicit end of an Event."""
        _event_txn(txn).event_end(event_id)


def open_event[W](ctx: TickContext[W], event: BaseEvent[W], due_tick: Tick, /) -> None:
    """Stage atomic admission of an Event and all of its children.

    Args:
        ctx: Current ordinary action context.
        event: Concrete Event with a stable id and complete child set.
        due_tick: Strictly future tick shared by every opening child.

    Raises:
        EventConfigurationError: If the Simulation has no Event collaborator.
        TypeError: If `due_tick` is not an integer.
        ValueError: If `due_tick` is not in the future.
    """
    sink = getattr(ctx, "_foliot_open_event", None)
    if not callable(sink):
        raise EventConfigurationError(
            "open_event() needs Simulation(..., events=Events(event_store))"
        )
    callback = cast(Callable[[BaseEvent[W], Tick], None], sink)
    callback(event, due_tick)


def end_event[W](ctx: FinalizationContext[W], event_id: EventId, /) -> None:
    """Stage Event closure from post-effect game finalization.

    Closing removes the Event and its current or newly staged children, then
    resumes actions suspended by that Event id.

    Raises:
        EventConfigurationError: If the Simulation has no Event collaborator.
    """
    sink = getattr(ctx, "_foliot_end_event", None)
    if not callable(sink):
        raise EventConfigurationError(
            "end_event() needs Simulation(..., events=Events(event_store))"
        )
    callback = cast(Callable[[EventId], None], sink)
    callback(event_id)


def _record_intent[W](ctx: TickContext[W], record: IntentRecord, /) -> None:
    """Internal bridge used by the final `EventAction.process` implementation."""
    sink = getattr(ctx, "_foliot_record_intent", None)
    if not callable(sink):
        raise EventConfigurationError(
            "EventAction needs Simulation(..., events=Events(event_store))"
        )
    callback = cast(Callable[[IntentRecord], None], sink)
    callback(record)


def replace_event_children[W](event: BaseEvent[W], children: tuple[EventAction[W], ...], /) -> None:
    """Publish a committed child replacement in the in-memory adapter."""
    _validate_children(event.event_id, children)
    event._children = children  # pyright: ignore[reportPrivateUsage] -- same-module lifecycle


def _event_txn[W](txn: Txn[W]) -> EventTxn[W]:
    if not isinstance(txn, EventTxn):
        raise EventConfigurationError(
            "the tick transaction does not implement the EventTxn capability"
        )
    return txn


def _validate_children[W](
    event_id: EventId,
    children: tuple[EventAction[W], ...],
) -> None:
    if not children:
        raise ValueError("an Event needs at least one child EventAction")
    _validate_distinct_actions(children, name="Event child")
    for child in children:
        if child.event_id != event_id:
            raise ValueError("every child EventAction must carry its Event's id")


def _validate_distinct_actions[W](actions: tuple[BaseAction[W], ...], *, name: str) -> None:
    seen: set[int] = set()
    for action in actions:
        action_key = id(action)
        if action_key in seen:
            raise ValueError(f"the same {name} object cannot appear twice")
        seen.add(action_key)


def _validate_distinct_schedule_targets[W](
    children: tuple[EventAction[W], ...],
    schedules: tuple[tuple[BaseAction[W], Tick | None], ...],
) -> None:
    actions: tuple[BaseAction[W], ...] = (*children, *(action for action, _ in schedules))
    _validate_distinct_actions(actions, name="scheduled action")


def _validate_schedule_shapes[W](
    schedules: tuple[tuple[BaseAction[W], Tick | None], ...],
) -> None:
    for _, due_tick in schedules:
        if due_tick is not None:
            _validate_tick(due_tick, name="scheduled due_tick")


def _lines(lines: Iterable[str]) -> tuple[str, ...]:
    return tuple(lines)


def _validate_namespace(namespace: object) -> str:
    if not isinstance(namespace, str):
        raise TypeError("namespace must be a str")
    if not namespace:
        raise ValueError("namespace must not be empty")
    return namespace


def _validate_tick(tick: object, *, name: str) -> None:
    if isinstance(tick, bool) or not isinstance(tick, int):
        raise TypeError(f"{name} must be an int, not bool")
    if tick < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_ordinal(ordinal: object) -> None:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise TypeError("ordinal must be an int, not bool")
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")


def _stable_id(kind: bytes, *parts: bytes) -> str:
    digest = blake2b(digest_size=16, person=_ID_PERSONALIZATION)
    for part in (kind, *parts):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()
