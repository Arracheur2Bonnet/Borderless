"""The fake backend mirrors Verification of Payee (v8): rule counts, card line, reply, R4 for a document IBAN."""
from bg.contracts.models import UntrustedDocument
from bg.contracts.params import DEMO_GOAL, DEMO_USER_ID, SEED_DOCUMENTS
from ui.fake_backend import build_fake

KARIM = "DE89 3704 0044 0532 0130 00"


def _add(text, docs=None):
    f = build_fake()
    sid = f.new_session(DEMO_USER_ID)
    t = f.handle_message(sid, text, docs)
    return t, t.actions[0]


def _failed(v):
    return [r.rule_id for r in v.decision.rules if not r.passed]


def test_add_payee_has_six_checks_and_match_passes():
    t, v = _add(f"add Karim Haddad with IBAN {KARIM}")
    assert len(v.decision.rules) == 6 and v.confirmation_request.checks_total == 6
    assert "PAYEE_VERIFICATION" in [r.rule_id for r in v.decision.rules]
    assert _failed(v) == ["NEW_PAYEE_ACTION"]
    assert 'Payee check: match - the bank has this account as "Karim Haddad".' in v.confirmation_request.lines
    assert "Name check passed" in t.assistant_text


def test_close_match_quotes_the_holder_and_asks_for_a_code():
    t, v = _add(f"add Karim Hadad with IBAN {KARIM}")
    assert "PAYEE_VERIFICATION" in _failed(v) and v.decision.risk_level.value == "R3"
    assert 'The bank has this account as "Karim Haddad". Did you mean that?' in t.assistant_text


def test_no_match_never_reveals_the_holder():
    t, v = _add(f"add Bob Smith with IBAN {KARIM}")
    assert "PAYEE_VERIFICATION" in _failed(v)
    assert "Karim" not in t.assistant_text and not any("Karim" in ln for ln in v.confirmation_request.lines)
    assert any(ln.startswith("Payee check: no match") for ln in v.confirmation_request.lines)


def test_not_available_for_an_unknown_account():
    t, v = _add("add Bob Smith with IBAN NL91ABNA0417164300")
    assert any(ln.startswith("Payee check: not available") for ln in v.confirmation_request.lines)
    assert "could not check the name" in t.assistant_text


def test_no_match_with_iban_from_a_document_is_denied():
    doc = UntrustedDocument(document_id="d1", source="upload", filename="invoice.txt", text=f"Please pay {KARIM}")
    t, v = _add(f"add Landlord Co with IBAN {KARIM}", [doc])
    assert v.status == "denied" and v.decision.risk_level.value == "R4" and v.receipt_id
    assert "invoice scam" in t.assistant_text and "Nothing was saved" in t.assistant_text


def test_golden_path_payment_has_fourteen_rules_and_a_payee_check_line_for_the_new_landlord():
    f = build_fake()
    sid = f.new_session(DEMO_USER_ID)
    t = f.handle_message(sid, DEMO_GOAL, [UntrustedDocument(**d) for d in SEED_DOCUMENTS])
    pays = [v for v in t.actions if v.action.action_type == "payment"]
    assert pays and all(len(v.decision.rules) == 14 for v in pays)
    rent = next(v for v in pays if v.action.beneficiary_id == "b_landlord")
    assert 'Payee check: match - the bank has this account as "Jean Dupont".' in rent.confirmation_request.lines
    tuition = next(v for v in pays if v.action.beneficiary_id == "b_univ")
    assert not any(ln.startswith("Payee check") for ln in tuition.confirmation_request.lines)
