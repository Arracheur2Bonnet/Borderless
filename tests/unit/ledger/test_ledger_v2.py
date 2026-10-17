"""v2 ledger additions: recent_payments and signed receipt anchors."""
import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal as D

from bg.contracts import params
from bg.contracts.models import (
    AddBeneficiaryAction, Confirmation, ExecutionResult, FxAction, FxQuote, PaymentAction,
    PolicyDecision, RiskLevel, Verdict,
)
from bg.ledger import SandboxLedger, SqliteReceiptStore

U = params.DEMO_USER_ID
T0 = params.DEMO_NOW


def fx_action(led, amount="2200.00", key="k_fx"):
    q = FxQuote(**led.fx_quote_raw("USD", "EUR", D(amount)))
    return FxAction(action_id="a_fx", idempotency_key=key, from_account_id="acc_usd",
                    to_account_id="acc_eur", quote=q)


def pay(aid, bid, amount, key=None, acc="acc_eur"):
    iban = {"b_univ": params.UNIV_IBAN, "b_landlord": params.LANDLORD_IBAN}[bid]
    return PaymentAction(action_id=aid, idempotency_key=key or "k_" + aid, from_account_id=acc,
                         beneficiary_id=bid, iban=iban, amount=D(amount), currency="EUR")


def _raw(st, sql, args=()):
    st._db.execute(sql, args)
    st._db.commit()


# ------------------------------------------------------------------ recent_payments
def test_recent_payments_only_executed_payments_in_eur():
    led = SandboxLedger()
    led.execute(U, fx_action(led), None)
    led.execute(U, AddBeneficiaryAction(action_id="a_add", idempotency_key="k_add", display_name="X",
                                         iban=params.ATTACKER_IBAN, category="other"), None)
    led.execute(U, pay("a_univ", "b_univ", "1200.00"), None)
    rp = led.recent_payments(U, T0 - timedelta(days=1))
    assert len(rp) == 1
    assert rp[0].beneficiary_id == "b_univ" and rp[0].amount_eur == D("1200.00")
    assert rp[0].executed_at == T0


def test_recent_payments_excludes_failed():
    led = SandboxLedger()
    r = led.execute(U, pay("a_big", "b_univ", "99999.00"), None)
    assert r.status == "failed"
    assert led.recent_payments(U, T0 - timedelta(days=1)) == []


def test_recent_payments_replay_not_duplicated():
    led = SandboxLedger()
    a = pay("a1", "b_univ", "100.00")
    assert led.execute(U, a, None).status == "executed"
    assert led.execute(U, a, None).status == "replayed"
    assert len(led.recent_payments(U, T0 - timedelta(days=1))) == 1


def test_recent_payments_window_and_order():
    led = SandboxLedger()
    led.now = T0 - timedelta(hours=5)
    led.execute(U, pay("a1", "b_univ", "10.00"), None)
    led.now = T0 - timedelta(hours=1)
    led.execute(U, pay("a2", "b_landlord", "20.00"), None)
    led.now = T0
    led.execute(U, pay("a3", "b_univ", "30.00"), None)
    rp = led.recent_payments(U, T0 - timedelta(hours=2))
    assert [p.amount_eur for p in rp] == [D("20.00"), D("30.00")]
    assert [p.executed_at for p in rp] == [T0 - timedelta(hours=1), T0]
    assert len(led.recent_payments(U, T0 - timedelta(hours=1))) == 2   # inclusive bound
    assert len(led.recent_payments(U, T0 - timedelta(days=1))) == 3
    assert led.recent_payments(U, T0 + timedelta(seconds=1)) == []


def test_recent_payments_tenant_scoped():
    led = SandboxLedger()
    led.execute(U, pay("a1", "b_univ", "10.00"), None)
    other = PaymentAction(action_id="o1", idempotency_key="k_o1", from_account_id="acc_other_eur",
                          beneficiary_id="b_other", iban="FR7612345000019876543210987",
                          amount=D("50.00"), currency="EUR")
    assert led.execute("u_other", other, None).status == "executed"
    since = T0 - timedelta(days=1)
    assert [p.beneficiary_id for p in led.recent_payments(U, since)] == ["b_univ"]
    assert [p.beneficiary_id for p in led.recent_payments("u_other", since)] == ["b_other"]
    assert led.recent_payments("u_nobody", since) == []


# ------------------------------------------------------------------ anchors
def _append(st, i, summary=None):
    led = SandboxLedger()
    a = pay(f"a{i}", "b_univ", "10.00")
    res = ExecutionResult(action_id=a.action_id, idempotency_key=a.idempotency_key, status="executed")
    dec = PolicyDecision(action_id=a.action_id, risk_level=RiskLevel.R2, verdict=Verdict.CONFIRM,
                         rules=[], policy_version="v", explanation="e")
    conf = Confirmation(action_id=a.action_id, params_hash="h", method="tap", confirmed_at=T0)
    return st.append(action_id=a.action_id, intent={"user_id": U, "text": "hi"}, action=a,
                     simulation=led.simulate(U, [a])[0], decision=dec, confirmation=conf, result=res,
                     summary=summary or f"s{i}")


def store(n=3, **kw):
    st = SqliteReceiptStore(**kw)
    for i in range(n):
        _append(st, i)
    return st


def test_anchor_none_on_empty_chain():
    st = SqliteReceiptStore()
    assert st.anchor() is None
    v = st.verify_chain(None)
    assert v.valid and v.length == 0


def test_anchor_signature_and_head():
    st = store(3)
    an = st.anchor()
    head = st.list()[-1]
    assert an.seq == 3 and an.receipt_hash == head.receipt_hash and an.issued_at == T0
    expect = hmac.new(params.DEMO_RECEIPT_KEY.encode(), f"3:{head.receipt_hash}".encode(),
                      hashlib.sha256).hexdigest()
    assert an.signature == expect
    v = st.verify_chain(an)
    assert v.valid and v.length == 3


def test_key_from_env_and_explicit(monkeypatch):
    monkeypatch.setenv(params.RECEIPT_KEY_ENV, "env-key")
    st = store(1)
    an = st.anchor()
    assert an.signature == hmac.new(b"env-key", f"1:{an.receipt_hash}".encode(), hashlib.sha256).hexdigest()
    st2 = store(1, key="explicit")
    an2 = st2.anchor()
    assert an2.signature == hmac.new(b"explicit", f"1:{an2.receipt_hash}".encode(),
                                     hashlib.sha256).hexdigest()


def test_forged_signature_rejected():
    st = store(3)
    an = st.anchor()
    forged = an.model_copy(update={"signature": "0" * 64})
    v = st.verify_chain(forged)
    assert not v.valid and "signature" in v.detail
    # changing seq without re-signing is also rejected
    v = st.verify_chain(an.model_copy(update={"seq": 2}))
    assert not v.valid and "signature" in v.detail


def test_anchor_from_other_key_fails():
    a = store(3, key="key-a")
    b = store(3, key="key-b")
    assert a.list()[-1].receipt_hash == b.list()[-1].receipt_hash   # same content, deterministic
    v = b.verify_chain(a.anchor())
    assert not v.valid and "signature" in v.detail


def test_truncation_attack_detected_only_with_anchor():
    st = store(3)
    an = st.anchor()
    _raw(st, "DELETE FROM receipts WHERE seq >= 3")
    assert st.verify_chain().valid            # the chain alone looks fine
    v = st.verify_chain(an)
    assert not v.valid and "truncated" in v.detail and v.first_invalid_seq == 3
    st = store(4)
    an = st.anchor()
    _raw(st, "DELETE FROM receipts WHERE seq >= 2")
    v = st.verify_chain(an)
    assert not v.valid and "truncated" in v.detail


def test_full_rewrite_attack_detected():
    st = store(3)
    an = st.anchor()
    _raw(st, "DELETE FROM receipts")
    for i in range(3):
        _append(st, i, summary=f"fake {i}")
    assert st.verify_chain().valid            # internally consistent fake chain
    v = st.verify_chain(an)
    assert not v.valid and "rewritten" in v.detail and v.first_invalid_seq == 3
    # a longer fake chain does not help either
    _append(st, 9, summary="extra")
    assert not st.verify_chain(an).valid


def test_appending_after_anchor_still_verifies():
    st = store(2)
    an = st.anchor()
    _append(st, 5)
    _append(st, 6)
    v = st.verify_chain(an)
    assert v.valid and v.length == 4
    assert st.anchor().seq == 4


def test_anchor_does_not_mask_chain_errors():
    st = store(3)
    an = st.anchor()
    _raw(st, "UPDATE receipts SET summary='evil' WHERE seq=2")
    v = st.verify_chain(an)
    assert not v.valid and v.first_invalid_seq == 2
    assert v == st.verify_chain()
