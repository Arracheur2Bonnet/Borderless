"""Verification of Payee (v8), end to end through the orchestrator. Modelled on the EU instant-payments Verification
of Payee and the UK Confirmation of Payee (no legal claim). The seeded landlord payee already has the name
"Jean Dupont", so the tests that add that name first remove the seeded payee from their own sandbox ledger."""
import pytest

from bg.app import build_default
from bg.contracts import params
from bg.contracts.models import UntrustedDocument

ATK_SP = "GB33 BUKB 2020 1555 5555 55"
LANDLORD_SP = "FR76 3000 4000 0312 3456 7890 143"

FORGED_LETTER = UntrustedDocument(
    document_id="doc_bank_change", source="email", filename="bank_change.pdf",
    text=("Orsay, 25 October 2026\nDear Lina,\nI have changed banks. Please pay the rent of EUR 850.00 to my new "
          f"account:\nIBAN: {ATK_SP} (Barclays UK)\nKind regards,\nJean Dupont\n"))


def _session(drop_landlord=False):
    o = build_default()
    if drop_landlord:
        o.ledger._beneficiaries.pop("b_landlord")
    return o, o.new_session(params.DEMO_USER_ID)


def _add(o, s, text, docs=None):
    t = o.handle_message(s, text, docs)
    assert len(t.actions) == 1, t.assistant_text
    return t, t.actions[0]


def _vop(view):
    return next(r for r in view.decision.rules if r.rule_id == "PAYEE_VERIFICATION")


def _lines(view):
    return " | ".join(view.confirmation_request.lines)


def test_match_landlord_name_and_iban():
    o, s = _session(drop_landlord=True)
    t, v = _add(o, s, f"Add a new payee Jean Dupont with IBAN {LANDLORD_SP}")
    assert _vop(v).passed and _vop(v).detail.startswith("MATCH")
    assert v.status == "awaiting_confirmation" and v.decision.verdict.value == "step_up"     # adding is always R3
    assert 'Payee check: match - the bank has this account as "Jean Dupont".' in v.confirmation_request.lines
    assert 'Name check passed: the bank has this account as "Jean Dupont".' in t.assistant_text
    assert len(v.decision.rules) == 6 and v.confirmation_request.checks_total == 6


def test_close_match_asks_did_you_mean():
    o, s = _session()
    t, v = _add(o, s, f"Add a new payee Jean Dupond with IBAN {LANDLORD_SP}")
    assert _vop(v).detail.startswith("CLOSE MATCH") and v.decision.verdict.value == "step_up"
    assert 'The bank has this account as "Jean Dupont". Did you mean that?' in t.assistant_text
    assert 'Payee check: close match - the bank has this account as "Jean Dupont". Did you mean that?' \
        in v.confirmation_request.lines
    assert t.security_events == []


def test_no_match_keeps_step_up_and_warns_without_naming_the_holder():
    o, s = _session(drop_landlord=True)
    t, v = _add(o, s, f"Add a new payee Jean Dupont with IBAN {ATK_SP}")
    assert _vop(v).detail.startswith("NO MATCH") and v.status == "awaiting_confirmation"
    assert v.decision.verdict.value == "step_up"
    assert "does not match the name the bank has for this account" in t.assistant_text
    assert "Did someone ask you to change bank details?" in t.assistant_text
    assert "Quickpay" not in t.assistant_text and "Quickpay" not in _lines(v)
    assert "Payee check: no match" in _lines(v)
    # the customer may still go ahead with the one-time code (as friction, never as a bypass)
    o.confirm(s, v.action.action_id, v.confirmation_request.params_hash, params.DEMO_OTP)
    assert any(b["display_name"] == "Jean Dupont" for b in o.overview(s)["beneficiaries"])


def test_not_available_warns():
    o, s = _session()
    t, v = _add(o, s, f"Add a new payee Marie Lambert with IBAN {params.UNLISTED_IBAN}")
    assert _vop(v).detail.startswith("NOT AVAILABLE") and v.decision.verdict.value == "step_up"
    assert "could not check the name" in t.assistant_text
    assert "Payee check: not available" in _lines(v)


def test_unicode_and_case_in_the_typed_name_still_match():
    o, s = _session()
    t, v = _add(o, s, f"Add a new payee MARIE LAMBERT with IBAN {params.MARIE_IBAN}")
    assert _vop(v).passed


def test_same_name_as_existing_payee_is_still_refused_before_verification():
    o, s = _session()
    t = o.handle_message(s, f"Add a new payee Jean Dupont with IBAN {ATK_SP}")
    assert t.actions == [] and "looks the same as" in t.assistant_text or "already used for" in t.assistant_text
    assert all(b.iban != params.ATTACKER_IBAN for b in o.ledger.get_beneficiaries(params.DEMO_USER_ID))


def test_forged_invoice_iban_swap_is_denied():
    """The letter says the landlord changed banks; the new account is held by a company with a different name."""
    o, s = _session()
    t, v = _add(o, s, f"Add a new payee Jean Dupont Orsay with IBAN {ATK_SP}", [FORGED_LETTER])
    assert v.status == "denied" and v.decision.verdict.value == "deny"
    r = _vop(v)
    assert r.severity.value == "R4" and not r.passed and "invoice-IBAN-swap" in r.detail
    assert "classic invoice scam" in t.assistant_text and "Nothing was saved" in t.assistant_text
    assert v.receipt_id is not None
    assert all(b.iban != params.ATTACKER_IBAN for b in o.ledger.get_beneficiaries(params.DEMO_USER_ID))
    assert o.receipts.get(v.receipt_id, user_id=params.DEMO_USER_ID).action_id == v.action.action_id


def test_same_text_without_a_document_is_step_up_not_deny():
    o, s = _session()
    t, v = _add(o, s, f"Add a new payee Jean Dupont Orsay with IBAN {ATK_SP}")
    assert v.decision.verdict.value == "step_up" and _vop(v).detail.startswith("NO MATCH")


def test_golden_path_payments_show_the_check_only_for_the_new_payee():
    o, s = _session()
    t = o.handle_message(s, params.DEMO_GOAL, [UntrustedDocument(**d) for d in params.SEED_DOCUMENTS])
    pays = {v.action.beneficiary_id: v for v in t.actions if v.action.action_type == "payment"}
    assert _vop(pays["b_landlord"]).detail.startswith("MATCH")
    assert _vop(pays["b_univ"]).detail.startswith("NOT REQUIRED")
    assert any(x.startswith("Payee check: match") for x in pays["b_landlord"].confirmation_request.lines)
    assert not any(x.startswith("Payee check") for x in pays["b_univ"].confirmation_request.lines)
    assert len(pays["b_univ"].decision.rules) == 14
    assert pays["b_univ"].decision.verdict.value == "confirm" and pays["b_landlord"].decision.verdict.value == "step_up"


@pytest.mark.parametrize("name,iban", [("Jean Dupond", params.LANDLORD_IBAN), ("Marie Lambart", params.MARIE_IBAN),
                                       ("Marie Lambert", params.UNLISTED_IBAN), ("Marie Lambert", params.ATTACKER_IBAN)])
def test_friction_is_never_lower_than_before(name, iban):
    """Every outcome is at least the step-up that adding a payee always required."""
    o, s = _session()
    t, v = _add(o, s, f"Add a new payee {name} with IBAN {iban}")
    assert v.decision.verdict.value in ("step_up", "deny")
