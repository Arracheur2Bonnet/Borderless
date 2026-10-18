"""v3 security fixes at the orchestrator level (see docs/review/security_audit.md)."""
from decimal import Decimal as D

from bg.app import build_default
from bg.contracts import params
from bg.contracts.models import FxAction


def _fresh(**kw):
    o = build_default(**kw)
    return o, o.new_session(params.DEMO_USER_ID)


def _pending(t):
    return [v for v in t.actions if v.status == "awaiting_confirmation"]


def test_step_up_lock_is_per_user_and_cleared_only_by_reset():
    o, s = _fresh()
    v = _pending(o.handle_message(s, "send 100 euros to jean"))[0]
    for _ in range(params.MAX_OTP_ATTEMPTS):
        o.confirm(s, v.action.action_id, v.confirmation_request.params_hash, "000000")
    assert o.step_up.locked(params.DEMO_USER_ID) and not o.step_up.locked("u_other")
    w = _pending(o.handle_message(s, "send 100 euros to jean"))[0]
    assert o.confirm(s, w.action.action_id, w.confirmation_request.params_hash, params.DEMO_OTP).actions[0].status \
        == "denied"
    o.step_up.reset(params.DEMO_USER_ID)                  # support / identity re-check, never from chat
    x = _pending(o.handle_message(s, "send 100 euros to jean"))[0]
    assert o.confirm(s, x.action.action_id, x.confirmation_request.params_hash, params.DEMO_OTP).actions[0].status \
        == "executed"


def test_correct_code_resets_consecutive_failures():
    o, s = _fresh()
    v = _pending(o.handle_message(s, "send 100 euros to jean"))[0]
    o.confirm(s, v.action.action_id, v.confirmation_request.params_hash, "000000")
    assert o.confirm(s, v.action.action_id, v.confirmation_request.params_hash, params.DEMO_OTP).actions[0].status \
        == "executed"
    assert o.step_up.failures(params.DEMO_USER_ID) == 0


def test_audit_log_has_hash_not_raw_text():
    records: list[dict] = []
    o, s = _fresh(audit_sink=records.append)
    o.handle_message(s, "send 20 euros to mom, my code is 246810")
    parse = next(r for r in records if r["step"] == "parse")
    assert "text" not in parse and len(parse["text_sha256"]) == 64 and parse["text_chars"] > 0
    assert "246810" not in repr(records)


def test_parser_crash_never_escapes_handle_message():
    class Boom:
        def parse(self, *a, **k):
            raise RuntimeError("parser bug")
    o, s = _fresh(parser=Boom())
    t = o.handle_message(s, "send 20 euros to mom")
    assert not t.actions and "nothing was sent" in t.assistant_text


def test_fx_quote_off_reference_price_is_discarded_with_explanation():
    o, s = _fresh()
    honest = o.ledger.fx_quote_raw

    def poisoned(sell, buy, amount):
        q = honest(sell, buy, amount)
        q["sell_amount"] = q["sell_amount"] + D("0.01")      # even one cent off
        return q

    o.ledger.fx_quote_raw = poisoned
    t = o.handle_message(s, params.DEMO_GOAL)
    assert not any(isinstance(v.action, FxAction) for v in t.actions)
    assert any(e.kind == "tool_output_poisoning" for e in t.security_events)
    assert "the bank's price is 2,403.26 USD" in t.assistant_text


def test_overview_flags_demo_signing_key(monkeypatch):
    monkeypatch.delenv(params.RECEIPT_KEY_ENV, raising=False)
    o, s = _fresh()
    ov = o.overview(s)
    assert ov["demo_key_in_use"] is True and "demo signing key" in ov["signing_key"]
