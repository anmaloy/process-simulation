"""Phase 3 — IO tests.

Acceptance bar (WORKFLOW.md P3):
- save→load a World is lossless (reuse the P1 round-trip guarantee on disk);
- a library exports and re-imports into a *different* World without id
  collisions;
- a malformed / wrong-version file fails with a clear error, not a crash.
"""

import json

import pytest

from process_sim.io import (
    LIBRARY_KIND,
    PROJECT_KIND,
    SCHEMA_VERSION,
    ConflictPolicy,
    Library,
    MalformedFileError,
    ProcessSimIOError,
    UnsupportedVersionError,
    export_library,
    import_library,
    library_from_dict,
    load_library,
    load_project,
    project_from_dict,
    project_to_dict,
    save_library,
    save_project,
)
from process_sim.model import (
    Category,
    Direction,
    Item,
    MachineDef,
    MachineNode,
    Recipe,
    SlotGroup,
    Stack,
    World,
)

FIXTURE_NAMES = ["furnace_chest", "powered_process"]


# --------------------------------------------------------------------------- #
# Project files — save → load is lossless.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_project_file_round_trip_lossless(name, tmp_path, request):
    world = request.getfixturevalue(name)
    path = tmp_path / f"{name}.psim.json"
    save_project(world, path)
    assert load_project(path) == world


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_project_round_trip_in_memory(name, request):
    world = request.getfixturevalue(name)
    assert project_from_dict(project_to_dict(world)) == world


def test_saved_project_has_version_and_kind(furnace_chest, tmp_path):
    path = tmp_path / "proj.json"
    save_project(furnace_chest, path)
    data = json.loads(path.read_text())
    assert data["schemaVersion"] == SCHEMA_VERSION
    assert data["kind"] == PROJECT_KIND
    assert "world" in data


def test_project_preserves_absent_fields_on_disk(furnace_chest, tmp_path):
    # The plain-consumer chest def carries no energy terms; absence must survive.
    path = tmp_path / "proj.json"
    save_project(furnace_chest, path)
    restored = load_project(path)
    chest = next(d for d in restored.machine_defs if d.id == "chest")
    assert chest.base_energy_consumption is None
    assert chest.energy_source is None
    assert chest.efficiency is None


# --------------------------------------------------------------------------- #
# Library — export a subset, re-import into a different World.
# --------------------------------------------------------------------------- #


def _tiny_world() -> World:
    """A minimal World whose ids don't collide with the fixtures."""

    return World(
        name="Tiny",
        items=[
            Item(id="water", name="Water", category=Category.LIQUID),
            Item(id="widget", name="Widget", category=Category.SOLID),
        ],
        recipes=[
            Recipe(
                id="make_widget",
                time_seconds=3,
                inputs=[Stack("water", 2)],
                outputs=[Stack("widget", 1)],
            )
        ],
        machine_defs=[
            MachineDef(
                id="assembler",
                name="Assembler",
                allowed_recipe_ids=["make_widget"],
                slots=[SlotGroup(Category.SOLID, Direction.OUT, 1, 64)],
            )
        ],
        nodes=[MachineNode(id="asm1", machine_def_id="assembler", recipe_id="make_widget")],
    )


def test_export_all_is_the_definitional_subset(powered_process):
    lib = export_library(powered_process)
    assert {i.id for i in lib.items} == {i.id for i in powered_process.items}
    assert {r.id for r in lib.recipes} == {r.id for r in powered_process.recipes}
    assert {d.id for d in lib.machine_defs} == {d.id for d in powered_process.machine_defs}


def test_export_selects_only_requested_ids(powered_process):
    lib = export_library(
        powered_process,
        name="Smelting",
        item_ids=["ore", "ingot"],
        recipe_ids=["smelt"],
        machine_def_ids=["smelter"],
    )
    assert lib.name == "Smelting"
    assert [i.id for i in lib.items] == ["ore", "ingot"]
    assert [r.id for r in lib.recipes] == ["smelt"]
    assert [d.id for d in lib.machine_defs] == ["smelter"]


def test_export_deep_copies(powered_process):
    lib = export_library(powered_process, item_ids=["ore"])
    lib.items[0].name = "Mutated"
    # Mutating the exported copy must not touch the source World.
    assert next(i for i in powered_process.items if i.id == "ore").name == "Iron Ore"


def test_import_into_different_world_no_collisions(powered_process):
    lib = export_library(powered_process)
    target = _tiny_world()
    result = import_library(target, lib)

    merged = result.world
    assert result.id_remap == {}  # no collisions → nothing renamed
    assert result.skipped_ids == set()

    # Every id is still unique after the merge.
    item_ids = [i.id for i in merged.items]
    assert len(item_ids) == len(set(item_ids))
    # Both the target's and the library's entities are present.
    assert set(item_ids) == {"water", "widget", "ore", "ingot", "plate", "coal", "eu"}
    assert {r.id for r in merged.recipes} == {"make_widget", "smelt", "press"}
    # The target's graph is untouched.
    assert [n.id for n in merged.nodes] == ["asm1"]


def test_import_does_not_mutate_target(powered_process):
    target = _tiny_world()
    before = len(target.items)
    import_library(target, export_library(powered_process))
    assert len(target.items) == before


# --------------------------------------------------------------------------- #
# Library — collision handling when importing into an overlapping World.
# --------------------------------------------------------------------------- #


def test_import_rename_remaps_and_rewrites_references(powered_process):
    # Export the smelt recipe + its item, then import back into the SAME world,
    # forcing collisions on every id.
    lib = export_library(
        powered_process, item_ids=["ingot"], recipe_ids=["smelt"], machine_def_ids=["smelter"]
    )
    result = import_library(powered_process, lib, on_conflict=ConflictPolicy.RENAME)
    merged = result.world

    # Colliding ids were renamed, originals untouched.
    assert result.id_remap == {
        "ingot": "ingot-2",
        "smelt": "smelt-2",
        "smelter": "smelter-2",
    }
    ids = [i.id for i in merged.items]
    assert ids.count("ingot") == 1 and "ingot-2" in ids

    # The renamed recipe's output stack now points at the renamed item.
    smelt2 = next(r for r in merged.recipes if r.id == "smelt-2")
    assert smelt2.outputs[0].item_id == "ingot-2"
    assert smelt2.inputs[0].item_id == "ore"  # 'ore' wasn't exported → unchanged

    # The renamed def's allowedRecipeIds point at the renamed recipe.
    smelter2 = next(d for d in merged.machine_defs if d.id == "smelter-2")
    assert smelter2.allowed_recipe_ids == ["smelt-2"]


def test_import_rename_avoids_existing_suffix(powered_process):
    # Pre-seed the collision target AND its first rename candidate so the
    # generator must skip 'ore-2' and land on 'ore-3'.
    seed = export_library(powered_process, item_ids=["ore"], recipe_ids=[], machine_def_ids=[])
    target = import_library(_tiny_world(), seed).world
    target.items.append(Item(id="ore-2", name="Decoy", category=Category.SOLID))
    lib = export_library(powered_process, item_ids=["ore"], recipe_ids=[], machine_def_ids=[])
    result = import_library(target, lib, on_conflict=ConflictPolicy.RENAME)
    assert result.id_remap == {"ore": "ore-3"}


def test_import_skip_drops_colliding_and_keeps_references(powered_process):
    lib = export_library(
        powered_process, item_ids=["ingot"], recipe_ids=["smelt"], machine_def_ids=[]
    )
    result = import_library(powered_process, lib, on_conflict=ConflictPolicy.SKIP)
    merged = result.world

    assert result.skipped_ids == {"ingot", "smelt"}
    assert result.id_remap == {}
    # No duplicates introduced.
    assert [i.id for i in merged.items] == [i.id for i in powered_process.items]
    assert [r.id for r in merged.recipes] == [r.id for r in powered_process.recipes]


def test_import_error_raises_on_collision(powered_process):
    lib = export_library(powered_process, item_ids=["ingot"])
    with pytest.raises(ProcessSimIOError, match="ingot"):
        import_library(powered_process, lib, on_conflict=ConflictPolicy.ERROR)


def test_library_file_round_trip(powered_process, tmp_path):
    lib = export_library(powered_process, name="Full")
    path = tmp_path / "lib.json"
    save_library(lib, path)
    data = json.loads(path.read_text())
    assert data["schemaVersion"] == SCHEMA_VERSION
    assert data["kind"] == LIBRARY_KIND

    loaded = load_library(path)
    assert loaded.name == "Full"
    assert [i.id for i in loaded.items] == [i.id for i in lib.items]
    assert [r.to_dict() for r in loaded.recipes] == [r.to_dict() for r in lib.recipes]


def test_exported_library_imports_into_fresh_world_from_file(powered_process, tmp_path):
    # End-to-end: export → file → load → import into a different World.
    path = tmp_path / "lib.json"
    save_library(export_library(powered_process), path)
    merged = import_library(_tiny_world(), load_library(path)).world
    assert {r.id for r in merged.recipes} == {"make_widget", "smelt", "press"}


# --------------------------------------------------------------------------- #
# Malformed / wrong-version files fail clearly, not with a crash.
# --------------------------------------------------------------------------- #


def test_invalid_json_raises_malformed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json")
    with pytest.raises(MalformedFileError, match="not valid JSON"):
        load_project(path)


def test_missing_version_raises_malformed(tmp_path):
    path = tmp_path / "noversion.json"
    path.write_text(json.dumps({"kind": "project", "world": {}}))
    with pytest.raises(MalformedFileError, match="schemaVersion"):
        load_project(path)


def test_non_integer_version_raises_malformed(tmp_path):
    path = tmp_path / "boolver.json"
    path.write_text(json.dumps({"schemaVersion": True, "kind": "project", "world": {}}))
    with pytest.raises(MalformedFileError, match="integer"):
        load_project(path)


def test_newer_version_raises_unsupported(tmp_path):
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps({"schemaVersion": SCHEMA_VERSION + 1, "kind": "project", "world": {}})
    )
    with pytest.raises(UnsupportedVersionError, match="newer"):
        load_project(path)


def test_older_version_raises_unsupported(tmp_path):
    path = tmp_path / "ancient.json"
    path.write_text(json.dumps({"schemaVersion": 0, "kind": "project", "world": {}}))
    with pytest.raises(UnsupportedVersionError, match="older"):
        load_project(path)


def test_wrong_kind_raises_malformed(furnace_chest, tmp_path):
    # A library file loaded as a project (and vice versa) must be rejected.
    lib_path = tmp_path / "lib.json"
    save_library(export_library(furnace_chest), lib_path)
    with pytest.raises(MalformedFileError, match="project"):
        load_project(lib_path)

    proj_path = tmp_path / "proj.json"
    save_project(furnace_chest, proj_path)
    with pytest.raises(MalformedFileError, match="library"):
        load_library(proj_path)


def test_missing_world_object_raises_malformed(tmp_path):
    path = tmp_path / "noworld.json"
    path.write_text(json.dumps({"schemaVersion": SCHEMA_VERSION, "kind": "project"}))
    with pytest.raises(MalformedFileError, match="world"):
        load_project(path)


def test_garbled_payload_raises_malformed():
    # A project whose World is structurally wrong (item missing 'id').
    bad = {"schemaVersion": SCHEMA_VERSION, "kind": "project", "world": {"items": [{}]}}
    with pytest.raises(MalformedFileError, match="malformed"):
        project_from_dict(bad)

    bad_lib = {"schemaVersion": SCHEMA_VERSION, "kind": LIBRARY_KIND, "items": [{}]}
    with pytest.raises(MalformedFileError, match="malformed"):
        library_from_dict(bad_lib)


def test_non_object_raises_malformed():
    with pytest.raises(MalformedFileError, match="object"):
        project_from_dict([1, 2, 3])


def test_v1_project_still_loads_and_resaves_as_current(tmp_path):
    # v2 is additive/optional over v1, so a v1 envelope is a valid payload and
    # loads without a migration step; saving it back stamps the current version.
    v1 = {
        "schemaVersion": 1,
        "kind": "project",
        "world": {
            "name": "legacy",
            "items": [{"id": "iron", "name": "Iron", "category": "solid"}],
            "recipes": [
                {
                    "id": "r",
                    "timeSeconds": 1.0,
                    "inputs": [{"itemId": "iron", "quantity": 1}],
                    "outputs": [{"itemId": "iron", "quantity": 1}],
                }
            ],
        },
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(v1))
    world = load_project(path)
    assert world.name == "legacy"
    # The v1 recipe gains the new fields as absent, not as defaulted values.
    assert world.recipes[0].eu_per_tick is None
    assert world.recipes[0].machine_type is None

    save_project(world, path)
    assert json.loads(path.read_text())["schemaVersion"] == SCHEMA_VERSION
