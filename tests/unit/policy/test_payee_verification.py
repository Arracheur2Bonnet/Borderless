"""Verification of Payee (v8): the name matcher and the PAYEE_VERIFICATION rule. Modelled on the EU instant-payments
Verification of Payee and the UK Confirmation of Payee (no legal claim)."""
from datetime import timedelta
from decimal import Decimal as D

import pytest

from bg.contracts import params
from bg.contracts.canonical import VOP_CLOSE, VOP_MATCH, VOP_NO_MATCH, payee_name_match, vop_tokens
from bg.contracts.models import (
    Account, AddBeneficiaryAction, Beneficiary, FxAction, PaymentAction, PolicyContext, RiskLevel, SimulationResult,
    Verdict,
)
from bg.policy import DeterministicPolicyEngine
from tests.unit.policy.test_engine import fx_quote

ENGINE = DeterministicPolicyEngine()
NOW = params.DEMO_NOW
U = params.DEMO_USER_ID
CYRILLIC_E = "е"


# ------------------------------------------------------------------ matcher
@pytest.mark.parametrize("typed,holder", [
    ("Jean Dupont", "Jean Dupont"),
    ("jean dupont", "JEAN DUPONT"),                          # case
    ("Jéan Dupônt", "Jean Dupont"),                # accents
    ("Universite Paris-Saclay", "Université Paris-Saclay"),
    ("  Jean   Dupont ", "Jean Dupont"),                      # whitespace
    ("Jean-Dupont", "Jean Dupont"),                           # punctuation
    ("Mme Samira Haddad", "Samira Haddad"),                   # title ignored
    ("Quickpay Holdings", "Quickpay Holdings Ltd"),           # legal form ignored
    ("ＪＥＡＮ Dupont", "Jean Dupont"),       # fullwidth letters (NFKC)
    ("Straße Müller", "Strasse Muller"),            # casefold ss, umlaut
])
def test_match(typed, holder):
    assert payee_name_match(typed, holder) == VOP_MATCH


@pytest.mark.parametrize("typed,holder", [
    ("Jean Dupond", "Jean Dupont"),                           # one-letter typo
    ("Jean Dupnt", "Jean Dupont"),                            # missing letter
    ("Dupont Jean", "Jean Dupont"),                           # word order
    ("Jean Dupont", "Jean Pierre Dupont"),                    # missing middle name
    ("Jean Pierre Dupont", "Jean Dupont"),                    # extra middle name
    ("J. Dupont", "Jean Dupont"),                             # initial
    ("Samira Hadad", "Samira Haddad"),
    ("J" + CYRILLIC_E + "an Dupont", "Jean Dupont"),          # look-alike letter is never an exact match
    ("Residence Service", "Residence Services Ltd"),
])
def test_close_match(typed, holder):
    assert payee_name_match(typed, holder) == VOP_CLOSE


@pytest.mark.parametrize("typed,holder", [
    ("Jean Dupont", "Quickpay Holdings Ltd"),
    ("Dupont", "Jean Dupont"),                                # one word is not enough
    ("J D", "Jean Dupont"),                                   # initials only
    ("Li Wu", "Li Xu"),                                       # short names: 1 letter is more than 15 percent
    ("Jean Dupont", "Jean Duval"),                            # distance 3
    ("Marie Lambert", "Marie Lamberton Holdings"),
    ("", "Jean Dupont"),
    ("Jean Dupont", ""),
    (None, "Jean Dupont"),
    ("x" * 201, "x" * 201),                                   # over the length cap: not compared
])
def test_no_match(typed, holder):
    assert payee_name_match(typed, holder) == VOP_NO_MATCH


def test_thresholds_are_the_documented_ones():
    assert params.VOP_MAX_EDIT_DISTANCE == 2 and params.VOP_MIN_SIMILARITY_PCT == 85
    # distance 2 on a long name is close (2/18 = 11 percent), distance 3 never is
    assert payee_name_match("Alexandre Fontaine", "Alexandre Fontenne") == VOP_CLOSE
    assert payee_name_match("Jean Dupont", "Jean Duvalt") == VOP_NO_MATCH


def test_tokens_drop_titles_and_legal_forms_but_never_everything():
    assert vop_tokens("Dr. Élise O'Neil, SARL") == ["elise", "o", "neil"]
    assert vop_tokens("Ltd") == ["ltd"]


# ------------------------------------------------------------------ rule
def _bens():
    return [Beneficiary(**b) for b in params.SEED_BENEFICIARIES]


def _ctx(**kw):
    base = dict(user_id=U, now=NOW, accounts=[Account(**a) for a in params.SEED_ACCOUNTS], beneficiaries=_bens(),
                executed_today_eur=D("0"), reserve_floor_eur=params.DEMO_RESERVE_EUR, step_up_verified=False,
                payee_holders=dict(params.ACCOUNT_HOLDERS))
    base.update(kw)
    return PolicyContext(**base)


def _sim(aid="a1"):
    return SimulationResult(action_id=aid, feasible=True, balances_before={"acc_eur": D("450")},
                            balances_after={"acc_eur": D("5000"), "acc_usd": D("100")}, errors=[])


def _add(name, iban):
    return AddBeneficiaryAction(action_id="a1", idempotency_key="k", display_name=name, iban=iban, category="other")


def _vop(d):
    return next(r for r in d.rules if r.rule_id == "PAYEE_VERIFICATION")


def test_add_match_passes_rule_and_keeps_step_up():
    d = ENGINE.evaluate(_ctx(), _add("Marie Lambert", params.MARIE_IBAN), _sim())
    assert _vop(d).passed and _vop(d).detail.startswith("MATCH")
    assert d.verdict is Verdict.STEP_UP and d.risk_level is RiskLevel.R3 and len(d.rules) == 6


def test_add_close_match_is_r3_and_quotes_the_holder():
    d = ENGINE.evaluate(_ctx(), _add("Marie Lambart", params.MARIE_IBAN), _sim())
    r = _vop(d)
    assert not r.passed and r.severity is RiskLevel.R3 and r.detail.startswith("CLOSE MATCH")
    assert "'Marie Lambert'" in r.detail and d.verdict is Verdict.STEP_UP


def test_add_no_match_is_r3_and_does_not_reveal_the_holder():
    d = ENGINE.evaluate(_ctx(), _add("Marie Lambert", params.ATTACKER_IBAN), _sim())
    r = _vop(d)
    assert not r.passed and r.severity is RiskLevel.R3 and r.detail.startswith("NO MATCH")
    assert "Quickpay" not in r.detail and "Quickpay" not in d.explanation
    assert d.verdict is Verdict.STEP_UP


def test_add_no_match_with_iban_from_a_document_is_denied():
    d = ENGINE.evaluate(_ctx(document_ibans=[params.ATTACKER_IBAN]), _add("Jean Dupont Orsay", params.ATTACKER_IBAN),
                        _sim())
    r = _vop(d)
    assert r.severity is RiskLevel.R4 and not r.passed and "invoice-IBAN-swap" in r.detail
    assert d.verdict is Verdict.DENY


def test_document_iban_is_compared_after_normalisation():
    d = ENGINE.evaluate(_ctx(document_ibans=["gb33 bukb 2020 1555 5555 55"]),
                        _add("Jean Dupont Orsay", params.ATTACKER_IBAN), _sim())
    assert d.verdict is Verdict.DENY


def test_a_document_does_not_deny_a_match_or_a_close_match():
    for name in ("Marie Lambert", "Marie Lambart"):
        d = ENGINE.evaluate(_ctx(document_ibans=[params.MARIE_IBAN]), _add(name, params.MARIE_IBAN), _sim())
        assert d.verdict is Verdict.STEP_UP


def test_add_not_available_is_r3():
    d = ENGINE.evaluate(_ctx(), _add("Marie Lambert", params.UNLISTED_IBAN), _sim())
    r = _vop(d)
    assert not r.passed and r.severity is RiskLevel.R3 and r.detail.startswith("NOT AVAILABLE")
    assert d.verdict is Verdict.STEP_UP


def test_empty_directory_means_not_available_never_less_friction():
    d = ENGINE.evaluate(_ctx(payee_holders={}), _add("Marie Lambert", params.MARIE_IBAN), _sim())
    assert _vop(d).detail.startswith("NOT AVAILABLE") and d.verdict is Verdict.STEP_UP


def _pay(ben, iban):
    return PaymentAction(action_id="a1", idempotency_key="k1", from_account_id="acc_eur", beneficiary_id=ben,
                         iban=iban, amount=D("100"), currency="EUR")


def test_payment_to_old_payee_is_not_rechecked():
    d = ENGINE.evaluate(_ctx(), _pay("b_mom", params.MOM_IBAN), _sim())
    assert _vop(d).passed and _vop(d).detail.startswith("NOT REQUIRED")
    assert d.verdict is Verdict.CONFIRM and len(d.rules) == 14


def test_payment_to_new_payee_is_rechecked_on_the_saved_name():
    d = ENGINE.evaluate(_ctx(), _pay("b_landlord", params.LANDLORD_IBAN), _sim())
    assert _vop(d).passed and _vop(d).detail.startswith("MATCH")
    assert d.verdict is Verdict.STEP_UP

    wrong = [b.model_copy(update={"iban": params.ATTACKER_IBAN}) if b.beneficiary_id == "b_landlord" else b
             for b in _bens()]
    d = ENGINE.evaluate(_ctx(beneficiaries=wrong), _pay("b_landlord", params.ATTACKER_IBAN), _sim())
    assert _vop(d).detail.startswith("NO MATCH") and d.verdict is Verdict.STEP_UP
    d = ENGINE.evaluate(_ctx(beneficiaries=wrong, document_ibans=[params.ATTACKER_IBAN]),
                        _pay("b_landlord", params.ATTACKER_IBAN), _sim())
    assert d.verdict is Verdict.DENY and _vop(d).severity is RiskLevel.R4


def test_old_payee_boundary_is_the_72_hour_window():
    young = [b.model_copy(update={"added_at": NOW - timedelta(hours=71)}) if b.beneficiary_id == "b_mom" else b
             for b in _bens()]
    d = ENGINE.evaluate(_ctx(beneficiaries=young, payee_holders={}), _pay("b_mom", params.MOM_IBAN), _sim())
    assert _vop(d).detail.startswith("NOT AVAILABLE")
    old = [b.model_copy(update={"added_at": NOW - timedelta(hours=72)}) if b.beneficiary_id == "b_mom" else b
           for b in _bens()]
    d = ENGINE.evaluate(_ctx(beneficiaries=old, payee_holders={}), _pay("b_mom", params.MOM_IBAN), _sim())
    assert _vop(d).detail.startswith("NOT REQUIRED")


def test_fx_has_no_payee_verification_rule():
    a = FxAction(action_id="a1", idempotency_key="k", from_account_id="acc_usd", to_account_id="acc_eur",
                 quote=fx_quote("2200"))
    d = ENGINE.evaluate(_ctx(), a, _sim())
    assert "PAYEE_VERIFICATION" not in [r.rule_id for r in d.rules] and len(d.rules) == 5
