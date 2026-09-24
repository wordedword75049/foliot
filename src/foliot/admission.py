"""The result and conflict error for external action admission."""

from dataclasses import dataclass

from foliot.ids import Tick

__all__ = ["ActionAdmission", "StaleSubmissionError"]


@dataclass(frozen=True, slots=True)
class ActionAdmission:
    """Receipt for an action committed at a logical tick boundary.

    Attributes:
        seq: The action's permanent store-assigned sequence identity.
        boundary_tick: Next unfinished tick when the action was admitted.
    """

    seq: int
    boundary_tick: Tick


class StaleSubmissionError(RuntimeError):
    """The observed boundary differs from the store's next unfinished tick.

    Attributes:
        expected_tick: Boundary from which the action was decided.
        actual_tick: Next unfinished tick in the store at admission time.
    """

    __slots__ = ("actual_tick", "expected_tick")

    def __init__(self, expected_tick: Tick, actual_tick: Tick, /) -> None:
        self.expected_tick = expected_tick
        self.actual_tick = actual_tick
        super().__init__(
            f"submission expected tick {expected_tick}, but the next unfinished tick is "
            f"{actual_tick}"
        )
