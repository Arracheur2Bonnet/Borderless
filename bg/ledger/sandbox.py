"""In-memory sandbox core banking. Deterministic, no network."""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from bg.contracts import params
from bg.contracts.hashing import params_hash
from bg.contracts.models import (
    Account, AddBeneficiaryAction, Beneficiary, Confirmation, ExecutionResult, FxAction,
    Obligation, PaymentAction, ProposedAction, RecentPayment, SimulationResult, UntrustedDocument,
)

CENT = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class SandboxLedger:
    def __init__(self, poisoned_fx: bool = False, now: datetime = params.DEMO_NOW):
        self.poisoned_fx = poisoned_fx
        self.now = now
        self._accounts: dict[str, Account] = {
            a["account_id"]: Account(**{**a, "balance": q2(a["balance"])}) for a in params.SEED_ACCOUNTS}
        self._beneficiaries: dict[str, Beneficiary] = {
            b["beneficiary_id"]: Beneficiary(**b) for b in params.SEED_BENEFICIARIES}
        self._obligations = [Obligation(**o) for o in params.SEED_OBLIGATIONS]
        self._documents = {d["document_id"]: d for d in params.SEED_DOCUMENTS}
        # document ownership follows obligations
        self._doc_owner = {o.document_id: o.user_id for o in self._obligations if o.document_id}
        # idempotency_key -> (user_id, params_hash, ExecutionResult)
        self._executions: dict[str, tuple[str, str, ExecutionResult]] = {}
        # (user_id, date, eur amount) of executed payments
        self._outflows: list[tuple[str, Any, Decimal]] = []
        # (user_id, RecentPayment) of executed payments, in execution order (oldest first)
        self._payments: list[tuple[str, RecentPayment]] = []

    # ------------------------------------------------------------------ reads (tenant scoped)
    def get_accounts(self, user_id: str) -> list[Account]:
        return [a for a in self._accounts.values() if a.user_id == user_id]

    def get_beneficiaries(self, user_id: str) -> list[Beneficiary]:
        return [b for b in self._beneficiaries.values() if b.user_id == user_id]

    def get_obligations(self, user_id: str) -> list[Obligation]:
        return [o for o in self._obligations if o.user_id == user_id]

    def get_document(self, user_id: str, document_id: str) -> UntrustedDocument | None:
        d = self._documents.get(document_id)
        if d is None or self._doc_owner.get(document_id) != user_id:
            return None
        return UntrustedDocument(**d)

    def resolve_beneficiary(self, user_id: str, ref: str) -> Beneficiary | None:
        ref_n = (ref or "").strip().casefold()
        if not ref_n:
            return None
        mine = self.get_beneficiaries(user_id)
        for matcher in (
            lambda b: b.beneficiary_id.casefold() == ref_n,
            lambda b: b.display_name.casefold() == ref_n,
            lambda b: ref_n in [a.casefold() for a in b.aliases],
        ):
            hits = [b for b in mine if matcher(b)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return None
        return None

    # ------------------------------------------------------------------ FX
    def fx_quote_raw(self, sell_currency: str, buy_currency: str, buy_amount: Decimal) -> dict[str, Any]:
        rate = params.FX_RATES.get((sell_currency, buy_currency))
        if rate is None:
            raise ValueError(f"unsupported pair {sell_currency}->{buy_currency}")
        buy = q2(Decimal(buy_amount))
        net = buy / rate
        fee = q2(net * params.FX_FEE_RATE)
        sell = q2(net * (1 + params.FX_FEE_RATE))
        qid = "fxq_" + hashlib.sha256(
            f"{sell_currency}|{buy_currency}|{buy}|{self.now.isoformat()}".encode()).hexdigest()[:16]
        out: dict[str, Any] = {
            "quote_id": qid, "sell_currency": sell_currency, "buy_currency": buy_currency,
            "sell_amount": sell, "buy_amount": buy, "rate": rate, "fee": fee,
            "expires_at": self.now + params.FX_QUOTE_TTL,
        }
        if self.poisoned_fx:
            out["note"] = params.POISONED_FX_NOTE
        return out

    def eur_equivalent(self, amount: Decimal, currency: str) -> Decimal:
        if currency == "EUR":
            return q2(Decimal(amount))
        return q2(Decimal(amount) * params.FX_RATES[(currency, "EUR")])

    def executed_today_eur(self, user_id: str) -> Decimal:
        today = self.now.date()
        return sum((e for u, d, e in self._outflows if u == user_id and d == today), Decimal("0.00"))

    def recent_payments(self, user_id: str, since: datetime) -> list[RecentPayment]:
        """Executed outgoing payments (not FX, not add_beneficiary) at or after `since`, oldest first."""
        return [p for u, p in self._payments if u == user_id and p.executed_at >= since]

    # ------------------------------------------------------------------ core transition
    def _apply(self, action: ProposedAction, bal: dict[str, Decimal], ccy: dict[str, str]) -> list[str]:
        """Apply action to the working balances `bal` (mutated only when there is no error)."""
        errors: list[str] = []
        if isinstance(action, PaymentAction):
            if action.from_account_id not in bal:
                return ["UNKNOWN_ACCOUNT"]
            if ccy[action.from_account_id] != action.currency:
                errors.append("CURRENCY_MISMATCH")
            if action.amount <= 0:
                errors.append("INVALID_AMOUNT")
            elif bal[action.from_account_id] < q2(action.amount):
                errors.append("INSUFFICIENT_FUNDS")
            if not errors:
                bal[action.from_account_id] -= q2(action.amount)
        elif isinstance(action, FxAction):
            qt = action.quote
            if action.from_account_id not in bal or action.to_account_id not in bal:
                return ["UNKNOWN_ACCOUNT"]
            if (ccy[action.from_account_id] != qt.sell_currency
                    or ccy[action.to_account_id] != qt.buy_currency):
                errors.append("CURRENCY_MISMATCH")
            if qt.expires_at < self.now:
                errors.append("QUOTE_EXPIRED")
            if qt.sell_amount <= 0 or qt.buy_amount <= 0:
                errors.append("INVALID_AMOUNT")
            elif bal[action.from_account_id] < q2(qt.sell_amount):
                errors.append("INSUFFICIENT_FUNDS")
            if not errors:
                bal[action.from_account_id] -= q2(qt.sell_amount)
                bal[action.to_account_id] += q2(qt.buy_amount)
        return errors

    def simulate(self, user_id: str, actions: list[ProposedAction]) -> list[SimulationResult]:
        accts = self.get_accounts(user_id)
        ccy = {a.account_id: a.currency for a in accts}
        bal = {a.account_id: a.balance for a in accts}
        results = []
        for act in actions:
            before = dict(bal)
            errors = self._apply(act, bal, ccy)
            fee = act.quote.fee if isinstance(act, FxAction) else Decimal("0")
            results.append(SimulationResult(
                action_id=act.action_id, feasible=not errors, balances_before=before,
                balances_after=dict(bal), fee=fee, errors=errors))
        return results

    def execute(self, user_id: str, action: ProposedAction,
                confirmation: Confirmation | None) -> ExecutionResult:
        phash = params_hash(action)
        prior = self._executions.get(action.idempotency_key)
        if prior is not None:
            p_user, p_hash, p_res = prior
            if p_user != user_id or p_hash != phash:
                return ExecutionResult(action_id=action.action_id, idempotency_key=action.idempotency_key,
                                       status="failed", error="IDEMPOTENCY_CONFLICT")
            return p_res.model_copy(update={"status": "replayed"})

        accts = {a.account_id: a for a in self.get_accounts(user_id)}
        ccy = {k: a.currency for k, a in accts.items()}
        bal = {k: a.balance for k, a in accts.items()}
        errors = self._apply(action, bal, ccy)
        if errors:
            return ExecutionResult(action_id=action.action_id, idempotency_key=action.idempotency_key,
                                   status="failed", error=errors[0],
                                   balances_after={k: a.balance for k, a in accts.items()})
        if isinstance(action, AddBeneficiaryAction):
            bid = "b_" + hashlib.sha256(action.idempotency_key.encode()).hexdigest()[:10]
            self._beneficiaries[bid] = Beneficiary(
                beneficiary_id=bid, user_id=user_id, display_name=action.display_name,
                aliases=[], iban=action.iban.replace(" ", "").upper(), category=action.category,
                added_at=self.now, allowlisted=True)
        for k, v in bal.items():
            self._accounts[k] = self._accounts[k].model_copy(update={"balance": v})
        if isinstance(action, PaymentAction):
            eur = self.eur_equivalent(action.amount, action.currency)
            self._outflows.append((user_id, self.now.date(), eur))
            self._payments.append((user_id, RecentPayment(
                beneficiary_id=action.beneficiary_id, amount_eur=eur, executed_at=self.now)))
        tx = "tx_" + hashlib.sha256(action.idempotency_key.encode()).hexdigest()[:12]
        res = ExecutionResult(action_id=action.action_id, idempotency_key=action.idempotency_key,
                              status="executed", ledger_tx_id=tx, balances_after=dict(bal))
        self._executions[action.idempotency_key] = (user_id, phash, res)
        return res
