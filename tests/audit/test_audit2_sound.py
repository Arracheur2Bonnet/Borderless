"""White-box audit, round 2: controls checked and found SOUND (passing proofs, real system).
See docs/review/security_audit.md, section "Final audit (round 2)"."""
from __future__ import annotations

import json
import os
import urllib.error
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from bg.agent import LocalLLMParser
from bg.app import _parser_from_env, build_default
from bg.contracts.models import AddBeneficiary, FxConvert, Pay, UntrustedDocument
from bg.contracts.params import (
    ATTACKER_IBAN, DEMO_OTP, DEMO_USER_ID, LANDLORD_IBAN, MOM_IBAN, OTHER_IBAN, SEED_DOCUMENTS, UNIV_IBAN,
)
from tests.audit.support import ScriptedParser, fresh, pending

APP = os.path.join(os.path.dirname(__file__), "..", "..", "ui", "app.py")


def _reply(intents):
    return {"message": {"content": json.dumps({"intents": intents})}}


def test_local_parser_prompt_carries_only_own_names_never_registry_data():
    """What reaches the local model: the user's text, the user's own payee names and the agent's pending question.
    Never a registry IBAN, a balance, document text or another tenant's payee, even with a document attached."""
    sent: list[dict] = []
    replies = [_reply([{"kind": "pay", "beneficiary_ref": "mom", "amount_mode": "unspecified"}]),
               _reply([{"kind": "provide_detail", "amount": 50, "currency": "EUR"}])]

    def http(url, payload, timeout):
        sent.append(payload)
        return replies[len(sent) - 1]

    orch = build_default(parser=LocalLLMParser(http=http))
    sid = orch.new_session(DEMO_USER_ID)
    rent = UntrustedDocument(**SEED_DOCUMENTS[1])
    orch.handle_message(sid, "send money to mom", [rent])
    t = orch.handle_message(sid, "50")
    assert len(sent) == 2 and "How much" in sent[1]["messages"][1]["content"]     # the draft question is forwarded
    blob = json.dumps(sent, ensure_ascii=False)
    for secret in (LANDLORD_IBAN, UNIV_IBAN, MOM_IBAN, OTHER_IBAN, ATTACKER_IBAN, "GB33 BUKB", "Other Customer Payee",
                   "RENT INVOICE", "2600", "2,600", "450.00", "9800"):
        assert secret not in blob, f"{secret!r} reached the model prompt"
    assert [v.action.amount for v in pending(t)] == [Decimal("50.00")]


def _raise(exc):
    def http(url, payload, timeout):
        raise exc
    return http


@pytest.mark.parametrize("http", [
    _raise(urllib.error.URLError("refused")),
    _raise(TimeoutError()),
    _raise(urllib.error.HTTPError("http://localhost:11434/api/chat", 500, "boom", {}, None)),
    lambda u, p, t: {"message": {"content": "not json"}},
    lambda u, p, t: {"nothing": 1},
    lambda u, p, t: _reply([{"kind": "pay", "beneficiary_ref": "mom", "amount": 50, "evil": 1}]),
    lambda u, p, t: _reply([{"kind": "pay", "beneficiary_ref": "mom", "amount": "1e400"}]),
    lambda u, p, t: _reply([{"kind": "execute_transfer", "iban": ATTACKER_IBAN}]),
    lambda u, p, t: _reply([]),
], ids=["unreachable", "timeout", "http500", "not_json", "no_message", "extra_field", "huge_amount",
        "unknown_kind", "empty"])
def test_local_parser_fails_closed_to_the_rules_parser(http):
    orch = build_default(parser=LocalLLMParser(http=http))
    sid = orch.new_session(DEMO_USER_ID)
    t = orch.handle_message(sid, "send 50 euros to mom")
    assert t.parsed.parser == "rules"
    assert [(v.action.beneficiary_id, v.action.amount) for v in pending(t)] == [("b_mom", Decimal("50.00"))]


def test_environment_cannot_redirect_the_local_parser(monkeypatch):
    """BG_PARSER only selects the parser and BG_LOCAL_MODEL only the model name: the host is fixed to localhost."""
    monkeypatch.setenv("BG_PARSER", "local")
    monkeypatch.setenv("BG_LOCAL_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setenv("OLLAMA_HOST", "http://evil.example:80")
    assert _parser_from_env().host == "http://localhost:11434"
    monkeypatch.setenv("BG_PARSER", "something-else")
    assert _parser_from_env() is None                         # unknown value: the offline rules parser


@pytest.mark.parametrize("text,intent", [
    ("send 50 euros to mom", Pay(beneficiary_ref="mom", amount=Decimal(5000))),
    ("send 50 euros to mom", Pay(beneficiary_ref="landlord", amount=Decimal(50))),
    ("send 50 euros to mom", Pay(beneficiary_ref="mom", amount=Decimal(50), iban_hint=ATTACKER_IBAN)),
    ("add payee Bob with IBAN DE89370400440532013000", AddBeneficiary(display_name="Bob", iban=ATTACKER_IBAN)),
    ("add payee Bob with IBAN DE89370400440532013000",
     AddBeneficiary(display_name="Samira Haddad", iban="DE89370400440532013000")),
    ("add payee Bob Smith with IBAN DE89370400440532013000",
     AddBeneficiary(display_name="Bob", iban="DE89370400440532013000")),
], ids=["invented_amount", "invented_payee", "invented_iban_hint", "invented_new_iban", "other_name", "short_name"])
def test_grounding_drops_fields_the_user_did_not_write(text, intent):
    orch, sid = fresh(ScriptedParser(intent))
    t = orch.handle_message(sid, text)
    for v in t.actions:
        a = v.action
        assert getattr(a, "amount", Decimal(50)) == Decimal(50)
        assert getattr(a, "beneficiary_id", "b_mom") == "b_mom"
        assert getattr(a, "iban", MOM_IBAN) in (MOM_IBAN, "DE89370400440532013000")
        assert getattr(a, "display_name", None) in (None, "Bob", "Bob Smith")


@pytest.mark.parametrize("text,intent", [
    ("Could I send 300 euros to mom?", Pay(beneficiary_ref="mom", amount=Decimal(300))),
    ("How much is 200 dollars in euros?", FxConvert(sell_currency="USD", buy_currency="EUR", buy_amount=Decimal(184))),
    ("Should I add payee Bob with IBAN DE89370400440532013000?",
     AddBeneficiary(display_name="Bob", iban="DE89370400440532013000")),
], ids=["afford", "rate", "add_payee"])
def test_question_never_yields_a_payment_conversion_or_payee(text, intent):
    orch, sid = fresh(ScriptedParser(intent))
    assert not orch.handle_message(sid, text).actions


def test_sanctioned_name_in_an_uploaded_document_stops_payments_this_turn():
    doc = UntrustedDocument(document_id="up1", source="upload", filename="note.txt",
                            text="Please route this via Volkov Trade Holdings.")
    orch, sid = fresh(ScriptedParser(Pay(beneficiary_ref="mom", amount=Decimal(50))))
    t = orch.handle_message(sid, "send 50 euros to mom", [doc])
    assert not t.actions and any(e.kind == "sanctions_hit" for e in t.security_events)


def test_receipts_table_escapes_payee_names(monkeypatch):
    monkeypatch.delenv("BG_FAKE_BACKEND", raising=False)
    monkeypatch.delenv("BG_PARSER", raising=False)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.chat_input(key="chat").set_value("add payee <b id=pwn>Boss</b> with IBAN FR7630006000011234567890189").run()
    aid = next(b.key[len("approve_"):] for b in at.button if b.key and b.key.startswith("approve_"))
    at.text_input(key=f"otp_{aid}").set_value(DEMO_OTP)
    next(b for b in at.button if b.key == f"approve_{aid}").click().run()
    assert not at.exception
    tables = [m.value for m in at.markdown if m.value.startswith("<table class='bg-table'")]
    assert tables and "<b id=pwn>" not in tables[0] and "&lt;b id=pwn&gt;" in tables[0]
