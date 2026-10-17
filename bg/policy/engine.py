"""Deterministic policy engine: the gate that authorises (or refuses) every action.

No LLM, no network, no randomness. The only clock is ``ctx.now``. Every applicable
rule yields a ``RuleResult``; the decision's risk level is the highest failing
severity (baseline R2 for payments/fx, R3 for add_beneficiary). Fail closed:
any exception or missing data produces a deny, never an allow.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from bg.contracts import params
from bg.contracts.models import (
    Account, AddBeneficiaryAction, Beneficiary, FxAction, PaymentAction, PolicyContext,
    PolicyDecision, ProposedAction, RiskLevel, RuleResult, SimulationResult, Verdict,
)

_ORDER = [RiskLevel.R0, RiskLevel.R1, RiskLevel.R2, RiskLevel.R3, RiskLevel.R4]
_VERDICT = {
    RiskLevel.R0: Verdict.ALLOW, RiskLevel.R1: Verdict.ALLOW, RiskLevel.R2: Verdict.CONFIRM,
    RiskLevel.R3: Verdict.STEP_UP, RiskLevel.R4: Verdict.DENY,
}


def _m(x: Decimal) -> str:
    """Display an amount with two decimals (display only; comparisons stay exact)."""
    return f"{x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}"


def _norm_iban(iban: str) -> str:
    return "".join(iban.split()).upper()


def _to_eur(amount: Decimal, currency: str) -> Decimal:
    if currency == "EUR":
        return amount
    return amount * params.FX_RATES[(currency, "EUR")]   # KeyError -> fail closed


def _hours(td: timedelta) -> str:
    return f"{td.total_seconds() / 3600:.2f}h"


def _R(rule_id: str, passed: bool, severity: RiskLevel, detail: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, passed=passed, severity=severity, detail=detail)


class DeterministicPolicyEngine:
    """Implements ``PolicyEngine``."""

    def evaluate(self, ctx: PolicyContext, action: ProposedAction,
                 simulation: SimulationResult) -> PolicyDecision:
        action_id = getattr(action, "action_id", "unknown")
        try:
            if isinstance(action, PaymentAction):
                rules, base = self._payment(ctx, action, simulation), RiskLevel.R2
            elif isinstance(action, FxAction):
                rules, base = self._fx(ctx, action, simulation), RiskLevel.R2
            elif isinstance(action, AddBeneficiaryAction):
                rules, base = self._add_beneficiary(ctx, action, simulation), RiskLevel.R3
            else:
                raise TypeError(f"unknown action type {type(action).__name__}")
            return self._decide(action_id, rules, base, ctx)
        except Exception as exc:  # fail closed, whatever went wrong
            rule = _R("POLICY_ERROR", False, RiskLevel.R4,
                      f"policy evaluation failed ({type(exc).__name__}: {exc}); denied by default")
            return PolicyDecision(
                action_id=str(action_id), risk_level=RiskLevel.R4, verdict=Verdict.DENY, rules=[rule],
                policy_version=params.POLICY_VERSION,
                explanation=f"Denied: {rule.detail}.")

    # ------------------------------------------------------------------ decision
    def _decide(self, action_id: str, rules: list[RuleResult], base: RiskLevel,
                ctx: PolicyContext) -> PolicyDecision:
        level = base
        for r in rules:
            if not r.passed and _ORDER.index(r.severity) > _ORDER.index(level):
                level = r.severity
        verdict = _VERDICT[level]
        failing = [r for r in rules if not r.passed and r.severity == level]
        if verdict is Verdict.DENY:
            why = "Denied: " + "; ".join(r.detail for r in failing)
        elif verdict is Verdict.STEP_UP:
            state = "verified" if ctx.step_up_verified else "required"
            why = f"Step-up authentication {state} (R3): " + "; ".join(r.detail for r in failing)
        elif verdict is Verdict.CONFIRM:
            why = f"Confirmation required (R2): all {len(rules)} policy checks passed"
        else:
            why = "Allowed: read-only or reversible action"
        return PolicyDecision(
            action_id=action_id, risk_level=level, verdict=verdict, rules=rules,
            policy_version=params.POLICY_VERSION, explanation=why.rstrip(".") + ".")

    # ------------------------------------------------------------------ shared rules
    @staticmethod
    def _sim_rule(action_id: str, sim: SimulationResult) -> RuleResult:
        if sim.action_id != action_id:
            return _R("SIM_FEASIBLE", False, RiskLevel.R4,
                      f"simulation is for action {sim.action_id}, not {action_id}")
        if not sim.feasible:
            errs = "; ".join(sim.errors) or "no reason given"
            return _R("SIM_FEASIBLE", False, RiskLevel.R4, f"simulation is not feasible: {errs}")
        return _R("SIM_FEASIBLE", True, RiskLevel.R4, "simulation is feasible")

    @staticmethod
    def _account(ctx: PolicyContext, account_id: str) -> Account | None:
        for a in ctx.accounts:
            if a.account_id == account_id:
                return a
        return None

    @staticmethod
    def _reserve_rule(ctx: PolicyContext, sim: SimulationResult) -> RuleResult | None:
        """EUR balance after all actions (cumulative simulation) must stay >= reserve floor.

        An account missing from ``balances_after`` falls back to ``balances_before``; if it is in
        neither the rule fails closed.
        """
        if ctx.reserve_floor_eur is None:
            return None
        floor = ctx.reserve_floor_eur
        eur = [a for a in ctx.accounts if a.user_id == ctx.user_id and a.currency == "EUR"]
        if not eur:
            return _R("RESERVE_FLOOR", False, RiskLevel.R4,
                      "no EUR account found for the user; cannot verify the reserve")
        total = Decimal(0)
        for a in eur:
            if a.account_id in sim.balances_after:
                total += sim.balances_after[a.account_id]
            elif a.account_id in sim.balances_before:
                total += sim.balances_before[a.account_id]
            else:
                return _R("RESERVE_FLOOR", False, RiskLevel.R4,
                          f"balance of {a.account_id} missing from the simulation; cannot verify the reserve")
        ok = total >= floor
        rel = "at or above" if ok else "below"
        return _R("RESERVE_FLOOR", ok, RiskLevel.R4,
                  f"EUR balance after all actions would be {_m(total)}, {rel} the {_m(floor)} reserve floor")

    # ------------------------------------------------------------------ payment
    def _payment(self, ctx: PolicyContext, a: PaymentAction, sim: SimulationResult) -> list[RuleResult]:
        out: list[RuleResult] = [self._sim_rule(a.action_id, sim)]
        ben: Beneficiary | None = next(
            (b for b in ctx.beneficiaries if b.beneficiary_id == a.beneficiary_id), None)
        eur = _to_eur(a.amount, a.currency)

        # registry
        if ben is None:
            out.append(_R("BENEFICIARY_REGISTERED", False, RiskLevel.R4,
                          f"beneficiary {a.beneficiary_id} is not in the user's registry"))
        elif not ben.allowlisted:
            out.append(_R("BENEFICIARY_REGISTERED", False, RiskLevel.R4,
                          f"beneficiary {ben.display_name} is registered but not allowlisted"))
        else:
            out.append(_R("BENEFICIARY_REGISTERED", True, RiskLevel.R4,
                          f"{ben.display_name} is an allowlisted registered payee"))

        if ben is None:
            out.append(_R("BENEFICIARY_NOT_BLOCKED", False, RiskLevel.R4,
                          "cannot check blocked status: beneficiary unknown"))
            out.append(_R("IBAN_MATCHES_REGISTRY", False, RiskLevel.R4,
                          "cannot compare IBAN: beneficiary unknown"))
        else:
            out.append(_R("BENEFICIARY_NOT_BLOCKED", not ben.blocked, RiskLevel.R4,
                          f"{ben.display_name} is blocked" if ben.blocked
                          else f"{ben.display_name} is not blocked"))
            got, want = _norm_iban(a.iban), _norm_iban(ben.iban)
            same = got == want
            out.append(_R("IBAN_MATCHES_REGISTRY", same, RiskLevel.R4,
                          "payment IBAN matches the registered IBAN" if same else
                          f"payment IBAN ending {got[-4:]} differs from the registered IBAN ending {want[-4:]}"))

        # tenant isolation
        acc = self._account(ctx, a.from_account_id)
        problems = []
        if acc is None:
            problems.append(f"source account {a.from_account_id} not found for this user")
        elif acc.user_id != ctx.user_id:
            problems.append(f"source account {a.from_account_id} belongs to another user")
        if ben is not None and ben.user_id != ctx.user_id:
            problems.append(f"beneficiary {ben.beneficiary_id} belongs to another user")
        out.append(_R("TENANT_ISOLATION", not problems, RiskLevel.R4,
                      "; ".join(problems) if problems else f"account and payee both belong to {ctx.user_id}"))

        reserve = self._reserve_rule(ctx, sim)
        if reserve is not None:
            out.append(reserve)

        # amount caps
        if a.amount <= 0:
            out.append(_R("MAX_SINGLE_PAYMENT", False, RiskLevel.R4,
                          f"payment amount {_m(a.amount)} must be positive"))
        else:
            ok = eur <= params.MAX_SINGLE_PAYMENT_EUR
            out.append(_R("MAX_SINGLE_PAYMENT", ok, RiskLevel.R4,
                          f"payment of {_m(eur)} EUR eq. {'is within' if ok else 'exceeds'} the "
                          f"{_m(params.MAX_SINGLE_PAYMENT_EUR)} per-payment cap"))
        total = ctx.executed_today_eur + eur
        ok = total <= params.DAILY_OUTFLOW_LIMIT_EUR
        out.append(_R("DAILY_LIMIT", ok, RiskLevel.R4,
                      f"executed today {_m(ctx.executed_today_eur)} + this payment {_m(eur)} = {_m(total)} EUR "
                      f"{'is within' if ok else 'exceeds'} the {_m(params.DAILY_OUTFLOW_LIMIT_EUR)} daily limit"))
        out.append(_R("UNTRUSTED_ORIGIN", not a.derived_from_untrusted, RiskLevel.R4,
                      "payment was derived from untrusted content (document or tool output)"
                      if a.derived_from_untrusted else "payment does not derive from untrusted content"))

        # R3 rules
        if ben is None:
            out.append(_R("BENEFICIARY_AGE", False, RiskLevel.R3,
                          "cannot determine payee age: beneficiary unknown"))
        else:
            age = ctx.now - ben.added_at
            ok = age >= params.NEW_BENEFICIARY_WINDOW
            out.append(_R("BENEFICIARY_AGE", ok, RiskLevel.R3,
                          f"payee added {_hours(age)} ago, "
                          f"{'at least' if ok else 'less than'} the {_hours(params.NEW_BENEFICIARY_WINDOW)} "
                          f"new-payee window"))
        ok = eur <= params.R2_MAX_PAYMENT_EUR
        out.append(_R("AMOUNT_TIER", ok, RiskLevel.R3,
                      f"payment of {_m(eur)} EUR eq. {'is within' if ok else 'exceeds'} the "
                      f"{_m(params.R2_MAX_PAYMENT_EUR)} one-tap limit"))
        return out

    # ------------------------------------------------------------------ fx
    def _fx(self, ctx: PolicyContext, a: FxAction, sim: SimulationResult) -> list[RuleResult]:
        out: list[RuleResult] = [self._sim_rule(a.action_id, sim)]
        problems = []
        for aid in (a.from_account_id, a.to_account_id):
            acc = self._account(ctx, aid)
            if acc is None:
                problems.append(f"account {aid} not found for this user")
            elif acc.user_id != ctx.user_id:
                problems.append(f"account {aid} belongs to another user")
        out.append(_R("TENANT_ISOLATION", not problems, RiskLevel.R4,
                      "; ".join(problems) if problems else f"both accounts belong to {ctx.user_id}"))
        reserve = self._reserve_rule(ctx, sim)
        if reserve is not None:
            out.append(reserve)
        q = a.quote
        eur = _to_eur(q.buy_amount, q.buy_currency)
        ok = eur <= params.R2_MAX_FX_EUR
        out.append(_R("FX_TIER", ok, RiskLevel.R3,
                      f"FX buy of {_m(eur)} EUR eq. {'is within' if ok else 'exceeds'} the "
                      f"{_m(params.R2_MAX_FX_EUR)} one-tap limit"))
        return out

    # ------------------------------------------------------------------ add beneficiary
    def _add_beneficiary(self, ctx: PolicyContext, a: AddBeneficiaryAction,
                         sim: SimulationResult) -> list[RuleResult]:
        return [
            self._sim_rule(a.action_id, sim),
            _R("NEW_PAYEE_ACTION", False, RiskLevel.R3,
               f"adding a new payee ({a.display_name}, IBAN ending {_norm_iban(a.iban)[-4:]}) "
               f"always requires step-up"),
        ]
