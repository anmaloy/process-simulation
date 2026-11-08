"""export_standalone.py — bake the current World into a single portable HTML file.

The desktop app (``app.py``) and the server (``server.py``) load the World live
from the Python backend, so you do NOT need this to run the app. Use it only to
produce a single ``.html`` file that opens in a plain browser with no Python —
handy for sharing a layout.

The export is **view-only**: the rate-balance engine is Python, so a backend-less
page renders the graph but does not recompute balances. Open the app for live
computation.

Usage::

    python export_standalone.py                 # -> dist/process_sim.html
    python export_standalone.py --out X.html
    python export_standalone.py --project a.psim.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from process_sim.io import load_project

BASE = Path(__file__).resolve().parent
TOKEN = "<!--WORLD_DATA_INJECTION_POINT-->"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--project",
        type=Path,
        default=None,
        help="project file to bake in (default: the autosave, else empty)",
    )
    ap.add_argument("--ui", type=Path, default=BASE / "ui" / "index.html")
    ap.add_argument("--out", type=Path, default=BASE / "dist" / "process_sim.html")
    args = ap.parse_args()

    if args.project is not None:
        world = load_project(args.project).to_dict()
    else:
        from backend import default_autosave_path

        autosave = default_autosave_path()
        world = load_project(autosave).to_dict() if autosave.is_file() else {}

    ui = args.ui.read_text(encoding="utf-8")
    if ui.count(TOKEN) != 1:
        raise SystemExit(
            f"UI must contain the data token exactly once (found {ui.count(TOKEN)})."
        )
    html = ui.replace(TOKEN, json.dumps(world, ensure_ascii=False))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(
        f"Wrote {args.out}  ({len(html):,} bytes, "
        f"{len(world.get('nodes', []))} nodes, {len(world.get('pipes', []))} pipes)"
    )
    print("This file is view-only (no backend) — open it in any browser.")


if __name__ == "__main__":
    main()
