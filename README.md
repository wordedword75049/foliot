# foliot

A deterministic tick-driven simulation core: durable queue, absolute deadlines, per-entity randomness. Domain-agnostic.

## Tinyworld

Run the bundled one-million-tick example:

```console
uv run python -m examples.tinyworld
```

Lira continuously crosses a haunted forest. Encounters may poison her, poison
reschedules its own strikes, and a walk targets the forest while carrying a
separate destination clearing. Arrival queues `Rest` against that clearing;
resting in the moonlit clearing may emit healing, chooses the next destination,
asks the game's pathing service for both the nearest POI and connecting
environment, and queues the next walk. The example uses only foliot's public
Layer 1 API and a fixed seed, so its journal hash is reproducible.
