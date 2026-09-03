"""Actions: game behaviour with engine-owned lifecycle bookkeeping (§6, §7).

Every game action that enters foliot's queue inherits `BaseAction`. This is
deliberately different from the library's structural ports: binding, stable
identity, and suspension are invariants every game needs and must implement in
exactly the same way.

An action always has a complete binding value. It starts `Unbound`; the store
changes it to `Bound(seq, state)` on first successful admission. The same game
object -- including all subclass fields -- survives every reschedule,
suspension, and resume, and its `seq` never changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from foliot.context import TickContext
from foliot.ids import EntityId, SuspensionId, Tick

__all__ = [
    "ActionBinding",
    "ActionState",
    "Active",
    "BaseAction",
    "Bound",
    "Suspended",
    "Unbound",
]


@dataclass(frozen=True, slots=True)
class Active:
    """An admitted action eligible to run according to its queue shape."""

    due_tick: Tick | None
    status: Literal["active"] = field(default="active", init=False)


@dataclass(frozen=True, slots=True)
class Suspended:
    """An admitted action paused by one event or other opaque suspender."""

    suspended_at: Tick
    suspended_by: SuspensionId
    due_tick: Tick | None
    status: Literal["suspended"] = field(default="suspended", init=False)


type ActionState = Active | Suspended


@dataclass(frozen=True, slots=True)
class Unbound:
    """A complete game action that has not entered the queue yet."""


@dataclass(frozen=True, slots=True)
class Bound:
    """The store-owned metadata of an admitted action.

    `seq` is assigned once, on first admission, and is carried unchanged through
    every replacement of `state`. It is the action's replay identity, not its
    position in a due list (§9.4b).
    """

    seq: int
    state: ActionState


type ActionBinding = Unbound | Bound


class BaseAction[W](ABC):
    """Mandatory blueprint for every game action entering foliot's queue.

    The base owns only fields the engine reads. Subclasses keep all game-owned
    payload -- poison damage, tick interval, arrival deadline, and so on -- on
    the same object and implement `process()`.
    """

    __slots__ = ("_binding", "_entity_id", "_suspendable")

    def __init__(self, entity_id: EntityId, *, suspendable: bool) -> None:
        self._entity_id = entity_id
        self._suspendable = suspendable
        self._binding: ActionBinding = Unbound()

    @property
    def entity_id(self) -> EntityId:
        return self._entity_id

    @property
    def suspendable(self) -> bool:
        """Whether an interruption pauses this action (§6.2)."""
        return self._suspendable

    @property
    def binding(self) -> ActionBinding:
        """Whether this object has entered the queue, and its metadata if so."""
        return self._binding

    @property
    def seq(self) -> int:
        """Stable replay identity assigned once by the store (§9.4b)."""
        match self._binding:
            case Bound(seq=seq):
                return seq
            case Unbound():
                raise RuntimeError("an unbound action has no seq")

    @property
    def state(self) -> ActionState:
        """Current queue state of an admitted action."""
        match self._binding:
            case Bound(state=state):
                return state
            case Unbound():
                raise RuntimeError("an unbound action has no state")

    def bind(self, seq: int, state: ActionState, /) -> None:
        """Bind this object after its first successful store admission.

        Durable stores also use this operation when hydrating an admitted
        action. Calling it twice would replace the replay identity and is
        therefore rejected.
        """
        match self._binding:
            case Unbound():
                self._binding = Bound(seq, state)
            case Bound():
                raise RuntimeError("an action can only be bound once")

    def reschedule(self, due_tick: Tick | None, /) -> None:
        """Replace an active deadline while preserving the action's `seq`."""
        match self._binding:
            case Bound(seq=seq, state=state):
                match state:
                    case Active():
                        self._binding = Bound(seq, Active(due_tick))
                    case Suspended():
                        raise RuntimeError("a suspended action cannot be rescheduled")
            case Unbound():
                raise RuntimeError("an unbound action must be bound before rescheduling")

    def suspend(self, tick: Tick, by: SuspensionId, /) -> None:
        """Pause this action, preserving its deadline and replay identity."""
        if not self._suspendable:
            return

        match self._binding:
            case Bound(seq=seq, state=state):
                match state:
                    case Active(due_tick=due_tick):
                        self._binding = Bound(
                            seq,
                            Suspended(
                                suspended_at=tick,
                                suspended_by=by,
                                due_tick=due_tick,
                            ),
                        )
                    case Suspended():
                        pass
            case Unbound():
                raise RuntimeError("an unbound action cannot be suspended")

    def resume(self, tick: Tick, /) -> None:
        """Resume this action and shift its deadlines by the pause length."""
        match self._binding:
            case Bound(seq=seq, state=state):
                match state:
                    case Suspended(suspended_at=suspended_at, due_tick=due_tick):
                        paused_for = tick - suspended_at
                        self._binding = Bound(
                            seq,
                            Active(
                                due_tick=None if due_tick is None else due_tick + paused_for,
                            ),
                        )
                        self.on_resume(paused_for)
                    case Active():
                        pass
            case Unbound():
                raise RuntimeError("an unbound action cannot be resumed")

    def on_resume(self, paused_for: int, /) -> None:  # noqa: B027 - optional hook
        """Override to shift game-owned deadlines by the same pause length."""

    @abstractmethod
    def process(self, ctx: TickContext[W], /) -> None:
        """Say what should happen by telling the collecting `ctx`."""
        ...


def restore_action_binding[W](action: BaseAction[W], binding: ActionBinding, /) -> None:
    """Restore engine-owned metadata after an in-memory commit failure.

    Internal to foliot and deliberately absent from `__all__`. Consumer stores
    use their own transaction rollback; only `MemoryStore` needs to restore a
    Python object that was already mutated while publishing staged commands.
    """
    action._binding = binding  # pyright: ignore[reportPrivateUsage] -- same-module rollback
