"""Regressions for defects found by the simulated judge panel (docs/review/judge_panel.md)."""
from decimal import Decimal

import pytest

from bg.app import build_default
from bg.contracts import params


def _run(text):
    o = build_default()
    s = o.new_session(params.DEMO_USER_ID)
    return o, s, o.handle_message(s, text)


def _payments(turn):
    return sorted((v.action.beneficiary_id, v.action.amount) for v in turn.actions if v.action.action_type == "payment")


@pytest.mark.parametrize("text,extra", [
    ("pay the rent and send mom 600", ("b_mom", Decimal("600"))),
    ("send mom 600 and pay the rent", ("b_mom", Decimal("600"))),
    ("pay rent, then 50 to mom", ("b_mom", Decimal("50"))),
    ("pay tuition and send 100 euros to mom", ("b_mom", Decimal("100"))),
])
def test_bill_and_extra_payment_are_both_planned_and_funded(text, extra):
    o, s, turn = _run(text)
    pays = _payments(turn)
    assert extra in pays, f"extra payment dropped: {pays}"
    assert all(v.status == "awaiting_confirmation" for v in turn.actions), [v.status for v in turn.actions]
    for v in turn.actions:
        cr = v.confirmation_request
        o.confirm(s, v.action.action_id, cr.params_hash, params.DEMO_OTP if cr.verdict == "step_up" else None)
    eur = next(a for a in o.overview(s)["accounts"] if a["currency"] == "EUR")
    assert Decimal(str(eur["balance"])) >= 0


@pytest.mark.parametrize("text", [
    "Pay rent and send mom whatever is left above 600 euros",
    "pay the landlord the rest",
    "send mom the remainder after rent",
])
def test_relative_amounts_never_become_a_payment(text):
    _o, _s, turn = _run(text)
    assert not turn.actions, f"relative amount turned into actions: {_payments(turn)}"


@pytest.mark.parametrize("text,needle", [
    ("how much is 200 USD in EUR right now?", "183.08"),
    ("What is the EUR/USD rate today and how much would 1000 USD give me?", "915.42"),
    ("what's the exchange rate?", "1 USD = 0.92 EUR"),
])
def test_rate_questions_are_answered_read_only(text, needle):
    _o, _s, turn = _run(text)
    assert not turn.actions, "a rate question must not propose an action"
    assert needle in turn.assistant_text, turn.assistant_text
