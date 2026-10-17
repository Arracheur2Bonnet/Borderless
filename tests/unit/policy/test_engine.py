from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from bg.contracts import params
from bg.contracts.models import (
    Account, AddBeneficiaryAction, Beneficiary, FxAction, FxQuote, PaymentAction, PolicyContext,
    RiskLevel, SimulationResult, Verdict,
)
from bg.policy import DeterministicPolicyEngine

NOW = params.DEMO_NOW
U = params.DEMO_USER_ID
ENGINE = DeterministicPolicyEngine()


def bens(**over):
    out = []
    for b in params.SEED_BENEFICIARIES:
        d = dict(b)
        d.update(over.get(d["beneficiary_id"], {}))
        out.append(Beneficiary(**d))
    return out


def accounts():
    return [Account(**a) for a in params.SEED_ACCOUNTS]


def ctx(**kw):
    base = dict(user_id=U, now=NOW, accounts=accounts(), beneficiaries=bens(), executed_today_eur=D("0"),
                reserve_floor_eur=params.DEMO_RESERVE_EUR, step_up_verified=False)
    base.update(kw)
    return PolicyContext(**base)


def sim(aid="a1", after=None, feasible=True, errors=()):
    return SimulationResult(action_id=aid, feasible=feasible,
                            balances_before={"acc_eur": D("450")},
                            balances_after=after if after is not None else {"acc_eur": D("5000"), "acc_usd": D("100")},
                            errors=list(errors))


def pay(amount="1200", ben="b_univ", iban=params.UNIV_IBAN, cur="EUR", acc="acc_eur", **kw):
    return PaymentAction(action_id="a1", idempotency_key="k1", from_account_id=acc, beneficiary_id=ben,
                         iban=iban, amount=D(amount), currency=cur, **kw)


def fx(buy="2200", cur="EUR"):
    q = FxQuote(quote_id="q1", sell_currency="USD", buy_currency=cur, sell_amount=D("2400"), buy_amount=D(buy),
                rate=D("0.92"), fee=D("10"), expires_at=NOW + timedelta(minutes=5))
    return FxAction(action_id="a1", idempotency_key="k1", from_account_id="acc_usd", to_account_id="acc_eur", quote=q)


def rule(dec, rid):
    return next(r for r in dec.rules if r.rule_id == rid)


def ev(c, a, s=None):
    return ENGINE.evaluate(c, a, s or sim())


# ------------------------------------------------------------------ golden path
def test_golden_fx_confirm():
    d = ev(ctx(), fx("2200"), sim(after={"acc_eur": D("600"), "acc_usd": D("100")}))
    assert (d.verdict, d.risk_level) == (Verdict.CONFIRM, RiskLevel.R2)
    assert d.policy_version == params.POLICY_VERSION
    assert d.action_id == "a1"


def test_golden_tuition_confirm():
    d = ev(ctx(), pay("1200"))
    assert (d.verdict, d.risk_level) == (Verdict.CONFIRM, RiskLevel.R2)
    assert all(r.passed for r in d.rules)


def test_golden_rent_step_up_new_payee():
    d = ev(ctx(), pay("850", "b_landlord", params.LANDLORD_IBAN))
    assert (d.verdict, d.risk_level) == (Verdict.STEP_UP, RiskLevel.R3)
    assert [r.rule_id for r in d.rules if not r.passed] == ["BENEFICIARY_AGE"]
    assert "20.00h" in rule(d, "BENEFICIARY_AGE").detail


def test_step_up_verified_keeps_step_up_verdict():
    d = ev(ctx(step_up_verified=True), pay("850", "b_landlord", params.LANDLORD_IBAN))
    assert d.verdict is Verdict.STEP_UP and "verified" in d.explanation


# ------------------------------------------------------------------ rule pass / fail
RULES_PASS = ["SIM_FEASIBLE", "BENEFICIARY_REGISTERED", "BENEFICIARY_NOT_BLOCKED", "IBAN_MATCHES_REGISTRY",
              "TENANT_ISOLATION", "RESERVE_FLOOR", "MAX_SINGLE_PAYMENT", "DAILY_LIMIT", "UNTRUSTED_ORIGIN",
              "SANCTIONS_SCREEN", "BENEFICIARY_AGE", "AMOUNT_TIER", "STRUCTURING"]


def test_all_payment_rules_present_and_pass():
    d = ev(ctx(), pay("100"))
    assert [r.rule_id for r in d.rules] == RULES_PASS
    assert all(r.passed for r in d.rules)


def test_sim_infeasible_denies():
    d = ev(ctx(), pay(), sim(feasible=False, errors=["insufficient funds"]))
    assert d.verdict is Verdict.DENY and not rule(d, "SIM_FEASIBLE").passed
    assert "insufficient funds" in d.explanation


def test_sim_for_other_action_denies():
    d = ev(ctx(), pay(), sim(aid="zzz"))
    assert d.verdict is Verdict.DENY and not rule(d, "SIM_FEASIBLE").passed


def test_unknown_beneficiary_denies():
    d = ev(ctx(), pay(ben="b_ghost"))
    assert d.verdict is Verdict.DENY
    assert not rule(d, "BENEFICIARY_REGISTERED").passed
    assert not rule(d, "IBAN_MATCHES_REGISTRY").passed


def test_not_allowlisted_denies():
    d = ev(ctx(beneficiaries=bens(b_univ={"allowlisted": False})), pay())
    assert d.verdict is Verdict.DENY and "not allowlisted" in rule(d, "BENEFICIARY_REGISTERED").detail


def test_blocked_beneficiary_denies():
    d = ev(ctx(beneficiaries=bens(b_univ={"blocked": True})), pay())
    assert d.verdict is Verdict.DENY and not rule(d, "BENEFICIARY_NOT_BLOCKED").passed


def test_iban_mismatch_denies():
    d = ev(ctx(), pay("850", "b_landlord", params.ATTACKER_IBAN))
    assert d.verdict is Verdict.DENY and d.risk_level is RiskLevel.R4
    assert not rule(d, "IBAN_MATCHES_REGISTRY").passed


def test_iban_comparison_normalises_spaces_and_case():
    spaced = "fr76 1010 7001 0112 3456 7890 129"
    d = ev(ctx(), pay(iban=spaced))
    assert rule(d, "IBAN_MATCHES_REGISTRY").passed and d.verdict is Verdict.CONFIRM


def test_tenant_isolation_foreign_account():
    d = ev(ctx(), pay(acc="acc_other_eur"))
    assert d.verdict is Verdict.DENY and "another user" in rule(d, "TENANT_ISOLATION").detail


def test_tenant_isolation_foreign_beneficiary():
    d = ev(ctx(), pay(ben="b_other", iban="FR7612345000019876543210987"))
    assert d.verdict is Verdict.DENY and not rule(d, "TENANT_ISOLATION").passed


def test_tenant_isolation_unknown_account():
    d = ev(ctx(), pay(acc="acc_nope"))
    assert d.verdict is Verdict.DENY and not rule(d, "TENANT_ISOLATION").passed


def test_untrusted_origin_denies():
    d = ev(ctx(), pay(derived_from_untrusted=True))
    assert d.verdict is Verdict.DENY and not rule(d, "UNTRUSTED_ORIGIN").passed


# ------------------------------------------------------------------ boundaries
@pytest.mark.parametrize("amount,verdict", [
    ("1500.00", Verdict.CONFIRM), ("1500.01", Verdict.STEP_UP), ("1499.99", Verdict.CONFIRM)])
def test_amount_tier_boundary(amount, verdict):
    d = ev(ctx(), pay(amount))
    assert d.verdict is verdict
    assert rule(d, "AMOUNT_TIER").passed == (verdict is Verdict.CONFIRM)


@pytest.mark.parametrize("age,verdict", [
    (timedelta(hours=72), Verdict.CONFIRM),
    (timedelta(hours=71, minutes=59), Verdict.STEP_UP),
    (timedelta(hours=72, seconds=1), Verdict.CONFIRM),
    (timedelta(hours=0), Verdict.STEP_UP)])
def test_beneficiary_age_boundary(age, verdict):
    c = ctx(beneficiaries=bens(b_univ={"added_at": NOW - age}))
    assert ev(c, pay()).verdict is verdict


def test_future_added_at_is_new_payee():
    c = ctx(beneficiaries=bens(b_univ={"added_at": NOW + timedelta(hours=1)}))
    assert ev(c, pay()).verdict is Verdict.STEP_UP


@pytest.mark.parametrize("amount,verdict", [("4000.00", Verdict.STEP_UP), ("4000.01", Verdict.DENY)])
def test_max_single_payment_boundary(amount, verdict):
    d = ev(ctx(reserve_floor_eur=None), pay(amount))
    assert d.verdict is verdict


@pytest.mark.parametrize("executed,verdict", [("3800", Verdict.CONFIRM), ("3800.01", Verdict.DENY)])
def test_daily_limit_boundary(executed, verdict):
    d = ev(ctx(executed_today_eur=D(executed)), pay("1200"))
    assert d.verdict is verdict
    assert rule(d, "DAILY_LIMIT").passed == (verdict is Verdict.CONFIRM)


def test_daily_limit_detail_is_precise():
    d = ev(ctx(executed_today_eur=D("4500")), pay("1200"))
    assert "4500.00 + this payment 1200.00 = 5700.00" in rule(d, "DAILY_LIMIT").detail


def test_usd_payment_converted_to_eur():
    # 1600 USD = 1472 EUR -> still one-tap; 1700 USD = 1564 EUR -> step-up
    assert ev(ctx(), pay("1600", cur="USD", acc="acc_usd")).verdict is Verdict.CONFIRM
    assert ev(ctx(), pay("1700", cur="USD", acc="acc_usd")).verdict is Verdict.STEP_UP


def test_nonpositive_payment_denied():
    for amt in ("0", "-5"):
        assert ev(ctx(), pay(amt)).verdict is Verdict.DENY


# ------------------------------------------------------------------ reserve floor
def test_reserve_floor_denial_detail():
    # 450 + 2200 FX - 1200 - 850 - 400 = 200 < 600
    d = ev(ctx(), pay("400"), sim(after={"acc_eur": D("380")}))
    r = rule(d, "RESERVE_FLOOR")
    assert d.verdict is Verdict.DENY and not r.passed
    assert r.detail == "EUR balance after all actions would be 380.00, below the 600.00 reserve floor"


def test_reserve_floor_exact_boundary():
    assert ev(ctx(), pay(), sim(after={"acc_eur": D("600.00")})).verdict is Verdict.CONFIRM
    assert ev(ctx(), pay(), sim(after={"acc_eur": D("599.99")})).verdict is Verdict.DENY


def test_reserve_floor_skipped_when_none():
    d = ev(ctx(reserve_floor_eur=None), pay(), sim(after={"acc_eur": D("1")}))
    assert "RESERVE_FLOOR" not in [r.rule_id for r in d.rules] and d.verdict is Verdict.CONFIRM


def test_reserve_floor_applies_to_fx_eur_to_usd():
    d = ev(ctx(), fx("100"), sim(after={"acc_eur": D("300")}))
    assert d.verdict is Verdict.DENY


def test_reserve_floor_falls_back_to_balances_before():
    s = SimulationResult(action_id="a1", feasible=True, balances_before={"acc_eur": D("700")}, balances_after={})
    assert ev(ctx(), pay(), s).verdict is Verdict.CONFIRM


def test_reserve_floor_missing_balance_fails_closed():
    s = SimulationResult(action_id="a1", feasible=True, balances_before={}, balances_after={})
    d = ev(ctx(), pay(), s)
    assert d.verdict is Verdict.DENY and not rule(d, "RESERVE_FLOOR").passed


def test_reserve_floor_ignores_other_users_eur_account():
    d = ev(ctx(), pay(), sim(after={"acc_eur": D("100"), "acc_other_eur": D("9800")}))
    assert d.verdict is Verdict.DENY


# ------------------------------------------------------------------ fx
@pytest.mark.parametrize("buy,verdict", [("2500.00", Verdict.CONFIRM), ("2500.01", Verdict.STEP_UP)])
def test_fx_tier_boundary(buy, verdict):
    assert ev(ctx(), fx(buy)).verdict is verdict


def test_fx_rules_listed():
    d = ev(ctx(), fx())
    assert [r.rule_id for r in d.rules] == ["SIM_FEASIBLE", "TENANT_ISOLATION", "RESERVE_FLOOR", "FX_TIER"]


def test_fx_foreign_account_denied():
    a = fx().model_copy(update={"to_account_id": "acc_other_eur"})
    assert ev(ctx(), a).verdict is Verdict.DENY


# ------------------------------------------------------------------ add beneficiary
def test_add_beneficiary_is_step_up():
    a = AddBeneficiaryAction(action_id="a1", idempotency_key="k", display_name="Bob",
                             iban="DE89370400440532013000", category="other")
    d = ev(ctx(), a)
    assert (d.verdict, d.risk_level) == (Verdict.STEP_UP, RiskLevel.R3)
    assert not rule(d, "NEW_PAYEE_ACTION").passed


def test_add_beneficiary_infeasible_denied():
    a = AddBeneficiaryAction(action_id="a1", idempotency_key="k", display_name="Bob",
                             iban="DE89370400440532013000", category="other")
    assert ev(ctx(), a, sim(feasible=False)).verdict is Verdict.DENY


# ------------------------------------------------------------------ fail closed / determinism
def test_fail_closed_on_naive_datetime():
    naive = datetime(2026, 1, 1)
    c = ctx(beneficiaries=bens(b_univ={"added_at": naive}))
    d = ev(c, pay())
    assert d.verdict is Verdict.DENY and d.rules[0].rule_id == "POLICY_ERROR"


def test_fail_closed_on_unsupported_currency_rate():
    class Fake:
        action_id = "a1"
    d = ENGINE.evaluate(ctx(), Fake(), sim())  # type: ignore[arg-type]
    assert d.verdict is Verdict.DENY and d.risk_level is RiskLevel.R4


def test_fail_closed_on_bad_context():
    d = ENGINE.evaluate(None, pay(), sim())  # type: ignore[arg-type]
    assert d.verdict is Verdict.DENY


def test_deterministic_same_input_same_output():
    assert ev(ctx(), pay("850", "b_landlord", params.LANDLORD_IBAN)) == ev(ctx(), pay("850", "b_landlord", params.LANDLORD_IBAN))


def test_r4_dominates_r3():
    d = ev(ctx(), pay("2000", "b_landlord", params.ATTACKER_IBAN))
    assert d.verdict is Verdict.DENY and d.explanation.startswith("Denied:")
    assert not rule(d, "AMOUNT_TIER").passed and not rule(d, "BENEFICIARY_AGE").passed


def test_explanation_is_single_sentence_for_confirm():
    d = ev(ctx(), pay())
    assert d.explanation == "Confirmation required (R2): all 13 policy checks passed."
