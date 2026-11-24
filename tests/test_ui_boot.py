"""The desktop UI boots into the World the backend restored — not an empty one.

Regression for "the state and recipes have to be re-imported on every launch".
The backend restored the autosave + saved libraries correctly, but the page booted
before the desktop bridge existed: ``app.py`` loads ``ui/index.html`` over
``file://`` and pywebview injects ``window.pywebview.api`` *after* the ``load``
event. ``boot()`` then took the standalone-export branch with an empty placeholder,
showed a blank World, and refused to boot again on ``pywebviewready``.

This drives the real page in headless Chromium the way pywebview does: ``file://``
URL, bridge injected only after ``load`` (optionally with pywebview's empty
``api`` object present first), every call forwarded to a real ``server.py``
backend restored from a persisted home dir. Needs Node + Playwright; skipped where
they are absent, so the plain ``pytest`` suite stays dependency-free.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import backend

REPO_ROOT = Path(__file__).resolve().parent.parent

_DRIVER = r"""
const { chromium } = require("playwright");
const [index, port, delay, earlyEmpty, fireEvent] = process.argv.slice(1);  // node -e
const methods = ["get_world","set_world","apply_edit","save_autosave","recompute"];
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.exposeFunction("__bridge", async (m, a) => {
    const r = await fetch(`http://127.0.0.1:${port}/api/${m}`, {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({args: JSON.parse(a)})});
    return JSON.stringify((await r.json()).result);
  });
  if (earlyEmpty === "1") await page.addInitScript("window.pywebview={api:{}};");
  await page.goto(index);                       // resolves on `load`
  await page.waitForTimeout(Number(delay));     // the bridge arrives later
  await page.evaluate(([ms, fire]) => {
    window.pywebview = window.pywebview || {};
    window.pywebview.api = window.pywebview.api || {};
    for (const m of ms)
      window.pywebview.api[m] = async (...a) => JSON.parse(await window.__bridge(m, JSON.stringify(a)));
    if (fire) window.dispatchEvent(new Event("pywebviewready"));
  }, [methods, fireEvent === "1"]);
  await page.waitForTimeout(1500);
  console.log(JSON.stringify(await page.evaluate(() =>
    ({booted: BOOTED, items: (WORLD && WORLD.items || []).map(i => i.id)}))));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
"""


def _node_with_playwright() -> tuple[str, dict[str, str]] | None:
    node = shutil.which("node")
    if node is None:
        return None
    env = dict(os.environ)
    npm = shutil.which("npm")
    if npm and "NODE_PATH" not in env:
        root = subprocess.run(
            [npm, "root", "-g"], capture_output=True, text=True, check=False
        ).stdout.strip()
        if root:
            env["NODE_PATH"] = root
    ok = subprocess.run(
        [node, "-e", "require('playwright')"], env=env, capture_output=True, check=False
    )
    return (node, env) if ok.returncode == 0 else None


_NODE = _node_with_playwright()
pytestmark = pytest.mark.skipif(_NODE is None, reason="needs node + playwright")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def restored_server(tmp_path: Path):
    """A server.py backend whose home dir holds a previous session's autosave."""

    home = tmp_path / "home"
    env = dict(os.environ, PROCESS_SIM_HOME=str(home), HOST="127.0.0.1")
    os.environ["PROCESS_SIM_HOME"] = str(home)
    try:
        api = backend.build_api(None, backend.default_autosave_path())
    finally:
        del os.environ["PROCESS_SIM_HOME"]
    item = {"id": "remembered_item", "name": "Remembered", "category": "solid"}
    api.apply_edit({"op": "upsert", "kind": "item", "entity": item})
    assert api.save_autosave()["data"]["saved"]

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
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.parametrize(
    ("delay_ms", "early_empty_api", "fire_ready"),
    [
        (300, False, True),  # bridge injected after load, then pywebviewready
        (300, True, True),  # pywebview's empty api object exists first
        (300, False, False),  # pywebviewready missed: the poll must still boot
    ],
)
def test_desktop_boot_loads_restored_world(
    restored_server: int, delay_ms: int, early_empty_api: bool, fire_ready: bool
) -> None:
    node, env = _NODE  # type: ignore[misc]
    out = subprocess.run(
        [
            node,
            "-e",
            _DRIVER,
            (REPO_ROOT / "ui" / "index.html").as_uri(),
            str(restored_server),
            str(delay_ms),
            "1" if early_empty_api else "0",
            "1" if fire_ready else "0",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    state = json.loads(out.stdout.strip().splitlines()[-1])
    assert state["booted"] is True
    assert "remembered_item" in state["items"]
