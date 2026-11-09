"""backend.py — the single Python API surface for the Process Simulation app.

Mirrors edunode's architecture: one ``Api`` class holds the working ``World``
and marshals JSON ↔ model, delegating all rate math to :mod:`process_sim.engine`
and all file formats to :mod:`process_sim.io`. The same instance is bound two
ways, with no build step and no second language:

- the **desktop app** (:mod:`app`) binds it over the pywebview bridge, so the UI
  calls ``window.pywebview.api.<method>(...)``;
- the **server** (:mod:`server`) exposes it over ``POST /api/<method>`` for any
  browser on the network.

The UI (``ui/index.html``) is plain HTML/CSS/JS — edit it and refresh; there is
no npm, no bundler, nothing to reinstall for a change.

``Api`` itself lives in :mod:`process_sim.api` (kept there so the test-suite and
the engine package import it without pulling in a UI). This module just re-exports
it and adds :func:`build_api`, the shared constructor that restores and persists
the working World between launches.
"""

from __future__ import annotations

from pathlib import Path

from process_sim.api import Api
from process_sim.io import ProcessSimIOError, load_project

__all__ = ["Api", "build_api", "default_autosave_path", "default_library_dir"]


def _home_dir() -> Path:
    """The app's data directory: ``PROCESS_SIM_HOME`` or a repo-local folder."""

    import os

    base = os.environ.get("PROCESS_SIM_HOME")
    return Path(base) if base else Path(__file__).resolve().parent / ".process_sim"


def default_autosave_path() -> Path:
    """Where the app auto-persists the working World between launches.

    ``PROCESS_SIM_HOME`` overrides the base directory; otherwise it lives in a
    repo-local ``.process_sim/`` directory (next to this file, gitignored), so a
    checkout's saves travel with the checkout rather than hiding under the user's
    home (``.process_sim/autosave.psim.json``).
    """

    return _home_dir() / "autosave.psim.json"


def default_library_dir() -> Path:
    """Where imported catalogs (e.g. the MI library) are saved, so they are
    auto-loaded into every project without re-importing (``.process_sim/libraries``)."""

    return _home_dir() / "libraries"


def build_api(
    project: Path | None = None,
    autosave_path: Path | None = None,
    library_dir: Path | None = None,
) -> Api:
    """Build the :class:`Api`, restoring and persisting the working World.

    Load order: an explicit ``project`` file wins; otherwise the autosave file is
    restored if it exists and is readable (a corrupt autosave is ignored so the
    app still launches). When ``autosave_path`` is set the :class:`Api` writes the
    World back to it after every change, so the next launch resumes where it left
    off. ``library_dir`` holds persisted catalogs (an imported MI library) the
    :class:`Api` merges into the World at startup, so the machine catalog is
    always available even in a fresh or catalog-less project.
    """

    world = None
    if project is not None:
        world = load_project(project)
    elif autosave_path is not None and autosave_path.is_file():
        try:
            world = load_project(autosave_path)
        except (ProcessSimIOError, OSError):
            world = None  # ignore a corrupt/unreadable autosave
    return Api(world, autosave_path=autosave_path, library_dir=library_dir)
