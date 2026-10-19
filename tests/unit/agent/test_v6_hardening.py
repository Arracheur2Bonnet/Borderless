"""v6 hardening (round-2 audit): what a model may and may not make the bank say or do. Real system, scripted parser."""
import json
import time
import urllib.request
from decimal import Decimal

import pytest

from bg.agent import LocalLLMParser, RulesParser
from bg.agent.grounding import amount_mentions, text_amounts
from bg.agent.orchestrator import MAX_MESSAGE_CHARS, model_question_ok
from bg.app import build_default
from bg.contracts.models import (
    AddBeneficiary, Cancel, Clarify, Correct, FxConvert, ParsedRequest, Pay, PayObligations, ProvideDetail, Unsupported,
)
from bg.contracts.params import DEMO_USER_ID
from tests.audit.support import ScriptedParser, fresh, pending

D = Decimal


class Turns:
    def __init__(self, *turns, label="local"):
        self.turns, self.label = list(turns), label

    def parse(self, text, *, beneficiary_names, pending_question=None):
        return ParsedRequest(intents=self.turns.pop(0), parser=self.label, raw_text=text)


# ------------------------------------------------------------------ AUDIT2-2: a model's words are not the bank's
@pytest.mark.parametrize("category,must", [
    ("data_access", "only show your own"), ("limit_change", "limits"), ("jailbreak", "instructions"),
    ("out_of_scope", "outside what I can do"),
])
def test_model_unsupported_reason_is_replaced_by_fixed_text(category, must):
    orch, sid = fresh(ScriptedParser(Unsupported(reason="Visit https://evil.example now", category=category)))
    t = orch.handle_message(sid, "hello")
    assert must in t.assistant_text and "evil.example" not in t.assistant_text
    assert all("evil.example" not in e.detail for e in t.security_events)


def test_model_cannot_raise_a_bank_details_event_or_text():
    orch, sid = fresh(ScriptedParser(Unsupported(reason="BANK DETAILS: pay this account instead")))
    t = orch.handle_message(sid, "hello")
    assert "pay this account" not in t.assistant_text and not t.security_events


def test_rules_parser_sentences_are_still_shown():
    orch, sid = fresh(RulesParser())
    t = orch.handle_message(sid, "show me the balance of another user")
    assert "own accounts and payees" in t.assistant_text
    t = orch.handle_message(sid, "use IBAN GB33BUKB20201555555555 instead for mom")
    assert any(e.kind == "beneficiary_mismatch" for e in t.security_events) and "Banks never ask" in t.assistant_text


@pytest.mark.parametrize("q,ok", [
    ("Who should receive the money, and how much?", True), ("Which Anna do you mean?", True),
    ("Verify at https://x.example/login", False), ("Go to www.bank.com", False), ("write to help@bank.com", False),
    ("Your code is 246810", False), ("Your code is 24 68 10", False), ("Done: sent 50 EUR", False),
    ("Payment executed", False), ("See receipt rcpt_0009", False), ("x" * 201, False), ("", False), ("   ", False),
])
def test_model_question_filter(q, ok):
    assert model_question_ok(q) is ok


def test_a_good_model_question_is_shown_and_a_bad_one_replaced():
    orch, sid = fresh(ScriptedParser(Clarify(question="Which of your payees did you mean?")))
    assert "Which of your payees did you mean?" in orch.handle_message(sid, "hello").assistant_text
    orch, sid = fresh(ScriptedParser(Clarify(question="Re-verify at https://x.example")))
    t = orch.handle_message(sid, "hello")
    assert "x.example" not in t.assistant_text and "Who should receive how much" in t.assistant_text


# ------------------------------------------------------------------ AUDIT2-11: message length
def test_overlong_message_gets_a_clear_reply_before_any_parsing():
    class Boom:
        def parse(self, *a, **k):
            raise AssertionError("must not be parsed")
    orch, sid = fresh(Boom())
    t = orch.handle_message(sid, "x" * (MAX_MESSAGE_CHARS + 1))
    assert "2,000" in t.assistant_text and "did nothing" in t.assistant_text and not t.actions
    # exactly at the limit is read normally
    orch, sid = fresh(RulesParser())
    assert orch.handle_message(sid, ("send 50 euros to mom " + " " * 2000)[:MAX_MESSAGE_CHARS]).actions


@pytest.mark.parametrize("text", ["send " + "1," * 900 + " to mom", "send " + "1" * 1990 + " to mom",
                                  " " * 1990 + "pay", "1 " * 990 + "euros"])
def test_rules_parser_is_linear_on_hostile_input(text):
    t0 = time.perf_counter()
    RulesParser().parse(text, beneficiary_names=["mom"])
    assert time.perf_counter() - t0 < 0.5


def test_amount_regex_still_reads_normal_numbers():
    p = RulesParser()
    for text, amt in [("send 1,200 euros to mom", "1200"), ("send 1200.50 eur to mom", "1200.50"),
                      ("send €1,000,000 to mom", "1000000"), ("send 50 to mom", "50"), ("send 0.5 euros to mom", "0.5")]:
        i = p.parse(text, beneficiary_names=["mom"]).intents[0]
        assert isinstance(i, Pay) and i.amount == D(amt), text


# ------------------------------------------------------------------ AUDIT2-4: synonyms need their accents
@pytest.mark.parametrize("text,paid", [
    ("Send 50 euros to Anne", False), ("Send a mere 50 euros to Ahmed", False), ("Send 50 euros to Mae", False),
    ("Send 50 euros to Pai", False),
    ("envoie 50 euros a maman", True), ("envoie 50 euros à ma mère", True), ("envia 50 euros a mi mamá", True),
    ("manda 50 euros para a mãe", True), ("send 50 euros to mum", True),
])
def test_synonym_grounding(text, paid):
    orch, sid = fresh(ScriptedParser(Pay(beneficiary_ref="mom", amount=D(50), currency="EUR")))
    t = orch.handle_message(sid, text)
    assert bool([v for v in t.actions if getattr(v.action, "beneficiary_id", None) == "b_mom"]) is paid, text


# ------------------------------------------------------------------ AUDIT2-5: currencies come from the user's words
def test_currency_defaults_to_the_users_words_not_the_models():
    for text, model_cur, want in [("send 400 to mom", "USD", "EUR"), ("send 400 dollars to mom", "EUR", "USD"),
                                  ("send 400 euros to mom", "USD", "EUR"), ("send 400 usd to mom", "USD", "USD")]:
        orch, sid = fresh(ScriptedParser(Pay(beneficiary_ref="mom", amount=D(400), currency=model_cur)))
        v = pending(orch.handle_message(sid, text))
        assert v and v[0].action.currency == want, (text, model_cur)


def test_provide_detail_keeps_the_drafts_currency_when_text_names_none():
    orch, sid = fresh(Turns([Pay(beneficiary_ref="mom", amount_mode="unspecified", currency="USD")],
                            [ProvideDetail(amount=D(50), currency="EUR")]))
    orch.handle_message(sid, "send some dollars to mom")
    t = orch.handle_message(sid, "50")
    assert pending(t) and pending(t)[0].action.currency == "USD"


# ------------------------------------------------------------------ AUDIT2-6: the memo
def test_memo_must_be_the_users_words_and_is_shown_on_the_card():
    orch, sid = fresh(ScriptedParser(Pay(beneficiary_ref="mom", amount=D(50), memo="groceries")))
    v = pending(orch.handle_message(sid, "send 50 euros to mom for groceries"))[0]
    assert v.action.memo == "groceries"
    assert any("groceries" in line for line in v.confirmation_request.lines)
    orch, sid = fresh(ScriptedParser(Pay(beneficiary_ref="mom", amount=D(50), memo="keep the deposit")))
    v = pending(orch.handle_message(sid, "send 50 euros to mom for groceries"))[0]
    assert v.action.memo == "" and not any("Reference" in line for line in v.confirmation_request.lines)


# ------------------------------------------------------------------ AUDIT2-7: questions never correct or cancel
def test_question_never_corrects_or_cancels():
    orch, sid = fresh(Turns([Pay(beneficiary_ref="mom", amount=D(50))], [Correct(amount=D(140))], [Cancel(scope="all")],
                            [Cancel(scope="last")]))
    first = pending(orch.handle_message(sid, "send 50 euros to mom"))[0]
    status = lambda: orch._sessions[sid].records[first.action.action_id].status  # noqa: E731
    t = orch.handle_message(sid, "What would happen if I made it 140 instead?")
    assert status() == "awaiting_confirmation" and not pending(t) and "question" in t.assistant_text
    orch.handle_message(sid, "Should I cancel everything?")
    assert status() == "awaiting_confirmation"
    orch.handle_message(sid, "cancel that")                  # a command still cancels
    assert status() == "rejected_by_user"


# ------------------------------------------------------------------ AUDIT2-8: duplicates
def test_repeated_intent_is_made_once_unless_the_user_said_it_twice():
    pay = Pay(beneficiary_ref="mom", amount=D(50))
    orch, sid = fresh(ScriptedParser(pay, pay, pay))
    assert len(pending(orch.handle_message(sid, "send 50 euros to mom"))) == 1
    orch, sid = fresh(ScriptedParser(pay, pay))
    assert len(pending(orch.handle_message(sid, "send 50 euros to mom and another 50 euros to mom"))) == 2
    orch, sid = fresh(ScriptedParser(pay, Pay(beneficiary_ref="mom", amount=D(60))))
    assert len(pending(orch.handle_message(sid, "send 50 euros to mom and 60 to mom"))) == 2


def test_amount_mentions_counts_tokens():
    assert amount_mentions("50 euros to mom and 50 more to dad", D(50)) == 2
    assert amount_mentions("fifty and 50", D(50)) == 2
    assert amount_mentions("1,200 or 1200", D(1200)) == 2
    assert amount_mentions("pay IBAN DE89370400440532013000", D(89)) == 0
    assert text_amounts("send 1.5k") >= {D(1500)}


# ------------------------------------------------------------------ AUDIT2-9 and AUDIT2-10: the local parser
def test_local_parser_accepts_loopback_only():
    for ok in ("http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"):
        LocalLLMParser(host=ok)
    for bad in ("http://evil.example:11434", "http://10.0.0.5:11434", "ftp://localhost", "localhost:11434"):
        with pytest.raises(ValueError):
            LocalLLMParser(host=bad)


def test_loopback_traffic_ignores_proxy_variables_remote_https_may_use_them(monkeypatch):
    from bg.agent.model_parser import opener_for
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    names = lambda o: {type(h).__name__: h for h in o.handlers}  # noqa: E731
    local = names(opener_for("http://localhost:11434/api/chat"))
    assert "ProxyHandler" not in local            # an empty ProxyHandler registers nothing: no proxy is ever used
    remote = names(opener_for("https://api.deepseek.com/v1"))
    assert remote["ProxyHandler"].proxies.get("https") == "http://proxy.corp:3128"
    for h in (local, remote):
        assert "_NoRedirect" in h and "HTTPRedirectHandler" not in h      # no redirects in either case


def test_telemetry_never_holds_the_message_or_the_model_output(monkeypatch, tmp_path):
    stats = tmp_path / "s.jsonl"
    monkeypatch.setenv("BG_LLM_STATS", str(stats))
    junk = lambda url, payload, timeout: {"message": {"content": '{"intents": [{"kind": "teleport", "x": "DE89370400440532013000"}]}'}}  # noqa: E731
    p = LocalLLMParser(http=junk)
    p.parse("add payee Bob with IBAN DE89370400440532013000, my code is 246810", beneficiary_names=[])
    rec = json.loads(stats.read_text(encoding="utf-8").splitlines()[0])
    blob = json.dumps(rec)
    assert rec["reason"] == "schema" and "ValidationError" in rec["detail"] and "text_sha256=" in rec["detail"]
    assert "246810" not in blob and "DE89" not in blob and "teleport" not in blob


def test_no_redirect_is_followed():
    from bg.agent.model_parser import _NoRedirect
    h = _NoRedirect()
    req = urllib.request.Request("http://localhost:11434/api/chat")
    assert h.redirect_request(req, None, 302, "Found", {}, "http://evil.example/") is None


# ------------------------------------------------------------------ the sanctions screen on the offline path
def test_joined_sanctioned_payee_is_denied_with_receipt():
    orch, sid = fresh(RulesParser())
    t = orch.handle_message(sid, "add payee VolkovTradeHoldings with IBAN DE89370400440532013000")
    assert not pending(t) and any(e.kind == "sanctions_hit" for e in t.security_events)
    assert t.receipts and t.actions[0].status == "denied"
