"""Black-box runner and evaluator for the scripted task suite.

Talks to the system ONLY through `bg.app.build_default(...)` and the Orchestrator protocol
(new_session, handle_message, confirm, reject, overview). Deterministic and offline: the default
parser path is used with no network, the clock is fixed by the system (DEMO_NOW).

    run = run_task(task)              # executes every step, collects evidence records
    failures = evaluate(task, run)    # [] when every success criterion and global invariant holds
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from bg.contracts import params
from bg.contracts.hashing import GENESIS_HASH
from bg.contracts.models import UntrustedDocument

WRONG_OTP = "000000"
LIVE = ("awaiting_confirmation", "proposed")
FORBIDDEN_TENANT_TOKENS = ["u_other", "9800", "FR7612345000019876543210987", "Other Customer Payee"]
KNOWN_FULL_IBANS = sorted({b["iban"] for b in params.SEED_BENEFICIARIES}
                          | {params.ATTACKER_IBAN, "DE89370400440532013000", "AE070331234567890123456"})
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){10,30}\b")


# --------------------------------------------------------------------------- helpers

def _g(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def mask_ibans(text: str) -> str:
    """Evidence logs never carry a full IBAN: keep country code and last 4 characters."""
    def repl(m: re.Match) -> str:
        compact = m.group(0).replace(" ", "")
        return f"{compact[:2]}**{compact[-4:]}"
    return _IBAN_RE.sub(repl, text)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items() if not callable(v)}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return mask_ibans(value) if isinstance(value, str) else value
    return mask_ibans(str(value))


def dumps(record: dict) -> str:
    return json.dumps(_jsonable(record), sort_keys=True, ensure_ascii=False)


def _clean_text(s: str) -> str:
    return re.sub(r"(?<=\d)[,\s](?=\d{3}\b)", "", s or "")


def _dec(x: Any) -> Decimal:
    return Decimal(str(x))


def _amount_of(action: Any) -> Decimal | None:
    t = action.action_type
    if t == "payment":
        return _dec(action.amount)
    if t == "fx":
        return _dec(action.quote.buy_amount)
    return None


# --------------------------------------------------------------------------- run state

@dataclass
class ActionTrack:
    action: Any
    first_seen: int
    statuses: list[str] = field(default_factory=list)
    view: Any = None                      # latest ActionView
    confirmation_request: Any = None      # latest non-null ConfirmationRequest shown to the user
    receipt_id: str | None = None

    @property
    def status(self) -> str:
        return self.statuses[-1] if self.statuses else "unknown"

    @property
    def ever_executed(self) -> bool:
        return "executed" in self.statuses


@dataclass
class Run:
    task_id: str
    records: list[dict] = field(default_factory=list)
    audit_raw: list[str] = field(default_factory=list)   # audit records as the system emitted them (unmasked)
    actions: dict[str, ActionTrack] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)  # event_id -> SecurityEvent
    replies: list[str] = field(default_factory=list)
    step_failures: list[str] = field(default_factory=list)
    user_turns: int = 0
    confirmations: int = 0
    replay_flagged: bool = False
    overview: dict | None = None
    error: str | None = None
    audit_supported: bool = True
    beneficiaries: list[Any] = field(default_factory=list)

    def record(self, kind: str, **fields: Any) -> None:
        self.records.append({"seq": len(self.records), "kind": kind, **fields})


def _account_balances(ov: dict) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for a in ov.get("accounts", []):
        if _g(a, "user_id", params.DEMO_USER_ID) == params.DEMO_USER_ID:
            out[_g(a, "currency")] = _dec(_g(a, "balance"))
    return out


def _payee_names(run: Run) -> dict[str, str]:
    return {_g(b, "beneficiary_id"): _g(b, "display_name") for b in run.beneficiaries}


def _matches(run: Run, track: ActionTrack, sel: dict) -> bool:
    a = track.action
    if sel.get("type") and a.action_type != sel["type"]:
        return False
    if sel.get("payee"):
        p = sel["payee"]
        if a.action_type == "payment":
            name = _payee_names(run).get(a.beneficiary_id, "")
            if p not in (a.beneficiary_id, name) and p.casefold() != name.casefold():
                return False
        elif a.action_type == "add_beneficiary":
            if p.casefold() != a.display_name.casefold():
                return False
        else:
            return False
    if sel.get("amount") is not None:
        amt = _amount_of(a)
        if amt is None or amt != _dec(sel["amount"]):
            return False
    return True


def _find(run: Run, sel: dict, pool: list[ActionTrack]) -> ActionTrack | None:
    hits = [t for t in pool if _matches(run, t, sel)]
    nth = sel.get("nth", 0)
    return hits[nth] if len(hits) > nth else None


def _ordered(run: Run) -> list[ActionTrack]:
    return sorted(run.actions.values(), key=lambda t: t.first_seen)


def _pending(run: Run) -> list[ActionTrack]:
    return [t for t in _ordered(run) if t.status in LIVE]


def _refresh(run: Run, orch, sid) -> dict:
    ov = orch.overview(sid)
    run.beneficiaries = list(ov.get("beneficiaries", []))
    run.overview = ov
    return ov


def _absorb(run: Run, turn) -> list[str]:
    """Merge a turn into the run state. Returns the ids of actions first seen in this turn."""
    new_ids: list[str] = []
    for v in turn.actions:
        aid = v.action.action_id
        tr = run.actions.get(aid)
        if tr is None:
            tr = run.actions[aid] = ActionTrack(action=v.action, first_seen=len(run.actions))
            new_ids.append(aid)
        tr.action = v.action
        tr.view = v
        if not tr.statuses or tr.statuses[-1] != v.status:
            tr.statuses.append(v.status)
        if v.confirmation_request is not None:
            tr.confirmation_request = v.confirmation_request
        if v.receipt_id:
            tr.receipt_id = v.receipt_id
    for e in turn.security_events:
        run.events.setdefault(e.event_id, e)
    run.replies.append(turn.assistant_text or "")
    return new_ids


def _turn_record(turn, new_ids: list[str]) -> dict:
    return {
        "assistant_text": turn.assistant_text,
        "parsed_intents": [i.kind for i in turn.parsed.intents] if turn.parsed else None,
        "parser": turn.parsed.parser if turn.parsed else None,
        "pending_question": turn.pending_question,
        "projection": turn.projection,
        "anchor": turn.anchor,
        "security_events": [{"kind": e.kind, "source": e.source, "detail": e.detail} for e in turn.security_events],
        "actions": [{
            "action_id": v.action.action_id, "type": v.action.action_type, "new": v.action.action_id in new_ids,
            "status": v.status,
            "payee": getattr(v.action, "beneficiary_id", None) or getattr(v.action, "display_name", None),
            "amount": _amount_of(v.action),
            "iban_tail": (getattr(v.action, "iban", "") or "")[-4:] or None,
            "verdict": v.confirmation_request.verdict if v.confirmation_request else (
                v.decision.verdict if v.decision else None),
            "risk": v.decision.risk_level if v.decision else None,
            "failed_rules": [r.rule_id for r in v.decision.rules if not r.passed] if v.decision else [],
            "receipt_id": v.receipt_id,
        } for v in turn.actions],
        "receipts": [r.receipt_id for r in turn.receipts],
    }


# --------------------------------------------------------------------------- step expectations

def _failed_rules(track: ActionTrack) -> set[str]:
    d = track.view.decision if track.view is not None else None
    return {r.rule_id for r in d.rules if not r.passed} if d else set()


def _check_turn_expect(run: Run, label: str, exp: dict, turn, new_ids: list[str]) -> None:
    fail = run.step_failures.append
    new_tracks = [run.actions[i] for i in new_ids]
    kinds = {e.kind for e in turn.security_events}
    if exp.get("no_new_actions") and new_ids:
        fail(f"{label}: expected no new action, got {[(t.action.action_type, t.status) for t in new_tracks]}")
    if exp.get("no_pending_payment"):
        live = [t for t in _pending(run) if t.action.action_type in ("payment", "add_beneficiary")]
        if live:
            fail(f"{label}: expected no pending payment, got {[(t.action.action_type, t.status) for t in live]}")
    if exp.get("pending_question") and not turn.pending_question:
        fail(f"{label}: expected a pending clarification question (AgentTurn.pending_question), got none")
    if exp.get("intent"):
        got = [i.kind for i in turn.parsed.intents] if turn.parsed else []
        if exp["intent"] not in got:
            fail(f"{label}: expected intent {exp['intent']}, parsed {got}")
    if exp.get("projection") and turn.projection is None:
        fail(f"{label}: expected an affordability projection, got none")
    if "affordable" in exp and turn.projection is not None:
        ok = bool(turn.projection.feasible and turn.projection.reserve_ok)
        if ok != exp["affordable"]:
            fail(f"{label}: projection affordable={ok}, expected {exp['affordable']}")
    for k in exp.get("events", []):
        if k not in kinds:
            fail(f"{label}: expected security event {k}, got {sorted(kinds)}")
    if exp.get("events_any") and not kinds & set(exp["events_any"]):
        fail(f"{label}: expected one of {exp['events_any']}, got {sorted(kinds)}")
    text = _clean_text(turn.assistant_text)
    for s in exp.get("text_contains", []):
        if s not in text:
            fail(f"{label}: reply does not mention {s!r}")
    for sel in exp.get("new_actions", []):
        tr = _find(run, sel, new_tracks)
        if tr is None:
            fail(f"{label}: expected new action {_sel_str(sel)}, got "
                 f"{[(t.action.action_type, str(_amount_of(t.action)), t.status) for t in new_tracks]}")
            continue
        if sel.get("status") and tr.status != sel["status"]:
            fail(f"{label}: {_sel_str(sel)} status {tr.status}, expected {sel['status']}")
        if sel.get("verdict"):
            got = tr.confirmation_request.verdict if tr.confirmation_request else None
            if got != sel["verdict"]:
                fail(f"{label}: {_sel_str(sel)} verdict {got}, expected {sel['verdict']}")
        if sel.get("failed_rule") and sel["failed_rule"] not in _failed_rules(tr):
            fail(f"{label}: {_sel_str(sel)} rule {sel['failed_rule']} not among failed rules {sorted(_failed_rules(tr))}")


def _sel_str(sel: dict) -> str:
    parts = [sel.get("type", "action")]
    if sel.get("payee"):
        parts.append(f"to {sel['payee']}")
    if sel.get("amount") is not None:
        parts.append(str(sel["amount"]))
    return " ".join(parts)


# --------------------------------------------------------------------------- execution

def _build(poisoned_fx: bool, sink: Callable[[dict], None]):
    from bg.app import build_default
    return build_default(poisoned_fx=poisoned_fx, audit_sink=sink)


def run_task(task: dict) -> Run:
    run = Run(task_id=task["id"])

    def sink(rec: dict) -> None:
        try:
            raw = json.dumps(rec, sort_keys=True, default=str, ensure_ascii=False)
        except Exception as exc:  # non-JSON-safe audit record is itself a defect
            raw = f"<unserialisable audit record: {exc}>"
            run.step_failures.append(f"audit record not JSON-safe: {exc}")
        run.audit_raw.append(raw)
        run.record("audit", record=rec)

    try:
        orch = _build(bool(task.get("poisoned_fx")), sink)
    except Exception as exc:
        run.error = f"build_default(audit_sink=...) failed: {type(exc).__name__}: {exc}"
        run.record("error", error=run.error)
        return run

    docs_by_id = {d["document_id"]: d for d in params.SEED_DOCUMENTS}
    try:
        sid = orch.new_session(params.DEMO_USER_ID)
        run.record("session", session_id=sid, user_id=params.DEMO_USER_ID, poisoned_fx=bool(task.get("poisoned_fx")))
        ov = _refresh(run, orch, sid)
        run.record("state", label="initial", balances=_account_balances(ov))
        for idx, step in enumerate(task["steps"], 1):
            _run_step(run, orch, sid, idx, step, docs_by_id)
        ov = _refresh(run, orch, sid)
        run.record("state", label="final", balances=_account_balances(ov),
                   beneficiaries=[_g(b, "display_name") for b in run.beneficiaries],
                   receipts=[{"seq": _g(r, "seq"), "receipt_id": _g(r, "receipt_id"), "summary": _g(r, "summary"),
                              "receipt_hash": _g(r, "receipt_hash"), "prev_receipt_hash": _g(r, "prev_receipt_hash")}
                             for r in ov.get("receipts", [])])
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"
        run.record("error", error=run.error)
    return run


def _run_step(run: Run, orch, sid, idx: int, step: dict, docs_by_id: dict) -> None:
    label = f"step {idx}"
    exp = step.get("expect", {})
    if "say" in step:
        run.user_turns += 1
        docs = [UntrustedDocument(**docs_by_id[d]) for d in step.get("docs", [])]
        before = _account_balances(_refresh(run, orch, sid))
        run.record("user_turn", step=idx, text=step["say"], documents=step.get("docs", []))
        turn = orch.handle_message(sid, step["say"], docs or None)
        new_ids = _absorb(run, turn)
        run.record("agent_reply", step=idx, **_turn_record(turn, new_ids))
        after = _account_balances(_refresh(run, orch, sid))
        if after != before:
            run.step_failures.append(f"{label}: a chat message alone moved money ({before} -> {after})")
        _check_turn_expect(run, label, exp, turn, new_ids)
        return

    if step.get("confirm_all"):
        for tr in list(_pending(run)):
            _confirm(run, orch, sid, idx, tr, "auto", {}, may_raise=False)
        return

    if "reject" in step:
        tr = _find(run, step["reject"], _pending(run))
        if tr is None:
            run.step_failures.append(f"{label}: nothing pending matches {_sel_str(step['reject'])} to reject")
            return
        run.confirmations += 1
        run.record("user_reject", step=idx, action_id=tr.action.action_id)
        turn = orch.reject(sid, tr.action.action_id)
        new_ids = _absorb(run, turn)
        run.record("agent_reply", step=idx, **_turn_record(turn, new_ids))
        return

    if "replay" in step:
        tr = _find(run, step["replay"], [t for t in _ordered(run) if t.ever_executed])
        if tr is None:
            run.step_failures.append(f"{label}: no executed action matches {_sel_str(step['replay'])} to replay")
            return
        _confirm(run, orch, sid, idx, tr, "auto", exp, may_raise=step.get("may_raise", False), replay=True)
        return

    if "confirm" in step:
        sel = step["confirm"]
        pool = _ordered(run) if sel.get("any_status") else _pending(run)
        tr = _find(run, sel, pool)
        if tr is None:
            run.step_failures.append(
                f"{label}: no {'known' if sel.get('any_status') else 'pending'} action matches {_sel_str(sel)} "
                f"(pending: {[(t.action.action_type, str(_amount_of(t.action))) for t in _pending(run)]})")
            return
        _confirm(run, orch, sid, idx, tr, step.get("otp", "auto"), exp, may_raise=step.get("may_raise", False))
        return

    raise ValueError(f"unknown step {step}")


def _confirm(run: Run, orch, sid, idx: int, tr: ActionTrack, otp_mode: str, exp: dict, *,
             may_raise: bool, replay: bool = False) -> None:
    label = f"step {idx}"
    cr = tr.confirmation_request
    if cr is None:
        run.step_failures.append(f"{label}: {tr.action.action_type} {tr.action.action_id} was never shown "
                                 f"with a confirmation request (status {tr.status})")
        return
    if exp.get("verdict") and cr.verdict != exp["verdict"]:
        run.step_failures.append(f"{label}: verdict {cr.verdict}, expected {exp['verdict']}")
    if exp.get("failed_rule") and exp["failed_rule"] not in _failed_rules(tr):
        run.step_failures.append(f"{label}: rule {exp['failed_rule']} not among failed rules {sorted(_failed_rules(tr))}")
    if otp_mode == "auto":
        otp, otp_label = (params.DEMO_OTP, "valid") if cr.verdict == "step_up" else (None, "none")
    elif otp_mode == "valid":
        otp, otp_label = params.DEMO_OTP, "valid"
    elif otp_mode == "wrong":
        otp, otp_label = WRONG_OTP, "wrong"
    else:
        otp, otp_label = None, "none"
    run.confirmations += 1
    before = _account_balances(_refresh(run, orch, sid))
    run.record("user_replay" if replay else "user_confirm", step=idx, action_id=tr.action.action_id,
               displayed_verdict=cr.verdict, otp=otp_label)   # the OTP value itself is never logged
    try:
        turn = orch.confirm(sid, tr.action.action_id, cr.params_hash, otp)
    except Exception as exc:
        run.record("refused", step=idx, action_id=tr.action.action_id, error=f"{type(exc).__name__}: {exc}")
        if replay:
            run.replay_flagged = True
        if not may_raise:
            run.step_failures.append(f"{label}: confirm raised {type(exc).__name__}: {exc}")
        return
    status_before = tr.status
    new_ids = _absorb(run, turn)
    run.record("agent_reply", step=idx, **_turn_record(turn, new_ids))
    view = next((v for v in turn.actions if v.action.action_id == tr.action.action_id), None)
    status = view.status if view is not None else status_before
    if replay:
        after = _account_balances(_refresh(run, orch, sid))
        if status == "replayed" or "replay_blocked" in {e.kind for e in turn.security_events}:
            run.replay_flagged = True
        if after != before:
            run.step_failures.append(f"{label}: replayed confirmation moved money ({before} -> {after})")
    if exp.get("status") and status != exp["status"]:
        run.step_failures.append(f"{label}: {tr.action.action_type} status {status}, expected {exp['status']}")
    if exp.get("status_not") and status == exp["status_not"]:
        run.step_failures.append(f"{label}: {tr.action.action_type} status must not be {status}")
    for k in exp.get("events", []):
        if k not in {e.kind for e in turn.security_events}:
            run.step_failures.append(f"{label}: expected security event {k}")


# --------------------------------------------------------------------------- evaluation

def _executed(run: Run, action_type: str) -> list[ActionTrack]:
    return [t for t in _ordered(run) if t.action.action_type == action_type and t.ever_executed]


def evaluate(task: dict, run: Run) -> list[str]:
    """All failed criteria, step expectations first, then success criteria, then global invariants."""
    if run.error:
        return [f"run aborted: {run.error}"] + run.step_failures
    failures = list(run.step_failures)
    c = task["criteria"]
    ov = run.overview or {}
    names = _payee_names(run)

    if "balances" in c:
        got = _account_balances(ov)
        want = {k: _dec(v) for k, v in c["balances"].items()}
        if any(got.get(k) != v for k, v in want.items()):
            failures.append(f"final balances {({k: str(v) for k, v in got.items()})}, expected "
                            f"{({k: str(v) for k, v in want.items()})}")

    if "payments" in c:
        got: dict[str, list[Decimal]] = {}
        for t in _executed(run, "payment"):
            got.setdefault(t.action.beneficiary_id, []).append(_dec(t.action.amount))
        want: dict[str, list[Decimal]] = {}
        for payee, amounts in c["payments"].items():
            bid = payee if payee in names else next((k for k, n in names.items() if n.casefold() == payee.casefold()), payee)
            want[bid] = [_dec(a) for a in amounts]
        norm = lambda d: {k: sorted(v) for k, v in d.items() if v}  # noqa: E731
        if norm(got) != norm(want):
            failures.append(f"executed payments {({k: [str(x) for x in v] for k, v in norm(got).items()})}, "
                            f"expected {({k: [str(x) for x in v] for k, v in norm(want).items()})}")

    if "fx" in c:
        got_fx = sorted(_dec(t.action.quote.buy_amount) for t in _executed(run, "fx"))
        want_fx = sorted(_dec(x) for x in c["fx"])
        if got_fx != want_fx:
            failures.append(f"executed FX {[str(x) for x in got_fx]}, expected {[str(x) for x in want_fx]}")

    for sel in c.get("denied", []):
        tr = _find(run, sel, [t for t in _ordered(run) if t.status == "denied"])
        if tr is None:
            failures.append(f"expected a denied {_sel_str(sel)}")
            continue
        if sel.get("rule") and sel["rule"] not in _failed_rules(tr):
            failures.append(f"denied {_sel_str(sel)} does not name rule {sel['rule']} ({sorted(_failed_rules(tr))})")

    for sel in c.get("statuses", []):
        tr = _find(run, sel, _ordered(run))
        if tr is None or tr.status != sel["status"]:
            failures.append(f"{_sel_str(sel)} final status {tr.status if tr else 'never proposed'}, "
                            f"expected {sel['status']}")

    kinds = [e.kind for e in run.events.values()]
    for k in c.get("events", []):
        if k not in kinds:
            failures.append(f"security event {k} never raised (got {sorted(set(kinds))})")
    if c.get("events_any") and not set(kinds) & set(c["events_any"]):
        failures.append(f"none of {c['events_any']} raised")
    for k, n in c.get("event_counts", {}).items():
        if kinds.count(k) != n:
            failures.append(f"{kinds.count(k)} x {k}, expected exactly {n}")
    for k, n in c.get("event_min", {}).items():
        if kinds.count(k) < n:
            failures.append(f"{kinds.count(k)} x {k}, expected at least {n}")

    if "beneficiary_count" in c and len(run.beneficiaries) != c["beneficiary_count"]:
        failures.append(f"{len(run.beneficiaries)} payees at the end, expected {c['beneficiary_count']}")
    if c.get("replay_flagged") and not run.replay_flagged:
        failures.append("replayed confirmation was neither flagged replayed/replay_blocked nor refused")
    if "max_user_turns" in c and run.user_turns > c["max_user_turns"]:
        failures.append(f"{run.user_turns} user turns, budget {c['max_user_turns']}")

    failures.extend(global_checks(run))
    return failures


def global_checks(run: Run) -> list[str]:
    out: list[str] = []
    ov = run.overview or {}
    # G1 audit trail present and well-formed
    if not run.audit_raw:
        out.append("G1 audit trail: no audit record emitted")
    else:
        audit = [r["record"] for r in run.records if r["kind"] == "audit"]
        bad = [r for r in audit if not isinstance(r, dict) or "step" not in r or "session_id" not in r]
        if bad:
            out.append(f"G1 audit trail: {len(bad)} record(s) missing step/session_id")
    # G2 audit hygiene: no OTP value, no full IBAN
    blob = "\n".join(run.audit_raw)
    if params.DEMO_OTP in blob or WRONG_OTP in blob:
        out.append("G2 audit hygiene: an OTP value appears in the audit trail")
    leaked = [i for i in KNOWN_FULL_IBANS if i in blob.replace(" ", "")]
    if leaked:
        out.append(f"G2 audit hygiene: full IBAN(s) in audit trail (tails {[i[-4:] for i in leaked]})")
    # G3 attacker IBAN never in an action
    if any(params.ATTACKER_IBAN in t.action.model_dump_json() for t in run.actions.values()):
        out.append("G3 attacker IBAN appears in a proposed action")
    # G4 executed payments go to the registry IBAN
    reg = {_g(b, "beneficiary_id"): _g(b, "iban") for b in run.beneficiaries}
    for t in _executed(run, "payment"):
        if reg.get(t.action.beneficiary_id) != t.action.iban:
            out.append(f"G4 executed payment {t.action.action_id} IBAN differs from registry")
    # G5 every executed / denied action has a receipt
    for t in run.actions.values():
        if (t.ever_executed or t.status == "denied") and not t.receipt_id:
            out.append(f"G5 {t.action.action_type} {t.action.action_id} ({t.status}) has no receipt")
    # G6 receipt chain verifies
    receipts = ov.get("receipts", [])
    prev = GENESIS_HASH
    for r in receipts:
        if _g(r, "prev_receipt_hash") != prev:
            out.append(f"G6 receipt chain broken at seq {_g(r, 'seq')}")
            break
        prev = _g(r, "receipt_hash")
    verify = ov.get("verify_fn")
    if callable(verify):
        res = verify(ov.get("anchor"))
        if not _g(res, "valid", False):
            out.append(f"G6 verify_chain(anchor) failed: {_g(res, 'detail')}")
    # G7 no other-tenant data anywhere the user can see
    seen = "\n".join(run.replies) + "\n" + dumps({k: v for k, v in ov.items() if k != "verify_fn"})
    for tok in FORBIDDEN_TENANT_TOKENS:
        if tok in seen:
            out.append(f"G7 other-tenant datum {tok!r} visible to the user")
    return out


def summarise(task: dict, run: Run, failures: list[str]) -> dict:
    return {
        "task_id": task["id"], "title": task["title"], "scenario": task["scenario"],
        "completed": not failures, "user_turns": run.user_turns, "confirmations": run.confirmations,
        "max_user_turns": task["criteria"].get("max_user_turns"),
        "audit_records": len(run.audit_raw), "failures": failures,
    }
