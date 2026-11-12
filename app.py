"""app.py — desktop entry point for Process Simulation.

Opens the planner in a native window and wires the Python backend to it over the
pywebview bridge — no browser, no local server, no build step. The whole UI is
``ui/index.html`` (plain HTML/CSS/JS); the backend is :class:`backend.Api`.

Run with::

    pip install pywebview        # one dependency
    python app.py                # open the app (needs a system web engine)
    python app.py project.psim.json   # open with a project loaded

The working World is auto-saved to ``~/.process_sim/autosave.psim.json`` after
every change (``PROCESS_SIM_HOME`` overrides the directory), so the app resumes
where you left off. An explicit project file on the command line wins over the
autosave.

Everything except the actual window creation is import-safe and unit-tested
without a display: ``webview`` is imported lazily inside :func:`main`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend import build_api, default_autosave_path, default_library_dir

__all__ = ["WINDOW_TITLE", "ui_index", "main"]

WINDOW_TITLE = "Process Simulation"
#: Matches the page's deep-slate base (``--bg``) so there is no white flash
#: while the webview paints.
WINDOW_BACKGROUND = "#0b0f17"
WINDOW_SIZE = (1360, 860)
WINDOW_MIN_SIZE = (900, 600)

BASE = Path(__file__).resolve().parent


def ui_index() -> Path:
    """Return ``ui/index.html``, or raise a clear error if it is missing."""

    index = BASE / "ui" / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"UI not found at {index}.")
    return index


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""

    args = _parse_args(argv)
    index = ui_index()
    autosave = default_autosave_path()
    autosave.parent.mkdir(parents=True, exist_ok=True)
    api = build_api(args.project, autosave, default_library_dir())

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
    # The window has closed. Autosave is timer/explicit (not per-edit), and the
    # in-page beforeunload hook is unreliable when a native window is torn down, so
    # flush any unsaved working state here as the authoritative close-time save.
    api.save_autosave()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
