import logging
from typing import override

import pytest

from foliot import Driver, RealtimeDriver


class _FakeRealtimeDriver(RealtimeDriver):
    __slots__ = ("now", "sleeps")

    def __init__(self, tick_seconds: float, *, start: float = 0.0) -> None:
        super().__init__(tick_seconds)
        self.now = start
        self.sleeps: list[float] = []

    def elapse(self, seconds: float) -> None:
        self.now += seconds

    @override
    def _now(self) -> float:
        return self.now

    @override
    def _sleep(self, seconds: float, /) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def accept_driver(driver: Driver) -> None:
    """Compile-time assertion that `RealtimeDriver` satisfies the protocol."""
    del driver


def test_realtime_driver_should_satisfy_driver_protocol() -> None:
    accept_driver(RealtimeDriver(1.0))


@pytest.mark.parametrize("tick_seconds", [1, 0.5, 1.5, 3600])
def test_realtime_driver_should_accept_positive_finite_durations(
    tick_seconds: float,
) -> None:
    driver = RealtimeDriver(tick_seconds)

    assert driver.tick_seconds == float(tick_seconds)


@pytest.mark.parametrize("tick_seconds", [True, False, "1", None])
def test_realtime_driver_should_reject_non_numeric_duration(tick_seconds: object) -> None:
    with pytest.raises(TypeError, match="tick_seconds must be an int or float, not bool"):
        RealtimeDriver(tick_seconds)  # pyright: ignore[reportArgumentType] -- runtime boundary test


@pytest.mark.parametrize("tick_seconds", [0, -1, float("inf"), float("-inf"), float("nan")])
def test_realtime_driver_should_reject_non_positive_or_non_finite_duration(
    tick_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="tick_seconds must be finite and greater than zero"):
        RealtimeDriver(tick_seconds)


def test_realtime_driver_should_start_first_tick_immediately() -> None:
    driver = _FakeRealtimeDriver(1.0, start=10.0)

    driver.wait_for(17)

    assert driver.now == 10.0
    assert driver.sleeps == []


def test_realtime_driver_should_anchor_when_run_starts_not_when_constructed() -> None:
    driver = _FakeRealtimeDriver(1.0, start=10.0)
    driver.elapse(50.0)

    driver.wait_for(17)
    driver.elapse(0.4)
    driver.wait_for(18)

    assert driver.now == 61.0
    assert driver.sleeps == pytest.approx([0.6])


def test_realtime_driver_should_recompute_fractional_deadlines_from_fixed_anchor() -> None:
    driver = _FakeRealtimeDriver(0.1, start=100.0)
    driver.wait_for(0)

    for tick in range(1, 36_001):
        driver.elapse(0.003)
        driver.wait_for(tick)

    assert driver.now == pytest.approx(3700.0)


def test_realtime_driver_should_not_warn_when_tick_finishes_before_deadline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="foliot.drivers")
    driver = _FakeRealtimeDriver(1.0, start=10.0)
    driver.wait_for(10)
    driver.elapse(0.4)

    driver.wait_for(11)

    assert driver.now == 11.0
    assert driver.sleeps == pytest.approx([0.6])
    assert caplog.records == []


def test_realtime_driver_should_use_boundary_that_tick_finishes_exactly_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="foliot.drivers")
    driver = _FakeRealtimeDriver(1.0, start=10.0)
    driver.wait_for(10)
    driver.elapse(1.0)

    driver.wait_for(11)

    assert driver.now == 11.0
    assert driver.sleeps == []
    assert caplog.records == []


def test_realtime_driver_should_wait_for_next_boundary_after_overrun(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="foliot.drivers")
    driver = _FakeRealtimeDriver(1.0, start=10.0)
    driver.wait_for(10)
    driver.elapse(1.4)

    driver.wait_for(11)

    assert driver.now == 12.0
    assert driver.sleeps == pytest.approx([0.6])
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert caplog.records[0].getMessage() == (
        "realtime tick overran cadence: tick=10 processing_seconds=1.4 "
        "tick_seconds=1 missed_slots=1"
    )


def test_realtime_driver_should_report_many_missed_slots_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="foliot.drivers")
    driver = _FakeRealtimeDriver(1.0, start=10.0)
    driver.wait_for(10)
    driver.elapse(3.4)

    driver.wait_for(11)

    assert driver.now == 14.0
    assert driver.sleeps == pytest.approx([0.6])
    assert len(caplog.records) == 1
    assert "tick=10" in caplog.records[0].getMessage()
    assert "processing_seconds=3.4" in caplog.records[0].getMessage()
    assert "missed_slots=3" in caplog.records[0].getMessage()


def test_realtime_driver_should_run_until_external_interruption() -> None:
    driver = RealtimeDriver(1.0)

    assert driver.should_continue(0)
    assert driver.should_continue(1_000_000)
