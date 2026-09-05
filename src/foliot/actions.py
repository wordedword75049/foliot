"""Actions with engine-owned lifecycle bookkeeping.

Every action that enters foliot's queue inherits `BaseAction`. This is
deliberately different from the library's structural ports: binding, stable
identity, and suspension are simulation invariants that must have one
implementation.

An action always has a complete binding value. It starts `Unbound`; the store
changes it to `Bound(seq, state)` on first successful admission. The same
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
    """Queue state of an admitted action that is eligible to run.

    Attributes:
        due_tick: Future deadline, or `None` for an action due every tick.
        status: Stable discriminator useful when persisting the state.
    """

    due_tick: Tick | None
    status: Literal["active"] = field(default="active", init=False)


@dataclass(frozen=True, slots=True)
class Suspended:
    """Queue state of an admitted action that is temporarily paused.

    Attributes:
        suspended_at: Tick at which the pause began.
        suspended_by: Handle that must be used to resume the action.
        due_tick: Deadline preserved from the active state, or `None` for a
            recurring action.
        status: Stable discriminator useful when persisting the state.
    """

    suspended_at: Tick
    suspended_by: SuspensionId
    due_tick: Tick | None
    status: Literal["suspended"] = field(default="suspended", init=False)


type ActionState = Active | Suspended


@dataclass(frozen=True, slots=True)
class Unbound:
    """A complete action that has not entered the queue yet."""


@dataclass(frozen=True, slots=True)
class Bound:
    """The store-owned metadata of an admitted action.

    `seq` is assigned once, on first admission, and is carried unchanged through
    every replacement of `state`. It is the action's replay identity, not its
    position in a due list.

    Attributes:
        seq: Positive sequence number assigned by the store.
        state: Current active or suspended queue state.
    """

    seq: int
    state: ActionState


type ActionBinding = Unbound | Bound


class BaseAction[W](ABC):
    """Mandatory blueprint for every action entering foliot's queue.

    The base owns only the lifecycle fields foliot needs. Subclasses keep all
    application-owned payload -- target, amount, interval, domain deadline,
    and so on -- on the same object and implement `process()`.

    Args:
        entity_id: Opaque identity of the entity that owns the action.
        suspendable: Whether owner-level suspension requests may pause it.
    """

    __slots__ = ("_binding", "_entity_id", "_suspendable")

    def __init__(self, entity_id: EntityId, *, suspendable: bool) -> None:
        self._entity_id = entity_id
        self._suspendable = suspendable
        self._binding: ActionBinding = Unbound()

    @property
    def entity_id(self) -> EntityId:
        """The entity that owns this queued action."""
        return self._entity_id

    @property
    def suspendable(self) -> bool:
        """Whether an owner-level suspension request may pause this action."""
        return self._suspendable

    @property
    def binding(self) -> ActionBinding:
        """Whether this object has entered the queue, and its metadata if so."""
        return self._binding

    @property
    def seq(self) -> int:
        """Return the permanent sequence number assigned by the store.

        Raises:
            RuntimeError: If the action has not been admitted yet.
        """
        match self._binding:
            case Bound(seq=seq):
                return seq
            case Unbound():
                raise RuntimeError("an unbound action has no seq")

    @property
    def state(self) -> ActionState:
        """Return the active or suspended state of an admitted action.

        Raises:
            RuntimeError: If the action has not been admitted yet.
        """
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

        Args:
            seq: Permanent sequence number allocated by the store.
            state: Restored active or suspended queue state.

        Raises:
            RuntimeError: If the action is already bound.
        """
        match self._binding:
            case Unbound():
                self._binding = Bound(seq, state)
            case Bound():
                raise RuntimeError("an action can only be bound once")

    def reschedule(self, due_tick: Tick | None, /) -> None:
        """Replace an active deadline while preserving the action's `seq`.

        Args:
            due_tick: New deadline, or `None` for recurring execution.

        Raises:
            RuntimeError: If the action is unbound or suspended.
        """
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
        """Pause this action, preserving its deadline and replay identity.

        Non-suspendable and already-suspended actions are unchanged.

        Args:
            tick: Tick at which the pause begins.
            by: Opaque handle that owes the later resumption.

        Raises:
            RuntimeError: If the action has not been admitted yet.
        """
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
        """Resume this action and shift its deadlines by the pause length.

        Active actions are unchanged. For a suspended action, the stored
        deadline is shifted and `on_resume(paused_for)` is called.

        Args:
            tick: Tick at which the action resumes.

        Raises:
            RuntimeError: If the action has not been admitted yet.
        """
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
        """React to resumption after `paused_for` logical ticks.

        Override this hook to shift application-owned deadlines such as `arrives_at`.
        The default implementation does nothing.
        """

    @abstractmethod
    def process(self, ctx: TickContext[W], /) -> None:
        """Describe this occurrence through the collecting context.

        Mutate neither the queue nor persistent world state directly. Use the
        context to stage effects, schedules, suspension, logs, and completion.
        """
        ...


def restore_action_binding[W](action: BaseAction[W], binding: ActionBinding, /) -> None:
    """Restore engine-owned metadata after an in-memory commit failure.

    Internal to foliot and deliberately absent from `__all__`. Consumer stores
    use their own transaction rollback; only `MemoryStore` needs to restore a
    Python object that was already mutated while publishing staged commands.
    """
    action._binding = binding  # pyright: ignore[reportPrivateUsage] -- same-module rollback
