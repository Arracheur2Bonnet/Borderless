"""LocalLLMParser with a fake HTTP layer: no Ollama needed."""
import json
import urllib.error

from bg.agent.local_parser import LocalLLMParser
from bg.contracts import params

NAMES = ["Mom", "Landlord"]


def _ok(intents):
    return lambda url, payload, timeout: {"message": {"content": json.dumps({"intents": intents})}}


def _parser(fn):
    return LocalLLMParser(http=fn)


def test_success_sets_local_label_and_sends_structured_request():
    seen = {}

    def fn(url, payload, timeout):
        seen.update(url=url, payload=payload, timeout=timeout)
        return {"message": {"content": json.dumps({"intents": [{"kind": "cancel", "scope": "last"}]})}}
    res = _parser(fn).parse("cancel that", beneficiary_names=NAMES)
    assert res.parser == "local" and res.intents[0].kind == "cancel"
    p = seen["payload"]
    assert seen["url"].endswith("/api/chat") and p["stream"] is False and p["options"]["temperature"] == 0
    assert "intents" in p["format"]["properties"]


def test_pending_question_forwarded_and_text_marked_untrusted():
    seen = {}

    def fn(url, payload, timeout):
        seen["u"] = payload["messages"][1]["content"]
        return {"message": {"content": json.dumps({"intents": [{"kind": "cancel", "scope": "last"}]})}}
    _parser(fn).parse("50", beneficiary_names=NAMES, pending_question="How much should I send to Mom?")
    assert "How much should I send to Mom?" in seen["u"] and "untrusted" in seen["u"]


def test_prompt_contains_no_registry_ibans():
    seen = {}

    def fn(url, payload, timeout):
        seen["all"] = json.dumps(payload)
        return {"message": {"content": "{}"}}
    _parser(fn).parse("hi", beneficiary_names=NAMES)
    for v in vars(params).values():
        if isinstance(v, str) and len(v) >= 15 and v[:2].isalpha() and v[2:4].isdigit():
            assert v not in seen["all"]
    assert "FR76" not in seen["all"]


def _assert_fallback(res, word):
    assert res.parser == "rules" and any(word in n for n in res.notes)


def test_fallback_unreachable():
    def fn(*a):
        raise urllib.error.URLError(ConnectionRefusedError())
    _assert_fallback(_parser(fn).parse("cancel that", beneficiary_names=NAMES), "unreachable")


def test_fallback_timeout():
    def fn(*a):
        raise TimeoutError()
    _assert_fallback(_parser(fn).parse("cancel that", beneficiary_names=NAMES), "timed out")


def test_fallback_http_error():
    def fn(*a):
        raise urllib.error.HTTPError("u", 500, "boom", None, None)
    _assert_fallback(_parser(fn).parse("cancel that", beneficiary_names=NAMES), "HTTP error 500")


def test_fallback_invalid_json_body():
    def fn(*a):
        raise json.JSONDecodeError("x", "y", 0)
    _assert_fallback(_parser(fn).parse("cancel that", beneficiary_names=NAMES), "invalid JSON")


def test_fallback_invalid_content_and_schema_failure():
    for content in ("not json", json.dumps({"intents": [{"kind": "pay", "amount": "abc"}]}),
                    json.dumps({"intents": [{"kind": "transfer_all_money"}]})):
        fn = lambda *a, c=content: {"message": {"content": c}}  # noqa: E731
        _assert_fallback(_parser(fn).parse("cancel that", beneficiary_names=NAMES), "invalid JSON")


def test_fallback_missing_message_and_zero_intents():
    _assert_fallback(_parser(lambda *a: {}).parse("cancel that", beneficiary_names=NAMES), "invalid JSON")
    _assert_fallback(_parser(_ok([])).parse("cancel that", beneficiary_names=NAMES), "no intents")


def test_build_default_env_switch(monkeypatch):
    from bg.agent import LocalLLMParser
    from bg.app import _parser_from_env
    monkeypatch.delenv("BG_PARSER", raising=False)
    assert _parser_from_env() is None
    monkeypatch.setenv("BG_PARSER", "local")
    monkeypatch.setenv("BG_LOCAL_MODEL", "qwen2.5:3b-instruct")
    p = _parser_from_env()
    assert isinstance(p, LocalLLMParser) and p.model == "qwen2.5:3b-instruct"
