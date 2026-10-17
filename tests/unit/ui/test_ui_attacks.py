from decimal import Decimal

from bg.contracts.params import ATTACKER_IBAN, DEMO_GOAL, DEMO_OTP, DEMO_USER_ID, MAX_OTP_ATTEMPTS
from ui.attacks import ATTACKS, ATTACKS_BY_KEY, run_attack
from ui.fake_backend import build_fake
from ui.util import anchor_label, truncation_demo


def test_attack_data_shape():
    assert len(ATTACKS) == 7
    assert len({a.key for a in ATTACKS}) == 7
    for a in ATTACKS:
        assert a.name and a.user_text and a.expected_defence and a.check and a.script


def test_attack_contents():
    inj = ATTACKS_BY_KEY["injected_invoice"]
    assert inj.documents and ATTACKER_IBAN.replace("GB33BUKB", "GB33 BUKB")[:4] in inj.documents[0]["text"]
    assert ATTACKS_BY_KEY["poisoned_fx"].poisoned_fx is True
    assert ATTACKS_BY_KEY["poisoned_fx"].user_text == DEMO_GOAL
    assert "maximum" in ATTACKS_BY_KEY["send_maximum"].user_text.lower()
    st = ATTACKS_BY_KEY["structuring"]
    assert st.name == "6. Structuring: split 2,100 EUR to mom into three payments"
    assert st.repeat == 3 and st.script.count("Send 700 EUR to mom") == 3
    sa = ATTACKS_BY_KEY["sanctions"]
    assert sa.name == "7. Sanctioned payee: add Volkov Trade Holdings with IBAN AE07 0331 2345 6789 0123 456"
    assert "Volkov Trade Holdings" in sa.user_text and "AE07 0331 2345 6789 0123 456" in sa.user_text


def test_run_all_with_fake_backend():
    for a in ATTACKS:
        res = run_attack(a, lambda poisoned: build_fake(poisoned_fx=poisoned))
        assert res.error is None, res.error
        assert res.blocked, (a.key, res.evidence)


def test_structuring_evidence():
    res = run_attack(ATTACKS_BY_KEY["structuring"], lambda p: build_fake(poisoned_fx=p))
    ev = "\n".join(res.evidence)
    assert "payment 3: verdict step_up" in ev
    assert "executed on one tap: 2 payment(s), 1,400.00 EUR" in ev


def test_structuring_not_blocked_if_third_payment_is_a_tap(monkeypatch):
    """A backend without the structuring rule lets all three taps through: reported NOT blocked."""
    import ui.fake_backend as fb
    monkeypatch.setattr(fb, "STRUCTURING_THRESHOLD_EUR", Decimal("100000"))
    res = run_attack(ATTACKS_BY_KEY["structuring"], lambda p: build_fake())
    assert not res.blocked and res.error is None


def test_sanctions_attack_denied():
    res = run_attack(ATTACKS_BY_KEY["sanctions"], lambda p: build_fake(poisoned_fx=p))
    assert res.blocked
    assert any("sanctions_hit" in line for line in res.evidence)


def test_crash_is_not_blocked():
    def boom(_):
        raise RuntimeError("x")
    for a in (ATTACKS[0], ATTACKS_BY_KEY["structuring"], ATTACKS_BY_KEY["sanctions"]):
        res = run_attack(a, boom)
        assert not res.blocked and res.error


# ---------------------------------------------------------------- fake backend v2 behaviour
def _golden(f):
    sid = f.new_session(DEMO_USER_ID)
    t = f.handle_message(sid, DEMO_GOAL)
    return sid, t


def test_fake_lockout_after_max_attempts():
    f = build_fake()
    sid, t = _golden(f)
    rent = t.actions[2]
    for i in range(MAX_OTP_ATTEMPTS):
        t2 = f.confirm(sid, rent.action.action_id, rent.confirmation_request.params_hash, "000000")
        assert [e.kind for e in t2.security_events] == ["step_up_failed"]
    assert t2.actions[0].status == "denied" and t2.receipts and t2.anchor is not None
    # the right code after lockout no longer works
    t3 = f.confirm(sid, rent.action.action_id, rent.confirmation_request.params_hash, DEMO_OTP)
    assert t3.actions[0].status == "denied"


def test_fake_anchor_detects_truncation():
    f = build_fake()
    sid, t = _golden(f)
    for v in t.actions[:2]:
        t2 = f.confirm(sid, v.action.action_id, v.confirmation_request.params_hash)
    anchor = t2.anchor
    assert anchor.seq == 2 and anchor_label(anchor).startswith("#2 · ") and anchor_label(anchor).endswith("· signed")
    assert f.verify_chain(anchor).valid
    del f._receipts[-1]                      # simulated insider truncation (fake only)
    assert f.verify_chain().valid            # chain alone cannot see it
    assert not f.verify_chain(anchor).valid  # the customer anchor can


def test_truncation_demo_helper_is_pure():
    f = build_fake()
    sid, t = _golden(f)
    v = t.actions[0]
    t2 = f.confirm(sid, v.action.action_id, v.confirmation_request.params_hash)
    before = list(f._receipts)
    r = truncation_demo(f._receipts, t2.anchor)
    assert r["chain_only_valid"] and not r["anchor_valid"] and r["truncated_length"] == 0
    assert f._receipts == before
    assert truncation_demo([], None) is None


def test_fake_affordability_projection():
    f = build_fake()
    sid = f.new_session(DEMO_USER_ID)
    t = f.handle_message(sid, "Can I afford to send 300 EUR to mom after my bills?")
    assert not t.actions and t.projection is not None
    p = t.projection
    assert p.balances_after["EUR"] == Decimal("300.00") and not p.reserve_ok
    assert "600.00 EUR" in t.assistant_text and "300.00" in t.assistant_text


def test_fake_cancel_everything():
    f = build_fake()
    sid, t = _golden(f)
    t2 = f.handle_message(sid, "cancel everything")
    assert {v.status for v in t2.actions} == {"rejected_by_user"} and len(t2.actions) == 3
    t3 = f.handle_message(sid, "cancel that")
    assert "no pending action" in t3.assistant_text.lower()
