# Tinyworld

Tinyworld is a deliberately small consumer of foliot's public Layer 1 API.
Lira walks forever between the forest edge and a moonlit clearing. The forest
may poison her, and poison schedules later strikes. When a walk arrives, it
queues `Rest` against its destination clearing. Resting decides whether Lira
needs a healing potion when the target is the moonlit clearing, asks the
game-specific pathing service for the nearest POI and which environment
connects the two places, and queues `Walk` against that environment. For this
two-clearing mock, "nearest" is a stored answer rather than a distance
algorithm.

Run one million logical ticks:

```console
uv run python -m examples.tinyworld
```

For a shorter run or a different deliberate seed, pass them positionally:

```console
uv run python -m examples.tinyworld 5000 12345
```

The command prints only a compact summary and the first and last five journal
entries. Its fixed seed makes the final HP, entry count, and journal SHA-256
repeatable.
