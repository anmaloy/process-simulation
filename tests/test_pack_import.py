"""Whole-modpack import: merged jars + pack data, conditions, composites, export.

Hermetic: each test builds a tiny fake launcher (``libraries/`` with stand-in
vanilla and NeoForge jars) and instance (``mods/*.jar`` written with
``zipfile``, a ``kubejs/`` data folder, ``mmc-pack.json``) in ``tmp_path``, and
points ``APPDATA`` / the home dir there so the machine's real launcher is never
read. The bar: data split across jars resolves (a tag one jar defines satisfies
another jar's recipe), NeoForge composite ingredients become "any of" inputs with
real members, load conditions and absent-mod recipe types drop recipes the game
wouldn't load, the pack's own data overrides jars, the runtime export replaces
the static view, and a re-import extends existing machines with new recipes
without reverting edits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from process_sim.api import Api
from process_sim.io import (
    ConflictPolicy,
    Library,
    import_library,
    import_pack,
    install_exporter,
    merge_libraries,
)
from process_sim.io.ingredients import (
    Ingredient,
    is_fluid_ingredient,
    parse_ingredient,
    resolve_members,
)
from process_sim.io.pack_import import (
    EXPORT_FORMAT,
    EXPORT_RELPATH,
    EXPORTER_INSTALL_RELPATH,
    EXPORTER_SCRIPT,
    find_base_jars,
    find_game_dir,
)
from process_sim.model import Category, Item, MachineDef, Recipe, Stack, World

MI = "modern_industrialization"
ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Fixture builders.
# --------------------------------------------------------------------------- #


def _jar(path: Path, files: dict[str, object], mod_ids: tuple[str, ...] = ()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        if mod_ids:
            toml = "".join(f'[[mods]]\nmodId = "{m}"\n' for m in mod_ids)
            z.writestr("META-INF/neoforge.mods.toml", toml)
        for name, obj in files.items():
            z.writestr(name, obj if isinstance(obj, str) else json.dumps(obj))
    return path


def _write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("APPDATA", "HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    return home


@pytest.fixture
def pack(tmp_path, isolated_home):
    """A launcher with one instance: two content mods, MI, Create, a pack datapack."""

    launcher = tmp_path / "launcher"
    inst = launcher / "instances" / "Test Pack"
    game = inst / "minecraft"
    _write(
        inst / "mmc-pack.json",
        {
            "components": [
                {"uid": "net.minecraft", "version": "9.9.9"},
                {"uid": "net.neoforged", "version": "99.1.0"},
            ]
        },
    )
    libs = launcher / "libraries"
    # Vanilla: a smelting recipe; NeoForge: the shared c: iron tags.
    _jar(
        libs / "com/mojang/minecraft/9.9.9/minecraft-9.9.9-client.jar",
        {
            "data/minecraft/recipe/iron_ingot_from_smelting_raw_iron.json": {
                "type": "minecraft:smelting",
                "cookingtime": 200,
                "ingredient": {"item": "minecraft:raw_iron"},
                "result": {"id": "minecraft:iron_ingot"},
            },
            "data/minecraft/recipe/stick.json": {
                "type": "minecraft:crafting_shaped",
                "pattern": ["#", "#"],
                "key": {"#": {"tag": "minecraft:planks"}},
                "result": {"id": "minecraft:stick", "count": 4},
            },
            "assets/minecraft/lang/en_us.json": {
                "item.minecraft.iron_ingot": "Iron Ingot",
                "item.minecraft.raw_iron": "Raw Iron",
            },
        },
    )
    _jar(
        libs / "net/neoforged/neoforge/99.1.0/neoforge-99.1.0-universal.jar",
        {
            "data/c/tags/item/ingots/iron.json": {"values": ["minecraft:iron_ingot"]},
            "data/c/tags/item/dusts/iron.json": {"values": [f"{MI}:iron_dust"]},
            "data/neoforge/data_maps/item/furnace_fuels.json": {
                "values": {"minecraft:coal": {"burn_time": 1600}}
            },
        },
    )

    mods = game / "mods"
    # MI: defines the nickel tags + names; a macerator recipe.
    _jar(
        mods / "Modern-Industrialization.jar",
        {
            f"data/{MI}/recipe/materials/nickel/macerator/ingot.json": {
                "type": f"{MI}:macerator",
                "eu": 2,
                "duration": 100,
                "item_inputs": [{"tag": "c:ingots/nickel", "amount": 1}],
                "item_outputs": [{"item": f"{MI}:nickel_dust", "amount": 1}],
            },
            "data/c/tags/item/ingots/nickel.json": {"values": [f"{MI}:nickel_ingot"]},
            "data/c/tags/item/dusts/nickel.json": {
                "values": [
                    f"{MI}:nickel_dust",
                    {"id": "absentmod:nickel_dust", "required": False},
                ]
            },
            f"assets/{MI}/lang/en_us.json": {
                f"item.{MI}.nickel_dust": "Nickel Dust",
                f"item.{MI}.nickel_ingot": "Nickel Ingot",
                f"item.{MI}.invar_ingot": "Invar Ingot",
                f"block.{MI}.electric_macerator": "Electric Macerator",
            },
        },
        mod_ids=(MI,),
    )
    # EI: an alloy smelter recipe whose inputs are NeoForge compounds over tags
    # another jar defines; a conditional recipe for an absent mod; a recipe
    # whose type belongs to an absent mod.
    _jar(
        mods / "extended-industrialization.jar",
        {
            "data/extended_industrialization/recipe/invar/ingot.json": {
                "type": "extended_industrialization:alloy_smelter",
                "eu": 4,
                "duration": 200,
                "item_inputs": [
                    {
                        "type": "neoforge:compound",
                        "amount": 2,
                        "children": [{"tag": "c:dusts/iron"}, {"tag": "c:ingots/iron"}],
                    },
                    {
                        "type": "neoforge:compound",
                        "amount": 1,
                        "children": [
                            {"tag": "c:dusts/nickel"},
                            {"tag": "c:ingots/nickel"},
                        ],
                    },
                ],
                "item_outputs": [{"item": f"{MI}:invar_ingot", "amount": 3}],
            },
            "data/extended_industrialization/recipe/compat/absent.json": {
                "neoforge:conditions": [
                    {"type": "neoforge:mod_loaded", "modid": "absentmod"}
                ],
                "type": "extended_industrialization:alloy_smelter",
                "eu": 4,
                "duration": 200,
                "item_inputs": [{"item": "absentmod:thing"}],
                "item_outputs": [{"item": "absentmod:other"}],
            },
            "data/extended_industrialization/recipe/compat/not_absent.json": {
                "neoforge:conditions": [
                    {
                        "type": "neoforge:not",
                        "value": {"type": "neoforge:mod_loaded", "modid": "absentmod"},
                    }
                ],
                "type": "extended_industrialization:alloy_smelter",
                "eu": 4,
                "duration": 100,
                "item_inputs": [{"tag": "c:ingots/iron", "amount": 1}],
                "item_outputs": [{"item": f"{MI}:iron_dust"}],
            },
            "data/absentmod/recipe/crusher/x.json": {
                "type": "absentmod:crusher",
                "eu": 4,
                "duration": 10,
                "item_inputs": [{"item": "minecraft:stone"}],
                "item_outputs": [{"item": "minecraft:gravel"}],
            },
            "data/extended_industrialization/recipe/to_override.json": {
                "type": "extended_industrialization:alloy_smelter",
                "eu": 4,
                "duration": 999,
                "item_inputs": [{"item": "minecraft:stone"}],
                "item_outputs": [{"item": "minecraft:gravel"}],
            },
        },
        mod_ids=("extended_industrialization",),
    )
    # Create: mixed item/fluid ingredients, a fluid result, no EU (passive).
    _jar(
        mods / "create.jar",
        {
            "data/create/recipe/mixing/tea.json": {
                "type": "create:mixing",
                "ingredients": [
                    {"tag": "minecraft:leaves"},
                    {
                        "type": "neoforge:single",
                        "amount": 250,
                        "fluid": "minecraft:water",
                    },
                ],
                "results": [{"amount": 500, "id": "create:tea"}],
            },
            "data/create/recipe/crushing/raw_iron.json": {
                "type": "create:crushing",
                "processing_time": 400,
                "ingredients": [{"item": "minecraft:raw_iron"}],
                "results": [
                    {"id": "create:crushed_raw_iron"},
                    {"id": "create:experience_nugget", "chance": 0.75},
                ],
            },
            "data/minecraft/tags/item/leaves.json": {
                "values": ["minecraft:oak_leaves"]
            },
        },
        mod_ids=("create",),
    )
    # The pack's own datapack (kubejs/data): overrides a jar recipe by id and
    # adds members to a tag.
    _write(
        game / "kubejs/data/extended_industrialization/recipe/to_override.json",
        {
            "type": "extended_industrialization:alloy_smelter",
            "eu": 8,
            "duration": 20,
            "item_inputs": [{"item": "minecraft:cobblestone"}],
            "item_outputs": [{"item": "minecraft:gravel"}],
        },
    )
    _write(
        game / "kubejs/data/c/tags/item/ingots/nickel.json",
        {"values": ["packmod:nickel_ingot"]},
    )
    _write(
        game / "kubejs/assets/kubejs/lang/en_us.json",
        {"item.packmod.nickel_ingot": "Pack Nickel"},
    )
    return inst


def _by_id(entities):
    return {e.id: e for e in entities}


# --------------------------------------------------------------------------- #
# Ingredients.
# --------------------------------------------------------------------------- #


def test_parse_ingredient_shapes():
    tag = Ingredient("tag", "c:dusts/iron")
    assert parse_ingredient({"tag": "c:dusts/iron"}) == tag
    assert parse_ingredient("#c:dusts/iron") == tag
    assert parse_ingredient({"item": "stone"}) == Ingredient("item", "minecraft:stone")
    assert parse_ingredient({"id": "create:tea", "amount": 5}) == Ingredient(
        "item", "create:tea"
    )
    # A one-element compound / array collapses to the element itself.
    assert parse_ingredient(
        {"type": "neoforge:compound", "ingredients": [{"item": "a:b"}]}
    ) == Ingredient("item", "a:b")
    union = parse_ingredient([{"tag": "c:x"}, {"item": "a:b"}, {"tag": "c:x"}])
    assert union.kind == "union" and len(union.parts) == 2
    assert union.canonical() == "#c:x|a:b"
    diff = parse_ingredient(
        {
            "type": "neoforge:difference",
            "base": {"tag": "c:ores"},
            "subtracted": {"item": "a:b"},
        }
    )
    assert diff.kind == "diff" and diff.canonical() == "#c:ores-a:b"
    inter = parse_ingredient(
        {"type": "neoforge:intersection", "children": [{"tag": "c:a"}, {"tag": "c:b"}]}
    )
    assert inter.kind == "inter"
    comp = parse_ingredient(
        {"type": "neoforge:components", "items": "minecraft:potion", "components": {}}
    )
    assert comp == Ingredient("item", "minecraft:potion")
    sized = parse_ingredient({"ingredient": {"tag": "c:gems"}, "count": 3})
    assert sized == Ingredient("tag", "c:gems")
    assert parse_ingredient({"type": "neoforge:empty"}) is None
    assert parse_ingredient({}) is None


def test_fluid_detection_and_member_resolution():
    assert is_fluid_ingredient({"type": "neoforge:single", "fluid": "minecraft:water"})
    assert is_fluid_ingredient({"type": "neoforge:tag", "tag": "c:milk"})
    assert not is_fluid_ingredient({"tag": "c:dusts/iron"})
    assert not is_fluid_ingredient(
        {"type": "neoforge:compound", "children": [{"item": "a:b"}]}
    )

    tags = {"c:a": {"x", "y", "z"}, "c:b": {"y"}}
    lookup = lambda t: tags.get(t, set())  # noqa: E731
    ing = parse_ingredient(
        {
            "type": "neoforge:difference",
            "base": [{"tag": "c:a"}, {"item": "w"}],
            "subtracted": {"tag": "c:b"},
        }
    )
    assert resolve_members(ing, lookup) == {"minecraft:w", "x", "z"}


# --------------------------------------------------------------------------- #
# Static pack import.
# --------------------------------------------------------------------------- #


def test_find_game_dir_and_base_jars(pack):
    game = find_game_dir(pack)
    assert game == pack / "minecraft"
    assert find_game_dir(game) == game
    names = [p.name for p in find_base_jars(game)]
    assert names == ["minecraft-9.9.9-client.jar", "neoforge-99.1.0-universal.jar"]
    with pytest.raises(Exception, match="not a modpack instance"):
        find_game_dir(pack.parent)


def test_cross_jar_tags_resolve_composite_inputs(pack):
    imp = import_pack(pack)
    lib = imp.library
    recipes, items = _by_id(lib.recipes), _by_id(lib.items)
    invar = recipes["extended_industrialization:invar/ingot"]
    ins = {s.resource_id: s.quantity for s in invar.inputs}
    iron_any = "any:#c:dusts/iron|#c:ingots/iron"
    nickel_any = "any:#c:dusts/nickel|#c:ingots/nickel"
    assert ins == {iron_any: 2.0, nickel_any: 1.0}
    # Members come from three different sources: NeoForge (iron tags), MI
    # (nickel tags), and the pack's kubejs data (an added nickel ingot). The
    # optional member of an absent mod is dropped.
    assert items[iron_any].is_tag
    assert set(items[iron_any].members) == {"minecraft:iron_ingot", f"{MI}:iron_dust"}
    assert set(items[nickel_any].members) == {
        f"{MI}:nickel_dust",
        f"{MI}:nickel_ingot",
        "packmod:nickel_ingot",
    }
    assert items[nickel_any].name == "Dusts Nickel or Ingots Nickel"
    # Members are real, named items (names from each mod's lang + the pack's).
    assert items[f"{MI}:nickel_ingot"].name == "Nickel Ingot"
    assert items["packmod:nickel_ingot"].name == "Pack Nickel"
    assert imp.stats["composites"] == 2
    assert not imp.stats["emptyTags"]


def test_conditions_absent_mod_types_and_pack_overrides(pack):
    imp = import_pack(pack)
    ids = {r.id for r in imp.library.recipes}
    assert "extended_industrialization:compat/absent" not in ids
    assert "extended_industrialization:compat/not_absent" in ids
    assert "absentmod:crusher/x" not in ids
    assert imp.stats["conditionsFailed"] >= 2
    # The pack's kubejs/data wins over the jar for the same recipe id.
    over = _by_id(imp.library.recipes)["extended_industrialization:to_override"]
    assert over.time_seconds == 1.0 and over.eu_per_tick == 8
    assert over.inputs[0].item_id == "minecraft:cobblestone"
    # Crafting-table recipes are not machine recipes; vanilla smelting is.
    assert "minecraft:stick" not in ids
    assert "minecraft:iron_ingot_from_smelting_raw_iron" in ids


def test_ids_names_and_machine_defs(pack):
    imp = import_pack(pack)
    lib = imp.library
    # MI recipes keep the single-jar importer's ``mi:<path>`` ids.
    assert "mi:materials/nickel/macerator/ingot" in {r.id for r in lib.recipes}
    assert lib.name == "Test Pack" and imp.stats["pack"] == "Test Pack"
    assert imp.stats["mods"] >= 3 and imp.stats["usedExport"] is False
    defs = _by_id(lib.machine_defs)
    # A recipe type with an EU draw is an electric consumer…
    alloy = defs["mi.machine.alloy_smelter"]
    assert alloy.energy_source.value == "electric"
    # …one without any (Create) is passive: no energy port, never unpowered.
    mixing = defs["mi.machine.mixing"]
    assert mixing.energy_source is None
    assert all(sl.category.value != "energy" for sl in mixing.slots)
    # Create's mixed lists split into item/fluid stacks; chance is probability.
    recipes = _by_id(lib.recipes)
    tea = recipes["create:mixing/tea"]
    cats = {
        s.resource_id: _by_id(lib.items)[s.resource_id].category.value
        for s in tea.inputs
    }
    assert cats == {"minecraft:leaves": "solid", "minecraft:water": "liquid"}
    assert tea.outputs[0].item_id == "create:tea" and tea.outputs[0].quantity == 500
    assert _by_id(lib.items)["create:tea"].category.value == "liquid"
    crush = recipes["create:crushing/raw_iron"]
    assert crush.time_seconds == 20.0
    assert {s.item_id: s.probability for s in crush.outputs} == {
        "create:crushed_raw_iron": None,
        "create:experience_nugget": 0.75,
    }


# --------------------------------------------------------------------------- #
# Runtime export overlay.
# --------------------------------------------------------------------------- #


def _export(game: Path, **extra) -> Path:
    path = game / EXPORT_RELPATH
    body = {
        "format": EXPORT_FORMAT,
        "version": 1,
        "exportedAt": "2026-11-24T00:00:00Z",
        "recipes": {
            # A script-added recipe for a script-registered material.
            "statech:kernite_dust": json.dumps(
                {
                    "type": f"{MI}:electrolyzer",
                    "eu": 16,
                    "duration": 600,
                    "item_inputs": [{"amount": 9, "tag": "c:dusts/kernite"}],
                    "item_outputs": [{"amount": 2, "item": f"{MI}:sodium_dust"}],
                }
            ),
            f"{MI}:materials/nickel/macerator/ingot": json.dumps(
                {
                    "type": f"{MI}:macerator",
                    "eu": 2,
                    "duration": 100,
                    "item_inputs": [{"tag": "c:ingots/nickel", "amount": 1}],
                    "item_outputs": [{"item": f"{MI}:nickel_dust", "amount": 1}],
                }
            ),
        },
        "itemTags": {"c:dusts/kernite": [f"{MI}:kernite_dust"]},
        "fluidTags": {},
        "itemNames": {
            f"{MI}:kernite_dust": "Kernite Dust",
            "x:untranslated": "item.x.untranslated",
        },
        "fluidNames": {},
        **extra,
    }
    _write(path, body)
    return path


def test_export_replaces_static_recipes_and_tags(pack):
    game = pack / "minecraft"
    _export(game)
    imp = import_pack(pack)
    ids = {r.id for r in imp.library.recipes if not r.id.startswith("mi.pump.")}
    # Only what the game actually loaded: the jar-only invar recipe is gone
    # (the export is authoritative), the script-added one is in.
    assert ids == {"statech:kernite_dust", "mi:materials/nickel/macerator/ingot"}
    items = _by_id(imp.library.items)
    assert items["c:dusts/kernite"].members == [f"{MI}:kernite_dust"]
    assert items[f"{MI}:kernite_dust"].name == "Kernite Dust"
    # Tags the export doesn't list fall back to the static merge.
    assert f"{MI}:nickel_ingot" in items["c:ingots/nickel"].members
    assert imp.stats["usedExport"] is True and imp.stats["exportStale"] is False
    assert imp.stats["exportedAt"] == "2026-11-24T00:00:00Z"


def test_export_staleness_and_bad_export(pack):
    game = pack / "minecraft"
    exp = _export(game)
    old = time.time() - 3600
    os.utime(exp, (old, old))
    assert import_pack(pack).stats["exportStale"] is True
    _write(exp, {"format": "something-else"})
    with pytest.raises(Exception, match="not a process-sim pack export"):
        import_pack(pack)


def test_install_exporter(pack):
    game = pack / "minecraft"
    with pytest.raises(Exception, match="no KubeJS"):
        install_exporter(pack)
    _jar(game / "mods" / "kubejs-neoforge-2101.7.2.jar", {}, mod_ids=("kubejs",))
    dest = install_exporter(pack)
    assert dest == game / EXPORTER_INSTALL_RELPATH
    assert dest.read_bytes() == EXPORTER_SCRIPT.read_bytes()
    assert import_pack(pack).stats["exporterInstalled"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_exporter_script_against_mock_kubejs(tmp_path):
    """Drive the shipped KubeJS script with a mock of the objects it touches."""

    out = tmp_path / "export.json"
    subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "kubejs_exporter_mock.js"),
            str(EXPORTER_SCRIPT),
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == EXPORT_FORMAT
    # Removed recipes and ones the game failed to load are excluded; a recipe
    # loaded in game but never seen as JSON is counted.
    assert sorted(data["recipes"]) == [
        f"{MI}:materials/iron/macerator/ore",
        "statech:kernite_dust",
    ]
    assert json.loads(data["recipes"]["statech:kernite_dust"])["eu"] == 16
    assert data["missingFromExport"] == 1
    assert data["itemTags"] == {"c:dusts/kernite": [f"{MI}:kernite_dust"]}
    assert data["fluidNames"] == {"minecraft:water": "Water"}


# --------------------------------------------------------------------------- #
# Re-import extends existing machines / tags without reverting edits.
# --------------------------------------------------------------------------- #


def _item(iid: str, **kw) -> Item:
    return Item(id=iid, name=iid, category=Category.SOLID, **kw)


def _recipe(rid: str) -> Recipe:
    return Recipe(id=rid, time_seconds=1.0, inputs=[], outputs=[Stack(item_id="a:x")])


def test_skip_extends_defs_and_tags_with_only_new_entities():
    world = World(
        items=[_item("a:x"), _item("c:t", is_tag=True, members=["a:x"])],
        recipes=[_recipe("r0"), _recipe("r1")],
        # The user removed r0 from this machine; it must stay removed.
        machine_defs=[MachineDef(id="m", allowed_recipe_ids=["r1"])],
    )
    lib = Library(
        items=[
            _item("a:x"),
            _item("a:y"),
            _item("c:t", is_tag=True, members=["a:x", "a:y"]),
        ],
        recipes=[_recipe("r0"), _recipe("r1"), _recipe("r2")],
        machine_defs=[MachineDef(id="m", allowed_recipe_ids=["r0", "r1", "r2"])],
    )
    res = import_library(world, lib, on_conflict=ConflictPolicy.SKIP)
    m = _by_id(res.world.machine_defs)["m"]
    assert m.allowed_recipe_ids == ["r1", "r2"]
    assert _by_id(res.world.items)["c:t"].members == ["a:x", "a:y"]
    assert res.extended_ids == {"m", "c:t"}
    # The input World is untouched.
    assert world.machine_defs[0].allowed_recipe_ids == ["r1"]
    # The saved-catalog merge follows the same rule.
    base = Library(
        items=world.items, recipes=world.recipes, machine_defs=world.machine_defs
    )
    merged = merge_libraries(base, lib)
    assert _by_id(merged.machine_defs)["m"].allowed_recipe_ids == ["r1", "r2"]
    assert _by_id(merged.items)["c:t"].members == ["a:x", "a:y"]


def test_api_import_pack_persists_and_reimport_adds_nothing(pack, tmp_path):
    libdir = tmp_path / "libs"
    api = Api(autosave_path=tmp_path / "auto.psim.json", library_dir=libdir)
    res = api.import_pack(str(pack))
    assert res["ok"], res
    data = res["data"]
    assert data["stats"]["recipesImported"] > 0 and data["extendedIds"] == []
    assert (libdir / "test_pack.psim-lib.json").is_file()
    again = api.import_pack(str(pack))["data"]
    assert again["extendedIds"] == []
    assert len(again["world"]["recipes"]) == len(data["world"]["recipes"])
    bad = api.import_pack(str(tmp_path / "nowhere"))
    assert bad["ok"] is False and "not a modpack instance" in bad["error"]["message"]
    inst = api.install_pack_exporter(str(pack))
    assert inst["ok"] is False and "no KubeJS" in inst["error"]["message"]
