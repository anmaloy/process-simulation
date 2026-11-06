"""Phase 2a — per-node computation and the fuel→fluid conversion.

Acceptance bar (WORKFLOW P2a): rates and net energy are correct; a node with no
base-energy field contributes no energy term (not zero-substituted); a recipe
term overrides base; and boiler fuelDrain/fluidOut/η match hand calcs at D = 0,
D = R, and a mid load.
"""

import math

from process_sim.engine import compute_node, convert_fuel
from process_sim.engine.energy import burn_time, usable_output
from process_sim.model import MachineDef, MachineNode, Recipe, Stack


def _node(**kw) -> MachineNode:
    kw.setdefault("id", "n1")
    kw.setdefault("machine_def_id", "d1")
    return MachineNode(**kw)


# --------------------------------------------------------------------------- #
# Rates.
# --------------------------------------------------------------------------- #


def test_rates_basic() -> None:
    recipe = Recipe(
        id="r",
        time_seconds=2.0,
        inputs=[Stack("ore", 1)],
        outputs=[Stack("ingot", 1)],
    )
    comp = compute_node(_node(recipe_id="r"), MachineDef(id="d1"), recipe)
    assert comp.cycles_per_sec == 0.5
    assert comp.outputs == {"ingot": 0.5}
    assert comp.inputs == {"ore": 0.5}


def test_rates_scale_with_overclock_and_count() -> None:
    recipe = Recipe(
        id="r",
        time_seconds=4.0,
        inputs=[Stack("ore", 2)],
        outputs=[Stack("ingot", 3)],
    )
    comp = compute_node(
        _node(recipe_id="r", overclock=2.0, count=3), MachineDef(id="d1"), recipe
    )
    # cyclesPerSec = 2 / 4 = 0.5; rate = 0.5 * count(3) = 1.5 cycles/s
    assert comp.cycles_per_sec == 0.5
    assert comp.outputs == {"ingot": 3 * 1.5}
    assert comp.inputs == {"ore": 2 * 1.5}


def test_repeated_item_accumulates() -> None:
    recipe = Recipe(
        id="r",
        time_seconds=1.0,
        outputs=[Stack("ingot", 1), Stack("ingot", 2)],
    )
    comp = compute_node(_node(recipe_id="r"), MachineDef(id="d1"), recipe)
    assert comp.outputs == {"ingot": 3.0}


def test_no_recipe_has_no_rates() -> None:
    comp = compute_node(_node(), MachineDef(id="d1", base_energy_production=100), None)
    assert comp.cycles_per_sec == 0.0
    assert comp.outputs == {} and comp.inputs == {}
    assert comp.energy_produce == 100.0  # base energy still contributes


# --------------------------------------------------------------------------- #
# Energy — absence, override, net.
# --------------------------------------------------------------------------- #


def test_absent_energy_is_none_not_zero() -> None:
    recipe = Recipe(id="r", time_seconds=1.0, outputs=[Stack("x", 1)])
    comp = compute_node(_node(recipe_id="r"), MachineDef(id="d1"), recipe)
    assert comp.energy_consume is None
    assert comp.energy_produce is None
    assert comp.net_energy is None  # not 0 — the term is absent


def test_machine_base_energy_used_when_recipe_silent() -> None:
    recipe = Recipe(id="r", time_seconds=1.0, outputs=[Stack("x", 1)])
    md = MachineDef(id="d1", base_energy_consumption=20)
    comp = compute_node(_node(recipe_id="r"), md, recipe)
    assert comp.energy_consume == 20.0
    assert comp.net_energy == -20.0


def test_recipe_energy_overrides_base() -> None:
    recipe = Recipe(id="r", time_seconds=1.0, energy_consumption=30)
    md = MachineDef(id="d1", base_energy_consumption=20)
    comp = compute_node(_node(recipe_id="r"), md, recipe)
    assert comp.energy_consume == 30.0  # recipe replaces base, not 50


def test_energy_scales_with_overclock_count() -> None:
    recipe = Recipe(
        id="r", time_seconds=1.0, energy_consumption=10, energy_production=50
    )
    comp = compute_node(
        _node(recipe_id="r", overclock=2.0, count=2), MachineDef(id="d1"), recipe
    )
    assert comp.energy_consume == 40.0  # 10 * 2 * 2
    assert comp.energy_produce == 200.0  # 50 * 2 * 2
    assert comp.net_energy == 160.0


def test_net_energy_one_sided() -> None:
    recipe = Recipe(id="r", time_seconds=1.0, energy_production=200)
    comp = compute_node(_node(recipe_id="r"), MachineDef(id="d1"), recipe)
    assert comp.energy_produce == 200.0
    assert comp.energy_consume is None
    assert comp.net_energy == 200.0  # missing consume side contributes nothing


# --------------------------------------------------------------------------- #
# Fuel → fluid conversion (energy module). The boiler numbers (w = 0.8, R = 256)
# are a default-content example — the engine never names the fluid.
# --------------------------------------------------------------------------- #


def test_lossless_conversion_is_one_to_one() -> None:
    conv = convert_fuel(max_rate=256, draw=256, waste=0.0)
    assert conv.fluid_out == 256
    assert conv.fuel_drain == 256
    assert conv.efficiency == 1.0
    assert conv.wasted == 0.0


def test_lossless_partial_draw_still_efficient() -> None:
    conv = convert_fuel(max_rate=256, draw=100, waste=0.0)
    assert conv.fuel_drain == 100  # lossless: drains only what is drawn
    assert conv.efficiency == 1.0


def test_lossless_zero_draw_defined() -> None:
    conv = convert_fuel(max_rate=256, draw=0, waste=0.0)
    assert conv.fuel_drain == 0.0
    assert conv.efficiency == 1.0  # 0/0 → defined as lossless


def test_boiler_at_zero_draw_idles_hot() -> None:
    conv = convert_fuel(max_rate=256, draw=0, waste=0.8)
    assert conv.fluid_out == 0.0
    assert math.isclose(conv.fuel_drain, 0.8 * 256)  # burns w·R = 204.8
    assert math.isclose(conv.wasted, 0.8 * 256)
    assert conv.efficiency == 0.0


def test_boiler_at_full_draw_is_efficient() -> None:
    conv = convert_fuel(max_rate=256, draw=256, waste=0.8)
    assert conv.fuel_drain == 256
    assert conv.efficiency == 1.0
    assert conv.wasted == 0.0


def test_boiler_mid_load() -> None:
    conv = convert_fuel(max_rate=256, draw=128, waste=0.8)
    # fuelDrain = 128 + 0.8·(256−128) = 230.4
    assert math.isclose(conv.fuel_drain, 230.4)
    assert math.isclose(conv.efficiency, 128 / 230.4)
    assert math.isclose(conv.wasted, 0.8 * 128)


def test_draw_capped_at_max_rate() -> None:
    conv = convert_fuel(max_rate=256, draw=9999, waste=0.5)
    assert conv.draw == 256
    assert conv.fluid_out == 256


def test_default_draw_is_full() -> None:
    conv = convert_fuel(max_rate=64)
    assert conv.draw == 64 and conv.fluid_out == 64


def test_usable_output_and_burn_time() -> None:
    conv = convert_fuel(max_rate=256, draw=128, waste=0.8)
    # one fuel unit worth 1000 EU
    assert math.isclose(usable_output(1000, conv.efficiency), 1000 * conv.efficiency)
    bt = burn_time(1000, conv.efficiency, conv.draw)
    assert bt is not None and math.isclose(bt, 1000 * conv.efficiency / 128)
    assert burn_time(1000, 1.0, 0) is None


def test_fuel_burning_node_converts_fuel() -> None:
    md = MachineDef(id="boiler", fuel_burning=True, efficiency=0.8, max_fluid_rate=256)
    comp = compute_node(_node(machine_def_id="boiler"), md, None)
    assert comp.fuel_conversion is not None
    assert comp.fuel_conversion.fluid_out == 256  # full draw by default


def test_fluid_output_scales_with_overclock() -> None:
    md = MachineDef(id="boiler", fuel_burning=True, max_fluid_rate=100)
    comp = compute_node(
        _node(machine_def_id="boiler", overclock=2.0), md, None, fluid_draw=50
    )
    assert comp.fuel_conversion is not None
    assert comp.fuel_conversion.max_rate == 200  # 100 * overclock 2
    assert comp.fuel_conversion.fluid_out == 50


def test_non_fuel_node_has_no_conversion() -> None:
    comp = compute_node(_node(), MachineDef(id="d1", max_fluid_rate=256), None)
    assert comp.fuel_conversion is None  # not flagged fuel_burning


# --------------------------------------------------------------------------- #
# Serialization — the camelCase JSON shape the P4 api will marshal.
# --------------------------------------------------------------------------- #


def test_to_dict_omits_absent_energy() -> None:
    recipe = Recipe(id="r", time_seconds=2.0, outputs=[Stack("ingot", 1)])
    d = compute_node(_node(recipe_id="r"), MachineDef(id="d1"), recipe).to_dict()
    assert d["outputs"] == {"ingot": 0.5}
    assert "netEnergy" not in d and "energyConsume" not in d  # absent, not 0
    assert "fuelConversion" not in d


def test_to_dict_includes_present_terms_and_conversion() -> None:
    md = MachineDef(id="boiler", fuel_burning=True, efficiency=0.8, max_fluid_rate=256)
    recipe = Recipe(id="r", time_seconds=1.0, energy_production=200)
    node = _node(machine_def_id="boiler", recipe_id="r")
    d = compute_node(node, md, recipe).to_dict()
    assert d["energyProduce"] == 200.0
    assert d["netEnergy"] == 200.0
    assert d["fuelConversion"]["fluidOut"] == 256
