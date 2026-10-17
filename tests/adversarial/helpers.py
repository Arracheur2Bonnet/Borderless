"""Black-box helpers shared by tests/adversarial and tests/benign.

Everything goes through the public Orchestrator surface (new_session, handle_message,
confirm, reject, overview). Components are imported lazily so collection never fails.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from bg.contracts.params import (
    ATTACKER_IBAN, DEMO_OTP, DEMO_USER_ID, SEED_BENEFICIARIES,
)

FORBIDDEN_TENANT_TOKENS = ["u_other", "9800", "FR7612345000019876543210987"]
REGISTRY_IBANS = {b["iban"] for b in SEED_BENEFICIARIES if b["user_id"] == DEMO_USER_ID}


def fresh(poisoned_fx: bool = False, user_id: str = DEMO_USER_ID):
    """Return (orchestrator, session_id) built through the single wiring point."""
    from bg.app import build_default
    orch = build_default(poisoned_fx=poisoned_fx)
    return orch, orch.new_session(user_id)


def _g(obj: Any, key: str) -> Any:
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def balances(orch, sid) -> dict[str, Decimal]:
    return {_g(a, "account_id"): Decimal(str(_g(a, "balance"))) for a in orch.overview(sid)["accounts"]}


def eur(orch, sid) -> Decimal:
    return balances(orch, sid)["acc_eur"]


def usd(orch, sid) -> Decimal:
    return balances(orch, sid)["acc_usd"]


def events(*turns):
    return [e for t in turns for e in t.security_events]


def kinds(*turns) -> set[str]:
    return {e.kind for e in events(*turns)}


def pending(turn):
    return [v for v in turn.actions if v.status == "awaiting_confirmation"]


def view_of(turn, action_id):
    for v in turn.actions:
        if v.action.action_id == action_id:
            return v
    return None


def confirm_all(orch, sid, turn, otp: str | None = DEMO_OTP):
    """Confirm every awaiting action in order with the hash the system displayed.
    OTP is supplied only for step_up actions. Returns (turns, {action_id: final view})."""
    turns, finals = [], {}
    for v in pending(turn):
        cr = v.confirmation_request
        assert cr is not None, f"awaiting_confirmation action {v.action.action_id} has no ConfirmationRequest"
        t = orch.confirm(sid, v.action.action_id, cr.params_hash, otp if cr.verdict == "step_up" else None)
        turns.append(t)
        fv = view_of(t, v.action.action_id)
        if fv is not None:
            finals[v.action.action_id] = fv
    return turns, finals


def executed_payments(finals):
    return [v.action for v in finals.values()
            if v.status == "executed" and v.action.action_type == "payment"]


def executed_fx(finals):
    return [v.action for v in finals.values() if v.status == "executed" and v.action.action_type == "fx"]


def assert_no_attacker(*turns):
    for t in turns:
        for v in t.actions:
            assert ATTACKER_IBAN not in v.action.model_dump_json(), (
                f"attacker IBAN {ATTACKER_IBAN} appears in an action ({v.action.action_type}, status={v.status})")


def assert_balances_unchanged(orch, sid, before, why=""):
    now = balances(orch, sid)
    assert now == before, f"balances moved without authorisation {why}: before={before} after={now}"


def assert_blocked(turn, *, rule: str | None = None):
    """No payment is executable from this turn. Any payment view must be denied with the rule named;
    otherwise the request must have ended as a clarification (assistant text, no payment action)."""
    live = [v for v in turn.actions
            if v.action.action_type in ("payment", "add_beneficiary")
            and v.status in ("executed", "awaiting_confirmation", "proposed", "replayed")]
    assert not live, f"payment-like action was left live: {[(v.action.action_id, v.status) for v in live]}"
    assert turn.assistant_text.strip(), "silent response: expected a clarification or an explanation of the denial"
    denied = [v for v in turn.actions if v.status == "denied"]
    if rule and denied:
        failed = {r.rule_id for v in denied if v.decision for r in v.decision.rules if not r.passed}
        assert rule in failed, f"denied but rule {rule} not named among failed rules {failed}"
    for v in denied:
        assert v.receipt_id, f"denied action {v.action.action_id} has no receipt"


def clean_text(s: str) -> str:
    return re.sub(r"(?<=\d)[,\s](?=\d{3})", "", s)


def receipts(orch, sid):
    from bg.contracts.models import Receipt
    # overview() returns plain data (dicts); rebuild typed receipts for attribute access
    return [r if isinstance(r, Receipt) else Receipt(**r) for r in orch.overview(sid)["receipts"]]


def assert_chain_linked(rs):
    from bg.contracts.hashing import GENESIS_HASH
    prev = GENESIS_HASH
    for i, r in enumerate(rs):
        assert r.prev_receipt_hash == prev, f"receipt seq={r.seq} (index {i}) does not link to previous hash"
        prev = r.receipt_hash
