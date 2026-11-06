"""Phase 10 — the pywebview desktop shell.

Everything here is the non-GUI surface of ``app.py``: locating the built
bundle, loading a project into the API, and the offline-asset guarantee. The
window itself (``webview.create_window`` / ``start``) needs a display and is not
exercised; ``main`` imports ``webview`` lazily so these tests run headless.

Acceptance bar (WORKFLOW P10): the app loads the *built* frontend from disk and
all assets load offline — the latter is machine-checked here and via
``python app.py --check``.
"""

from pathlib import Path

import pytest

import app
from process_sim.io import save_project
from process_sim.model import World

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILT_DIST = REPO_ROOT / "frontend" / "dist"


# --------------------------------------------------------------------------- #
# dist resolution
# --------------------------------------------------------------------------- #


def test_dist_index_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="npm run build"):
        app.dist_index(tmp_path)


def test_dist_index_found(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert app.dist_index(tmp_path) == tmp_path / "index.html"


def test_dist_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_SIM_DIST", str(tmp_path))
    assert app.dist_dir() == tmp_path


# --------------------------------------------------------------------------- #
# API construction
# --------------------------------------------------------------------------- #


def test_build_api_empty() -> None:
    api = app.build_api()
    world = api.get_world()
    assert world["ok"] is True
    assert world["data"].get("nodes", []) == []


def test_build_api_loads_project(tmp_path: Path, furnace_chest: World) -> None:
    project = tmp_path / "chain.json"
    save_project(furnace_chest, project)
    api = app.build_api(project)
    data = api.get_world()["data"]
    assert {n["id"] for n in data["nodes"]} == {"furnace1", "chest1"}


# --------------------------------------------------------------------------- #
# Offline-asset check
# --------------------------------------------------------------------------- #


def test_find_external_refs_clean(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="./assets/app.css">'
        '<script src="./assets/app.js"></script>',
        encoding="utf-8",
    )
    (tmp_path / "app.css").write_text(
        "@font-face{src:url(./fonts/x.woff2)} .a{background:url('./img/y.png')}",
        encoding="utf-8",
    )
    assert app.find_external_refs(tmp_path) == []


def test_find_external_refs_flags_cdn_import(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<div></div>", encoding="utf-8")
    (tmp_path / "app.css").write_text(
        "@import url('https://fonts.googleapis.com/css?family=X');",
        encoding="utf-8",
    )
    refs = app.find_external_refs(tmp_path)
    assert len(refs) == 1 and refs[0][0] == "app.css"


def test_find_external_refs_flags_html_and_protocol_relative(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<script src="https://cdn.example.com/x.js"></script>'
        '<link href="//cdn.example.com/x.css">',
        encoding="utf-8",
    )
    refs = app.find_external_refs(tmp_path)
    assert len(refs) == 2
    assert all(where == "index.html" for where, _ in refs)


def test_main_check_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (tmp_path / "index.html").write_text(
        '<script src="./assets/app.js"></script>', encoding="utf-8"
    )
    monkeypatch.setenv("PROCESS_SIM_DIST", str(tmp_path))
    assert app.main(["--check"]) == 0
    assert "loads no external assets" in capsys.readouterr().out


def test_main_check_dirty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (tmp_path / "index.html").write_text("<div></div>", encoding="utf-8")
    (tmp_path / "x.css").write_text("@import url(https://cdn.example.com/a.css);", encoding="utf-8")
    monkeypatch.setenv("PROCESS_SIM_DIST", str(tmp_path))
    assert app.main(["--check"]) == 1
    assert "not offline" in capsys.readouterr().out


def test_main_check_missing_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCESS_SIM_DIST", str(tmp_path / "nope"))
    assert app.main(["--check"]) == 2


# --------------------------------------------------------------------------- #
# The real built bundle, when present, must be offline (the P10 guarantee).
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (BUILT_DIST / "index.html").is_file(),
    reason="frontend not built (run `npm run build` in frontend/)",
)
def test_built_bundle_is_offline() -> None:
    assert app.find_external_refs(BUILT_DIST) == []
