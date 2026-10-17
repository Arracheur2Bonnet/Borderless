"""Component interfaces. FROZEN.

Each workstream implements exactly one of these. Code against the Protocol,
not against another workstream's internals.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel

from .models import (
    Account, AgentTurn, Beneficiary, ChainVerification, Confirmation, DocumentScan,
    ExecutionResult, FinancialIntent, Obligation, ParsedRequest, PolicyContext,
    PolicyDecision, ProposedAction, Receipt, SecurityEvent, SimulationResult,
    UntrustedDocument,
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


class ReceiptStore(Protocol):
    """Append-only Proof-of-Intent hash chain. Owner: ledger workstream (bg/ledger)."""

    def append(self, *, action_id: str, intent: BaseModel | dict, action: BaseModel,
               simulation: BaseModel | None, decision: BaseModel, confirmation: BaseModel | None,
               result: BaseModel, summary: str) -> Receipt: ...
    def list(self, user_id: str | None = None) -> list[Receipt]: ...
    def get(self, receipt_id: str) -> Receipt | None: ...
    def verify_chain(self) -> ChainVerification: ...


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

    def parse(self, text: str, *, beneficiary_names: list[str]) -> ParsedRequest: ...


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
