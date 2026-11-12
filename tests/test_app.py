"""The desktop shell (app.py), the shared backend factory (backend.py), and the
server's method surface (server.py).

The UI is now a single hand-written ``ui/index.html`` (no build step), so the
app just locates and loads it; the window itself (``webview.create_window`` /
``start``) needs a display and is not exercised (``webview`` is imported lazily
inside ``main`` so these tests run headless). The offline guarantee is now a
property of the one static file plus its local ``fonts/`` — checked here by
scanning for any ``http(s)://`` or protocol-relative asset load.
"""

import re
from pathlib import Path

import app
import backend
import server
from process_sim.io import save_project
from process_sim.model import World

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_INDEX = REPO_ROOT / "ui" / "index.html"

# External-load patterns (an absolute http(s):// or protocol-relative // target
# in an HTML src/href or a CSS url()/@import).
_EXT_HTML = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.IGNORECASE)
_EXT_CSS = re.compile(
    r"""(?:@import\s+(?:url\(\s*)?|url\(\s*)["']?\s*(?:https?:)?//""", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# UI resolution
# --------------------------------------------------------------------------- #


def test_ui_index_resolves() -> None:
    assert app.ui_index() == UI_INDEX
    assert UI_INDEX.is_file()


# --------------------------------------------------------------------------- #
# API construction (shared factory)
# --------------------------------------------------------------------------- #


def test_build_api_empty() -> None:
    world = app.build_api().get_world()
    assert world["ok"] is True
    assert world["data"].get("nodes", []) == []


def test_build_api_loads_project(tmp_path: Path, furnace_chest: World) -> None:
    project = tmp_path / "chain.json"
    save_project(furnace_chest, project)
    data = app.build_api(project).get_world()["data"]
    assert {n["id"] for n in data["nodes"]} == {"furnace1", "chest1"}


def test_backend_reexports_factory() -> None:
    # app and backend share one factory/Api — the desktop and server bind the same.
    assert app.build_api is backend.build_api
    assert backend.Api is server.API.__class__


# --------------------------------------------------------------------------- #
# Persistence (autosave): edits survive a relaunch.
# --------------------------------------------------------------------------- #


def test_edits_autosave_and_restore(tmp_path: Path) -> None:
    autosave = tmp_path / "autosave.psim.json"
    api = app.build_api(None, autosave)
    api.apply_edit(
        {
            "op": "upsert",
            "kind": "item",
            "entity": {"id": "ore", "name": "Ore", "category": "solid"},
        }
    )
    # Autosave is no longer per-edit (it would thrash the disk on a big world):
    # the edit marks the World dirty and the flush is explicit (timer / Save).
    assert not autosave.is_file()
    flushed = api.save_autosave()
    assert flushed["data"]["saved"] is True
    assert autosave.is_file()
    restored = app.build_api(None, autosave)
    assert [i["id"] for i in restored.get_world()["data"]["items"]] == ["ore"]


def test_save_autosave_is_noop_when_clean(tmp_path: Path) -> None:
    autosave = tmp_path / "autosave.psim.json"
    api = app.build_api(None, autosave)
    # Nothing changed yet → nothing to write.
    assert api.save_autosave()["data"]["saved"] is False
    assert not autosave.is_file()


def test_corrupt_autosave_is_ignored(tmp_path: Path) -> None:
    autosave = tmp_path / "autosave.psim.json"
    autosave.write_text("{ not json", encoding="utf-8")
    api = app.build_api(None, autosave)  # must not raise
    assert api.get_world()["data"].get("nodes", []) == []


def test_explicit_project_wins_over_autosave(
    tmp_path: Path, furnace_chest: World
) -> None:
    autosave = tmp_path / "autosave.psim.json"
    save_project(World(items=[], nodes=[]), autosave)
    project = tmp_path / "chain.json"
    save_project(furnace_chest, project)
    api = app.build_api(project, autosave)
    assert {n["id"] for n in api.get_world()["data"]["nodes"]} == {"furnace1", "chest1"}


# --------------------------------------------------------------------------- #
# Offline guarantee — the single UI file (and its CSS) loads nothing external.
# --------------------------------------------------------------------------- #


def test_ui_is_offline() -> None:
    text = UI_INDEX.read_text(encoding="utf-8")
    assert not _EXT_HTML.search(text), "UI references an external HTML asset"
    assert not _EXT_CSS.search(text), "UI references an external CSS asset"


def test_ui_fonts_present() -> None:
    fonts = REPO_ROOT / "ui" / "fonts"
    assert fonts.is_dir()
    assert any(fonts.glob("*.woff2")), "bundled fonts missing"


def test_ui_has_export_token_once() -> None:
    # export_standalone bakes the World at this token; it must appear exactly once.
    text = UI_INDEX.read_text(encoding="utf-8")
    assert text.count("<!--WORLD_DATA_INJECTION_POINT-->") == 1


# --------------------------------------------------------------------------- #
# Server method surface — only genuine public Api methods are callable.
# --------------------------------------------------------------------------- #


def test_server_exposes_api_methods() -> None:
    expected = {
        "get_world",
        "set_world",
        "apply_edit",
        "recompute",
        "load_project",
        "save_project",
        "import_mi_dir",
        "export_library",
        "import_library",
    }
    assert expected <= server.API_METHODS
    assert not any(m.startswith("_") for m in server.API_METHODS)
