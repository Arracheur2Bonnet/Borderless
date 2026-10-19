"""v2 compliance rules: STRUCTURING (R3) and SANCTIONS_SCREEN (R4)."""
from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest

from bg.contracts import params
from bg.contracts.models import (
    Account, AddBeneficiaryAction, Beneficiary, FxAction, FxQuote, PaymentAction, PolicyContext,
    RecentPayment, RiskLevel, SimulationResult, Verdict,
)
from bg.policy import DeterministicPolicyEngine

NOW = params.DEMO_NOW
U = params.DEMO_USER_ID
ENGINE = DeterministicPolicyEngine()
SANCTIONED_IBAN = params.SANCTIONS_WATCHLIST_IBANS[0]


def bens(**over):
    out = []
    for b in params.SEED_BENEFICIARIES:
        d = dict(b)
        d.update(over.get(d["beneficiary_id"], {}))
        out.append(Beneficiary(**d))
    return out


def ctx(**kw):
    base = dict(user_id=U, now=NOW, accounts=[Account(**a) for a in params.SEED_ACCOUNTS], beneficiaries=bens(),
                executed_today_eur=D("0"), reserve_floor_eur=params.DEMO_RESERVE_EUR, step_up_verified=False)
    base.update(kw)
    return PolicyContext(**base)


def sim(aid="a1"):
    return SimulationResult(action_id=aid, feasible=True, balances_before={"acc_eur": D("450")},
                            balances_after={"acc_eur": D("5000"), "acc_usd": D("100")}, errors=[])


def pay(amount, ben="b_mom", iban=params.MOM_IBAN, cur="EUR"):
    return PaymentAction(action_id="a1", idempotency_key="k1", from_account_id="acc_eur", beneficiary_id=ben,
                         iban=iban, amount=D(amount), currency=cur)


def recent(amount, ben="b_mom", hours_ago=1.0):
    return RecentPayment(beneficiary_id=ben, amount_eur=D(amount), executed_at=NOW - timedelta(hours=hours_ago))


def add_ben(name, iban="DE89370400440532013000"):
    return AddBeneficiaryAction(action_id="a1", idempotency_key="k", display_name=name, iban=iban, category="other")


def rule(dec, rid):
    return next(r for r in dec.rules if r.rule_id == rid)


def ev(c, a):
    return ENGINE.evaluate(c, a, sim())


# ================================================================== STRUCTURING (R3)
def test_structuring_detail_names_count_total_and_threshold():
    c = ctx(recent_payments=[recent("700"), recent("700", hours_ago=5)])
    d = ev(c, pay("730"))
    r = rule(d, "STRUCTURING")
    assert not r.passed and r.severity is RiskLevel.R3
    assert r.detail == ("3 payments to Samira Haddad in the last 24h would total 2130.00 EUR, "
                        "above the 1500.00 structuring threshold")
    assert (d.verdict, d.risk_level) == (Verdict.STEP_UP, RiskLevel.R3)
    assert r.detail in d.explanation


def test_structuring_exactly_at_threshold_passes():
    d = ev(ctx(recent_payments=[recent("700"), recent("400")]), pay("400"))
    r = rule(d, "STRUCTURING")
    assert r.passed and "1500.00 EUR, within the 1500.00" in r.detail
    assert d.verdict is Verdict.CONFIRM


def test_structuring_one_cent_above_threshold_steps_up():
    d = ev(ctx(recent_payments=[recent("700"), recent("400")]), pay("400.01"))
    assert not rule(d, "STRUCTURING").passed
    assert d.verdict is Verdict.STEP_UP


def test_structuring_single_payment_passes_with_singular_detail():
    r = rule(ev(ctx(), pay("100")), "STRUCTURING")
    assert r.passed and r.detail.startswith("1 payment to Samira Haddad in the last 24h would total 100.00 EUR")


def test_structuring_different_payees_not_aggregated():
    c = ctx(recent_payments=[recent("900", ben="b_univ"), recent("900", ben="b_landlord")])
    d = ev(c, pay("900"))
    assert rule(d, "STRUCTURING").passed and d.verdict is Verdict.CONFIRM


def test_structuring_payments_outside_window_ignored():
    c = ctx(recent_payments=[recent("1000", hours_ago=24.01), recent("1000", hours_ago=48)])
    d = ev(c, pay("900"))
    assert rule(d, "STRUCTURING").passed and d.verdict is Verdict.CONFIRM


def test_structuring_window_boundary_is_inclusive():
    c = ctx(recent_payments=[recent("1000", hours_ago=24)])   # executed exactly now - 24h -> counted
    assert not rule(ev(c, pay("900")), "STRUCTURING").passed


def test_structuring_not_evaluated_for_fx():
    # recent payments are payments only; an FX buy never carries a STRUCTURING rule
    q = FxQuote(quote_id="q1", sell_currency="USD", buy_currency="EUR", sell_amount=D("2403.26"),
                buy_amount=D("2200"), rate=D("0.92"), fee=D("11.96"), expires_at=NOW + timedelta(minutes=5))
    a = FxAction(action_id="a1", idempotency_key="k1", from_account_id="acc_usd", to_account_id="acc_eur", quote=q)
    d = ev(ctx(recent_payments=[recent("1400", ben="b_mom")]), a)
    assert "STRUCTURING" not in [r.rule_id for r in d.rules] and d.verdict is Verdict.CONFIRM


def test_structuring_uses_eur_equivalent_of_this_payment():
    # 900 USD = 828 EUR; 700 + 828 = 1528 > 1500
    d = ev(ctx(recent_payments=[recent("700")]), pay("900", cur="USD"))
    r = rule(d, "STRUCTURING")
    assert not r.passed and "1528.00 EUR" in r.detail


def test_payment_alone_above_r2_is_amount_tier_not_structuring_and_not_r4():
    d = ev(ctx(recent_payments=[recent("1400"), recent("1400")]), pay("1600"))
    assert not rule(d, "AMOUNT_TIER").passed
    s = rule(d, "STRUCTURING")
    assert s.passed and "AMOUNT_TIER" in s.detail
    assert (d.verdict, d.risk_level) == (Verdict.STEP_UP, RiskLevel.R3)


def test_structuring_never_escalates_beyond_step_up():
    d = ev(ctx(recent_payments=[recent("1400")] * 2), pay("1400"))
    assert (d.verdict, d.risk_level) == (Verdict.STEP_UP, RiskLevel.R3)


def test_structuring_negative_recent_amount_fails_closed():
    d = ev(ctx(recent_payments=[recent("-5000")]), pay("900"))
    assert d.verdict is Verdict.DENY and d.rules[0].rule_id == "POLICY_ERROR"


def test_structuring_naive_recent_datetime_fails_closed():
    bad = RecentPayment(beneficiary_id="b_mom", amount_eur=D("10"), executed_at=datetime(2026, 10, 28, 8))
    d = ev(ctx(recent_payments=[bad]), pay("900"))
    assert d.verdict is Verdict.DENY


# ================================================================== SANCTIONS_SCREEN (R4)
@pytest.mark.parametrize("name", [
    "Volkov Trade Holdings",
    "VOLKOV TRADE HOLDINGS",
    "  volkov   trade\tholdings ",
    "Volkov-Trade Holdings",          # punctuation normalised to a space
    "Volkov Trade",                   # partial: name inside the watchlist entry
    "Volkov Trade Holdings Ltd",      # watchlist entry inside the name
    "northgate exchange fze",
])
def test_add_beneficiary_sanctioned_name_variants_denied(name):
    d = ev(ctx(), add_ben(name))
    r = rule(d, "SANCTIONS_SCREEN")
    assert not r.passed and r.severity is RiskLevel.R4 and "matches sanctions watchlist entry" in r.detail
    assert (d.verdict, d.risk_level) == (Verdict.DENY, RiskLevel.R4)


@pytest.mark.parametrize("iban", [
    SANCTIONED_IBAN,
    "ae07 0331 2345 6789 0123 456",
    " AE07 0331 2345 6789 0123 456 ",
])
def test_add_beneficiary_sanctioned_iban_variants_denied(iban):
    d = ev(ctx(), add_ben("Harmless Name", iban))
    r = rule(d, "SANCTIONS_SCREEN")
    assert not r.passed and r.detail == "new payee IBAN ending 3456 is on the sanctions watchlist"
    assert d.verdict is Verdict.DENY


def test_sanctions_name_and_iban_both_reported():
    r = rule(ev(ctx(), add_ben("Volkov Trade Holdings", SANCTIONED_IBAN)), "SANCTIONS_SCREEN")
    assert "'Volkov Trade Holdings'" in r.detail and "IBAN ending 3456" in r.detail


@pytest.mark.parametrize("name", [
    "Samira Haddad", "Université Paris-Saclay", "Jean Dupont", "Bob",
    # Design choice (documented false-positive trade-off): matching is on WHOLE WORDS and the
    # "name inside entry" direction needs at least two words. So "Northgate University" (shares
    # only the word "Northgate" with "Northgate Exchange FZE") is NOT flagged, nor are lone common
    # words like "Trade" or "Northgate". A two-word fragment of an entry ("Volkov Trade",
    # "Exchange FZE") IS flagged: we accept that false positive rather than miss a truncated name.
    "Northgate University", "Northgate", "Trade", "Holdings", "Volkovsky Trading",
])
def test_clean_names_not_flagged(name):
    d = ev(ctx(beneficiaries=[]), add_ben(name))     # empty registry: no duplicate-name refusal
    assert rule(d, "SANCTIONS_SCREEN").passed
    assert d.verdict is Verdict.STEP_UP   # still a new payee -> R3


@pytest.mark.parametrize("name", ["Samira Haddad", "SAMIRA  haddad", "Sаmira Haddad", "mom", "Landlord", "b_univ"])
def test_new_payee_name_colliding_with_existing_is_denied(name):
    """AUDIT-5: same name (case, spacing, look-alike letters), an alias or an id of an existing payee."""
    d = ev(ctx(), add_ben(name))
    assert d.verdict is Verdict.DENY and not rule(d, "PAYEE_NAME_UNIQUE").passed


@pytest.mark.parametrize("name", [
    "Vօlkov Trade Holdings", "Ꮩolkov Trade Holdings", "Volᴋov Trade Holdings", "Nօrthgate Exchange FZE",
    "V0lkov Trade Holdings", "Vołkov Trade Holdings",
])
def test_lookalike_sanctioned_names_hit(name):
    """AUDIT-4: letters outside the Latin map are wildcards; digits used as letters are folded."""
    r = rule(ev(ctx(beneficiaries=[]), add_ben(name)), "SANCTIONS_SCREEN")
    assert not r.passed and "matches sanctions watchlist" in r.detail


@pytest.mark.parametrize("name", ["Ахмед Иванов", "王伟", "محمد علي", "Bob 李"])
def test_unscreenable_letters_fail_closed(name):
    """AUDIT-4 trade-off: a name the Latin watchlist cannot be compared with goes to manual review (deny)."""
    d = ev(ctx(beneficiaries=[]), add_ben(name))
    r = rule(d, "SANCTIONS_SCREEN")
    assert not r.passed and "manual sanctions review" in r.detail and d.verdict is Verdict.DENY


@pytest.mark.parametrize("iban", ["GB00TEST", "FR7612345000019876543210987", "AE٠٧0331234567890123456",
                                  "GB33BUKB2020155555555"])
def test_invalid_iban_new_payee_denied(iban):
    """AUDIT-2: an IBAN failing format, length or mod-97 is a hard deny, never step-up."""
    d = ev(ctx(beneficiaries=[]), add_ben("Bob", iban))
    assert d.verdict is Verdict.DENY and not rule(d, "IBAN_VALID").passed


@pytest.mark.parametrize("iban", ["AE07​0331234567890123456", "AE07-0331-2345-6789-0123-456",
                                  "ＡＥ０７0331234567890123456", "AE٠٧0331234567890123456"])
def test_non_canonical_sanctioned_iban_still_screened(iban):
    r = rule(ev(ctx(beneficiaries=[]), add_ben("Harmless Name", iban)), "SANCTIONS_SCREEN")
    assert not r.passed and "on the sanctions watchlist" in r.detail


def test_partial_two_word_fragment_is_flagged():
    assert not rule(ev(ctx(), add_ben("Exchange FZE")), "SANCTIONS_SCREEN").passed


def test_empty_name_fails_closed():
    d = ev(ctx(), add_ben("  - "))
    assert d.verdict is Verdict.DENY and "name is missing or empty" in rule(d, "SANCTIONS_SCREEN").detail


def test_add_beneficiary_rule_order():
    d = ev(ctx(), add_ben("Bob"))
    assert [r.rule_id for r in d.rules] == ["SIM_FEASIBLE", "IBAN_VALID", "PAYEE_NAME_UNIQUE", "SANCTIONS_SCREEN",
                                            "PAYEE_VERIFICATION", "NEW_PAYEE_ACTION"]


def test_payment_to_registered_sanctioned_name_denied():
    c = ctx(beneficiaries=bens(b_univ={"display_name": "Volkov Trade Holdings"}))
    d = ev(c, pay("100", "b_univ", params.UNIV_IBAN))
    assert d.verdict is Verdict.DENY and not rule(d, "SANCTIONS_SCREEN").passed
    assert "payee name 'Volkov Trade Holdings'" in d.explanation


def test_payment_to_registered_sanctioned_iban_denied():
    c = ctx(beneficiaries=bens(b_univ={"iban": SANCTIONED_IBAN}))
    d = ev(c, pay("100", "b_univ", "ae070331234567890123456"))
    r = rule(d, "SANCTIONS_SCREEN")
    assert not r.passed and r.detail == "payee IBAN ending 3456 is on the sanctions watchlist"
    assert d.verdict is Verdict.DENY


def test_payment_iban_differing_from_registry_is_also_screened():
    d = ev(ctx(), pay("100", "b_mom", SANCTIONED_IBAN))
    assert not rule(d, "SANCTIONS_SCREEN").passed
    assert not rule(d, "IBAN_MATCHES_REGISTRY").passed and d.verdict is Verdict.DENY


def test_sanctions_dominates_step_up_even_when_verified():
    c = ctx(step_up_verified=True, beneficiaries=bens(b_landlord={"display_name": "Northgate Exchange FZE"}))
    d = ev(c, pay("850", "b_landlord", params.LANDLORD_IBAN))
    assert (d.verdict, d.risk_level) == (Verdict.DENY, RiskLevel.R4)


def test_payment_unknown_beneficiary_sanctions_fails_closed():
    d = ev(ctx(), pay("100", "b_ghost"))
    assert not rule(d, "SANCTIONS_SCREEN").passed and d.verdict is Verdict.DENY


def test_clean_payment_sanctions_pass_detail():
    r = rule(ev(ctx(), pay("100")), "SANCTIONS_SCREEN")
    assert r.passed and r.detail == ("Samira Haddad (IBAN ending 4921) screened against 2 watchlist names "
                                     "and 1 IBANs: no match")
