"""Application entry point — the pywebview desktop shell (WORKFLOW Phase 10).

Runs the whole app as a single Python process: it builds the thin :class:`Api`
(the P4 JSON bridge), hands it to a pywebview window, and loads the **built**
frontend bundle (``frontend/dist/index.html``) from disk — no dev server, no
network. The engine and model stay headless; this module only wires the shell.

Everything here except the actual window creation is import-safe and pure, so it
is unit-tested without a display: ``webview`` is imported lazily inside
:func:`main`, the dist path is resolved by :func:`dist_dir`, and the offline
guarantee is checked by :func:`find_external_refs` (also exposed as
``python app.py --check``).

Run it:

    cd backend && python app.py [project.json]   # open the app (needs a GUI)
    python app.py --check                         # verify the bundle is offline

Packaging per OS uses PyInstaller, bundling the built frontend beside the binary:

    npm --prefix ../frontend run build
    pyinstaller --name process-sim --windowed \\
        --add-data "../frontend/dist:frontend/dist" app.py

At runtime the bundle is located via, in order: ``PROCESS_SIM_DIST`` (an explicit
override), the PyInstaller unpack dir (``sys._MEIPASS``), then the repo layout.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from process_sim.api import Api
from process_sim.io import load_project

__all__ = [
    "WINDOW_TITLE",
    "dist_dir",
    "dist_index",
    "find_external_refs",
    "build_api",
    "main",
]

WINDOW_TITLE = "Process Simulation"
#: Matches the page's deep-slate base (``--bg``) so there is no white flash
#: while the webview paints.
WINDOW_BACKGROUND = "#0b0f17"
WINDOW_SIZE = (1280, 820)
WINDOW_MIN_SIZE = (900, 600)

# External-reference patterns for the offline check. We scan the places that
# actually trigger a network *load* — HTML ``src``/``href`` and CSS
# ``url()``/``@import`` — for an absolute ``http(s)://`` or protocol-relative
# ``//`` target. (The JS bundle legitimately contains namespace URLs like
# ``http://www.w3.org/2000/svg`` that are never fetched, so it is out of scope.)
_HTML_REF = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.IGNORECASE)
# One pattern for both CSS forms — ``@import url(...)``, ``@import "..."``, and a
# bare ``url(...)`` — so a reference is counted once (finditer is non-overlapping).
_CSS_REF = re.compile(
    r"""(?:@import\s+(?:url\(\s*)?|url\(\s*)["']?\s*(?:https?:)?//""",
    re.IGNORECASE,
)


def dist_dir() -> Path:
    """Locate the built frontend directory.

    Resolution order: the ``PROCESS_SIM_DIST`` override, the PyInstaller unpack
    directory (``sys._MEIPASS``) when frozen, then the repo layout relative to
    this file (``backend/../frontend/dist``).
    """

    override = os.environ.get("PROCESS_SIM_DIST")
    if override:
        return Path(override)
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        bundled = Path(frozen_base) / "frontend" / "dist"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def dist_index(base: Path | None = None) -> Path:
    """Return the built ``index.html``, or raise a clear error if it is missing."""

    directory = base if base is not None else dist_dir()
    index = directory / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"Built frontend not found at {index}. "
            "Run `npm run build` in frontend/ before launching the app."
        )
    return index


def find_external_refs(base: Path | None = None) -> list[tuple[str, str]]:
    """Return external asset references in the built bundle (empty = offline).

    Scans ``index.html`` and every ``*.css`` for an ``http(s)://`` or
    protocol-relative load. Each hit is ``(relative_path, matched_snippet)``.
    """

    directory = base if base is not None else dist_dir()
    hits: list[tuple[str, str]] = []

    index = directory / "index.html"
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        for m in _HTML_REF.finditer(text):
            hits.append((index.name, _snippet(text, m.start())))

    for css in sorted(directory.rglob("*.css")):
        text = css.read_text(encoding="utf-8")
        rel = css.relative_to(directory).as_posix()
        for m in _CSS_REF.finditer(text):
            hits.append((rel, _snippet(text, m.start())))
    return hits


def _snippet(text: str, at: int, span: int = 60) -> str:
    """A short one-line excerpt around ``at`` for reporting a reference."""

    chunk = text[at : at + span].splitlines()[0] if at < len(text) else ""
    return chunk.strip()


def build_api(project: Path | None = None) -> Api:
    """Build the JS-API instance, optionally preloaded with a project file."""

    world = load_project(project) if project is not None else None
    return Api(world)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="process-sim",
        description="Production chain planner — pywebview desktop shell.",
    )
    parser.add_argument(
        "project",
        nargs="?",
        type=Path,
        help="optional project file to open on launch",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the built bundle loads no external assets, then exit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""

    args = _parse_args(argv)

    # The offline check runs without a display (and without importing webview),
    # so it is usable in CI after a build.
    if args.check:
        try:
            directory = dist_dir()
            dist_index(directory)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 2
        refs = find_external_refs(directory)
        if refs:
            print(
                f"Found {len(refs)} external asset reference(s) — "
                "the bundle is not offline:"
            )
            for where, snippet in refs:
                print(f"  {where}: {snippet}")
            return 1
        print(f"OK — {directory} loads no external assets.")
        return 0

    index = dist_index()
    api = build_api(args.project)

    # Imported lazily so the module (and its tests) load without a GUI backend.
    import webview

    webview.create_window(
        WINDOW_TITLE,
        url=index.as_uri(),
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        background_color=WINDOW_BACKGROUND,
    )
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
