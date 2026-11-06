#!/usr/bin/env python3
"""Dev server: build the site, serve it, rebuild on change, reload the browser.

    python dev.py --content ~/vault/Books --vault ~/vault

Takes the same options as build.py, plus --port, --host and --open.
Watches your book notes, templates/, static/, series.yml and build.py. A failed
rebuild keeps serving the last good build and shows the error in the browser.
For a production build, use build.py.
"""
from __future__ import annotations

import fnmatch
import importlib
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import build

ROOT = Path(__file__).resolve().parent
POLL_SECONDS = 0.5
SETTLE_SECONDS = 0.3  # wait for multi-file saves (Obsidian sync, git checkout) to finish

# Injected into HTML at serve time only; dist/ never contains it.
RELOAD_SNIPPET = r"""<script>
(function () {
  var version = null, banner = null;
  function show(msg) {
    if (!msg) { if (banner) { banner.remove(); banner = null; } return; }
    if (!banner) {
      banner = document.createElement('pre');
      banner.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;margin:0;padding:12px 16px;max-height:40vh;overflow:auto;background:#9d0006;color:#fbf1c7;font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap';
      document.body.appendChild(banner);
    }
    banner.textContent = 'build failed - showing the last good build\n\n' + msg;
  }
  function poll() {
    fetch('/__dev/status', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (version === null) version = s.version;
        else if (s.version !== version) { location.reload(); return; }
        show(s.error);
      })
      .catch(function () {})
      .then(function () { setTimeout(poll, 700); });
  }
  poll();
})();
</script>"""


class State:
    """Shared between the watcher (writes) and the HTTP threads (read)."""

    def __init__(self) -> None:
        self.version = 0
        self.error: str | None = None


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S}  {message}", flush=True)


# --------------------------------------------------------------------------- #
# Watching
# --------------------------------------------------------------------------- #

def watched_files(args) -> dict[Path, tuple[int, int]]:
    """Snapshot of (mtime, size) for everything that can change the site."""
    skip = {args.out.expanduser().resolve(), args.cache.expanduser().resolve()}
    specs = [
        (args.content.expanduser().resolve(), ("*.md", "*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif")),
        (ROOT / "templates", ("*",)),
        (ROOT / "static", ("*",)),
    ]
    files: dict[Path, tuple[int, int]] = {}
    for base, patterns in specs:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d != "__pycache__" and (Path(dirpath) / d).resolve() not in skip
            ]
            for name in filenames:
                if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                    path = Path(dirpath) / name
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    files[path] = (stat.st_mtime_ns, stat.st_size)
    for extra in (args.series.expanduser().resolve(), ROOT / "build.py"):
        try:
            stat = extra.stat()
        except OSError:
            continue
        files[extra] = (stat.st_mtime_ns, stat.st_size)
    return files


def rebuild(args, state: State, reload_builder: bool = False) -> bool:
    global build
    started = time.perf_counter()
    try:
        if reload_builder:
            build = importlib.reload(build)
        result = build.build_site(args)
    except build.BuildError as exc:
        message = str(exc)
    except Exception:  # noqa: BLE001 - a template or code error must not kill the dev server
        message = "\n".join(traceback.format_exc().strip().splitlines()[-14:])
    else:
        state.error = None
        state.version += 1
        log(f"rebuilt in {time.perf_counter() - started:.1f}s ({result['books']} books)")
        return True
    state.error = message
    print(message, file=sys.stderr)
    log("build failed, still serving the last good build")
    return False


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #

def make_handler(out: Path, state: State):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out), **kw)

        def log_request(self, code="-", size="-"):
            if str(code).startswith(("4", "5")) and "favicon" not in self.path:
                log(f"{code} {self.path}")

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/__dev/status":
                return self._send(json.dumps({"version": state.version, "error": state.error}).encode(), "application/json")
            target = Path(self.translate_path(route))
            if target.is_dir():
                if not route.endswith("/"):
                    return super().do_GET()  # stdlib redirects /stats -> /stats/
                target = target / "index.html"
            if target.suffix == ".html" and target.is_file():
                page = target.read_text(encoding="utf-8")
                cut = page.rfind("</body>")
                page = page + RELOAD_SNIPPET if cut < 0 else page[:cut] + RELOAD_SNIPPET + page[cut:]
                return self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            return super().do_GET()

    return Handler


def start_server(out: Path, state: State, host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    handler = make_handler(out, state)
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer((host, candidate), handler)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, candidate
    raise SystemExit(f"ports {port}-{port + 9} are all in use; pass --port")


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))  # no packet is sent
            return sock.getsockname()[0]
    except OSError:
        return None


# --------------------------------------------------------------------------- #

def main() -> int:
    parser = build.make_parser()
    parser.description = __doc__
    parser.add_argument("--port", type=int, default=8000, help="port to serve on (next free port if busy)")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open the site from your phone on the same network")
    parser.add_argument("--open", action="store_true", help="open the site in your browser")
    args = parser.parse_args()

    state = State()
    if not rebuild(args, state):
        return 1

    out = args.out.expanduser().resolve()
    server, port = start_server(out, state, args.host, args.port)
    local = f"http://localhost:{port}/"
    log(f"serving {out}")
    log(f"  local:   {local}")
    if args.host in ("0.0.0.0", "") and (ip := lan_address()):
        log(f"  network: http://{ip}:{port}/")
    log("watching for changes, Ctrl-C to stop")
    if args.open:
        webbrowser.open(local)

    seen = watched_files(args)
    try:
        while True:
            time.sleep(POLL_SECONDS)
            if watched_files(args) == seen:
                continue
            time.sleep(SETTLE_SECONDS)
            now = watched_files(args)
            changed = sorted(p for p in now.keys() | seen.keys() if now.get(p) != seen.get(p))
            seen = now
            if not changed:
                continue
            names = ", ".join(p.name for p in changed[:3]) + (f" (+{len(changed) - 3} more)" if len(changed) > 3 else "")
            log(f"changed: {names}")
            rebuild(args, state, reload_builder=(ROOT / "build.py") in changed)
    except KeyboardInterrupt:
        print()
        log("stopped")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())