"""The smallest complete zero-player world built on foliot Layer 1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from hashlib import sha256
from typing import override

from foliot import (
    BaseAction,
    EntityId,
    ManualDriver,
    MemoryStore,
    Rng,
    Simulation,
    Tick,
    TickContext,
)

__all__ = ["TinyworldResult", "run_tinyworld"]

_DEFAULT_SEED = 20_260_904
_DEFAULT_TICKS = 1_000_000
_LIRA_ID = EntityId("lira")
_FOREST_ID = EntityId("haunted-forest")
_FOREST_EDGE_ID = EntityId("forest-edge")
_MOONLIT_CLEARING_ID = EntityId("moonlit-clearing")
_TRAVEL_TICKS = 20
_POISON_DAMAGE = 2
_POISON_INTERVAL = 4
_POISON_DURATION = 10
_POTION_HEALING = 20


@dataclass(slots=True)
class Character:
    """Mutable because game effects change HP during the apply phase."""

    entity_id: EntityId
    name: str
    hp: int
    max_hp: int
    perception: float


@dataclass(slots=True)
class TinyWorld:
    """Game-owned state. Foliot never looks inside it."""

    characters: dict[EntityId, Character]
    clearings: dict[EntityId, Clearing]
    forests: dict[EntityId, Forest]
    pathing: Pathing


class GameAction(BaseAction[TinyWorld]):
    """Tinyworld's mandatory directed action, layered on foliot's lifecycle."""

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


@dataclass(frozen=True, slots=True)
class Clearing:
    """A game-owned point of interest, without navigation policy."""

    entity_id: EntityId
    name: str


class Response(Enum):
    NOTHING = auto()
    POISONED = auto()


@dataclass(frozen=True, slots=True)
class Forest:
    entity_id: EntityId
    name: str
    hauntedness: float

    def determine_response(self, rng: Rng, walker_perception: float) -> Response:
        poison_probability = self.hauntedness * (1.0 - walker_perception)
        if rng.random() < poison_probability:
            return Response.POISONED
        return Response.NOTHING


@dataclass(frozen=True, slots=True)
class Pathing:
    """Tinyworld's deliberately tiny game-specific navigation mock."""

    nearest_pois: dict[EntityId, EntityId]
    routes: dict[tuple[EntityId, EntityId], EntityId]

    def find_nearest_poi(self, current_place_id: EntityId, /) -> EntityId:
        return self.nearest_pois[current_place_id]

    def between(self, origin_id: EntityId, destination_id: EntityId, /) -> EntityId:
        return self.routes[(origin_id, destination_id)]


@dataclass(frozen=True, slots=True)
class Damage:
    entity_id: EntityId
    amount: int

    def apply(self, world: TinyWorld, /) -> None:
        character = world.characters[self.entity_id]
        character.hp = max(0, character.hp - self.amount)


@dataclass(frozen=True, slots=True)
class Healing:
    entity_id: EntityId
    amount: int

    def apply(self, world: TinyWorld, /) -> None:
        character = world.characters[self.entity_id]
        character.hp = min(character.max_hp, character.hp + self.amount)


class Walk(GameAction):
    """Recurring journey whose environment gets one response roll per tick."""

    __slots__ = (
        "arrives_at",
        "character",
        "destination_id",
        "world",
    )

    def __init__(
        self,
        character: Character,
        world: TinyWorld,
        *,
        target_id: EntityId,
        destination_id: EntityId,
        arrives_at: Tick,
    ) -> None:
        super().__init__(character.entity_id, target_id, suspendable=True)
        self.character = character
        self.world = world
        self.destination_id = destination_id
        self.arrives_at = arrives_at

    @override
    def process(self, ctx: TickContext[TinyWorld], /) -> None:
        if ctx.tick >= self.arrives_at:
            self._arrive(ctx)
            return

        forest = self.world.forests[self.target_id]
        response = forest.determine_response(ctx.rng, self.character.perception)
        if response is Response.POISONED:
            ctx.log(f"The haunted pines poison {self.character.name}.")
            ctx.schedule(
                Poison(
                    self.character,
                    damage=_POISON_DAMAGE,
                    interval=_POISON_INTERVAL,
                    expires_at=ctx.tick + _POISON_DURATION,
                ),
                ctx.tick + 1,
            )

    def _arrive(self, ctx: TickContext[TinyWorld]) -> None:
        destination = self.world.clearings[self.destination_id]
        ctx.log(f"{self.character.name} arrives at the {destination.name}.")
        ctx.schedule(
            Rest(
                self.character,
                self.world,
                target_id=self.destination_id,
            ),
            ctx.tick + 1,
        )
        ctx.finish()


class Poison(GameAction):
    """Scheduled effect that keeps its permanent seq while rescheduling."""

    __slots__ = ("character", "damage", "expires_at", "interval")

    def __init__(
        self,
        character: Character,
        *,
        damage: int,
        interval: int,
        expires_at: Tick,
    ) -> None:
        super().__init__(
            character.entity_id,
            character.entity_id,
            suspendable=False,
        )
        self.character = character
        self.damage = damage
        self.interval = interval
        self.expires_at = expires_at

    @override
    def process(self, ctx: TickContext[TinyWorld], /) -> None:
        ctx.emit(Damage(self.character.entity_id, self.damage))
        ctx.log(f"Poison drains {self.damage} HP from {self.character.name}.")

        next_strike = ctx.tick + self.interval
        if next_strike < self.expires_at:
            ctx.schedule(self, next_strike)
        else:
            ctx.log(f"The poison affecting {self.character.name} passes.")


class Rest(GameAction):
    """One-shot action against a place that decides what happens there."""

    __slots__ = ("character", "world")

    def __init__(
        self,
        character: Character,
        world: TinyWorld,
        *,
        target_id: EntityId,
    ) -> None:
        super().__init__(character.entity_id, target_id, suspendable=False)
        self.character = character
        self.world = world

    @override
    def process(self, ctx: TickContext[TinyWorld], /) -> None:
        clearing = self.world.clearings[self.target_id]
        ctx.log(f"{self.character.name} rests in the {clearing.name}.")
        if self.target_id == _MOONLIT_CLEARING_ID and self.character.hp * 2 < self.character.max_hp:
            ctx.emit(Healing(self.character.entity_id, _POTION_HEALING))
            ctx.log(f"{self.character.name} drinks a potion.")

        destination_id = self.world.pathing.find_nearest_poi(self.target_id)
        route_id = self.world.pathing.between(self.target_id, destination_id)
        ctx.schedule(
            Walk(
                self.character,
                self.world,
                target_id=route_id,
                destination_id=destination_id,
                arrives_at=ctx.tick + 1 + _TRAVEL_TICKS,
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class TinyworldResult:
    ticks: int
    seed: int
    final_hp: int
    journal: tuple[tuple[Tick, str], ...]

    @property
    def journal_hash(self) -> str:
        digest = sha256()
        for tick, line in self.journal:
            digest.update(f"{tick}\0{line}\n".encode())
        return digest.hexdigest()

    @property
    def potions_drunk(self) -> int:
        return sum("drinks a potion" in line for _, line in self.journal)


def run_tinyworld(
    *,
    ticks: int = _DEFAULT_TICKS,
    seed: int = _DEFAULT_SEED,
) -> TinyworldResult:
    """Run Lira's world for exactly `ticks` logical ticks."""
    if type(ticks) is not int:
        raise TypeError("ticks must be an int, not bool")
    if ticks <= 0:
        raise ValueError("ticks must be greater than zero")

    lira = Character(_LIRA_ID, "Lira", hp=20, max_hp=20, perception=0.8)
    forest_edge = Clearing(
        _FOREST_EDGE_ID,
        "forest edge",
    )
    moonlit_clearing = Clearing(
        _MOONLIT_CLEARING_ID,
        "moonlit clearing",
    )
    forest = Forest(_FOREST_ID, "haunted forest", hauntedness=0.1)
    world = TinyWorld(
        characters={_LIRA_ID: lira},
        clearings={
            forest_edge.entity_id: forest_edge,
            moonlit_clearing.entity_id: moonlit_clearing,
        },
        forests={forest.entity_id: forest},
        pathing=Pathing(
            nearest_pois={
                _FOREST_EDGE_ID: _MOONLIT_CLEARING_ID,
                _MOONLIT_CLEARING_ID: _FOREST_EDGE_ID,
            },
            routes={
                (_FOREST_EDGE_ID, _MOONLIT_CLEARING_ID): _FOREST_ID,
                (_MOONLIT_CLEARING_ID, _FOREST_EDGE_ID): _FOREST_ID,
            },
        ),
    )
    first_walk = Walk(
        lira,
        world,
        target_id=_FOREST_ID,
        destination_id=_MOONLIT_CLEARING_ID,
        arrives_at=_TRAVEL_TICKS,
    )
    store = MemoryStore(
        world,
        seed,
        initial_actions=((first_walk, None),),
    )

    Simulation(store).run(ManualDriver(until_tick=ticks - 1))
    return TinyworldResult(
        ticks=ticks,
        seed=seed,
        final_hp=lira.hp,
        journal=store.logs,
    )
