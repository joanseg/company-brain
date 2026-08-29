"""Local read-only dashboard.

Runs on a daemon thread inside the MCP process, so it lives and dies with the
session — no pid file, no subprocess to supervise. Loopback only, and it
refuses requests whose Host header it does not recognise, because a page you
visit could otherwise reach it by DNS rebinding.
"""

from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

DEFAULT_PORT = 4717
LADDER = 20
HOSTNAME = "127.0.0.1"


def configured_port() -> int:
    raw = os.getenv("COMPANY_BRAIN_VIEW_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PORT
    if not (1 <= value <= 65535):
        return DEFAULT_PORT
    return value


def host_allowed(header: str | None, port: int) -> bool:
    """Only this server's own loopback origins may be addressed."""
    if not header:
        return False
    return header in ("127.0.0.1:%d" % port, "localhost:%d" % port)


def bind(preferred: int) -> ThreadingHTTPServer:
    """Preferred port, then the next LADDER, then an ephemeral one.

    Binds with a placeholder handler; the caller swaps in the real one via
    `server.RequestHandlerClass` once it knows the port. Closing and
    re-binding to publish the port would race another process onto it.

    An out-of-range `preferred` (e.g. a bad env value that slipped past
    `configured_port`) raises `OverflowError` from the socket layer rather
    than `OSError`, so it is caught alongside `OSError` and treated as just
    another failed candidate — the ladder still falls through to an
    ephemeral port instead of the caller crashing.
    """
    from http.server import BaseHTTPRequestHandler

    handler = BaseHTTPRequestHandler
    candidates = [preferred] + [preferred + n for n in range(1, LADDER + 1)] + [0]
    if preferred == 0:
        candidates = [0]
    last: OSError | None = None
    for candidate in candidates:
        try:
            return ThreadingHTTPServer((HOSTNAME, candidate), handler)
        except (OSError, OverflowError) as exc:
            last = exc if isinstance(exc, OSError) else OSError(str(exc))
    raise last if last else OSError("could not bind")


import json
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ASSETS = Path(__file__).resolve().parent / "assets"
# Explicit allow-list: never join a request path onto a directory.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/explainer.html": ("explainer.html", "text/html; charset=utf-8"),
}

_url: str | None = None
_server: ThreadingHTTPServer | None = None


def url() -> str | None:
    return _url


def _int_query(query: dict, key: str, default: int) -> int:
    """Coerce a query param to int; fall back to `default` when missing or junk."""
    raw = query.get(key, [None])[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _payload(path: str, query: dict) -> dict | None:
    from . import db, views

    builders = {
        "/api/overview": lambda conn: views.overview(conn),
        "/api/communities": lambda conn: views.communities(conn),
        "/api/corpus": lambda conn: views.corpus(conn),
        "/api/suggestions": lambda conn: views.suggestions(conn),
        "/api/graph": lambda conn: views.graph(
            conn, _int_query(query, "limit", 400)),
    }
    build = builders.get(path)
    if build is None:
        return None
    conn = db.connect()
    try:
        return build(conn)
    finally:
        conn.close()


def _handler(port: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CompanyBrain"

        def log_message(self, *args):  # keep the MCP stdio stream clean
            pass

        def handle_error(self, request, client_address):
            # The default implementation prints a full traceback to stderr,
            # bypassing log_message; do_GET already turns builder exceptions
            # into a JSON 500, so this is only a backstop. Stay silent and
            # keep serving other requests.
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # Deliberately no Access-Control-Allow-Origin: same-origin policy
            # is what stops a foreign page reading these responses.
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not host_allowed(self.headers.get("Host"), port):
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            parsed = urlparse(self.path)
            if parsed.path in STATIC:
                name, content_type = STATIC[parsed.path]
                candidate = ASSETS / name
                if not candidate.is_file():
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                self._send(200, candidate.read_bytes(), content_type)
                return
            try:
                data = _payload(parsed.path, parse_qs(parsed.query))
            except Exception as exc:
                # Never leak the exception message or traceback to a caller —
                # only the class name, so a malformed request can't fish for
                # internals. The thread stays alive either way.
                body = json.dumps({"error": "internal",
                                    "detail": type(exc).__name__}).encode()
                self._send(500, body, "application/json; charset=utf-8")
                return
            if data is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, json.dumps(data, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

    return Handler


def start(port: int | None = None) -> str | None:
    """Bind and serve on a daemon thread. Returns the base URL, or None."""
    global _url, _server
    preferred = configured_port() if port is None else port
    try:
        server = bind(preferred)
    except OSError:
        _url = None
        return None
    # The handler needs the port, which only exists after binding. Swapping the
    # class in beats closing and re-binding, which would race for the port.
    server.RequestHandlerClass = _handler(server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True,
                     name="company-brain-webui").start()
    _server = server
    _url = "http://%s:%d" % (HOSTNAME, server.server_address[1])
    return _url


def stop() -> None:
    """Shut down the server started by `start`, if any. Idempotent.

    Exists mainly for tests: `start()` leaves a daemon thread and a
    listening socket alive for the rest of the process otherwise.
    """
    global _url, _server
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None
    _url = None
