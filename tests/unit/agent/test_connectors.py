"""OpenAICompatibleParser, CascadeParser and make_parser with a fake HTTP layer: no network, no Ollama."""
import json
import urllib.error

import pytest

from bg.agent import CascadeParser, LocalLLMParser, OpenAICompatibleParser, RulesParser, make_parser
from bg.agent.rules_parser import NOT_UNDERSTOOD_QUESTION
from bg.app import _parser_from_env, build_default
from bg.contracts.models import Clarify, Pay, ParsedRequest
from bg.contracts.params import DEMO_USER_ID

NAMES = ["Mom", "mom", "Landlord"]
KEY = "sk-test-SECRET-0123456789"


def _chat(intents):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"intents": intents})}}]}


PAY = [{"kind": "pay", "beneficiary_ref": "mom", "amount_mode": "exact", "amount": 50, "currency": "EUR"}]


def _parser(http, **kw):
    kw.setdefault("base_url", "https://api.deepseek.com/v1")
    kw.setdefault("model", "deepseek-chat")
    return OpenAICompatibleParser(http=http, **kw)


# ------------------------------------------------------------------ the connector
def test_request_shape_label_and_success():
    seen = {}

    def http(url, payload, timeout, headers):
        seen.update(url=url, payload=payload, timeout=timeout, headers=headers)
        return _chat(PAY)
    res = _parser(http, api_key=KEY, timeout=12).parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert res.parser == "connected:api.deepseek.com" and isinstance(res.intents[0], Pay)
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions" and seen["timeout"] == 12
    p = seen["payload"]
    assert p["model"] == "deepseek-chat" and p["temperature"] == 0 and p["stream"] is False
    assert p["response_format"]["type"] == "json_schema" and "intents" in p["response_format"]["json_schema"]["schema"]["properties"]
    assert p["messages"][0]["role"] == "system" and "untrusted" in p["messages"][0]["content"].lower()
    assert seen["headers"] == {"Authorization": f"Bearer {KEY}"}


def test_same_prompt_and_payee_names_only_as_the_local_parser():
    from bg.agent.local_parser import SYSTEM_PROMPT
    seen = {}

    def http(url, payload, timeout, headers):
        seen["m"] = payload["messages"]
        return _chat(PAY)
    _parser(http).parse("send 50 euros to mom", beneficiary_names=NAMES, pending_question="How much?")
    assert seen["m"][0]["content"] == SYSTEM_PROMPT
    assert "How much?" in seen["m"][1]["content"] and "FR76" not in json.dumps(seen["m"])


def test_falls_back_to_json_object_when_json_schema_is_refused_and_remembers_it():
    modes = []

    def http(url, payload, timeout, headers):
        modes.append(payload["response_format"]["type"])
        if payload["response_format"]["type"] == "json_schema":
            raise urllib.error.HTTPError(url, 400, "unsupported", {}, None)
        assert "JSON schema" in payload["messages"][0]["content"]       # the schema moves into the prompt
        return _chat(PAY)
    p = _parser(http)
    assert p.parse("send 50 euros to mom", beneficiary_names=NAMES).parser == "connected:api.deepseek.com"
    p.parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert modes == ["json_schema", "json_object", "json_object"]


def test_markdown_fenced_json_is_accepted():
    fenced = {"choices": [{"message": {"content": "```json\n" + json.dumps({"intents": PAY}) + "\n```"}}]}
    res = _parser(lambda *a: fenced).parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert res.parser.startswith("connected:") and isinstance(res.intents[0], Pay)


@pytest.mark.parametrize("exc,word", [
    (urllib.error.URLError("refused"), "unreachable"), (TimeoutError(), "timed out"),
    (urllib.error.HTTPError("u", 500, "boom", {}, None), "HTTP error 500"), (json.JSONDecodeError("x", "y", 0), "invalid JSON"),
])
def test_transport_failures_fall_back_to_rules(exc, word):
    def http(*a):
        raise exc
    res = _parser(http).parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert res.parser == "rules" and any(word in n for n in res.notes) and isinstance(res.intents[0], Pay)


@pytest.mark.parametrize("data", [
    {}, {"choices": []}, {"choices": [{"message": {}}]}, {"choices": [{"message": {"content": "not json"}}]},
    {"choices": [{"message": {"content": None}}]}, _chat([]), _chat([{"kind": "execute_transfer"}]),
    _chat([{"kind": "pay", "beneficiary_ref": "mom", "amount": 50, "evil": 1}]),
])
def test_bad_answers_fall_back_to_rules(data):
    res = _parser(lambda *a: data).parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert res.parser == "rules" and res.notes


def test_endpoint_rules():
    OpenAICompatibleParser("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus")
    p = OpenAICompatibleParser("http://localhost:11434/v1/", "qwen2.5:7b-instruct")      # loopback may be http
    assert p.base_url == "http://localhost:11434/v1" and p.parser_tag == "connected:localhost"
    for bad in ("http://api.example.com/v1", "ftp://x/v1", "not a url", "https://user:pw@api.example.com/v1",
                "https://api.example.com/v1?key=abc"):
        with pytest.raises(ValueError):
            OpenAICompatibleParser(bad, "m")


def test_label_names_the_endpoint_host_and_fits_the_contract():
    res = _parser(lambda *a: _chat(PAY), base_url="https://open.bigmodel.cn/api/paas/v4").parse(
        "send 50 euros to mom", beneficiary_names=NAMES)
    assert res.parser == "connected:open.bigmodel.cn"
    ParsedRequest.model_validate(res.model_dump())           # the label is valid for the contract


def test_api_key_from_environment_and_never_leaks(monkeypatch, tmp_path):
    monkeypatch.setenv("BG_MODEL_API_KEY", KEY)
    stats = tmp_path / "calls.jsonl"
    monkeypatch.setenv("BG_LLM_STATS", str(stats))
    seen = {}
    audit = []

    def http(url, payload, timeout, headers):
        seen["h"] = headers
        if "fail" in payload["messages"][1]["content"]:
            raise urllib.error.HTTPError(url, 401, "bad key", {}, None)
        return _chat(PAY)
    parser = _parser(http)
    assert seen == {} and parser._api_key == KEY
    orch = build_default(parser=parser, audit_sink=audit.append)
    sid = orch.new_session(DEMO_USER_ID)
    t1 = orch.handle_message(sid, "send 50 euros to mom")
    t2 = orch.handle_message(sid, "fail please, send 40 euros to mom")           # HTTP 401 -> rules fallback
    assert seen["h"] == {"Authorization": f"Bearer {KEY}"}
    blob = json.dumps([audit, t1.model_dump(mode="json"), t2.model_dump(mode="json"), repr(parser), str(vars(parser).get("label"))],
                      default=str) + (stats.read_text(encoding="utf-8") if stats.exists() else "")
    assert KEY not in blob and "SECRET" not in blob
    assert t2.parsed.parser == "rules" and any("401" in n for n in t2.parsed.notes)


def test_explicit_key_wins_over_environment(monkeypatch):
    monkeypatch.setenv("BG_MODEL_API_KEY", "from-env")
    seen = {}

    def http(url, payload, timeout, headers):
        seen["h"] = headers
        return _chat(PAY)
    _parser(http, api_key="explicit").parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert seen["h"]["Authorization"] == "Bearer explicit"
    monkeypatch.delenv("BG_MODEL_API_KEY")
    _parser(http).parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert seen["h"] == {}                                                   # no key, no header


# ------------------------------------------------------------------ the cascade
class _Tier:
    def __init__(self, label, intents=None, parser="local", boom=False):
        self.tier_label, self.calls = label, 0
        self._intents, self._parser, self._boom = intents, parser, boom

    def parse(self, text, *, beneficiary_names, pending_question=None):
        self.calls += 1
        if self._boom:
            raise RuntimeError("tier down")
        return ParsedRequest(intents=self._intents, parser=self._parser, raw_text=text)


def _pay(ref="mom", amount=50):
    from decimal import Decimal
    return Pay(beneficiary_ref=ref, amount=Decimal(amount))


def test_cascade_stops_at_rules_when_rules_understand():
    t2 = _Tier("local/x", [_pay()])
    res = CascadeParser([RulesParser(), t2]).parse("send 50 euros to mom", beneficiary_names=NAMES)
    assert res.parser == "cascade:rules" and t2.calls == 0 and isinstance(res.intents[0], Pay)


def test_cascade_moves_on_when_rules_only_say_they_did_not_understand():
    t2 = _Tier("local/qwen2.5:3b-instruct", [_pay()])
    res = CascadeParser([RulesParser(), t2]).parse("envoie 50 euros a maman", beneficiary_names=NAMES)
    assert t2.calls == 1 and res.parser == "cascade:local/qwen2.5:3b-instruct"
    assert RulesParser().parse("envoie 50 euros a maman", beneficiary_names=NAMES).intents[0].question == NOT_UNDERSTOOD_QUESTION


def test_cascade_skips_a_tier_that_fell_back_and_one_that_crashed():
    down = _Tier("local/a", [Clarify(question="x")], parser="rules")        # a model tier returning the rules fallback
    boom = _Tier("local/b", boom=True)
    good = _Tier("local/c", [_pay()])
    res = CascadeParser([RulesParser(), down, boom, good]).parse("envoie 50 euros a maman", beneficiary_names=NAMES)
    assert (down.calls, boom.calls, good.calls) == (1, 1, 1) and res.parser == "cascade:local/c"


def test_cascade_skips_a_tier_whose_intents_grounding_rejects_entirely():
    invented = _Tier("local/a", [_pay("mom", 9999)])                          # 9999 is not in the message
    good = _Tier("local/b", [_pay("mom", 50)])
    res = CascadeParser([RulesParser(), invented, good]).parse("envoie 50 euros a mom", beneficiary_names=NAMES)
    assert res.parser == "cascade:local/b" and res.intents[0].amount == 50


def test_cascade_keeps_a_partly_valid_answer():
    partly = _Tier("local/a", [_pay("mom", 50), _pay("mom", 9999)])
    good = _Tier("local/b", [_pay()])
    res = CascadeParser([RulesParser(), partly, good]).parse("envoie 50 euros a mom", beneficiary_names=NAMES)
    assert res.parser == "cascade:local/a" and good.calls == 0


def test_cascade_when_nobody_convinces_returns_the_last_model_answer_or_rules():
    only_generic = _Tier("local/a", [Clarify(question=NOT_UNDERSTOOD_QUESTION)])
    res = CascadeParser([RulesParser(), only_generic]).parse("qwerty", beneficiary_names=NAMES)
    assert res.parser == "cascade:local/a" and isinstance(res.intents[0], Clarify)
    res = CascadeParser([RulesParser(), _Tier("local/a", parser="rules", intents=[Clarify(question="x")])]).parse(
        "qwerty", beneficiary_names=NAMES)
    assert res.parser == "cascade:rules"
    with pytest.raises(ValueError):
        CascadeParser([])


def test_cascade_pending_question_skips_the_grounding_check():
    # "50" answers a pending question: the payee comes from the draft, not from this text
    t2 = _Tier("local/a", [_pay("mom", 50)])
    res = CascadeParser([_Tier("local/pre", [_pay("mom", 50)]), t2]).parse(
        "50", beneficiary_names=NAMES, pending_question="How much should I send to Mom?")
    assert res.parser == "cascade:local/pre"


def test_cascade_records_the_answering_tier(monkeypatch, tmp_path):
    stats = tmp_path / "s.jsonl"
    monkeypatch.setenv("BG_LLM_STATS", str(stats))
    CascadeParser([RulesParser()]).parse("send 50 euros to mom", beneficiary_names=NAMES)
    rec = json.loads(stats.read_text(encoding="utf-8").splitlines()[0])
    assert rec["cascade_tier"] == "rules" and rec["tiers_tried"] == 1 and "latency_s" in rec


def test_cascade_through_the_orchestrator_labels_the_turn():
    orch = build_default(parser=CascadeParser([RulesParser()]))
    sid = orch.new_session(DEMO_USER_ID)
    assert orch.handle_message(sid, "send 50 euros to mom").parsed.parser == "cascade:rules"


# ------------------------------------------------------------------ factory and environment
def test_make_parser_kinds(monkeypatch):
    monkeypatch.delenv("BG_MODEL_API_KEY", raising=False)
    assert make_parser("rules") is None and make_parser("whatever") is None and make_parser("") is None
    local = make_parser("local", model="qwen2.5:3b-instruct")
    assert isinstance(local, LocalLLMParser) and local.model == "qwen2.5:3b-instruct"
    assert make_parser("local").model == "qwen2.5:7b-instruct"
    c = make_parser("cascade")
    assert isinstance(c, CascadeParser) and isinstance(c.tiers[0], RulesParser)
    assert [t.model for t in c.tiers[1:]] == ["qwen2.5:3b-instruct", "qwen2.5:7b-instruct"]
    conn = make_parser("connected", model="m", base_url="https://api.deepseek.com/v1", api_key="k")
    assert isinstance(conn, OpenAICompatibleParser) and conn.parser_tag == "connected:api.deepseek.com"
    assert make_parser("connected", model="m") is None                      # not configured: rules
    assert make_parser("connected", model="m", base_url="http://api.example.com/v1") is None   # not https: refused


def test_parser_from_env(monkeypatch):
    for k in ("BG_PARSER", "BG_LOCAL_MODEL", "BG_MODEL_BASE_URL", "BG_MODEL_NAME", "BG_MODEL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert _parser_from_env() is None
    monkeypatch.setenv("BG_PARSER", "cascade")
    assert isinstance(_parser_from_env(), CascadeParser)
    monkeypatch.setenv("BG_PARSER", "connected")
    assert _parser_from_env() is None                                       # no endpoint configured
    monkeypatch.setenv("BG_MODEL_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("BG_MODEL_NAME", "qwen2.5:7b-instruct")
    p = _parser_from_env()
    assert isinstance(p, OpenAICompatibleParser) and p.model == "qwen2.5:7b-instruct"
    monkeypatch.setenv("BG_PARSER", "claude")                                # removed: unknown value -> rules
    assert _parser_from_env() is None


def test_claude_is_gone():
    import bg.agent
    assert not hasattr(bg.agent, "ClaudeParser")
    with pytest.raises(ImportError):
        import bg.agent.claude_parser  # noqa: F401
