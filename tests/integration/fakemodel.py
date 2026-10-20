"""A tiny OpenAI-compatible server on a real loopback socket, for tests that need real HTTP (status codes, timeouts,
slow bodies, redirects, truncated replies) and no model. The standard library only.

`FakeModel(behaviour)` serves POST /v1/chat/completions. `behaviour(request) -> Reply | None` decides each answer;
every request is recorded in `.requests` (path, headers, JSON body)."""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


@dataclass
class Reply:
    status: int = 200
    body: bytes | str = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0            # wait before answering
    drip: float = 0.0             # seconds between single bytes of the body
    truncate: bool = False        # promise more bytes than are sent, then close
    close: bool = False           # close the connection without any reply


def chat(content: str | dict) -> Reply:
    """A normal OpenAI-style answer whose message content is `content` (a dict is serialised as JSON)."""
    text = content if isinstance(content, str) else json.dumps(content)
    return Reply(body=json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}))


def intents(*items: dict) -> Reply:
    return chat({"intents": list(items)})


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:      # noqa: D401
        pass

    def do_POST(self) -> None:              # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        srv: FakeModel = self.server.owner      # type: ignore[attr-defined]
        srv.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        reply = srv.behaviour(srv.requests[-1]) or Reply(status=500)
        if reply.close:
            self.connection.close()
            return
        if reply.delay:
            time.sleep(reply.delay)
        data = reply.body.encode("utf-8") if isinstance(reply.body, str) else reply.body
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/json")
        for k, v in reply.headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data) + (1000 if reply.truncate else 0)))
        self.end_headers()
        try:
            if reply.drip:
                for i in range(len(data)):
                    self.wfile.write(data[i:i + 1]); self.wfile.flush()
                    time.sleep(reply.drip)
            else:
                self.wfile.write(data)
            if reply.truncate:
                self.connection.close()
        except OSError:
            pass


class FakeModel:
    def __init__(self, behaviour: Callable[[dict], Reply | None]) -> None:
        self.behaviour = behaviour
        self.requests: list[dict] = []
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._srv.owner = self                  # type: ignore[attr-defined]
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "FakeModel":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def dead_port() -> int:
    """A loopback port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
