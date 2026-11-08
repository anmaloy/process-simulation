"""server.py — HTTP entry point for Process Simulation.

Serves the same UI (``ui/index.html``) and the same :class:`backend.Api` as the
desktop app, but over HTTP instead of the pywebview bridge — so the planner can
run on one always-on machine and be reached from any device's browser.

Run with::

    python server.py            # listens on 0.0.0.0:8000
    python server.py 9000       # custom port
    HOST=127.0.0.1 python server.py

No extra dependencies beyond the standard library (pywebview is only needed for
the desktop app). The UI picks whichever transport is present at runtime.

Single-user assumption: ``Api`` state is in memory over the working World and is
auto-saved to disk, so run exactly one server process. A lock serialises requests
within this process to avoid torn reads/writes.

**Do not expose this to the public internet** — it has no authentication. Reach it
over a LAN or a private mesh VPN (e.g. Tailscale).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend import build_api, default_autosave_path

BASE = Path(__file__).resolve().parent
UI = BASE / "ui"

# One shared backend instance (auto-saving to disk); one lock to serialise
# mutations/reads against it.
_autosave = default_autosave_path()
_autosave.parent.mkdir(parents=True, exist_ok=True)
API = build_api(autosave_path=_autosave)
API_LOCK = threading.Lock()
# Only genuine public Api methods are callable over HTTP — no dunders, no
# internals, nothing added later by accident. Built once at startup.
API_METHODS = {
    name
    for name in dir(API)
    if not name.startswith("_") and callable(getattr(API, name))
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProcessSim/1.0"

    # ---- helpers ----
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_static(self, path):
        """Map a URL path to a file inside ui/, or None (missing / escapes ui/)."""

        rel = path.lstrip("/")
        if rel in ("", "index.html"):
            rel = "index.html"
        target = (UI / rel).resolve()
        try:
            target.relative_to(UI.resolve())
        except ValueError:
            return None  # refuse anything climbing out of ui/
        return target if target.is_file() else None

    # ---- routes ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        target = self._resolve_static(path)
        if target is None:
            self._send_json({"error": "not found"}, status=404)
            return
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send_bytes(target.read_bytes(), ctype)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._send_json({"error": "not found"}, status=404)
            return
        method = path[len("/api/") :]
        if method not in API_METHODS:
            self._send_json({"error": "unknown method: " + method}, status=404)
            return

        # Body carries the call arguments. Preferred shape: {"args": [...]} (zero
        # or more positional args). {"arg": x} is also accepted as a single arg.
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        args: list = []
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json({"error": "invalid JSON body"}, status=400)
                return
            if isinstance(payload, dict):
                if "args" in payload and isinstance(payload["args"], list):
                    args = payload["args"]
                elif "arg" in payload:
                    args = [payload["arg"]]

        fn = getattr(API, method)
        try:
            with API_LOCK:
                result = fn(*args)
        except Exception as exc:  # surface backend errors, don't 500 opaquely
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json({"result": result})

    # quieter logging: one line per request is enough for a home server
    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    default_port = int(os.environ.get("PORT", "8000"))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else default_port
    httpd = ThreadingHTTPServer((host, port), Handler)
    shown = host if host != "0.0.0.0" else "0.0.0.0 (all interfaces)"
    print(f"Process Simulation server on http://{shown}:{port}")
    print(f"Reach it from this machine at http://localhost:{port}")
    print("From other devices, use this machine's LAN / Tailscale address.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
