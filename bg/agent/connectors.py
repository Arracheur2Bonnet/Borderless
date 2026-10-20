"""Model connectors and the parser cascade (v6).

* `OpenAICompatibleParser`: any OpenAI-compatible `/chat/completions` endpoint (Ollama /v1, LM Studio, vLLM,
  DeepSeek, DashScope/Qwen, Zhipu/GLM and others), with the same prompt, validation and rules fallback as the local
  parser (shared code in `model_parser`).
* `CascadeParser`: rules first, then bigger and slower tiers only when the cheaper one could not read the message.
* `make_parser`: the one place that turns a kind ("rules", "local", "connected", "cascade") into a parser.

The model is only ever a proposer of typed intents. The API key comes from the argument, or from BG_MODEL_API_KEY only for the
endpoint named in BG_MODEL_BASE_URL; it is sent only in the Authorization header, and is never logged, stored in notes or put in an audit record."""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
from typing import Any, Callable

from bg.contracts.interfaces import IntentParser
from bg.contracts.models import Beneficiary, Clarify, ParsedRequest

from .grounding import GroundingContext, ground_intents
from .local_parser import DEFAULT_LOCAL_MODEL, LocalLLMParser
from .model_parser import LOOPBACK_HOSTS, ModelEndpointRefused, ModelParser, post_json
from .rules_parser import RulesParser, is_generic_clarify

log = logging.getLogger(__name__)

HttpWithHeaders = Callable[[str, dict, float, dict], dict]
CASCADE_LOCAL_MODELS = ("qwen2.5:3b-instruct", "qwen2.5:7b-instruct")


def _check_base_url(base_url: str) -> urllib.parse.ParseResult:
    u = urllib.parse.urlparse((base_url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("base_url must be an http(s) URL such as https://api.example.com/v1")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("base_url must not contain credentials, a query or a fragment (the key is a separate setting)")
    if u.hostname not in LOOPBACK_HOSTS and u.scheme != "https":
        raise ValueError("a model endpoint outside this machine must use https")
    return u


def check_public_endpoint(url: str) -> None:
    """A remote model endpoint must resolve to public addresses only (APPSEC-4: no SSRF into the operator's network
    from a Base URL typed in the UI). Loopback stays allowed for local model servers. An operator who runs a model
    on a private network on purpose sets BG_MODEL_ALLOW_PRIVATE=1."""
    host = urllib.parse.urlparse(url).hostname or ""
    if host in LOOPBACK_HOSTS or os.environ.get("BG_MODEL_ALLOW_PRIVATE") == "1":
        return
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ModelEndpointRefused("model endpoint host does not resolve") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if not addr.is_global:
            raise ModelEndpointRefused("model endpoint resolves to a private, loopback or reserved address")


def _origin(u: urllib.parse.ParseResult) -> tuple[str, str, int | None]:
    return (u.scheme, (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80))


def _env_key_for(u: urllib.parse.ParseResult) -> str | None:
    """BG_MODEL_API_KEY is the operator's key for the operator's endpoint (BG_MODEL_BASE_URL). It is used only when
    this parser talks to that same origin, so a URL typed by a visitor in the UI can never receive it (APPSEC-1)."""
    key = os.environ.get("BG_MODEL_API_KEY") or None
    env_url = os.environ.get("BG_MODEL_BASE_URL") or ""
    if not key or not env_url:
        return None
    try:
        return key if _origin(_check_base_url(env_url)) == _origin(u) else None
    except ValueError:
        return None


class OpenAICompatibleParser(ModelParser):
    """POST {base_url}/chat/completions with temperature 0 and a JSON response format.

    `response_format` is a json_schema when the endpoint accepts it, otherwise json_object (the schema is then given in
    the system prompt); the choice is remembered after the first refusal. The parser label is
    `connected:<endpoint host>`, for example `connected:api.deepseek.com`."""

    parser_tag = "connected"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 30,
                 label: str = "connected", http: HttpWithHeaders | None = None) -> None:
        super().__init__()
        u = _check_base_url(base_url)
        self.base_url = base_url.strip().rstrip("/")
        self.host_name = u.hostname or ""
        self.model = model
        self.timeout = timeout
        self.label = label or "connected"
        self.noun = f"{self.label.capitalize()} model"
        self._api_key = api_key if api_key else _env_key_for(u)
        self._http = http or self._default_http
        self._mode = "json_schema"
        self._temperature = True
        self.parser_tag = f"connected:{self.host_name[:80]}"      # type: ignore[misc]   (the label is capped at 100 characters)

    # the key must not leak through repr(), logging or a debugger print of the parser
    def __repr__(self) -> str:
        return (f"OpenAICompatibleParser(base_url={self.base_url!r}, model={self.model!r}, "
                f"api_key={'<set>' if self._api_key else None})")

    @property
    def tier_label(self) -> str:                      # type: ignore[override]
        return self.parser_tag

    @staticmethod
    def _default_http(url: str, payload: dict, timeout: float, headers: dict) -> dict:
        check_public_endpoint(url)                     # checked at every call: DNS may change between calls
        return post_json(url, payload, timeout, headers)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _post(self, mode: str, messages: list[dict], temperature: bool = True) -> dict:
        msgs = [dict(m) for m in messages]
        if mode == "json_schema":
            rf: dict[str, Any] = {"type": "json_schema",
                                  "json_schema": {"name": "intents", "strict": False, "schema": self.schema}}
        else:
            rf = {"type": "json_object"}
            msgs[0]["content"] = (msgs[0]["content"] + "\n\nReply with ONE JSON object that follows this JSON schema:\n"
                                  + json.dumps(self.schema, separators=(",", ":")))
        payload = {"model": self.model, "stream": False, "messages": msgs, "response_format": rf}
        if temperature:
            payload["temperature"] = 0
        return self._http(self.base_url + "/chat/completions", payload, self.timeout, self._headers())

    @staticmethod
    def _refusal_text(exc: urllib.error.HTTPError) -> str:
        """The first bytes of an error body, lower-cased; empty when there is none to read."""
        deadline = time.monotonic() + 2.0                     # review P2-3: an error body sent slowly cannot hold the call
        got = b""
        try:
            while len(got) < 2048 and time.monotonic() < deadline:
                fp = exc.fp
                chunk = (fp.read1(512) if hasattr(fp, "read1") else fp.read(512)) if fp is not None else b""
                if not chunk:
                    break
                got += chunk
        except Exception:                                                          # noqa: BLE001
            pass
        return got.decode("utf-8", "replace").lower()

    def _send(self, messages: list[dict]) -> dict:
        mode, temp = self._mode, self._temperature
        try:
            return self._post(mode, messages, temp)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 404, 415, 422):
                raise
            why = self._refusal_text(exc)
            if temp and "temperature" in why and "response_format" not in why and "schema" not in why:
                self._temperature = False                      # some reasoning models accept no temperature
                return self._post(mode, messages, False)
            # json_schema not supported here. A 404/422 about something else (the model name) changes nothing.
            if mode == "json_schema" and (not why or any(w in why for w in ("response_format", "json_schema", "schema", "json"))):
                self._mode = "json_object"
                return self._post("json_object", messages, temp)
            raise

    def _content(self, data: dict) -> str:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("model content is not text")
        return content


# ---------------------------------------------------------------------------------------------- cascade
def _tier_label(tier: Any) -> str:
    return str(getattr(tier, "tier_label", None) or type(tier).__name__.lower())


def _standins(names: list[str]) -> list[Beneficiary]:
    """Payee stand-ins built from the names a parser receives, enough for grounding to find a name in the text."""
    from datetime import datetime
    now = datetime(2000, 1, 1)
    return [Beneficiary(beneficiary_id=n, user_id="-", display_name=n, iban="XX00", category="other", added_at=now,
                        allowlisted=True) for n in dict.fromkeys(names) if n]


_ACTIONABLE = ("pay", "pay_obligations", "fx_convert", "add_beneficiary", "correct", "provide_detail",
               "affordability_check")


def grounding_rejects_all(res: ParsedRequest, text: str, names: list[str]) -> bool:
    """True when the message contains something to act on and grounding would drop or change every such intent
    (an invented amount or payee): the answer is not worth taking from this tier."""
    todo = [i for i in res.intents if getattr(i, "kind", "") in _ACTIONABLE]
    if not todo:
        return False
    out, reps = ground_intents(list(res.intents), GroundingContext(text=text or "", beneficiaries=_standins(names)))
    return bool(reps) and not any(i is o for i in todo for o in out)


class CascadeParser:
    """Try the tiers in order and take the first useful answer.

    A tier is passed over when it falls back to the rules parser (a model that is down or answered badly), when it
    only says it did not understand (a generic Clarify), or when grounding would reject all of its intents. The tier
    that answered is recorded in `ParsedRequest.parser` ("cascade:rules", "cascade:local/qwen2.5:3b-instruct", ...).
    Safety does not depend on any tier: the orchestrator grounds and polices whatever comes out."""

    def __init__(self, tiers: list[IntentParser]) -> None:
        if not tiers:
            raise ValueError("a cascade needs at least one tier")
        self.tiers = list(tiers)
        self.tier_label = "cascade"

    @staticmethod
    def _stat(tier: str, latency: float, tried: int) -> None:
        path = os.environ.get("BG_LLM_STATS")
        if not path:
            return
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"cascade_tier": tier, "latency_s": round(latency, 3), "tiers_tried": tried}) + "\n")
        except OSError:
            pass

    def parse(self, text: str, *, beneficiary_names: list[str],
              pending_question: str | None = None) -> ParsedRequest:
        t0 = time.perf_counter()
        rules_res: ParsedRequest | None = None
        last: tuple[ParsedRequest, str] | None = None
        for n, tier in enumerate(self.tiers, 1):
            label = _tier_label(tier)
            try:
                res = tier.parse(text, beneficiary_names=beneficiary_names, pending_question=pending_question)
            except Exception:                       # a tier never takes the chat down
                log.warning("cascade tier %s failed", label)
                continue
            is_rules = isinstance(tier, RulesParser) or label == "rules"
            if not is_rules and res.parser == "rules":      # the model tier gave up and returned the rules answer
                rules_res = rules_res or res
                continue
            if is_rules and rules_res is None:
                rules_res = res
            if not is_rules:
                last = (res, label)
            # A smaller model that only asks a question has not read the message (the 3B turns "Bitte 40 Euro an Mama
            # senden" into "Which payee should receive the money?"): ask the next tier. The last tier's question stands.
            asks_only = (not is_rules and n < len(self.tiers) and pending_question is None
                         and all(isinstance(i, Clarify) for i in res.intents))
            useless = is_generic_clarify(res.intents) or asks_only or (
                pending_question is None and grounding_rejects_all(res, text, beneficiary_names))
            if useless:
                continue
            self._stat(label, time.perf_counter() - t0, n)
            return res.model_copy(update={"parser": f"cascade:{label}"})
        if last is not None:                        # nobody was convincing: the last model's answer (grounded later)
            res, label = last
        elif rules_res is not None:
            res, label = rules_res, "rules"
        else:
            res, label = RulesParser().parse(text, beneficiary_names=beneficiary_names,
                                             pending_question=pending_question), "rules"
        self._stat(label, time.perf_counter() - t0, len(self.tiers))
        return res.model_copy(update={"parser": f"cascade:{label}"})


# ---------------------------------------------------------------------------------------------- factory
def make_parser(kind: str, *, model: str | None = None, base_url: str | None = None,
                api_key: str | None = None) -> IntentParser | None:
    """"rules" (or anything unknown) -> None, which the orchestrator turns into its own RulesParser;
    "local" -> Ollama on localhost; "connected" -> an OpenAI-compatible endpoint; "cascade" -> rules, then local
    qwen2.5:3b-instruct, then local qwen2.5:7b-instruct. A "connected" parser that is not fully configured, or whose
    endpoint is refused (not https), is not built: the rules parser is used."""
    k = (kind or "rules").strip().lower()
    if k == "local":
        return LocalLLMParser(model=model or DEFAULT_LOCAL_MODEL)
    if k == "connected":
        if not base_url or not model:
            log.warning("BG_PARSER=connected needs a base URL and a model name; using the rules parser")
            return None
        try:
            return OpenAICompatibleParser(base_url=base_url, model=model, api_key=api_key)
        except ValueError as exc:
            log.warning("connected parser refused (%s); using the rules parser", exc)
            return None
    if k == "cascade":
        return CascadeParser([RulesParser(), *(LocalLLMParser(model=m) for m in CASCADE_LOCAL_MODELS)])
    return None
