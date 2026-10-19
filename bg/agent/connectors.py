"""Model connectors and the parser cascade (v6).

* `OpenAICompatibleParser`: any OpenAI-compatible `/chat/completions` endpoint (Ollama /v1, LM Studio, vLLM,
  DeepSeek, DashScope/Qwen, Zhipu/GLM and others), with the same prompt, validation and rules fallback as the local
  parser (shared code in `model_parser`).
* `CascadeParser`: rules first, then bigger and slower tiers only when the cheaper one could not read the message.
* `make_parser`: the one place that turns a kind ("rules", "local", "connected", "cascade") into a parser.

The model is only ever a proposer of typed intents. The API key comes from the argument or BG_MODEL_API_KEY, is
sent only in the Authorization header, and is never logged, stored in notes or put in an audit record."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
from typing import Any, Callable

from bg.contracts.interfaces import IntentParser
from bg.contracts.models import Beneficiary, ParsedRequest

from .grounding import GroundingContext, ground_intents
from .local_parser import DEFAULT_LOCAL_MODEL, LocalLLMParser
from .model_parser import LOOPBACK_HOSTS, ModelParser, post_json
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
        self._api_key = api_key if api_key else (os.environ.get("BG_MODEL_API_KEY") or None)
        self._http = http or self._default_http
        self._mode = "json_schema"
        self.parser_tag = f"connected:{self.host_name}"          # type: ignore[misc]

    # the key must not leak through repr(), logging or a debugger print of the parser
    def __repr__(self) -> str:
        return (f"OpenAICompatibleParser(base_url={self.base_url!r}, model={self.model!r}, "
                f"api_key={'<set>' if self._api_key else None})")

    @property
    def tier_label(self) -> str:                      # type: ignore[override]
        return self.parser_tag

    @staticmethod
    def _default_http(url: str, payload: dict, timeout: float, headers: dict) -> dict:
        return post_json(url, payload, timeout, headers)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _post(self, mode: str, messages: list[dict]) -> dict:
        msgs = [dict(m) for m in messages]
        if mode == "json_schema":
            rf: dict[str, Any] = {"type": "json_schema",
                                  "json_schema": {"name": "intents", "strict": False, "schema": self.schema}}
        else:
            rf = {"type": "json_object"}
            msgs[0]["content"] = (msgs[0]["content"] + "\n\nReply with ONE JSON object that follows this JSON schema:\n"
                                  + json.dumps(self.schema, separators=(",", ":")))
        payload = {"model": self.model, "temperature": 0, "stream": False, "messages": msgs, "response_format": rf}
        return self._http(self.base_url + "/chat/completions", payload, self.timeout, self._headers())

    def _send(self, messages: list[dict]) -> dict:
        mode = self._mode
        try:
            return self._post(mode, messages)
        except urllib.error.HTTPError as exc:
            if mode == "json_schema" and exc.code in (400, 404, 415, 422):     # json_schema not supported here
                self._mode = "json_object"
                return self._post("json_object", messages)
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
            useless = is_generic_clarify(res.intents) or (
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
