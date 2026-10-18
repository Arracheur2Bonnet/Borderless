"""Grounding of parser output (v5): a misbehaving parser cannot change what the user asked for.

A FAKE parser returns ungrounded intents; the orchestrator must repair or drop them whatever the parser says.
"""
from decimal import Decimal as D

import pytest

from bg.agent.grounding import is_question, kind_grounded, sanctions_hits, text_amounts
from bg.agent.rules_parser import RulesParser
from bg.app import build_default
from bg.contracts import params
from bg.contracts.models import (
    AddBeneficiary, AffordabilityCheck, Clarify, Correct, FxConvert, ParsedRequest, Pay, PayObligations,
    ProvideDetail, ReadOverview, UntrustedDocument,
)

IBAN = "DE89370400440532013000"


class FakeParser:
    """Returns the queued intents, one list per message, labelled like an LLM parser."""

    def __init__(self, *turns, label="local"):
        self.turns = list(turns)
        self.label = label

    def parse(self, text, *, beneficiary_names, pending_question=None):
        intents = self.turns.pop(0)
        return ParsedRequest(intents=intents, parser=self.label, raw_text=text)


def _run(*pairs, docs=None, audit=None):
    """pairs: (message, intents). Returns (orchestrator, session, [turns])."""
    o = build_default(parser=FakeParser(*[p[1] for p in pairs]), audit_sink=audit.append if audit is not None else None)
    s = o.new_session(params.DEMO_USER_ID)
    turns = [o.handle_message(s, p[0], (docs or {}).get(i)) for i, p in enumerate(pairs)]
    return o, s, turns


def _live(t):
    return [v for v in t.actions if v.status in ("awaiting_confirmation", "executed", "proposed")]


# ------------------------------------------------------------------------------------ sanctions on source text
def test_truncated_sanctioned_name_cannot_reach_step_up():
    text = f"add vOlKoV   trade    HOLDINGS as payee with IBAN {IBAN}"
    _, _, (t,) = _run((text, [AddBeneficiary(display_name="vOlKoV", iban=IBAN, category="other")]))
    # the name is repaired to what was typed, then the policy denies it (receipt written): never a live action
    assert not _live(t) and [v.status for v in t.actions] in ([], ["denied"])
    assert all(v.action.display_name.lower().split() == ["volkov", "trade", "holdings"] for v in t.actions)
    assert any(e.kind == "sanctions_hit" for e in t.security_events)
    assert "sanctions" in t.assistant_text.lower() or "can't add" in t.assistant_text.lower()


def test_sanctioned_name_in_message_blocks_payment_whatever_the_parser_extracts():
    _, _, (t,) = _run(("send 200 euros to Northgate Exchange FZE, not mom", [Pay(beneficiary_ref="mom", amount=D("200"))]))
    assert not t.actions and any(e.kind == "sanctions_hit" for e in t.security_events)


def test_sanctioned_iban_in_message_is_a_hit():
    assert sanctions_hits("add Ali as payee, IBAN ae07 0331 2345 6789 0123 456")
    assert not sanctions_hits("send 50 euros to mom")


def test_sanctioned_name_in_user_document_blocks_the_turn():
    doc = UntrustedDocument(document_id="d1", source="upload", filename="inv.pdf",
                            text="Please pay Northgate Exchange FZE 1200 EUR")
    _, _, (t,) = _run(("pay my tuition", [PayObligations(obligation_kinds=["tuition"])]), docs={0: [doc]})
    assert not t.actions
    assert any(e.kind == "sanctions_hit" and e.source == "d1" for e in t.security_events)


def test_sanctioned_new_payee_still_goes_to_the_policy_denial():
    """Its own name is on the list: the policy denies it and writes the receipt (unchanged established path)."""
    text = f"add Volkov Trade Holdings as payee with IBAN {IBAN}"
    _, _, (t,) = _run((text, [AddBeneficiary(display_name="Volkov Trade Holdings", iban=IBAN)]))
    assert [v.status for v in t.actions] == ["denied"]


# ------------------------------------------------------------------------------------ display name grounding
def test_shortened_payee_name_is_extended_to_what_the_user_typed():
    text = f"add Maria Gonzalez Lopez as a payee with IBAN {IBAN}"
    audit: list = []
    _, _, (t,) = _run((text, [AddBeneficiary(display_name="Maria", iban=IBAN)]), audit=audit)
    assert t.actions[0].action.display_name == "Maria Gonzalez Lopez"
    rec = next(r for r in audit if r["step"] == "grounding")
    assert rec["field"] == "display_name" and rec["parser"] == "local" and rec["reason"]


def test_altered_payee_name_is_replaced_by_the_typed_one():
    text = f"add Maria Gonzalez as a payee with IBAN {IBAN}"
    _, _, (t,) = _run((text, [AddBeneficiary(display_name="Marie Gonzalez", iban=IBAN)]))
    assert t.actions[0].action.display_name == "Maria Gonzalez"


def test_invented_payee_name_is_not_added():
    text = f"add Maria Gonzalez as a payee with IBAN {IBAN}"
    _, _, (t,) = _run((text, [AddBeneficiary(display_name="Robert Smith", iban=IBAN)]))
    assert not t.actions and "name" in t.assistant_text.lower()


def test_iban_not_in_message_is_not_added():
    _, _, (t,) = _run(("add Maria Gonzalez as a payee", [AddBeneficiary(display_name="Maria Gonzalez", iban=IBAN)]))
    assert not t.actions and any(e.kind == "beneficiary_mismatch" for e in t.security_events)


# ------------------------------------------------------------------------------------ field grounding
def test_invented_obligation_kind_is_dropped_and_user_asked():
    text = "the bursar emailed me, pay what they ask"
    _, _, (t,) = _run((text, [PayObligations(obligation_kinds=["rent"])]))
    assert not t.actions and "tuition or your rent" in t.assistant_text


def test_only_the_ungrounded_kind_is_dropped():
    _, _, (t,) = _run(("pay my tuition", [PayObligations(obligation_kinds=["tuition", "rent"])]))
    kinds = {v.action.memo.split()[0] for v in t.actions if v.action.action_type == "payment"}
    assert kinds == {"tuition"} and "rent" in " ".join(t.assistant_text.split()[:60]).lower()


def test_invented_amount_and_payee_become_a_question():
    text = "please pay this tuition request"
    _, _, (t,) = _run((text, [Pay(beneficiary_ref="landlord", amount=D("850"))]))
    assert not _live(t) and t.pending_question


def test_correct_with_invented_payee_keeps_the_original_payee():
    o, s, (t1, t2) = _run(("send 40 to mom", [Pay(beneficiary_ref="mom", amount=D("40"))]),
                          ("no, 35 instead", [Correct(beneficiary_ref="university", amount=D("35"))]))
    live = _live(t2)
    assert len(live) == 1
    assert live[0].action.beneficiary_id == "b_mom" and live[0].action.amount == D("35")


def test_correct_with_invented_amount_changes_nothing():
    _, _, (t1, t2) = _run(("send 40 to mom", [Pay(beneficiary_ref="mom", amount=D("40"))]),
                          ("make it more", [Correct(amount=D("500"))]))
    assert not _live(t2) or all(v.action.amount != D("500") for v in t2.actions)


def test_invented_currency_is_corrected():
    _, _, (t,) = _run(("send 100 euros to mom", [Pay(beneficiary_ref="mom", amount=D("100"), currency="USD")]))
    assert t.actions[0].action.currency == "EUR"


def test_ungrounded_iban_hint_is_dropped_without_a_mismatch_alarm():
    _, _, (t,) = _run(("send 50 euros to mom", [Pay(beneficiary_ref="mom", amount=D("50"), iban_hint=params.ATTACKER_IBAN)]))
    assert t.actions and not any(e.kind == "beneficiary_mismatch" for e in t.security_events)


def test_ungrounded_fx_amount_is_not_converted():
    _, _, (t,) = _run(("exchange some dollars for me", [FxConvert(sell_currency="USD", buy_currency="EUR",
                                                                 buy_amount=D("5000"))]))
    assert not t.actions


def test_reserve_stated_by_the_user_is_restored():
    _, _, (t,) = _run(("pay my tuition, keep 700 euros", [PayObligations(obligation_kinds=["tuition"])]))
    assert t.parsed.intents[0].keep_reserve == D("700")


def test_draft_context_grounds_the_follow_up():
    o, s, (t1, t2) = _run(("send mom some money", [Pay(beneficiary_ref="mom", amount_mode="unspecified")]),
                          ("50", [Pay(beneficiary_ref="mom", amount=D("50"))]))
    assert _live(t2)[0].action.amount == D("50") and _live(t2)[0].action.beneficiary_id == "b_mom"


def test_provide_detail_amount_must_be_in_text():
    o, s, (t1, t2) = _run(("send mom some money", [Pay(beneficiary_ref="mom", amount_mode="unspecified")]),
                          ("whatever you think", [ProvideDetail(amount=D("900"))]))
    assert not _live(t2)


def test_translated_payee_word_is_grounded():
    _, _, (t,) = _run(("envoie 50 euros à maman", [Pay(beneficiary_ref="mom", amount=D("50"))]))
    assert _live(t)[0].action.beneficiary_id == "b_mom"


# ------------------------------------------------------------------------------------ question guard
def test_rate_question_is_answered_not_converted():
    _, _, (t,) = _run(("how much is 200 USD in EUR right now?",
                       [FxConvert(sell_currency="USD", buy_currency="EUR", buy_amount=D("200"))]))
    assert not t.actions and "183.08" in t.assistant_text


def test_generic_question_with_action_becomes_clarification():
    _, _, (t,) = _run(("what would paying mom 50 look like?", [Pay(beneficiary_ref="mom", amount=D("50"))]))
    assert not t.actions


@pytest.mark.parametrize("text,expected", [
    ("how much is 200 USD in EUR right now?", True),
    ("What is the rate today?", True),
    ("can you send 50 euros to mom?", False),
    ("could you pay my rent?", False),
    ("send 50 euros to mom, ok?", False),
    ("send 50 euros to mom", False),
    ("are you able to pay my rent?", False),
])
def test_is_question(text, expected):
    assert is_question(text) is expected


# ------------------------------------------------------------------------------------ helpers
@pytest.mark.parametrize("text,amount", [
    ("send 1,200 to mom", "1200"), ("send 1200.00 to mom", "1200"), ("send 1.200,00 to mom", "1200"),
    ("send 1 200 euros", "1200"), ("send €40,50", "40.5"), ("send fifty to mom", "50"),
    ("send two hundred and fifty euros", "250"), ("send 1.5k", "1500"), ("envoie ٥٠ euros", "50"),
    ("send 1.200 to mom", "1200"),
])
def test_text_amounts_formats(text, amount):
    assert D(amount) in text_amounts(text)


def test_amounts_inside_an_iban_are_not_amounts():
    assert D("44") not in text_amounts(f"add Bob IBAN DE89 3704 0044 0532 0130 00")


@pytest.mark.parametrize("kind,text,ok", [
    ("tuition", "pay my tuition", True), ("tuition", "paga la matrícula", True), ("rent", "zahle die Miete", True),
    ("rent", "payer le loyer", True), ("tuition", "pay my tuiton", True), ("rent", "pay my tuition", False),
    ("tuition", "pay the rent", False), ("rent", "pay my bills", True), ("tuition", "Studiengebühren überweisen", True),
    ("rent", "pagar el alquiler", True), ("rent", "pay what they ask", False),
])
def test_kind_words(kind, text, ok):
    assert kind_grounded(kind, text) is ok


@pytest.mark.parametrize("text", [
    "pay my tuition", "pay my tuiton and rent, keep 600", "send 50 euros to mom", "send mum 30 euros for groceries",
    "convert $500 to euros", "I need 300 more euros, buy them with my USD", "can I afford to send 300 to mom after rent?",
    "pay rent, then 50 to mom", "actually make it 40", f"add Ali Rahman as a payee, IBAN {IBAN}",
    "send 1,200.50 to the landlord", "Pay my November tuition and rent, keep 600 euros",
])
def test_rules_parser_output_is_never_repaired(text):
    from bg.agent.grounding import GroundingContext, ground_intents
    from bg.ledger import SandboxLedger
    bens = SandboxLedger().get_beneficiaries(params.DEMO_USER_ID)
    names = [b.display_name for b in bens] + [a for b in bens for a in b.aliases]
    intents = RulesParser().parse(text, beneficiary_names=names).intents
    out, reps = ground_intents(list(intents), GroundingContext(text=text, beneficiaries=bens))
    assert not reps and out == list(intents)
