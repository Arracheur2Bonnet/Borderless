"""Component interfaces. FROZEN.

Each workstream implements exactly one of these. Code against the Protocol,
not against another workstream's internals.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel

from datetime import datetime

from .models import (
    Account, AgentTurn, Beneficiary, ChainVerification, Confirmation, DocumentScan,
    ExecutionResult, FinancialIntent, Obligation, ParsedRequest, PolicyContext,
    PolicyDecision, ProposedAction, Receipt, ReceiptAnchor, RecentPayment, SecurityEvent,
    SimulationResult, UntrustedDocument,
)


class Ledger(Protocol):
    """Sandbox core banking. Owner: ledger workstream (bg/ledger)."""

    def get_accounts(self, user_id: str) -> list[Account]: ...
    def get_beneficiaries(self, user_id: str) -> list[Beneficiary]: ...
    def get_obligations(self, user_id: str) -> list[Obligation]: ...
    def get_document(self, user_id: str, document_id: str) -> UntrustedDocument | None: ...

    def resolve_beneficiary(self, user_id: str, ref: str) -> Beneficiary | None:
        """Deterministic: exact id, then exact display name, then exact alias (case-insensitive).
        Returns None on no match OR on more than one match. Never fuzzy."""
        ...

    def fx_quote_raw(self, sell_currency: str, buy_currency: str, buy_amount: Decimal) -> dict[str, Any]:
        """Raw response of the (simulated) external FX provider. UNTRUSTED: may contain
        extra fields such as a 'note' with injected text. Must go through ContentGuard."""
        ...

    def simulate(self, user_id: str, actions: list[ProposedAction]) -> list[SimulationResult]:
        """Sequential and cumulative: action N is simulated on the state after actions 0..N-1.
        Pure: never mutates state."""
        ...

    def execute(self, user_id: str, action: ProposedAction, confirmation: Confirmation | None) -> ExecutionResult:
        """Idempotent on action.idempotency_key: a second call with the same key returns the
        first result with status 'replayed' and does not move money again. Same key with
        different params -> status 'failed', error 'IDEMPOTENCY_CONFLICT'.
        The ledger trusts the caller for authorisation (the policy gate runs before)."""
        ...

    def executed_today_eur(self, user_id: str) -> Decimal: ...
    def eur_equivalent(self, amount: Decimal, currency: str) -> Decimal: ...

    def recent_payments(self, user_id: str, since: datetime) -> list[RecentPayment]:
        """Executed outgoing payments (not FX) at or after `since`, oldest first. Tenant-scoped."""
        ...

    def account_holder(self, iban: str) -> str | None:
        """v8 (Verification of Payee): registered holder name of the account, or None when the bank has no record
        (outcome "not available"). Bank-wide directory, not customer data."""
        ...

    def holder_directory(self) -> dict[str, str]:
        """v8: copy of the whole directory, normalised IBAN -> holder name (passed to the policy context)."""
        ...


class ReceiptStore(Protocol):
    """Append-only Proof-of-Intent hash chain. Owner: ledger workstream (bg/ledger)."""

    def append(self, *, action_id: str, intent: BaseModel | dict, action: BaseModel,
               simulation: BaseModel | None, decision: BaseModel, confirmation: BaseModel | None,
               result: BaseModel, summary: str) -> Receipt: ...
    def list(self, user_id: str | None = None) -> list[Receipt]: ...
    def get(self, receipt_id: str) -> Receipt | None: ...

    def verify_chain(self, anchor: ReceiptAnchor | None = None) -> ChainVerification:
        """Recompute every link. With an anchor (customer-held copy of an earlier head): also check
        its signature, that receipt `anchor.seq` still exists and still has `anchor.receipt_hash`.
        That detects truncation and full rewrites that the chain alone cannot."""
        ...

    def anchor(self) -> ReceiptAnchor | None:
        """Signed anchor for the current head (None when the chain is empty)."""
        ...


class PolicyEngine(Protocol):
    """Deterministic risk gate. No LLM calls. Owner: policy workstream (bg/policy)."""

    def evaluate(self, ctx: PolicyContext, action: ProposedAction,
                 simulation: SimulationResult) -> PolicyDecision: ...


class ContentGuard(Protocol):
    """Trust-boundary filter for untrusted text and tool outputs. Owner: policy workstream."""

    def scan_document(self, doc: UntrustedDocument) -> DocumentScan: ...

    def scan_user_message(self, text: str) -> list[SecurityEvent]:
        """Flags jailbreak / data-exfiltration attempts in the user's own message.
        Detection only: the policy gate is what actually blocks."""
        ...

    def sanitize_tool_output(self, tool_name: str, payload: dict[str, Any],
                             schema: type[BaseModel]) -> tuple[BaseModel | None, list[SecurityEvent]]:
        """Strict parse against schema: unknown fields dropped and reported, invalid -> None."""
        ...


class IntentParser(Protocol):
    """Natural language -> typed intents. Owner: agent workstream (bg/agent)."""

    def parse(self, text: str, *, beneficiary_names: list[str],
              pending_question: str | None = None) -> ParsedRequest:
        """`pending_question` is the agent's last unanswered clarification, so a bare '50 euros'
        can be read as ProvideDetail. Implementations must accept and may ignore it."""
        ...


class Orchestrator(Protocol):
    """The agent loop the UI talks to. Owner: agent workstream (bg/agent)."""

    def new_session(self, user_id: str) -> str: ...
    def handle_message(self, session_id: str, text: str,
                       documents: list[UntrustedDocument] | None = None) -> AgentTurn: ...
    def confirm(self, session_id: str, action_id: str, params_hash: str,
                otp: str | None = None) -> AgentTurn: ...
    def reject(self, session_id: str, action_id: str) -> AgentTurn: ...
    def overview(self, session_id: str) -> dict[str, Any]:
        """{'accounts': [...], 'beneficiaries': [...], 'obligations': [...], 'receipts': [...]}"""
        ...
