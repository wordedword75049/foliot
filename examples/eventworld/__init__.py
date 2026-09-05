"""A small zero-player fight built on foliot's optional Event layer."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import override

from foliot import (
    BaseAction,
    EntityId,
    FinalizationContext,
    ManualDriver,
    Rng,
    Simulation,
    Tick,
    TickContext,
)
from foliot.events import (
    BaseEvent,
    DecisionContext,
    EntityIdTemplate,
    EventAction,
    EventId,
    EventIdTemplate,
    EventMemoryStore,
    Events,
    IntentRecord,
    Outcome,
    ResolutionContext,
    end_event,
    open_event,
)

__all__ = ["EventworldResult", "run_eventworld"]

_DEFAULT_SEED = 20_260_905
_DEFAULT_TICKS = 20
_LIRA_ID = EntityId("lira")
_FOREST_ID = EntityId("haunted-forest")
_CLEARING_ID = EntityId("moonlit-clearing")
_FIGHT_IDS = EventIdTemplate("eventworld.fight")
_WOLF_IDS = EntityIdTemplate("eventworld.wolf")


@dataclass(slots=True)
class Character:
    """A game entity whose combat statistics may change."""

    entity_id: EntityId
    name: str
    hp: int
    max_hp: int
    agility: int
    damage: int
    potions: int = 0


@dataclass(slots=True)
class EventWorld:
    """Only game-owned state; foliot does not inspect these fields."""

    lira: Character
    active_fight: FightEvent | None = None
    fights_started: int = 0
    fights_ended: int = 0
    last_fight_result: str | None = None
    arrived: bool = False


class GameAction(BaseAction[EventWorld]):
    """The game's directed ordinary-action base."""

    __slots__ = ("_target_id",)

    def __init__(
        self,
        entity_id: EntityId,
        target_id: EntityId,
        *,
        suspendable: bool,
    ) -> None:
        super().__init__(entity_id, suspendable=suspendable)
        self._target_id = target_id

    @property
    def target_id(self) -> EntityId:
        return self._target_id


class GameEventAction(EventAction[EventWorld]):
    """The same directed-action convention for one Event round."""

    __slots__ = ("_target_id",)

    def __init__(self, entity_id: EntityId, target_id: EntityId, event_id: EventId) -> None:
        super().__init__(entity_id, event_id)
        self._target_id = target_id

    @property
    def target_id(self) -> EntityId:
        return self._target_id


@dataclass(frozen=True, slots=True)
class Attack:
    """The participant intends to attack its EventAction target."""


@dataclass(frozen=True, slots=True)
class DodgeAndHeal:
    """The participant intends to dodge while drinking a potion."""


@dataclass(frozen=True, slots=True)
class Escape:
    """The participant intends to leave the fight."""


type FightIntent = Attack | DodgeAndHeal | Escape


@dataclass(frozen=True, slots=True)
class ChangeHealth:
    target: Character
    amount: int

    def apply(self, world: EventWorld, /) -> None:
        del world
        self.target.hp = min(self.target.max_hp, max(0, self.target.hp + self.amount))


@dataclass(frozen=True, slots=True)
class DrinkPotion:
    target: Character
    healing: int

    def apply(self, world: EventWorld, /) -> None:
        del world
        self.target.potions -= 1
        self.target.hp = min(self.target.max_hp, self.target.hp + self.healing)


@dataclass(frozen=True, slots=True)
class BeginFight:
    fight: FightEvent

    def apply(self, world: EventWorld, /) -> None:
        world.active_fight = self.fight
        world.fights_started += 1


@dataclass(frozen=True, slots=True)
class FinishFight:
    fight: FightEvent
    result: str

    def apply(self, world: EventWorld, /) -> None:
        if world.active_fight is self.fight:
            world.active_fight = None
        world.fights_ended += 1
        world.last_fight_result = self.result


@dataclass(frozen=True, slots=True)
class Arrive:
    def apply(self, world: EventWorld, /) -> None:
        world.arrived = True


@dataclass(frozen=True, slots=True)
class Forest:
    entity_id: EntityId
    name: str
    danger: float

    def should_start_fight(self, rng: Rng, caution: float, /) -> bool:
        encounter_probability = self.danger * (1.0 - caution)
        return rng.random() < encounter_probability

    def create_fight(self, walk: Walk, tick: Tick, /) -> FightEvent:
        event_id = _FIGHT_IDS.from_action(walk, tick=tick, ordinal=0)
        wolf = Character(
            _WOLF_IDS.from_event(event_id, ordinal=0),
            "the ash wolf",
            hp=9,
            max_hp=9,
            agility=5,
            damage=3,
        )
        children = (
            LiraTurn(walk.lira, wolf, event_id),
            WolfTurn(wolf, walk.lira, event_id),
        )
        return FightEvent(event_id, children, walk.lira, wolf)


class Walk(GameAction):
    """Lira's journey, paused while the forest's fight Event is open."""

    __slots__ = ("arrives_at", "caution", "encounter_finished", "forest", "lira")

    def __init__(
        self,
        lira: Character,
        forest: Forest,
        *,
        arrives_at: Tick,
        caution: float,
    ) -> None:
        super().__init__(lira.entity_id, forest.entity_id, suspendable=True)
        self.lira = lira
        self.forest = forest
        self.arrives_at = arrives_at
        self.caution = caution
        self.encounter_finished = False

    @override
    def process(self, ctx: TickContext[EventWorld], /) -> None:
        if ctx.tick >= self.arrives_at:
            ctx.emit(Arrive())
            ctx.log(f"{self.lira.name} arrives at the moonlit clearing.")
            ctx.finish()
            return

        if self.encounter_finished or not self.forest.should_start_fight(ctx.rng, self.caution):
            return

        fight = self.forest.create_fight(self, ctx.tick)
        open_event(ctx, fight, ctx.tick + 1)
        ctx.suspend(self.entity_id, by=fight.event_id)
        ctx.emit(BeginFight(fight))
        ctx.log(f"{self.forest.name} sends {fight.wolf.name} against {self.lira.name}.")

    @override
    def on_resume(self, paused_for: int, /) -> None:
        self.encounter_finished = True
        self.arrives_at += paused_for


class LiraTurn(GameEventAction):
    __slots__ = ("lira", "wolf")

    def __init__(self, lira: Character, wolf: Character, event_id: EventId) -> None:
        super().__init__(lira.entity_id, wolf.entity_id, event_id)
        self.lira = lira
        self.wolf = wolf

    @override
    def decide(self, ctx: DecisionContext, /) -> FightIntent:
        del ctx
        if self.lira.hp * 2 < self.lira.max_hp and self.lira.potions > 0:
            return DodgeAndHeal()
        return Attack()


class WolfTurn(GameEventAction):
    __slots__ = ("lira", "wolf")

    def __init__(self, wolf: Character, lira: Character, event_id: EventId) -> None:
        super().__init__(wolf.entity_id, lira.entity_id, event_id)
        self.wolf = wolf
        self.lira = lira

    @override
    def decide(self, ctx: DecisionContext, /) -> FightIntent:
        if self.wolf.hp * 2 <= self.wolf.max_hp and ctx.rng.random() < 0.6:
            return Escape()
        return Attack()


class FightEvent(BaseEvent[EventWorld]):
    """Game-owned combat rules for one simultaneous round."""

    __slots__ = ("lira", "wolf")

    def __init__(
        self,
        event_id: EventId,
        children: tuple[LiraTurn, WolfTurn],
        lira: Character,
        wolf: Character,
    ) -> None:
        super().__init__(event_id, children)
        self.lira = lira
        self.wolf = wolf

    @override
    def resolve(
        self,
        ctx: ResolutionContext,
        intents: tuple[IntentRecord, ...],
        /,
    ) -> Outcome[EventWorld]:
        chosen = {record.entity_id: _fight_intent(record.intent) for record in intents}
        lira_intent = chosen[self.lira.entity_id]
        wolf_intent = chosen[self.wolf.entity_id]

        if isinstance(lira_intent, Escape):
            return Outcome.end(
                effects=(FinishFight(self, "Lira escaped"),),
                log=(f"{self.lira.name} escapes from the fight.",),
            )
        if isinstance(wolf_intent, Escape):
            return Outcome.end(
                effects=(FinishFight(self, "the wolf escaped"),),
                log=(f"{self.wolf.name.capitalize()} escapes into the trees.",),
            )

        effects: list[ChangeHealth | DrinkPotion] = []
        lines: list[str] = []
        if isinstance(lira_intent, DodgeAndHeal):
            effects.append(DrinkPotion(self.lira, healing=5))
            lines.append(f"{self.lira.name} drinks a potion and tries to dodge.")
        elif _hits(ctx.rng, self.lira, self.wolf, dodging=False):
            effects.append(ChangeHealth(self.wolf, -self.lira.damage))
            lines.append(f"{self.lira.name} strikes {self.wolf.name}.")
        else:
            lines.append(f"{self.lira.name} misses {self.wolf.name}.")

        if _hits(
            ctx.rng,
            self.wolf,
            self.lira,
            dodging=isinstance(lira_intent, DodgeAndHeal),
        ):
            effects.append(ChangeHealth(self.lira, -self.wolf.damage))
            lines.append(f"{self.wolf.name.capitalize()} bites {self.lira.name}.")
        else:
            lines.append(f"{self.lira.name} avoids {self.wolf.name}'s bite.")

        return Outcome.continue_with(
            LiraTurn(self.lira, self.wolf, self.event_id),
            WolfTurn(self.wolf, self.lira, self.event_id),
            due_tick=ctx.tick + 1,
            effects=effects,
            log=lines,
        )


class FightFinalizer:
    """Game-owned death policy, run after a round's effects."""

    def finalize(self, world: EventWorld, ctx: FinalizationContext[EventWorld], /) -> None:
        fight = world.active_fight
        if fight is None:
            return

        lira_dead = fight.lira.hp == 0
        wolf_dead = fight.wolf.hp == 0
        if not lira_dead and not wolf_dead:
            return

        if lira_dead:
            ctx.delete_owned_by(fight.lira.entity_id)
        if wolf_dead:
            ctx.delete_owned_by(fight.wolf.entity_id)

        if lira_dead and wolf_dead:
            result = "both combatants died"
            line = f"{fight.lira.name} and {fight.wolf.name} fall together."
        elif lira_dead:
            result = "Lira died"
            line = f"{fight.lira.name} falls in the forest."
        else:
            result = "the wolf died"
            line = f"{fight.wolf.name.capitalize()} falls, and the path is clear."

        ctx.emit(FinishFight(fight, result))
        ctx.log(line)
        end_event(ctx, fight.event_id)


def _fight_intent(value: object) -> FightIntent:
    if isinstance(value, (Attack, DodgeAndHeal, Escape)):
        return value
    raise TypeError(f"unknown fight Intent: {type(value).__qualname__}")


def _hits(rng: Rng, attacker: Character, defender: Character, *, dodging: bool) -> bool:
    health_fraction = defender.hp / defender.max_hp
    weakened_bonus = (1.0 - health_fraction) * 0.15
    agility_difference = (attacker.agility - defender.agility) * 0.06
    dodge_penalty = 0.3 if dodging else 0.0
    probability = 0.55 + weakened_bonus + agility_difference - dodge_penalty
    probability = min(0.9, max(0.1, probability))
    return rng.random() < probability


@dataclass(frozen=True, slots=True)
class EventworldResult:
    ticks: int
    seed: int
    lira_hp: int
    arrived: bool
    fights_started: int
    fights_ended: int
    fight_result: str | None
    journal: tuple[tuple[Tick, str], ...]

    @property
    def journal_hash(self) -> str:
        digest = sha256()
        for tick, line in self.journal:
            digest.update(f"{tick}\0{line}\n".encode())
        return digest.hexdigest()


def run_eventworld(
    *,
    ticks: int = _DEFAULT_TICKS,
    seed: int = _DEFAULT_SEED,
) -> EventworldResult:
    """Run one deterministic forest encounter for exactly `ticks` ticks."""
    if type(ticks) is not int:
        raise TypeError("ticks must be an int, not bool")
    if ticks <= 0:
        raise ValueError("ticks must be greater than zero")

    lira = Character(
        _LIRA_ID,
        "Lira",
        hp=12,
        max_hp=12,
        agility=6,
        damage=4,
        potions=1,
    )
    world = EventWorld(lira)
    forest = Forest(_FOREST_ID, "The haunted forest", danger=0.9)
    walk = Walk(lira, forest, arrives_at=6, caution=0.2)
    store = EventMemoryStore(
        world,
        seed,
        initial_actions=((walk, None),),
    )
    simulation = Simulation(
        store,
        events=Events(store),
        finalizer=FightFinalizer(),
    )

    simulation.run(ManualDriver(until_tick=ticks - 1))
    return EventworldResult(
        ticks=ticks,
        seed=seed,
        lira_hp=lira.hp,
        arrived=world.arrived,
        fights_started=world.fights_started,
        fights_ended=world.fights_ended,
        fight_result=world.last_fight_result,
        journal=store.logs,
    )
