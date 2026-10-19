"""Local LLM intent parser (Qwen served by Ollama). Structured output only: no tools, no execution power.

The model emits typed intents in the {intents: [...]} schema, validated by pydantic; any failure falls back to the
offline rules parser. Shared code lives in `model_parser`. Standard library HTTP only; the host must be loopback and
the traffic never goes through an HTTP proxy (AUDIT2-9)."""
from __future__ import annotations

import urllib.parse
from typing import Callable

from .model_parser import (  # noqa: F401  (re-exported: other modules and tests import them from here)
    LOOPBACK_HOSTS, SYSTEM_PROMPT, ModelParser, _Envelope, constrained_schema as _constrained_schema,
    numbers_only as _numbers_only, post_json,
)

DEFAULT_LOCAL_MODEL = "qwen2.5:7b-instruct"
HttpFn = Callable[[str, dict, float], dict]


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    return post_json(url, payload, timeout)


class LocalLLMParser(ModelParser):
    noun = "Local model"
    parser_tag = "local"

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, host: str = "http://localhost:11434",
                 timeout: float = 30, http: HttpFn | None = None) -> None:
        super().__init__()
        host = host.rstrip("/")
        u = urllib.parse.urlparse(host)
        if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK_HOSTS:
            # AUDIT2-9: the local parser talks to this machine only; a remote model is the connected parser's job
            raise ValueError("the local parser only talks to a loopback host (localhost, 127.0.0.1, ::1)")
        self.model = model
        self.host = host
        self.timeout = timeout
        self._http = http or _post_json

    @property
    def tier_label(self) -> str:                      # type: ignore[override]
        return f"local/{self.model}"

    def _send(self, messages: list[dict]) -> dict:
        payload = {"model": self.model, "stream": False, "format": self.schema,
                   "options": {"temperature": 0}, "messages": messages}
        return self._http(self.host + "/api/chat", payload, self.timeout)

    def _content(self, data: dict) -> str:
        return data["message"]["content"]
