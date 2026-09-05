# Eventworld

Eventworld is a deliberately small consumer of foliot's optional Event layer.
It is separate from `tinyworld`, which remains the proof that Layer 1 works
without importing Events.

Lira walks through a haunted forest toward a moonlit clearing. The forest may
create a Fight Event and its temporary wolf. Opening the Event explicitly
suspends Lira's walk and schedules one EventAction for each combatant. Each
round, Lira and the wolf independently choose an Intent from the same
tick-start state. The Fight Event resolves both choices together and either
creates the next round or ends explicitly.

Combat formulas, HP, potions, death, targets, and the wolf are all game-owned.
The game-owned finalizer checks HP after effects, removes a dead entity's
actions, and explicitly ends the Event. When the wolf dies or escapes, foliot
removes the Event children and resumes Lira's walk. Its arrival deadline moves
forward by exactly the time spent suspended.

Run the default deterministic story:

```console
uv run python -m examples.eventworld
```

For another length or deliberate seed:

```console
uv run python -m examples.eventworld 30 12345
```
