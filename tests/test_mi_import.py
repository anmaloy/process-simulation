"""Phase 3 — Modern Industrialization recipe import.

Hermetic: each test builds a tiny fake extracted-jar directory (recipes + a lang
map + a fluid-fuels data map) in ``tmp_path``, so the suite never depends on the
uploaded MI jar. The bar: MI's recipe shapes parse (object-or-list I/O, bare vs
namespaced ids, tags, probability, default amount), units convert (ticks→seconds,
eu kept per-tick), items/tags/fluids are built with the right category and names,
and machine defs group by ``type`` with a data-derived slot layout.
"""

import json

import pytest

from process_sim.api import Api
from process_sim.io import import_mi_library
from process_sim.model import Category

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
    assert {r.machine_type for r in imp.library.recipes} == {
        "macerator",
        "mixer",
        "assembler",
        "chemical_reactor",
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
    assert set(defs) == {
        "mi.machine.macerator",
        "mi.machine.mixer",
        "mi.machine.assembler",
        "mi.machine.chemical_reactor",
    }
    asm = defs["mi.machine.assembler"]
    assert asm.energy_source.value == "electric"
    assert asm.base_energy_consumption is None  # energy now rides the recipe
    assert asm.max_eu_per_tick is None  # tier caps deferred to the catalog (P5)
    assert asm.allowed_recipe_ids == ["mi:assembler/gear"]
    layout = {(g.category.value, g.direction.value): g.slots for g in asm.slots}
    # Derived from the one assembler recipe: 2 solid in, 1 solid out, 1 fluid in.
    assert layout == {("solid", "in"): 2, ("solid", "out"): 1, ("liquid", "in"): 1}


# --------------------------------------------------------------------------- #
# API surface.
# --------------------------------------------------------------------------- #


def test_api_import_mi_dir_merges_and_reports_stats(mi_root):
    api = Api()
    res = api.import_mi_dir(str(mi_root), {"machineTypes": ["macerator", "mixer"]})
    assert res["ok"] is True
    data = res["data"]
    assert data["stats"]["recipesImported"] == 2
    assert {r["machineType"] for r in data["world"]["recipes"]} == {
        "macerator",
        "mixer",
    }
    # The merge landed in the api's live World.
    assert len(api._world.machine_defs) == 2


def test_api_import_mi_dir_bad_path_is_error_envelope(tmp_path):
    api = Api()
    res = api.import_mi_dir(str(tmp_path / "nope"))
    assert res["ok"] is False
    assert "type" in res["error"] and "message" in res["error"]
