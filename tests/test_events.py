import logging
import os
import subprocess
import sys
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import override

import pytest

from foliot import (
    Active,
    BaseAction,
    Bound,
    EntityId,
    FinalizationContext,
    Simulation,
    Store,
    Suspended,
    SuspensionId,
    Tick,
    TickContext,
    TickFinalizer,
    Txn,
    Unbound,
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
    EventStore,
    EventTxn,
    IntentRecord,
    Outcome,
    ResolutionContext,
    end_event,
    open_event,
)


@dataclass(slots=True)
class World:
    hp: dict[EntityId, int]


@dataclass(frozen=True, slots=True)
class Damage:
    entity_id: EntityId
    amount: int

    def apply(self, world: World, /) -> None:
        world.hp[self.entity_id] -= self.amount


class RaisingEffect:
    def apply(self, world: World, /) -> None:
        del world
        raise RuntimeError("Event effect failed")


@dataclass(frozen=True, slots=True)
class Choice:
    name: str


@dataclass(frozen=True, slots=True)
class Wolf:
    entity_id: EntityId
    name: str


class Turn(EventAction[World]):
    __slots__ = ("choice", "fail", "rolls")

    def __init__(
        self,
        entity_id: EntityId,
        event_id: EventId,
        choice: str,
        *,
        fail: bool = False,
        rolls: list[float] | None = None,
    ) -> None:
        super().__init__(entity_id, event_id)
        self.choice = choice
        self.fail = fail
        self.rolls = rolls

    @override
    def decide(self, ctx: DecisionContext, /) -> object:
        if self.rolls is not None:
            self.rolls.append(ctx.rng.random())
        if self.fail:
            raise RuntimeError("turn failed")
        return Choice(self.choice)


class FightEvent(BaseEvent[World]):
    __slots__ = (
        "damage",
        "ending",
        "next_due_offset",
        "raise_on_resolve",
        "raising_effect",
        "resolution_rolls",
        "wolf",
    )

    def __init__(
        self,
        event_id: EventId,
        children: tuple[Turn, ...],
        wolf: Wolf,
        *,
        ending: bool,
        damage: int = 1,
        next_due_offset: int = 2,
        raise_on_resolve: bool = False,
        raising_effect: bool = False,
        resolution_rolls: list[float] | None = None,
    ) -> None:
        super().__init__(event_id, children)
        self.wolf = wolf
        self.ending = ending
        self.damage = damage
        self.next_due_offset = next_due_offset
        self.raise_on_resolve = raise_on_resolve
        self.raising_effect = raising_effect
        self.resolution_rolls = resolution_rolls

    @override
    def resolve(
        self,
        ctx: ResolutionContext,
        intents: tuple[IntentRecord, ...],
        /,
    ) -> Outcome[World]:
        assert [record.entity_id for record in intents] == [
            child.entity_id for child in self.children
        ]
        if self.raise_on_resolve:
            raise RuntimeError("resolver failed")
        if self.resolution_rolls is not None:
            self.resolution_rolls.append(ctx.rng.random())

        effects = (
            (RaisingEffect(),) if self.raising_effect else (Damage(EntityId("lira"), self.damage),)
        )
        if self.ending:
            return Outcome.end(effects=effects, log=("The wolf escapes.",))
        next_children = tuple(
            Turn(child.entity_id, self.event_id, "next") for child in self.children
        )
        return Outcome.continue_with(
            *next_children,
            due_tick=ctx.tick + self.next_due_offset,
            effects=effects,
            log=("The fight continues.",),
        )


class OpenFight(BaseAction[World]):
    __slots__ = ("event", "fail_during_apply", "should_suspend")

    def __init__(
        self,
        event: FightEvent,
        *,
        should_suspend: bool = True,
        fail_during_apply: bool = False,
    ) -> None:
        super().__init__(EntityId("lira"), suspendable=True)
        self.event = event
        self.should_suspend = should_suspend
        self.fail_during_apply = fail_during_apply

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        open_event(ctx, self.event, ctx.tick + 1)
        if self.should_suspend:
            ctx.suspend(self.entity_id, by=self.event.event_id)
        if self.fail_during_apply:
            ctx.emit(RaisingEffect())


class OrdinaryDamage(BaseAction[World]):
    __slots__ = ("amount", "successor")

    def __init__(self, amount: int, successor: BaseAction[World] | None = None) -> None:
        super().__init__(EntityId("lira"), suspendable=False)
        self.amount = amount
        self.successor = successor

    @override
    def process(self, ctx: TickContext[World], /) -> None:
        ctx.emit(Damage(self.entity_id, self.amount))
        if self.successor is not None:
            ctx.schedule(self.successor, ctx.tick + 1)


class NoOp(BaseAction[World]):
    @override
    def process(self, ctx: TickContext[World], /) -> None:
        del ctx


class ObserveHpEvent(FightEvent):
    __slots__ = ("world",)

    def __init__(
        self,
        event_id: EventId,
        children: tuple[Turn, ...],
        wolf: Wolf,
        world: World,
    ) -> None:
        super().__init__(event_id, children, wolf, ending=True, damage=0)
        self.world = world

    @override
    def resolve(
        self,
        ctx: ResolutionContext,
        intents: tuple[IntentRecord, ...],
        /,
    ) -> Outcome[World]:
        del ctx, intents
        hp = self.world.hp[EntityId("lira")]
        return Outcome.end(log=(f"resolver saw {hp} HP",))


class DeathFinalizer:
    __slots__ = ("event_id",)

    def __init__(self, event_id: EventId | None = None) -> None:
        self.event_id = event_id

    def finalize(self, world: World, ctx: FinalizationContext[World], /) -> None:
        if world.hp[EntityId("lira")] > 0:
            return
        ctx.delete_owned_by(EntityId("lira"))
        if self.event_id is not None:
            end_event(ctx, self.event_id)
        ctx.log("Lira died.")


class RaisingFinalizer:
    __slots__ = ("successor",)

    def __init__(self, successor: BaseAction[World]) -> None:
        self.successor = successor

    def finalize(self, world: World, ctx: FinalizationContext[World], /) -> None:
        del world
        ctx.schedule(self.successor, ctx.tick + 1)
        ctx.log("must roll back")
        raise RuntimeError("finalizer failed")


class EndWithoutEvents:
    def finalize(self, world: World, ctx: FinalizationContext[World], /) -> None:
        del world
        end_event(ctx, EventId("missing"))


class ReverseEventStore:
    __slots__ = ("inner",)

    def __init__(self, inner: EventMemoryStore[World]) -> None:
        self.inner = inner

    @property
    def world_seed(self) -> int:
        return self.inner.world_seed

    def current_tick(self) -> Tick:
        return self.inner.current_tick()

    def due(self, tick: Tick, /) -> tuple[BaseAction[World], ...]:
        return tuple(reversed(self.inner.due(tick)))

    def event(self, event_id: EventId, /) -> BaseEvent[World] | None:
        return self.inner.event(event_id)

    def tick_transaction(self, tick: Tick, /) -> AbstractContextManager[Txn[World]]:
        return self.inner.tick_transaction(tick)


def accept_store(store: Store[World]) -> None:
    del store


def accept_event_store(store: EventStore[World]) -> None:
    del store


def accept_finalizer(finalizer: TickFinalizer[World]) -> None:
    del finalizer


def make_fight(
    *,
    ending: bool,
    damage: int = 1,
    failing_wolf: bool = False,
    raise_on_resolve: bool = False,
    raising_effect: bool = False,
    lira_rolls: list[float] | None = None,
    resolution_rolls: list[float] | None = None,
) -> tuple[FightEvent, tuple[Turn, Turn]]:
    event_id = EventId("fight-1")
    wolf = Wolf(EntityId("wolf-1"), "wolf")
    children = (
        Turn(EntityId("lira"), event_id, "attack", rolls=lira_rolls),
        Turn(wolf.entity_id, event_id, "bite", fail=failing_wolf),
    )
    return (
        FightEvent(
            event_id,
            children,
            wolf,
            ending=ending,
            damage=damage,
            raise_on_resolve=raise_on_resolve,
            raising_effect=raising_effect,
            resolution_rolls=resolution_rolls,
        ),
        children,
    )


def open_fight(
    event: FightEvent,
    *,
    seed: int = 1,
    hp: int = 10,
    finalizer: TickFinalizer[World] | None = None,
) -> tuple[EventMemoryStore[World], Simulation[World], OpenFight]:
    opener = OpenFight(event)
    store = EventMemoryStore(
        World({EntityId("lira"): hp}),
        seed,
        initial_actions=((opener, None),),
    )
    simulation = Simulation(store, events=Events(store), finalizer=finalizer)
    simulation.process_tick()
    return store, simulation, opener


def test_event_memory_store_should_satisfy_both_store_protocols() -> None:
    store = EventMemoryStore(World({}), 1)

    accept_store(store)
    accept_event_store(store)
    accept_finalizer(DeathFinalizer())

    with store.tick_transaction(0) as txn:
        assert isinstance(txn, EventTxn)


def test_open_event_should_persist_bind_children_and_suspend_explicitly() -> None:
    event, children = make_fight(ending=False)

    store, _, opener = open_fight(event)

    assert store.event(event.event_id) is event
    assert event.children == children
    assert children[0].binding == Bound(2, Active(1))
    assert children[1].binding == Bound(3, Active(1))
    assert opener.binding == Bound(
        1,
        Suspended(0, SuspensionId(str(event.event_id)), None),
    )
    assert store.due(1) == children


def test_opening_without_suspension_should_leave_existing_activity_active() -> None:
    event, children = make_fight(ending=False)
    opener = OpenFight(event, should_suspend=False)
    store = EventMemoryStore(
        World({EntityId("lira"): 10}),
        1,
        initial_actions=((opener, None),),
    )

    Simulation(store, events=Events(store)).process_tick()

    assert opener.binding == Bound(1, Active(None))
    assert store.due(1) == (opener, *children)


def test_opening_failure_should_roll_back_event_children_suspension_and_clock() -> None:
    event, children = make_fight(ending=False)
    opener = OpenFight(event, fail_during_apply=True)
    store = EventMemoryStore(
        World({EntityId("lira"): 10}),
        1,
        initial_actions=((opener, None),),
    )

    with pytest.raises(RuntimeError, match="Event effect failed"):
        Simulation(store, events=Events(store)).process_tick()

    assert store.current_tick() == 0
    assert store.event(event.event_id) is None
    assert opener.binding == Bound(1, Active(None))
    assert all(child.binding == Unbound() for child in children)
    assert store.due(0) == (opener,)


def test_complete_round_should_replace_children_with_fresh_sequences_and_due_tick() -> None:
    event, old_children = make_fight(ending=False)
    store, simulation, _ = open_fight(event)

    simulation.process_tick()

    new_children = event.children
    assert new_children != old_children
    assert [child.seq for child in new_children] == [4, 5]
    assert all(child.state == Active(3) for child in new_children)
    assert store.due(2) == ()
    assert store.due(3) == new_children
    assert store.logs == ((1, "The fight continues."),)
    assert store.event(event.event_id) is event


def test_ending_round_should_remove_event_payload_children_and_resume_its_activity() -> None:
    event, children = make_fight(ending=True)
    store, simulation, opener = open_fight(event)

    simulation.process_tick()

    assert store.event(event.event_id) is None
    assert store.due(2) == (opener,)
    assert opener.binding == Bound(1, Active(None))
    assert all(child not in store.due(100) for child in children)
    assert store.logs == ((1, "The wolf escapes."),)


def test_incomplete_round_should_keep_same_children_and_retry_fresh_next_tick() -> None:
    lira_rolls: list[float] = []
    event, children = make_fight(
        ending=False,
        failing_wolf=True,
        lira_rolls=lira_rolls,
    )
    store, simulation, _ = open_fight(event)

    simulation.process_tick()
    simulation.process_tick()

    assert event.children == children
    assert [child.seq for child in children] == [2, 3]
    assert store.due(3) == children
    assert store.logs == ()
    assert len(lira_rolls) == 2
    assert lira_rolls[0] != lira_rolls[1]


def test_resolver_failure_should_be_visible_and_leave_event_pending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event, children = make_fight(ending=False, raise_on_resolve=True)
    store, simulation, _ = open_fight(event)
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    simulation.process_tick()

    assert store.event(event.event_id) is event
    assert event.children == children
    assert store.due(2) == children
    assert store.current_tick() == 2
    assert "event_id=fight-1" in caplog.text
    assert "resolver=FightEvent" in caplog.text
    assert "resolver failed" in caplog.text


def test_resolver_failure_should_not_block_unrelated_ordinary_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event, children = make_fight(ending=False, raise_on_resolve=True)
    opener = OpenFight(event)
    unrelated = OrdinaryDamage(1)
    world = World({EntityId("lira"): 10})
    store = EventMemoryStore(
        world,
        1,
        initial_actions=((opener, None), (unrelated, 1)),
    )
    simulation = Simulation(store, events=Events(store))
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    simulation.process_tick()
    simulation.process_tick()

    assert store.current_tick() == 2
    assert store.event(event.event_id) is event
    assert store.due(2) == children
    assert store.logs == ()
    assert world.hp[EntityId("lira")] == 9
    assert "resolver failed" in caplog.text


def test_event_effect_failure_should_abort_tick_and_keep_round_pending() -> None:
    event, children = make_fight(ending=True, raising_effect=True)
    store, simulation, _ = open_fight(event)

    with pytest.raises(RuntimeError, match="Event effect failed"):
        simulation.process_tick()

    assert store.current_tick() == 1
    assert store.event(event.event_id) is event
    assert store.due(1) == children
    assert store.logs == ()


def test_resolution_rng_should_replay_and_be_separate_from_participant_rng() -> None:
    first_action_rolls: list[float] = []
    first_event_rolls: list[float] = []
    first, _ = make_fight(
        ending=True,
        lira_rolls=first_action_rolls,
        resolution_rolls=first_event_rolls,
    )
    _, first_simulation, _ = open_fight(first, seed=987)
    first_simulation.process_tick()

    second_action_rolls: list[float] = []
    second_event_rolls: list[float] = []
    second, _ = make_fight(
        ending=True,
        lira_rolls=second_action_rolls,
        resolution_rolls=second_event_rolls,
    )
    _, second_simulation, _ = open_fight(second, seed=987)
    second_simulation.process_tick()

    assert first_event_rolls == second_event_rolls
    assert first_action_rolls == second_action_rolls
    assert first_event_rolls != first_action_rolls


def test_reversing_due_order_should_not_change_event_result_or_journal() -> None:
    normal_event, _ = make_fight(ending=True, damage=3)
    normal_opener = OpenFight(normal_event)
    normal_world = World({EntityId("lira"): 10})
    normal_store = EventMemoryStore(
        normal_world,
        919,
        initial_actions=((normal_opener, None),),
    )

    reverse_event, _ = make_fight(ending=True, damage=3)
    reverse_opener = OpenFight(reverse_event)
    reverse_world = World({EntityId("lira"): 10})
    reverse_inner = EventMemoryStore(
        reverse_world,
        919,
        initial_actions=((reverse_opener, None),),
    )
    reverse_store = ReverseEventStore(reverse_inner)
    accept_store(reverse_store)
    accept_event_store(reverse_store)

    normal_simulation = Simulation(normal_store, events=Events(normal_store))
    reverse_simulation = Simulation(reverse_store, events=Events(reverse_store))
    normal_simulation.process_tick()
    reverse_simulation.process_tick()
    normal_simulation.process_tick()
    reverse_simulation.process_tick()

    assert normal_world == reverse_world
    assert normal_store.logs == reverse_inner.logs
    assert normal_store.current_tick() == reverse_inner.current_tick()
    assert normal_store.due(2) == (normal_opener,)
    assert reverse_inner.due(2) == (reverse_opener,)


def test_resolver_should_read_tick_start_hp_before_same_tick_poison_applies() -> None:
    world = World({EntityId("lira"): 6})
    event_id = EventId("hp-fight")
    wolf = Wolf(EntityId("wolf-hp"), "wolf")
    children = (
        Turn(EntityId("lira"), event_id, "dodge"),
        Turn(wolf.entity_id, event_id, "bite"),
    )
    event = ObserveHpEvent(event_id, children, wolf, world)
    opener = OpenFight(event)
    poison = OrdinaryDamage(2)
    store = EventMemoryStore(
        world,
        1,
        initial_actions=((opener, None), (poison, 1)),
    )
    simulation = Simulation(store, events=Events(store))

    simulation.process_tick()
    simulation.process_tick()

    assert store.logs == ((1, "resolver saw 6 HP"),)
    assert world.hp[EntityId("lira")] == 4


def test_finalizer_should_see_effects_and_delete_newly_scheduled_owned_action() -> None:
    successor = NoOp(EntityId("lira"), suspendable=True)
    damage = OrdinaryDamage(10, successor)
    store = EventMemoryStore(
        World({EntityId("lira"): 10}),
        1,
        initial_actions=((damage, 0),),
    )

    Simulation(store, finalizer=DeathFinalizer()).process_tick()

    assert store.current_tick() == 1
    assert store.due(100) == ()
    assert successor.binding == Bound(2, Active(1))
    assert store.logs == ((0, "Lira died."),)


def test_finalizer_failure_should_abort_every_collected_final_command() -> None:
    successor = NoOp(EntityId("lira"), suspendable=False)
    source = NoOp(EntityId("source"), suspendable=False)
    store = EventMemoryStore(
        World({EntityId("lira"): 10}),
        1,
        initial_actions=((source, None),),
    )

    with pytest.raises(RuntimeError, match="finalizer failed"):
        Simulation(store, finalizer=RaisingFinalizer(successor)).process_tick()

    assert store.current_tick() == 0
    assert store.logs == ()
    assert store.due(0) == (source,)
    assert successor.binding == Unbound()


def test_finalizer_end_event_should_cancel_a_continuation_created_that_tick() -> None:
    event, old_children = make_fight(ending=False, damage=10)
    finalizer = DeathFinalizer(event.event_id)
    store, simulation, opener = open_fight(event, hp=10, finalizer=finalizer)

    simulation.process_tick()

    assert store.event(event.event_id) is None
    assert store.due(100) == ()
    assert opener.binding == Bound(1, Active(None))
    assert all(child not in store.due(100) for child in old_children)
    assert store.logs == (
        (1, "The fight continues."),
        (1, "Lira died."),
    )


def test_event_api_without_event_configuration_should_fail_clearly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event, _ = make_fight(ending=False)
    opener = OpenFight(event)
    from foliot import MemoryStore

    store = MemoryStore(
        World({EntityId("lira"): 10}),
        1,
        initial_actions=((opener, None),),
    )
    caplog.set_level(logging.ERROR, logger="foliot.engine")

    Simulation(store).process_tick()

    assert "needs Simulation(..., events=Events(event_store))" in caplog.text
    assert opener.binding == Bound(1, Active(None))
    assert store.current_tick() == 1


def test_end_event_without_event_configuration_should_abort_finalization() -> None:
    from foliot import MemoryStore

    store = MemoryStore(World({}), 1)

    with pytest.raises(RuntimeError, match="needs Simulation"):
        Simulation(store, finalizer=EndWithoutEvents()).process_tick()

    assert store.current_tick() == 0


def test_event_opt_in_should_not_change_an_ordinary_action_tick() -> None:
    ordinary_world = World({EntityId("lira"): 10})
    event_world = World({EntityId("lira"): 10})
    ordinary_action = OrdinaryDamage(2)
    event_action = OrdinaryDamage(2)
    from foliot import MemoryStore

    ordinary_store = MemoryStore(
        ordinary_world,
        44,
        initial_actions=((ordinary_action, 0),),
    )
    event_store = EventMemoryStore(
        event_world,
        44,
        initial_actions=((event_action, 0),),
    )

    Simulation(ordinary_store).process_tick()
    Simulation(event_store, events=Events(event_store)).process_tick()

    assert ordinary_world == event_world
    assert ordinary_store.logs == event_store.logs
    assert ordinary_store.current_tick() == event_store.current_tick()
    assert ordinary_store.due(1) == event_store.due(1) == ()


def test_importing_layer_one_should_not_import_the_event_package() -> None:
    program = """
import sys
import foliot
print("foliot.events" in sys.modules)
"""

    assert subprocess.check_output([sys.executable, "-c", program], text=True) == "False\n"


def test_id_templates_should_be_stable_typed_and_length_framed() -> None:
    source = NoOp(EntityId("source"), suspendable=False)
    EventMemoryStore(World({}), 1, initial_actions=((source, None),))
    events = EventIdTemplate("zpg.fight")
    wolves = EntityIdTemplate("zpg.fight.wolf")

    event_id = events.from_action(source, tick=50, ordinal=0)
    wolf_id = wolves.from_event(event_id, ordinal=0)

    assert event_id == EventId("event-v1:76510fb7c9280abdfd4f9df68e68bc77")
    assert wolf_id == EntityId("entity-v1:81e9a4da8d4b231b3fceed6f269a7111")
    assert events.from_action(source, tick=50, ordinal=1) != event_id
    assert (
        EntityIdTemplate("zpg.fight.wol").from_event(EventId(f"f{event_id}"), ordinal=0) != wolf_id
    )
    assert EventIdTemplate("бой").from_action(source, tick=50, ordinal=0) != event_id
    assert EntityIdTemplate("ab").from_event(EventId("c"), ordinal=0) != EntityIdTemplate(
        "a"
    ).from_event(EventId("bc"), ordinal=0)


def test_id_templates_should_ignore_python_hash_randomization_across_processes() -> None:
    program = """
from typing import override
from foliot import BaseAction, EntityId, TickContext
from foliot.events import EventIdTemplate, EventMemoryStore

class Source(BaseAction[dict[str, int]]):
    @override
    def process(self, ctx: TickContext[dict[str, int]], /) -> None:
        del ctx

source = Source(EntityId("source"), suspendable=False)
EventMemoryStore({}, 1, initial_actions=((source, None),))
print(EventIdTemplate("zpg.fight").from_action(source, tick=50, ordinal=0))
"""

    def output_with_hash_seed(hash_seed: str) -> str:
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        return subprocess.check_output(
            [sys.executable, "-c", program],
            env=environment,
            text=True,
        )

    assert output_with_hash_seed("1") == output_with_hash_seed("987654")


def test_new_event_objects_should_start_complete_and_unbound() -> None:
    event_id = EventId("fight-shape")
    child = Turn(EntityId("lira"), event_id, "wait")
    event = FightEvent(event_id, (child,), Wolf(EntityId("wolf"), "wolf"), ending=True)

    assert event.event_id == event_id
    assert event.children == (child,)
    assert child.binding == Unbound()


def test_event_children_should_be_complete_matching_distinct_and_fresh() -> None:
    event_id = EventId("fight-validation")
    wolf = Wolf(EntityId("wolf"), "wolf")

    with pytest.raises(ValueError, match="at least one child"):
        FightEvent(event_id, (), wolf, ending=True)

    wrong_event_child = Turn(EntityId("lira"), EventId("another-event"), "wait")
    with pytest.raises(ValueError, match="Event's id"):
        FightEvent(event_id, (wrong_event_child,), wolf, ending=True)

    repeated_child = Turn(EntityId("lira"), event_id, "wait")
    with pytest.raises(ValueError, match="cannot appear twice"):
        FightEvent(event_id, (repeated_child, repeated_child), wolf, ending=True)

    bound_child = Turn(EntityId("lira"), event_id, "wait")
    event = FightEvent(event_id, (bound_child,), wolf, ending=True)
    store = EventMemoryStore(
        World({EntityId("lira"): 10}),
        1,
        initial_actions=((bound_child, None),),
    )
    with pytest.raises(RuntimeError, match="unbound child"):
        Events(store).validate_open(event, 1, 0)


def test_simulation_should_reject_events_connected_to_a_different_store() -> None:
    first = EventMemoryStore(World({}), 1)
    second = EventMemoryStore(World({}), 1)

    with pytest.raises(ValueError, match="same store"):
        Simulation(first, events=Events(second))
