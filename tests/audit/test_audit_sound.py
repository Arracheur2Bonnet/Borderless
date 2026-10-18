"""White-box audit: controls checked and found SOUND (passing proofs). See docs/review/security_audit.md."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from bg.contracts.hashing import params_hash
from bg.contracts.models import (
    AddBeneficiary, Correct, Pay, PaymentAction, RecentPayment, ReceiptAnchor, SimulationResult, UntrustedDocument,
    Verdict,
)
from bg.contracts.params import ATTACKER_IBAN, DEMO_NOW, DEMO_OTP, DEMO_USER_ID, MOM_IBAN
from bg.ledger import SandboxLedger, SqliteReceiptStore
from bg.policy.engine import DeterministicPolicyEngine
from tests.audit.support import ScriptedParser, balances, fresh, fund, pending


def _confirm(orch, sid, v, otp=None):
    return orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash, otp)


def test_daily_limit_rechecked_at_confirm_across_sessions():
    """Two sessions each propose 3,000 EUR while nothing is executed (each passes alone); the second
    confirmation is re-evaluated against executed_today and denied."""
    orch, s1 = fresh()
    fund(orch, "acc_eur", "20000.00")
    s2 = orch.new_session(DEMO_USER_ID)
    a = pending(orch.handle_message(s1, "send 3000 euros to mom"))[0]
    b = pending(orch.handle_message(s2, "send 3000 euros to university"))[0]
    assert _confirm(orch, s1, a, DEMO_OTP).actions[0].status == "executed"
    r = _confirm(orch, s2, b, DEMO_OTP)
    assert r.actions[0].status == "denied"
    assert any(x.rule_id == "DAILY_LIMIT" and not x.passed for x in r.actions[0].decision.rules)


def test_daily_limit_counts_foreign_currency_payments():
    orch, sid = fresh()
    fund(orch, "acc_usd", "20000.00")
    fund(orch, "acc_eur", "20000.00")
    a = pending(orch.handle_message(sid, "send 4000 euros to mom"))[0]
    assert _confirm(orch, sid, a, DEMO_OTP).actions[0].status == "executed"
    t = orch.handle_message(sid, "send 1200 dollars to mom")      # 1,104 EUR eq. -> 5,104 > 5,000
    assert [v.status for v in t.actions] == ["denied"]
    assert any(r.rule_id == "DAILY_LIMIT" and not r.passed for r in t.actions[0].decision.rules)


def test_structuring_rechecked_at_confirm_within_one_plan():
    orch, sid = fresh()
    fund(orch, "acc_eur", "5000.00")
    p = pending(orch.handle_message(sid, "send 400 euros to mom and 400 euros to mom"))
    assert len(p) == 2 and all(v.confirmation_request.verdict == "confirm" for v in p)
    q = pending(orch.handle_message(sid, "send 800 euros to mom"))[0]
    assert q.confirmation_request.verdict == "confirm"            # nothing executed yet: passes alone
    _confirm(orch, sid, p[0]); _confirm(orch, sid, p[1])
    r = _confirm(orch, sid, q)                                    # 400+400+800 = 1,600 > 1,500 -> step-up
    assert r.actions[0].status == "awaiting_confirmation"
    assert r.actions[0].confirmation_request.verdict == "step_up"
    assert balances(orch, sid)["acc_eur"] == Decimal("4200.00")


def test_confirmation_bound_to_session_and_user():
    orch, lina = fresh()
    v = pending(orch.handle_message(lina, "send 20 euros to mom"))[0]
    other_same_user = orch.new_session(DEMO_USER_ID)
    other_tenant = orch.new_session("u_other")
    for sid in (other_same_user, other_tenant):
        r = _confirm(orch, sid, v)
        assert not r.actions or r.actions[0].status != "executed"
    assert balances(orch, lina)["acc_eur"] == Decimal("450.00")


def test_params_hash_distinguishes_every_field():
    base = PaymentAction(action_id="a", idempotency_key="k", from_account_id="acc_eur", beneficiary_id="b_mom",
                         iban=MOM_IBAN, amount=Decimal("50"), currency="EUR")
    variants = [base.model_copy(update={f: v}) for f, v in (
        ("amount", Decimal("50.01")), ("iban", ATTACKER_IBAN), ("beneficiary_id", "b_univ"),
        ("currency", "USD"), ("from_account_id", "acc_usd"), ("memo", "x"), ("derived_from_untrusted", True),
        ("idempotency_key", "k2"))]
    hashes = {params_hash(x) for x in [base, *variants]}
    assert len(hashes) == len(variants) + 1
    assert params_hash(base) == params_hash(base.model_copy(update={"amount": Decimal("50.00")}))


def test_tampered_hash_refused_and_nothing_moves():
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "send 20 euros to mom"))[0]
    forged = params_hash(v.action.model_copy(update={"amount": Decimal("400")}))
    r = orch.confirm(sid, v.action.action_id, forged)
    assert any(e.kind == "param_tamper" for e in r.security_events)
    assert balances(orch, sid)["acc_eur"] == Decimal("450.00")


def test_idempotency_key_cannot_be_reused_by_another_tenant():
    led = SandboxLedger()
    a = PaymentAction(action_id="a", idempotency_key="shared", from_account_id="acc_eur", beneficiary_id="b_mom",
                      iban=MOM_IBAN, amount=Decimal("10"), currency="EUR")
    assert led.execute(DEMO_USER_ID, a, None).status == "executed"
    other = a.model_copy(update={"from_account_id": "acc_other_eur"})
    r = led.execute("u_other", other, None)
    assert r.status == "failed" and r.error == "IDEMPOTENCY_CONFLICT"
    r2 = led.execute("u_other", a, None)            # exact replay by another tenant: not a 'replayed' success
    assert r2.status == "failed"


def test_step_up_verification_does_not_leak_to_other_actions():
    orch, sid = fresh()
    a = pending(orch.handle_message(sid, "send 100 euros to jean"))[0]
    b = pending(orch.handle_message(sid, "send 50 euros to jean"))[0]
    assert _confirm(orch, sid, a, DEMO_OTP).actions[0].status == "executed"
    r = _confirm(orch, sid, b)                       # no code: still needs its own OTP
    assert r.actions[0].status == "awaiting_confirmation"


def test_locked_action_stays_locked_with_correct_code():
    orch, sid = fresh()
    v = pending(orch.handle_message(sid, "send 100 euros to jean"))[0]
    for _ in range(3):
        _confirm(orch, sid, v, "000000")
    r = _confirm(orch, sid, v, DEMO_OTP)
    assert r.actions[0].status == "denied"
    assert balances(orch, sid)["acc_eur"] == Decimal("450.00")


def test_correction_above_cap_is_denied_not_inherited():
    orch, sid = fresh()
    fund(orch, "acc_eur", "20000.00")
    pending(orch.handle_message(sid, "send 50 euros to mom"))
    t = orch.handle_message(sid, "actually make it 4500")
    new = [v for v in t.actions if v.status not in ("rejected_by_user",)]
    assert new and all(v.status == "denied" for v in new)


def test_compromised_parser_cannot_pay_other_tenant_payee_or_unregistered_iban():
    orch, sid = fresh(parser=ScriptedParser(Pay(beneficiary_ref="b_other", amount=Decimal("10")),
                                            Pay(beneficiary_ref="mom", amount=Decimal("10"), iban_hint=ATTACKER_IBAN)))
    t = orch.handle_message(sid, "x")
    for v in t.actions:
        assert v.action.iban == MOM_IBAN
    assert all(getattr(v.action, "beneficiary_id", "") != "b_other" for v in t.actions)


def test_document_iban_and_amount_never_reach_executed_action():
    orch, sid = fresh()
    doc = UntrustedDocument(document_id="up", source="upload", filename="tuition.pdf",
                            text=f"Amount due: EUR 3,999.00\nIBAN: {ATTACKER_IBAN}\n")
    t = orch.handle_message(sid, "pay my tuition", [doc])
    pays = [v.action for v in t.actions if v.action.action_type == "payment"]
    assert pays and all(p.iban != ATTACKER_IBAN and p.amount == Decimal("1200.00") for p in pays)


def test_tenant_scoped_reads():
    led = SandboxLedger()
    assert led.resolve_beneficiary(DEMO_USER_ID, "b_other") is None
    assert led.get_document("u_other", "doc_rent_nov") is None
    assert all(a.user_id == DEMO_USER_ID for a in led.get_accounts(DEMO_USER_ID))
    assert led.resolve_beneficiary(DEMO_USER_ID, "mom​") is None     # no fuzzy / normalisation-based match


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_amounts_rejected_by_contract(bad):
    with pytest.raises(ValidationError):
        Pay(beneficiary_ref="mom", amount=bad)


def test_policy_fails_closed_on_bad_context():
    orch, sid = fresh()
    a = PaymentAction(action_id="a", idempotency_key="k", from_account_id="acc_eur", beneficiary_id="b_mom",
                      iban=MOM_IBAN, amount=Decimal("10"), currency="EUR")
    ctx = orch._ctx(orch._sessions[sid], False, [], None).model_copy(update={"recent_payments": [
        RecentPayment(beneficiary_id="b_mom", amount_eur=Decimal("-1"), executed_at=DEMO_NOW)]})
    sim = SimulationResult(action_id="a", feasible=True, balances_before={}, balances_after={})
    d = DeterministicPolicyEngine().evaluate(ctx, a, sim)
    assert d.verdict == Verdict.DENY and d.rules[0].rule_id == "POLICY_ERROR"


def test_anchor_from_other_chain_or_key_is_rejected():
    s1, s2 = SqliteReceiptStore(key="k1"), SqliteReceiptStore(key="k1")
    s3 = SqliteReceiptStore(key="k2")
    for st, summary in ((s1, "A"), (s2, "B"), (s3, "A")):
        st.append(action_id="x", intent={"user_id": DEMO_USER_ID}, action={"a": 1}, simulation=None,
                  decision={"d": 1}, confirmation=None, result={"r": 1}, summary=summary)
    anc = s1.anchor()
    assert s1.verify_chain(anc).valid
    assert not s2.verify_chain(anc).valid           # same key, different chain: hash mismatch
    assert not s3.verify_chain(anc).valid           # different key: signature invalid
    assert not s1.verify_chain(ReceiptAnchor(seq=anc.seq + 5, receipt_hash=anc.receipt_hash,
                                             signature=anc.signature, issued_at=DEMO_NOW)).valid


def test_audit_trail_drops_otp_field_and_masks_plain_ibans():
    records: list[dict] = []
    orch, sid = fresh(audit_sink=records.append)
    v = pending(orch.handle_message(sid, "send 100 euros to jean"))[0]
    _confirm(orch, sid, v, DEMO_OTP)
    orch.handle_message(sid, f"send 5 euros to mom iban {ATTACKER_IBAN}")
    blob = repr(records)
    assert DEMO_OTP not in blob and ATTACKER_IBAN not in blob and MOM_IBAN not in blob
