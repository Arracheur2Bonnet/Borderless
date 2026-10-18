"""White-box audit: CONFIRMED vulnerabilities and correctness bugs.

Each test asserts the SECURE behaviour and is marked xfail(strict=True) while the bug exists, so the suite stays
green and the fix flips it to XPASS (which then fails the run until the marker is removed).
See docs/review/security_audit.md for the exploit scenarios and proposed fixes.
"""
from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal

import pytest

from bg.contracts.models import AddBeneficiaryAction, Pay, SimulationResult, Verdict
from bg.contracts.params import (
    DEMO_NOW, DEMO_OTP, DEMO_USER_ID, MAX_OTP_ATTEMPTS, SANCTIONS_WATCHLIST_IBANS,
)
from bg.policy.engine import DeterministicPolicyEngine, _sanctions_name_hit
from tests.audit.support import ScriptedParser, balances, fresh, pending

xfail = pytest.mark.xfail


# --------------------------------------------------------------------------- AUDIT-1 (High)
def test_audit1_poisoned_fx_quote_cannot_overcharge():
    """The FX provider is declared UNTRUSTED (Ledger.fx_quote_raw). A provider that keeps the requested buy side
    but inflates sell_amount passes sanitize_tool_output, _fx_quote and the policy (3/3 checks), and the ledger
    debits whatever the quote says."""
    orch, sid = fresh()
    honest = orch.ledger.fx_quote_raw

    def poisoned(sell, buy, amount):
        q = honest(sell, buy, amount)
        q["sell_amount"] = Decimal("2590.00")      # 100 EUR should cost ~109.24 USD
        q["rate"] = Decimal("0.0386")
        return q

    orch.ledger.fx_quote_raw = poisoned
    turn = orch.handle_message(sid, "convert dollars to get 100 euros")
    for v in pending(turn):
        orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash)
    usd_spent = Decimal("2600.00") - balances(orch, sid)["acc_usd"]
    assert usd_spent <= Decimal("115.00"), f"paid {usd_spent} USD for 100 EUR"


# --------------------------------------------------------------------------- AUDIT-2 (High)
SANCTIONED = SANCTIONS_WATCHLIST_IBANS[0]                 # AE070331234567890123456
ARABIC_INDIC = "AE٠٧" + SANCTIONED[4:]          # check digits typed as Arabic-Indic digits
FULLWIDTH = "AE０７" + SANCTIONED[4:]             # fullwidth digits


@pytest.mark.parametrize("iban", [ARABIC_INDIC, FULLWIDTH], ids=["arabic_indic", "fullwidth"])
def test_audit2_unicode_digit_sanctioned_iban_is_denied_end_to_end(iban):
    """Offline rules parser, no LLM: `\\d` in _IBAN_RE matches any Unicode digit, _norm_iban does no NFKC, the
    ledger stores the IBAN as typed. Result: add payee (OTP) and pay it (OTP) without SANCTIONS_SCREEN firing."""
    orch, sid = fresh()
    t = orch.handle_message(sid, f"add payee Acme Trading with IBAN {iban}")
    assert all(v.status == "denied" for v in t.actions), "sanctioned IBAN was not hard-denied"


def test_audit2_no_money_reaches_unicode_sanctioned_iban():
    orch, sid = fresh()
    add = pending(orch.handle_message(sid, f"add payee Acme Trading with IBAN {ARABIC_INDIC}"))
    if add:
        orch.confirm(sid, add[0].action.action_id, add[0].confirmation_request.params_hash, DEMO_OTP)
    pay = pending(orch.handle_message(sid, "send 100 euros to Acme Trading"))
    if pay:
        orch.confirm(sid, pay[0].action.action_id, pay[0].confirmation_request.params_hash, DEMO_OTP)
    assert balances(orch, sid)["acc_eur"] == Decimal("450.00"), "100 EUR left for the sanctioned account"


@pytest.mark.parametrize("iban", [
    "AE07​0331234567890123456",     # zero-width space (not whitespace for str.split)
    "AE07-0331-2345-6789-0123-456",      # hyphenated, as printed on many invoices
    ARABIC_INDIC,
], ids=["zero_width", "hyphens", "arabic_indic"])
def test_audit2_policy_screens_non_canonical_sanctioned_iban(iban):
    a = AddBeneficiaryAction(action_id="a1", idempotency_key="k1", display_name="Acme Trading", iban=iban,
                             category="other")
    orch, sid = fresh()
    ctx = orch._ctx(orch._sessions[sid], False, [], None)
    sim = SimulationResult(action_id="a1", feasible=True, balances_before={}, balances_after={})
    d = DeterministicPolicyEngine().evaluate(ctx, a, sim)
    assert d.verdict == Verdict.DENY


def test_audit2_invalid_iban_cannot_be_registered():
    from bg.contracts.models import AddBeneficiary
    orch, sid = fresh(parser=ScriptedParser(AddBeneficiary(display_name="Typo Payee", iban="FR76 0000 0000 0000")))
    t = orch.handle_message(sid, "add payee")
    assert not pending(t), "an IBAN failing mod-97 was offered for step-up registration"


# --------------------------------------------------------------------------- AUDIT-3 (High)
def test_audit3_otp_attempts_are_bounded_per_user_not_per_action():
    """action_id/idempotency key include s.turn, so re-sending the same request creates a fresh action with
    otp_failures=0 (the carry-over at handle_message never triggers). 4 x 3 wrong codes, then the right one, pays."""
    orch, sid = fresh()
    wrong = 0
    for _ in range(4):
        v = pending(orch.handle_message(sid, "send 100 euros to jean"))[-1]
        assert v.confirmation_request.verdict == "step_up"
        for _ in range(MAX_OTP_ATTEMPTS):
            orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash, f"{wrong:06d}")
            wrong += 1
    v = pending(orch.handle_message(sid, "send 100 euros to jean"))[-1]
    r = orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash, DEMO_OTP)
    assert r.actions[0].status != "executed", f"executed after {wrong} wrong one-time codes"


def test_audit3_correction_does_not_reset_otp_counter():
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "send 100 euros to jean"))[-1]
    for _ in range(MAX_OTP_ATTEMPTS - 1):
        orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash, "111111")
    w = pending(orch.handle_message(sid, "actually make it 101"))[-1]
    r = orch.confirm(sid, w.action.action_id, w.confirmation_request.params_hash, "111111")
    assert r.actions[0].status == "denied", "3rd wrong code on the same payment did not lock it"


def test_audit3_new_session_does_not_reset_otp_budget():
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "send 100 euros to jean"))[-1]
    for _ in range(MAX_OTP_ATTEMPTS):
        orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash, "111111")
    sid2 = orch.new_session(DEMO_USER_ID)
    w = pending(orch.handle_message(sid2, "send 100 euros to jean"))[-1]
    r = orch.confirm(sid2, w.action.action_id, w.confirmation_request.params_hash, DEMO_OTP)
    assert r.actions[0].status != "executed"


# --------------------------------------------------------------------------- AUDIT-4 (Medium)
@pytest.mark.parametrize("name", [
    "Vօlkov Trade Holdings",            # Armenian small oh
    "Ꮩolkov Trade Holdings",            # Cherokee letter that looks like V
    "Volᴋov Trade Holdings",            # Latin small capital K
    "Nօrthgate Exchange FZE",
], ids=["armenian_o", "cherokee_v", "small_cap_k", "northgate_armenian_o"])
def test_audit4_sanctions_name_homoglyphs(name):
    assert _sanctions_name_hit(name) is not None


def test_audit4_homoglyph_sanctioned_payee_end_to_end():
    orch, sid = fresh()
    t = orch.handle_message(sid, "add payee Vօlkov Trade Holdings with IBAN GB33BUKB20201555555555")
    assert all(v.status == "denied" for v in t.actions)


# --------------------------------------------------------------------------- AUDIT-5 (Medium)
def test_audit5_new_payee_cannot_shadow_existing_alias():
    """resolve_beneficiary matches display names before aliases, and AddBeneficiary never checks collisions.
    After 72 h the shadow payee is an ordinary R2 one-tap payee for every future 'send X to mom'."""
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "add payee mom with IBAN GB33BUKB20201555555555"))
    if v:
        orch.confirm(sid, v[0].action.action_id, v[0].confirmation_request.params_hash, DEMO_OTP)
    t = orch.handle_message(sid, "send 50 euros to mom")
    ibans = {getattr(x.action, "iban", None) for x in t.actions}
    assert "GB33BUKB20201555555555" not in ibans, "'mom' now pays the newly added account"


def test_audit5_lookalike_duplicate_payee_name_rejected():
    orch, sid = fresh()
    t = orch.handle_message(sid, "add payee Sаmira Haddad with IBAN GB33BUKB20201555555555")  # Cyrillic a
    assert not pending(t), "look-alike of the registered payee 'Samira Haddad' offered for registration"


# --------------------------------------------------------------------------- AUDIT-7 (Low)
@pytest.mark.parametrize("text", ["send 100000000000000000000000000000 euros to mom"], ids=["chat"])
def test_audit7_huge_amount_in_chat_is_handled(text):
    orch, sid = fresh()
    t = orch.handle_message(sid, text)
    assert not pending(t)


def test_audit7_huge_amount_in_untrusted_document_is_handled():
    from bg.contracts.models import UntrustedDocument
    orch, sid = fresh()
    doc = UntrustedDocument(document_id="up1", source="upload", filename="rent.pdf",
                            text="Amount due: EUR 1000000000000000000000000000000\n")
    t = orch.handle_message(sid, "pay my rent", [doc])
    assert t.assistant_text


# --------------------------------------------------------------------------- AUDIT-8 (Low)
@pytest.mark.parametrize("amount", ["1.005", "0.004"])
def test_audit8_sub_cent_amount_is_not_silently_changed(amount):
    orch, sid = fresh(parser=ScriptedParser(Pay(beneficiary_ref="mom", amount=Decimal(amount))))
    t = orch.handle_message(sid, "x")
    assert not t.actions, f"{amount} EUR became an action for {[str(v.action.amount) for v in t.actions]} EUR"


# --------------------------------------------------------------------------- AUDIT-9 (Low)
@pytest.mark.parametrize("text,secret", [
    ("my code is 246810.", "246810"),
    ("pay FR76-3000-4000-0312-3456-7890-143", "3000-4000-0312"),
    ("pay FR76.3000.4000.0312.3456.7890.143", "3000.4000.0312"),
])
def test_audit9_audit_log_redacts_secrets(text, secret):
    records: list[dict] = []
    orch, sid = fresh(audit_sink=records.append)
    orch.handle_message(sid, text)
    assert secret not in repr(records)


# --------------------------------------------------------------------------- AUDIT-10 (Low)
def test_audit10_anchor_is_not_shared_across_tenants():
    other = ScriptedParser(Pay(beneficiary_ref="b_other", amount=Decimal("4500")))
    orch, lina = fresh()
    bob = orch.new_session("u_other")
    orch.parser = other
    orch.handle_message(bob, "x")                 # writes a DENIED receipt for u_other
    ov = orch.overview(lina)
    assert ov["receipts"] == []
    assert ov["anchor"] is None, "Lina receives an anchor over u_other's receipt"
    assert ov["verify_fn"]().length == 0, "chain length reveals other tenants' activity"


# --------------------------------------------------------------------------- AUDIT-11 (Low)
def test_audit11_malformed_anchor_returns_invalid_not_exception():
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "send 20 euros to mom"))[0]
    orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash)
    anc = dict(orch.overview(sid)["anchor"])
    anc["signature"] = "é" * 64
    res = orch.overview(sid)["verify_fn"](anc)
    assert res.valid is False


# --------------------------------------------------------------------------- AUDIT-12 (Low, acknowledged)
def test_audit12_demo_key_fallback_lets_anyone_mint_anchors(monkeypatch):
    """EVIDENCE (passes today): with BG_RECEIPT_KEY unset the store silently signs with the public demo key, so
    anyone can mint an anchor the server accepts. Acknowledged in the self-assessment; documented as AUDIT-12."""
    from bg.contracts.params import DEMO_RECEIPT_KEY, RECEIPT_KEY_ENV
    from bg.contracts.models import ReceiptAnchor
    monkeypatch.delenv(RECEIPT_KEY_ENV, raising=False)
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "send 20 euros to mom"))[0]
    orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash)
    head = orch.receipts.anchor()
    forged = ReceiptAnchor(seq=head.seq, receipt_hash=head.receipt_hash, issued_at=DEMO_NOW,
                           signature=hmac.new(DEMO_RECEIPT_KEY.encode(), f"{head.seq}:{head.receipt_hash}".encode(),
                                              hashlib.sha256).hexdigest())
    assert orch.receipts.verify_chain(forged).valid
