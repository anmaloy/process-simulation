"""Domain dataclasses.

Every entity carries a stable ``id``; nodes, pipes, and recipes reference items
and defs **by id**, so a :class:`World` serializes flat. Sparse fields are
genuinely optional (``None`` / absent), never defaulted to zero — see
``common`` for the serialization rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import Category, Direction, PipeEndRole, Serializable

__all__ = [
    "Item",
    "Stack",
    "Recipe",
    "SlotGroup",
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
    """

    id: str
    name: str
    category: Category
    eu: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Item:
        return cls(
            id=d["id"],
            name=d["name"],
            category=Category(d["category"]),
            eu=d.get("eu"),
        )


@dataclass
class Stack(Serializable):
    """An ``{item, quantity}`` pair, per recipe cycle unless noted."""

    item_id: str
    quantity: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Stack:
        return cls(item_id=d["itemId"], quantity=d["quantity"])


@dataclass
class Recipe(Serializable):
    """Input stacks → output stacks over ``time_seconds``.

    A set energy term here *overrides* (replaces) the machine-base term of the
    same kind per the resolved decisions; unset terms stay absent.
    """

    id: str
    time_seconds: float
    name: str | None = None
    inputs: list[Stack] = field(default_factory=list)
    outputs: list[Stack] = field(default_factory=list)
    energy_consumption: float | None = None
    energy_production: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recipe:
        return cls(
            id=d["id"],
            time_seconds=d["timeSeconds"],
            name=d.get("name"),
            inputs=[Stack.from_dict(s) for s in d.get("inputs", [])],
            outputs=[Stack.from_dict(s) for s in d.get("outputs", [])],
            energy_consumption=d.get("energyConsumption"),
            energy_production=d.get("energyProduction"),
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
class MachineDef(Serializable):
    """A machine template: energy behavior, fuel-burning, slots, overclock bounds.

    A fuel-burning converter sets ``fuel_burning`` and may set ``efficiency``
    (the waste fraction ``w``, default 0 = lossless) and ``max_fluid_rate``
    (``R``, the peak output-fluid rate). Which fluid it produces is content, not
    a model concept. Non-fuel defs omit those. Base energy terms are absent
    unless set.
    """

    id: str
    name: str | None = None
    base_energy_consumption: float | None = None
    base_energy_production: float | None = None
    fuel_burning: bool = False
    efficiency: float | None = None
    max_fluid_rate: float | None = None
    allowed_recipe_ids: list[str] = field(default_factory=list)
    slots: list[SlotGroup] = field(default_factory=list)
    overclock_min: float | None = None
    overclock_max: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MachineDef:
        return cls(
            id=d["id"],
            name=d.get("name"),
            base_energy_consumption=d.get("baseEnergyConsumption"),
            base_energy_production=d.get("baseEnergyProduction"),
            fuel_burning=d.get("fuelBurning", False),
            efficiency=d.get("efficiency"),
            max_fluid_rate=d.get("maxFluidRate"),
            allowed_recipe_ids=list(d.get("allowedRecipeIds", [])),
            slots=[SlotGroup.from_dict(s) for s in d.get("slots", [])],
            overclock_min=d.get("overclockMin"),
            overclock_max=d.get("overclockMax"),
        )


@dataclass
class MachineNode(Serializable):
    """A placed machine bound to a def, an optional recipe, overclock, and count.

    ``x``/``y`` are canvas coordinates, absent until placed by the UI.
    """

    id: str
    machine_def_id: str
    recipe_id: str | None = None
    overclock: float = 1.0
    count: int = 1
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
    """A user-defined group of nodes (and nested systems) treated as one unit."""

    id: str
    name: str | None = None
    member_node_ids: list[str] = field(default_factory=list)
    member_system_ids: list[str] = field(default_factory=list)
    exposed_ports: list[SystemPort] = field(default_factory=list)
    collapsed: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> System:
        return cls(
            id=d["id"],
            name=d.get("name"),
            member_node_ids=list(d.get("memberNodeIds", [])),
            member_system_ids=list(d.get("memberSystemIds", [])),
            exposed_ports=[SystemPort.from_dict(p) for p in d.get("exposedPorts", [])],
            collapsed=d.get("collapsed", False),
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
