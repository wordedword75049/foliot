# Drivers

A `Driver` controls pacing. It never decides what a tick means and never
changes the world.

## ManualDriver

`ManualDriver(until_tick=n)` processes immediately through `n`, inclusively.
It is useful for tests, offline advancement, and fast-forwarding:

```python
Simulation(store).run(ManualDriver(until_tick=999_999))
```

If the store starts at tick 0, that processes one million logical ticks.

## RealtimeDriver

`RealtimeDriver(tick_seconds=1.0)` runs continuously on a monotonic wall-clock
cadence:

```python
Simulation(store).run(RealtimeDriver(tick_seconds=1.0))
```

The first tick starts immediately. Later ticks target an absolute cadence so
sleep and processing overhead do not accumulate drift. If processing overruns,
the driver skips missed wall-clock slots, logs one operational warning, and
continues with the next logical tick. Logical tick numbers are never skipped.

The driver intentionally has no stop flag. The surrounding application owns
shutdown and interruption. Create a new `RealtimeDriver` after restarting to
establish a fresh wall-clock cadence; persisted logical time remains in the
store.

`tick_seconds` accepts positive finite whole or fractional seconds. Booleans,
zero, negative values, infinities, and NaN are rejected.
