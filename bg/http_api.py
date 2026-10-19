"""Localhost HTTP interface to the harness (v7, P1-05). Standard library only (http.server), no new dependency.

    POST /session   {"user": "u_lina"}                                  -> {"session": "...", "user": "..."}
    POST /message   {"session": "...", "text": "..."}                    -> turn record
    POST /confirm   {"session": "...", "action": "last", "otp": "..."}   -> turn record
    POST /reject    {"session": "...", "action": "last"}                 -> turn record
    GET  /overview?session=...                                           -> balances, payees, bills, receipts
    GET  /health                                                         -> {"status": "ok", ...}

Safety: bound to 127.0.0.1 by default (any other host needs allow_remote=True), the Host header must name the
loopback address while bound to it (DNS rebinding), POST bodies must be application/json and at most MAX_BODY_BYTES,
every field is type-checked, an optional bearer token (BG_API_TOKEN) is compared in constant time, responses never
carry an OTP, a full IBAN, a stack trace or a secret, and the server writes no access log. The orchestrator is the
same one the UI uses; the HTTP layer cannot move money by itself.
"""
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bg.harness import KNOWN_USERS, Harness, HarnessError

MAX_BODY_BYTES = 64 * 1024
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_HOSTS_OK = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _host_of(header: str) -> str:
    """The host part of a Host header, without the port ('[::1]:8765' -> '[::1]')."""
    h = header.strip().lower()
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h.rsplit(":", 1)[0] if ":" in h else h


class _Handler(BaseHTTPRequestHandler):
    server_version = "BorderlessGuardHarness"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = 10                                        # a client that stalls mid-request is dropped

    # ------------------------------------------------------------------ plumbing
    @property
    def harness(self) -> Harness:
        return self.server.harness                      # type: ignore[attr-defined]

    def log_message(self, *a: Any) -> None:             # no access log: bodies and queries can hold a code
        pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"ok": False, "error": message})

    def _gate(self) -> bool:
        """Host and token checks common to every request. True when the request may continue."""
        host = _host_of(self.headers.get("Host") or "")
        if self.server.loopback_only and host.lower() not in _HOSTS_OK:       # type: ignore[attr-defined]
            self._error(403, "host not allowed")
            return False
        token = self.server.token                                              # type: ignore[attr-defined]
        if token:
            given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
                self._error(401, "missing or wrong bearer token")
                return False
        return True

    def _json_body(self) -> dict[str, Any] | None:
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._error(415, "Content-Type must be application/json")
            return None
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            self._error(411, "Content-Length is required")
            return None
        try:
            n = int(raw_len)
        except ValueError:
            self._error(400, "invalid Content-Length")
            return None
        if n < 0:
            self._error(400, "invalid Content-Length")
            return None
        if n > MAX_BODY_BYTES:
            self.rfile.read(min(n, 4 * MAX_BODY_BYTES))      # drain a bounded amount so the client can read the reply
            self._error(413, f"request body is larger than {MAX_BODY_BYTES} bytes")
            return None
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._error(400, "request body is not valid JSON")
            return None
        if not isinstance(data, dict):
            self._error(400, "request body must be a JSON object")
            return None
        return data

    # ------------------------------------------------------------------ routes
    def do_GET(self) -> None:                                  # noqa: N802
        try:
            if not self._gate():
                return
            url = urlsplit(self.path)
            if url.path == "/health":
                self._send(200, {"ok": True, "status": "ok", "parser": self.harness.parser_label,
                                 "users": list(KNOWN_USERS)})
            elif url.path == "/overview":
                sid = (parse_qs(url.query).get("session") or [None])[0]
                self._send(200, self.harness.overview(sid))
            elif url.path in ("/session", "/message", "/confirm", "/reject"):
                self._error(405, "use POST")
            else:
                self._error(404, "not found")
        except HarnessError as exc:
            self._error(exc.status, str(exc))
        except Exception:                                      # fail closed, no trace to the client
            self._error(500, "internal error")

    def do_POST(self) -> None:                                 # noqa: N802
        try:
            if not self._gate():
                return
            path = urlsplit(self.path).path
            if path not in ("/session", "/message", "/confirm", "/reject"):
                self._error(405 if path in ("/health", "/overview") else 404,
                            "use GET" if path in ("/health", "/overview") else "not found")
                return
            data = self._json_body()
            if data is None:
                return
            h = self.harness
            if path == "/session":
                out = h.open_session(data.get("user"))
                self._send(201, {"ok": True, **out})
            elif path == "/message":
                self._send(200, h.message(data.get("session"), data.get("text", "")))
            elif path == "/confirm":
                self._send(200, h.confirm(data.get("session"), data.get("action", "last"), data.get("otp")))
            else:
                self._send(200, h.reject(data.get("session"), data.get("action", "last")))
        except HarnessError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            self._error(500, "internal error")

    def _method_not_allowed(self) -> None:
        self._error(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = _method_not_allowed        # noqa: N815


class HarnessServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], harness: Harness, *, loopback_only: bool, token: str | None) -> None:
        super().__init__(address, _Handler)
        self.harness, self.loopback_only, self.token = harness, loopback_only, token


def make_server(host: str = "127.0.0.1", port: int = 0, harness: Harness | None = None, *,
                allow_remote: bool = False, token: str | None = None) -> HarnessServer:
    """Build (not start) the server. port=0 picks a free port (see `server.server_address`)."""
    if host not in LOOPBACK and not allow_remote:
        raise ValueError(f"refusing to listen on {host!r}: the harness API is localhost-only unless allow_remote=True (--allow-remote on the command line)")
    token = token if token is not None else (os.environ.get("BG_API_TOKEN") or None)
    return HarnessServer((host, port), harness or Harness(), loopback_only=host in LOOPBACK, token=token)
