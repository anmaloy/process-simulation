"""Phase 1 — domain model tests.

Acceptance bar (WORKFLOW.md P1): every entity constructs, a hand-built World
round-trips through JSON identical, and unset optional fields survive as
*absent* (not ``0``). The absence assertions are the load-bearing ones.
"""

import json

import pytest

from process_sim.model import (
    Category,
    Direction,
    EnergySource,
    Item,
    MachineDef,
    MachineNode,
    Pipe,
    PipeEnd,
    PipeEndRole,
    Recipe,
    SlotGroup,
    Stack,
    StorageSpec,
    System,
    SystemPort,
    World,
)


# --------------------------------------------------------------------------- #
# Construction — each dataclass builds with minimal args.
# --------------------------------------------------------------------------- #


def test_item_constructs_minimal() -> None:
    item = Item(id="iron", name="Iron", category=Category.SOLID)
    assert item.category is Category.SOLID
    assert item.eu is None  # not a fuel


def test_item_constructs_fuel() -> None:
    coal = Item(id="coal", name="Coal", category=Category.SOLID, eu=1000)
    assert coal.eu == 1000


def test_stack_constructs() -> None:
    assert Stack(item_id="iron", quantity=2).quantity == 2


def test_recipe_constructs_minimal() -> None:
    r = Recipe(id="smelt", time_seconds=10)
    assert r.inputs == [] and r.outputs == []


def test_slot_group_constructs() -> None:
    sg = SlotGroup(
        category=Category.SOLID, direction=Direction.IN, slots=4, stack_size=64
    )
    assert sg.slots * sg.stack_size == 256


def test_machine_def_constructs_minimal() -> None:
    d = MachineDef(id="furnace")
    assert d.energy_source is None and d.source_item_id is None
    assert d.output_item_id is None and d.storage is None
    assert d.efficiency is None and d.max_fluid_rate is None
    assert d.slots == [] and d.allowed_recipe_ids == []


def test_storage_spec_constructs() -> None:
    s = StorageSpec(category=Category.LIQUID, capacity=16000)
    d = MachineDef(id="tank", storage=s)
    assert d.storage is not None and d.storage.capacity == 16000


def test_machine_node_constructs_minimal() -> None:
    n = MachineNode(id="n1", machine_def_id="furnace")
    assert n.overclock == 1.0 and n.count == 1
    assert n.recipe_id is None and n.x is None and n.y is None


def test_pipe_end_constructs_minimal() -> None:
    e = PipeEnd(node_id="n1", role=PipeEndRole.OUT)
    assert e.insert_priority is None and e.extract_priority is None


def test_pipe_constructs_minimal() -> None:
    p = Pipe(
        id="p1",
        category=Category.SOLID,
        a=PipeEnd(node_id="n1", role=PipeEndRole.OUT),
        b=PipeEnd(node_id="n2", role=PipeEndRole.IN),
        movement_capacity=16,
    )
    assert p.overclock == 1.0 and p.storage_capacity is None


def test_system_constructs_minimal() -> None:
    s = System(id="s1")
    assert s.member_node_ids == [] and s.exposed_ports == []
    assert s.collapsed is False


def test_world_constructs_empty() -> None:
    w = World()
    assert w.items == [] and w.pipes == [] and w.systems == []


# --------------------------------------------------------------------------- #
# Serialization shape — camelCase keys, and absence of unset fields.
# --------------------------------------------------------------------------- #


def test_to_dict_uses_camel_case() -> None:
    r = Recipe(id="r", time_seconds=10)
    d = r.to_dict()
    assert "timeSeconds" in d
    assert "time_seconds" not in d


def test_unset_item_eu_is_absent_not_zero() -> None:
    d = Item(id="iron", name="Iron", category=Category.SOLID).to_dict()
    assert "eu" not in d


def test_zero_eu_is_distinct_from_unset() -> None:
    # eu = 0 is a real value and must be preserved, distinct from absent.
    d = Item(id="void", name="Void", category=Category.ENERGY, eu=0).to_dict()
    assert d["eu"] == 0
    assert Item.from_dict(d).eu == 0


def test_recipe_has_no_energy_keys() -> None:
    d = Recipe(id="r", time_seconds=5).to_dict()
    assert "energyConsumption" not in d
    assert "energyProduction" not in d


def test_unset_machine_def_energy_and_efficiency_absent() -> None:
    d = MachineDef(id="furnace").to_dict()
    # A passive def has no energy/converter/storage keys at all (unset ≠ 0).
    for key in (
        "energySource",
        "sourceItemId",
        "baseEnergyConsumption",
        "outputItemId",
        "efficiency",
        "maxFluidRate",
        "storage",
        "overclockMin",
        "overclockMax",
    ):
        assert key not in d


def test_unset_pipe_end_priorities_absent() -> None:
    # An out-only end carries no extract priority; an in-only end no insert.
    out_end = PipeEnd(node_id="n1", role=PipeEndRole.OUT, insert_priority=10).to_dict()
    assert "insertPriority" in out_end
    assert "extractPriority" not in out_end


def test_empty_collections_emit_as_lists() -> None:
    # Empty collections are not unset scalars — they serialize as [].
    d = World().to_dict()
    assert d["items"] == []
    assert d["pipes"] == []


# --------------------------------------------------------------------------- #
# Round-trip identity — per entity and for a full World (through json).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "entity",
    [
        Item(id="coal", name="Coal", category=Category.SOLID, eu=1000),
        Item(id="iron", name="Iron", category=Category.SOLID),
        Stack(item_id="iron", quantity=3),
        Recipe(
            id="smelt",
            time_seconds=10,
            name="Smelt Iron",
            inputs=[Stack("ore", 1)],
            outputs=[Stack("iron", 1)],
        ),
        SlotGroup(
            category=Category.LIQUID, direction=Direction.OUT, slots=2, stack_size=16000
        ),
        StorageSpec(category=Category.LIQUID, capacity=64000),
        MachineDef(
            id="boiler",
            name="Large Steam Boiler",
            energy_source=EnergySource.FUEL,
            source_item_id="coal",
            output_item_id="steam",
            efficiency=0.8,
            max_fluid_rate=256,
            allowed_recipe_ids=["burn_coal"],
            slots=[SlotGroup(Category.SOLID, Direction.IN, 1, 64)],
        ),
        MachineDef(
            id="tank",
            name="Steam Tank",
            storage=StorageSpec(category=Category.LIQUID, capacity=64000),
            slots=[SlotGroup(Category.LIQUID, Direction.IN, 1, 16000)],
        ),
        MachineNode(id="n1", machine_def_id="boiler", recipe_id="burn_coal", x=10, y=20),
        PipeEnd(node_id="n1", role=PipeEndRole.IO, insert_priority=0, extract_priority=5),
        Pipe(
            id="p1",
            category=Category.LIQUID,
            a=PipeEnd(node_id="n1", role=PipeEndRole.OUT, extract_priority=0),
            b=PipeEnd(node_id="n2", role=PipeEndRole.IN, insert_priority=10),
            movement_capacity=256,
            storage_capacity=64000,
            overclock=2,
        ),
        SystemPort(node_id="n1", port="out0", name="steam out"),
        System(
            id="s1",
            name="Power Block",
            member_node_ids=["n1", "n2"],
            exposed_ports=[SystemPort(node_id="n2", name="power")],
            collapsed=True,
            x=120.0,
            y=-40.0,
        ),
    ],
)
def test_entity_round_trip(entity: object) -> None:
    cls = type(entity)
    restored = cls.from_dict(entity.to_dict())  # type: ignore[attr-defined]
    assert restored == entity


def _sample_world() -> World:
    """A furnace→chest-ish chain exercising fuel/steam, pipes, and a system."""

    return World(
        name="Sample",
        items=[
            Item(id="coal", name="Coal", category=Category.SOLID, eu=1600),
            Item(id="ore", name="Iron Ore", category=Category.SOLID),
            Item(id="iron", name="Iron", category=Category.SOLID),
            Item(id="steam", name="Steam", category=Category.LIQUID),
        ],
        recipes=[
            Recipe(
                id="smelt",
                time_seconds=10,
                name="Smelt",
                inputs=[Stack("ore", 1)],
                outputs=[Stack("iron", 1)],
            ),
        ],
        machine_defs=[
            MachineDef(
                id="furnace",
                name="Furnace",
                energy_source=EnergySource.LIQUID,
                source_item_id="steam",
                base_energy_consumption=2,
                allowed_recipe_ids=["smelt"],
                slots=[
                    SlotGroup(Category.SOLID, Direction.IN, 1, 64),
                    SlotGroup(Category.SOLID, Direction.OUT, 1, 64),
                ],
                overclock_min=1,
                overclock_max=8,
            ),
            MachineDef(
                id="boiler",
                name="Large Steam Boiler",
                energy_source=EnergySource.FUEL,
                source_item_id="coal",
                output_item_id="steam",
                efficiency=0.8,
                max_fluid_rate=256,
            ),
        ],
        nodes=[
            MachineNode(id="n_furnace", machine_def_id="furnace", recipe_id="smelt", x=0, y=0),
            MachineNode(
                id="n_boiler",
                machine_def_id="boiler",
                overclock=1,
                x=200,
                y=0,
            ),
        ],
        pipes=[
            Pipe(
                id="p_steam",
                category=Category.LIQUID,
                a=PipeEnd(node_id="n_boiler", role=PipeEndRole.OUT, extract_priority=0),
                b=PipeEnd(node_id="n_furnace", role=PipeEndRole.IN, insert_priority=0),
                movement_capacity=256,
            ),
        ],
        systems=[
            System(
                id="sys_power",
                name="Power",
                member_node_ids=["n_boiler"],
                exposed_ports=[SystemPort(node_id="n_boiler", name="steam")],
            ),
        ],
    )


def test_world_round_trip_identical() -> None:
    world = _sample_world()
    assert World.from_dict(world.to_dict()) == world


def test_world_survives_json_text() -> None:
    world = _sample_world()
    text = json.dumps(world.to_dict())
    restored = World.from_dict(json.loads(text))
    assert restored == world


def test_world_serialization_is_idempotent() -> None:
    world = _sample_world()
    once = world.to_dict()
    twice = World.from_dict(once).to_dict()
    assert once == twice


def test_world_round_trip_preserves_absent_fields() -> None:
    world = _sample_world()
    d = world.to_dict()
    # The furnace is a consumer, not a converter: no efficiency / output / storage.
    furnace = next(m for m in d["machineDefs"] if m["id"] == "furnace")
    assert "efficiency" not in furnace
    assert "outputItemId" not in furnace
    assert "storage" not in furnace
    # Plain iron item is not a fuel; steam is a liquid, not a fuel either.
    iron = next(i for i in d["items"] if i["id"] == "iron")
    assert "eu" not in iron
    steam = next(i for i in d["items"] if i["id"] == "steam")
    assert "eu" not in steam
    # And those absences survive reconstruction.
    restored = World.from_dict(d)
    furnace_def = next(m for m in restored.machine_defs if m.id == "furnace")
    assert furnace_def.efficiency is None and furnace_def.output_item_id is None
    assert next(i for i in restored.items if i.id == "iron").eu is None
