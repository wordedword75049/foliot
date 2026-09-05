"""The deterministic tick loop.

Handlers only describe work in private collecting contexts. The engine asks
every due action first, resolves any complete optional Events, then applies the
valid work and optional finalization inside one store transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from foliot._event_bridge import EventConfigurationError
from foliot.actions import Active, BaseAction, Suspended
from foliot.context import TickFinalizer
from foliot.drivers import Driver
from foliot.effects import Effect
from foliot.ids import EntityId, SuspensionId, Tick
from foliot.rng import Rng, counter_rng
from foliot.stores import Store, Txn

if TYPE_CHECKING:
    from foliot.events import BaseEvent, EventId, Events, IntentRecord, Outcome

__all__ = ["Simulation"]

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Context[W]:
    owner: BaseAction[W]
    _tick: Tick
    _rng: Rng
    _events_enabled: bool
    effects: list[Effect[W]] = field(default_factory=list)
    schedules: list[tuple[BaseAction[W], Tick | None]] = field(default_factory=list)
    suspensions: list[tuple[EntityId, SuspensionId]] = field(default_factory=list)
    event_opens: list[tuple[BaseEvent[W], Tick]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    intent: IntentRecord | None = None
    finished: bool = False

    @property
    def tick(self) -> Tick:
        return self._tick

    @property
    def rng(self) -> Rng:
        return self._rng

    def emit(self, effect: Effect[W], /) -> None:
        self.effects.append(effect)

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        _validate_due_tick(due_tick, self._tick)
        self.schedules.append((action, due_tick))

    def log(self, line: str, /) -> None:
        self.lines.append(line)

    def suspend(
        self,
        entity_id: EntityId,
        /,
        *,
        by: SuspensionId | EventId,
    ) -> None:
        self.suspensions.append((entity_id, SuspensionId(str(by))))

    def finish(self) -> None:
        self.finished = True

    def _foliot_open_event(self, event: BaseEvent[W], due_tick: Tick, /) -> None:
        if not self._events_enabled:
            raise EventConfigurationError(
                "open_event() needs Simulation(..., events=Events(event_store))"
            )
        _validate_due_tick(due_tick, self._tick, recurring=False)
        self.event_opens.append((event, due_tick))

    def _foliot_record_intent(self, intent: IntentRecord, /) -> None:
        if not self._events_enabled:
            raise EventConfigurationError(
                "EventAction needs Simulation(..., events=Events(event_store))"
            )
        if self.intent is not None:
            raise RuntimeError("an EventAction may register only one Intent")
        self.intent = intent

    def reschedules_owner(self) -> bool:
        return any(action is self.owner for action, _ in self.schedules)

    def validate(self) -> None:
        if self.finished and self.reschedules_owner():
            raise RuntimeError("an action cannot finish and reschedule itself")
        if self.intent is not None and (
            self.effects
            or self.schedules
            or self.suspensions
            or self.event_opens
            or self.lines
            or self.finished
        ):
            raise RuntimeError("an EventAction may produce only its one Intent")


@dataclass(frozen=True, slots=True)
class _ResolvedEvent[W]:
    event: BaseEvent[W]
    outcome: Outcome[W]

    @property
    def order(self) -> tuple[int, str]:
        return min(child.seq for child in self.event.children), str(self.event.event_id)


type _WorkUnit[W] = _Context[W] | _ResolvedEvent[W]


@dataclass(slots=True)
class _FinalizationContext[W]:
    _tick: Tick
    _events_enabled: bool
    _already_scheduled: set[int]
    _already_ended: set[EventId]
    effects: list[Effect[W]] = field(default_factory=list)
    schedules: list[tuple[BaseAction[W], Tick | None]] = field(default_factory=list)
    deletes: list[BaseAction[W]] = field(default_factory=list)
    owner_deletes: list[EntityId] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    event_ends: list[EventId] = field(default_factory=list)

    @property
    def tick(self) -> Tick:
        return self._tick

    def emit(self, effect: Effect[W], /) -> None:
        self.effects.append(effect)

    def schedule(self, action: BaseAction[W], due_tick: Tick | None, /) -> None:
        _validate_due_tick(due_tick, self._tick)
        action_key = id(action)
        if action_key in self._already_scheduled:
            raise RuntimeError("the same action object was scheduled more than once")
        self._already_scheduled.add(action_key)
        self.schedules.append((action, due_tick))

    def delete(self, action: BaseAction[W], /) -> None:
        self.deletes.append(action)

    def delete_owned_by(self, entity_id: EntityId, /) -> None:
        self.owner_deletes.append(entity_id)

    def log(self, line: str, /) -> None:
        self.lines.append(line)

    def _foliot_end_event(self, event_id: EventId, /) -> None:
        if not self._events_enabled:
            raise EventConfigurationError(
                "end_event() needs Simulation(..., events=Events(event_store))"
            )
        if event_id in self._already_ended or event_id in self.event_ends:
            raise RuntimeError(f"Event was ended more than once: {event_id}")
        self.event_ends.append(event_id)


class Simulation[W]:
    """Advance one world through atomic, deterministic ticks.

    Args:
        store: Consumer-supplied persistence adapter for world type `W`.
        events: Optional Event collaborator using the exact same store.
        finalizer: Optional application policy run after normal effects and before
            commit.

    Raises:
        ValueError: If `events` is connected to a different store.
    """

    __slots__ = ("_events", "_finalizer", "_store")

    def __init__(
        self,
        store: Store[W],
        /,
        *,
        events: Events[W] | None = None,
        finalizer: TickFinalizer[W] | None = None,
    ) -> None:
        if events is not None and not events.uses_store(store):
            raise ValueError("Events must use the same store passed to Simulation")
        self._store = store
        self._events = events
        self._finalizer = finalizer

    @property
    def tick(self) -> Tick:
        """The next unfinished tick."""
        return self._store.current_tick()

    def process_tick(self) -> None:
        """Process and commit exactly one tick without waiting.

        Handler and resolver failures are logged and isolated before writes.
        Exceptions raised while applying effects or finalization escape and
        cause the store transaction to roll back.
        """
        tick = self.tick
        with self._store.tick_transaction(tick) as txn:
            contexts = self._process_actions(tick)
            contexts = _without_invalid_event_opens(contexts, self._events, tick)
            resolutions = _resolve_events(
                self._events,
                contexts,
                tick,
                self._store.world_seed,
            )
            ordinary = [context for context in contexts if context.intent is None]
            ordinary, resolutions = _without_duplicate_schedules(
                ordinary,
                resolutions,
                tick,
            )

            for context in sorted(ordinary, key=lambda item: item.owner.seq):
                _apply_context(txn, context, tick, self._events)
            for resolution in sorted(resolutions, key=lambda item: item.order):
                _apply_resolution(txn, resolution, tick, self._events)

            if self._finalizer is not None:
                scheduled = _scheduled_action_ids(ordinary, resolutions)
                ended = {
                    resolution.event.event_id
                    for resolution in resolutions
                    if resolution.outcome.is_ending
                }
                final_context = _FinalizationContext[W](
                    _tick=tick,
                    _events_enabled=self._events is not None,
                    _already_scheduled=scheduled,
                    _already_ended=ended,
                )
                self._finalizer.finalize(txn.world, final_context)
                _apply_finalization(txn, final_context, tick, self._events)

    def _process_actions(self, tick: Tick) -> list[_Context[W]]:
        contexts: list[_Context[W]] = []
        for action in self._store.due(tick):
            context = _Context(
                owner=action,
                _tick=tick,
                _rng=counter_rng(
                    self._store.world_seed,
                    action.entity_id,
                    tick,
                    action.seq,
                ),
                _events_enabled=self._events is not None,
            )
            try:
                action.process(context)
                context.validate()
            except Exception:
                _LOGGER.exception(
                    "action failed at tick=%s action=%s entity_id=%s seq=%s",
                    tick,
                    type(action).__qualname__,
                    action.entity_id,
                    action.seq,
                )
                continue
            contexts.append(context)
        return contexts

    def run(self, driver: Driver, /) -> None:
        """Process ticks while `driver` permits and provides pacing.

        Args:
            driver: Manual, real-time, or consumer-defined pacing strategy.
        """
        while driver.should_continue(self.tick):
            driver.wait_for(self.tick)
            self.process_tick()


def _without_invalid_event_opens[W](
    contexts: list[_Context[W]],
    events: Events[W] | None,
    tick: Tick,
) -> list[_Context[W]]:
    if events is None:
        return contexts

    invalid: dict[int, _Context[W]] = {}
    opening: dict[EventId, list[_Context[W]]] = {}
    for context in contexts:
        for event, due_tick in context.event_opens:
            opening.setdefault(event.event_id, []).append(context)
            try:
                events.validate_open(event, due_tick, tick)
            except Exception as error:
                invalid[id(context)] = context
                _report_invalid_context(context.owner, tick, str(error))

    for event_id, requesting_contexts in opening.items():
        if len(requesting_contexts) < 2:
            continue
        for context in requesting_contexts:
            context_key = id(context)
            if context_key not in invalid:
                invalid[context_key] = context
                _report_invalid_context(
                    context.owner,
                    tick,
                    f"Event id was opened more than once: {event_id}",
                )

    return [context for context in contexts if id(context) not in invalid]


def _resolve_events[W](
    events: Events[W] | None,
    contexts: list[_Context[W]],
    tick: Tick,
    world_seed: int,
) -> list[_ResolvedEvent[W]]:
    if events is None:
        return []

    attempts: dict[EventId, list[_Context[W]]] = {}
    for context in contexts:
        if context.intent is not None:
            attempts.setdefault(context.intent.event_id, []).append(context)

    resolved: list[_ResolvedEvent[W]] = []
    for event_id in sorted(attempts, key=str):
        event = events.event(event_id)
        if event is None:
            _report_event_failure(
                tick,
                event_id,
                "<missing>",
                "an EventAction refers to an Event that does not exist",
            )
            continue

        actual = {context.owner.seq: context for context in attempts[event_id]}
        expected = {child.seq: child for child in event.children}
        if actual.keys() != expected.keys() or any(
            actual[seq].owner is not child for seq, child in expected.items() if seq in actual
        ):
            continue

        records = tuple(actual[child.seq].intent for child in event.children)
        if any(record is None for record in records):
            continue
        intent_records = tuple(record for record in records if record is not None)
        try:
            outcome = event.resolve(
                events.resolution_context(world_seed, event_id, tick),
                intent_records,
            )
            events.validate_outcome(event, outcome, tick)
        except Exception:
            _LOGGER.exception(
                "event failed at tick=%s event_id=%s resolver=%s",
                tick,
                event_id,
                type(event).__qualname__,
            )
            continue
        resolved.append(_ResolvedEvent(event, outcome))
    return resolved


def _without_duplicate_schedules[W](
    contexts: list[_Context[W]],
    resolutions: list[_ResolvedEvent[W]],
    tick: Tick,
) -> tuple[list[_Context[W]], list[_ResolvedEvent[W]]]:
    units: list[_WorkUnit[W]] = [*contexts, *resolutions]
    requests: dict[int, list[_WorkUnit[W]]] = {}
    targets: dict[int, BaseAction[W]] = {}
    for unit in units:
        for action in _unit_scheduled_actions(unit):
            action_key = id(action)
            requests.setdefault(action_key, []).append(unit)
            targets[action_key] = action

    invalid: dict[int, _WorkUnit[W]] = {}
    duplicate_targets: dict[int, list[BaseAction[W]]] = {}
    for action_key, requesting_units in requests.items():
        if len(requesting_units) < 2:
            continue
        target = targets[action_key]
        for unit in requesting_units:
            unit_key = id(unit)
            invalid[unit_key] = unit
            duplicate_targets.setdefault(unit_key, []).append(target)

    for unit_key, unit in invalid.items():
        targets_text = ", ".join(
            f"{type(action).__qualname__}(entity_id={action.entity_id!s})"
            for action in duplicate_targets[unit_key]
        )
        message = f"the same action object was scheduled more than once: {targets_text}"
        match unit:
            case _Context(owner=owner):
                _report_invalid_context(owner, tick, message)
            case _ResolvedEvent(event=event):
                _report_event_failure(
                    tick,
                    event.event_id,
                    type(event).__qualname__,
                    message,
                )

    return (
        [context for context in contexts if id(context) not in invalid],
        [resolution for resolution in resolutions if id(resolution) not in invalid],
    )


def _unit_scheduled_actions[W](unit: _WorkUnit[W]) -> tuple[BaseAction[W], ...]:
    match unit:
        case _Context(schedules=schedules, event_opens=event_opens):
            return (
                *(action for action, _ in schedules),
                *(child for event, _ in event_opens for child in event.children),
            )
        case _ResolvedEvent(outcome=outcome):
            return outcome.scheduled_actions


def _scheduled_action_ids[W](
    contexts: list[_Context[W]],
    resolutions: list[_ResolvedEvent[W]],
) -> set[int]:
    return {
        id(action) for unit in (*contexts, *resolutions) for action in _unit_scheduled_actions(unit)
    }


def _report_invalid_context[W](
    action: BaseAction[W],
    tick: Tick,
    message: str,
) -> None:
    try:
        raise RuntimeError(message)
    except RuntimeError:
        _LOGGER.exception(
            "action failed at tick=%s action=%s entity_id=%s seq=%s",
            tick,
            type(action).__qualname__,
            action.entity_id,
            action.seq,
        )


def _report_event_failure(
    tick: Tick,
    event_id: EventId,
    resolver: str,
    message: str,
) -> None:
    try:
        raise RuntimeError(message)
    except RuntimeError:
        _LOGGER.exception(
            "event failed at tick=%s event_id=%s resolver=%s",
            tick,
            event_id,
            resolver,
        )


def _apply_context[W](
    txn: Txn[W],
    context: _Context[W],
    tick: Tick,
    events: Events[W] | None,
) -> None:
    for action, due_tick in context.schedules:
        txn.schedule(action, due_tick)
    for event, due_tick in context.event_opens:
        if events is None:
            raise RuntimeError("an Event request survived without Event support")
        events.open(txn, event, due_tick)
    for entity_id, by in context.suspensions:
        txn.suspend(entity_id, by)
    for effect in context.effects:
        effect.apply(txn.world)
    for line in context.lines:
        txn.log(tick, line)

    match context.owner.state:
        case Active(due_tick=due_tick):
            if context.finished or (due_tick is not None and not context.reschedules_owner()):
                txn.delete(context.owner)
        case Suspended():
            raise RuntimeError("a store returned a suspended action as due")


def _apply_resolution[W](
    txn: Txn[W],
    resolution: _ResolvedEvent[W],
    tick: Tick,
    events: Events[W] | None,
) -> None:
    if events is None:
        raise RuntimeError("an Event resolution survived without Event support")
    outcome = resolution.outcome
    for action, due_tick in outcome.schedules:
        txn.schedule(action, due_tick)
    for action in outcome.deletes:
        txn.delete(action)
    for effect in outcome.effects:
        effect.apply(txn.world)
    for line in outcome.lines:
        txn.log(tick, line)
    if outcome.is_ending:
        events.end(txn, resolution.event.event_id)
    else:
        events.continue_event(txn, resolution.event, outcome)


def _apply_finalization[W](
    txn: Txn[W],
    context: _FinalizationContext[W],
    tick: Tick,
    events: Events[W] | None,
) -> None:
    for action, due_tick in context.schedules:
        txn.schedule(action, due_tick)
    for action in context.deletes:
        txn.delete(action)
    for effect in context.effects:
        effect.apply(txn.world)
    for entity_id in context.owner_deletes:
        txn.delete_owned_by(entity_id)
    for line in context.lines:
        txn.log(tick, line)
    for event_id in context.event_ends:
        if events is None:
            raise RuntimeError("an Event ending survived without Event support")
        events.end(txn, event_id)


def _validate_due_tick(
    due_tick: Tick | None,
    tick: Tick,
    *,
    recurring: bool = True,
) -> None:
    if due_tick is None:
        if recurring:
            return
        raise TypeError("due_tick must be an int, not None")
    if type(due_tick) is not int:
        raise TypeError("due_tick must be an int or None, not bool")
    if due_tick <= tick:
        raise ValueError("due_tick must be later than the current tick")
