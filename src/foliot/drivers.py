"""Who decides when the next tick happens (§4.2).

`process_tick()` never waits. That split is the whole reason the same code path
runs at one tick per second in production and at ten million ticks per second in
a test: only the driver differs. Hardcoding `time.sleep` into the loop makes the
library untestable, and it is felt immediately.

`ManualDriver` advances without waiting. `RealtimeDriver` paces the same loop
against a monotonic, absolute cadence and skips wall-clock slots after an
overrun without skipping logical ticks.
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import Protocol

from foliot.ids import Tick

__all__ = ["Driver", "ManualDriver", "RealtimeDriver"]

_LOGGER = logging.getLogger(__name__)


class Driver(Protocol):
    """Paces the loop. Injected, never constructed by the engine."""

    def wait_for(self, tick: Tick, /) -> None:
        """Block until `tick` should begin.

        `ManualDriver` returns at once. `RealtimeDriver` sleeps toward an
        absolute cadence target -- `start + slot * duration` -- never a
        relative one. Cadence slots may be skipped after an overrun; logical
        ticks may not. Sleeping "the rest of the second" accumulates the
        overhead of waking, measuring and sleeping again, which at 2 ms per
        tick is roughly ninety minutes of drift per month, silently (§4.1).
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


class RealtimeDriver:
    """Run continuously on a fixed monotonic cadence.

    The object is intentionally stateful: it remembers one run's cadence
    anchor, current wall-clock slot, and most recently started logical tick.
    Create a new driver to establish a fresh cadence after restart.
    """

    __slots__ = (
        "_cadence_start",
        "_slot",
        "_started_at",
        "_started_tick",
        "_tick_seconds",
    )

    def __init__(self, tick_seconds: float) -> None:
        if type(tick_seconds) not in (int, float):
            raise TypeError("tick_seconds must be an int or float, not bool")
        try:
            normalized = float(tick_seconds)
        except OverflowError as error:
            raise ValueError("tick_seconds must be finite and greater than zero") from error
        if not math.isfinite(normalized) or normalized <= 0.0:
            raise ValueError("tick_seconds must be finite and greater than zero")

        self._tick_seconds = normalized
        self._cadence_start: float | None = None
        self._slot = 0
        self._started_at: float | None = None
        self._started_tick: Tick | None = None

    @property
    def tick_seconds(self) -> float:
        """Seconds between wall-clock cadence slots."""
        return self._tick_seconds

    def wait_for(self, tick: Tick, /) -> None:
        """Wait until `tick` may begin on this run's absolute cadence."""
        now = self._now()
        if self._cadence_start is None:
            self._cadence_start = now
            self._started_at = now
            self._started_tick = tick
            return

        cadence_start = self._cadence_start
        started_at = self._started_at
        started_tick = self._started_tick
        assert started_at is not None
        assert started_tick is not None

        next_slot = self._slot + 1
        slot = max(next_slot, math.ceil((now - cadence_start) / self._tick_seconds))
        deadline = cadence_start + slot * self._tick_seconds
        while deadline < now:
            slot += 1
            deadline = cadence_start + slot * self._tick_seconds

        missed_slots = slot - next_slot
        if missed_slots:
            _LOGGER.warning(
                "realtime tick overran cadence: tick=%s processing_seconds=%.9g "
                "tick_seconds=%.9g missed_slots=%s",
                started_tick,
                now - started_at,
                self._tick_seconds,
                missed_slots,
            )

        remaining = deadline - now
        if remaining > 0.0:
            self._sleep(remaining)

        self._slot = slot
        self._started_at = self._now()
        self._started_tick = tick

    def should_continue(self, tick: Tick, /) -> bool:
        """Run until the surrounding application interrupts the loop."""
        del tick
        return True

    def _now(self) -> float:
        """Return monotonic time; overridden only by foliot's private tests."""
        return time.monotonic()

    def _sleep(self, seconds: float, /) -> None:
        """Sleep in real time; overridden only by foliot's private tests."""
        time.sleep(seconds)
