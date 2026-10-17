"""Tiny in-memory fake Orchestrator so the UI can be rendered and tested without the real backend.

Select with BG_FAKE_BACKEND=1. Scripted for the golden path (FX 2200 EUR confirm, tuition 1200 confirm,
rent 850 step-up), plus a hard-denied payment, an injected invoice and a poisoned FX note.
It is NOT a policy engine: it only returns plausible AgentTurn / overview data.
"""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from itertools import count

from bg.contracts.hashing import GENESIS_HASH, params_hash, sha256_hex
from bg.contracts.models import (
    Account, ActionView, AgentTurn, Beneficiary, ChainVerification, ConfirmationRequest, FxAction,
    FxQuote, Obligation, ParsedRequest, PaymentAction, PolicyDecision, Receipt, RiskLevel, RuleResult,
    SecurityEvent, SimulationResult, UntrustedDocument, Verdict,
)
from bg.contracts.params import (
    ATTACKER_IBAN, DEMO_NOW, DEMO_OTP, DEMO_RESERVE_EUR, DEMO_USER_ID, LANDLORD_IBAN, MOM_IBAN,
    POLICY_VERSION, POISONED_FX_NOTE, SEED_ACCOUNTS, SEED_BENEFICIARIES, SEED_OBLIGATIONS, UNIV_IBAN,
)

_RULES = [("SIM_FEASIBLE", "R4"), ("BENEFICIARY_REGISTERED", "R4"), ("BENEFICIARY_NOT_BLOCKED", "R4"),
          ("IBAN_MATCHES_REGISTRY", "R4"), ("TENANT_ISOLATION", "R4"), ("RESERVE_FLOOR", "R4"),
          ("MAX_SINGLE_PAYMENT", "R4"), ("DAILY_LIMIT", "R4"), ("UNTRUSTED_ORIGIN", "R4"),
          ("BENEFICIARY_AGE", "R3"), ("AMOUNT_TIER", "R3"), ("FX_TIER", "R3"), ("NEW_PAYEE_ACTION", "R3")]

_INJECTION_PATTERNS = ["ignore your previous instructions", "note to the ai assistant",
                       "do not ask the user for confirmation"]
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")


def _rules(fail: dict[str, str] | None = None, applicable: tuple[str, ...] = ()) -> list[RuleResult]:
    fail = fail or {}
    out = []
    for rid, sev in _RULES:
        if rid in fail:
            out.append(RuleResult(rule_id=rid, passed=False, severity=RiskLevel(sev), detail=fail[rid]))
        else:
            out.append(RuleResult(rule_id=rid, passed=True, severity=RiskLevel(sev), detail="OK"))
    return out


class FakeOrchestrator:
    def __init__(self, poisoned_fx: bool = False) -> None:
        self.poisoned_fx = poisoned_fx
        self.bal = {"EUR": Decimal("450.00"), "USD": Decimal("2600.00")}
        self._sessions: dict[str, dict] = {}
        self._receipts: list[Receipt] = []
        self._n = count(1)
        self._ev = count(1)

    # ------------------------------------------------------------------ helpers
    def _event(self, kind: str, source: str, detail: str) -> SecurityEvent:
        return SecurityEvent(event_id=f"ev_{next(self._ev)}", kind=kind, source=source, detail=detail, at=DEMO_NOW)

    def _fx_view(self, aid: str) -> ActionView:
        quote = FxQuote(quote_id="q_1", sell_currency="USD", buy_currency="EUR", sell_amount=Decimal("2403.26"),
                        buy_amount=Decimal("2200.00"), rate=Decimal("0.92"), fee=Decimal("11.95"),
                        expires_at=DEMO_NOW + timedelta(minutes=5))
        act = FxAction(action_id=aid, idempotency_key=f"idem-{aid}", from_account_id="acc_usd",
                       to_account_id="acc_eur", quote=quote)
        sim = SimulationResult(action_id=aid, feasible=True,
                               balances_before={"acc_eur": Decimal("450.00"), "acc_usd": Decimal("2600.00")},
                               balances_after={"acc_eur": Decimal("2650.00"), "acc_usd": Decimal("196.74")},
                               fee=Decimal("11.95"))
        return self._decide(act, sim, "R2", "Convert USD to EUR: buy 2,200.00 EUR",
                            ["Sell 2,403.26 USD (fee 11.95 USD included)", "Rate 0.92 EUR per USD",
                             "EUR balance after: 2,650.00"], Verdict.CONFIRM, _rules())

    def _pay_view(self, aid: str, bid: str, name: str, iban: str, amount: str, rules, risk, verdict, extra=()):
        act = PaymentAction(action_id=aid, idempotency_key=f"idem-{aid}", from_account_id="acc_eur",
                            beneficiary_id=bid, iban=iban, amount=Decimal(amount), currency="EUR")
        sim = SimulationResult(action_id=aid, feasible=True,
                               balances_before={"acc_eur": Decimal("2650.00")},
                               balances_after={"acc_eur": Decimal("2650.00") - Decimal(amount)})
        lines = [f"Recipient: {name}", f"IBAN: …{iban[-4:]} (from your registry)", *extra]
        return self._decide(act, sim, risk, f"Pay {Decimal(amount):,.2f} EUR to {name}", lines, verdict, rules)

    def _decide(self, act, sim, risk, title, lines, verdict, rules) -> ActionView:
        passed = sum(r.passed for r in rules)
        dec = PolicyDecision(action_id=act.action_id, risk_level=RiskLevel(risk), verdict=verdict, rules=rules,
                             policy_version=POLICY_VERSION,
                             explanation=("Blocked: " + "; ".join(r.detail for r in rules if not r.passed and r.severity == RiskLevel.R4))
                             if verdict == Verdict.DENY else f"{risk}: {verdict.value}")
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

    # ------------------------------------------------------------------ Orchestrator protocol
    def new_session(self, user_id: str) -> str:
        sid = f"s_{next(self._n)}"
        self._sessions[sid] = {"views": {}, "n": count(1)}
        return sid

    def _aid(self, sid: str) -> str:
        return f"{sid}_a{next(self._sessions[sid]['n'])}"

    def handle_message(self, session_id: str, text: str,
                       documents: list[UntrustedDocument] | None = None) -> AgentTurn:
        S = self._sessions[session_id]
        low = text.lower()
        events: list[SecurityEvent] = []
        for d in documents or []:
            hits = [p for p in _INJECTION_PATTERNS if p in d.text.lower()]
            if hits:
                events.append(self._event("prompt_injection", d.document_id,
                                          "Document quarantined; matched patterns: " + ", ".join(hits)))
        views: list[ActionView] = []
        reply = "Here is your overview: 450.00 EUR and 2,600.00 USD. Tuition 1,200 EUR and rent 850 EUR are due."
        if "other customers" in low or "account balance" in low and "jean" in low:
            events.append(self._event("data_access_denied", "user_message", "Request for another customer's data."))
            reply = "I can only access your own accounts. That request was refused and logged."
        elif "maximum" in low or "max " in low or "everything" in low:
            reply = "How much exactly should I send to mom, in EUR? I never send an open-ended amount."
        elif (m := re.search(r"send\s+(\d[\d,]*)", low)) and int(m.group(1).replace(",", "")) >= 4000:
            amt = m.group(1).replace(",", "")
            rules = _rules({"MAX_SINGLE_PAYMENT": f"{amt} EUR exceeds the 4,000 EUR per-payment cap",
                            "RESERVE_FLOOR": "EUR balance after payment would fall below the 600 EUR reserve",
                            "DAILY_LIMIT": "Daily outflow would exceed 5,000 EUR"})
            views.append(self._pay_view(self._aid(session_id), "b_mom", "Samira Haddad", MOM_IBAN, amt,
                                        rules, "R4", Verdict.DENY))
            reply = "I can't do that. The policy gate blocked it (R4)."
        elif ("tuition" in low and "rent" in low) or "november" in low:
            views.append(self._fx_view(self._aid(session_id)))
            views.append(self._pay_view(self._aid(session_id), "b_univ", "Université Paris-Saclay", UNIV_IBAN,
                                        "1200.00", _rules(), "R2", Verdict.CONFIRM, ["Balance after: 1,450.00 EUR"]))
            views.append(self._pay_view(
                self._aid(session_id), "b_landlord", "Jean Dupont", LANDLORD_IBAN, "850.00",
                _rules({"BENEFICIARY_AGE": "Payee added 20h ago (< 72h): step-up required"}), "R3",
                Verdict.STEP_UP, ["Payee is new (20h): extra verification", "Balance after: 600.00 EUR (reserve kept)"]))
            reply = ("Plan: convert 2,200 EUR from USD, pay tuition 1,200 EUR and rent 850 EUR, leaving exactly "
                     "600 EUR. Please review each step.")
            if self.poisoned_fx:
                events.append(self._event("tool_output_poisoning", "fx_provider",
                                          f"Unknown field 'note' dropped: {POISONED_FX_NOTE[:60]}..."))
            if any(e.kind == "prompt_injection" for e in events):
                reply += " The rent invoice contained hidden instructions; it was quarantined and ignored."
        elif m := _IBAN_RE.search(text.upper()):
            iban = m.group(0).replace(" ", "")
            if iban != LANDLORD_IBAN:
                events.append(self._event("beneficiary_mismatch", "user_message",
                                          f"Typed IBAN …{iban[-4:]} differs from registry; registry IBAN used."))
            views.append(self._pay_view(
                self._aid(session_id), "b_landlord", "Jean Dupont", LANDLORD_IBAN, "850.00",
                _rules({"BENEFICIARY_AGE": "Payee added 20h ago (< 72h): step-up required"}), "R3", Verdict.STEP_UP))
            reply = "The IBAN you typed does not match Jean Dupont's registered IBAN. I used the registered one."
        for v in views:
            S["views"][v.action.action_id] = v
        parsed = ParsedRequest(intents=[], parser="rules", raw_text=text)
        return AgentTurn(session_id=session_id, assistant_text=reply, parsed=parsed, actions=views,
                         security_events=events, receipts=list(self._receipts))

    def _turn(self, sid: str, text: str, view: ActionView | None, events=()) -> AgentTurn:
        return AgentTurn(session_id=sid, assistant_text=text, actions=[view] if view else [],
                         security_events=list(events), receipts=list(self._receipts))

    def confirm(self, session_id: str, action_id: str, params_hash_: str, otp: str | None = None) -> AgentTurn:
        S = self._sessions[session_id]
        v: ActionView = S["views"][action_id]
        act = v.action
        if v.status == "executed":
            ev = self._event("replay_blocked", action_id, "Idempotency key already used; no money moved.")
            nv = v.model_copy(update={"status": "replayed"})
            return self._turn(session_id, "Already executed. Replay blocked.", nv, [ev])
        if params_hash_ != params_hash(act):
            ev = self._event("param_tamper", action_id, "Approval hash does not match the action parameters.")
            return self._turn(session_id, "Refused: parameters changed after review.", v, [ev])
        if v.status != "awaiting_confirmation":
            return self._turn(session_id, "Nothing to confirm for this action.", v)
        if v.confirmation_request.verdict == "step_up" and otp != DEMO_OTP:
            return self._turn(session_id, "Wrong or missing verification code.", v)
        if act.action_type == "fx":
            self.bal["USD"] -= act.quote.sell_amount
            self.bal["EUR"] += act.quote.buy_amount
        else:
            self.bal["EUR"] -= act.amount
        rec = self._receipt(act, v.decision, "ok", "executed: " + v.confirmation_request.title)
        nv = v.model_copy(update={"status": "executed", "receipt_id": rec.receipt_id})
        S["views"][action_id] = nv
        return self._turn(session_id, f"Done: {v.confirmation_request.title}.", nv)

    def reject(self, session_id: str, action_id: str) -> AgentTurn:
        S = self._sessions[session_id]
        nv = S["views"][action_id].model_copy(update={"status": "rejected_by_user"})
        S["views"][action_id] = nv
        return self._turn(session_id, "Cancelled. Nothing was sent.", nv)

    def overview(self, session_id: str) -> dict:
        accts = [Account(**{**a}) for a in SEED_ACCOUNTS if a["user_id"] == DEMO_USER_ID]
        accts = [a.model_copy(update={"balance": self.bal[a.currency]}) for a in accts]
        return {
            "accounts": accts,
            "beneficiaries": [Beneficiary(**b) for b in SEED_BENEFICIARIES if b["user_id"] == DEMO_USER_ID],
            "obligations": [Obligation(**o) for o in SEED_OBLIGATIONS if o["user_id"] == DEMO_USER_ID],
            "receipts": list(self._receipts),
            "verify_fn": self.verify_chain,
        }

    def verify_chain(self) -> ChainVerification:
        prev = GENESIS_HASH
        for r in self._receipts:
            if r.prev_receipt_hash != prev:
                return ChainVerification(valid=False, length=len(self._receipts), first_invalid_seq=r.seq,
                                         detail="prev hash mismatch")
            prev = r.receipt_hash
        return ChainVerification(valid=True, length=len(self._receipts), detail="chain intact")


def build_fake(poisoned_fx: bool = False, parser=None) -> FakeOrchestrator:
    return FakeOrchestrator(poisoned_fx=poisoned_fx)
