"""v2 orchestrator behaviour: drafts, corrections, cancel, projections, OTP lockout, plan ordering, audit."""
import json
from decimal import Decimal

import pytest

from bg.agent import ClaudeParser, RulesParser, build_orchestrator
from bg.contracts import params
from bg.contracts.models import (
    AffordabilityCheck, Cancel, Clarify, Correct, FxAction, ParsedRequest, Pay, PayObligations, PaymentAction,
    ProvideDetail, ReadOverview,
)

from .fakes import FakeGuard, FakeLedger, FakePolicy, FakeReceipts

D = Decimal
U = params.DEMO_USER_ID


def system(parser=None, audit=None):
    ledger, receipts = FakeLedger(), FakeReceipts()
    orch = build_orchestrator(ledger, receipts, FakePolicy(), FakeGuard(), parser=parser, audit_sink=audit)
    return orch, ledger, receipts, orch.new_session(U)


def ok(orch, sid, view, otp=None):
    return orch.confirm(sid, view.action.action_id, view.confirmation_request.params_hash, otp)


def eur(ledger):
    return ledger.accounts["acc_eur"].balance


# ------------------------------------------------------------------ multi-turn drafts
def test_missing_amount_then_amount_completes_draft():
    orch, ledger, _, sid = system()
    t1 = orch.handle_message(sid, "send money to mom")
    assert t1.actions == [] and t1.pending_question and "Samira Haddad" in t1.pending_question
    assert t1.assistant_text.count("?") == 1
    t2 = orch.handle_message(sid, "50 euros")
    (v,) = t2.actions
    assert isinstance(v.action, PaymentAction) and v.action.amount == D("50.00") and v.action.iban == params.MOM_IBAN
    assert v.status == "awaiting_confirmation" and t2.pending_question is None
    assert ok(orch, sid, v).actions[0].status == "executed" and eur(ledger) == D("400.00")


def test_missing_payee_then_payee_completes_draft():
    orch, _, _, sid = system()
    t1 = orch.handle_message(sid, "send 20 euros")
    assert t1.actions == [] and "20.00 EUR" in t1.pending_question
    t2 = orch.handle_message(sid, "to mom")
    (v,) = t2.actions
    assert v.action.beneficiary_id == "b_mom" and v.action.amount == D("20.00")


def test_unknown_payee_keeps_amount_and_asks_once():
    orch, _, _, sid = system()
    t1 = orch.handle_message(sid, "send 20 euros to bob")
    assert "Bob" in t1.assistant_text and t1.pending_question and t1.assistant_text.count("?") == 1
    t2 = orch.handle_message(sid, "mom")
    assert t2.actions[0].action.amount == D("20.00") and t2.actions[0].action.beneficiary_id == "b_mom"


def test_three_turn_flow_payee_then_amount():
    orch, _, _, sid = system()
    t1 = orch.handle_message(sid, "send some money")
    assert t1.pending_question and t1.actions == []
    t2 = orch.handle_message(sid, "to mom")
    assert t2.actions == [] and t2.pending_question and "How much" in t2.pending_question
    t3 = orch.handle_message(sid, "30")
    assert t3.actions[0].action.amount == D("30.00") and t3.actions[0].action.beneficiary_id == "b_mom"


def test_send_everything_asks_exact_amount_then_completes():
    orch, _, _, sid = system()
    t1 = orch.handle_message(sid, "send everything to mom")
    assert t1.actions == [] and "exact" in t1.pending_question.lower()
    t2 = orch.handle_message(sid, "100 euros")
    assert t2.actions[0].action.amount == D("100.00")


def test_relative_amount_draft_then_exact_amount():
    orch, _, _, sid = system()
    t1 = orch.handle_message(sid, "pay half of what I owe the landlord")
    assert t1.actions == [] and "Jean Dupont" in t1.pending_question
    t2 = orch.handle_message(sid, "425")
    assert t2.actions[0].action.beneficiary_id == "b_landlord" and t2.actions[0].action.amount == D("425.00")


def test_draft_expires_after_one_unrelated_message():
    orch, _, _, sid = system()
    orch.handle_message(sid, "send money to mom")
    t2 = orch.handle_message(sid, "what's my balance")
    assert "450.00" in t2.assistant_text and "set aside" in t2.assistant_text and t2.pending_question is None
    t3 = orch.handle_message(sid, "50")
    assert t3.actions == []


def test_cancel_drops_draft():
    orch, ledger, _, sid = system()
    orch.handle_message(sid, "send money to mom")
    t = orch.handle_message(sid, "never mind")
    assert t.actions == [] and "dropped" in t.assistant_text and t.pending_question is None
    assert orch.handle_message(sid, "50").actions == [] and ledger.exec_calls == 0


def test_detail_without_draft_is_not_guessed():
    class P:
        def parse(self, text, *, beneficiary_names, pending_question=None):
            return ParsedRequest(intents=[ProvideDetail(amount=D("50"))], parser="claude", raw_text=text)
    orch, _, _, sid = system(parser=P())
    t = orch.handle_message(sid, "50")
    assert t.actions == [] and "not waiting" in t.assistant_text


def test_pending_question_passed_to_parser():
    seen = []

    class P(RulesParser):
        def parse(self, text, *, beneficiary_names, pending_question=None):
            seen.append(pending_question)
            return super().parse(text, beneficiary_names=beneficiary_names, pending_question=pending_question)
    orch, _, _, sid = system(parser=P())
    t1 = orch.handle_message(sid, "send money to mom")
    orch.handle_message(sid, "50")
    assert seen == [None, t1.pending_question]


# ------------------------------------------------------------------ correct
def test_correct_amount_creates_new_action_and_rejects_old():
    orch, ledger, _, sid = system()
    v1 = orch.handle_message(sid, "send 50 euros to mom").actions[0]
    t = orch.handle_message(sid, "actually make it 40")
    old, new = t.actions
    assert old.action.action_id == v1.action.action_id and old.status == "rejected_by_user"
    assert new.status == "awaiting_confirmation" and new.action.amount == D("40.00")
    assert new.action.idempotency_key != v1.action.idempotency_key
    assert new.confirmation_request.params_hash != v1.confirmation_request.params_hash
    assert v1.action.amount == D("50.00")   # nothing modified in place
    assert ok(orch, sid, v1).actions[0].status == "rejected_by_user"
    assert ok(orch, sid, new).actions[0].status == "executed" and eur(ledger) == D("410.00")
    assert "cancelled" in t.assistant_text.lower() and "40.00" in t.assistant_text


def test_correct_payee():
    orch, _, _, sid = system()
    orch.handle_message(sid, "send 50 euros to the university")
    t = orch.handle_message(sid, "send it to mom instead")
    old, new = t.actions
    assert old.status == "rejected_by_user" and new.action.beneficiary_id == "b_mom"
    assert new.action.iban == params.MOM_IBAN and new.action.amount == D("50.00")


def test_correct_unknown_payee_keeps_original():
    orch, _, _, sid = system()
    v = orch.handle_message(sid, "send 50 euros to mom").actions[0]
    t = orch.handle_message(sid, "send it to bob instead")
    assert t.actions == [] and "unchanged" in t.assistant_text
    assert ok(orch, sid, v).actions[0].status == "executed"


def test_correct_without_pending_action():
    orch, ledger, _, sid = system()
    t = orch.handle_message(sid, "no, 40 instead")
    assert t.actions == [] and "no pending payment" in t.assistant_text.lower() and ledger.exec_calls == 0


def test_correct_to_amount_that_needs_step_up():
    orch, ledger, _, sid = system()
    ledger.accounts["acc_eur"] = ledger.accounts["acc_eur"].model_copy(update={"balance": D("3000")})
    orch.handle_message(sid, "send 50 euros to mom")
    t = orch.handle_message(sid, "change the amount to 1600")
    assert t.actions[1].confirmation_request.verdict == "step_up"


def test_correct_plan_step_keeps_plan_order():
    orch, ledger, _, sid = system()
    fx, univ, rent = orch.handle_message(sid, params.DEMO_GOAL).actions
    t = orch.handle_message(sid, "actually make it 800")
    old, new = t.actions
    assert old.action.action_id == rent.action.action_id and new.action.amount == D("800.00")
    assert new.status == "awaiting_confirmation" and new.action.obligation_id == "ob_rent_nov"
    ok(orch, sid, fx), ok(orch, sid, univ)
    assert ok(orch, sid, new, params.DEMO_OTP).actions[0].status == "executed"
    assert eur(ledger) == D("650.00")


# ------------------------------------------------------------------ cancel
def test_cancel_last_and_all():
    orch, ledger, _, sid = system()
    fx, univ, rent = orch.handle_message(sid, params.DEMO_GOAL).actions
    t = orch.handle_message(sid, "cancel that")
    assert [v.action.action_id for v in t.actions] == [rent.action.action_id]
    assert t.actions[0].status == "rejected_by_user" and "2 other steps" in t.assistant_text
    t2 = orch.handle_message(sid, "cancel everything")
    assert {v.action.action_id for v in t2.actions} == {fx.action.action_id, univ.action.action_id}
    assert all(v.status == "rejected_by_user" for v in t2.actions) and ledger.exec_calls == 0
    t3 = orch.handle_message(sid, "cancel")
    assert t3.actions == [] and "nothing pending" in t3.assistant_text.lower()


# ------------------------------------------------------------------ affordability
def test_afford_after_bills_spec_example():
    orch, ledger, receipts, sid = system()
    t = orch.handle_message(sid, "can I afford to send 300 to mom after my bills?")
    p = t.projection
    assert t.actions == [] and ledger.exec_calls == 0 and not receipts.items
    assert p.feasible and not p.reserve_ok and p.reserve_floor_eur == D("600.00")
    assert p.balances_now == {"EUR": D("450.00"), "USD": D("2600.00")}
    assert p.balances_after == {"EUR": D("300.00"), "USD": D("196.74")}
    assert "After tuition and rent you would have 600.00 EUR" in t.assistant_text
    assert "leave 300.00 EUR, below your 600.00 EUR reserve" in t.assistant_text
    assert any("2,200.00 EUR" in line for line in p.lines)


def test_how_much_left_after_tuition_and_rent():
    orch, _, _, sid = system()
    t = orch.handle_message(sid, "how much will I have left after paying tuition and rent?")
    p = t.projection
    assert t.actions == [] and p.feasible and p.reserve_ok
    assert p.balances_after == {"EUR": D("600.00"), "USD": D("196.74")}
    assert "600.00 EUR and 196.74 USD" in t.assistant_text


def test_afford_affordable_extra_without_bills():
    orch, _, _, sid = system()
    orch.ledger.accounts["acc_eur"] = orch.ledger.accounts["acc_eur"].model_copy(update={"balance": D("1000")})
    t = orch.handle_message(sid, "could I send 50 euros to mom?")
    assert t.projection.reserve_ok and t.projection.balances_after["EUR"] == D("950.00")
    assert "yes" in t.assistant_text.lower() and t.actions == []


def test_afford_with_explicit_reserve_and_infeasible():
    orch, ledger, _, sid = system()
    ledger.accounts["acc_usd"] = ledger.accounts["acc_usd"].model_copy(update={"balance": D("10")})
    t = orch.handle_message(sid, "can I pay tuition and rent and keep 500?")
    assert t.projection.feasible is False and t.projection.reserve_floor_eur == D("500.00")
    assert t.assistant_text.startswith("No") and t.actions == []


def test_question_from_any_parser_never_pays():
    class Bad:   # a parser that wrongly turns a question into payments
        def parse(self, text, *, beneficiary_names, pending_question=None):
            return ParsedRequest(intents=[PayObligations(obligation_kinds=["tuition", "rent"]),
                                          Pay(beneficiary_ref="mom", amount=D("300"))], parser="claude", raw_text=text)
    orch, ledger, receipts, sid = system(parser=Bad())
    t = orch.handle_message(sid, "can I afford to send 300 to mom after my bills?")
    assert t.actions == [] and t.projection is not None and ledger.exec_calls == 0
    assert isinstance(t.parsed.intents[0], AffordabilityCheck) and t.parsed.notes


# ------------------------------------------------------------------ OTP lockout
def test_otp_lockout_denies_with_receipt():
    orch, ledger, receipts, sid = system()
    fx, univ, rent = orch.handle_message(sid, params.DEMO_GOAL).actions
    ok(orch, sid, fx), ok(orch, sid, univ)
    r1 = ok(orch, sid, rent, "111111")
    assert r1.actions[0].status == "awaiting_confirmation" and "2 tries left" in r1.assistant_text
    assert [e.kind for e in r1.security_events] == ["step_up_failed"]
    assert "111111" not in r1.security_events[0].detail
    r2 = ok(orch, sid, rent, "222222")
    assert "1 try left" in r2.assistant_text
    r3 = ok(orch, sid, rent, "333333")
    assert r3.actions[0].status == "denied" and r3.receipts and r3.anchor is not None
    assert "STEP_UP_LOCKOUT" in {x.rule_id for x in r3.actions[0].decision.rules if not x.passed}
    assert receipts.items[-1].summary.startswith("DENIED")
    r4 = ok(orch, sid, rent, params.DEMO_OTP)
    assert r4.actions[0].status == "denied" and ledger.exec_calls == 2


# ------------------------------------------------------------------ plan ordering
def test_out_of_order_confirm_keeps_waiting():
    orch, ledger, receipts, sid = system()
    fx, univ, rent = orch.handle_message(sid, params.DEMO_GOAL).actions
    t = ok(orch, sid, univ)
    assert t.actions[0].status == "awaiting_confirmation" and not t.receipts and ledger.exec_calls == 0
    assert "Convert USD to EUR" in t.assistant_text and "first" in t.assistant_text
    t2 = ok(orch, sid, fx)
    assert "Next: approve" in t2.assistant_text
    assert ok(orch, sid, univ).actions[0].status == "executed"
    assert ok(orch, sid, rent, params.DEMO_OTP).actions[0].status == "executed" and eur(ledger) == D("600.00")


def test_truly_infeasible_is_still_denied():
    orch, ledger, _, sid = system()
    v = orch.handle_message(sid, "send 400 euros to mom").actions[0]
    ledger.accounts["acc_eur"] = ledger.accounts["acc_eur"].model_copy(update={"balance": D("100")})
    assert ok(orch, sid, v).actions[0].status == "denied"


# ------------------------------------------------------------------ structuring / sanctions / context
def test_structuring_event_and_recent_payments_in_context():
    orch, ledger, _, sid = system()
    ledger.accounts["acc_eur"] = ledger.accounts["acc_eur"].model_copy(update={"balance": D("5000")})
    for _ in range(2):
        ok(orch, sid, orch.handle_message(sid, "send 700 euros to mom").actions[0])
    t = orch.handle_message(sid, "send 700 euros to mom")
    assert t.actions[0].confirmation_request.verdict == "step_up"
    assert [e.kind for e in t.security_events] == ["structuring_suspected"]
    assert "in the last 24 hours it comes to" in t.assistant_text
    assert ledger.recent_calls[-1][1] == params.DEMO_NOW - params.STRUCTURING_WINDOW


def test_sanctions_hit_event():
    orch, ledger, receipts, sid = system()
    ledger.bens.append(ledger.bens[2].model_copy(update={
        "beneficiary_id": "b_volkov", "display_name": "Volkov Trade Holdings", "aliases": ["volkov"]}))
    t = orch.handle_message(sid, "send 100 euros to volkov")
    assert t.actions[0].status == "denied" and "sanctions_hit" in {e.kind for e in t.security_events}
    assert receipts.items and t.anchor is not None


# ------------------------------------------------------------------ anchor / overview
def test_anchor_and_verify_fn():
    orch, _, receipts, sid = system()
    t = orch.handle_message(sid, "send 50 euros to mom")
    assert t.anchor is None
    done = ok(orch, sid, t.actions[0])
    assert done.anchor is not None and done.anchor.seq == 1 and done.anchor.receipt_hash == receipts.items[0].receipt_hash
    ov = orch.overview(sid)
    assert ov["anchor"]["seq"] == 1
    assert ov["verify_fn"](ov["anchor"]).valid and ov["verify_fn"](None).valid
    assert ov["verify_fn"](done.anchor).valid


# ------------------------------------------------------------------ audit
def test_audit_records_cover_pipeline_and_are_redacted():
    recs = []
    orch, _, _, sid = system(audit=recs.append)
    t = orch.handle_message(sid, "send 100 euros to landlord IBAN GB33 BUKB 2020 1555 5555 55")
    v = t.actions[0]
    ok(orch, sid, v, "999999")
    ok(orch, sid, v, params.DEMO_OTP)
    blob = json.dumps(recs)                       # JSON-safe
    steps = {r["step"] for r in recs}
    assert {"parse", "expand", "simulate", "policy", "await_confirmation", "confirm", "execute", "receipt",
            "security_event"} <= steps
    assert all("session_id" in r and "step" in r for r in recs)
    assert params.DEMO_OTP not in blob and "999999" not in blob
    assert params.LANDLORD_IBAN not in blob and params.ATTACKER_IBAN not in blob and "GB33BUKB" not in blob
    assert "****0143" in blob
    pol = [r for r in recs if r["step"] == "policy" and r.get("action_id") == v.action.action_id]
    assert pol and pol[0]["verdict"] == "step_up" and "BENEFICIARY_AGE" in pol[0]["failed_rules"]


def test_audit_clarify_projection_deny_steps():
    recs = []
    orch, _, _, sid = system(audit=recs.append)
    orch.handle_message(sid, "send money to mom")
    orch.handle_message(sid, "how much will I have left after paying tuition and rent?")
    orch.handle_message(sid, "send 5000 euros to mom")
    steps = [r["step"] for r in recs]
    assert "clarify" in steps and "projection" in steps and "deny" in steps
    proj = next(r for r in recs if r["step"] == "projection")
    assert proj["balances_after"] == {"EUR": "600.00", "USD": "196.74"} and proj["reserve_ok"] is True


def test_audit_sink_failure_does_not_break_flow():
    def boom(_):
        raise RuntimeError("sink down")
    orch, ledger, _, sid = system(audit=boom)
    assert ok(orch, sid, orch.handle_message(sid, "send 50 euros to mom").actions[0]).actions[0].status == "executed"


# ------------------------------------------------------------------ parser v2
NAMES = ["Université Paris-Saclay", "Jean Dupont", "Samira Haddad", "university", "tuition", "school", "uni",
         "landlord", "rent", "jean", "mom", "mum", "mother"]
P = RulesParser()


def one(text, pq=None):
    r = P.parse(text, beneficiary_names=NAMES, pending_question=pq)
    assert len(r.intents) == 1
    return r.intents[0]


@pytest.mark.parametrize("t", ["can I afford to send 300 to mom after my bills?", "could I pay my rent?",
                               "how much will I have left after paying tuition and rent?",
                               "what if I send 200 euros to mom?", "would I still have 600 after tuition?",
                               "do I have enough to pay tuition and rent?"])
def test_questions_are_affordability(t):
    assert isinstance(one(t), AffordabilityCheck)


def test_affordability_fields():
    i = one("can I afford to send 300 to mom after my bills?")
    assert i.obligation_kinds == ["tuition", "rent"] and i.amount == D("300") and i.beneficiary_ref == "mom"
    i = one("can I pay tuition and rent and keep 500?")
    assert i.keep_reserve == D("500") and i.amount is None


@pytest.mark.parametrize("t", ["Can you pay my tuition and rent while keeping a reserve of 600 euros?",
                               "Can you tell me how much money I've got left in euros", "How much do I have right now?"])
def test_requests_and_reads_not_questions(t):
    assert not isinstance(one(t), AffordabilityCheck)


@pytest.mark.parametrize("t,amount,ref", [("actually make it 40", D("40"), None), ("no, 40 instead", D("40"), None),
                                          ("change the amount to 40", D("40"), None),
                                          ("send it to mom instead", None, "mom"), ("actually, 40 euros", D("40"), None)])
def test_corrections(t, amount, ref):
    i = one(t)
    assert isinstance(i, Correct) and i.amount == amount and i.beneficiary_ref == ref


@pytest.mark.parametrize("t,scope", [("cancel", "last"), ("cancel that", "last"), ("never mind", "last"),
                                     ("stop", "last"), ("cancel everything", "all")])
def test_cancel(t, scope):
    i = one(t)
    assert isinstance(i, Cancel) and i.scope == scope


@pytest.mark.parametrize("t,amount,cur,ref", [("50", D("50"), None, None), ("50 euros", D("50"), "EUR", None),
                                              ("to mom", None, None, "mom"), ("bob", None, None, "bob")])
def test_provide_detail_with_pending_question(t, amount, cur, ref):
    i = one(t, pq="Who should receive the money, or how much?")
    assert isinstance(i, ProvideDetail) and i.amount == amount and i.currency == cur and i.beneficiary_ref == ref


def test_no_detail_without_pending_question_and_new_request_wins():
    assert isinstance(one("50 euros"), Clarify)
    assert isinstance(one("send 50 euros to mom", pq="How much?"), Pay)
    assert isinstance(one("what's my balance", pq="How much?"), ReadOverview)


def test_regressions_kept():
    assert isinstance(one("pay half of what I owe the landlord"), Clarify)
    assert isinstance(one("no, I want to pay my rent"), PayObligations)
    assert one("Exchange some USD to get 300 EUR.").buy_amount == D("300")


# ------------------------------------------------------------------ ClaudeParser v2
class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [_Block(text)]


class _Client:
    def __init__(self, text=None, exc=None):
        outer = self

        class M:
            def create(self, **kw):
                outer.kw = kw
                if exc:
                    raise exc
                return _Resp(text)
        self.messages = M()


def test_claude_new_intents_and_pending_question_in_prompt():
    c = _Client('{"intents":[{"kind":"provide_detail","amount":"50","currency":"EUR","beneficiary_ref":null}]}')
    r = ClaudeParser(client=c).parse("50 euros", beneficiary_names=["mom"], pending_question="How much to mom?")
    assert r.parser == "claude" and isinstance(r.intents[0], ProvideDetail)
    assert "How much to mom?" in c.kw["messages"][0]["content"]
    assert "affordability_check" in c.kw["system"] and "never pay" in c.kw["system"].lower()
    schema = json.dumps(c.kw["output_config"]["format"]["schema"])
    for kind in ("affordability_check", "provide_detail", "correct", "cancel"):
        assert kind in schema


def test_claude_fallback_uses_pending_question():
    r = ClaudeParser(client=_Client(exc=RuntimeError("x"))).parse(
        "50", beneficiary_names=["mom"], pending_question="How much should I send to mom?")
    assert r.parser == "rules" and isinstance(r.intents[0], ProvideDetail)
