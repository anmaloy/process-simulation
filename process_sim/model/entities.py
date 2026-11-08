"""Domain dataclasses.

Every entity carries a stable ``id``; nodes, pipes, and recipes reference items
and defs **by id**, so a :class:`World` serializes flat. Sparse fields are
genuinely optional (``None`` / absent), never defaulted to zero — see
``common`` for the serialization rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import Category, Direction, EnergySource, PipeEndRole, Serializable

__all__ = [
    "Item",
    "Stack",
    "Recipe",
    "SlotGroup",
    "StorageSpec",
    "MachineDef",
    "MachineNode",
    "PipeEnd",
    "Pipe",
    "SystemPort",
    "System",
    "World",
]


@dataclass
class Item(Serializable):
    """A tracked resource.

    ``eu`` is the item's stored-energy (fuel) content in EU; items without it
    are not fuels. Absent ``eu`` means "not a fuel", distinct from ``eu = 0``.

    ``is_tag`` marks a *tag* resource — a named group ("any item in this set",
    e.g. Modern Industrialization's ``c:gears/iron``) rather than a concrete
    item. A tag item is a first-class, fungible resource in the balance: a
    :class:`Stack` may reference it, and the engine treats its id like any other
    resource id. Absent = a concrete item (the common case), not ``False`` in
    the wire format.
    """

    id: str
    name: str
    category: Category
    eu: float | None = None
    is_tag: bool | None = None
    members: list[str] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Item:
        members = d.get("members")
        return cls(
            id=d["id"],
            name=d["name"],
            category=Category(d["category"]),
            eu=d.get("eu"),
            is_tag=d.get("isTag"),
            members=list(members) if members else None,
        )


@dataclass
class Stack(Serializable):
    """A ``{resource, quantity}`` pair, per recipe cycle unless noted.

    The resource is either a concrete item (``item_id``) **or** a tag
    (``tag`` — a group reference like ``c:gears/iron``); exactly one is set.
    :attr:`resource_id` returns whichever applies, which is the id the engine
    keys flows by (a tag resolves to a tag :class:`Item` of the same id).

    ``probability`` is the expected consume/produce chance for a probabilistic
    recipe term (absent = always, i.e. ``1``). ``probability = 0`` is a catalyst
    (referenced but neither consumed nor produced) — distinct from absent.
    """

    item_id: str | None = None
    quantity: float = 0.0
    tag: str | None = None
    probability: float | None = None

    @property
    def resource_id(self) -> str | None:
        """The concrete item id, else the tag id — the flow key for this stack."""

        return self.item_id if self.item_id is not None else self.tag

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Stack:
        return cls(
            item_id=d.get("itemId"),
            quantity=d["quantity"],
            tag=d.get("tag"),
            probability=d.get("probability"),
        )


@dataclass
class Recipe(Serializable):
    """Input stacks → output stacks over ``time_seconds``.

    Energy is **hybrid** (resolved decision #2, revised to match Modern
    Industrialization): a recipe may carry its own ``eu_per_tick`` — the EU/tick
    its running machine draws while making it — which *overrides* the machine's
    ``base_energy_consumption`` default when present. A recipe without
    ``eu_per_tick`` falls back to the machine's own draw, so pre-existing
    machine-energy projects keep working. ``eu_per_tick`` is per **tick** (the
    canonical EU/t the UI and MI data both use); the engine converts to
    per-second (×20) at compute time, mirroring the duration ticks→seconds
    conversion the importer applies.

    ``machine_type`` is the kind of machine that runs this recipe (MI's recipe
    ``type``, e.g. ``macerator``). It names the binding from the machine side; a
    :class:`MachineDef` still lists its ``allowed_recipe_ids``, but importers key
    recipe→machine off this field.
    """

    id: str
    time_seconds: float
    name: str | None = None
    inputs: list[Stack] = field(default_factory=list)
    outputs: list[Stack] = field(default_factory=list)
    eu_per_tick: float | None = None
    machine_type: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recipe:
        return cls(
            id=d["id"],
            time_seconds=d["timeSeconds"],
            name=d.get("name"),
            inputs=[Stack.from_dict(s) for s in d.get("inputs", [])],
            outputs=[Stack.from_dict(s) for s in d.get("outputs", [])],
            eu_per_tick=d.get("euPerTick"),
            machine_type=d.get("machineType"),
        )


@dataclass
class SlotGroup(Serializable):
    """Buffer layout for one category × direction.

    Holds ``slots × stack_size`` of its category in its direction.
    """

    category: Category
    direction: Direction
    slots: int
    stack_size: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SlotGroup:
        return cls(
            category=Category(d["category"]),
            direction=Direction(d["direction"]),
            slots=d["slots"],
            stack_size=d["stackSize"],
        )


@dataclass
class StorageSpec(Serializable):
    """A buffer's capacity for one category (makes the def a storage node).

    ``capacity`` is in the category's canonical unit — item count (``slots ×
    stack_size``) for solids, mB for liquids, EU for energy. A storage node is a
    transparent passthrough in the steady-state balance (buffers only absorb
    transients); the capacity is a display/annotation figure.
    """

    category: Category
    capacity: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StorageSpec:
        return cls(category=Category(d["category"]), capacity=d["capacity"])


@dataclass
class MachineDef(Serializable):
    """A machine template: energy source, slots, overclock bounds, optional storage.

    ``energy_source`` (replacing the old fuel-burning flag) says how the machine
    is powered while it runs; ``source_item_id`` names the carrier it consumes
    (a fuel item, or a liquid like steam). A **converter** additionally sets
    ``output_item_id`` + ``max_fluid_rate`` (``R``) and optional ``efficiency``
    (``w``, waste fraction, default 0 = lossless): it spends its source's energy
    to turn an optional ``input_item_id`` **material** into that output carrier (a
    boiler heats water → steam burning fuel; a turbine spends steam → electricity
    with no separate material). ``material_ratio`` is how many units of output one
    unit of that material yields (default absent = ``1``, a 1:1 conversion); a
    boiler making 16 steam per water sets it to ``16``. A plain **consumer** sets
    only
    ``base_energy_consumption`` — the EU/s it draws while running its recipe.
    A **storage** def sets ``storage`` and acts as a buffer. All energy fields are
    absent on a passive machine.

    ``base_energy_consumption`` is now the machine's *default* draw: a bound
    recipe's own ``eu_per_tick`` overrides it (see :class:`Recipe`). The machine
    additionally carries a Modern-Industrialization-style power ceiling:
    ``voltage_tier`` (a tier label such as ``LV`` / ``MV`` / ``HV``) and
    ``max_eu_per_tick`` (the most EU/tick the machine can draw). The engine
    clamps the effective overclock so ``eu_per_tick × overclock`` never exceeds
    ``max_eu_per_tick`` — the tier/upgrade cap that gates overclocking in MI.
    Both are absent on a machine with no modelled ceiling.
    """

    id: str
    name: str | None = None
    energy_source: EnergySource | None = None
    source_item_id: str | None = None
    base_energy_consumption: float | None = None
    input_item_id: str | None = None
    output_item_id: str | None = None
    efficiency: float | None = None
    max_fluid_rate: float | None = None
    material_ratio: float | None = None
    storage: StorageSpec | None = None
    allowed_recipe_ids: list[str] = field(default_factory=list)
    slots: list[SlotGroup] = field(default_factory=list)
    overclock_min: float | None = None
    overclock_max: float | None = None
    voltage_tier: str | None = None
    max_eu_per_tick: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MachineDef:
        source = d.get("energySource")
        storage = d.get("storage")
        return cls(
            id=d["id"],
            name=d.get("name"),
            energy_source=EnergySource(source) if source is not None else None,
            source_item_id=d.get("sourceItemId"),
            base_energy_consumption=d.get("baseEnergyConsumption"),
            input_item_id=d.get("inputItemId"),
            output_item_id=d.get("outputItemId"),
            efficiency=d.get("efficiency"),
            max_fluid_rate=d.get("maxFluidRate"),
            material_ratio=d.get("materialRatio"),
            storage=StorageSpec.from_dict(storage) if storage is not None else None,
            allowed_recipe_ids=list(d.get("allowedRecipeIds", [])),
            slots=[SlotGroup.from_dict(s) for s in d.get("slots", [])],
            overclock_min=d.get("overclockMin"),
            overclock_max=d.get("overclockMax"),
            voltage_tier=d.get("voltageTier"),
            max_eu_per_tick=d.get("maxEuPerTick"),
        )


@dataclass
class MachineNode(Serializable):
    """A placed machine bound to a def, an optional recipe, overclock, and count.

    ``x``/``y`` are canvas coordinates, absent until placed by the UI.

    The scope is modelled as an automatic system: a machine runs only to the
    extent its inputs are actually delivered by connected pipes and its outputs
    are actually taken downstream. Two **boundary** flags opt a node out of that,
    for the edge of the system where a player feeds or empties it by hand:

    - ``assume_inputs`` (manual insertion): treat any input not supplied by a
      pipe as available, so the machine can run without every feed wired up.
    - ``assume_outputs`` (manual extraction): treat the outputs as removed, so
      the machine can run even when nothing downstream consumes them (its output
      is the system's product).
    """

    id: str
    machine_def_id: str
    recipe_id: str | None = None
    overclock: float = 1.0
    count: int = 1
    assume_inputs: bool = False
    assume_outputs: bool = False
    x: float | None = None
    y: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MachineNode:
        return cls(
            id=d["id"],
            machine_def_id=d["machineDefId"],
            recipe_id=d.get("recipeId"),
            overclock=d.get("overclock", 1.0),
            count=d.get("count", 1),
            assume_inputs=d.get("assumeInputs", False),
            assume_outputs=d.get("assumeOutputs", False),
            x=d.get("x"),
            y=d.get("y"),
        )


@dataclass
class PipeEnd(Serializable):
    """One end of a pipe at a node's port.

    Priorities are signed scalars (−128..127, default 0 in the UI). They are
    optional and set independently: an ``in``-only end has no extract priority,
    an ``out``-only end no insert priority — absent, not zero.
    """

    node_id: str
    role: PipeEndRole
    port: str | None = None
    insert_priority: int | None = None
    extract_priority: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PipeEnd:
        return cls(
            node_id=d["nodeId"],
            role=PipeEndRole(d["role"]),
            port=d.get("port"),
            insert_priority=d.get("insertPriority"),
            extract_priority=d.get("extractPriority"),
        )


@dataclass
class Pipe(Serializable):
    """The graph edge: carries one category between two machine ports.

    ``movement_capacity`` caps sustained flow (× overclock); ``storage_capacity``
    only buffers transients.
    """

    id: str
    category: Category
    a: PipeEnd
    b: PipeEnd
    movement_capacity: float
    storage_capacity: float | None = None
    overclock: float = 1.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pipe:
        return cls(
            id=d["id"],
            category=Category(d["category"]),
            a=PipeEnd.from_dict(d["a"]),
            b=PipeEnd.from_dict(d["b"]),
            movement_capacity=d["movementCapacity"],
            storage_capacity=d.get("storageCapacity"),
            overclock=d.get("overclock", 1.0),
        )


@dataclass
class SystemPort(Serializable):
    """A port a system exposes for external connection, naming an internal port."""

    node_id: str
    port: str | None = None
    name: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SystemPort:
        return cls(
            node_id=d["nodeId"],
            port=d.get("port"),
            name=d.get("name"),
        )


@dataclass
class System(Serializable):
    """A user-defined group of nodes (and nested systems) treated as one unit.

    ``x``/``y`` position the collapsed representation on the canvas (absent until
    the UI places it). ``collapsed`` is a presentation flag only — it never
    changes the balance, since the engine always aggregates the real member
    nodes whether the group is drawn folded or expanded.
    """

    id: str
    name: str | None = None
    member_node_ids: list[str] = field(default_factory=list)
    member_system_ids: list[str] = field(default_factory=list)
    exposed_ports: list[SystemPort] = field(default_factory=list)
    collapsed: bool = False
    x: float | None = None
    y: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> System:
        return cls(
            id=d["id"],
            name=d.get("name"),
            member_node_ids=list(d.get("memberNodeIds", [])),
            member_system_ids=list(d.get("memberSystemIds", [])),
            exposed_ports=[SystemPort.from_dict(p) for p in d.get("exposedPorts", [])],
            collapsed=d.get("collapsed", False),
            x=d.get("x"),
            y=d.get("y"),
        )


@dataclass
class World(Serializable):
    """Everything: items, recipes, machine defs, nodes, pipes, and systems.

    Serializes flat — entities reference one another by id, not by nesting.
    """

    name: str | None = None
    items: list[Item] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)
    machine_defs: list[MachineDef] = field(default_factory=list)
    nodes: list[MachineNode] = field(default_factory=list)
    pipes: list[Pipe] = field(default_factory=list)
    systems: list[System] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> World:
        return cls(
            name=d.get("name"),
            items=[Item.from_dict(x) for x in d.get("items", [])],
            recipes=[Recipe.from_dict(x) for x in d.get("recipes", [])],
            machine_defs=[MachineDef.from_dict(x) for x in d.get("machineDefs", [])],
            nodes=[MachineNode.from_dict(x) for x in d.get("nodes", [])],
            pipes=[Pipe.from_dict(x) for x in d.get("pipes", [])],
            systems=[System.from_dict(x) for x in d.get("systems", [])],
        )
