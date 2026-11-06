"""Domain model: dataclasses for Item, Recipe, MachineDef, MachineNode, Pipe,
System, and World.

Pure data with JSON round-trip (``to_dict`` / ``from_dict``). Entities carry
stable ids and reference one another by id, so a :class:`World` serializes flat.
Sparse optional fields survive the round-trip as *absent*, never as ``0``.
"""

from .common import (
    Category,
    Direction,
    EnergySource,
    PipeEndRole,
    Serializable,
    to_camel,
)
from .entities import (
    Item,
    MachineDef,
    MachineNode,
    Pipe,
    PipeEnd,
    Recipe,
    SlotGroup,
    Stack,
    StorageSpec,
    System,
    SystemPort,
    World,
)

__all__ = [
    "Category",
    "Direction",
    "EnergySource",
    "PipeEndRole",
    "Serializable",
    "to_camel",
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
