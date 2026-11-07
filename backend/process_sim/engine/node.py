"""Per-node computation (WORKFLOW P2a).

For a node running recipe ``R`` at ``overclock`` with parallel ``count`` (all
rates per second)::

    cyclesPerSec   = overclock / R.timeSeconds
    outputRate(s)  = s.quantity * cyclesPerSec * count
    inputDemand(s) = s.quantity * cyclesPerSec * count

Energy is **hybrid** (resolved decision #2): a recipe's own ``eu_per_tick``
overrides the machine's ``base_energy_consumption`` default. The effective
per-tick draw is scaled by overclock × count and converted to per-second (×20);
the overclock is first clamped so ``eu_per_tick × overclock ≤ max_eu_per_tick``
(the machine's power-tier ceiling), which slows the recipe too, since speed and
energy move together. How that energy is sourced is the machine's
``energy_source``, and every carrier
— fuel, steam, **and electricity** — flows as a routed item so pipe throughput
caps it (electric connections give electricity its transfer limit):

- ``electric`` with a ``source_item_id`` routes the draw as that energy-category
  carrier; with none it falls back to a scalar grid draw (an abstract mains).
- ``liquid`` / ``fuel`` on a **converter** (one with ``output_item_id`` +
  ``max_fluid_rate``) consumes its source (a fuel item by its ``eu``, or a
  liquid like steam 1:1) and emits its output carrier as a routed item.

The scalar energy terms (``energy_produce`` / ``energy_consume`` / ``net_energy``)
are **derived** from the energy-category carriers that flow as items (plus any
scalar grid draw), so "net energy" stays the electricity balance. See
:mod:`.energy` for the fuel→fluid conversion math, which is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..model import Category, EnergySource, Item, MachineDef, MachineNode, Recipe
from .energy import FuelConversion, convert_fuel

__all__ = ["NodeComputation", "compute_node", "TICKS_PER_SEC"]

#: Game ticks per second. Recipe energy (``eu_per_tick``) and the machine power
#: ceiling (``max_eu_per_tick``) are per **tick** (Modern Industrialization's
#: native base); the engine is canonical per **second**, so draws convert ×20.
TICKS_PER_SEC = 20.0


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
    count = node.count

    # Effective per-tick energy draw (hybrid, resolved decision #2): a recipe's
    # own ``eu_per_tick`` overrides the machine's ``base_energy_consumption``
    # default. ``base`` is stored per-second, so convert it to per-tick for the
    # tier comparison and the draw. Absent on both = no recipe-driven draw (a
    # converter, or a passive machine).
    base = machine_def.base_energy_consumption
    if recipe is not None and recipe.eu_per_tick is not None:
        eu_per_tick: float | None = recipe.eu_per_tick
    elif base is not None:
        eu_per_tick = base / TICKS_PER_SEC
    else:
        eu_per_tick = None

    # Modern-Industrialization-style power ceiling: a machine cannot overclock
    # past the EU/tick its tier/upgrades feed, so clamp the overclock used for
    # *all* scaling — speed and energy move together, so a capped machine runs
    # its recipe slower too. A machine with no ceiling (or no per-tick draw to
    # measure against) overclocks freely.
    overclock = node.overclock
    cap = machine_def.max_eu_per_tick
    if cap is not None and eu_per_tick is not None and eu_per_tick > 0:
        max_oc = cap / eu_per_tick
        if overclock > max_oc:
            overclock = max_oc
    scale = overclock * count

    cycles_per_sec = 0.0
    outputs: dict[str, float] = {}
    inputs: dict[str, float] = {}
    if recipe is not None and recipe.time_seconds > 0:
        cycles_per_sec = overclock / recipe.time_seconds
        rate = cycles_per_sec * count
        # A probabilistic term contributes its *expected* amount (quantity ×
        # probability); absent probability = always (1), probability 0 = a
        # catalyst (referenced but neither consumed nor produced).
        for s in recipe.outputs:
            rid = s.resource_id
            prob = s.probability if s.probability is not None else 1.0
            if rid is not None and prob != 0.0:
                _accumulate(outputs, rid, s.quantity * prob * rate)
        for s in recipe.inputs:
            rid = s.resource_id
            prob = s.probability if s.probability is not None else 1.0
            # A probability-0 term is a catalyst: referenced but not consumed /
            # produced, so it imposes no demand and is left absent (never a
            # zero-rate entry).
            if rid is not None and prob != 0.0:
                _accumulate(inputs, rid, s.quantity * prob * rate)

    src_id = machine_def.source_item_id

    def _consume_source(amount: float) -> None:
        """Route a carrier draw onto ``inputs`` (a fuel by its eu, else 1:1)."""

        if src_id is None:
            return
        src = items.get(src_id)
        is_fuel = machine_def.energy_source is EnergySource.FUEL
        if is_fuel and src is not None and src.eu:
            _accumulate(inputs, src_id, amount / src.eu)
        else:
            _accumulate(inputs, src_id, amount)

    # A machine draws energy while it runs: ``eu_per_tick`` (recipe override or
    # machine default) × 20 → per-second, scaled by the clamped overclock ×
    # count. With a source carrier chosen it is *routed* (and therefore
    # transfer-limited by its pipes); without one it falls back to a scalar grid
    # draw (an abstract mains supply).
    scalar_consume = 0.0
    if eu_per_tick is not None:
        draw = eu_per_tick * TICKS_PER_SEC * scale
        if src_id is not None:
            _consume_source(draw)
        else:
            scalar_consume = draw

    # A converter turns its source carrier into its output carrier; both ride as
    # routed items (electricity included — it is a routed energy-category item,
    # capped by its electric connections).
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
        # The source carrier supplies the *energy*; an optional material input is
        # what actually becomes the output (a boiler burns fuel to turn water
        # into steam). The material is consumed at ``material_ratio`` units of
        # output per unit of material (default 1 = 1:1); a boiler making 16 steam
        # per water sets it to 16, so water demand is fluid_out / 16.
        _consume_source(conv.fuel_drain)
        if machine_def.input_item_id is not None:
            material_ratio = machine_def.material_ratio or 1.0
            _accumulate(
                inputs, machine_def.input_item_id, conv.fluid_out / material_ratio
            )
        _accumulate(outputs, machine_def.output_item_id, conv.fluid_out)  # type: ignore[arg-type]

    # Energy terms are *derived* from the energy-category carriers that now flow
    # as items, plus any scalar grid draw. "Net energy" stays the electricity
    # balance while electricity itself routes like any other carrier.
    def _energy_cat_total(flows: dict[str, float]) -> float:
        total = 0.0
        for iid, rate in flows.items():
            it = items.get(iid)
            if it is not None and it.category is Category.ENERGY:
                total += rate
        return total

    produce_items = _energy_cat_total(outputs)
    consume_items = _energy_cat_total(inputs)
    energy_produce = produce_items if produce_items > 1e-12 else None
    total_consume = consume_items + scalar_consume
    energy_consume = (
        total_consume if (scalar_consume > 0.0 or consume_items > 1e-12) else None
    )

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
