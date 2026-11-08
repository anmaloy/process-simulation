"""Phase 4 — API surface tests.

Acceptance bar (WORKFLOW.md P4): every method is exercised by a Python-side test
calling ``api.py`` directly (no webview) with JSON in / JSON out; the method list
and shapes are the frozen contract.

The api is marshaling only, so each test proves two things: the envelope/shape is
right, and the payload matches what the engine/io layer produces when called
directly (fidelity — the api adds no logic and loses nothing).
"""

import json

import pytest

from process_sim.api import Api
from process_sim.engine import (
    compute_scope,
    compute_system,
    compute_world,
    trace_process,
)
from process_sim.io import (
    MalformedFileError,
    export_library,
    import_library,
    library_to_dict,
    load_project,
)
from process_sim.model import Category, Item, MachineNode, World

FIXTURE_NAMES = ["furnace_chest", "powered_process"]


def _roundtrip_json(obj):
    """Assert a payload survives a JSON encode/decode — i.e. it is wire-ready."""

    return json.loads(json.dumps(obj))


# --------------------------------------------------------------------------- #
# World state — get / set / edit.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_get_world_returns_world_dict(name, request):
    world = request.getfixturevalue(name)
    api = Api(world)
    res = api.get_world()
    assert res["ok"] is True
    assert res["data"] == world.to_dict()
    assert _roundtrip_json(res) == res


def test_get_world_defaults_to_empty():
    res = Api().get_world()
    assert res["ok"] is True
    assert res["data"] == World().to_dict()


def test_set_world_replaces_and_normalizes(furnace_chest):
    api = Api()
    payload = furnace_chest.to_dict()
    res = api.set_world(payload)
    assert res["ok"] is True
    assert res["data"] == payload
    # The stored world is the new one, observable through a fresh read.
    assert api.get_world()["data"] == payload


def test_set_world_malformed_returns_error_envelope():
    api = Api()
    # An item missing its required 'category' — a model from_dict KeyError.
    res = api.set_world({"items": [{"id": "x", "name": "X"}]})
    assert res["ok"] is False
    assert res["error"]["type"] == "KeyError"
    assert "category" in res["error"]["message"]


def test_apply_edit_upsert_adds_then_replaces(furnace_chest):
    api = Api(furnace_chest)
    # Add a brand-new item.
    res = api.apply_edit(
        {
            "op": "upsert",
            "kind": "item",
            "entity": {"id": "coal", "name": "Coal", "category": "solid", "eu": 1000},
        }
    )
    assert res["ok"] is True
    items = {i["id"]: i for i in res["data"]["items"]}
    assert items["coal"]["eu"] == 1000

    # Upsert the same id again → replace in place, no duplicate.
    res = api.apply_edit(
        {
            "op": "upsert",
            "kind": "item",
            "entity": {"id": "coal", "name": "Coal Lump", "category": "solid"},
        }
    )
    coal = [i for i in res["data"]["items"] if i["id"] == "coal"]
    assert len(coal) == 1
    assert coal[0]["name"] == "Coal Lump"
    # Replacement dropped the unset 'eu' — absence survives, not a stale value.
    assert "eu" not in coal[0]


def test_apply_edit_pipe_node_kind_round_trips():
    api = Api()
    res = api.apply_edit(
        {
            "op": "upsert",
            "kind": "pipeNode",
            "entity": {"id": "j1", "category": "energy", "x": 10, "y": 20},
        }
    )
    assert res["ok"] is True
    assert res["data"]["pipeNodes"] == [
        {"id": "j1", "category": "energy", "x": 10, "y": 20}
    ]
    gone = api.apply_edit({"op": "remove", "kind": "pipeNode", "id": "j1"})
    assert gone["ok"] is True
    assert gone["data"].get("pipeNodes", []) == []


def test_apply_edit_remove_drops_entity(furnace_chest):
    api = Api(furnace_chest)
    res = api.apply_edit({"op": "remove", "kind": "node", "id": "chest1"})
    assert res["ok"] is True
    assert [n["id"] for n in res["data"]["nodes"]] == ["furnace1"]


def test_apply_edit_unknown_kind_and_op_error(furnace_chest):
    api = Api(furnace_chest)
    bad_kind = api.apply_edit({"op": "upsert", "kind": "widget", "entity": {}})
    assert bad_kind["ok"] is False
    assert "widget" in bad_kind["error"]["message"]

    bad_op = api.apply_edit({"op": "frobnicate", "kind": "item", "id": "ore"})
    assert bad_op["ok"] is False
    assert "frobnicate" in bad_op["error"]["message"]
    # A rejected edit leaves the world untouched.
    assert api.get_world()["data"] == furnace_chest.to_dict()


# --------------------------------------------------------------------------- #
# Project files.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_save_then_load_project_round_trip(name, tmp_path, request):
    world = request.getfixturevalue(name)
    path = tmp_path / f"{name}.psim.json"

    saver = Api(world)
    saved = saver.save_project(str(path))
    assert saved["ok"] is True
    assert saved["data"] == {"path": str(path)}
    # On-disk file matches the io layer exactly.
    assert load_project(path) == world

    loader = Api()
    loaded = loader.load_project(str(path))
    assert loaded["ok"] is True
    assert loaded["data"] == world.to_dict()
    assert loader.get_world()["data"] == world.to_dict()


def test_load_project_missing_file_errors(tmp_path):
    res = Api().load_project(str(tmp_path / "nope.json"))
    assert res["ok"] is False
    assert res["error"]["type"] == "FileNotFoundError"


def test_load_project_malformed_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    res = Api().load_project(str(bad))
    assert res["ok"] is False
    assert res["error"]["type"] == MalformedFileError.__name__


# --------------------------------------------------------------------------- #
# Recompute — every scope, matching the engine directly.
# --------------------------------------------------------------------------- #


def test_recompute_world_matches_engine(powered_process):
    api = Api(powered_process)
    res = api.recompute({"scope": "world"})
    assert res["ok"] is True
    assert res["data"] == compute_world(powered_process).to_dict()
    assert _roundtrip_json(res) == res


def test_recompute_defaults_to_world(powered_process):
    api = Api(powered_process)
    assert api.recompute()["data"] == compute_world(powered_process).to_dict()


def test_recompute_node_scope(furnace_chest):
    api = Api(furnace_chest)
    res = api.recompute({"scope": "node", "nodeId": "furnace1"})
    assert res["ok"] is True
    assert res["data"] == compute_scope(furnace_chest, ["furnace1"]).to_dict()
    # Single-machine scope carries exactly that node.
    assert res["data"]["nodeIds"] == ["furnace1"]


def test_recompute_process_scope(powered_process):
    api = Api(powered_process)
    res = api.recompute({"scope": "process", "nodeId": "smelter1"})
    assert res["ok"] is True
    ids = trace_process(powered_process, "smelter1")
    assert res["data"] == compute_scope(powered_process, ids).to_dict()


def test_recompute_system_scope():
    # A world with a system grouping two nodes.
    world = World(
        items=[Item("w", "Widget", Category.SOLID)],
        machine_defs=[],
        nodes=[MachineNode("a", "d"), MachineNode("b", "d")],
    )
    from process_sim.model import System

    world.systems = [System(id="sys", member_node_ids=["a", "b"])]
    api = Api(world)
    res = api.recompute({"scope": "system", "systemId": "sys"})
    assert res["ok"] is True
    assert res["data"] == compute_system(world, "sys").to_dict()
    assert set(res["data"]["nodeIds"]) == {"a", "b"}


def test_recompute_unknown_scope_errors(furnace_chest):
    res = Api(furnace_chest).recompute({"scope": "galaxy"})
    assert res["ok"] is False
    assert "galaxy" in res["error"]["message"]


# --------------------------------------------------------------------------- #
# Library exchange.
# --------------------------------------------------------------------------- #


def test_export_library_full_matches_io(powered_process):
    api = Api(powered_process)
    res = api.export_library()
    assert res["ok"] is True
    expected = library_to_dict(export_library(powered_process))
    assert res["data"] == expected
    # Definitional subset only — no nodes/pipes.
    assert "nodes" not in res["data"]


def test_export_library_filtered(powered_process):
    api = Api(powered_process)
    res = api.export_library({"name": "Just Smelting", "recipeIds": ["smelt"]})
    assert res["ok"] is True
    assert res["data"]["name"] == "Just Smelting"
    assert [r["id"] for r in res["data"]["recipes"]] == ["smelt"]


def test_save_and_load_library_file(powered_process, tmp_path):
    path = tmp_path / "lib.json"
    saver = Api(powered_process)
    saved = saver.save_library(str(path), {"name": "Lib"})
    assert saved["ok"] is True

    loaded = Api().load_library(str(path))
    assert loaded["ok"] is True
    assert loaded["data"]["name"] == "Lib"
    assert loaded["data"]["kind"] == "library"


def test_import_library_merges_with_rename(furnace_chest, powered_process):
    # Export the powered-process library, import into the furnace world. They
    # share ids ('ore', 'ingot', 'smelt', ...) so rename must kick in.
    lib = library_to_dict(export_library(powered_process))
    api = Api(furnace_chest)
    before = len(furnace_chest.items)

    res = api.import_library(lib, "rename")
    assert res["ok"] is True
    assert res["data"]["idRemap"]  # collisions were renamed
    assert res["data"]["skippedIds"] == []
    merged = res["data"]["world"]
    assert len(merged["items"]) > before
    # The current world is now the merged one.
    assert api.get_world()["data"] == merged
    # Matches the io layer's own merge.
    expected = import_library(
        furnace_chest, export_library(powered_process)
    ).world.to_dict()
    assert merged == expected


def test_import_library_error_policy_reports_collision(furnace_chest, powered_process):
    lib = library_to_dict(export_library(powered_process))
    res = Api(furnace_chest).import_library(lib, "error")
    assert res["ok"] is False
    assert res["error"]["type"] == "ProcessSimIOError"


def test_import_library_skip_policy(furnace_chest, powered_process):
    lib = library_to_dict(export_library(powered_process))
    res = Api(furnace_chest).import_library(lib, "skip")
    assert res["ok"] is True
    assert set(res["data"]["skippedIds"]) >= {"ore", "ingot", "smelt"}


def test_import_library_file(furnace_chest, powered_process, tmp_path):
    path = tmp_path / "lib.json"
    Api(powered_process).save_library(str(path))

    api = Api(furnace_chest)
    res = api.import_library_file(str(path), "rename")
    assert res["ok"] is True
    assert api.get_world()["data"] == res["data"]["world"]
