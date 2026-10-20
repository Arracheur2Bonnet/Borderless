"""Localhost HTTP interface to the harness (v7, P1-05). Standard library only (http.server), no new dependency.

    POST /session   {"user": "u_lina"}                                  -> {"session": "...", "user": "..."}
    POST /message   {"session": "...", "text": "..."}                    -> turn record
    POST /confirm   {"session": "...", "action": "last", "otp": "...", "params_hash": "..."} -> turn record
                    (params_hash is the value shown with the pending action: the approval is bound to it)
    POST /reject    {"session": "...", "action": "last"}                 -> turn record
    GET  /overview?session=...                                           -> balances, payees, bills, receipts
    GET  /health                                                         -> {"status": "ok", ...}

Safety: bound to 127.0.0.1 by default (any other host needs allow_remote=True), the Host header must name the
loopback address while bound to it (DNS rebinding), POST bodies must be application/json and at most MAX_BODY_BYTES,
every field is type-checked, an optional bearer token (BG_API_TOKEN) or per-customer tokens (BG_API_TOKENS, written
"token=customer,token2=customer2": a token can only open and use the sessions of its own customer) are compared in
constant time, session ids are random and unguessable, an approval must carry the hash shown with the action, responses never
carry an OTP, a full IBAN, a stack trace or a secret, and the server writes no access log. The orchestrator is the
same one the UI uses; the HTTP layer cannot move money by itself.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bg.harness import KNOWN_USERS, Harness, HarnessError

MAX_BODY_BYTES = 64 * 1024
MAX_SESSIONS_PER_USER = 50              # one customer (or token) cannot use up the whole budget of the others
MAX_SESSIONS = 200                       # per server process: a client cannot grow the in-memory state without bound
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_HOSTS_OK = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


_HOST_RE = re.compile(r"^(\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:[0-9]{1,5})?$")


def _host_of(header: str) -> str:
    """The host part of a Host header, without the port ('[::1]:8765' -> '[::1]'). A malformed header (userinfo,
    spaces, a path) gives '' and is refused."""
    h = header.strip().lower()
    m = _HOST_RE.match(h)
    return m.group(1) if m else ""


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
        self._drain()
        self._send(status, {"ok": False, "error": message})

    def _drain(self) -> None:
        """Read a request body that will not be used (refused method, type or host), bounded, so the client is not
        reset mid-upload (Windows answers an unread close with RST)."""
        if getattr(self, "_body_done", False):
            return
        self._body_done = True
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < n <= 4 * MAX_BODY_BYTES:
            self.rfile.read(n)

    def _gate(self) -> bool:
        """Host and token checks common to every request. True when the request may continue; sets self._user to the
        customer the token is bound to (None: any customer)."""
        self._user: str | None = None
        if len(self.headers.get_all("Host") or []) > 1:                         # one Host header, never two
            self._error(400, "bad host header")
            return False
        host = _host_of(self.headers.get("Host") or "")
        if self.server.loopback_only and host.lower() not in _HOSTS_OK:       # type: ignore[attr-defined]
            self._error(403, "host not allowed")
            return False
        token = self.server.token                                              # type: ignore[attr-defined]
        user_tokens = self.server.user_tokens                                  # type: ignore[attr-defined]
        if token or user_tokens:
            given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip().encode("utf-8")
            ok, who = False, None
            if token and hmac.compare_digest(given, token.encode("utf-8")):
                ok = True
            for t, u in user_tokens.items():                  # no early exit: the time does not tell which token matched
                if hmac.compare_digest(given, t.encode("utf-8")) and not ok:
                    ok, who = True, u
            if not ok:
                self._error(401, "missing or wrong bearer token")
                return False
            self._user = who
        return True

    def _session(self, public: Any) -> tuple[str, str]:
        """(internal session id, customer) for a public id, only if this caller may use it."""
        entry = self.server.sessions.get(public) if isinstance(public, str) else None   # type: ignore[attr-defined]
        if entry is None or (self._user is not None and entry[1] != self._user):
            raise HarnessError("unknown session", 404)       # a token never learns that another customer's id exists
        return entry

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
            self._body_done = True
            self._error(413, f"request body is larger than {MAX_BODY_BYTES} bytes")
            return None
        raw = self.rfile.read(n)
        self._body_done = True
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
                public = (parse_qs(url.query).get("session") or [None])[0]
                sid, _ = self._session(public)
                out = self.harness.overview(sid)
                out["session"] = public
                self._send(200, out)
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
            public = data.get("session")
            if path == "/session":
                only = os.environ.get("BG_API_USER")       # optional: this API serves one customer only
                user = data.get("user") or self._user or only or None
                for pinned in (self._user, only):
                    if pinned and user != pinned:
                        raise HarnessError("this API key serves one customer only", 403)
                with self.server.sessions_lock:                                    # type: ignore[attr-defined]
                    if len(self.server.sessions) >= MAX_SESSIONS:                  # type: ignore[attr-defined]
                        raise HarnessError("too many open sessions on this server", 429)
                    if sum(1 for _, u in self.server.sessions.values() if u == user) >= MAX_SESSIONS_PER_USER:   # type: ignore[attr-defined]
                        raise HarnessError("too many open sessions for this customer", 429)
                    out = h.open_session(user)
                    public = secrets.token_urlsafe(18)                             # unguessable, unlike the internal id
                    self.server.sessions[public] = (out["session"], out["user"])   # type: ignore[attr-defined]
                self._send(201, {"ok": True, "session": public, "user": out["user"]})
                return
            sid, _ = self._session(public)
            if path == "/message":
                out = h.message(sid, data.get("text", ""))
            elif path == "/confirm":
                if data.get("params_hash") is None:
                    raise HarnessError("'params_hash' is required: send the hash shown with the action you approve", 400)
                out = h.confirm(sid, data.get("action", "last"), data.get("otp"), data.get("params_hash"))
            else:
                out = h.reject(sid, data.get("action", "last"))
            out["session"] = public
            self._send(200, out)
        except HarnessError as exc:
            self._error(exc.status, str(exc))
        except Exception:
            self._error(500, "internal error")

    def _method_not_allowed(self) -> None:
        self._error(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = _method_not_allowed        # noqa: N815


class HarnessServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets another process bind the same port and receive requests (tokens, codes):
    # use an exclusive bind there instead (APPSEC-8).
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address: tuple[str, int], harness: Harness, *, loopback_only: bool, token: str | None,
                 user_tokens: dict[str, str] | None = None) -> None:
        super().__init__(address, _Handler)
        self.harness, self.loopback_only, self.token = harness, loopback_only, token
        self.user_tokens: dict[str, str] = dict(user_tokens or {})
        self.sessions: dict[str, tuple[str, str]] = {}           # public id -> (internal id, customer)
        self.sessions_lock = threading.Lock()


def parse_user_tokens(spec: str | None) -> dict[str, str]:
    """"tokenA=u_lina,tokenB=u_other" -> {"tokenA": "u_lina", ...}. Every customer must be a known one."""
    out: dict[str, str] = {}
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        tok, sep, user = part.strip().partition("=")
        if not sep or len(tok) < 16 or user not in KNOWN_USERS:
            raise ValueError("BG_API_TOKENS must look like 'token=customer,...' with tokens of at least 16 characters "
                             f"and customers among {', '.join(KNOWN_USERS)}")
        out[tok] = user
    return out


def make_server(host: str = "127.0.0.1", port: int = 0, harness: Harness | None = None, *,
                allow_remote: bool = False, token: str | None = None,
                user_tokens: dict[str, str] | None = None) -> HarnessServer:
    """Build (not start) the server. port=0 picks a free port (see `server.server_address`)."""
    if host not in LOOPBACK and not allow_remote:
        raise ValueError(f"refusing to listen on {host!r}: the harness API is localhost-only unless allow_remote=True (--allow-remote on the command line)")
    token = token if token is not None else (os.environ.get("BG_API_TOKEN") or None)
    user_tokens = user_tokens if user_tokens is not None else parse_user_tokens(os.environ.get("BG_API_TOKENS"))
    if host not in LOOPBACK and not (token or user_tokens):
        raise ValueError("refusing to listen on a non-loopback address without a token: set BG_API_TOKEN "
                         "(remote mode is plain HTTP, so use it only on a trusted network)")
    return HarnessServer((host, port), harness or Harness(), loopback_only=host in LOOPBACK, token=token,
                         user_tokens=user_tokens)
