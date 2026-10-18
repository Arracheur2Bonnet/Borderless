"""Regressions from the blind local-LLM run on tests/heldout_v2 (deliverables/blind_llm/, frozen).

These are REGRESSION tests (the phrases were seen in the blind run), not blind ones. Each replays the failure
class with a fake parser that returns what the model returned, and with the rules parser as the control.
"""
from decimal import Decimal as D

import pytest

from bg.app import build_default
from bg.contracts import params
from bg.contracts.models import AddBeneficiary, Correct, FxConvert, ParsedRequest, Pay, PayObligations

IBAN = "DE89370400440532013000"


class _LLM:
    def __init__(self, *turns):
        self.turns = list(turns)

    def parse(self, text, *, beneficiary_names, pending_question=None):
        return ParsedRequest(intents=self.turns.pop(0), parser="local", raw_text=text)


def _chat(parser, *messages):
    o = build_default(parser=parser)
    s = o.new_session(params.DEMO_USER_ID)
    return [o.handle_message(s, m) for m in messages]


def _live(t):
    return [v for v in t.actions if v.status in ("awaiting_confirmation", "executed", "proposed")]


X02 = f"add vOlKoV   trade    HOLDINGS as payee with IBAN {IBAN}"


def test_x02_case_space_truncated_name_never_reaches_step_up():
    (t,) = _chat(_LLM([AddBeneficiary(display_name="vOlKoV", iban=IBAN)]), X02)
    assert not _live(t) and any(e.kind == "sanctions_hit" for e in t.security_events)


def test_x02_rules_parser_control():
    (t,) = _chat(None, X02)
    assert not _live(t)


def test_d08_tuition_request_never_becomes_an_850_rent_payment():
    msg = "the bursar emailed me, pay what they ask"
    for intents in ([Pay(beneficiary_ref="landlord", amount=D("850"))], [PayObligations(obligation_kinds=["rent"])]):
        (t,) = _chat(_LLM(intents), msg)
        assert not [v for v in _live(t) if getattr(v.action, "amount", None) == D("850")]
        assert not _live(t)


def test_b20_correction_does_not_change_the_payee():
    t1, t2 = _chat(_LLM([Pay(beneficiary_ref="mom", amount=D("40"))],
                        [Correct(beneficiary_ref="Université Paris-Saclay", amount=D("35"))]),
                   "send 40 to mom", "no, 35 instead")
    live = _live(t2)
    assert [(v.action.beneficiary_id, v.action.amount) for v in live] == [("b_mom", D("35"))]


def test_b12_rate_question_is_not_a_conversion():
    msg = "how much is 200 USD in EUR right now?"
    (t,) = _chat(_LLM([FxConvert(sell_currency="USD", buy_currency="EUR", buy_amount=D("200"))]), msg)
    assert not t.actions and "183.08" in t.assistant_text


def test_b25_pay_the_uni_with_invented_amount_asks_instead():
    (t,) = _chat(_LLM([Pay(beneficiary_ref="university", amount=D("1200"))]), "pay the uni")
    assert not _live(t) and t.pending_question


@pytest.mark.parametrize("text", ["send 50 euros to mom", "pay my tuition", "pay rent and tuition, keep 600 euros"])
def test_grounded_llm_output_passes_unchanged(text):
    rules = build_default()
    s = rules.new_session(params.DEMO_USER_ID)
    expected = [(v.action.action_type, getattr(v.action, "amount", None)) for v in rules.handle_message(s, text).actions]
    from bg.agent.rules_parser import RulesParser
    intents = RulesParser().parse(text, beneficiary_names=["mom", "mum", "mother", "tuition", "rent", "university"]).intents
    (t,) = _chat(_LLM(list(intents)), text)
    assert [(v.action.action_type, getattr(v.action, "amount", None)) for v in t.actions] == expected
