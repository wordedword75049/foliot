"""Who decides when the next tick happens (§4.2).

`process_tick()` never waits. That split is the whole reason the same code path
runs at one tick per second in production and at ten million ticks per second in
a test: only the driver differs. Hardcoding `time.sleep` into the loop makes the
library untestable, and it is felt immediately.

`ManualDriver` arrives at M5; `RealtimeDriver` follows at M6.
"""

from dataclasses import dataclass
from typing import Protocol

from foliot.ids import Tick

__all__ = ["Driver", "ManualDriver"]


class Driver(Protocol):
    """Paces the loop. Injected, never constructed by the engine."""

    def wait_for(self, tick: Tick, /) -> None:
        """Block until `tick` should begin.

        `ManualDriver` returns at once. `RealtimeDriver` sleeps toward an
        absolute target -- `start + n * duration` -- never a relative one:
        sleeping "the rest of the second" accumulates the overhead of waking,
        measuring and sleeping again, which at 2 ms per tick is roughly ninety
        minutes of drift per month, silently (§4.1).
        """
        ...

    def should_continue(self, tick: Tick, /) -> bool:
        """Whether the next unfinished `tick` should be processed."""
        ...


@dataclass(frozen=True, slots=True)
class ManualDriver:
    """Run immediately through an inclusive target tick."""

    until_tick: Tick

    def __post_init__(self) -> None:
        if type(self.until_tick) is not int:
            raise TypeError("until_tick must be an int, not bool")
        if self.until_tick < 0:
            raise ValueError("until_tick must be non-negative")

    def wait_for(self, tick: Tick, /) -> None:
        """Return immediately; manual time never sleeps."""
        del tick

    def should_continue(self, tick: Tick, /) -> bool:
        """Include `until_tick`, then stop at the following tick."""
        return tick <= self.until_tick
