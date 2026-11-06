"""Per-node computation (WORKFLOW P2a).

For a node running recipe ``R`` at ``overclock`` with parallel ``count`` (all
rates per second)::

    cyclesPerSec   = overclock / R.timeSeconds
    outputRate(s)  = s.quantity * cyclesPerSec * count
    inputDemand(s) = s.quantity * cyclesPerSec * count

Energy is a **machine** property, not a recipe one: a machine draws
``base_energy_consumption`` EU/s while it runs (scaled by overclock × count),
accounted as a scalar ``energy_consume``. How that energy is sourced is the
machine's ``energy_source``:

- ``electric`` — the draw is pure grid electricity (scalar; not routed).
- ``liquid`` / ``fuel`` on a **converter** (one with ``output_item_id`` +
  ``max_fluid_rate``) turns its source carrier into an output carrier. The
  source is consumed as a routed item (a fuel item by its ``eu``, or a liquid
  like steam 1:1); the output is a routed liquid/solid carrier, **or**, when the
  output item is ``energy``-category (electricity), a scalar ``energy_produce``.

So steam and fuel flow as ordinary items through pipes, while electricity stays
the scalar grid balance (``netEnergy = produce − consume``). See :mod:`.energy`
for the fuel→fluid conversion math, which is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..model import Category, EnergySource, Item, MachineDef, MachineNode, Recipe
from .energy import FuelConversion, convert_fuel

__all__ = ["NodeComputation", "compute_node"]


@dataclass
class NodeComputation:
    """Steady-state per-second rates and energy for one placed node.

    ``outputs`` / ``inputs`` map item id → rate (production) / demand — this
    includes routed energy carriers (steam, fuel). Scalar energy terms are
    ``None`` when the node has no term of that kind (distinct from ``0``);
    ``fuel_conversion`` is set only for a converter.
    """

    node_id: str
    cycles_per_sec: float
    outputs: dict[str, float] = field(default_factory=dict)
    inputs: dict[str, float] = field(default_factory=dict)
    energy_produce: float | None = None
    energy_consume: float | None = None
    net_energy: float | None = None
    fuel_conversion: FuelConversion | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "nodeId": self.node_id,
            "cyclesPerSec": self.cycles_per_sec,
            "outputs": dict(self.outputs),
            "inputs": dict(self.inputs),
        }
        if self.energy_produce is not None:
            out["energyProduce"] = self.energy_produce
        if self.energy_consume is not None:
            out["energyConsume"] = self.energy_consume
        if self.net_energy is not None:
            out["netEnergy"] = self.net_energy
        if self.fuel_conversion is not None:
            out["fuelConversion"] = self.fuel_conversion.to_dict()
        return out


def _accumulate(target: dict[str, float], item_id: str, amount: float) -> None:
    """Add ``amount`` to ``target[item_id]`` (recipes may repeat an item)."""

    target[item_id] = target.get(item_id, 0.0) + amount


def compute_node(
    node: MachineNode,
    machine_def: MachineDef,
    recipe: Recipe | None,
    *,
    items: Mapping[str, Item] | None = None,
    fluid_draw: float | None = None,
) -> NodeComputation:
    """Compute per-node rates, energy terms, and (if applicable) conversion.

    ``recipe`` may be ``None`` (an unbound, storage, or converter-only node): it
    then has no item rates, but the machine may still draw base energy and, for a
    converter, consume its source / emit its output. ``items`` maps item id →
    :class:`Item` (for a fuel's ``eu`` and a carrier's category); ``fluid_draw``
    is the output drawn by downstream demand, ``None`` = full draw.
    """

    items = items or {}
    overclock = node.overclock
    count = node.count
    scale = overclock * count

    cycles_per_sec = 0.0
    outputs: dict[str, float] = {}
    inputs: dict[str, float] = {}
    if recipe is not None and recipe.time_seconds > 0:
        cycles_per_sec = overclock / recipe.time_seconds
        rate = cycles_per_sec * count
        for s in recipe.outputs:
            _accumulate(outputs, s.item_id, s.quantity * rate)
        for s in recipe.inputs:
            _accumulate(inputs, s.item_id, s.quantity * rate)

    # A machine draws base energy while it runs (scaled by overclock × count).
    # This is the grid-electricity consume term; a converter earns its own
    # produce term below.
    base = machine_def.base_energy_consumption
    energy_consume = base * scale if base is not None else None
    energy_produce: float | None = None

    fuel_conversion: FuelConversion | None = None
    is_converter = (
        machine_def.energy_source in (EnergySource.FUEL, EnergySource.LIQUID)
        and machine_def.max_fluid_rate is not None
        and machine_def.output_item_id is not None
    )
    if is_converter:
        r = machine_def.max_fluid_rate * scale  # type: ignore[operator]
        conv = convert_fuel(r, fluid_draw, machine_def.efficiency or 0.0)
        fuel_conversion = conv

        # Consume the source carrier as a routed item: a fuel by its eu, else
        # (a liquid such as steam) one-to-one with the energy drawn.
        src_id = machine_def.source_item_id
        if src_id is not None:
            src = items.get(src_id)
            if (
                machine_def.energy_source is EnergySource.FUEL
                and src is not None
                and src.eu
            ):
                _accumulate(inputs, src_id, conv.fuel_drain / src.eu)
            else:
                _accumulate(inputs, src_id, conv.fuel_drain)

        # Emit the output carrier: electricity (energy category) is scalar grid
        # power; any other carrier (steam, …) is a routed item.
        out_id = machine_def.output_item_id
        out_item = items.get(out_id) if out_id is not None else None
        if out_item is not None and out_item.category is Category.ENERGY:
            energy_produce = conv.fluid_out
        elif out_id is not None:
            _accumulate(outputs, out_id, conv.fluid_out)

    if energy_produce is None and energy_consume is None:
        net_energy = None
    else:
        net_energy = (energy_produce or 0.0) - (energy_consume or 0.0)

    return NodeComputation(
        node_id=node.id,
        cycles_per_sec=cycles_per_sec,
        outputs=outputs,
        inputs=inputs,
        energy_produce=energy_produce,
        energy_consume=energy_consume,
        net_energy=net_energy,
        fuel_conversion=fuel_conversion,
    )
