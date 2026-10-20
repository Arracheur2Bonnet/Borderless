"""The model path over real HTTP, with no model: a stdlib server on a loopback socket plays the endpoint.

The unit tests hand the parser a Python callable, so they cannot see real status codes, read timeouts, a body that
arrives one byte at a time, a redirect, a truncated reply or an oversize one. Here the real `OpenAICompatibleParser`
talks to a real socket through the real orchestrator. Every failure must end the same way: no crash, no action
created by the failure, the message read by the offline rules, and nothing sensitive in logs, telemetry or audit.

Marked `integration` (real local sockets, no external service)."""
from __future__ import annotations

import json
import logging
import time

import pytest

from bg.agent.connectors import OpenAICompatibleParser
from bg.app import build_default
from bg.contracts import params
from tests.integration.fakemodel import FakeModel, Reply, chat, dead_port, intents

pytestmark = pytest.mark.integration

U = params.DEMO_USER_ID
SECRET_KEY = "sk-test-0123456789abcdef-not-a-real-key"
PAY = {"kind": "pay", "beneficiary_ref": "mom", "amount": 50, "amount_mode": "exact", "currency": "EUR"}
TEXT = "send 50 euros to mom"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("BG_MODEL_API_KEY", "BG_MODEL_BASE_URL", "BG_MODEL_NAME", "BG_LLM_STATS", "BG_MODEL_ALLOW_PRIVATE"):
        monkeypatch.delenv(k, raising=False)


def run(server_or_url, text=TEXT, *, timeout=5.0, key=None, stats=None, monkeypatch=None):
    url = server_or_url.base_url if hasattr(server_or_url, "base_url") else server_or_url
    parser = OpenAICompatibleParser(url, "test-model", api_key=key, timeout=timeout)
    audit: list[dict] = []
    orch = build_default(parser=parser, audit_sink=audit.append)
    sid = orch.new_session(U)
    return parser, orch, sid, orch.handle_message(sid, text), audit


def pending(turn):
    return [v for v in turn.actions if v.status == "awaiting_confirmation"]


# ------------------------------------------------------------------------------------------------ the good path
def test_a_good_answer_over_http_makes_the_card_and_sends_the_right_request():
    with FakeModel(lambda r: intents(PAY)) as srv:
        parser, orch, sid, t, audit = run(srv, key=SECRET_KEY)
    (card,) = pending(t)
    assert card.action.amount == 50 and t.parsed.parser == "connected:127.0.0.1"
    (req,) = srv.requests
    assert req["path"] == "/v1/chat/completions" and req["headers"]["authorization"] == f"Bearer {SECRET_KEY}"
    body = req["body"]
    assert body["model"] == "test-model" and body["temperature"] == 0 and body["stream"] is False
    assert body["response_format"]["type"] == "json_schema"
    user_msg = body["messages"][1]["content"]                          # what is specific to this customer
    assert TEXT in user_msg and "balance" not in user_msg.lower()
    for b in orch.ledger.get_beneficiaries(U):
        assert b.iban not in user_msg and b.iban not in json.dumps(body)   # no registry IBAN anywhere in the request
    assert not any("not-a-real-key" in json.dumps(a, default=str) for a in audit)


# ------------------------------------------------------------------------------------------------ the failure matrix
BAD_JSON = Reply(body="<html>502 Bad Gateway</html>")
CASES = {
    "http_400_always": (lambda r: Reply(status=400, body='{"error":"bad request"}'), "HTTP error 400"),
    "http_401": (lambda r: Reply(status=401, body='{"error":"invalid key"}'), "HTTP error 401"),
    "http_404": (lambda r: Reply(status=404, body="no such model"), "HTTP error 404"),
    "http_422_always": (lambda r: Reply(status=422, body="{}"), "HTTP error 422"),
    "http_429": (lambda r: Reply(status=429, body="slow down", headers={"Retry-After": "30"}), "HTTP error 429"),
    "http_500": (lambda r: Reply(status=500, body="boom"), "HTTP error 500"),
    "http_503": (lambda r: Reply(status=503, body="down"), "HTTP error 503"),
    "html_body": (lambda r: BAD_JSON, "invalid JSON"),
    "empty_body": (lambda r: Reply(body=b""), "invalid JSON"),
    "binary_body": (lambda r: Reply(body=b"\xff\xfe\x00garbage"), "invalid JSON"),
    "error_object_with_200": (lambda r: Reply(body=json.dumps({"error": {"message": "overloaded"}})), "invalid JSON"),
    "content_not_json": (lambda r: chat("Sure! I will send 50 euros to mom."), "invalid JSON"),
    "content_is_a_list": (lambda r: chat("[1, 2, 3]"), "invalid JSON"),
    "unknown_kind": (lambda r: intents({"kind": "transfer_everything", "to": "mallory"}), "invalid JSON"),
    "extra_field": (lambda r: intents({**PAY, "execute_now": True}), "invalid JSON"),
    "amount_as_words": (lambda r: intents({**PAY, "amount": "fifty euros"}), "invalid JSON"),
    "absurd_amount": (lambda r: intents({**PAY, "amount": 1e30}), "invalid JSON"),
    "no_intents": (lambda r: intents(), "no intents"),
    "closed_without_reply": (lambda r: Reply(close=True), "unreachable"),
    "truncated_body": (lambda r: Reply(body=chat({"intents": [PAY]}).body, truncate=True), "unreachable"),
    "truncated_mid_json": (lambda r: Reply(body=chat({"intents": [PAY]}).body[:40]), "invalid JSON"),
    "too_many_intents": (lambda r: intents(*[PAY] * 11), "invalid JSON"),
    "a_field_far_too_long": (lambda r: intents({**PAY, "memo": "x" * 600}), "invalid JSON"),
    "oversize_body": (lambda r: Reply(body=b"x" * (300 * 1024)), "too large"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_every_failure_ends_on_the_rules_without_an_action_of_its_own(name, caplog, tmp_path, monkeypatch):
    behaviour, note = CASES[name]
    stats = tmp_path / "stats.jsonl"
    monkeypatch.setenv("BG_LLM_STATS", str(stats))
    caplog.set_level(logging.DEBUG)
    with FakeModel(behaviour) as srv:
        parser, orch, sid, t, audit = run(srv, key=SECRET_KEY)
    assert t.parsed.parser == "rules"                                  # read by the offline rules
    (card,) = pending(t)                                               # the rules read "send 50 euros to mom" as usual
    assert card.action.amount == 50 and card.action.beneficiary_id
    if note:
        assert any(note in n for n in t.parsed.notes), t.parsed.notes
    assert "the built-in rules read this message" in t.assistant_text    # and the customer is told
    # nothing sensitive anywhere
    blob = "\n".join([caplog.text, stats.read_text(encoding="utf-8") if stats.exists() else "",
                      json.dumps(audit, default=str)])
    assert "not-a-real-key" not in blob and "Bearer" not in blob
    assert TEXT not in blob.replace("text_sha256", "")


def test_a_refused_connection_falls_back_quickly(caplog):
    t0 = time.monotonic()
    parser, orch, sid, t, audit = run(f"http://127.0.0.1:{dead_port()}/v1", timeout=3)
    assert t.parsed.parser == "rules" and time.monotonic() - t0 < 2.5
    assert any("unreachable" in n for n in t.parsed.notes) and len(pending(t)) == 1


def test_a_slow_server_times_out_and_the_rules_answer(monkeypatch):
    with FakeModel(lambda r: Reply(body=intents(PAY).body, delay=3)) as srv:
        t0 = time.monotonic()
        parser, orch, sid, t, audit = run(srv, timeout=0.5)
    assert t.parsed.parser == "rules" and time.monotonic() - t0 < 2.5
    assert any("timed out" in n for n in t.parsed.notes)


def test_a_body_that_drips_one_byte_at_a_time_cannot_hold_the_call_past_its_timeout():
    """AI audit F4: the socket timeout is per read, so a trickle used to last (bytes x pause) seconds."""
    body = intents(PAY).body
    with FakeModel(lambda r: Reply(body=body, drip=0.15)) as srv:       # ~60 s in all if it were allowed to finish
        t0 = time.monotonic()
        parser, orch, sid, t, audit = run(srv, timeout=1.0)
    assert time.monotonic() - t0 < 4.0
    assert t.parsed.parser == "rules" and any("timed out" in n for n in t.parsed.notes)


def test_a_redirect_is_never_followed_and_the_key_goes_nowhere_else():
    with FakeModel(lambda r: intents(PAY)) as target:
        with FakeModel(lambda r: Reply(status=307, headers={"Location": target.base_url + "/chat/completions"})) as front:
            parser, orch, sid, t, audit = run(front, key=SECRET_KEY)
        assert target.requests == [] and len(front.requests) == 1
    assert t.parsed.parser == "rules" and any("HTTP error 307" in n for n in t.parsed.notes)


# ------------------------------------------------------------------------------------------------ format negotiation
def test_a_json_schema_refusal_is_retried_as_json_object_and_remembered():
    def behaviour(r):
        if r["body"]["response_format"]["type"] == "json_schema":
            return Reply(status=400, body='{"error":"response_format json_schema is not supported"}')
        return intents(PAY)
    with FakeModel(behaviour) as srv:
        parser, orch, sid, t, audit = run(srv)
        assert [r["body"]["response_format"]["type"] for r in srv.requests] == ["json_schema", "json_object"]
        orch.handle_message(sid, "send 20 euros to mom")
        assert srv.requests[-1]["body"]["response_format"]["type"] == "json_object" and len(srv.requests) == 3
    assert t.parsed.parser == "connected:127.0.0.1"


def test_an_error_about_something_else_does_not_downgrade_the_format():
    """AI audit F12: a 404 for an unknown model must not switch the parser to json_object for good."""
    with FakeModel(lambda r: Reply(status=404, body='{"error":"model test-model not found"}')) as srv:
        parser, orch, sid, t, audit = run(srv)
        assert len(srv.requests) == 1 and parser._mode == "json_schema"


def test_a_model_that_rejects_temperature_is_retried_without_it():
    def behaviour(r):
        if "temperature" in r["body"]:
            return Reply(status=400, body='{"error":"Unsupported value: temperature does not support 0 with this model"}')
        return intents(PAY)
    with FakeModel(behaviour) as srv:
        parser, orch, sid, t, audit = run(srv)
    assert t.parsed.parser == "connected:127.0.0.1" and len(srv.requests) == 2 and "temperature" not in srv.requests[-1]["body"]


# ------------------------------------------------------------------------------------------------ endpoint rules
def test_a_hostname_label_over_the_limit_does_not_break_the_chat():
    """AI audit F9: the parser label is capped at 100 characters; a long host name used to disable the chat."""
    long_host = "a" * 60 + "." + "b" * 60 + ".example.com"
    def down(*a):
        raise OSError("down")
    parser = OpenAICompatibleParser(f"https://{long_host}/v1", "m", timeout=1, http=down)
    assert len(parser.parser_tag) <= 100 + len("connected:")
    orch = build_default(parser=parser, audit_sink=lambda e: None)
    t = orch.handle_message(orch.new_session(U), TEXT)
    assert "couldn't process that safely" not in t.assistant_text


def test_an_error_body_sent_slowly_cannot_hold_the_call(monkeypatch):
    """Review P2-3: the body of a 400 is read to decide whether the format was refused; that read has a time limit."""
    body = b'{"error":"' + b"x" * 400 + b'"}'
    with FakeModel(lambda r: Reply(status=400, body=body, drip=0.3)) as srv:
        t0 = time.monotonic()
        parser, orch, sid, t, audit = run(srv, timeout=1.0)
    assert time.monotonic() - t0 < 6 and t.parsed.parser == "rules"
