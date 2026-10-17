"""The agent loop. The LLM (via the parser) proposes typed intents; this deterministic code builds
actions from the registry, the policy gate authorises, the ledger executes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from bg.contracts import params
from bg.contracts.hashing import params_hash, sha256_hex
from bg.contracts.interfaces import ContentGuard, IntentParser, Ledger, PolicyEngine, ReceiptStore
from bg.contracts.models import (
    ActionView, AddBeneficiary, AddBeneficiaryAction, AgentTurn, Beneficiary, Clarify, Confirmation,
    ConfirmationRequest, ExecutionResult, FxAction, FxConvert, FxQuote, Pay, ParsedRequest,
    PayObligations, PaymentAction, PolicyContext, PolicyDecision, ProposedAction, Receipt,
    ReadOverview, RiskLevel, SecurityEvent, SimulationResult, Unsupported, UntrustedDocument, Verdict,
)

from .rules_parser import RulesParser

_CENT = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return x.quantize(_CENT)


def _norm_iban(s: str) -> str:
    return "".join(s.split()).upper()


@dataclass
class _Rec:
    action: ProposedAction
    intent: Any
    title: str
    status: str = "proposed"
    sim: SimulationResult | None = None
    decision: PolicyDecision | None = None
    creq: ConfirmationRequest | None = None
    receipt_id: str | None = None
    reserve: Decimal | None = None


@dataclass
class _Session:
    session_id: str
    user_id: str
    turn: int = 0
    ev_n: int = 0
    reserve: Decimal | None = None
    records: dict[str, _Rec] = field(default_factory=dict)
    events: list[SecurityEvent] = field(default_factory=list)


class _Orchestrator:
    def __init__(self, ledger: Ledger, receipts: ReceiptStore, policy: PolicyEngine, guard: ContentGuard,
                 parser: IntentParser | None, now: datetime) -> None:
        self.ledger, self.receipts, self.policy, self.guard = ledger, receipts, policy, guard
        self.parser = parser or RulesParser()
        self.now = now
        self._sessions: dict[str, _Session] = {}
        self._n = 0

    # ------------------------------------------------------------------ public API
    def new_session(self, user_id: str) -> str:
        self._n += 1
        sid = f"s_{self._n}_{sha256_hex([user_id, self._n])[:8]}"
        self._sessions[sid] = _Session(session_id=sid, user_id=user_id)
        return sid

    def overview(self, session_id: str) -> dict[str, Any]:
        s = self._sess(session_id)
        dump = lambda xs: [x.model_dump(mode="python") for x in xs]  # noqa: E731
        return {
            "accounts": dump(self.ledger.get_accounts(s.user_id)),
            "beneficiaries": dump(self.ledger.get_beneficiaries(s.user_id)),
            "obligations": dump(self.ledger.get_obligations(s.user_id)),
            "receipts": dump(self.receipts.list(s.user_id)),
        }

    def handle_message(self, session_id: str, text: str,
                       documents: list[UntrustedDocument] | None = None) -> AgentTurn:
        s = self._sess(session_id)
        s.turn += 1
        s.reserve = None   # a reserve applies to the actions of the request that stated it
        turn_events: list[SecurityEvent] = list(self.guard.scan_user_message(text))
        for e in turn_events:
            self._remember(s, e)

        bens = self.ledger.get_beneficiaries(s.user_id)
        names = [b.display_name for b in bens] + [a for b in bens for a in b.aliases]
        parsed = self.parser.parse(text, beneficiary_names=names)

        notes: list[str] = []
        scans: dict[str, Any] = {}
        docs = list(documents or [])
        if any(isinstance(i, PayObligations) for i in parsed.intents):
            have = {d.document_id for d in docs}
            for ob in self.ledger.get_obligations(s.user_id):
                if ob.document_id and ob.document_id not in have:
                    d = self.ledger.get_document(s.user_id, ob.document_id)
                    if d is not None:
                        docs.append(d)
                        have.add(d.document_id)
        for d in docs:
            scan = self.guard.scan_document(d)
            scans[d.document_id] = scan
            if scan.injection_detected or scan.quarantined:
                turn_events.append(self._event(s, "prompt_injection", d.document_id,
                                               "Hidden instructions found in document; quarantined and treated as data only. "
                                               f"Patterns: {', '.join(scan.matched_patterns) or 'n/a'}"))
                notes.append(f"The document {d.filename or d.document_id} contained hidden instructions. "
                             "I quarantined it and ignored them; it is treated as data only.")

        proposals: list[tuple[Any, ProposedAction, str]] = []   # (intent, action, title)
        info: list[str] = []
        for idx, intent in enumerate(parsed.intents):
            self._expand(s, idx, intent, proposals, info, turn_events, scans)

        views: list[ActionView] = []
        new_receipts: list[Receipt] = []
        if proposals:
            actions = [a for _, a, _ in proposals]
            sims = self.ledger.simulate(s.user_id, actions)
            for (intent, action, title), sim in zip(proposals, sims):
                aid = action.action_id
                rec = _Rec(action=action, intent=intent, title=title, sim=sim, reserve=s.reserve)
                ctx = self._ctx(s, False, turn_events, rec.reserve)
                rec.decision = self.policy.evaluate(ctx, action, sim)
                s.records[aid] = rec
                v = rec.decision.verdict
                if v == Verdict.DENY:
                    new_receipts.append(self._deny(s, rec))
                elif v == Verdict.ALLOW:
                    new_receipts.append(self._execute(s, rec, None, turn_events))
                else:
                    rec.status = "awaiting_confirmation"
                    rec.creq = self._creq(rec)
                views.append(self._view(rec))

        for e in turn_events:
            if e not in s.events:
                self._remember(s, e)
        reply = self._compose(parsed, info, notes, views, turn_events)
        return AgentTurn(session_id=session_id, assistant_text=reply, parsed=parsed, actions=views,
                         security_events=turn_events, receipts=new_receipts)

    def confirm(self, session_id: str, action_id: str, params_hash_: str, otp: str | None = None) -> AgentTurn:
        s = self._sess(session_id)
        events: list[SecurityEvent] = []
        rec = s.records.get(action_id)
        if rec is None:
            return self._turn(s, f"I cannot find an action with id {action_id} in this session. Nothing was done.")
        if params_hash_ != params_hash(rec.action):
            events.append(self._event(s, "param_tamper", action_id,
                                      "The approval hash does not match the action parameters; approval refused."))
            return self._turn(s, "Refused: the approval does not match the exact parameters of this action "
                                 "(possible tampering). Nothing was executed.", [self._view(rec)], events)

        if rec.status == "executed" or rec.status == "replayed":
            res = self.ledger.execute(s.user_id, rec.action, None)
            if res.status == "executed":      # cannot normally happen; ledger decides
                res = res.model_copy(update={"status": "replayed"})
            events.append(self._event(s, "replay_blocked", action_id,
                                      "Repeated confirmation of an already executed action; no money moved again."))
            receipt = self._append(s, rec, None, res, f"REPLAY blocked: {rec.title}")
            view = self._view(rec).model_copy(update={"status": "replayed", "receipt_id": receipt.receipt_id})
            return self._turn(s, f"This action was already executed ({rec.title}). I did not move money again; "
                                 "the repeat was recorded as a replay.", [view], events, [receipt])
        if rec.status != "awaiting_confirmation":
            return self._turn(s, f"This action is {rec.status.replace('_', ' ')} and cannot be confirmed.",
                              [self._view(rec)])

        prior = rec.decision
        was_step_up = prior is not None and prior.verdict == Verdict.STEP_UP
        verified = False
        if was_step_up:
            if otp != params.DEMO_OTP:
                return self._turn(s, "The verification code is incorrect. The action stays pending; "
                                     "enter the code that was sent to you.", [self._view(rec)])
            verified = True

        sim = self.ledger.simulate(s.user_id, [rec.action])[0]
        rec.sim = sim
        decision = self.policy.evaluate(self._ctx(s, verified, events, rec.reserve), rec.action, sim)
        rec.decision = decision
        if decision.verdict == Verdict.DENY:
            r = self._deny(s, rec)
            return self._turn(s, self._deny_text(rec), [self._view(rec)], events, [r])
        if decision.verdict == Verdict.STEP_UP and not verified:
            rec.creq = self._creq(rec)
            return self._turn(s, "This action now needs step-up verification. Enter your one-time code to continue.",
                              [self._view(rec)], events)
        method = "step_up_otp" if verified else "tap"
        conf = Confirmation(action_id=action_id, params_hash=params_hash_, method=method, confirmed_at=self.now)  # type: ignore[arg-type]
        receipt = self._execute(s, rec, conf, events)
        if rec.status == "executed":
            text = f"Done: {rec.title}. Recorded in your Proof-of-Intent chain (receipt {receipt.receipt_id})."
        elif rec.status == "replayed":
            text = f"This action had already been executed ({rec.title}); no money moved again."
        else:
            text = f"The action failed: {rec.title}. {self._last_error(rec)}"
        return self._turn(s, text, [self._view(rec)], events, [receipt])

    def reject(self, session_id: str, action_id: str) -> AgentTurn:
        s = self._sess(session_id)
        rec = s.records.get(action_id)
        if rec is None:
            return self._turn(s, f"I cannot find an action with id {action_id}.")
        if rec.status != "awaiting_confirmation":
            return self._turn(s, f"This action is {rec.status.replace('_', ' ')}; there is nothing to cancel.",
                              [self._view(rec)])
        rec.status = "rejected_by_user"
        return self._turn(s, f"Cancelled: {rec.title}. Nothing was executed.", [self._view(rec)])

    # ------------------------------------------------------------------ intent expansion
    def _expand(self, s: _Session, idx: int, intent: Any, out: list, info: list[str],
                events: list[SecurityEvent], scans: dict[str, Any]) -> None:
        if isinstance(intent, ReadOverview):
            accs = self.ledger.get_accounts(s.user_id)
            bal = ", ".join(f"{a.currency} {_q(a.balance):.2f}" for a in accs) or "no accounts"
            ben = ", ".join(b.display_name for b in self.ledger.get_beneficiaries(s.user_id)) or "none"
            info.append(f"Your balances: {bal}. Saved payees: {ben}.")
        elif isinstance(intent, Clarify):
            info.append(intent.question)
        elif isinstance(intent, Unsupported):
            if intent.category == "data_access":
                events.append(self._event(s, "data_access_denied", "user_message", intent.reason))
            elif intent.category in ("jailbreak", "limit_change"):
                if not any(e.kind == "jailbreak_attempt" for e in events):
                    events.append(self._event(s, "jailbreak_attempt", "user_message", intent.reason))
            info.append(f"I cannot do that: {intent.reason}")
        elif isinstance(intent, PayObligations):
            self._expand_obligations(s, idx, intent, out, info, events, scans)
        elif isinstance(intent, FxConvert):
            self._expand_fx(s, idx, 0, intent, intent.sell_currency, intent.buy_currency, intent.buy_amount,
                            out, info, events)
        elif isinstance(intent, Pay):
            self._expand_pay(s, idx, intent, out, info, events)
        elif isinstance(intent, AddBeneficiary):
            iban = _norm_iban(intent.iban)
            a = AddBeneficiaryAction(action_id=self._aid(self._idem(s, idx, intent, 0)), idempotency_key=self._idem(s, idx, intent, 0),
                                     display_name=intent.display_name.strip(), iban=iban, category=intent.category)
            out.append((intent, a, f"Add payee {a.display_name} ({a.category}), IBAN ending {iban[-4:]}"))

    def _account(self, s: _Session, currency: str):
        return next((a for a in self.ledger.get_accounts(s.user_id) if a.currency == currency), None)

    def _expand_fx(self, s, idx, step, intent, sell, buy, amount, out, info, events) -> FxAction | None:
        sell_acc, buy_acc = self._account(s, sell), self._account(s, buy)
        if sell == buy or sell_acc is None or buy_acc is None or amount <= 0:
            info.append("I cannot convert that: I need a distinct pair of your own accounts and a positive amount.")
            return None
        amount = _q(amount)
        raw = self.ledger.fx_quote_raw(sell, buy, amount)
        quote, evs = self.guard.sanitize_tool_output("fx_quote", raw, FxQuote)
        events.extend(evs)
        if quote is None or not isinstance(quote, FxQuote):
            info.append("The FX provider returned an invalid quote, so I did not propose a conversion.")
            return None
        if quote.sell_currency != sell or quote.buy_currency != buy or quote.buy_amount != amount:
            info.append("The FX quote did not match the requested conversion, so I discarded it.")
            events.append(self._event(s, "tool_output_poisoning", "fx_quote",
                                      "Quote parameters differ from the request; quote discarded."))
            return None
        a = FxAction(action_id=self._aid(self._idem(s, idx, intent, step)), idempotency_key=self._idem(s, idx, intent, step),
                     from_account_id=sell_acc.account_id, to_account_id=buy_acc.account_id, quote=quote)
        out.append((intent, a, f"Convert {sell} to {buy}: buy {_q(quote.buy_amount):.2f} {buy} for "
                               f"{_q(quote.sell_amount):.2f} {sell} (fee {_q(quote.fee):.2f} {sell})"))
        return a

    def _expand_obligations(self, s, idx, intent, out, info, events, scans) -> None:
        bens = {b.beneficiary_id: b for b in self.ledger.get_beneficiaries(s.user_id)}
        obs_all = self.ledger.get_obligations(s.user_id)
        obs = []
        for kind in intent.obligation_kinds:
            obs.extend(o for o in obs_all if o.kind == kind)
        if not obs:
            info.append("I found no open obligations of that kind on your account.")
            return
        reserve = Decimal("0")
        if intent.keep_reserve is not None:
            reserve = intent.keep_reserve
            if intent.reserve_currency != "EUR":
                reserve = self.ledger.eur_equivalent(reserve, intent.reserve_currency)
            s.reserve = _q(reserve)
        eur = self._account(s, "EUR")
        if eur is None:
            info.append("You have no EUR account to pay these from.")
            return
        total = sum((o.amount for o in obs if o.currency == "EUR"), Decimal("0"))
        shortfall = _q(total + reserve - eur.balance)
        step = 0
        if shortfall > 0:
            fx = self._expand_fx(s, idx, step, intent, "USD", "EUR", shortfall, out, info, events)
            if fx is None:
                return
            step += 1
        for o in obs:
            b = bens.get(o.beneficiary_id)
            if b is None or b.user_id != s.user_id:
                info.append(f"The payee for your {o.kind} is not in your registry, so I skipped it.")
                continue
            acc = self._account(s, o.currency)
            if acc is None:
                continue
            scan = scans.get(o.document_id or "")
            doc_iban = (scan.extracted.get("iban") if scan is not None else None)
            if doc_iban and _norm_iban(doc_iban) != b.iban:
                events.append(self._event(s, "beneficiary_mismatch", o.document_id or o.obligation_id,
                                          f"IBAN in the {o.kind} document differs from the registered IBAN; "
                                          "the registered IBAN is used."))
            a = PaymentAction(action_id=self._aid(self._idem(s, idx, intent, step)), idempotency_key=self._idem(s, idx, intent, step),
                              from_account_id=acc.account_id, beneficiary_id=b.beneficiary_id, iban=b.iban,
                              amount=_q(o.amount), currency=o.currency, obligation_id=o.obligation_id,
                              memo=f"{o.kind} {o.due_date.isoformat()}")
            out.append((intent, a, self._pay_title(a, b)))
            step += 1

    def _expand_pay(self, s, idx, intent: Pay, out, info, events) -> None:
        if intent.amount_mode != "exact" or intent.amount is None or intent.amount <= 0:
            what = "the maximum amount" if intent.amount_mode == "all" else "an amount"
            info.append(f"How much would you like to send to {intent.beneficiary_ref}? I need an exact amount, "
                        f"I will not guess {what}.")
            return
        b = self.ledger.resolve_beneficiary(s.user_id, intent.beneficiary_ref)
        if b is None:
            info.append(f"I could not match \"{intent.beneficiary_ref}\" to exactly one of your saved payees. "
                        "Please use the payee's registered name, or add them first.")
            return
        acc = self._account(s, intent.currency)
        if acc is None:
            info.append(f"You have no {intent.currency} account to pay from.")
            return
        if intent.iban_hint and _norm_iban(intent.iban_hint) != b.iban:
            events.append(self._event(s, "beneficiary_mismatch", "user_message",
                                      f"The IBAN you typed (ending {_norm_iban(intent.iban_hint)[-4:]}) differs from the "
                                      f"registered IBAN for {b.display_name} (ending {b.iban[-4:]}); the registered IBAN is used."))
        a = PaymentAction(action_id=self._aid(self._idem(s, idx, intent, 0)), idempotency_key=self._idem(s, idx, intent, 0),
                          from_account_id=acc.account_id, beneficiary_id=b.beneficiary_id, iban=b.iban,
                          amount=_q(intent.amount), currency=intent.currency, memo=intent.memo[:140])
        out.append((intent, a, self._pay_title(a, b)))

    @staticmethod
    def _pay_title(a: PaymentAction, b: Beneficiary) -> str:
        return f"Pay {_q(a.amount):.2f} {a.currency} to {b.display_name} ({b.category})"

    # ------------------------------------------------------------------ execution / receipts
    def _execute(self, s: _Session, rec: _Rec, conf: Confirmation | None, events: list[SecurityEvent]) -> Receipt:
        res = self.ledger.execute(s.user_id, rec.action, conf)
        if res.status == "replayed":
            events.append(self._event(s, "replay_blocked", rec.action.action_id,
                                      "This request was already executed; no money moved again."))
        rec.status = {"executed": "executed", "replayed": "replayed"}.get(res.status, "failed")
        rec._last = res  # type: ignore[attr-defined]
        summary = {"executed": "EXECUTED", "replayed": "REPLAYED"}.get(res.status, "FAILED") + f": {rec.title}"
        r = self._append(s, rec, conf, res, summary)
        rec.receipt_id = r.receipt_id
        return r

    def _deny(self, s: _Session, rec: _Rec) -> Receipt:
        rec.status = "denied"
        reason = self._deny_reason(rec)
        res = ExecutionResult(action_id=rec.action.action_id, idempotency_key=rec.action.idempotency_key,
                              status="denied", error=reason)
        rec._last = res  # type: ignore[attr-defined]
        r = self._append(s, rec, None, res, f"DENIED: {rec.title}. {reason}")
        rec.receipt_id = r.receipt_id
        return r

    def _append(self, s: _Session, rec: _Rec, conf: Confirmation | None, res: ExecutionResult, summary: str) -> Receipt:
        # The store filters receipts by the user_id carried inside the signed intent.
        return self.receipts.append(action_id=rec.action.action_id,
                                    intent={"user_id": s.user_id, "intent": rec.intent}, action=rec.action,
                                    simulation=rec.sim, decision=rec.decision, confirmation=conf,
                                    result=res, summary=summary)

    @staticmethod
    def _deny_reason(rec: _Rec) -> str:
        d = rec.decision
        if d is None:
            return "Denied."
        failed = [r for r in d.rules if not r.passed and r.severity == RiskLevel.R4] or [r for r in d.rules if not r.passed]
        if failed:
            return "; ".join(f"{r.rule_id}: {r.detail}" for r in failed)
        return d.explanation

    def _deny_text(self, rec: _Rec) -> str:
        return f"Blocked: {rec.title}. Nothing was executed. Rule(s) that blocked it: {self._deny_reason(rec)}"

    @staticmethod
    def _last_error(rec: _Rec) -> str:
        res = getattr(rec, "_last", None)
        return f"Reason: {res.error}" if res is not None and res.error else ""

    # ------------------------------------------------------------------ helpers
    def _creq(self, rec: _Rec) -> ConfirmationRequest:
        d, sim, a = rec.decision, rec.sim, rec.action
        assert d is not None
        lines: list[str] = []
        if isinstance(a, PaymentAction):
            lines += [f"Recipient IBAN ends {a.iban[-4:]} (from your registry)", f"From account {a.from_account_id}"]
        elif isinstance(a, FxAction):
            lines += [f"Rate {a.quote.rate} (quote {a.quote.quote_id})", f"Fee {_q(a.quote.fee):.2f} {a.quote.sell_currency}"]
        elif isinstance(a, AddBeneficiaryAction):
            lines += [f"IBAN ends {a.iban[-4:]}", "Payments to this payee need extra verification for 72 hours"]
        if sim is not None:
            if sim.fee:
                lines.append(f"Fee: {_q(sim.fee):.2f}")
            lines += [f"Balance after: {acc} = {_q(v):.2f}" for acc, v in sim.balances_after.items()
                      if sim.balances_before.get(acc) != v]
        lines.append(d.explanation)
        return ConfirmationRequest(
            action_id=a.action_id, params_hash=params_hash(a),
            verdict="step_up" if d.verdict == Verdict.STEP_UP else "confirm",
            risk_level=d.risk_level, title=rec.title, lines=lines,
            checks_passed=sum(1 for r in d.rules if r.passed), checks_total=len(d.rules))

    def _view(self, rec: _Rec) -> ActionView:
        return ActionView(action=rec.action, simulation=rec.sim, decision=rec.decision, status=rec.status,  # type: ignore[arg-type]
                          confirmation_request=rec.creq if rec.status == "awaiting_confirmation" else None,
                          receipt_id=rec.receipt_id)

    def _ctx(self, s: _Session, verified: bool, events: list[SecurityEvent],
             reserve: Decimal | None) -> PolicyContext:
        return PolicyContext(
            user_id=s.user_id, now=self.now, accounts=self.ledger.get_accounts(s.user_id),
            beneficiaries=self.ledger.get_beneficiaries(s.user_id),
            executed_today_eur=self.ledger.executed_today_eur(s.user_id),
            reserve_floor_eur=reserve, step_up_verified=verified,
            security_events=[*s.events, *[e for e in events if e not in s.events]])

    def _turn(self, s: _Session, text: str, views: list[ActionView] | None = None,
              events: list[SecurityEvent] | None = None, receipts: list[Receipt] | None = None) -> AgentTurn:
        for e in events or []:
            self._remember(s, e)
        return AgentTurn(session_id=s.session_id, assistant_text=text, actions=views or [],
                         security_events=events or [], receipts=receipts or [])

    def _compose(self, parsed: ParsedRequest, info: list[str], notes: list[str], views: list[ActionView],
                 events: list[SecurityEvent]) -> str:
        out: list[str] = list(info)
        for e in events:
            if e.kind == "beneficiary_mismatch":
                out.append(f"Warning: {e.detail}")
            elif e.kind == "tool_output_poisoning":
                out.append("Warning: the FX provider's response contained untrusted extra content. "
                           "I discarded it and used only the validated quote fields.")
            elif e.kind in ("jailbreak_attempt", "data_access_denied") and not info:
                out.append(f"I cannot do that: {e.detail}")
        out += notes
        executed = [v for v in views if v.status in ("executed", "replayed")]
        waiting = [v for v in views if v.status == "awaiting_confirmation"]
        denied = [v for v in views if v.status == "denied"]
        failed = [v for v in views if v.status == "failed"]
        title = lambda v: v.confirmation_request.title if v.confirmation_request else None  # noqa: E731
        for v in executed:
            out.append(f"Done: {self._rec_title(v)}.")
        for v in waiting:
            cr = v.confirmation_request
            need = "needs step-up verification (one-time code)" if cr and cr.verdict == "step_up" else "needs your confirmation"
            out.append(f"{title(v)} {need}. Risk {v.decision.risk_level.value if v.decision else ''}; "
                       f"{cr.checks_passed}/{cr.checks_total} checks passed." if cr else "")
        for v in denied:
            out.append(f"Blocked: {self._rec_title(v)}. Nothing was executed. Rule(s) that blocked it: "
                       f"{self._decision_reason(v.decision)}")
        for v in failed:
            out.append(f"Failed: {self._rec_title(v)}.")
        if not out:
            out.append("I could not turn that into an action.")
        if waiting:
            out.append("Nothing moves until you confirm each step.")
        return "\n".join(x for x in out if x)

    def _rec_title(self, v: ActionView) -> str:
        r = self._sessions_rec(v.action.action_id)
        return r.title if r else v.action.action_id

    def _sessions_rec(self, action_id: str) -> _Rec | None:
        for s in self._sessions.values():
            if action_id in s.records:
                return s.records[action_id]
        return None

    @staticmethod
    def _decision_reason(d: PolicyDecision | None) -> str:
        if d is None:
            return "denied"
        failed = [r for r in d.rules if not r.passed and r.severity == RiskLevel.R4] or [r for r in d.rules if not r.passed]
        return "; ".join(f"{r.rule_id}: {r.detail}" for r in failed) if failed else d.explanation

    def _sess(self, session_id: str) -> _Session:
        if session_id not in self._sessions:
            raise KeyError(f"unknown session {session_id}")
        return self._sessions[session_id]

    def _event(self, s: _Session, kind: str, source: str, detail: str) -> SecurityEvent:
        s.ev_n += 1
        return SecurityEvent(event_id=f"ev_{s.session_id}_{s.ev_n}", kind=kind, source=source,  # type: ignore[arg-type]
                             detail=detail, at=self.now)

    def _remember(self, s: _Session, e: SecurityEvent) -> None:
        if e not in s.events:
            s.events.append(e)

    @staticmethod
    def _aid(idem: str) -> str:
        # deterministic: a re-sent request yields the same action (same params_hash), so the ledger replays it
        return "act_" + idem[len("idem_"):][:16]

    @staticmethod
    def _idem(s: _Session, idx: int, intent: Any, step: int) -> str:
        return "idem_" + sha256_hex([s.session_id, idx, intent, step])[:24]


def build_orchestrator(ledger: Ledger, receipts: ReceiptStore, policy: PolicyEngine, guard: ContentGuard,
                       parser: IntentParser | None = None, now: datetime = params.DEMO_NOW):
    return _Orchestrator(ledger, receipts, policy, guard, parser, now)
