"""Who decides when the next tick happens (§4.2).

`process_tick()` never waits. That split is the whole reason the same code path
runs at one tick per second in production and at ten million ticks per second in
a test: only the driver differs. Hardcoding `time.sleep` into the loop makes the
library untestable, and it is felt immediately.

`ManualDriver` and `RealtimeDriver` arrive at M5 and M6.
"""

from typing import Protocol

from foliot.ids import Tick

__all__ = ["Driver"]


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
        """False ends the run. `ManualDriver` stops at a tick count."""
        ...
