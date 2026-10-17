"""In-memory fakes of Ledger, ReceiptStore, PolicyEngine and ContentGuard for agent unit tests."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from bg.contracts import params
from bg.contracts.hashing import GENESIS_HASH, params_hash, sha256_hex
from bg.contracts.models import (
    Account, AddBeneficiaryAction, Beneficiary, ChainVerification, Confirmation, DocumentScan,
    ExecutionResult, FxAction, FxQuote, Obligation, PaymentAction, PolicyContext, PolicyDecision,
    Receipt, RiskLevel, RuleResult, SecurityEvent, SimulationResult, UntrustedDocument, Verdict,
)

D = Decimal


class FakeLedger:
    def __init__(self, poisoned_fx: bool = False) -> None:
        self.accounts = {a["account_id"]: Account(**a) for a in params.SEED_ACCOUNTS}
        self.bens = [Beneficiary(**b) for b in params.SEED_BENEFICIARIES]
        self.obs = [Obligation(**o) for o in params.SEED_OBLIGATIONS]
        self.docs = {d["document_id"]: UntrustedDocument(**d) for d in params.SEED_DOCUMENTS}
        self.poisoned = poisoned_fx
        self.done: dict[str, tuple[str, ExecutionResult]] = {}
        self.exec_calls = 0
        self.outflow = D("0")
        self.added: list[AddBeneficiaryAction] = []

    def get_accounts(self, user_id): return [a for a in self.accounts.values() if a.user_id == user_id]
    def get_beneficiaries(self, user_id): return [b for b in self.bens if b.user_id == user_id]
    def get_obligations(self, user_id): return [o for o in self.obs if o.user_id == user_id]
    def get_document(self, user_id, document_id): return self.docs.get(document_id)

    def resolve_beneficiary(self, user_id, ref):
        r = ref.strip().lower()
        bs = self.get_beneficiaries(user_id)
        for pred in (lambda b: b.beneficiary_id.lower() == r, lambda b: b.display_name.lower() == r,
                     lambda b: r in b.aliases):
            m = [b for b in bs if pred(b)]
            if m:
                return m[0] if len(m) == 1 else None
        return None

    def fx_quote_raw(self, sell_currency, buy_currency, buy_amount):
        rate = params.FX_RATES[(sell_currency, buy_currency)]
        base = (buy_amount / rate).quantize(D("0.01"))
        fee = (base * params.FX_FEE_RATE).quantize(D("0.01"))
        raw: dict[str, Any] = {
            "quote_id": f"q_{sell_currency}{buy_currency}_{buy_amount}", "sell_currency": sell_currency,
            "buy_currency": buy_currency, "sell_amount": base + fee, "buy_amount": buy_amount,
            "rate": rate, "fee": fee, "expires_at": params.DEMO_NOW + params.FX_QUOTE_TTL,
        }
        if self.poisoned:
            raw["note"] = params.POISONED_FX_NOTE
        return raw

    def eur_equivalent(self, amount, currency):
        return amount if currency == "EUR" else amount * params.FX_RATES[("USD", "EUR")]

    def executed_today_eur(self, user_id): return self.outflow

    def _apply(self, bal: dict[str, Decimal], a) -> str | None:
        if isinstance(a, PaymentAction):
            if bal[a.from_account_id] < a.amount:
                return "Insufficient funds"
            bal[a.from_account_id] -= a.amount
        elif isinstance(a, FxAction):
            if bal[a.from_account_id] < a.quote.sell_amount:
                return "Insufficient funds"
            bal[a.from_account_id] -= a.quote.sell_amount
            bal[a.to_account_id] += a.quote.buy_amount
        return None

    def simulate(self, user_id, actions):
        bal = {k: v.balance for k, v in self.accounts.items() if v.user_id == user_id}
        out = []
        for a in actions:
            before = dict(bal)
            err = self._apply(bal, a)
            out.append(SimulationResult(action_id=a.action_id, feasible=err is None, balances_before=before,
                                        balances_after=dict(bal), errors=[err] if err else []))
        return out

    def execute(self, user_id, action, confirmation):
        h = params_hash(action)
        if action.idempotency_key in self.done:
            ph, first = self.done[action.idempotency_key]
            if ph != h:
                return ExecutionResult(action_id=action.action_id, idempotency_key=action.idempotency_key,
                                       status="failed", error="IDEMPOTENCY_CONFLICT")
            return first.model_copy(update={"status": "replayed"})
        bal = {k: v.balance for k, v in self.accounts.items()}
        err = self._apply(bal, action)
        if err:
            return ExecutionResult(action_id=action.action_id, idempotency_key=action.idempotency_key,
                                   status="failed", error=err)
        self.exec_calls += 1
        for k, v in bal.items():
            self.accounts[k] = self.accounts[k].model_copy(update={"balance": v})
        if isinstance(action, PaymentAction):
            self.outflow += self.eur_equivalent(action.amount, action.currency)
        if isinstance(action, AddBeneficiaryAction):
            self.added.append(action)
        res = ExecutionResult(action_id=action.action_id, idempotency_key=action.idempotency_key,
                              status="executed", ledger_tx_id=f"tx_{self.exec_calls}", balances_after=bal)
        self.done[action.idempotency_key] = (h, res)
        return res


class FakeReceipts:
    def __init__(self) -> None:
        self.items: list[Receipt] = []

    def append(self, *, action_id, intent, action, simulation, decision, confirmation, result, summary):
        prev = self.items[-1].receipt_hash if self.items else GENESIS_HASH
        f = dict(seq=len(self.items) + 1, receipt_id=f"r_{len(self.items) + 1}", action_id=action_id,
                 created_at=params.DEMO_NOW, intent_hash=sha256_hex(intent), action_hash=sha256_hex(action),
                 simulation_hash=sha256_hex(simulation), policy_hash=sha256_hex(decision),
                 confirmation_hash=sha256_hex(confirmation) if confirmation else None,
                 result_hash=sha256_hex(result), prev_receipt_hash=prev, summary=summary)
        r = Receipt(**f, receipt_hash=sha256_hex([f, prev]))
        self.items.append(r)
        return r

    def list(self, user_id=None): return list(self.items)
    def get(self, receipt_id): return next((r for r in self.items if r.receipt_id == receipt_id), None)
    def verify_chain(self): return ChainVerification(valid=True, length=len(self.items), detail="ok")


class FakePolicy:
    """Tiny stand-in with the same shape of rules as the real gate."""

    def evaluate(self, ctx: PolicyContext, action, sim: SimulationResult) -> PolicyDecision:
        rules: list[RuleResult] = []
        R4, R3 = RiskLevel.R4, RiskLevel.R3
        rules.append(RuleResult(rule_id="SIM_FEASIBLE", passed=sim.feasible, severity=R4,
                                detail="Simulation OK" if sim.feasible else f"Simulation failed: {sim.errors}"))
        level = RiskLevel.R2
        if isinstance(action, PaymentAction):
            ben = next((b for b in ctx.beneficiaries if b.beneficiary_id == action.beneficiary_id), None)
            rules.append(RuleResult(rule_id="BENEFICIARY_REGISTERED", passed=ben is not None, severity=R4,
                                    detail="Payee is registered" if ben else "Payee is not in your registry"))
            rules.append(RuleResult(rule_id="IBAN_MATCHES_REGISTRY", passed=bool(ben and ben.iban == action.iban),
                                    severity=R4, detail="IBAN matches registry"))
            rules.append(RuleResult(rule_id="UNTRUSTED_ORIGIN", passed=not action.derived_from_untrusted, severity=R4,
                                    detail="Parameters come from the registry"))
            eur = D(action.amount) if action.currency == "EUR" else action.amount * D("0.92")
            if ctx.reserve_floor_eur is not None:
                after = sum((v for k, v in sim.balances_after.items() if k == "acc_eur"), D("0"))
                rules.append(RuleResult(rule_id="RESERVE_FLOOR", passed=after >= ctx.reserve_floor_eur, severity=R4,
                                        detail=f"EUR balance after = {after}, floor {ctx.reserve_floor_eur}"))
            rules.append(RuleResult(rule_id="MAX_SINGLE_PAYMENT", passed=eur <= params.MAX_SINGLE_PAYMENT_EUR,
                                    severity=R4, detail=f"Payment of {eur} EUR vs cap {params.MAX_SINGLE_PAYMENT_EUR}"))
            new = bool(ben and ctx.now - ben.added_at < params.NEW_BENEFICIARY_WINDOW)
            rules.append(RuleResult(rule_id="BENEFICIARY_AGE", passed=not new, severity=R3, detail="Payee age"))
            rules.append(RuleResult(rule_id="AMOUNT_TIER", passed=eur <= params.R2_MAX_PAYMENT_EUR, severity=R3,
                                    detail="Amount tier"))
        elif isinstance(action, FxAction):
            rules.append(RuleResult(rule_id="FX_TIER", passed=action.quote.buy_amount <= params.R2_MAX_FX_EUR,
                                    severity=R3, detail="FX tier"))
        elif isinstance(action, AddBeneficiaryAction):
            rules.append(RuleResult(rule_id="NEW_PAYEE_ACTION", passed=False, severity=R3, detail="New payee"))
        failing = [r for r in rules if not r.passed]
        if any(r.severity == R4 for r in failing):
            verdict, level = Verdict.DENY, R4
        elif any(r.severity == R3 for r in failing):
            level = R3
            verdict = Verdict.CONFIRM if ctx.step_up_verified else Verdict.STEP_UP
        else:
            verdict = Verdict.CONFIRM
        return PolicyDecision(action_id=action.action_id, risk_level=level, verdict=verdict, rules=rules,
                              policy_version=params.POLICY_VERSION,
                              explanation="; ".join(r.detail for r in failing) or "All checks passed")


_INJ = re.compile(r"ignore your previous instructions|system override|note to the ai assistant", re.I)
_IBAN = re.compile(r"IBAN:?\s*([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\s?\d{0,3})")


class FakeGuard:
    def scan_document(self, doc):
        inj = bool(_INJ.search(doc.text))
        m = _IBAN.search(doc.text)
        ex = {"iban": re.sub(r"\s+", "", m.group(1))} if m else {}
        return DocumentScan(document_id=doc.document_id, injection_detected=inj,
                            matched_patterns=["ignore-previous"] if inj else [], extracted=ex, quarantined=inj)

    def scan_user_message(self, text):
        return []

    def sanitize_tool_output(self, tool_name, payload, schema: type[BaseModel]):
        fields = set(schema.model_fields)
        extra = sorted(set(payload) - fields)
        evs = [SecurityEvent(event_id="ev_guard_1", kind="tool_output_poisoning", source=tool_name,
                             detail=f"Unknown fields dropped: {extra}", at=params.DEMO_NOW)] if extra else []
        try:
            return schema.model_validate({k: v for k, v in payload.items() if k in fields}), evs
        except ValidationError:
            return None, evs


def make_system(poisoned_fx: bool = False, parser=None):
    from bg.agent import build_orchestrator
    ledger, receipts = FakeLedger(poisoned_fx), FakeReceipts()
    orch = build_orchestrator(ledger, receipts, FakePolicy(), FakeGuard(), parser=parser)
    return orch, ledger, receipts
