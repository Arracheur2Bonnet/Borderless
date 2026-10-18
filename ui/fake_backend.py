"""Tiny in-memory fake Orchestrator so the UI can be rendered and tested without the real backend.

Select with BG_FAKE_BACKEND=1. It simulates the v2 behaviour of the real agent closely enough for the UI:
golden path (FX 2200 EUR confirm, tuition 1200 confirm, rent 850 step-up), multi-turn pending_question,
Correct / Cancel through the dialogue, affordability projections, OTP attempts and lockout, structuring and
sanctions denials, receipts with a signed customer anchor.
It is NOT a policy engine: it only returns plausible AgentTurn / overview data.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import timedelta
from decimal import Decimal
from itertools import count

from bg.contracts.hashing import GENESIS_HASH, params_hash, sha256_hex
from bg.contracts.models import (
    Account, ActionView, AddBeneficiaryAction, AgentTurn, Beneficiary, ChainVerification, ConfirmationRequest,
    FxAction, FxQuote, Obligation, ParsedRequest, PaymentAction, PolicyDecision, Projection, Receipt,
    ReceiptAnchor, RiskLevel, RuleResult, SecurityEvent, SimulationResult, UntrustedDocument, Verdict,
)
from bg.contracts.params import (
    DAILY_OUTFLOW_LIMIT_EUR, DEMO_NOW, DEMO_OTP, DEMO_RECEIPT_KEY, DEMO_RESERVE_EUR, DEMO_USER_ID, FX_FEE_RATE,
    FX_RATES, LANDLORD_IBAN, MAX_OTP_ATTEMPTS, MAX_SINGLE_PAYMENT_EUR, POLICY_VERSION, POISONED_FX_NOTE,
    R2_MAX_PAYMENT_EUR, SANCTIONS_WATCHLIST_IBANS, SANCTIONS_WATCHLIST_NAMES, SEED_ACCOUNTS, SEED_BENEFICIARIES,
    SEED_OBLIGATIONS, STRUCTURING_THRESHOLD_EUR,
)

_RULES = [("SIM_FEASIBLE", "R4"), ("BENEFICIARY_REGISTERED", "R4"), ("BENEFICIARY_NOT_BLOCKED", "R4"),
          ("IBAN_MATCHES_REGISTRY", "R4"), ("TENANT_ISOLATION", "R4"), ("RESERVE_FLOOR", "R4"),
          ("MAX_SINGLE_PAYMENT", "R4"), ("DAILY_LIMIT", "R4"), ("UNTRUSTED_ORIGIN", "R4"),
          ("SANCTIONS_SCREEN", "R4"), ("BENEFICIARY_AGE", "R3"), ("AMOUNT_TIER", "R3"), ("FX_TIER", "R3"),
          ("NEW_PAYEE_ACTION", "R3"), ("STRUCTURING", "R3")]

_INJECTION_PATTERNS = ["ignore your previous instructions", "note to the ai assistant",
                       "do not ask the user for confirmation"]
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{2,4}){3,8}\b")
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_BENS = [b for b in SEED_BENEFICIARIES if b["user_id"] == DEMO_USER_ID]
_OBS = {o["kind"]: o for o in SEED_OBLIGATIONS if o["user_id"] == DEMO_USER_ID}
_Q = Decimal("0.01")


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", "")).quantize(_Q)


_FX_RULES = [("SIM_FEASIBLE", "R4"), ("TENANT_ISOLATION", "R4"), ("RESERVE_FLOOR", "R4"), ("FX_PRICE_CHECK", "R4"),
             ("FX_TIER", "R3")]
_PAYEE_RULES = [("SIM_FEASIBLE", "R4"), ("IBAN_VALID", "R4"), ("PAYEE_NAME_UNIQUE", "R4"), ("SANCTIONS_SCREEN", "R4"),
                ("NEW_PAYEE_ACTION", "R3")]
_PAYMENT_RULES = [r for r in _RULES if r[0] not in ("FX_TIER", "NEW_PAYEE_ACTION")]


def _rules(fail: dict[str, str] | None = None, table: list | None = None) -> list[RuleResult]:
    fail = fail or {}
    return [RuleResult(rule_id=rid, passed=rid not in fail, severity=RiskLevel(sev), detail=fail.get(rid, "OK"))
            for rid, sev in (table or _PAYMENT_RULES)]


class _StepUp:
    """Per-USER wrong-code counter, like bg.ledger.stepup.StepUpAttempts (shared by all sessions)."""

    def __init__(self, max_attempts: int = MAX_OTP_ATTEMPTS) -> None:
        self.max_attempts, self._fails, self._locked = max_attempts, {}, set()

    def remaining(self, user_id: str) -> int:
        return 0 if user_id in self._locked else max(self.max_attempts - self._fails.get(user_id, 0), 0)

    def locked(self, user_id: str) -> bool:
        return user_id in self._locked

    def record_failure(self, user_id: str) -> int:
        self._fails[user_id] = self._fails.get(user_id, 0) + 1
        if self._fails[user_id] >= self.max_attempts:
            self._locked.add(user_id)
        return self.remaining(user_id)

    def record_success(self, user_id: str) -> None:
        if user_id not in self._locked:
            self._fails.pop(user_id, None)


def _resolve(low: str) -> dict | None:
    for b in _BENS:
        for n in [b["display_name"].lower(), *b["aliases"]]:
            if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", low):
                return b
    return None


def _fx_numbers(buy: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    base = (buy / FX_RATES[("USD", "EUR")]).quantize(_Q)
    fee = (base * FX_FEE_RATE).quantize(_Q)
    return base + fee, fee, base


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


class FakeOrchestrator:
    def __init__(self, poisoned_fx: bool = False) -> None:
        self.poisoned_fx = poisoned_fx
        self.bal = {"EUR": Decimal("450.00"), "USD": Decimal("2600.00")}
        self.paid_obligations: set[str] = set()
        self.executed_payments: list[tuple[str, Decimal]] = []   # (beneficiary_id, amount) in the window
        self._sessions: dict[str, dict] = {}
        self._receipts: list[Receipt] = []
        self._n = count(1)
        self._ev = count(1)
        self.step_up = _StepUp()

    # ------------------------------------------------------------------ helpers
    def _event(self, kind: str, source: str, detail: str) -> SecurityEvent:
        return SecurityEvent(event_id=f"ev_{next(self._ev)}", kind=kind, source=source, detail=detail, at=DEMO_NOW)

    def _aid(self, sid: str) -> str:
        return f"{sid}_a{next(self._sessions[sid]['n'])}"

    def _fx_view(self, aid: str, buy: Decimal, eur_before: Decimal | None = None) -> ActionView:
        sell, fee, _ = _fx_numbers(buy)
        eur_before = self.bal["EUR"] if eur_before is None else eur_before
        quote = FxQuote(quote_id=f"q_{aid}", sell_currency="USD", buy_currency="EUR", sell_amount=sell,
                        buy_amount=buy, rate=FX_RATES[("USD", "EUR")], fee=fee,
                        expires_at=DEMO_NOW + timedelta(minutes=5))
        act = FxAction(action_id=aid, idempotency_key=f"idem-{aid}", from_account_id="acc_usd",
                       to_account_id="acc_eur", quote=quote)
        feasible = sell <= self.bal["USD"]
        sim = SimulationResult(action_id=aid, feasible=feasible,
                               balances_before={"acc_eur": eur_before, "acc_usd": self.bal["USD"]},
                               balances_after={"acc_eur": eur_before + buy, "acc_usd": self.bal["USD"] - sell},
                               fee=fee)
        fail = {} if feasible else {"SIM_FEASIBLE": f"Insufficient USD: {sell:,.2f} needed"}
        verdict = Verdict.DENY if fail else Verdict.CONFIRM
        return self._decide(act, sim, "R4" if fail else "R2", f"Convert USD to EUR: buy {buy:,.2f} EUR",
                            [f"Sell {sell:,.2f} USD (fee {fee:,.2f} USD included)", "Rate 0.92 EUR per USD",
                             f"EUR balance after: {eur_before + buy:,.2f}"], verdict, _rules(fail, _FX_RULES))

    def _pay_view(self, sid: str, ben: dict, amount: Decimal, available: Decimal | None = None,
                  events: list | None = None, reserve: Decimal | None = None, label: str = "") -> ActionView:
        aid = self._aid(sid)
        available = self.bal["EUR"] if available is None else available
        name, bid = ben["display_name"], ben["beneficiary_id"]
        fail: dict[str, str] = {}
        if amount > available:
            fail["SIM_FEASIBLE"] = f"Insufficient EUR: {available:,.2f} available, {amount:,.2f} needed"
        if amount > MAX_SINGLE_PAYMENT_EUR:
            fail["MAX_SINGLE_PAYMENT"] = f"{amount:,.2f} EUR exceeds the {MAX_SINGLE_PAYMENT_EUR:,.0f} EUR per-payment cap"
        spent = sum((a for _, a in self.executed_payments), Decimal(0))
        if spent + amount > DAILY_OUTFLOW_LIMIT_EUR:
            fail["DAILY_LIMIT"] = f"Daily outflow would reach {spent + amount:,.2f} EUR (limit {DAILY_OUTFLOW_LIMIT_EUR:,.0f})"
        if reserve is not None and available - amount < reserve:
            fail["RESERVE_FLOOR"] = f"EUR after payment {available - amount:,.2f} would fall below your {reserve:,.2f} reserve"
        if ben["iban"] == LANDLORD_IBAN:
            fail["BENEFICIARY_AGE"] = "payee added 20.00h ago, less than the 72.00h new-payee window"
        if amount > R2_MAX_PAYMENT_EUR and amount <= MAX_SINGLE_PAYMENT_EUR:
            fail["AMOUNT_TIER"] = f"{amount:,.2f} EUR is above the {R2_MAX_PAYMENT_EUR:,.0f} EUR one-tap tier"
        prior = [a for b, a in self.executed_payments if b == bid]
        total = sum(prior, Decimal(0)) + amount
        if prior and amount <= R2_MAX_PAYMENT_EUR and total > STRUCTURING_THRESHOLD_EUR:
            detail = (f"{len(prior) + 1} payments to {name} within 24h total {total:,.2f} EUR "
                      f"(> {STRUCTURING_THRESHOLD_EUR:,.0f} EUR): possible structuring, step-up required")
            fail["STRUCTURING"] = detail
            if events is not None:
                events.append(self._event("structuring_suspected", aid, detail))
        rules = _rules(fail)
        r4 = [r for r in rules if not r.passed and r.severity == RiskLevel.R4]
        r3 = [r for r in rules if not r.passed and r.severity == RiskLevel.R3]
        risk, verdict = ("R4", Verdict.DENY) if r4 else (("R3", Verdict.STEP_UP) if r3 else ("R2", Verdict.CONFIRM))
        act = PaymentAction(action_id=aid, idempotency_key=f"idem-{aid}", from_account_id="acc_eur",
                            beneficiary_id=bid, iban=ben["iban"], amount=amount, currency="EUR")
        sim = SimulationResult(action_id=aid, feasible="SIM_FEASIBLE" not in fail,
                               balances_before={"acc_eur": available},
                               balances_after={"acc_eur": available - amount})
        lines = [f"Recipient: {name}", f"IBAN: …{ben['iban'][-4:]} (from your registry)"]
        lines += [f"Needs your one-time code: {r.detail}" for r in r3]
        lines.append(f"EUR balance after: {available - amount:,.2f}")
        title = f"Pay {amount:,.2f} EUR to {name}" + (f" ({label})" if label else "")
        return self._decide(act, sim, risk, title, lines, verdict, rules)

    def _decide(self, act, sim, risk, title, lines, verdict, rules) -> ActionView:
        passed = sum(r.passed for r in rules)
        blockers = [r.detail for r in rules if not r.passed and r.severity == RiskLevel.R4]
        dec = PolicyDecision(action_id=act.action_id, risk_level=RiskLevel(risk), verdict=verdict, rules=rules,
                             policy_version=POLICY_VERSION,
                             explanation=("Blocked: " + "; ".join(blockers)) if verdict == Verdict.DENY
                             else f"{risk}: {verdict.value}")
        if verdict == Verdict.DENY:
            rec = self._receipt(act, dec, None, "denied: " + title)
            return ActionView(action=act, simulation=sim, decision=dec, status="denied", receipt_id=rec.receipt_id)
        cr = ConfirmationRequest(action_id=act.action_id, params_hash=params_hash(act), verdict=verdict.value,
                                 risk_level=RiskLevel(risk), title=title, lines=lines,
                                 checks_passed=passed, checks_total=len(rules))
        return ActionView(action=act, simulation=sim, decision=dec, status="awaiting_confirmation",
                          confirmation_request=cr)

    def _receipt(self, act, dec, conf, summary) -> Receipt:
        seq = len(self._receipts) + 1
        prev = self._receipts[-1].receipt_hash if self._receipts else GENESIS_HASH
        h = lambda tag, o: sha256_hex({"t": tag, "o": o})  # noqa: E731
        body = dict(seq=seq, receipt_id=f"r_{seq}", action_id=act.action_id, created_at=DEMO_NOW,
                    intent_hash=h("intent", act.action_id), action_hash=params_hash(act),
                    simulation_hash=h("sim", act.action_id), policy_hash=sha256_hex(dec),
                    confirmation_hash=h("conf", act.action_id) if conf else None,
                    result_hash=h("res", summary), prev_receipt_hash=prev, summary=summary)
        rec = Receipt(**body, receipt_hash=sha256_hex(body))
        self._receipts.append(rec)
        return rec

    @staticmethod
    def _sign(seq: int, h: str) -> str:
        return hmac.new(DEMO_RECEIPT_KEY.encode(), f"{seq}:{h}".encode(), hashlib.sha256).hexdigest()

    def anchor(self) -> ReceiptAnchor | None:
        if not self._receipts:
            return None
        head = self._receipts[-1]
        return ReceiptAnchor(seq=head.seq, receipt_hash=head.receipt_hash,
                             signature=self._sign(head.seq, head.receipt_hash), issued_at=DEMO_NOW)

    def _turn(self, sid: str, text: str, views=(), events=(), n_before: int | None = None, **kw) -> AgentTurn:
        new = self._receipts[n_before:] if n_before is not None else []
        return AgentTurn(session_id=sid, assistant_text=text, actions=list(views), security_events=list(events),
                         receipts=list(new), anchor=self.anchor() if new else None, **kw)

    # ------------------------------------------------------------------ Orchestrator protocol
    def new_session(self, user_id: str) -> str:
        sid = f"s_{next(self._n)}"
        self._sessions[sid] = {"views": {}, "n": count(1), "draft": None, "otp_fails": {}}
        return sid

    def handle_message(self, session_id: str, text: str,
                       documents: list[UntrustedDocument] | None = None) -> AgentTurn:
        S = self._sessions[session_id]
        n0 = len(self._receipts)
        low = text.lower().strip()
        events: list[SecurityEvent] = []
        for d in documents or []:
            hits = [p for p in _INJECTION_PATTERNS if p in d.text.lower()]
            if hits:
                events.append(self._event("prompt_injection", d.document_id,
                                          f"Document {d.filename} quarantined; matched patterns: " + ", ".join(hits)))
        draft, S["draft"] = S["draft"], None       # a draft lives for exactly one following message
        views: list[ActionView] = []
        projection = None
        question = None
        no_iban = _IBAN_RE.sub(" ", text.upper()).lower()
        amounts = [_dec(m) for m in re.findall(_NUM, no_iban)]
        ben = _resolve(low)
        has_pay = re.search(r"\b(send|pay|transfer|wire)\b", low)

        if draft and (m := re.fullmatch(r"(?:to\s+)?(?:" + _NUM + r")?\s*(?:eur|euros?|€)?\s*(?:to\s+(.+))?", low)) \
                and (m.group(1) or m.group(2)):
            amount = _dec(m.group(1)) if m.group(1) else draft.get("amount")
            ben = _resolve(m.group(2) or "") or draft.get("ben")
            if amount is None or ben is None:
                S["draft"] = {"amount": amount, "ben": ben}
                question = "Who should receive it?" if ben is None else f"How much should I send to {ben['display_name']}?"
                reply = question
            else:
                views.append(self._pay_view(session_id, ben, amount, events=events))
                reply = self._one_reply(views[0])
        elif re.match(r"(cancel|never mind|nevermind|stop)\b", low):
            pending = [v for v in S["views"].values() if v.status == "awaiting_confirmation"]
            targets = pending if re.search(r"\b(everything|all)\b", low) else pending[-1:]
            for v in targets:
                nv = v.model_copy(update={"status": "rejected_by_user"})
                S["views"][v.action.action_id] = nv
                views.append(nv)
            reply = ("Cancelled: " + "; ".join(v.confirmation_request.title for v in targets if v.confirmation_request)
                     + ". Nothing was executed." if targets else "There is no pending action to cancel.")
        elif re.search(r"\b(actually|instead|change the amount)\b", low) and amounts:
            pending = [v for v in S["views"].values() if v.status == "awaiting_confirmation"]
            if not pending:
                reply = "There is no pending action to change."
            else:
                old = pending[-1]
                nv_old = old.model_copy(update={"status": "rejected_by_user"})
                S["views"][old.action.action_id] = nv_old
                views.append(nv_old)
                if old.action.action_type == "payment":
                    b = next(x for x in _BENS if x["beneficiary_id"] == old.action.beneficiary_id)
                    new = self._pay_view(session_id, b, amounts[0], events=events)
                else:
                    new = self._fx_view(self._aid(session_id), amounts[0])
                views.append(new)
                old_t = old.confirmation_request.title if old.confirmation_request else "the previous step"
                new_t = new.confirmation_request.title if new.confirmation_request else "a new step"
                reply = f'Changed: I cancelled "{old_t}" and prepared "{new_t}" instead.\n' + self._one_reply(new)
        elif re.match(r"(can|could|would|what if|how much will)\b", low):
            projection, reply = self._afford(low, ben, amounts)
        elif "other customers" in low or ("account balance" in low and "jean" in low):
            events.append(self._event("data_access_denied", "user_message", "Request for another customer's data."))
            reply = "I can only show your own accounts and payees."
        elif "maximum" in low or "everything" in low:
            S["draft"] = {"amount": None, "ben": ben}
            question = f"How much exactly should I send to {ben['display_name'] if ben else 'them'}, in EUR?"
            reply = question + (" I don't send \"everything\" or \"the maximum\"; give me a figure and I'll check "
                                "it against your balance and limits.")
        elif m := re.match(r"(?:please\s+)?(?:add|register)\s+(.+?)\s+(?:with|,)\s*(?:iban\s*)?(.+)$", text, re.I):
            reply = self._add_payee(session_id, m.group(1), m.group(2), views, events)
        elif re.search(r"\b(buy|convert|exchange)\b", low) and amounts and not has_pay:
            views.append(self._fx_view(self._aid(session_id), amounts[0]))
            reply = self._one_reply(views[0])
        elif (m := _IBAN_RE.search(text.upper())) and has_pay:
            iban = m.group(0).replace(" ", "")
            b = ben or next(x for x in _BENS if x["iban"] == LANDLORD_IBAN)
            if iban != b["iban"]:
                events.append(self._event("beneficiary_mismatch", "user_message",
                                          f"Typed IBAN …{iban[-4:]} differs from registry; registry IBAN used."))
            views.append(self._pay_view(session_id, b, amounts[0] if amounts else Decimal("850.00"), label="rent"))
            reply = f"The IBAN you typed does not match {b['display_name']}'s registered IBAN. I used the registered one."
        elif has_pay and not amounts and ("tuition" in low or "rent" in low or "november" in low) or \
                (has_pay and "tuition" in low and "rent" in low):
            reply = self._plan(session_id, low, views, events)
        elif has_pay and ben and amounts:
            views.append(self._pay_view(session_id, ben, amounts[0], events=events))
            v = views[0]
            reply = self._one_reply(v)
        elif has_pay and amounts and not ben and not re.search(r"\b(tuition|rent)\b", low):
            S["draft"] = {"amount": amounts[0], "ben": None}
            question = f"Who should receive the {amounts[0]:,.2f} EUR?"
            reply = question
        elif has_pay and ben:
            S["draft"] = {"amount": None, "ben": ben}
            question = f"How much should I send to {ben['display_name']}? For example: 50 euros."
            reply = question
        elif "overview" in low or "balance" in low:
            reply = (f"Here is your overview: {self.bal['EUR']:,.2f} EUR and {self.bal['USD']:,.2f} USD. "
                     "Tuition 1,200 EUR and rent 850 EUR are due.")
        else:
            reply = ("Sorry, I didn't get that. Try: \"what's my balance?\", \"pay my rent\", "
                     "\"send 50 euros to mom\" or \"convert 200 dollars\".")
        if any(e.kind == "prompt_injection" for e in events):
            names = ", ".join(d.filename for d in documents or [])
            reply = (f"Watch out: your invoice ({names}) contains hidden text telling me to send money to another "
                     "account without asking you. I ignored it. " + reply)
        for v in views:
            S["views"][v.action.action_id] = v
        parsed = ParsedRequest(intents=[], parser="rules", raw_text=text)
        return self._turn(session_id, reply, views, events, n0, parsed=parsed, projection=projection,
                          pending_question=question)

    def _one_reply(self, v: ActionView) -> str:
        if v.status == "denied":
            why = v.decision.explanation if v.decision else "the rules blocked it"
            return f"I can't do that: {why}. Nothing was sent."
        cr = v.confirmation_request
        how = "I need your one-time code for this one." if cr.verdict == "step_up" else "One tap to approve."
        return f"{cr.title}. {how}"

    def _plan(self, sid: str, low: str, views: list, events: list) -> str:
        kinds = [k for k in ("tuition", "rent") if k in low and k not in self.paid_obligations]
        if not kinds and "november" in low:
            kinds = [k for k in ("tuition", "rent") if k not in self.paid_obligations]
        m = re.search(r"(?:keep|at least)\D{0,20}" + _NUM, low)
        reserve = _dec(m.group(1)) if m else None
        need = sum((_OBS[k]["amount"] for k in kinds), Decimal(0)) + (reserve or 0)
        short = max(Decimal(0), need - self.bal["EUR"]).quantize(_Q)
        available = self.bal["EUR"]
        if short:
            views.append(self._fx_view(self._aid(sid), short))
            available += short
        for k in kinds:
            ob = _OBS[k]
            b = next(x for x in _BENS if x["beneficiary_id"] == ob["beneficiary_id"])
            v = self._pay_view(sid, b, ob["amount"], available, events, reserve, label=k)
            views.append(v)
            available -= ob["amount"]
        parts = [f"{'convert ' + format(short, ',.2f') + ' EUR from USD, ' if short else ''}"
                 + ", ".join(f"pay {k} {_OBS[k]['amount']:,.2f} EUR" for k in kinds)]
        reply = "\n".join([self._one_reply(v) for v in views]
                          + [f"After all steps you will have {available:,.2f} EUR left.",
                             "Nothing moves until you approve each step, in this order."])
        if self.poisoned_fx and short:
            events.append(self._event("tool_output_poisoning", "fx_provider",
                                      f"Unknown field 'note' dropped: {POISONED_FX_NOTE[:60]}..."))
        return reply

    def _afford(self, low: str, ben: dict | None, amounts: list[Decimal]) -> tuple[Projection, str]:
        extra = amounts[0] if amounts else Decimal(0)
        bills = [k for k in ("tuition", "rent") if k not in self.paid_obligations] \
            if re.search(r"\b(bills|tuition|rent|obligations)\b", low) else []
        reserve = DEMO_RESERVE_EUR
        bills_total = sum((_OBS[k]["amount"] for k in bills), Decimal(0))
        fx_buy = max(Decimal(0), bills_total + reserve - self.bal["EUR"]).quantize(_Q) if bills else Decimal(0)
        sell, fee, _ = _fx_numbers(fx_buy) if fx_buy else (Decimal(0), Decimal(0), Decimal(0))
        eur_after_bills = self.bal["EUR"] + fx_buy - bills_total
        eur_after = eur_after_bills - extra
        usd_after = self.bal["USD"] - sell
        lines = []
        if fx_buy:
            lines.append(f"Buy {fx_buy:,.2f} EUR for {sell:,.2f} USD (fee {fee:,.2f} USD)")
        lines += [f"Pay {k} {_OBS[k]['amount']:,.2f} EUR" for k in bills]
        who = ben["display_name"] if ben else "the payee"
        if extra:
            lines.append(f"Send {extra:,.2f} EUR to {who}")
        ok = eur_after >= reserve
        lines.append(f"EUR after: {eur_after:,.2f} vs reserve {reserve:,.2f}: "
                     + ("kept" if ok else f"breached by {reserve - eur_after:,.2f}"))
        p = Projection(balances_now=dict(self.bal), balances_after={"EUR": eur_after, "USD": usd_after},
                       reserve_floor_eur=reserve, reserve_ok=ok, feasible=eur_after >= 0 and usd_after >= 0,
                       lines=lines)
        head = f"After {' and '.join(bills)} you would have {eur_after_bills:,.2f} EUR; " if bills else ""
        verdict = "within" if ok else "below"
        text = (f"{head}sending {extra:,.2f} more to {who} would leave {eur_after:,.2f} EUR, {verdict} your "
                f"{reserve:,.2f} EUR reserve. This is a simulation: nothing was proposed or executed.")
        return p, text[0].upper() + text[1:]

    def _add_payee(self, sid: str, name: str, iban_text: str, views: list, events: list) -> str:
        aid = self._aid(sid)
        iban = re.sub(r"\s", "", iban_text).upper().strip(".")
        act = AddBeneficiaryAction(action_id=aid, idempotency_key=f"idem-{aid}", display_name=name.strip(),
                                   iban=iban, category="other")
        n = _norm(name)
        hit = any(_norm(w) in n or n in _norm(w) for w in SANCTIONS_WATCHLIST_NAMES) or iban in SANCTIONS_WATCHLIST_IBANS
        sim = SimulationResult(action_id=aid, feasible=True, balances_before={}, balances_after={})
        if hit:
            detail = f"'{name.strip()}' / IBAN …{iban[-4:]} matches the sanctions watchlist"
            events.append(self._event("sanctions_hit", aid, detail))
            views.append(self._decide(act, sim, "R4", f"Add payee {name.strip()}", [], Verdict.DENY,
                                      _rules({"SANCTIONS_SCREEN": detail}, _PAYEE_RULES)))
            return (f"I can't add {name.strip()} as a payee: the payee matches an entry on the sanctions list, so the "
                    "bank can't deal with it. Nothing was saved.")
        views.append(self._decide(act, sim, "R3", f"Add payee {name.strip()}", [f"IBAN: …{iban[-4:]}"],
                                  Verdict.STEP_UP, _rules({"NEW_PAYEE_ACTION": "adding a payee needs a one-time code"},
                                                          _PAYEE_RULES)))
        return (f"Add {name.strip()} as a payee (account ending {iban[-4:]}). Adding a payee always needs your one-time "
                "code.\nNothing moves until you approve it.")

    def confirm(self, session_id: str, action_id: str, params_hash_: str, otp: str | None = None) -> AgentTurn:
        S = self._sessions[session_id]
        n0 = len(self._receipts)
        v: ActionView = S["views"][action_id]
        act = v.action
        if v.status in ("executed", "replayed"):
            ev = self._event("replay_blocked", action_id, "Idempotency key already used; no money moved.")
            nv = v.model_copy(update={"status": "replayed"})
            return self._turn(session_id, "Already executed. Replay blocked.", [nv], [ev], n0)
        if params_hash_ != params_hash(act):
            ev = self._event("param_tamper", action_id, "Approval hash does not match the action parameters.")
            return self._turn(session_id, "Refused: parameters changed after review.", [v], [ev], n0)
        if v.status != "awaiting_confirmation":
            return self._turn(session_id, f"This action is {v.status.replace('_', ' ')} and cannot be confirmed.", [v])
        if v.confirmation_request.verdict == "step_up" and self.step_up.locked(DEMO_USER_ID):
            return self._turn(session_id, "Approval by code is locked on your account after 3 wrong codes. The bank has "
                                          "to unlock it. Nothing was sent.", [v], (), n0)
        if v.confirmation_request.verdict == "step_up" and otp != DEMO_OTP:
            left = self.step_up.record_failure(DEMO_USER_ID)
            ev = self._event("step_up_failed", action_id, f"Wrong verification code ({left} left).")
            if left > 0:
                return self._turn(session_id, f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left; "
                                              "after that, approving with a code is locked on your account. "
                                              "Nothing has moved.", [v], [ev], n0)
            dec = v.decision.model_copy(update={"verdict": Verdict.DENY, "explanation":
                                                f"Locked after {MAX_OTP_ATTEMPTS} wrong verification codes."})
            rec = self._receipt(act, dec, None, "denied (step-up lockout): " + v.confirmation_request.title)
            nv = v.model_copy(update={"status": "denied", "decision": dec, "receipt_id": rec.receipt_id,
                                      "confirmation_request": None})
            S["views"][action_id] = nv
            return self._turn(session_id, f"Blocked: that was the {MAX_OTP_ATTEMPTS}rd wrong code in a row, so I didn't "
                                          "send it and I locked approval by code on your account to protect it. The "
                                          f"attempt is recorded (receipt {rec.receipt_id}).", [nv], [ev], n0)
        if v.confirmation_request.verdict == "step_up":
            self.step_up.record_success(DEMO_USER_ID)
        if act.action_type == "fx":
            self.bal["USD"] -= act.quote.sell_amount
            self.bal["EUR"] += act.quote.buy_amount
        elif act.action_type == "payment":
            self.bal["EUR"] -= act.amount
            self.executed_payments.append((act.beneficiary_id, act.amount))
            for k, ob in _OBS.items():
                if ob["beneficiary_id"] == act.beneficiary_id and ob["amount"] == act.amount:
                    self.paid_obligations.add(k)
        rec = self._receipt(act, v.decision, "ok", "executed: " + v.confirmation_request.title)
        # like the real orchestrator: the confirmation request is gone once the action ran
        nv = v.model_copy(update={"status": "executed", "receipt_id": rec.receipt_id, "confirmation_request": None})
        S["views"][action_id] = nv
        return self._turn(session_id, f"Done: {v.confirmation_request.title}.", [nv], (), n0)

    def reject(self, session_id: str, action_id: str) -> AgentTurn:
        S = self._sessions[session_id]
        nv = S["views"][action_id].model_copy(update={"status": "rejected_by_user"})
        S["views"][action_id] = nv
        return self._turn(session_id, "Cancelled. Nothing was sent.", [nv])

    def overview(self, session_id: str) -> dict:
        accts = [Account(**{**a}) for a in SEED_ACCOUNTS if a["user_id"] == DEMO_USER_ID]
        accts = [a.model_copy(update={"balance": self.bal[a.currency]}) for a in accts]
        return {
            "accounts": accts,
            "beneficiaries": [Beneficiary(**b) for b in _BENS],
            "obligations": [Obligation(**o) for o in SEED_OBLIGATIONS if o["user_id"] == DEMO_USER_ID],
            "receipts": list(self._receipts),
            "anchor": self.anchor(),
            "verify_fn": self.verify_chain,
            "demo_key_in_use": True,
            "signing_key": "demo signing key (public, BG_RECEIPT_KEY not set): anyone can forge an anchor",
        }

    def verify_chain(self, anchor: ReceiptAnchor | None = None) -> ChainVerification:
        prev = GENESIS_HASH
        n = len(self._receipts)
        for r in self._receipts:
            if r.prev_receipt_hash != prev:
                return ChainVerification(valid=False, length=n, first_invalid_seq=r.seq, detail="prev hash mismatch")
            prev = r.receipt_hash
        if anchor is not None:
            if not hmac.compare_digest(anchor.signature, self._sign(anchor.seq, anchor.receipt_hash)):
                return ChainVerification(valid=False, length=n, detail="anchor signature invalid")
            r = next((x for x in self._receipts if x.seq == anchor.seq), None)
            if r is None or r.receipt_hash != anchor.receipt_hash:
                return ChainVerification(valid=False, length=n, first_invalid_seq=anchor.seq,
                                         detail=f"receipt #{anchor.seq} missing or rewritten (chain truncated?)")
            return ChainVerification(valid=True, length=n, detail=f"chain intact and matches your anchor #{anchor.seq}")
        return ChainVerification(valid=True, length=n, detail="chain intact")


def build_fake(poisoned_fx: bool = False, parser=None) -> FakeOrchestrator:
    return FakeOrchestrator(poisoned_fx=poisoned_fx)
