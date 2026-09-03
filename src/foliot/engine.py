"""The deterministic tick loop (§7.4, §8).

Handlers only describe work in private collecting contexts. The engine asks
every due action first, validates the complete pile, then applies successful
contexts in permanent `seq` order inside one store transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from foliot.actions import Active, BaseAction, Suspended
from foliot.drivers import Driver
from foliot.effects import Effect
from foliot.ids import Tick
from foliot.rng import Rng, counter_rng
from foliot.stores import Store, Txn

__all__ = ["Simulation"]

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Context[W]:
    owner: BaseAction[W]
    _tick: Tick
    _rng: Rng
    effects: list[Effect[W]] = field(default_factory=list)
    schedules: list[tuple[BaseAction[W], Tick | None]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
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
        if due_tick is not None:
            if type(due_tick) is not int:
                raise TypeError("due_tick must be an int or None, not bool")
            if due_tick <= self._tick:
                raise ValueError("due_tick must be later than the current tick")
        self.schedules.append((action, due_tick))

    def log(self, line: str, /) -> None:
        self.lines.append(line)

    def finish(self) -> None:
        self.finished = True

    def reschedules_owner(self) -> bool:
        return any(action is self.owner for action, _ in self.schedules)

    def validate(self) -> None:
        if self.finished and self.reschedules_owner():
            raise RuntimeError("an action cannot finish and reschedule itself")


class Simulation[W]:
    """Advance one world through atomic, deterministic ticks."""

    __slots__ = ("_store",)

    def __init__(self, store: Store[W], /) -> None:
        self._store = store

    @property
    def tick(self) -> Tick:
        """The next unfinished tick."""
        return self._store.current_tick()

    def process_tick(self) -> None:
        """Process and commit exactly one tick without waiting."""
        tick = self.tick
        with self._store.tick_transaction(tick) as txn:
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

            contexts = _without_duplicate_schedules(contexts, tick)
            for context in sorted(contexts, key=lambda item: item.owner.seq):
                _apply_context(txn, context, tick)

    def run(self, driver: Driver, /) -> None:
        """Process ticks while the injected driver permits."""
        while driver.should_continue(self.tick):
            driver.wait_for(self.tick)
            self.process_tick()


def _without_duplicate_schedules[W](
    contexts: list[_Context[W]],
    tick: Tick,
) -> list[_Context[W]]:
    requests: dict[int, list[_Context[W]]] = {}
    targets: dict[int, BaseAction[W]] = {}
    for context in contexts:
        for action, _ in context.schedules:
            action_key = id(action)
            requests.setdefault(action_key, []).append(context)
            targets[action_key] = action

    invalid: dict[int, _Context[W]] = {}
    duplicate_targets: dict[int, list[BaseAction[W]]] = {}
    for action_key, requesting_contexts in requests.items():
        if len(requesting_contexts) < 2:
            continue
        target = targets[action_key]
        for context in requesting_contexts:
            context_key = id(context)
            invalid[context_key] = context
            duplicate_targets.setdefault(context_key, []).append(target)

    for context_key, context in invalid.items():
        targets_text = ", ".join(
            f"{type(action).__qualname__}(entity_id={action.entity_id!s})"
            for action in duplicate_targets[context_key]
        )
        _report_invalid_context(
            context.owner,
            tick,
            f"the same action object was scheduled more than once: {targets_text}",
        )

    return [context for context in contexts if id(context) not in invalid]


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


def _apply_context[W](txn: Txn[W], context: _Context[W], tick: Tick) -> None:
    for action, due_tick in context.schedules:
        txn.schedule(action, due_tick)
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
