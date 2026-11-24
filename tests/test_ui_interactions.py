"""Everyday UI interactions behave — driven in a real browser against server.py.

The engine and API are covered headlessly; this covers what only shows up when a
person uses the page: the inspector/rail/readout keeping their scroll position
across the re-render every edit causes, focus and half-typed text surviving a
commit, two quick edits both landing, number entry (decimals, a decimal comma,
blanks for optional vs. required fields, ranges, whole numbers, no mouse-wheel
drift), Escape/Delete doing the local thing, comboboxes not keeping abandoned text,
rename/required-name validation, new entities appearing ready to name, undo, the
import panel and project dialog, and the basic canvas gestures.

The scenarios live in ``tests/ui_interactions.js`` (Playwright, run with Node);
each starts from a freshly seeded World on a reloaded page and reports the
expectations it broke. Needs Node + Playwright; skipped where they are absent so
the plain ``pytest`` suite stays dependency-free.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.test_ui_boot import _NODE

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).resolve().parent / "ui_interactions.js"

pytestmark = pytest.mark.skipif(_NODE is None, reason="needs node + playwright")


def _world() -> dict:
    """A World big enough to scroll: 120 items, 40 recipes, 14 placed machines."""

    items = [
        {"id": f"ore_{i}", "name": f"Ore {i}", "category": "solid"} for i in range(120)
    ]
    items += [
        {"id": "electricity", "name": "Electricity", "category": "energy"},
        {"id": "water", "name": "Water", "category": "liquid"},
        {"id": "coal", "name": "Coal", "category": "solid", "eu": 32000},
    ]
    recipes = [
        {
            "id": f"mac_{i}",
            "name": f"Macerate {i}",
            "timeSeconds": 5,
            "euPerTick": 2,
            "inputs": [{"itemId": f"ore_{i}", "quantity": 1}],
            "outputs": [{"itemId": f"ore_{i + 1}", "quantity": 2, "probability": 0.5}],
        }
        for i in range(60)
    ]
    slots = [
        {"category": "solid", "direction": "in", "slots": 1, "stackSize": 64},
        {"category": "solid", "direction": "out", "slots": 1, "stackSize": 64},
        {"category": "energy", "direction": "in", "slots": 1, "stackSize": 1000},
    ]
    defs = [
        {
            "id": "macerator",
            "name": "Electric Macerator",
            "energySource": "electric",
            "sourceItemId": "electricity",
            "baseEnergyConsumption": 40,
            "voltageTier": "LV",
            "maxEuPerTick": 32,
            "allowedRecipeIds": [r["id"] for r in recipes],
            "slots": slots,
        },
        {
            "id": "generator",
            "name": "Generator",
            "slots": [
                {
                    "category": "energy",
                    "direction": "out",
                    "slots": 1,
                    "stackSize": 1000,
                }
            ],
        },
    ] + [{"id": f"def_{i}", "name": f"Machine {i}"} for i in range(30)]
    nodes = [
        {
            "id": f"mac_node_{i + 1}",
            "machineDefId": "macerator",
            "recipeIds": [f"mac_{i}"],
            "x": (i % 6) * 260,
            "y": (i // 6) * 200,
        }
        for i in range(14)
    ]
    nodes.append({"id": "gen_1", "machineDefId": "generator", "x": -300, "y": 0})
    pipes = [
        {
            "id": f"pipe_{i + 1}",
            "category": "solid",
            "movementCapacity": 16,
            "a": {"nodeId": f"mac_node_{i + 1}", "role": "out", "port": "solid:out"},
            "b": {"nodeId": f"mac_node_{i + 2}", "role": "in", "port": "solid:in"},
        }
        for i in range(6)
    ]
    pipes.append(
        {
            "id": "pipe_power",
            "category": "energy",
            "movementCapacity": 5120,
            "a": {"nodeId": "gen_1", "role": "out", "port": "energy:out"},
            "b": {"nodeId": "mac_node_1", "role": "in", "port": "energy:in"},
        }
    )
    return {
        "items": items,
        "recipes": recipes,
        "machineDefs": defs,
        "nodes": nodes,
        "pipes": pipes,
        "pipeNodes": [{"id": "junction_1", "category": "solid", "x": -100, "y": -100}],
        "systems": [],
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict:
    tmp = tmp_path_factory.mktemp("ui")
    world_path = tmp / "world.json"
    world_path.write_text(json.dumps(_world()))
    env = dict(os.environ, PROCESS_SIM_HOME=str(tmp / "home"), HOST="127.0.0.1")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "server.py", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/get_world",
                    data=b'{"args":[]}',
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        node, node_env = _NODE  # type: ignore[misc]
        out = subprocess.run(
            [node, str(DRIVER), str(port), str(world_path)],
            env=node_env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert out.returncode == 0, out.stderr[-3000:]
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        proc.terminate()
        proc.wait(timeout=10)


SCENARIOS = [
    "inspector_scroll_kept_after_recipe_pick",
    "inspector_scroll_kept_after_checkbox",
    "inspector_scroll_resets_for_new_selection",
    "rail_scroll_kept_after_select",
    "readout_scroll_kept_after_select",
    "recipe_finder_stays_open_with_filters_and_scroll",
    "canvas_view_kept_on_select_and_edit",
    "tab_between_fields_keeps_both_values",
    "fast_consecutive_edits_all_land",
    "half_typed_value_survives_rerender",
    "set_button_focuses_its_new_input",
    "backspace_after_inspector_button_does_not_delete",
    "decimal_round_trip_per_tick_fields",
    "decimal_entry_forms",
    "clearing_optional_number_unsets_it",
    "required_and_out_of_range_numbers_rejected",
    "wheel_does_not_change_numbers",
    "arrow_keys_step_numbers",
    "pipe_energy_rate_in_eu_per_tick",
    "escape_in_field_reverts_and_keeps_selection",
    "escape_in_combobox_keeps_selection",
    "combobox_abandoned_text_reverts_and_enter_picks",
    "combobox_list_stays_inside_the_panel",
    "add_another_combobox_keeps_focus",
    "rename_validation",
    "item_name_required",
    "create_item_is_visible_and_named_by_typing",
    "undo_restores_and_drops_dead_selection",
    "import_panel_survives_rerender",
    "rail_search_keeps_focus_across_render",
    "project_dialog_escape_and_empty_path",
    "run_and_save_commit_the_field_being_typed",
    "canvas_drag_wire_and_undo",
    "rail_pick_reveals_offscreen_node",
    "shift_click_group",
]


def test_every_scenario_ran(results: dict) -> None:
    assert sorted(results) == sorted(SCENARIOS)


@pytest.mark.parametrize("name", SCENARIOS)
def test_ui_interaction(results: dict, name: str) -> None:
    assert name in results, f"scenario {name} did not run"
    assert results[name]["fails"] == [], "\n".join(results[name]["fails"])
