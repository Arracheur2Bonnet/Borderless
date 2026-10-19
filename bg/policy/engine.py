"""Deterministic policy engine: the gate that authorises (or refuses) every action.

No LLM, no network, no randomness. The only clock is ``ctx.now``. Every applicable
rule yields a ``RuleResult``; the decision's risk level is the highest failing
severity (baseline R2 for payments/fx, R3 for add_beneficiary). Fail closed:
any exception or missing data produces a deny, never an allow.
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from bg.contracts import params
from bg.contracts.canonical import (
    VOP_CLOSE, VOP_MATCH, iban_problem, iban_screening_form, name_skeleton, normalize_iban, payee_name_match, q2,
    unscreenable_letters,
)
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
    return f"{q2(x):f}"


def _norm_iban(iban: str) -> str:
    """Canonical comparison form (shared with the parser and the ledger): NFKC, separators and invisible
    characters removed, upper-cased. Validity is checked separately (``iban_problem``)."""
    return normalize_iban(iban)


def _norm_name(name: str) -> str:
    """Screening form of a name: look-alikes folded to Latin, accents stripped, casefolded,
    punctuation -> space, whitespace collapsed. Letters that cannot be folded to a-z are dropped here;
    ``_sanctions_rule`` fails closed on them (see ``unscreenable_letters``).

    "  VOLKOV-trade   Holdings " -> "volkov trade holdings"; "Université" -> "universite".
    """
    return name_skeleton(name)


_LEET = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"})
_LEET_I = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"})   # '1' as 'i'
_LEET_ALT = {"0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"}


def _word_match(n: str, e: str) -> bool:
    """Whole-word containment either way; '?' in `n` is a one-letter wildcard (an unscreenable letter).
    v6 (AUDIT2-1): the entry is also looked for with every space removed from the name, so a name written without
    spaces ("VolkovTradeHoldings") or with different ones is still a hit."""
    if not n or not e:
        return False
    # the entry inside the name: each letter of the entry may appear as '?' in the name
    pe = "".join(f"[{c}?]" if c.isalnum() else re.escape(c) for c in e)
    if re.search(r"(?:^| )" + pe + r"(?: |$)", n):
        return True
    flat = "".join(f"[{c}?]" for c in e if c.isalnum())
    if flat and re.search(flat, n.replace(" ", "")):
        return True
    # the name inside the entry (two words or more): '?' in the name matches any letter of the entry
    if len(n.split()) >= 2:
        pn = "".join("[a-z0-9]" if c == "?" else re.escape(c) for c in n)
        if re.search(r"(?:^| )" + pn + r"(?: |$)", e):
            return True
    return False


def _join_letter_runs(n: str) -> str:
    """'v o l k o v trade' -> 'volkov trade': consecutive one-character words are joined (letter-spacing, dotted
    acronyms such as 'f z e' from 'F.Z.E.')."""
    out: list[str] = []
    run = ""
    for w in n.split():
        if len(w) == 1:
            run += w
            continue
        if run:
            out.append(run)
            run = ""
        out.append(w)
    if run:
        out.append(run)
    return " ".join(out)


def _leet_forms(n: str) -> list[str]:
    """The name with digits read as letters. '1' is tried as 'l' and as 'i' (every combination for up to 4 ones)."""
    forms = [n]
    ones = n.count("1")
    base = n.translate(str.maketrans(_LEET_ALT))
    if "1" not in n:
        return list(dict.fromkeys([*forms, base]))
    if ones <= 4:
        from itertools import product
        for combo in product("li", repeat=ones):
            it = iter(combo)
            forms.append("".join(next(it) if ch == "1" else _LEET_ALT.get(ch, ch) for ch in n))
    else:
        forms += [n.translate(_LEET), n.translate(_LEET_I)]
    return list(dict.fromkeys(forms))


def _sanctions_name_hit(name: str) -> str | None:
    """Return the watchlist entry the (raw) name matches, or None.

    Matching is on whole words of the normalised forms, in both directions:
      * the full watchlist entry appears inside the name ("Volkov Trade Holdings Ltd" -> hit), or
      * the name appears inside the entry ("Volkov Trade" -> hit), but only for names of at least
        two words, so a lone common word ("Trade", "Northgate") does not flag legitimate payees.
    v3 (AUDIT-4): a letter that cannot be folded to Latin (Armenian "օ", Cherokee "Ꮩ", ...) is a wildcard, and
    digits used as letters ("V0lkov") are also tried, so look-alike spellings of a listed name still hit.
    v6 (AUDIT2-1): the name is also tried with runs of single letters joined ("V o l k o v", "F.Z.E."), with the
    spaces of the name ignored ("VolkovTradeHoldings"), and with "1" read as "i" as well as "l".
    """
    raw = name_skeleton(name, unknown="?")
    forms: list[str] = []
    for base in dict.fromkeys((raw, _join_letter_runs(raw))):
        forms += _leet_forms(base)
    for n in dict.fromkeys(forms):
        for entry in params.SANCTIONS_WATCHLIST_NAMES:
            e = name_skeleton(entry)
            if _word_match(n, e):
                return entry
    return None


def _sanctions_rule(name: str | None, iban: str | None, what: str, iban_invalid_denied: bool = False) -> RuleResult:
    """SANCTIONS_SCREEN (R4). Fails closed when the name is missing/empty, contains letters that cannot be
    screened against the Latin watchlist, or the IBAN is missing or invalid."""
    if name is None or not (_norm_name(name) or unscreenable_letters(name)):
        return _R("SANCTIONS_SCREEN", False, RiskLevel.R4,
                  f"cannot screen {what}: name is missing or empty")
    if iban is None or not _norm_iban(iban):
        return _R("SANCTIONS_SCREEN", False, RiskLevel.R4,
                  f"cannot screen {what} {name}: IBAN is missing")
    hits = []
    entry = _sanctions_name_hit(name)
    if entry is not None:
        hits.append(f"{what} name {name!r} matches sanctions watchlist entry {entry!r}")
    nib = iban_screening_form(iban)
    if nib in {iban_screening_form(x) for x in params.SANCTIONS_WATCHLIST_IBANS}:
        hits.append(f"{what} IBAN ending {nib[-4:]} is on the sanctions watchlist")
    if hits:
        return _R("SANCTIONS_SCREEN", False, RiskLevel.R4, "; ".join(hits))
    odd = unscreenable_letters(name)
    if odd:
        # Fail closed: a Latin watchlist cannot be compared with these letters. Trade-off: a legitimate name in
        # another script is also refused here; in a bank it goes to manual review, in the sandbox it is denied.
        return _R("SANCTIONS_SCREEN", False, RiskLevel.R4,
                  f"cannot screen {what} name {name!r}: it contains letters outside the Latin alphabet "
                  f"({''.join(odd[:5])}); manual sanctions review required")
    problem = iban_problem(iban)
    if problem is not None:
        if iban_invalid_denied:
            # The name and the screening form of the IBAN were checked above with no match. The invalid IBAN is
            # already a hard deny under IBAN_VALID; failing here too would wrongly report a sanctions hit.
            return _R("SANCTIONS_SCREEN", True, RiskLevel.R4,
                      f"{what} name {name!r} and IBAN screened: no watchlist match (the IBAN itself is invalid, "
                      f"see IBAN_VALID)")
        return _R("SANCTIONS_SCREEN", False, RiskLevel.R4, f"cannot screen {what} {name}: IBAN {problem}")
    nib = _norm_iban(iban)
    return _R("SANCTIONS_SCREEN", True, RiskLevel.R4,
              f"{name} (IBAN ending {nib[-4:]}) screened against {len(params.SANCTIONS_WATCHLIST_NAMES)} "
              f"watchlist names and {len(params.SANCTIONS_WATCHLIST_IBANS)} IBANs: no match")


def _vop_rule(ctx: PolicyContext, typed_name: str, iban: str) -> RuleResult:
    """PAYEE_VERIFICATION (v8). Modelled on the EU Verification of Payee and the UK Confirmation of Payee: the name the
    customer gave is compared with the holder the bank has on record for the IBAN. The detail always starts with the
    outcome so that the agent can phrase it: "MATCH", "CLOSE MATCH", "NO MATCH" or "NOT AVAILABLE".

    MATCH passes. CLOSE MATCH, NO MATCH and NOT AVAILABLE fail at R3 (step-up). NO MATCH becomes R4 (deny) when the
    IBAN also appears in a document supplied for this request: the invoice IBAN-swap scam. The holder's name is only
    ever quoted for a close match (as banks do); a no-match detail never reveals who holds the account.
    """
    nib = _norm_iban(iban)
    tail = nib[-4:]
    holder = ctx.payee_holders.get(nib)
    if holder is None:
        return _R("PAYEE_VERIFICATION", False, RiskLevel.R3,
                  f"NOT AVAILABLE: the bank has no account-holder record for the account ending {tail}, so the name "
                  f"{typed_name!r} could not be checked")
    outcome = payee_name_match(typed_name, holder)
    if outcome == VOP_MATCH:
        return _R("PAYEE_VERIFICATION", True, RiskLevel.R3,
                  f"MATCH: the name {typed_name!r} matches the account holder on record for the account ending {tail}")
    if outcome == VOP_CLOSE:
        return _R("PAYEE_VERIFICATION", False, RiskLevel.R3,
                  f"CLOSE MATCH: you gave {typed_name!r}; the bank has the account ending {tail} as {holder!r}")
    in_doc = nib in {_norm_iban(x) for x in ctx.document_ibans}
    if in_doc:
        return _R("PAYEE_VERIFICATION", False, RiskLevel.R4,
                  f"NO MATCH: the name {typed_name!r} does not match the account holder on record for the account "
                  f"ending {tail}, and that account number comes from a document (invoice or letter) supplied for this "
                  f"request: invoice-IBAN-swap pattern")
    return _R("PAYEE_VERIFICATION", False, RiskLevel.R3,
              f"NO MATCH: the name {typed_name!r} does not match the account holder on record for the account "
              f"ending {tail}")


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
            problem = iban_problem(got)
            same = got == want and problem is None
            out.append(_R("IBAN_MATCHES_REGISTRY", same, RiskLevel.R4,
                          "payment IBAN matches the registered IBAN" if same else
                          f"payment IBAN is invalid: {problem}" if got == want else
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

        # sanctions: registered name + registered IBAN, and the payment IBAN if it differs
        if ben is None:
            out.append(_R("SANCTIONS_SCREEN", False, RiskLevel.R4,
                          "cannot screen payee name: beneficiary unknown"))
        else:
            res = _sanctions_rule(ben.display_name, ben.iban, "payee")
            if res.passed and _norm_iban(a.iban) != _norm_iban(ben.iban):
                res = _sanctions_rule(ben.display_name, a.iban, "payee")
            out.append(res)

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
        out.append(self._vop_payment_rule(ctx, ben))
        ok = eur <= params.R2_MAX_PAYMENT_EUR
        out.append(_R("AMOUNT_TIER", ok, RiskLevel.R3,
                      f"payment of {_m(eur)} EUR eq. {'is within' if ok else 'exceeds'} the "
                      f"{_m(params.R2_MAX_PAYMENT_EUR)} one-tap limit"))
        out.append(self._structuring_rule(ctx, a, ben, eur))
        return out

    @staticmethod
    def _vop_payment_rule(ctx: PolicyContext, ben: Beneficiary | None) -> RuleResult:
        """PAYEE_VERIFICATION for a payment: re-run while the payee is younger than VOP_CHECK_WINDOW, on the name the
        customer gave when saving the payee and the IBAN on file. An older payee passes (it was checked when added and
        has been paid before). Never less friction than before: a failing check only adds R3 or R4."""
        if ben is None:
            return _R("PAYEE_VERIFICATION", False, RiskLevel.R3, "NOT AVAILABLE: payee unknown, name not checked")
        age = ctx.now - ben.added_at
        if age >= params.VOP_CHECK_WINDOW:
            return _R("PAYEE_VERIFICATION", True, RiskLevel.R3,
                      f"NOT REQUIRED: {ben.display_name} has been a payee for {_hours(age)}, "
                      f"at least {_hours(params.VOP_CHECK_WINDOW)}; the name was checked when it was added")
        return _vop_rule(ctx, ben.display_name, ben.iban)

    @staticmethod
    def _structuring_rule(ctx: PolicyContext, a: PaymentAction, ben: Beneficiary | None,
                          eur: Decimal) -> RuleResult:
        """STRUCTURING (R3): same-payee payments inside the window + this one above the threshold.

        Bad recent-payment data (negative amount, naive datetime) raises -> POLICY_ERROR deny.
        """
        cap, win = params.STRUCTURING_THRESHOLD_EUR, params.STRUCTURING_WINDOW
        since = ctx.now - win
        prior = []
        for p in ctx.recent_payments:
            if p.amount_eur < 0:
                raise ValueError(f"recent payment to {p.beneficiary_id} has a negative amount")
            if p.beneficiary_id == a.beneficiary_id and p.executed_at >= since:
                prior.append(p.amount_eur)
        name = ben.display_name if ben is not None else a.beneficiary_id
        hours = f"{win.total_seconds() / 3600:g}h"
        n = len(prior) + 1
        total = sum(prior, Decimal(0)) + eur
        noun = "payment" if n == 1 else "payments"
        if eur > params.R2_MAX_PAYMENT_EUR:
            return _R("STRUCTURING", True, RiskLevel.R3,
                      f"payment of {_m(eur)} EUR eq. alone exceeds the {_m(params.R2_MAX_PAYMENT_EUR)} one-tap "
                      f"limit and is already stepped up by AMOUNT_TIER; not a split payment")
        ok = total <= cap
        return _R("STRUCTURING", ok, RiskLevel.R3,
                  f"{n} {noun} to {name} in the last {hours} would total {_m(total)} EUR, "
                  f"{'within' if ok else 'above'} the {_m(cap)} structuring threshold")

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
        out.append(self._fx_price_rule(ctx, q))
        eur = _to_eur(q.buy_amount, q.buy_currency)
        ok = eur <= params.R2_MAX_FX_EUR
        out.append(_R("FX_TIER", ok, RiskLevel.R3,
                      f"FX buy of {_m(eur)} EUR eq. {'is within' if ok else 'exceeds'} the "
                      f"{_m(params.R2_MAX_FX_EUR)} one-tap limit"))
        return out

    @staticmethod
    def _fx_price_rule(ctx: PolicyContext, q) -> RuleResult:
        """FX_PRICE_CHECK (R4, v3, AUDIT-1): the FX provider is untrusted, so the quote must match the bank's own
        price (params.FX_RATES + FX_FEE_RATE) to the cent, and must not be expired at ctx.now."""
        rate = params.FX_RATES.get((q.sell_currency, q.buy_currency))
        if rate is None:
            return _R("FX_PRICE_CHECK", False, RiskLevel.R4,
                      f"no reference rate for {q.sell_currency}->{q.buy_currency}")
        net = q2(q.buy_amount) / rate
        sell, fee = q2(net * (1 + params.FX_FEE_RATE)), q2(net * params.FX_FEE_RATE)
        problems = []
        if q.rate != rate:
            problems.append(f"rate {q.rate} differs from the reference {rate}")
        if q2(q.sell_amount) != sell or q2(q.fee) != fee:
            problems.append(f"price {_m(q.sell_amount)} {q.sell_currency} (fee {_m(q.fee)}) differs from the "
                            f"reference {_m(sell)} (fee {_m(fee)}) for {_m(q.buy_amount)} {q.buy_currency}")
        if q.expires_at < ctx.now:
            problems.append("quote has expired")
        elif q.expires_at > ctx.now + params.FX_QUOTE_TTL:
            problems.append("quote expiry is later than the bank allows")
        if problems:
            return _R("FX_PRICE_CHECK", False, RiskLevel.R4, "FX quote rejected: " + "; ".join(problems))
        return _R("FX_PRICE_CHECK", True, RiskLevel.R4,
                  f"quote matches the reference price: {_m(sell)} {q.sell_currency} incl. {_m(fee)} fee")

    # ------------------------------------------------------------------ add beneficiary
    def _add_beneficiary(self, ctx: PolicyContext, a: AddBeneficiaryAction,
                         sim: SimulationResult) -> list[RuleResult]:
        problem = iban_problem(a.iban)
        sk = name_skeleton(a.display_name)
        clash = next((b for b in ctx.beneficiaries if b.user_id == ctx.user_id and sk and sk in
                      {name_skeleton(x) for x in (b.beneficiary_id, b.display_name, *b.aliases)}), None)
        return [
            self._sim_rule(a.action_id, sim),
            _R("IBAN_VALID", problem is None, RiskLevel.R4,
               f"IBAN ending {_norm_iban(a.iban)[-4:]} is a valid IBAN (format and mod-97 check digits)"
               if problem is None else f"IBAN is not valid: {problem}"),
            _R("PAYEE_NAME_UNIQUE", clash is None, RiskLevel.R4,
               f"no existing payee, alias or id looks like {a.display_name!r}" if clash is None else
               f"{a.display_name!r} looks the same as your existing payee {clash.display_name} "
               f"(name, alias or id); a second payee under that name would be ambiguous"),
            _sanctions_rule(a.display_name, a.iban, "new payee", iban_invalid_denied=True),
            _vop_rule(ctx, a.display_name, a.iban),
            _R("NEW_PAYEE_ACTION", False, RiskLevel.R3,
               f"adding a new payee ({a.display_name}, IBAN ending {_norm_iban(a.iban)[-4:]}) "
               f"always requires step-up"),
        ]
