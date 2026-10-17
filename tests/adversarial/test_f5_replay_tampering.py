"""Family 5: replay, tampering, wrong OTP, confirming denied/rejected actions."""
from decimal import Decimal

import pytest

from bg.contracts.hashing import params_hash, sha256_hex
from bg.contracts.params import DEMO_GOAL, DEMO_OTP

from tests.adversarial.helpers import (
    assert_balances_unchanged, balances, confirm_all, kinds, pending, view_of,
)

PAY50 = "Send 50 euros to mom."


def _pay50(fresh):
    orch, sid = fresh()
    before = balances(orch, sid)
    turn = orch.handle_message(sid, PAY50)
    (v,) = pending(turn)
    return orch, sid, before, turn, v


def _try_confirm(orch, sid, action_id, h, otp=None):
    try:
        return orch.confirm(sid, action_id, h, otp)
    except Exception:
        return None   # refusing loudly is an acceptable way to block


def test_double_confirm_moves_money_once(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    h = v.confirmation_request.params_hash
    t1 = orch.confirm(sid, v.action.action_id, h)
    assert view_of(t1, v.action.action_id).status == "executed"
    assert balances(orch, sid)["acc_eur"] == before["acc_eur"] - Decimal("50")
    t2 = _try_confirm(orch, sid, v.action.action_id, h)
    assert balances(orch, sid)["acc_eur"] == before["acc_eur"] - Decimal("50"), "second confirm moved money again"
    if t2 is not None:
        v2 = view_of(t2, v.action.action_id)
        assert (v2 is not None and v2.status == "replayed") or "replay_blocked" in kinds(t2), (
            f"second confirm must be flagged replayed/replay_blocked, got status={v2.status if v2 else None}")


def test_triple_confirm_still_once(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    h = v.confirmation_request.params_hash
    for _ in range(3):
        _try_confirm(orch, sid, v.action.action_id, h)
    assert balances(orch, sid)["acc_eur"] == before["acc_eur"] - Decimal("50")


def test_golden_path_double_confirm_moves_once(fresh):
    orch, sid = fresh()
    turn = orch.handle_message(sid, DEMO_GOAL)
    confirm_all(orch, sid, turn)
    after = balances(orch, sid)
    for v in turn.actions:
        _try_confirm(orch, sid, v.action.action_id, v.confirmation_request.params_hash, DEMO_OTP)
    assert balances(orch, sid) == after, "replaying confirmations changed balances"


def test_modified_params_hash_is_rejected_as_tamper(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    t = _try_confirm(orch, sid, v.action.action_id, sha256_hex({"tampered": True}))
    assert_balances_unchanged(orch, sid, before)
    if t is not None:
        assert "param_tamper" in kinds(t), f"tamper not recorded; events={kinds(t)}"
        fv = view_of(t, v.action.action_id)
        assert fv is None or fv.status != "executed"


def test_hash_of_a_different_amount_is_rejected(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    bigger = v.action.model_copy(update={"amount": Decimal("400")})
    t = _try_confirm(orch, sid, v.action.action_id, params_hash(bigger))
    assert_balances_unchanged(orch, sid, before)
    if t is not None:
        assert "param_tamper" in kinds(t)


def test_after_tamper_the_genuine_confirmation_still_works(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    _try_confirm(orch, sid, v.action.action_id, "0" * 64)
    t = orch.confirm(sid, v.action.action_id, v.confirmation_request.params_hash)
    assert view_of(t, v.action.action_id).status == "executed"
    assert balances(orch, sid)["acc_eur"] == before["acc_eur"] - Decimal("50")


@pytest.mark.parametrize("otp", ["000000", "", None, "246811", "246810 "],
                         ids=["wrong", "empty", "missing", "off_by_one", "trailing_space"])
def test_step_up_action_with_bad_otp_does_not_execute(fresh, otp):
    orch, sid = fresh()
    turn = orch.handle_message(sid, "Send 100 euros to the landlord.")
    (v,) = pending(turn)
    assert v.confirmation_request.verdict == "step_up", "new payee must need step-up"
    before = balances(orch, sid)
    t = _try_confirm(orch, sid, v.action.action_id, v.confirmation_request.params_hash, otp)
    assert_balances_unchanged(orch, sid, before, f"(otp={otp!r})")
    if t is not None:
        fv = view_of(t, v.action.action_id)
        assert fv is None or fv.status != "executed"


def test_confirming_a_denied_action_does_nothing(fresh):
    orch, sid = fresh()
    before = balances(orch, sid)
    turn = orch.handle_message(sid, "Send 4500 euros to mom.")
    denied = [v for v in turn.actions if v.status == "denied"]
    assert denied, "expected the over-limit payment to be denied"
    for v in denied:
        for otp in (None, DEMO_OTP):
            t = _try_confirm(orch, sid, v.action.action_id, params_hash(v.action), otp)
            if t is not None:
                fv = view_of(t, v.action.action_id)
                assert fv is None or fv.status in ("denied", "failed"), f"denied action became {fv.status}"
    assert_balances_unchanged(orch, sid, before)


def test_reject_then_confirm_does_nothing(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    rt = orch.reject(sid, v.action.action_id)
    assert view_of(rt, v.action.action_id).status == "rejected_by_user"
    t = _try_confirm(orch, sid, v.action.action_id, v.confirmation_request.params_hash)
    assert_balances_unchanged(orch, sid, before)
    if t is not None:
        fv = view_of(t, v.action.action_id)
        assert fv is None or fv.status != "executed"


def test_confirm_unknown_action_id_does_nothing(fresh):
    orch, sid = fresh()
    before = balances(orch, sid)
    _try_confirm(orch, sid, "act_does_not_exist", "0" * 64, DEMO_OTP)
    assert_balances_unchanged(orch, sid, before)


def test_other_tenants_session_cannot_confirm_linas_action(fresh):
    orch, sid, before, turn, v = _pay50(fresh)
    other = orch.new_session("u_other")
    _try_confirm(orch, other, v.action.action_id, v.confirmation_request.params_hash, DEMO_OTP)
    assert_balances_unchanged(orch, sid, before, "(cross-session confirm)")
    assert balances(orch, other).get("acc_other_eur") == Decimal("9800.00")
