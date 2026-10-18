"""Helpers for the white-box security audit (docs/review/security_audit.md)."""
from __future__ import annotations

from decimal import Decimal

from bg.app import build_default
from bg.contracts.models import ParsedRequest
from bg.contracts.params import DEMO_USER_ID


class ScriptedParser:
    """Stands in for a compromised or simply creative Claude parser: returns fixed typed intents.
    The security model (self-assessment section 4) says the system must stay safe whatever it returns."""

    def __init__(self, *intents):
        self.intents = list(intents)

    def parse(self, text, *, beneficiary_names, pending_question=None):
        return ParsedRequest(intents=self.intents, parser="claude", raw_text=text)


def fresh(parser=None, user_id: str = DEMO_USER_ID, **kw):
    orch = build_default(parser=parser, **kw)
    return orch, orch.new_session(user_id)


def pending(turn):
    return [v for v in turn.actions if v.status == "awaiting_confirmation"]


def balances(orch, sid) -> dict[str, Decimal]:
    return {a["account_id"]: Decimal(str(a["balance"])) for a in orch.overview(sid)["accounts"]}


def fund(orch, account_id: str, amount: str) -> None:
    """Test setup only: give an account more money so limits (not funds) are what binds."""
    led = orch.ledger
    led._accounts[account_id] = led._accounts[account_id].model_copy(update={"balance": Decimal(amount)})
