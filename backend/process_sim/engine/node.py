"""Per-node computation (WORKFLOW P2a).

For a node running recipe ``R`` at ``overclock`` with parallel ``count`` (all
rates per second)::

    cyclesPerSec   = overclock / R.timeSeconds
    outputRate(s)  = s.quantity * cyclesPerSec * count
    inputDemand(s) = s.quantity * cyclesPerSec * count

Energy terms, each **omitted when unset** (never zero-substituted): a recipe
energy term, if set, **overrides** (replaces) the machine-base term of the same
kind::

    consume   = (recipe.energyConsumption or base) * overclock * count
    produce   = (recipe.energyProduction  or base) * overclock * count
    netEnergy = produce − produce        # a missing side contributes nothing

A fuel-burning def additionally generates steam; see :mod:`.energy`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import MachineDef, MachineNode, Recipe
from .energy import SteamGeneration, steam_generation

__all__ = ["NodeComputation", "compute_node"]


@dataclass
class NodeComputation:
    """Steady-state per-second rates and energy for one placed node.

    ``outputs`` / ``inputs`` map item id → rate (production) / demand. Energy
    terms are ``None`` when the node has no term of that kind — distinct from a
    present term of ``0``. ``steam`` is set only for a fuel-burning def.
    """

    node_id: str
    cycles_per_sec: float
    outputs: dict[str, float] = field(default_factory=dict)
    inputs: dict[str, float] = field(default_factory=dict)
    energy_produce: float | None = None
    energy_consume: float | None = None
    net_energy: float | None = None
    steam: SteamGeneration | None = None

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
        if self.steam is not None:
            out["steam"] = self.steam.to_dict()
        return out


def _accumulate(target: dict[str, float], item_id: str, amount: float) -> None:
    """Add ``amount`` to ``target[item_id]`` (recipes may repeat an item)."""

    target[item_id] = target.get(item_id, 0.0) + amount


def compute_node(
    node: MachineNode,
    machine_def: MachineDef,
    recipe: Recipe | None,
    *,
    steam_draw: float | None = None,
) -> NodeComputation:
    """Compute per-node rates, energy terms, and (if applicable) steam output.

    ``recipe`` may be ``None`` (an unbound or storage-only node): it then has no
    item rates and no recipe energy terms, but may still carry machine-base
    energy and, for a fuel-burning def, steam generation. ``steam_draw`` is the
    steam actually drawn by downstream demand; ``None`` means full draw (the
    standalone / max case). The flow layer passes the resolved draw.
    """

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

    # Energy: recipe term overrides machine base; an absent term stays absent.
    consume_term = _override(
        recipe.energy_consumption if recipe else None,
        machine_def.base_energy_consumption,
    )
    produce_term = _override(
        recipe.energy_production if recipe else None,
        machine_def.base_energy_production,
    )
    energy_consume = consume_term * scale if consume_term is not None else None
    energy_produce = produce_term * scale if produce_term is not None else None

    if energy_produce is None and energy_consume is None:
        net_energy = None
    else:
        net_energy = (energy_produce or 0.0) - (energy_consume or 0.0)

    steam: SteamGeneration | None = None
    if machine_def.fuel_burning and machine_def.max_steam_rate is not None:
        # Overclock and parallel count scale the generation rate like any other
        # energy production; the draw is likewise against the scaled max.
        r = machine_def.max_steam_rate * scale
        steam = steam_generation(r, steam_draw, machine_def.efficiency or 0.0)

    return NodeComputation(
        node_id=node.id,
        cycles_per_sec=cycles_per_sec,
        outputs=outputs,
        inputs=inputs,
        energy_produce=energy_produce,
        energy_consume=energy_consume,
        net_energy=net_energy,
        steam=steam,
    )


def _override(recipe_term: float | None, base_term: float | None) -> float | None:
    """Recipe term replaces base when present; else base; else absent."""

    return recipe_term if recipe_term is not None else base_term
