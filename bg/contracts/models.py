"""Shared data contracts for BorderlessGuard.

FROZEN. Owned by the architect. Workstream agents import from here and never edit it.
If a contract is wrong or missing something, report it instead of working around it.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Currency = Literal["EUR", "USD"]

# v3: every amount that crosses a component boundary is bounded (AUDIT-7/8). At most 12 integer digits and
# 6 decimals: a 30-digit "amount" is rejected by the contract instead of overflowing Decimal arithmetic later.
# Sub-cent amounts (1.005) are still representable so the agent can ask about them instead of rounding.
Amount = Annotated[Decimal, Field(max_digits=18, decimal_places=6, allow_inf_nan=False)]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- domain

class Account(Frozen):
    account_id: str
    user_id: str
    currency: Currency
    balance: Decimal


class Beneficiary(Frozen):
    beneficiary_id: str
    user_id: str
    display_name: str
    aliases: list[str] = []          # lowercase names the user may say ("landlord", "rent")
    iban: str                        # normalised: no spaces, uppercase
    category: Literal["tuition", "rent", "family", "other"]
    added_at: datetime
    allowlisted: bool                # user-approved payee
    blocked: bool = False


class UntrustedDocument(Frozen):
    """Text from outside the trust boundary (invoice, email, API note).

    DATA ONLY. Its content must never be executed as an instruction and no
    action may take a parameter from it without the registry agreeing.
    """
    document_id: str
    source: Literal["upload", "email", "api"]
    filename: str
    text: str


class Obligation(Frozen):
    obligation_id: str
    user_id: str
    kind: Literal["tuition", "rent"]
    beneficiary_id: str
    amount: Decimal
    currency: Currency
    due_date: date
    document_id: str | None = None


class FxQuote(Frozen):
    quote_id: str
    sell_currency: Currency
    buy_currency: Currency
    sell_amount: Amount              # total debited from the sell account, fee included
    buy_amount: Amount               # credited to the buy account
    rate: Decimal                    # buy units per 1 sell unit
    fee: Amount                      # in sell currency, included in sell_amount
    expires_at: datetime


# --------------------------------------------------------------------------- intents
# What the parser (rule-based or a model) is allowed to output. Nothing else.

class ReadOverview(Frozen):
    kind: Literal["read_overview"] = "read_overview"


class PayObligations(Frozen):
    """'Pay my tuition and rent, keep 600 EUR' -> expanded into actions by deterministic code."""
    kind: Literal["pay_obligations"] = "pay_obligations"
    obligation_kinds: list[Literal["tuition", "rent"]]
    keep_reserve: Amount | None = None
    reserve_currency: Currency = "EUR"


class FxConvert(Frozen):
    kind: Literal["fx_convert"] = "fx_convert"
    sell_currency: Currency
    buy_currency: Currency
    buy_amount: Amount


class Pay(Frozen):
    kind: Literal["pay"] = "pay"
    beneficiary_ref: str             # what the user said: "mom", "landlord", "b_univ"
    amount_mode: Literal["exact", "all", "unspecified"] = "exact"
    amount: Amount | None = None     # required when amount_mode == "exact"
    currency: Currency = "EUR"
    iban_hint: str | None = None     # an IBAN typed by the user; never trusted over the registry
    memo: str = ""


class AddBeneficiary(Frozen):
    kind: Literal["add_beneficiary"] = "add_beneficiary"
    display_name: str
    iban: str
    category: Literal["tuition", "rent", "family", "other"] = "other"


class Clarify(Frozen):
    kind: Literal["clarify"] = "clarify"
    question: str


class Unsupported(Frozen):
    """Out-of-scope or disallowed request (other users' data, system prompt, limit changes...)."""
    kind: Literal["unsupported"] = "unsupported"
    reason: str
    category: Literal["out_of_scope", "data_access", "limit_change", "jailbreak"] = "out_of_scope"


class AffordabilityCheck(Frozen):
    """'Can I afford to send 300 to mom after my bills?' -> simulate and answer. Never proposes an action."""
    kind: Literal["affordability_check"] = "affordability_check"
    obligation_kinds: list[Literal["tuition", "rent"]] = []
    beneficiary_ref: str | None = None
    amount: Amount | None = None
    currency: Currency = "EUR"
    keep_reserve: Amount | None = None


class ProvideDetail(Frozen):
    """Answer to the agent's last clarification question: '50 euros', 'to mom'."""
    kind: Literal["provide_detail"] = "provide_detail"
    amount: Amount | None = None
    currency: Currency | None = None
    beneficiary_ref: str | None = None


class Correct(Frozen):
    """Change a pending, not yet executed action: 'actually make it 40', 'send it to mom instead'."""
    kind: Literal["correct"] = "correct"
    amount: Amount | None = None
    currency: Currency | None = None
    beneficiary_ref: str | None = None


class Cancel(Frozen):
    """'cancel that' (last pending action) / 'cancel everything' (all pending actions)."""
    kind: Literal["cancel"] = "cancel"
    scope: Literal["last", "all"] = "last"


FinancialIntent = Annotated[
    Union[ReadOverview, PayObligations, FxConvert, Pay, AddBeneficiary, Clarify, Unsupported,
          AffordabilityCheck, ProvideDetail, Correct, Cancel],
    Field(discriminator="kind"),
]


# v6: which parser answered. "rules", "local" and "connected" are the original labels; a label may carry a suffix
# after ":" ("connected:api.deepseek.com", "cascade:rules", "cascade:local/qwen2.5:3b-instruct").
ParserLabel = Annotated[str, StringConstraints(pattern=r"^(?:rules|local|connected|cascade)(?::\S.{0,100})?$")]


class ParsedRequest(Frozen):
    intents: list[FinancialIntent]
    parser: ParserLabel
    raw_text: str
    notes: list[str] = []


# --------------------------------------------------------------------------- actions
# Executable, fully resolved. Built only by deterministic code, never by the LLM.

class PaymentAction(Frozen):
    action_type: Literal["payment"] = "payment"
    action_id: str
    idempotency_key: str
    from_account_id: str
    beneficiary_id: str
    iban: str                        # copied from the registry, never from text
    amount: Amount
    currency: Currency
    obligation_id: str | None = None
    memo: str = ""
    derived_from_untrusted: bool = False


class FxAction(Frozen):
    action_type: Literal["fx"] = "fx"
    action_id: str
    idempotency_key: str
    from_account_id: str
    to_account_id: str
    quote: FxQuote


class AddBeneficiaryAction(Frozen):
    action_type: Literal["add_beneficiary"] = "add_beneficiary"
    action_id: str
    idempotency_key: str
    display_name: str
    iban: str
    category: Literal["tuition", "rent", "family", "other"]


ProposedAction = Annotated[
    Union[PaymentAction, FxAction, AddBeneficiaryAction],
    Field(discriminator="action_type"),
]


class SimulationResult(Frozen):
    action_id: str
    feasible: bool
    balances_before: dict[str, Decimal]   # account_id -> balance
    balances_after: dict[str, Decimal]
    fee: Decimal = Decimal("0")
    errors: list[str] = []


# --------------------------------------------------------------------------- policy

class RiskLevel(str, Enum):
    R0 = "R0"   # read-only
    R1 = "R1"   # reversible
    R2 = "R2"   # low financial risk -> one-tap confirmation
    R3 = "R3"   # high financial risk -> step-up authentication
    R4 = "R4"   # forbidden -> hard deny


class Verdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    STEP_UP = "step_up"
    DENY = "deny"


class RuleResult(Frozen):
    rule_id: str                     # e.g. "RESERVE_FLOOR"
    passed: bool
    severity: RiskLevel              # level this rule forces when it fails
    detail: str                      # human-readable, shown in "why blocked"


class SecurityEvent(Frozen):
    event_id: str
    kind: Literal[
        "prompt_injection", "beneficiary_mismatch", "tool_output_poisoning",
        "replay_blocked", "data_access_denied", "param_tamper", "jailbreak_attempt",
        "step_up_failed", "structuring_suspected", "sanctions_hit",
    ]
    source: str                      # document id, tool name, "user_message", action id...
    detail: str
    at: datetime


class RecentPayment(Frozen):
    """An executed outgoing payment, used for velocity / structuring checks."""
    beneficiary_id: str
    amount_eur: Decimal
    executed_at: datetime


class PolicyContext(Frozen):
    user_id: str
    now: datetime
    accounts: list[Account]
    beneficiaries: list[Beneficiary]
    executed_today_eur: Decimal      # EUR-equivalent outflow already executed today
    reserve_floor_eur: Decimal | None
    step_up_verified: bool
    security_events: list[SecurityEvent] = []
    recent_payments: list[RecentPayment] = []   # executed payments inside STRUCTURING_WINDOW
    # v8 (Verification of Payee): the bank's account-holder directory (normalised IBAN -> registered holder name; an
    # IBAN that is absent is "not available"), and the IBANs of THIS turn's action that also appear in a document
    # (invoice, letter) the customer supplied or that was linked to the request. Both default to empty.
    payee_holders: dict[str, str] = {}
    document_ibans: list[str] = []


class PolicyDecision(Frozen):
    action_id: str
    risk_level: RiskLevel
    verdict: Verdict
    rules: list[RuleResult]
    policy_version: str
    explanation: str


class DocumentScan(Frozen):
    document_id: str
    injection_detected: bool
    matched_patterns: list[str]
    extracted: dict[str, str]        # amount / iban / due_date found as data
    quarantined: bool


# --------------------------------------------------------------------------- confirmation / execution / proof

class ConfirmationRequest(Frozen):
    action_id: str
    params_hash: str                 # binds the approval to these exact parameters
    verdict: Literal["confirm", "step_up"]
    risk_level: RiskLevel
    title: str                       # "Pay 850.00 EUR to Jean Dupont (rent)"
    lines: list[str]                 # recipient, IBAN tail, fee, balance after, ...
    checks_passed: int
    checks_total: int


class Confirmation(Frozen):
    action_id: str
    params_hash: str
    method: Literal["tap", "step_up_otp"]
    confirmed_at: datetime


class ExecutionResult(Frozen):
    action_id: str
    idempotency_key: str
    status: Literal["executed", "replayed", "denied", "failed"]
    ledger_tx_id: str | None = None
    error: str | None = None
    balances_after: dict[str, Decimal] = {}


class Receipt(Frozen):
    """One link of the Proof-of-Intent chain. Written for executed AND denied actions."""
    seq: int
    receipt_id: str
    action_id: str
    created_at: datetime
    intent_hash: str
    action_hash: str
    simulation_hash: str
    policy_hash: str
    confirmation_hash: str | None
    result_hash: str
    prev_receipt_hash: str           # "0"*64 for the first receipt
    receipt_hash: str
    summary: str


class ChainVerification(Frozen):
    valid: bool
    length: int
    first_invalid_seq: int | None = None
    detail: str


class ReceiptAnchor(Frozen):
    """Customer-held copy of the chain head, like a statement number. Lets the customer detect
    a truncated or rewritten chain, which the server-side chain alone cannot."""
    seq: int
    receipt_hash: str
    signature: str                   # HMAC-SHA256(server key, f"{seq}:{receipt_hash}")
    issued_at: datetime


class Projection(Frozen):
    """Answer to an AffordabilityCheck: simulated, nothing proposed or executed."""
    balances_now: dict[str, Decimal]     # currency -> amount
    balances_after: dict[str, Decimal]
    reserve_floor_eur: Decimal | None
    reserve_ok: bool
    feasible: bool
    lines: list[str]                     # human-readable breakdown


# --------------------------------------------------------------------------- UI-facing turn

ActionStatus = Literal[
    "proposed", "awaiting_confirmation", "executed", "denied",
    "rejected_by_user", "failed", "replayed",
]


class ActionView(Frozen):
    action: ProposedAction
    simulation: SimulationResult | None = None
    decision: PolicyDecision | None = None
    status: ActionStatus
    confirmation_request: ConfirmationRequest | None = None
    receipt_id: str | None = None


class AgentTurn(Frozen):
    session_id: str
    assistant_text: str
    parsed: ParsedRequest | None = None
    actions: list[ActionView] = []
    security_events: list[SecurityEvent] = []
    receipts: list[Receipt] = []
    projection: Projection | None = None
    anchor: ReceiptAnchor | None = None      # latest customer-held chain anchor, after any new receipt
    pending_question: str | None = None      # set when the agent is waiting for a detail from the user
