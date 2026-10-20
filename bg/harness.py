"""Scripted-task harness (v7, P1-05): one JSON-in / JSON-out interface to the orchestrator.

Used by `scripts/agent_cli.py` (JSONL tasks, interactive chat) and `bg/http_api.py` (localhost HTTP). It adds no
behaviour: every operation goes through the same `Orchestrator` as the UI (parser -> grounding -> policy engine ->
sandbox ledger), so the principle holds here too: the harness only carries text, a confirmation and an optional
one-time code in, and structured facts out. The model never sees this layer.

Task line (JSONL):
    {"session": "s1", "user": "u_lina", "op": "message", "text": "Pay my tuition and rent, keep 600 EUR"}
    {"session": "s1", "op": "confirm", "action": "last", "otp": "246810"}
    {"session": "s1", "op": "reject", "action": "<action_id>"}
    {"session": "s1", "op": "overview"}

`"action": "last"` (alias `"next"`) means the next step still waiting for approval in the most recent proposed plan,
in the order the plan must be approved (the exchange first, then the payments). An explicit action id is also accepted.
Output records never contain an OTP, a full IBAN or a secret.
"""
from __future__ import annotations

import hmac
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from bg.contracts import params
from bg.contracts.models import ActionView, AgentTurn

MAX_TEXT_CHARS = 20_000     # transport bound; the orchestrator itself replies "too long" above MAX_MESSAGE_CHARS
MAX_OTP_CHARS = 32
OPS = ("message", "confirm", "reject", "overview")
KNOWN_USERS = tuple(dict.fromkeys(a["user_id"] for a in params.SEED_ACCOUNTS))


class HarnessError(Exception):
    """A request the harness refuses before it reaches the orchestrator. `status` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class _Sess:
    sid: str
    user: str
    pending: dict[str, str] = field(default_factory=dict)      # action_id -> params_hash, still awaiting approval
    hashes: dict[str, str] = field(default_factory=dict)       # every action_id seen -> params_hash
    latest_plan: list[str] = field(default_factory=list)       # action ids of the latest proposal, in approval order
    titles: dict[str, str] = field(default_factory=dict)       # action_id -> card title


def _mask_iban(iban: str) -> str:
    return ("****" + iban[-4:]) if iban else ""


def _money(x: Any) -> str:
    return f"{Decimal(str(x)):.2f}"


def _view(v: ActionView, titles: dict[str, str]) -> dict[str, Any]:
    d, cr = v.decision, v.confirmation_request
    return {
        "id": v.action.action_id,
        "type": v.action.action_type,
        "title": cr.title if cr else titles.get(v.action.action_id),
        "status": v.status,
        "risk": d.risk_level.value if d else None,
        "verdict": d.verdict.value if d else None,
        "failed_rules": [r.rule_id for r in d.rules if not r.passed] if d else [],
        "needs_otp": bool(d and d.verdict.value == "step_up"),
        "receipt_id": v.receipt_id,
        # the hash that binds an approval to this exact action; only while it waits for approval
        "params_hash": cr.params_hash if (cr and v.status == "awaiting_confirmation") else None,
    }


class Harness:
    """Sessions and operations over one in-memory sandbox (ledger, receipts, policy). Thread-safe (one lock)."""

    def __init__(self, parser: Any = None, poisoned_fx: bool = False, orchestrator: Any = None) -> None:
        if orchestrator is None:
            from bg.agent import RulesParser
            from bg.app import build_default
            # deterministic and offline unless a parser is passed: BG_PARSER in the environment is ignored here
            orchestrator = build_default(poisoned_fx=poisoned_fx, parser=parser or RulesParser())
        self.orch = orchestrator
        self.parser_label = getattr(parser, "parser_tag", None) or ("rules" if parser is None else type(parser).__name__)
        self._sessions: dict[str, _Sess] = {}
        self._labels: dict[str, str] = {}              # task-file session label -> session id (CLI)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ sessions
    def open_session(self, user: str | None = None) -> dict[str, Any]:
        user = user or params.DEMO_USER_ID
        if not isinstance(user, str) or user not in KNOWN_USERS:
            raise HarnessError(f"unknown user (known: {', '.join(KNOWN_USERS)})", 400)
        with self._lock:
            sid = self.orch.new_session(user)
            self._sessions[sid] = _Sess(sid=sid, user=user)
        return {"session": sid, "user": user}

    def _get(self, sid: Any) -> _Sess:
        if not isinstance(sid, str) or sid not in self._sessions:
            raise HarnessError("unknown session", 404)
        return self._sessions[sid]

    # ------------------------------------------------------------------ operations
    def message(self, sid: Any, text: Any) -> dict[str, Any]:
        if not isinstance(text, str):
            raise HarnessError("'text' must be a string", 400)
        if len(text) > MAX_TEXT_CHARS:
            raise HarnessError(f"'text' is longer than {MAX_TEXT_CHARS} characters", 413)
        with self._lock:
            s = self._get(sid)
            turn = self.orch.handle_message(sid, text)
            plan = [v.action.action_id for v in turn.actions if v.status == "awaiting_confirmation"
                    and v.confirmation_request is not None]
            self._track(s, turn)
            if plan:
                s.latest_plan = plan
            return self._record(s, "message", turn)

    def confirm(self, sid: Any, action: Any = "last", otp: Any = None, params_hash: Any = None) -> dict[str, Any]:
        """Approve a pending action. When the caller passes `params_hash` (the value shown with the action), it must
        equal the hash the server holds, so the approval is bound to the exact action the caller saw (the HTTP API
        requires it; the scripted CLI replay does not pass it)."""
        if otp is not None and (not isinstance(otp, str) or len(otp) > MAX_OTP_CHARS):
            raise HarnessError("'otp' must be a short string", 400)
        if params_hash is not None and (not isinstance(params_hash, str) or len(params_hash) > 128):
            raise HarnessError("'params_hash' must be the hash shown with the action", 400)
        with self._lock:
            s = self._get(sid)
            aid = self._resolve(s, action)
            if params_hash is not None and not hmac.compare_digest(params_hash.encode("utf-8"), s.hashes.get(aid, "").encode("utf-8")):
                raise HarnessError("the approval does not match the action it was shown for; nothing was done", 409)
            turn = self.orch.confirm(sid, aid, s.hashes.get(aid, ""), otp)
            self._track(s, turn)
            return self._record(s, "confirm", turn)

    def reject(self, sid: Any, action: Any = "last") -> dict[str, Any]:
        with self._lock:
            s = self._get(sid)
            aid = self._resolve(s, action)
            turn = self.orch.reject(sid, aid)
            self._track(s, turn)
            return self._record(s, "reject", turn)

    def overview(self, sid: Any) -> dict[str, Any]:
        with self._lock:
            s = self._get(sid)
            ov = self.orch.overview(sid)
            verdict = ov["verify_fn"](None)
            return {
                "session": sid, "user": s.user, "op": "overview", "ok": True,
                "balances": {a["currency"]: _money(a["balance"]) for a in ov["accounts"]},
                "beneficiaries": [{"id": b["beneficiary_id"], "name": b["display_name"], "iban": _mask_iban(b["iban"]),
                                   "category": b["category"]} for b in ov["beneficiaries"]],
                "obligations": [{"id": o["obligation_id"], "kind": o["kind"], "amount": _money(o["amount"]),
                                 "currency": o["currency"], "due": str(o["due_date"])} for o in ov["obligations"]],
                "receipts": [{"id": r["receipt_id"], "seq": r["seq"], "summary": r["summary"],
                              "hash": r["receipt_hash"][:16]} for r in ov["receipts"]],
                "receipt_ids": [r["receipt_id"] for r in ov["receipts"]],
                "chain_valid": bool(verdict.valid), "chain_length": verdict.length,
                "pending_actions": list(s.pending),
            }

    # ------------------------------------------------------------------ task lines (CLI)
    def run_task(self, task: Any) -> dict[str, Any]:
        """One JSONL task. Never raises: a bad task becomes {"ok": false, "error": ...}."""
        label = task.get("session") if isinstance(task, dict) else None
        op = task.get("op") if isinstance(task, dict) else None
        try:
            if not isinstance(task, dict):
                raise HarnessError("a task must be a JSON object")
            if op not in OPS:
                raise HarnessError(f"unknown op {op!r} (use one of {', '.join(OPS)})")
            if not isinstance(label, str) or not label:
                raise HarnessError("'session' must be a non-empty string")
            sid = self._labels.get(label)
            if sid is None:
                sid = self.open_session(task.get("user"))["session"]
                self._labels[label] = sid
            elif task.get("user") not in (None, self._sessions[sid].user):
                raise HarnessError(f"session {label!r} belongs to {self._sessions[sid].user}")
            if op == "message":
                out = self.message(sid, task.get("text", ""))
            elif op == "confirm":
                out = self.confirm(sid, task.get("action", "last"), task.get("otp"))
            elif op == "reject":
                out = self.reject(sid, task.get("action", "last"))
            else:
                out = self.overview(sid)
            out["session"] = label
            return out
        except HarnessError as exc:
            return {"session": label, "op": op, "ok": False, "error": str(exc)}

    # ------------------------------------------------------------------ internals
    def _resolve(self, s: _Sess, action: Any) -> str:
        if action in (None, "last", "next"):
            for aid in s.latest_plan:
                if aid in s.pending:
                    return aid
            if s.pending:
                return next(reversed(s.pending))
            raise HarnessError("no action is waiting for approval in this session", 409)
        if not isinstance(action, str) or not action or len(action) > 128:
            raise HarnessError("'action' must be 'last' or an action id", 400)
        if action not in s.hashes:
            raise HarnessError("unknown action for this session", 404)
        return action

    @staticmethod
    def _track(s: _Sess, turn: AgentTurn) -> None:
        for v in turn.actions:
            aid = v.action.action_id
            if v.confirmation_request is not None:
                s.hashes[aid] = v.confirmation_request.params_hash
                s.titles[aid] = v.confirmation_request.title
            if v.status == "awaiting_confirmation" and v.confirmation_request is not None:
                s.pending[aid] = v.confirmation_request.params_hash
            else:
                s.pending.pop(aid, None)

    def _record(self, s: _Sess, op: str, turn: AgentTurn) -> dict[str, Any]:
        ov = self.orch.overview(s.sid)
        return {
            "session": s.sid, "user": s.user, "op": op, "ok": True,
            "assistant_text": turn.assistant_text,
            "actions": [_view(v, s.titles) for v in turn.actions],
            "events": [{"kind": e.kind, "source": e.source, "detail": e.detail} for e in turn.security_events],
            "balances": {a["currency"]: _money(a["balance"]) for a in ov["accounts"]},
            "receipts": [r.receipt_id for r in turn.receipts],
            "receipt_total": len(ov["receipts"]),
            "pending_question": turn.pending_question,
            "pending_actions": list(s.pending),
            # who read the message: "rules", "local", "connected:<host>", "cascade:<tier>"; the signed receipts do not say
            "parser": turn.parsed.parser if turn.parsed is not None else None,
        }
