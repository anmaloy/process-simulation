"""Phase 3 — Modern Industrialization recipe import.

Hermetic: each test builds a tiny fake extracted-jar directory (recipes + a lang
map + a fluid-fuels data map) in ``tmp_path``, so the suite never depends on the
uploaded MI jar. The bar: MI's recipe shapes parse (object-or-list I/O, bare vs
namespaced ids, tags, probability, default amount), units convert (ticks→seconds,
eu kept per-tick), items/tags/fluids are built with the right category and names,
and machine defs group by ``type`` with a data-derived slot layout.
"""

import json
from pathlib import Path

import pytest

from process_sim.api import Api
from process_sim.engine import compute_world
from process_sim.io import (
    CABLE_TIER_EU,
    MACHINE_CATALOG,
    import_mi_library,
    load_project,
)
from process_sim.model import Category

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

NS = "modern_industrialization"


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def mi_root(tmp_path):
    """A minimal extracted MI jar exercising every parser branch."""

    recipe = tmp_path / "data" / NS / "recipe"

    # Macerator: list form, a tag input, a probabilistic output.
    _write(
        recipe / "macerator" / "iron.json",
        {
            "type": f"{NS}:macerator",
            "duration": 200,
            "eu": 2,
            "item_inputs": [{"amount": 1, "tag": "c:ingots/iron"}],
            "item_outputs": [
                {"amount": 2, "item": f"{NS}:iron_dust", "probability": 0.5}
            ],
        },
    )
    # Mixer: single-object form, a bare fluid id, an output with no amount.
    _write(
        recipe / "mixer" / "clay.json",
        {
            "type": f"{NS}:mixer",
            "eu": 4,
            "duration": 100,
            "item_inputs": {"item": f"{NS}:clay_dust"},
            "fluid_inputs": {"fluid": "water", "amount": 125},
            "item_outputs": {"item": f"{NS}:clay"},
        },
    )
    # Assembler: a fluid input plus a tag + a concrete item input.
    _write(
        recipe / "assembler" / "gear.json",
        {
            "type": f"{NS}:assembler",
            "duration": 400,
            "eu": 8,
            "fluid_inputs": [{"amount": 100, "fluid": f"{NS}:soldering_alloy"}],
            "item_inputs": [
                {"amount": 4, "tag": "c:plates/iron"},
                {"amount": 1, "item": f"{NS}:iron_ring"},
            ],
            "item_outputs": [{"amount": 2, "item": f"{NS}:iron_gear"}],
        },
    )
    # Chemical reactor: a catalyst input (probability 0) and a fuel fluid output.
    _write(
        recipe / "chem" / "biodiesel.json",
        {
            "type": f"{NS}:chemical_reactor",
            "eu": 8,
            "duration": 200,
            "item_inputs": [
                {"item": f"{NS}:catalyst", "amount": 1, "probability": 0.0}
            ],
            "fluid_inputs": {"fluid": f"{NS}:raw_biodiesel", "amount": 1000},
            "fluid_outputs": {"fluid": f"{NS}:biodiesel", "amount": 1000},
        },
    )
    # A non-target recipe type — must be skipped.
    _write(
        recipe / "vanilla" / "plank.json",
        {"type": "minecraft:crafting_shaped", "result": {"id": "x"}},
    )

    _write(
        tmp_path / "assets" / NS / "lang" / "en_us.json",
        {
            f"item.{NS}.iron_dust": "Iron Dust",
            f"block.{NS}.assembler": "Assembler",
            f"block.{NS}.chemical_reactor": "Chemical Reactor",
            f"item.{NS}.biodiesel_bucket": "Biodiesel Bucket",
        },
    )
    _write(
        tmp_path / "data" / NS / "data_maps" / "fluid" / "fluid_fuels.json",
        {"values": {f"{NS}:biodiesel": {"eu_per_mb": 250}}},
    )
    # Tag membership: c:ingots/iron (used by the macerator recipe) nests a second
    # tag, so flattening is exercised too.
    _write(
        tmp_path / "data" / "c" / "tags" / "item" / "ingots" / "iron.json",
        {"values": [f"{NS}:iron_ingot", "#c:ingots/wrought_iron"]},
    )
    _write(
        tmp_path / "data" / "c" / "tags" / "item" / "ingots" / "wrought_iron.json",
        {"values": [{"id": f"{NS}:wrought_iron_ingot", "required": False}]},
    )
    return tmp_path


def _recipe(lib, rid):
    return next(r for r in lib.recipes if r.id == rid)


def _item(lib, iid):
    return next(i for i in lib.items if i.id == iid)


# --------------------------------------------------------------------------- #
# Selection, units, and recipe fields.
# --------------------------------------------------------------------------- #


def test_imports_targets_and_skips_other_types(mi_root):
    imp = import_mi_library(mi_root)
    assert imp.stats["recipesImported"] == 4
    assert imp.stats["recipesSkipped"] >= 1  # the crafting recipe
    # Parsed recipes plus the three hand-authored water-pump recipes.
    assert {r.machine_type for r in imp.library.recipes} == {
        "macerator",
        "mixer",
        "assembler",
        "chemical_reactor",
        "bronze_water_pump",
        "steel_water_pump",
        "electric_water_pump",
    }


def test_units_ticks_to_seconds_and_eu_kept_per_tick(mi_root):
    lib = import_mi_library(mi_root).library
    r = _recipe(lib, "mi:macerator/iron")
    assert r.time_seconds == 10.0  # 200 ticks ÷ 20
    assert r.eu_per_tick == 2.0  # kept per tick (engine scales ×20)
    assert r.machine_type == "macerator"


def test_object_and_list_io_both_parse(mi_root):
    lib = import_mi_library(mi_root).library
    # Mixer used single-object I/O; assembler used lists — both yield stacks.
    mixer = _recipe(lib, "mi:mixer/clay")
    assert {s.resource_id for s in mixer.inputs} == {
        f"{NS}:clay_dust",
        "minecraft:water",
    }
    assert [s.resource_id for s in mixer.outputs] == [f"{NS}:clay"]
    asm = _recipe(lib, "mi:assembler/gear")
    assert len(asm.inputs) == 3 and len(asm.outputs) == 1


def test_default_amount_and_probability_passthrough(mi_root):
    lib = import_mi_library(mi_root).library
    clay = next(s for s in _recipe(lib, "mi:mixer/clay").outputs)
    assert clay.quantity == 1.0  # no "amount" → default 1
    dust = next(s for s in _recipe(lib, "mi:macerator/iron").outputs)
    assert dust.probability == 0.5
    catalyst = next(s for s in _recipe(lib, "mi:chem/biodiesel").inputs)
    assert catalyst.probability == 0.0  # preserved on the stack (engine drops it)


# --------------------------------------------------------------------------- #
# Items, tags, fluids.
# --------------------------------------------------------------------------- #


def test_tag_input_becomes_is_tag_item_and_tag_stack(mi_root):
    lib = import_mi_library(mi_root).library
    tag = _item(lib, "c:ingots/iron")
    assert tag.is_tag is True and tag.category is Category.SOLID
    stack = _recipe(lib, "mi:macerator/iron").inputs[0]
    assert stack.tag == "c:ingots/iron" and stack.item_id is None


def test_tag_membership_resolved_and_flattened(mi_root):
    lib = import_mi_library(mi_root).library
    tag = _item(lib, "c:ingots/iron")
    # Nested #c:ingots/wrought_iron is flattened into the member list.
    assert tag.members == [
        f"{NS}:iron_ingot",
        f"{NS}:wrought_iron_ingot",
    ]
    # Members not otherwise referenced are registered as named, concrete items.
    wrought = _item(lib, f"{NS}:wrought_iron_ingot")
    assert wrought.is_tag is None and wrought.category is Category.SOLID
    # A concrete item carries no member list.
    assert _item(lib, f"{NS}:iron_dust").members is None


def test_bare_fluid_id_namespaced_and_liquid_category(mi_root):
    lib = import_mi_library(mi_root).library
    water = _item(lib, "minecraft:water")  # bare "water" → minecraft namespace
    assert water.category is Category.LIQUID
    assert water.name == "Water"  # prettified (no lang entry)


def test_fluid_fuel_eu_populated_from_data_map(mi_root):
    lib = import_mi_library(mi_root).library
    assert _item(lib, f"{NS}:biodiesel").eu == 250.0
    # A non-fuel fluid carries no eu (absent, not zero).
    assert _item(lib, f"{NS}:soldering_alloy").eu is None


def test_names_from_lang_with_prettify_fallback(mi_root):
    lib = import_mi_library(mi_root).library
    assert _item(lib, f"{NS}:iron_dust").name == "Iron Dust"  # item.* lang
    assert _item(lib, f"{NS}:biodiesel").name == "Biodiesel"  # bucket, " Bucket" cut
    assert _item(lib, f"{NS}:clay_dust").name == "Clay Dust"  # prettify fallback


# --------------------------------------------------------------------------- #
# Machine defs.
# --------------------------------------------------------------------------- #


def test_machine_defs_group_with_electric_source_and_derived_slots(mi_root):
    lib = import_mi_library(mi_root).library
    defs = {d.id: d for d in lib.machine_defs}
    # The parsed processing machines: the electric variant per type, plus the
    # bronze/steel steam tiers for the machines that have them (macerator, mixer).
    # (Energy defs are mi.energy.*, pumps mi.pump.*, storage mi.storage.* — below.)
    assert {k for k in defs if k.startswith("mi.machine.")} == {
        "mi.machine.macerator",
        "mi.machine.macerator.bronze",
        "mi.machine.macerator.steel",
        "mi.machine.mixer",
        "mi.machine.mixer.bronze",
        "mi.machine.mixer.steel",
        "mi.machine.assembler",
        "mi.machine.chemical_reactor",
    }
    asm = defs["mi.machine.assembler"]
    assert asm.energy_source.value == "electric"
    assert asm.base_energy_consumption is None  # energy now rides the recipe
    # Electricity is a routed carrier: the consumer draws it as an item.
    assert asm.source_item_id == "mi:electricity"
    # Catalog (P5): the targeted types are LV machines, so the overclock ceiling
    # is the LV tier's EU/t (32, read from the jar's CableTier).
    assert asm.voltage_tier == "LV"
    assert asm.max_eu_per_tick == 32
    assert asm.allowed_recipe_ids == ["mi:assembler/gear"]
    layout = {(g.category.value, g.direction.value): g.slots for g in asm.slots}
    # Derived from the one assembler recipe (2 solid in, 1 solid out, 1 fluid in)
    # plus the routed-electricity energy-in port.
    assert layout == {
        ("solid", "in"): 2,
        ("solid", "out"): 1,
        ("liquid", "in"): 1,
        ("energy", "in"): 1,
    }


def test_catalog_tier_caps_match_jar_cable_tiers():
    # The authored EU/t ceilings are MI's CableTier values (verified from the
    # jar), not guesses; the targeted subset is all Low Voltage.
    assert CABLE_TIER_EU == {
        "LV": 32,
        "MV": 128,
        "HV": 1024,
        "EV": 8192,
        "SV": 128_000_000,
    }
    for t in ("macerator", "mixer", "assembler", "chemical_reactor"):
        spec = MACHINE_CATALOG[t]
        assert spec.voltage_tier == "LV" and spec.max_eu_per_tick == 32


def test_forge_hammer_ingredient_result_shape(tmp_path):
    # The forge hammer uses a vanilla-style single ingredient/result shape (no
    # *_inputs/_outputs arrays, no eu/duration — it's a manual tool). The parser
    # normalizes count->amount and result.id->item so its I/O imports correctly.
    recipe = tmp_path / "data" / NS / "recipe"
    _write(
        recipe / "forge_hammer" / "bolt.json",
        {
            "type": f"{NS}:forge_hammer",
            "ingredient": {"item": f"{NS}:bronze_double_ingot"},
            "result": {"count": 4, "id": f"{NS}:bronze_bolt"},
        },
    )
    lib = import_mi_library(tmp_path, machine_types=("forge_hammer",)).library
    r = next(r for r in lib.recipes if r.machine_type == "forge_hammer")
    assert [(s.resource_id, s.quantity) for s in r.inputs] == [
        (f"{NS}:bronze_double_ingot", 1.0)
    ]
    assert [(s.resource_id, s.quantity) for s in r.outputs] == [
        (f"{NS}:bronze_bolt", 4.0)
    ]
    # No duration in the data → a manual (zero-time) recipe, no energy draw.
    assert r.time_seconds == 0.0 and r.eu_per_tick is None
    # A tag ingredient also parses (ores → raw metal).
    _write(
        recipe / "forge_hammer" / "ore.json",
        {
            "type": f"{NS}:forge_hammer",
            "ingredient": {"tag": "c:ores/gold"},
            "result": {"count": 2, "id": "minecraft:raw_gold"},
        },
    )
    lib2 = import_mi_library(tmp_path, machine_types=("forge_hammer",)).library
    ore = next(r for r in lib2.recipes if r.id.endswith("forge_hammer/ore"))
    assert ore.inputs[0].tag == "c:ores/gold"


def test_energy_machines_generated_as_converters(mi_root):
    lib = import_mi_library(mi_root).library
    defs = {d.id: d for d in lib.machine_defs}
    items = {i.id: i for i in lib.items}

    # All 15 energy machines are present as converter defs.
    energy = {k for k in defs if k.startswith("mi.energy.")}
    assert len(energy) == 15
    assert "mi.energy.large_steam_boiler" in energy
    assert "mi.energy.plasma_turbine" in energy

    # Electricity is a new energy-category carrier; steam/water/plasma exist.
    assert items["mi:electricity"].category is Category.ENERGY
    assert items["mi:electricity"].eu is None
    assert items[f"{NS}:steam"].category is Category.LIQUID

    # Boiler: fuel (player-chosen, unset) → steam, water material 16:1, w 0.8.
    # Rate is the jar's 256 EU/t stored ×20 for the per-second engine.
    boiler = defs["mi.energy.large_steam_boiler"]
    assert boiler.energy_source.value == "fuel"
    assert boiler.source_item_id is None
    assert boiler.output_item_id == f"{NS}:steam"
    assert boiler.input_item_id == "minecraft:water"
    assert boiler.material_ratio == 16
    assert boiler.efficiency == 0.8
    assert boiler.max_fluid_rate == 256 * 20

    # HV steam turbine: steam → electricity, 512 EU/t ×20.
    turbine = defs["mi.energy.hv_steam_turbine"]
    assert turbine.source_item_id == f"{NS}:steam"
    assert turbine.output_item_id == "mi:electricity"
    assert turbine.max_fluid_rate == 512 * 20

    # LV diesel generator: diesel fuel → electricity, 64 EU/t ×20.
    gen = defs["mi.energy.lv_diesel_generator"]
    assert gen.energy_source.value == "fuel"
    assert gen.source_item_id == f"{NS}:diesel"
    assert gen.max_fluid_rate == 64 * 20


def test_steam_tiers_added_for_steam_machines(mi_root):
    lib = import_mi_library(mi_root).library
    defs = {d.id: d for d in lib.machine_defs}
    electric = defs["mi.machine.macerator"]
    bronze = defs["mi.machine.macerator.bronze"]
    steel = defs["mi.machine.macerator.steel"]
    # Steam tiers run the same recipes as the electric variant...
    assert bronze.allowed_recipe_ids == electric.allowed_recipe_ids
    # ...but are powered by steam (a liquid carrier), not electricity...
    assert bronze.energy_source.value == "liquid"
    assert bronze.source_item_id == f"{NS}:steam"
    assert bronze.name == "Bronze Macerator"
    # ...and clamp to the tier's EU/tick ceiling (jar MachineTier: bronze 2, steel 4).
    assert (bronze.voltage_tier, bronze.max_eu_per_tick) == ("Bronze", 2)
    assert (steel.voltage_tier, steel.max_eu_per_tick) == ("Steel", 4)
    # The steam carrier item is registered.
    assert any(i.id == f"{NS}:steam" for i in lib.items)
    # An electric-only machine (assembler) has no steam tiers.
    assert "mi.machine.assembler.bronze" not in defs
    assert "mi.machine.assembler.steel" not in defs


def test_water_pumps_added(mi_root):
    imp = import_mi_library(mi_root)
    recipes = {r.id: r for r in imp.library.recipes}
    defs = {d.id: d for d in imp.library.machine_defs}
    # Pump water rate = multiplier × 200 mB/s (bronze 1, steel 2, electric 16).
    assert recipes["mi.pump.bronze_water_pump"].outputs[0].quantity == 200
    assert recipes["mi.pump.steel_water_pump"].outputs[0].quantity == 400
    assert recipes["mi.pump.electric_water_pump"].outputs[0].quantity == 3200
    # Every pump draws a fixed 1 EU/tick, and outputs water.
    bronze = recipes["mi.pump.bronze_water_pump"]
    assert bronze.eu_per_tick == 1 and bronze.inputs == []
    assert bronze.outputs[0].item_id == "minecraft:water"
    # Bronze/steel pumps run on steam; the electric pump on electricity.
    assert defs["mi.pump.bronze_water_pump.machine"].source_item_id == f"{NS}:steam"
    elec = defs["mi.pump.electric_water_pump.machine"]
    assert elec.energy_source.value == "electric"
    assert elec.source_item_id == "mi:electricity"


def test_storage_added_with_jar_capacities(mi_root):
    lib = import_mi_library(mi_root).library
    defs = {d.id: d for d in lib.machine_defs}
    # Material tanks (buckets ×1000 mB) and barrels (stacks ×64 items).
    assert defs["mi.storage.bronze_tank"].storage.capacity == 4000
    assert defs["mi.storage.titanium_tank"].storage.capacity == 1024000
    assert defs["mi.storage.bronze_tank"].storage.category.value == "liquid"
    assert defs["mi.storage.bronze_barrel"].storage.capacity == 32 * 64
    assert defs["mi.storage.bronze_barrel"].storage.category.value == "solid"
    # Single-block configurable tank/chest (jar: 16 000 mB, 27 slots).
    assert defs["mi.storage.configurable_tank"].storage.capacity == 16000
    assert defs["mi.storage.configurable_chest"].storage.capacity == 27 * 64
    # Large tank multiblock (64 000 mB per inner block).
    assert defs["mi.storage.large_tank"].storage.capacity == 64000 * 27


def test_include_energy_false_skips_energy_machines_and_pumps(mi_root):
    lib = import_mi_library(mi_root, include_energy=False).library
    assert not any(d.id.startswith("mi.energy.") for d in lib.machine_defs)
    assert all(i.id != "mi:electricity" for i in lib.items)
    # Pumps ride with the energy bundle (they need the carriers), so they are
    # skipped too — but storage and the steam machine tiers are not.
    assert not any(d.id.startswith("mi.pump.") for d in lib.machine_defs)
    assert any(d.id.startswith("mi.storage.") for d in lib.machine_defs)
    assert any(d.id == "mi.machine.macerator.bronze" for d in lib.machine_defs)


def test_uncatalogued_type_imports_without_a_ceiling(tmp_path):
    # A type not in the catalog (here a multiblock) still imports (generic), just
    # with no clamp — the honest default for machines whose ceiling isn't a fixed
    # single-block tier.
    recipe = tmp_path / "data" / NS / "recipe"
    _write(
        recipe / "vacuum_freezer" / "x.json",
        {
            "type": f"{NS}:vacuum_freezer",
            "duration": 100,
            "eu": 2,
            "fluid_inputs": [{"fluid": f"{NS}:helium", "amount": 100}],
            "fluid_outputs": [{"fluid": f"{NS}:liquid_helium", "amount": 100}],
        },
    )
    lib = import_mi_library(tmp_path, machine_types=("vacuum_freezer",)).library
    vf = next(d for d in lib.machine_defs if d.id == "mi.machine.vacuum_freezer")
    assert vf.voltage_tier is None and vf.max_eu_per_tick is None


# --------------------------------------------------------------------------- #
# API surface.
# --------------------------------------------------------------------------- #


def test_api_import_mi_dir_merges_and_reports_stats(mi_root):
    api = Api()
    res = api.import_mi_dir(str(mi_root), {"machineTypes": ["macerator", "mixer"]})
    assert res["ok"] is True
    data = res["data"]
    assert data["stats"]["recipesImported"] == 2
    # Two parsed recipes, plus the three hand-authored water-pump recipes.
    assert {r["machineType"] for r in data["world"]["recipes"]} == {
        "macerator",
        "mixer",
        "bronze_water_pump",
        "steel_water_pump",
        "electric_water_pump",
    }
    # The merge landed in the api's live World: the electric processing defs plus
    # the bronze/steel steam tiers (macerator and mixer both have them).
    defs = {d.id for d in api._world.machine_defs}
    assert {d for d in defs if d.startswith("mi.machine.")} == {
        "mi.machine.macerator",
        "mi.machine.macerator.bronze",
        "mi.machine.macerator.steel",
        "mi.machine.mixer",
        "mi.machine.mixer.bronze",
        "mi.machine.mixer.steel",
    }
    assert data["stats"]["energyMachines"] == len(
        [d for d in defs if d.startswith("mi.energy.")]
    )
    assert data["stats"]["energyMachines"] == 15
    # Water pumps and storage ride every import too.
    assert data["stats"]["pumps"] == len([d for d in defs if d.startswith("mi.pump.")])
    assert data["stats"]["pumps"] == 3
    assert data["stats"]["storage"] == len(
        [d for d in defs if d.startswith("mi.storage.")]
    )
    assert data["stats"]["steamMachines"] == 4  # macerator + mixer, bronze & steel


# --------------------------------------------------------------------------- #
# Committed sample project (P5) — loads and computes to the documented balance.
# --------------------------------------------------------------------------- #


def test_sample_mi_project_loads_and_balances():
    world = load_project(EXAMPLES / "mi_lv_battery_line.psim.json")
    rep = compute_world(world).to_dict()
    # Both nodes run at full duty; the battery maker (count 2) exactly feeds the
    # hull maker, so there is no bottleneck and the hull is the product.
    assert rep["nodeDuty"] == {"assembler_battery": 1.0, "assembler_hull": 1.0}
    assert rep["netEnergy"] == -480.0  # 8 EU/t ×20 × (2 + 1) nodes
    assert rep["bottlenecks"] == []
    assert rep["externalOutputs"].get(
        "modern_industrialization:basic_machine_hull"
    ) == pytest.approx(0.1)


def test_api_import_mi_dir_bad_path_is_error_envelope(tmp_path):
    api = Api()
    res = api.import_mi_dir(str(tmp_path / "nope"))
    assert res["ok"] is False
    assert "type" in res["error"] and "message" in res["error"]


# --------------------------------------------------------------------------- #
# Solid-fuel stored energy (furnace_fuels data map) and vanilla c: tags the jar
# references but NeoForge — not the mod — ships.
# --------------------------------------------------------------------------- #


def test_solid_fuel_eu_from_furnace_fuels(tmp_path):
    """A solid fuel's furnace ``burn_time`` becomes its stored ``eu``.

    Both a concrete key and a ``#tag`` key resolve: the tag carries its burn-time
    to each member once membership is resolved. A non-fuel item stays ``eu``-less.
    """

    recipe = tmp_path / "data" / NS / "recipe"
    _write(
        recipe / "compressor" / "coal.json",
        {
            "type": f"{NS}:compressor",
            "duration": 200,
            "eu": 2,
            # Referencing the tag registers it, so its burn-time can propagate.
            "item_inputs": [{"tag": "c:dusts/coal"}],
            "item_outputs": [{"item": f"{NS}:coke"}, {"item": f"{NS}:coke_block"}],
        },
    )
    _write(
        tmp_path / "data" / "neoforge" / "data_maps" / "item" / "furnace_fuels.json",
        {
            "values": {
                "#c:dusts/coal": {"burn_time": 1600},
                f"{NS}:coke": {"burn_time": 6400},
            }
        },
    )
    _write(
        tmp_path / "data" / "c" / "tags" / "item" / "dusts" / "coal.json",
        {"values": [f"{NS}:coal_dust"]},
    )
    lib = import_mi_library(tmp_path).library
    assert _item(lib, f"{NS}:coke").eu == 6400  # concrete furnace-fuel key
    assert _item(lib, f"{NS}:coal_dust").eu == 1600  # via the #c:dusts/coal tag
    assert _item(lib, f"{NS}:coke_block").eu is None  # not a listed fuel


def test_vanilla_ore_tag_resolved_from_convention(tmp_path):
    """A ``c:ores/<vanilla>`` tag the jar never ships still gets its members.

    MI relies on NeoForge/vanilla to populate ``c:ores/iron`` & co.; that data
    isn't in the mod jar, so the importer fills the canonical vanilla member ids
    by convention (and registers them, so the ore is selectable / findable and a
    producer of it can satisfy the recipe). A modded material stays unresolved.
    """

    recipe = tmp_path / "data" / NS / "recipe"
    _write(
        recipe / "macerator" / "iron_and_osmium.json",
        {
            "type": f"{NS}:macerator",
            "duration": 200,
            "eu": 2,
            "item_inputs": [{"tag": "c:ores/iron"}],
            "item_outputs": [{"item": f"{NS}:iron_dust"}],
        },
    )
    _write(
        recipe / "macerator" / "osmium.json",
        {
            "type": f"{NS}:macerator",
            "duration": 200,
            "eu": 2,
            "item_inputs": [{"tag": "c:ores/osmium"}],
            "item_outputs": [{"item": f"{NS}:osmium_dust"}],
        },
    )
    # No c/tags/item/ores/*.json shipped — exactly as in the real MI jar.
    lib = import_mi_library(tmp_path).library
    iron_tag = _item(lib, "c:ores/iron")
    assert iron_tag.is_tag
    assert set(iron_tag.members or []) == {
        "minecraft:iron_ore",
        "minecraft:deepslate_iron_ore",
    }
    # The vanilla member items are registered (solid), so they show in the finder.
    assert _item(lib, "minecraft:iron_ore").category is Category.SOLID
    # A modded material has no vanilla convention → left unresolved, no phantoms.
    assert (_item(lib, "c:ores/osmium").members or []) == []


# --------------------------------------------------------------------------- #
# Generic discovery (any mod's machine types) + multiblock block variants.
# --------------------------------------------------------------------------- #


def _def(lib, did):
    return next((d for d in lib.machine_defs if d.id == did), None)


def test_discovers_any_namespaced_machine_type(tmp_path):
    """Without an allowlist, a different mod's machine recipe type is imported.

    A namespaced type with a machine shape is picked up; the ``minecraft``
    namespace and bare vanilla types are skipped.
    """

    recipe = tmp_path / "data" / "coolmod" / "recipe"
    _write(
        recipe / "grinder" / "ore.json",
        {
            "type": "coolmod:grinder",
            "duration": 100,
            "eu": 4,
            "item_inputs": [{"item": "coolmod:raw_ore"}],
            "item_outputs": [{"item": "coolmod:powder", "amount": 2}],
        },
    )
    _write(
        recipe / "craft.json",
        {"type": "minecraft:crafting_shaped", "result": {"id": "x"}},
    )
    _write(recipe / "bare.json", {"type": "crafting_shaped", "result": {"id": "y"}})
    lib = import_mi_library(
        recipe.parent, include_energy=False
    ).library  # root at the mod's data dir
    assert _def(lib, "mi.machine.grinder") is not None
    assert {r.machine_type for r in lib.recipes} == {"grinder"}
    assert len(lib.recipes) == 1  # the two vanilla recipes were skipped


def test_explicit_machine_types_still_narrow(tmp_path):
    """Passing ``machine_types`` keeps only those bare type names."""

    recipe = tmp_path / "data" / NS / "recipe"
    _write(
        recipe / "macerator" / "a.json",
        {
            "type": f"{NS}:macerator",
            "eu": 2,
            "duration": 20,
            "item_outputs": [{"item": f"{NS}:dust"}],
        },
    )
    _write(
        recipe / "mixer" / "b.json",
        {
            "type": f"{NS}:mixer",
            "eu": 2,
            "duration": 20,
            "item_outputs": [{"item": f"{NS}:mix"}],
        },
    )
    lib = import_mi_library(
        tmp_path, machine_types=("macerator",), include_energy=False
    ).library
    assert {r.machine_type for r in lib.recipes} == {"macerator"}


def test_multiblock_quarry_and_blast_furnace_variants(tmp_path):
    """A type run by named steam/electric blocks yields those variants, not one def."""

    recipe = tmp_path / "data" / NS / "recipe"
    _write(
        recipe / "quarry" / "stone.json",
        {
            "type": f"{NS}:quarry",
            "eu": 8,
            "duration": 100,
            "item_outputs": [{"item": f"{NS}:cobblestone"}],
        },
    )
    _write(
        recipe / "blast_furnace" / "steel.json",
        {
            "type": f"{NS}:blast_furnace",
            "eu": 16,
            "duration": 200,
            "item_inputs": [{"item": f"{NS}:iron"}],
            "item_outputs": [{"item": f"{NS}:steel"}],
        },
    )
    lib = import_mi_library(tmp_path, include_energy=False).library

    # The generic single def is replaced by the named variant blocks.
    assert _def(lib, "mi.machine.quarry") is None
    assert _def(lib, "mi.machine.blast_furnace") is None
    sq = _def(lib, "mi.machine.steam_quarry")
    eq = _def(lib, "mi.machine.electric_quarry")
    assert sq is not None and eq is not None
    assert sq.energy_source.value == "liquid" and sq.source_item_id == f"{NS}:steam"
    assert eq.energy_source.value == "electric"
    # Both variants run the same quarry recipes.
    assert sq.allowed_recipe_ids == eq.allowed_recipe_ids
    assert _def(lib, "mi.machine.steam_blast_furnace") is not None
    assert _def(lib, "mi.machine.electric_blast_furnace") is not None
