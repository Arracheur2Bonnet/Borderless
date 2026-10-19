"""The agent loop. The LLM (via the parser) proposes typed intents; this deterministic code builds
actions from the registry, the policy gate authorises, the ledger executes."""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel

from bg.contracts import params
from bg.contracts.canonical import (
    iban_problem, is_whole_cents, money, name_skeleton, normalize_iban, parse_amount, q2,
)
from bg.contracts.hashing import params_hash, sha256_hex
from bg.contracts.interfaces import ContentGuard, IntentParser, Ledger, PolicyEngine, ReceiptStore
from bg.contracts.models import (
    ActionView, AddBeneficiary, AddBeneficiaryAction, AffordabilityCheck, AgentTurn, Beneficiary, Cancel,
    ChainVerification, Clarify, Confirmation, ConfirmationRequest, Correct, ExecutionResult, FxAction, FxConvert,
    FxQuote, Pay, ParsedRequest, PayObligations, PaymentAction, PolicyContext, PolicyDecision, ProposedAction,
    Projection, ProvideDetail, ReadOverview, Receipt, ReceiptAnchor, RiskLevel, RuleResult, SecurityEvent,
    SimulationResult, Unsupported, UntrustedDocument, Verdict,
)

from bg.ledger.stepup import StepUpAttempts

from . import help as _help
from .grounding import GroundingContext, Repair, ground_intents, is_question, own_sanctions_hit, sanctions_hits
from .rules_parser import RulesParser, is_affordability_question, payment_draft_hint

_CENT = Decimal("0.01")
AuditSink = Callable[[dict], None]

# policy rule id -> security event kind raised when that rule fails
_FLAG_RULES = {"STRUCTURING": "structuring_suspected", "SANCTIONS_SCREEN": "sanctions_hit"}
# Audit redaction (AUDIT-9). IBANs written with spaces, '-' or '.' between groups; a 6-digit code even when a
# full stop follows it ("my code is 246810."). Hex hashes and ids are protected by the \w look-arounds.
_IBAN_TEXT_RE = re.compile(r"\b[A-Za-z]{2}\d{2}(?:[ .\-]?[A-Za-z0-9]{2,4}){3,8}\b")
_OTP_TEXT_RE = re.compile(r"(?<![\w.])\d{6}(?!\w)")
# What a hidden instruction in a document asks for (used only to describe the attack to the user).
_INJ_AMOUNT = re.compile(r"\b(?:transfer|send|wire|remit|move|pay|vire[zr]?|envoie[zr]?|transf[èe]re[zr]?)\s+"
                         r"(?:the\s+|a\s+total\s+of\s+|imm[ée]diatement\s+)?"
                         r"(?:(EUR|USD|€|\$)\s*([0-9][0-9.,]*)|([0-9][0-9.,]*)\s*(EUR|USD|€|\$|euros?|dollars?))", re.I)
_ANY_IBAN_TEXT = re.compile(r"\b[A-Z]{2}[0-9]{2}(?:[ .\-]?[A-Z0-9]{4}){2,7}(?:[ .\-]?[A-Z0-9]{1,4})?\b")
_COUNTRY = {"GB": "a UK", "FR": "a French", "DE": "a German", "AE": "a UAE", "MA": "a Moroccan", "ES": "a Spanish",
            "IT": "an Italian", "NL": "a Dutch", "BE": "a Belgian", "CH": "a Swiss", "IE": "an Irish",
            "PT": "a Portuguese", "LT": "a Lithuanian", "CY": "a Cypriot", "TR": "a Turkish"}
_ZERO_WIDTH = re.compile("[\u00ad\u034f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


# v6 (AUDIT2-11): the longest chat message read at all; longer ones get a clear reply before any parsing.
MAX_MESSAGE_CHARS = 2000
# v6 (AUDIT2-2): the offline rules parser's sentences are written by us. What a model writes (Unsupported.reason,
# Clarify.question) is never shown as the bank's own words: a fixed sentence per category, and a model question
# only when it passes a filter.
_TRUSTED_PARSERS = frozenset({"rules", "cascade:rules"})
_REFUSAL_TEXT = {
    "data_access": "I can only show your own accounts and payees.",
    "limit_change": "I can't change or switch off your limits, confirmations or codes; they are set by the bank.",
    "jailbreak": "I can't change my instructions or skip any of the bank's checks. I can help with your balance, "
                 "your bills and payments to your saved payees.",
    "out_of_scope": "That's outside what I can do. I can help with your balance, your bills and payments to your "
                    "saved payees.",
}
_FIXED_CLARIFY = "I didn't fully understand that. Who should receive how much? For example \"send 50 euros to mom\"."
_Q_LINK = re.compile(r"https?:|ftp:|www\.|\b[\w.+-]+@[\w-]+\.[a-z]{2,}|\b[\w-]+\.(?:com|net|org|io|info|biz|ru|cn|example)\b",
                     re.I)
_Q_CODE = re.compile(r"\d(?:[\s.\-]?\d){5}")
_Q_CLAIM = re.compile(r"\b(?:done|sent|executed|completed|processed|transferred|approved|confirmed|receipt|rcpt_\w*)\b",
                      re.I)


def model_question_ok(q: str) -> bool:
    """A parser's Clarify.question is shown only if it is short and carries no link, address, code or claim that
    something was done (it is shown as the bank's own words)."""
    q = (q or "").strip()
    return bool(q) and len(q) <= 200 and not (_Q_LINK.search(q) or _Q_CODE.search(q) or _Q_CLAIM.search(q))


_RATE_Q = re.compile(r"\b(rate|exchange rate|how much (?:is|are|would|will)|worth|give me|get me|in (?:eur|euros?|usd|dollars?))\b",
                     re.I)
_RATE_AMT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(usd|dollars?|\$|eur|euros?|€)", re.I)


def _rate_answer(text: str) -> str | None:
    """Read-only answer to 'how much is 200 USD in EUR?' or 'what is the rate?'. Uses the same rate and fee
    as the sandbox FX provider. Never proposes or executes anything."""
    if not _RATE_Q.search(text):
        return None
    fee = params.FX_FEE_RATE
    usd_eur = params.FX_RATES[("USD", "EUR")]
    m = _RATE_AMT.search(text)
    if not m:
        if re.search(r"\brate\b", text, re.I):
            return (f"Today's rate: 1 USD = {usd_eur} EUR, plus a {fee * 100:.1f}% fee on the amount converted. "
                    "Nothing was converted.")
        return None
    parsed = parse_amount(m.group(1))
    if parsed is None:
        return "That amount is too large for me to work with. Try a smaller figure, for example 200 USD."
    amount = _q(parsed)
    sell = "USD" if m.group(2).lower() in ("usd", "dollar", "dollars", "$") else "EUR"
    buy = "EUR" if sell == "USD" else "USD"
    rate = params.FX_RATES[(sell, buy)]
    received = _q(amount / (Decimal(1) + fee) * rate)
    return (f"{money(amount)} {sell} would give you about {money(received)} {buy} at {rate} {buy} per {sell}, after "
            f"the {fee * 100:.1f}% fee. Nothing was converted; say \"convert\" if you want to do it.")


def _q(x: Decimal) -> Decimal:
    """The one rounding rule (ROUND_HALF_UP, shared with ledger and policy). Only applied to amounts that are
    already whole cents or to computed values; a user amount with sub-cent digits is clarified, never rounded."""
    return q2(x)


def _m(x: Decimal) -> str:
    """The one money display format: thousands separators, two decimals."""
    return money(x)


def fx_cost(q: FxQuote) -> tuple[Decimal, Decimal]:
    """(total cost of the conversion in the sold currency, that cost as a percentage of the amount sold before the
    fee). Derived only from the quote; the rate is the bank's single reference rate, so the fee is the whole cost
    and no comparison with any other provider is made (P2-02)."""
    net = q.sell_amount - q.fee
    pct = (q.fee / net * 100) if net > 0 else Decimal("0")
    return _q(q.fee), _q(pct)


def fx_cost_plain(q: FxQuote) -> str:
    """'11.96 USD, 0.50%' (for use inside a parenthesis)."""
    cost, pct = fx_cost(q)
    return f"{_m(cost)} {q.sell_currency}, {pct:.2f}%"


def fx_cost_text(q: FxQuote) -> str:
    """'11.96 USD (0.50%)'."""
    cost, pct = fx_cost(q)
    return f"{_m(cost)} {q.sell_currency} ({pct:.2f}%)"


def _norm_iban(s: str) -> str:
    return normalize_iban(s)


def _sub_cent_question(amount: Decimal, currency: str, what: str = "send") -> str:
    lo, hi = amount.quantize(_CENT, rounding=ROUND_FLOOR), amount.quantize(_CENT, rounding=ROUND_CEILING)
    options = f"{_m(lo)} or {_m(hi)} {currency}" if lo > 0 else f"{_m(hi)} {currency}"
    return (f"{format(amount, 'f')} {currency} has more than two decimals, and I only {what} whole cents. "
            f"Did you mean {options}?")


# ---------------------------------------------------------------------- audit redaction
def _mask_ibans(text: str) -> str:
    def rep(m: re.Match) -> str:
        raw = m.group(0)
        toks = re.findall(r"[ .\-]|[A-Za-z0-9]+", raw)
        while len(toks) > 1 and (toks[-1] in (" ", ".", "-") or (toks[-1].isalpha() and toks[-2] in (" ", ".", "-"))):
            toks.pop()                                      # do not swallow a trailing word ("... 55 instead")
        kept = "".join(toks)
        cand = "".join(t for t in toks if t not in (" ", ".", "-"))
        if not 15 <= len(cand) <= 34:
            return raw
        return "****" + cand[-4:].upper() + raw[len(kept):]
    return _IBAN_TEXT_RE.sub(rep, text)


def _redact(text: str) -> str:
    s = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))
    return _OTP_TEXT_RE.sub("******", _mask_ibans(s))


def _safe(value: Any, key: str | None = None) -> Any:
    """JSON-safe copy for audit records: IBANs reduced to the last 4 characters, OTP-like codes masked."""
    if key in ("iban", "iban_hint"):
        return ("****" + _norm_iban(str(value))[-4:]) if value else value
    if isinstance(value, BaseModel):
        return _safe(value.model_dump(mode="python"), key)
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in value.items() if str(k) not in ("otp",)}
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _redact(str(value))


# ---------------------------------------------------------------------- session state
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
    plan_id: str = ""
    plan_pos: int = 0
    otp_failures: int = 0
    flagged: set[str] = field(default_factory=set)
    last: ExecutionResult | None = None


@dataclass
class _Draft:
    """An unfinished payment waiting for one detail (amount or payee)."""
    question: str
    ref: str | None = None
    amount: Decimal | None = None
    currency: str = "EUR"
    iban_hint: str | None = None
    memo: str = ""

    def describe(self) -> str:
        bits = []
        if self.amount is not None:
            bits.append(f"{_m(self.amount)} {self.currency}")
        if self.ref:
            bits.append(f"to {self.ref}")
        return "the unfinished payment" + (f" ({' '.join(bits)})" if bits else "")


@dataclass
class _Session:
    session_id: str
    user_id: str
    turn: int = 0
    ev_n: int = 0
    reserve: Decimal | None = None
    records: dict[str, _Rec] = field(default_factory=dict)
    events: list[SecurityEvent] = field(default_factory=list)
    draft: _Draft | None = None


@dataclass
class _Turn:
    """What one handle_message call accumulates."""
    events: list[SecurityEvent]
    info: list[str] = field(default_factory=list)
    extra_eur: Decimal = Decimal("0")     # exact EUR payments asked alongside a bill in the same message
    notes: list[str] = field(default_factory=list)
    views: list[ActionView] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)
    projection: Projection | None = None
    question: str | None = None
    explained: set[str] = field(default_factory=set)   # event ids already explained in plain words
    trusted_text: bool = True   # the parser's own sentences may be shown (offline rules parser); a model's may not
    plain_clarify: set[int] = field(default_factory=set)   # ids of Clarify intents written by grounding: shown as is


class _Orchestrator:
    def __init__(self, ledger: Ledger, receipts: ReceiptStore, policy: PolicyEngine, guard: ContentGuard,
                 parser: IntentParser | None, now: datetime, audit_sink: AuditSink | None = None,
                 step_up: StepUpAttempts | None = None) -> None:
        self.ledger, self.receipts, self.policy, self.guard = ledger, receipts, policy, guard
        # AUDIT-3: wrong one-time codes are counted per user in a store shared by every session (the ledger's
        # by default), so a new action, a correction or a new session never gets a fresh budget.
        self.step_up = step_up or getattr(ledger, "step_up", None) or StepUpAttempts()
        self.parser = parser or RulesParser()
        self.now = now
        self.audit_sink = audit_sink
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
        anchor = self._anchor(s.user_id)
        demo_key = bool(getattr(self.receipts, "demo_key_in_use", False))
        user = s.user_id
        return {
            "accounts": dump(self.ledger.get_accounts(s.user_id)),
            "beneficiaries": dump(self.ledger.get_beneficiaries(s.user_id)),
            "obligations": dump(self.ledger.get_obligations(s.user_id)),
            "receipts": dump(self.receipts.list(s.user_id)),
            # AUDIT-10: the anchor and the verifier cover this customer's own chain only
            "anchor": anchor.model_dump(mode="python") if anchor is not None else None,
            "verify_fn": lambda a=None: self._verify(a, user),
            # AUDIT-12: say plainly when receipts are signed with the public demo key
            "demo_key_in_use": demo_key,
            "signing_key": ("demo signing key (public, BG_RECEIPT_KEY not set): anyone can forge an anchor; "
                            "a deployment sets its own key") if demo_key else "bank signing key (BG_RECEIPT_KEY)",
        }

    def _anchor(self, user_id: str) -> ReceiptAnchor | None:
        try:
            return self.receipts.anchor(user_id=user_id)        # type: ignore[call-arg]
        except TypeError:                                       # a store without per-customer chains
            return self.receipts.anchor()

    def _verify(self, anchor: ReceiptAnchor | dict | None = None, user_id: str | None = None) -> ChainVerification:
        """Verify the customer's receipt chain, optionally against a customer-held anchor (model or plain dict).
        A malformed anchor is reported as invalid, never raised (AUDIT-11)."""
        if anchor is not None and not isinstance(anchor, ReceiptAnchor):
            try:
                anchor = ReceiptAnchor.model_validate(anchor)
            except Exception:
                return ChainVerification(valid=False, length=0, detail="anchor is malformed")
        try:
            return self.receipts.verify_chain(anchor, user_id=user_id)   # type: ignore[call-arg]
        except TypeError:
            return self.receipts.verify_chain(anchor)

    def handle_message(self, session_id: str, text: str,
                       documents: list[UntrustedDocument] | None = None) -> AgentTurn:
        s = self._sess(session_id)
        try:
            return self._handle_message(s, session_id, text, documents)
        except Exception as exc:   # fail closed: bad data (a crafted document, a parser bug) never crashes the chat
            self._audit("clarify", s, error=type(exc).__name__)
            return AgentTurn(session_id=session_id,
                             assistant_text="Sorry, I couldn't process that safely, so I did nothing and nothing was "
                                            "sent. Please rephrase it, for example \"send 50 euros to mom\".")

    def _handle_message(self, s: "_Session", session_id: str, text: str,
                        documents: list[UntrustedDocument] | None) -> AgentTurn:
        if len(text or "") > MAX_MESSAGE_CHARS:      # AUDIT2-11: before any scan or parse
            self._audit("clarify", s, reason="message_too_long", text_chars=len(text or ""))
            return AgentTurn(session_id=session_id, assistant_text=(
                f"That message is {len(text):,} characters long and I read at most {MAX_MESSAGE_CHARS:,}, so I "
                "didn't read it and did nothing. Please send a shorter message, for example "
                "\"send 50 euros to mom\"."), pending_question=s.draft.question if s.draft else None)
        s.turn += 1
        s.reserve = None   # a reserve applies to the actions of the request that stated it
        t = _Turn(events=list(self.guard.scan_user_message(text)))
        for e in t.events:
            self._remember(s, e)

        bens = self.ledger.get_beneficiaries(s.user_id)
        names = [b.display_name for b in bens] + [a for b in bens for a in b.aliases]
        draft, s.draft = s.draft, None     # a draft lives for exactly one more message
        parsed = self.parser.parse(text, beneficiary_names=names,
                                   pending_question=draft.question if draft else None)
        t.trusted_text = parsed.parser in _TRUSTED_PARSERS
        parsed = self._questions_never_pay(parsed)
        parsed, repairs = self._ground(s, parsed, GroundingContext(
            text=text or "", beneficiaries=bens, draft_ref=draft.ref if draft else None,
            draft_amount=draft.amount if draft else None))
        for r in repairs:
            t.notes.append(r.note)
            if r.replaced_by is not None:
                t.plain_clarify.add(id(r.replaced_by))
            self._audit("grounding", s, parser=parsed.parser, intent=r.intent, field=r.field, reason=r.reason,
                        before=r.before, after=r.after)
            if r.event:
                ev = self._event(s, r.event, "user_message", r.event_detail)
                t.events.append(ev)
                t.explained.add(ev.event_id)
        parsed = self._sanctions_on_sources(s, parsed, text or "", list(documents or []), t)
        scoped = _help.apply(text or "", list(parsed.intents))   # P1-01: our words for help and unsupported topics
        if scoped is not None:
            topic, new_intents = scoped
            parsed = parsed.model_copy(update={"intents": new_intents})
            t.plain_clarify.update(id(i) for i in new_intents)
            self._audit("clarify", s, help_topic=topic)
        # AUDIT-9: the raw message is not logged (it can hold a code or bank details); a hash and length
        # identify it, and the parsed, masked intents carry what the step did.
        self._audit("parse", s, parser=parsed.parser,
                    text_sha256=hashlib.sha256((text or "").encode("utf-8")).hexdigest(), text_chars=len(text or ""),
                    intents=[i.model_dump(mode="python") for i in parsed.intents], notes=parsed.notes,
                    pending_question=draft.question if draft else None)
        intents = self._apply_draft(s, draft, list(parsed.intents), t)

        scans: dict[str, Any] = {}
        docs = list(documents or [])
        wanted = {k for i in intents if isinstance(i, PayObligations) for k in i.obligation_kinds}
        uploaded = {d.document_id for d in docs}
        if wanted:
            have = set(uploaded)
            for ob in self.ledger.get_obligations(s.user_id):
                # only the documents behind what was asked: paying tuition must not scan the rent invoice.
                # When the user attached their own invoice, that one is the document of record.
                if docs and len(wanted) == 1:
                    continue
                if ob.kind in wanted and ob.document_id and ob.document_id not in have:
                    d = self.ledger.get_document(s.user_id, ob.document_id)
                    if d is not None:
                        docs.append(d)
                        have.add(d.document_id)
        for d in docs:
            scan = self.guard.scan_document(d)
            scans[d.document_id] = scan
            if scan.injection_detected or scan.quarantined:
                t.events.append(self._event(s, "prompt_injection", d.document_id,
                                            "Hidden instructions found in document; quarantined and treated as data only. "
                                            f"Patterns: {', '.join(scan.matched_patterns) or 'n/a'}"))
                t.notes.append(self._injection_note(s, d, scan, wanted))
            # A swapped IBAN with no instruction text is the classic invoice fraud: tell the user explicitly.
            doc_iban = (scan.extracted or {}).get("iban")
            registered = {_norm_iban(b.iban) for b in self.ledger.get_beneficiaries(s.user_id)}
            if doc_iban and _norm_iban(doc_iban) not in registered:
                ev = self._event(s, "beneficiary_mismatch", d.document_id,
                                 f"Document IBAN ending {doc_iban[-4:]} is not a registered payee IBAN.")
                t.events.append(ev)
                t.explained.add(ev.event_id)
                t.notes.append(f"Careful: {d.filename or d.document_id} asks for payment to an account ending "
                               f"{doc_iban[-4:]}, which isn't saved for any of your payees. I only pay the account on "
                               "file. Banks never change account details by email: if the payee says they changed "
                               "bank, call them on a number you already know, then add the new account as a payee "
                               "(that needs your one-time code).")
            # An inflated amount is data too: the registry obligation is what gets paid, and the user is told.
            doc_amount = parse_amount((scan.extracted or {}).get("amount"))
            due = [o for o in self.ledger.get_obligations(s.user_id) if not wanted or o.kind in wanted]
            if doc_amount is not None and due and all(doc_amount != o.amount for o in due):
                expected = ", ".join(f"{o.kind} {_m(o.amount)} {o.currency}" for o in due)
                t.notes.append(f"Note: {d.filename or d.document_id} says {_m(doc_amount)}, but what you owe on record "
                               f"is {expected}. I only pay the amount on record.")

        proposals: list[tuple[Any, ProposedAction, str]] = []   # (intent, action, title)
        if any(isinstance(i, PayObligations) for i in intents):
            t.extra_eur = sum((i.amount for i in intents if isinstance(i, Pay) and i.amount_mode == "exact"
                               and i.amount is not None and i.currency == "EUR"), Decimal("0"))
        for idx, intent in enumerate(intents):
            self._expand(s, idx, intent, proposals, t, scans, draft, text, names)

        if proposals:
            actions = [a for _, a, _ in proposals]
            sims = self._simulate(s, actions)
            plan_id = f"plan_{s.turn}"
            for pos, ((intent, action, title), sim) in enumerate(zip(proposals, sims)):
                prev = s.records.get(action.action_id)
                rec = _Rec(action=action, intent=intent, title=title, sim=sim, reserve=s.reserve,
                           plan_id=plan_id, plan_pos=pos,
                           otp_failures=prev.otp_failures if prev is not None else 0)
                s.records[action.action_id] = rec
                r = self._decide(s, rec, t.events)
                if r is not None:
                    t.receipts.append(r)
                t.views.append(self._view(rec))

        for e in t.events:
            self._remember(s, e)
        reply = self._compose(t, s)
        return AgentTurn(session_id=session_id, assistant_text=reply, parsed=parsed, actions=t.views,
                         security_events=t.events, receipts=t.receipts, projection=t.projection,
                         anchor=self._anchor(s.user_id) if t.receipts else None,
                         pending_question=s.draft.question if s.draft else None)

    def confirm(self, session_id: str, action_id: str, params_hash_: str, otp: str | None = None) -> AgentTurn:
        s = self._sess(session_id)
        events: list[SecurityEvent] = []
        rec = s.records.get(action_id)
        if rec is None:
            self._audit("confirm", s, action_id=action_id, outcome="unknown_action")
            return self._turn(s, "I can't find that step in this conversation, so nothing was done.")
        if params_hash_ != params_hash(rec.action):
            events.append(self._event(s, "param_tamper", action_id,
                                      "The approval hash does not match the action parameters; approval refused."))
            self._audit("confirm", s, action_id=action_id, outcome="param_tamper")
            return self._turn(s, "I refused that approval: it doesn't match the exact details you were shown, which "
                                 "can mean it was tampered with. Nothing was sent.", [self._view(rec)], events)

        if rec.status == "executed" or rec.status == "replayed":
            self._audit("confirm", s, action_id=action_id, outcome="replay")
            res = self.ledger.execute(s.user_id, rec.action, None)
            if res.status == "executed":      # cannot normally happen; ledger decides
                res = res.model_copy(update={"status": "replayed"})
            self._audit("execute", s, action_id=action_id, status=res.status, error=res.error)
            events.append(self._event(s, "replay_blocked", action_id,
                                      "Repeated confirmation of an already executed action; no money moved again."))
            receipt = self._append(s, rec, None, res, f"REPLAY blocked: {rec.title}")
            view = self._view(rec).model_copy(update={"status": "replayed", "receipt_id": receipt.receipt_id})
            return self._turn(s, f"That was already done ({self._plain(rec)}), so I didn't move the money again. "
                                 "The repeat is recorded.", [view], events, [receipt])
        if rec.status != "awaiting_confirmation":
            self._audit("confirm", s, action_id=action_id, outcome=f"not_pending:{rec.status}")
            state = {"denied": "was blocked", "rejected_by_user": "was cancelled", "failed": "failed"}.get(
                rec.status, f"is {rec.status.replace('_', ' ')}")
            return self._turn(s, f"That step {state}, so it can't be approved any more. Nothing was sent.",
                              [self._view(rec)])

        # Plan ordering: a step that only fails because an earlier step of the same plan is still pending
        # stays pending (it is not denied) and the user is told which step to confirm first.
        sim0 = self._simulate(s, [rec.action])[0]
        pre = self._evaluate(s, rec, sim0, False, events, flag=False)
        if pre.verdict == Verdict.DENY:
            first = self._ordering_blocker(s, rec, events)
            if first is not None:
                self._audit("confirm", s, action_id=action_id, outcome="waiting_for_earlier_step",
                            earlier_action_id=first.action.action_id)
                return self._turn(s, f"Please approve \"{first.title}\" first: this step depends on it. "
                                     f"Nothing was sent, and \"{rec.title}\" is still waiting for you.",
                                  [self._view(rec), self._view(first)], events)

        prior = rec.decision
        was_step_up = prior is not None and prior.verdict == Verdict.STEP_UP
        verified = False
        if was_step_up:
            if self.step_up.locked(s.user_id):
                return self._otp_locked(s, rec, events)
            if not otp:
                self._audit("confirm", s, action_id=action_id, outcome="otp_missing")
                left = self.step_up.remaining(s.user_id)
                return self._turn(s, "This one needs your one-time code. Enter the 6-digit code we sent you "
                                     f"({left} {'try' if left == 1 else 'tries'} left). Nothing has moved.",
                                  [self._view(rec)])
            if not hmac.compare_digest(str(otp).encode("utf-8"), params.DEMO_OTP.encode("utf-8")):
                return self._otp_failed(s, rec, events)
            self.step_up.record_success(s.user_id)
            verified = True

        sim = self._simulate(s, [rec.action])[0]
        rec.sim = sim
        decision = self._evaluate(s, rec, sim, verified, events)
        rec.decision = decision
        if decision.verdict == Verdict.DENY:
            self._audit("confirm", s, action_id=action_id, outcome="denied_on_recheck")
            r = self._deny(s, rec)
            return self._turn(s, self._deny_text(rec), [self._view(rec)], events, [r])
        if decision.verdict == Verdict.STEP_UP and not verified:
            rec.creq = self._creq(rec)
            self._audit("await_confirmation", s, action_id=action_id, verdict="step_up",
                        params_hash=rec.creq.params_hash)
            return self._turn(s, "Something changed since this was proposed, so it now needs your one-time code. "
                                 "Enter it to continue. Nothing has moved.", [self._view(rec)], events)
        method = "step_up_otp" if verified else "tap"
        self._audit("confirm", s, action_id=action_id, outcome="approved", method=method)
        conf = Confirmation(action_id=action_id, params_hash=params_hash_, method=method, confirmed_at=self.now)  # type: ignore[arg-type]
        receipt = self._execute(s, rec, conf, events)
        if rec.status == "executed":
            text = f"Done: {self._done(rec)}. Receipt {receipt.receipt_id} is in your signed history."
            nxt = self._next_in_plan(s, rec)
            if nxt is not None:
                text += f" Next: approve \"{nxt.title}\"."
        elif rec.status == "replayed":
            text = f"That was already done ({self._plain(rec)}), so no money moved again."
        else:
            text = f"That didn't go through: {self._plain(rec)}. {self._last_error(rec)} Nothing was sent."
        return self._turn(s, text, [self._view(rec)], events, [receipt])

    def reject(self, session_id: str, action_id: str) -> AgentTurn:
        s = self._sess(session_id)
        rec = s.records.get(action_id)
        if rec is None:
            return self._turn(s, "I can't find that step in this conversation.")
        if rec.status != "awaiting_confirmation":
            return self._turn(s, "That step isn't waiting for approval, so there is nothing to cancel.",
                              [self._view(rec)])
        self._reject_rec(s, rec, "rejected")
        return self._turn(s, f"Cancelled: {rec.title}. Nothing was sent.", [self._view(rec)])

    # ------------------------------------------------------------------ OTP lockout
    def _otp_failed(self, s: _Session, rec: _Rec, events: list[SecurityEvent]) -> AgentTurn:
        left = self.step_up.record_failure(s.user_id)
        rec.otp_failures += 1
        used = self.step_up.failures(s.user_id)
        aid = rec.action.action_id
        events.append(self._event(s, "step_up_failed", aid,
                                  f"Wrong one-time code ({used} of {params.MAX_OTP_ATTEMPTS} attempts for this "
                                  "customer)."))
        self._audit("confirm", s, action_id=aid, outcome="otp_wrong", failures=used, attempts_left=left)
        if left > 0:
            return self._turn(s, f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left; after that, "
                                 "approving with a code is locked on your account. Nothing has moved.",
                              [self._view(rec)], events)
        r = self._lock_rec(s, rec)
        return self._turn(s, f"Blocked: that was the {_ordinal(params.MAX_OTP_ATTEMPTS)} wrong code in a row, so I "
                             f"didn't {self._plain(rec)} and I locked approval by code on your account to protect it. "
                             f"The attempt is recorded (receipt {r.receipt_id}). The bank has to unlock it before you "
                             "can approve with a code again.", [self._view(rec)], events, [r])

    def _lock_rec(self, s: _Session, rec: _Rec) -> Receipt:
        prior = rec.decision
        assert prior is not None
        lock = RuleResult(rule_id="STEP_UP_LOCKOUT", passed=False, severity=RiskLevel.R4,
                          detail=f"{params.MAX_OTP_ATTEMPTS} wrong one-time codes for this customer; "
                                 "step-up approval locked until reset")
        rec.decision = PolicyDecision(
            action_id=rec.action.action_id, risk_level=RiskLevel.R4, verdict=Verdict.DENY, rules=[*prior.rules, lock],
            policy_version=prior.policy_version,
            explanation=f"Step-up verification failed {params.MAX_OTP_ATTEMPTS} times for this customer; "
                        "approval by one-time code is locked.")
        return self._deny(s, rec)

    def _otp_locked(self, s: _Session, rec: _Rec, events: list[SecurityEvent]) -> AgentTurn:
        aid = rec.action.action_id
        events.append(self._event(s, "step_up_failed", aid,
                                  "Step-up approval is locked for this customer; code not checked."))
        self._audit("confirm", s, action_id=aid, outcome="otp_locked")
        r = self._lock_rec(s, rec)
        return self._turn(s, f"Approval by code is locked on your account after {params.MAX_OTP_ATTEMPTS} wrong "
                             f"codes, so I didn't {self._plain(rec)}. The attempt is recorded (receipt "
                             f"{r.receipt_id}). The bank has to unlock it first.",
                          [self._view(rec)], events, [r])

    # ------------------------------------------------------------------ plan ordering
    def _earlier_pending(self, s: _Session, rec: _Rec) -> list[_Rec]:
        xs = [r for r in s.records.values() if r is not rec and r.plan_id == rec.plan_id
              and r.plan_pos < rec.plan_pos and r.status == "awaiting_confirmation"]
        return sorted(xs, key=lambda r: r.plan_pos)

    def _ordering_blocker(self, s: _Session, rec: _Rec, events: list[SecurityEvent]) -> _Rec | None:
        earlier = self._earlier_pending(s, rec)
        if not earlier:
            return None
        sims = self.ledger.simulate(s.user_id, [*(r.action for r in earlier), rec.action])
        if not all(x.feasible for x in sims):
            return None
        d = self._evaluate(s, rec, sims[-1], False, events, flag=False)
        return earlier[0] if d.verdict != Verdict.DENY else None

    def _next_in_plan(self, s: _Session, rec: _Rec) -> _Rec | None:
        xs = [r for r in s.records.values() if r.plan_id == rec.plan_id and r.status == "awaiting_confirmation"]
        return min(xs, key=lambda r: r.plan_pos) if xs else None

    # ------------------------------------------------------------------ intent expansion
    def _expand(self, s: _Session, idx: int, intent: Any, out: list, t: _Turn, scans: dict[str, Any],
                draft: _Draft | None, text: str, names: list[str]) -> None:
        if isinstance(intent, ReadOverview):
            accs = self.ledger.get_accounts(s.user_id)
            bal = _and([f"{_m(a.balance)} {a.currency}" for a in accs]) or "no accounts"
            ben = _and([b.display_name for b in self.ledger.get_beneficiaries(s.user_id)]) or "none yet"
            t.info.append(f"You have {bal}. Saved payees: {ben}.")
            dues = sorted(self.ledger.get_obligations(s.user_id), key=lambda o: o.due_date)
            if dues:
                t.info.append("Coming up: " + "; ".join(
                    f"{o.kind} {_m(o.amount)} {o.currency}, due {o.due_date.strftime('%d %B').lstrip('0')}"
                    for o in dues) + ".")
            quote = _rate_answer(text or "")
            if quote:
                t.info.append(quote)
        elif isinstance(intent, Clarify) and id(intent) in t.plain_clarify:
            t.info.append(intent.question)
            self._audit("clarify", s, question=intent.question)
        elif isinstance(intent, Clarify):
            self._clarify_from_parser(s, intent, t, text, names)
        elif isinstance(intent, Unsupported):
            fixed = _REFUSAL_TEXT[intent.category]
            shown = intent.reason if t.trusted_text else fixed          # AUDIT2-2: never relay a model's free text
            if intent.category == "data_access":
                t.events.append(self._event(s, "data_access_denied", "user_message", shown))
            elif intent.category in ("jailbreak", "limit_change"):
                if not any(e.kind == "jailbreak_attempt" for e in t.events):
                    t.events.append(self._event(s, "jailbreak_attempt", "user_message", shown))
            more = _help.out_of_scope_suffix() if intent.category == "out_of_scope" else ""
            if not t.trusted_text:
                if intent.reason:       # kept for the audit log only
                    self._audit("clarify", s, unsupported_category=intent.category, model_reason=intent.reason[:200])
                t.info.append(fixed + more)
            elif intent.reason.startswith("BANK DETAILS: "):
                reason = intent.reason.removeprefix("BANK DETAILS: ")
                ev = self._event(s, "beneficiary_mismatch", "user_message",
                                 "Request to pay or redirect to an unregistered IBAN in chat; refused.")
                t.events.append(ev)
                t.explained.add(ev.event_id)
                t.info.append(reason)
            else:
                t.info.append(intent.reason + more)
        elif isinstance(intent, PayObligations):
            self._expand_obligations(s, idx, intent, out, t, scans)
        elif isinstance(intent, FxConvert):
            self._expand_fx(s, idx, 0, intent, intent.sell_currency, intent.buy_currency, intent.buy_amount, out, t)
        elif isinstance(intent, Pay):
            self._expand_pay(s, idx, intent, out, t)
        elif isinstance(intent, AddBeneficiary):
            self._expand_add(s, idx, intent, out, t)
        elif isinstance(intent, AffordabilityCheck):
            self._afford(s, intent, t)
        elif isinstance(intent, Correct):
            self._correct(s, intent, t)
        elif isinstance(intent, Cancel):
            self._cancel(s, intent, t, draft)
        elif isinstance(intent, ProvideDetail):
            # a detail with no open question: do not guess what it was for
            what = " ".join(x for x in (
                f"{_m(intent.amount)} {intent.currency or 'EUR'}" if intent.amount is not None else "",
                f"to {intent.beneficiary_ref}" if intent.beneficiary_ref else "") if x)
            q = (f"I am not waiting for any detail right now. What would you like to do with \"{what}\"?"
                 if what else "What would you like to do?")
            t.info.append(q)
            self._audit("clarify", s, question=q)

    def _injection_note(self, s: _Session, d: UntrustedDocument, scan: Any, wanted: set[str]) -> str:
        """Say what the hidden text tried to make me do, concretely, and where the money really goes."""
        text = d.text or ""
        name = d.filename or d.document_id
        obs = self.ledger.get_obligations(s.user_id)
        ob = next((o for o in obs if o.document_id == d.document_id), None)
        if ob is None and len(wanted) == 1:
            ob = next((o for o in obs if o.kind in wanted), None)
        label = f"your {ob.kind} invoice ({name})" if ob is not None else name
        bens = self.ledger.get_beneficiaries(s.user_id)
        registered = {_norm_iban(b.iban) for b in bens}
        m = _INJ_AMOUNT.search(text)
        amount = ""
        if m:
            val = parse_amount(m.group(2) or m.group(3))
            cur = (m.group(1) or m.group(4) or "").upper().replace("€", "EUR").replace("$", "USD")
            cur = {"EURO": "EUR", "EUROS": "EUR", "DOLLAR": "USD", "DOLLARS": "USD"}.get(cur, cur)
            if val is not None:
                amount = f"{_m(val)} {cur}".strip()
        foreign = [n for n in (_norm_iban(x.group(0)) for x in _ANY_IBAN_TEXT.finditer(text)) if n not in registered]
        where = ""
        if foreign:
            where = _COUNTRY.get(foreign[0][:2], "another")
            where = f" to {where} account"
        patterns = set(getattr(scan, "matched_patterns", []) or [])
        sneaky = " without asking you" if "skip_confirmation" in patterns or "skip_confirmation_fr" in patterns else ""
        if amount or where:
            core = (f"Watch out: {label} contains hidden text telling me to send "
                    f"{amount or 'money'}{where}{sneaky}. I ignored it.")
        else:
            core = (f"Watch out: {label} contains hidden instructions aimed at me. I ignored them and only read "
                    "the amount and due date from it.")
        b = next((x for x in bens if ob is not None and x.beneficiary_id == ob.beneficiary_id), None)
        if b is not None and ob is not None:
            core += (f" {ob.kind.capitalize()} goes to {b.display_name}'s account on file, ending {b.iban[-4:]}.")
        return core

    def _name_taken(self, s: _Session, name: str) -> Beneficiary | None:
        """An existing payee whose name, alias or id looks the same as `name` (case, spacing and look-alike
        letters folded). A second payee under such a name would capture or shadow the first one (AUDIT-5)."""
        sk = name_skeleton(name)
        if not sk:
            return None
        for b in self.ledger.get_beneficiaries(s.user_id):
            if sk in {name_skeleton(x) for x in (b.beneficiary_id, b.display_name, *b.aliases)}:
                return b
        return None

    def _expand_add(self, s: _Session, idx: int, intent: AddBeneficiary, out: list, t: _Turn) -> None:
        name = " ".join("".join(ch for ch in intent.display_name if unicodedata.category(ch) != "Cc").split())
        if not name:
            t.info.append("What name should I save this payee under?")
            return
        if len(name) > 70:
            t.info.append("That payee name is too long (70 characters at most). What shorter name should I use?")
            return
        taken = self._name_taken(s, name)
        if taken is not None:
            same = name_skeleton(name) in {name_skeleton(taken.display_name), name_skeleton(taken.beneficiary_id)}
            how = "looks the same as" if same and name != taken.display_name else "is already used for"
            nick = "" if same else " (it is one of the names you use for them)"
            q = (f"\"{name}\" {how} your saved payee {taken.display_name}{nick}. Two payees with the same name could "
                 f"send money to the wrong account, so I didn't add it. Please choose a different name for this one.")
            t.info.append(q)
            self._audit("clarify", s, question=q)
            return
        iban = _norm_iban(intent.iban)      # canonical form; the policy denies it if it is not a valid IBAN
        key = self._idem(s, idx, intent, 0)
        a = AddBeneficiaryAction(action_id=self._aid(key), idempotency_key=key,
                                 display_name=name, iban=iban, category=intent.category)
        self._audit_action(s, a, "add_beneficiary")
        out.append((intent, a, f"Add payee {a.display_name} ({a.category}), IBAN ending {iban[-4:]}"))

    def _account(self, s: _Session, currency: str):
        return next((a for a in self.ledger.get_accounts(s.user_id) if a.currency == currency), None)

    def _fx_quote(self, sell: str, buy: str, amount: Decimal, events: list[SecurityEvent],
                  s: _Session) -> tuple[FxQuote | None, str | None]:
        raw = self.ledger.fx_quote_raw(sell, buy, amount)
        quote, evs = self.guard.sanitize_tool_output("fx_quote", raw, FxQuote)
        events.extend(e for e in evs if e not in events)
        if quote is None or not isinstance(quote, FxQuote):
            return None, "The FX provider returned an invalid quote, so I did not propose a conversion."
        if quote.sell_currency != sell or quote.buy_currency != buy or quote.buy_amount != amount:
            events.append(self._event(s, "tool_output_poisoning", "fx_quote",
                                      "Quote parameters differ from the request; quote discarded."))
            return None, "The exchange quote did not match the conversion you asked for, so I discarded it."
        # AUDIT-1: the provider is untrusted; its price must equal the bank's own reference price to the cent
        rate = params.FX_RATES.get((sell, buy))
        if rate is None:
            return None, "I can't convert between those currencies."
        net = _q(amount) / rate
        ref_sell, ref_fee = _q(net * (1 + params.FX_FEE_RATE)), _q(net * params.FX_FEE_RATE)
        if (quote.rate != rate or _q(quote.sell_amount) != ref_sell or _q(quote.fee) != ref_fee
                or not self.now <= quote.expires_at <= self.now + params.FX_QUOTE_TTL):
            events.append(self._event(
                s, "tool_output_poisoning", "fx_quote",
                f"Quote of {_m(quote.sell_amount)} {sell} (rate {quote.rate}, fee {_m(quote.fee)}) for "
                f"{_m(amount)} {buy} differs from the reference price {_m(ref_sell)} {sell} (rate {rate}, fee "
                f"{_m(ref_fee)}); quote discarded."))
            return None, (f"The exchange provider quoted {_m(quote.sell_amount)} {sell} for {_m(amount)} {buy}, but "
                          f"the bank's price is {_m(ref_sell)} {sell}. I discarded that quote and did not plan a "
                          "conversion. Nothing was sent.")
        return quote, None

    def _expand_fx(self, s, idx, step, intent, sell, buy, amount, out, t: _Turn, key: str | None = None) -> FxAction | None:
        sell_acc, buy_acc = self._account(s, sell), self._account(s, buy)
        if sell == buy or sell_acc is None or buy_acc is None or amount <= 0:
            t.info.append("I can't convert that: I need two of your own accounts in different currencies and an "
                          "amount above zero.")
            return None
        if not is_whole_cents(amount):
            t.info.append(_sub_cent_question(amount, buy, "convert"))
            return None
        amount = _q(amount)
        quote, err = self._fx_quote(sell, buy, amount, t.events, s)
        if quote is None:
            t.info.append(err or "No valid FX quote.")
            return None
        key = key or self._idem(s, idx, intent, step)
        a = FxAction(action_id=self._aid(key), idempotency_key=key,
                     from_account_id=sell_acc.account_id, to_account_id=buy_acc.account_id, quote=quote)
        title = self._fx_title(a)
        self._audit_action(s, a, title)
        out.append((intent, a, title))
        return a

    @staticmethod
    def _fx_title(a: FxAction) -> str:
        qt = a.quote
        return (f"Convert {qt.sell_currency} to {qt.buy_currency}: buy {_m(qt.buy_amount)} {qt.buy_currency} for "
                f"{_m(qt.sell_amount)} {qt.sell_currency} (fee {_m(qt.fee)} {qt.sell_currency})")

    def _expand_obligations(self, s, idx, intent, out, t: _Turn, scans) -> None:
        bens = {b.beneficiary_id: b for b in self.ledger.get_beneficiaries(s.user_id)}
        obs_all = self.ledger.get_obligations(s.user_id)
        obs = []
        for kind in intent.obligation_kinds:
            obs.extend(o for o in obs_all if o.kind == kind)
        if not obs:
            t.info.append("I found no open obligations of that kind on your account.")
            return
        reserve = Decimal("0")
        if intent.keep_reserve is not None and not is_whole_cents(intent.keep_reserve):
            t.info.append(_sub_cent_question(intent.keep_reserve, intent.reserve_currency, "keep")
                          .replace("Did you mean", "Which reserve do you want:"))
            return
        if intent.keep_reserve is not None:
            reserve = intent.keep_reserve
            if intent.reserve_currency != "EUR":
                reserve = self.ledger.eur_equivalent(reserve, intent.reserve_currency)
            s.reserve = _q(reserve)
        eur = self._account(s, "EUR")
        if eur is None:
            t.info.append("You have no EUR account to pay these from.")
            return
        total = sum((o.amount for o in obs if o.currency == "EUR"), Decimal("0"))
        # Other exact EUR payments asked in the same message ("pay the rent and send mom 600") are funded by the
        # same conversion, so they are not rejected for lack of euros right after the bill is planned.
        total += getattr(t, "extra_eur", Decimal("0"))
        shortfall = _q(total + reserve - eur.balance)
        step = 0
        if shortfall > 0:
            fx = self._expand_fx(s, idx, step, intent, "USD", "EUR", shortfall, out, t)
            if fx is None:
                return
            step += 1
        for o in obs:
            b = bens.get(o.beneficiary_id)
            if b is None or b.user_id != s.user_id:
                t.info.append(f"The payee for your {o.kind} is not in your registry, so I skipped it.")
                continue
            acc = self._account(s, o.currency)
            if acc is None:
                continue
            scan = scans.get(o.document_id or "")
            doc_iban = (scan.extracted.get("iban") if scan is not None else None)
            if doc_iban and _norm_iban(doc_iban) != _norm_iban(b.iban):
                ev = self._event(s, "beneficiary_mismatch", o.document_id or o.obligation_id,
                                 f"IBAN in the {o.kind} document differs from the registered IBAN; "
                                 "the registered IBAN is used.")
                t.events.append(ev)
                t.explained.add(ev.event_id)        # the document note above already says it in plain words
            key = self._idem(s, idx, intent, step)
            a = PaymentAction(action_id=self._aid(key), idempotency_key=key,
                              from_account_id=acc.account_id, beneficiary_id=b.beneficiary_id, iban=b.iban,
                              amount=_q(o.amount), currency=o.currency, obligation_id=o.obligation_id,
                              memo=f"{o.kind} {o.due_date.isoformat()}")
            title = self._pay_title(a, b)
            self._audit_action(s, a, title)
            out.append((intent, a, title))
            step += 1

    def _expand_pay(self, s, idx, intent: Pay, out, t: _Turn) -> None:
        ref = (intent.beneficiary_ref or "").strip()
        exact = intent.amount_mode == "exact" and intent.amount is not None and intent.amount > 0
        b = self.ledger.resolve_beneficiary(s.user_id, ref) if ref else None
        draft = dict(currency=intent.currency, iban_hint=intent.iban_hint, memo=intent.memo)
        if not exact:
            if ref and b is None:
                self._ask(s, t, self._payee_question(s, ref, None, intent.currency), ref=None, amount=None, **draft)
                return
            if not ref:
                self._ask(s, t, self._payee_question(s, None, None, intent.currency), ref=None, amount=None, **draft)
                return
            who = self._who(b, ref)
            if intent.amount_mode == "all":
                q = (f"How much exactly should I send to {who}? I don't send \"everything\" or \"the maximum\"; "
                     "give me a figure and I'll check it against your balance and limits.")
            elif intent.amount is not None and intent.amount <= 0:
                q = f"How much should I send to {who}? The amount has to be more than zero."
            else:
                q = f"How much should I send to {who}?"
            self._ask(s, t, q, ref=ref, amount=None, **draft)
            return
        if not is_whole_cents(intent.amount):  # type: ignore[arg-type]
            self._ask(s, t, _sub_cent_question(intent.amount, intent.currency), ref=ref or None, amount=None, **draft)  # type: ignore[arg-type]
            return
        amount = _q(intent.amount)  # type: ignore[arg-type]
        if b is None:
            self._ask(s, t, self._payee_question(s, ref or None, amount, intent.currency), ref=None, amount=amount,
                      **draft)
            return
        acc = self._account(s, intent.currency)
        if acc is None:
            t.info.append(f"You have no {intent.currency} account to pay from.")
            return
        if intent.iban_hint and _norm_iban(intent.iban_hint) != _norm_iban(b.iban):
            typed = _norm_iban(intent.iban_hint)[-4:]
            ev = self._event(s, "beneficiary_mismatch", "user_message",
                             f"The IBAN you typed (ending {typed}) differs from the registered IBAN for "
                             f"{b.display_name} (ending {b.iban[-4:]}); the registered IBAN is used.")
            t.events.append(ev)
            t.explained.add(ev.event_id)
            t.notes.append(f"You typed an account ending {typed}, but the account saved for {b.display_name} ends "
                           f"{b.iban[-4:]}. I'll only pay the saved account. If {b.display_name} gave you new bank "
                           "details, check with them on a number you already know, then add the account as a payee.")
        key = self._idem(s, idx, intent, 0)
        a = PaymentAction(action_id=self._aid(key), idempotency_key=key,
                          from_account_id=acc.account_id, beneficiary_id=b.beneficiary_id, iban=b.iban,
                          amount=amount, currency=intent.currency, memo=intent.memo[:140])
        title = self._pay_title(a, b)
        self._audit_action(s, a, title)
        out.append((intent, a, title))

    @staticmethod
    def _pay_title(a: PaymentAction, b: Beneficiary) -> str:
        return f"Pay {_m(a.amount)} {a.currency} to {b.display_name} ({b.category})"

    # ------------------------------------------------------------------ clarification / drafts
    @staticmethod
    def _who(b: Beneficiary | None, ref: str) -> str:
        if b is None:
            return ref
        if ref and ref.lower() != b.display_name.lower() and ref.lower() != b.beneficiary_id.lower():
            return f"{b.display_name} ({ref})"
        return b.display_name

    def _payee_question(self, s: _Session, ref: str | None, amount: Decimal | None, currency: str) -> str:
        names = [b.display_name for b in self.ledger.get_beneficiaries(s.user_id) if not b.blocked]
        choices = (", ".join(names[:-1]) + " or " + names[-1]) if len(names) > 1 else (names[0] if names else "")
        what = f"the {_m(amount)} {currency}" if amount is not None else "the money"
        if ref:
            n = _norm_iban(ref)
            if re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", n):
                lead = (f"I only pay saved payees, and the account ending {n[-4:]} isn't one of them. To pay it, "
                        "add it as a payee first (that needs your one-time code).")
            else:
                name = _display_ref(ref)
                lead = (f"{name} isn't one of your saved payees. To pay them, add them as a payee first (that needs "
                        "your one-time code).")
            return f"{lead} Or did you mean {choices}?" if choices else lead
        tail = f": {choices}?" if choices else "? Please add the payee first."
        return f"Who should receive {what}{tail}"

    def _ask(self, s: _Session, t: _Turn, question: str, *, ref: str | None, amount: Decimal | None,
             currency: str = "EUR", iban_hint: str | None = None, memo: str = "") -> None:
        s.draft = _Draft(question=question, ref=ref, amount=amount, currency=currency or "EUR",
                         iban_hint=iban_hint, memo=memo)
        t.question = question
        self._audit("clarify", s, question=question, draft={"payee": ref, "amount": amount, "currency": currency})

    def _clarify_from_parser(self, s: _Session, intent: Clarify, t: _Turn, text: str, names: list[str]) -> None:
        if not t.trusted_text and not model_question_ok(intent.question):
            self._audit("clarify", s, model_question_rejected=True, model_question=intent.question[:200])
            intent = Clarify(question=_FIXED_CLARIFY)      # AUDIT2-2: a model's question must pass the filter
        hint = payment_draft_hint(text, names) if s.draft is None else None
        if hint is None:
            t.info.append(intent.question)
            self._audit("clarify", s, question=intent.question)
            return
        ref, amount, cur = hint
        b = self.ledger.resolve_beneficiary(s.user_id, ref) if ref else None
        if ref and b is not None and amount is None:
            q = (intent.question if "too large" in intent.question else
                 f"What exact amount should I send to {self._who(b, ref)}? I don't guess partial or relative amounts.")
            self._ask(s, t, q, ref=ref, amount=None, currency=cur or "EUR")
        elif b is None:
            self._ask(s, t, self._payee_question(s, ref, amount, cur or "EUR"), ref=None, amount=amount,
                      currency=cur or "EUR")
        else:   # payee and amount known yet the parser still asked: show its question, keep the draft
            self._ask(s, t, intent.question, ref=ref, amount=amount, currency=cur or "EUR")

    def _apply_draft(self, s: _Session, draft: _Draft | None, intents: list, t: _Turn) -> list:
        """Complete a pending draft from this message, or let it expire (one unrelated message)."""
        if draft is None:
            return intents
        if len(intents) == 1:
            i = intents[0]
            detail: tuple[Decimal | None, str | None, str | None, str | None] | None = None
            if isinstance(i, ProvideDetail):
                detail = (i.amount, i.currency, i.beneficiary_ref, None)
            elif isinstance(i, Correct) and not self._pending(s):
                detail = (i.amount, i.currency, i.beneficiary_ref, None)
            elif isinstance(i, Pay) and (i.amount_mode != "exact" or i.amount is None) and draft.amount is not None:
                detail = (None, None, i.beneficiary_ref, i.iban_hint)
            elif isinstance(i, Cancel):
                return intents      # _cancel drops the draft and says so
            if detail is not None:
                amount, cur, ref, hint = detail
                amount = amount if amount is not None and amount > 0 else draft.amount
                ref = ref or draft.ref
                pay = Pay(beneficiary_ref=ref or "", amount_mode="exact" if amount is not None else "unspecified",
                          amount=amount, currency=cur or draft.currency, iban_hint=hint or draft.iban_hint,  # type: ignore[arg-type]
                          memo=draft.memo)
                self._audit("parse", s, completed_draft=True, intents=[pay.model_dump(mode="python")])
                return [pay]
        t.notes.append(f"I set aside {draft.describe()}; ask again whenever you are ready.")
        self._audit("clarify", s, draft_expired=True)
        return intents

    def _questions_never_pay(self, parsed: ParsedRequest) -> ParsedRequest:
        """Deterministic net under any parser: a question about money is answered read-only, never acted on.
        Affordability questions become a simulation; rate questions the rate answer; any other question that
        ends in '?' and opens with an interrogative becomes a clarification."""
        acting = (Pay, PayObligations, FxConvert, AddBeneficiary, Correct, Cancel)   # AUDIT2-7: also Correct, Cancel
        if not any(isinstance(i, acting) for i in parsed.intents):
            return parsed
        afford = is_affordability_question(parsed.raw_text) and any(
            isinstance(i, (Pay, PayObligations, FxConvert)) for i in parsed.intents)
        if not afford:
            if not (is_affordability_question(parsed.raw_text) or is_question(parsed.raw_text)):
                return parsed
            rest = [i for i in parsed.intents if not isinstance(i, acting)]
            if _rate_answer(parsed.raw_text) is not None:
                head: Any = ReadOverview()
                note = "The message is a question, so it was answered with the rate only; nothing was converted."
            else:
                head = Clarify(question="That reads as a question, so I didn't do anything. If you want me to act, "
                                        "tell me what to do, for example \"send 50 euros to mom\".")
                note = "The message is a question, so no action was proposed."
            return parsed.model_copy(update={"intents": [head, *rest], "notes": [*parsed.notes, note]})
        kinds: list[str] = []
        ref, amount, cur, reserve = None, None, "EUR", None
        kept = []
        for i in parsed.intents:
            if isinstance(i, PayObligations):
                kinds += [k for k in i.obligation_kinds if k not in kinds]
                reserve = i.keep_reserve if i.keep_reserve is not None else reserve
            elif isinstance(i, Pay):
                if amount is None and i.amount_mode == "exact":
                    ref, amount, cur = i.beneficiary_ref, i.amount, i.currency
            elif isinstance(i, (FxConvert, AddBeneficiary)):
                continue
            else:
                kept.append(i)
        check = AffordabilityCheck(obligation_kinds=kinds, beneficiary_ref=ref, amount=amount,  # type: ignore[arg-type]
                                   currency=cur, keep_reserve=reserve)  # type: ignore[arg-type]
        return parsed.model_copy(update={"intents": [check, *kept], "notes": [
            *parsed.notes, "The message is a question, so it was answered with a simulation only."]})

    # ------------------------------------------------------------------ grounding (every parser)
    def _ground(self, s: _Session, parsed: ParsedRequest, ctx: GroundingContext) -> tuple[ParsedRequest, list[Repair]]:
        intents, repairs = ground_intents(list(parsed.intents), ctx)
        if not repairs:
            return parsed, []
        return parsed.model_copy(update={"intents": intents,
                                         "notes": [*parsed.notes, *(f"grounding: {r.field}: {r.reason}" for r in repairs)]}), repairs

    def _sanctions_on_sources(self, s: _Session, parsed: ParsedRequest, text: str, docs: list[UntrustedDocument],
                              t: _Turn) -> ParsedRequest:
        """Sanctions screen on what the user wrote and on the documents they supplied, whatever a parser
        extracted from them. A hit means no payee is added and nothing is paid this turn."""
        sources = [("your message", "user_message", text)] + [
            (f"the document {d.filename or d.document_id}", d.document_id, d.text or "") for d in docs]
        found = [(label, src, e) for label, src, body in sources for e in sanctions_hits(body)]
        if not found:
            return parsed
        blocked = (Pay, PayObligations, AddBeneficiary, Correct, ProvideDetail)
        # A new payee whose own name or IBAN is on the list is left to the policy (SANCTIONS_SCREEN denies it and
        # writes the receipt, the established path); everything else of the blocked kinds is dropped here.
        to_policy = [i for i in parsed.intents if isinstance(i, AddBeneficiary) and own_sanctions_hit(i.display_name, i.iban)]
        kept = [i for i in parsed.intents if not isinstance(i, blocked) or any(i is p for p in to_policy)]
        dropped = [i.kind for i in parsed.intents if isinstance(i, blocked) and not any(i is p for p in to_policy)]
        if to_policy and not dropped:
            return parsed
        for label, src, entry in dict.fromkeys(found):
            ev = self._event(s, "sanctions_hit", src,
                             f"{label} mentions '{entry}', which is on the sanctions watchlist; "
                             "no payee was added and nothing was paid in this turn.")
            t.events.append(ev)
            t.explained.add(ev.event_id)
            self._audit("grounding", s, parser=parsed.parser, intent=",".join(dropped) or "none", field="source_text",
                        reason="sanctions watchlist entry found in " + label, before=entry, after=None)
        label, _src, entry = found[0]
        t.info.append(f"I can't add a payee or send any payment this time: {label} mentions \"{entry}\", which is "
                      "on the sanctions list, so the bank can't deal with it. Nothing was added or sent.")
        return parsed.model_copy(update={"intents": kept, "notes": [
            *parsed.notes, "grounding: sanctions watchlist entry found in the source text"]})

    # ------------------------------------------------------------------ correct / cancel
    def _pending(self, s: _Session) -> list[_Rec]:
        return [r for r in s.records.values() if r.status == "awaiting_confirmation"]

    def _reject_rec(self, s: _Session, rec: _Rec, reason: str) -> None:
        rec.status = "rejected_by_user"
        self._audit("confirm", s, action_id=rec.action.action_id, outcome="rejected_by_user", reason=reason)

    def _cancel(self, s: _Session, intent: Cancel, t: _Turn, draft: _Draft | None) -> None:
        if draft is not None:
            t.info.append(f"OK, I dropped {draft.describe()}. Nothing was sent.")
            self._audit("clarify", s, draft_cancelled=True)
            if intent.scope == "last":
                return
        pend = self._pending(s)
        if not pend:
            if draft is None:
                t.info.append("There is nothing pending to cancel, and nothing has been executed.")
            return
        targets = pend[-1:] if intent.scope == "last" else pend
        for r in targets:
            self._reject_rec(s, r, f"cancel_{intent.scope}")
            t.views.append(self._view(r))
        t.info.append("Cancelled: " + "; ".join(r.title for r in targets) + ". Nothing was executed.")
        left = len(pend) - len(targets)
        if left:
            t.info.append(f"{left} other step{'s are' if left != 1 else ' is'} still waiting for your confirmation; "
                          "say \"cancel everything\" to drop them too.")

    def _correct(self, s: _Session, intent: Correct, t: _Turn) -> None:
        pend = self._pending(s)
        target = next((r for r in reversed(pend) if isinstance(r.action, PaymentAction)), None)
        if target is None and intent.beneficiary_ref is None and intent.amount is not None:
            target = next((r for r in reversed(pend) if isinstance(r.action, FxAction)), None)
        if target is None:
            if pend:
                t.info.append("I can only change the amount or the payee of a pending payment or conversion.")
            else:
                q = "There is no pending payment to change, and nothing has been executed. What would you like to send, and to whom?"
                t.info.append(q)
                self._audit("clarify", s, question=q)
            return
        old = target.action
        out: list = []
        if isinstance(old, PaymentAction):
            bens = {b.beneficiary_id: b for b in self.ledger.get_beneficiaries(s.user_id)}
            if intent.beneficiary_ref:
                b = self.ledger.resolve_beneficiary(s.user_id, intent.beneficiary_ref)
                if b is None:
                    t.info.append(f"\"{intent.beneficiary_ref}\" is not one of your saved payees, so I kept "
                                  f"\"{target.title}\" unchanged. It is still waiting for your confirmation.")
                    return
            else:
                b = bens.get(old.beneficiary_id)
                if b is None:
                    t.info.append("I could not find the payee of that payment any more; nothing was changed.")
                    return
            if intent.amount is not None and not is_whole_cents(intent.amount):
                t.info.append(_sub_cent_question(intent.amount, intent.currency or old.currency) +
                              f" \"{target.title}\" is unchanged and still waiting for you.")
                return
            amount = _q(intent.amount) if intent.amount is not None else old.amount
            currency = intent.currency or old.currency
            acc = self._account(s, currency)
            if acc is None or amount <= 0:
                t.info.append(f"I cannot pay {_m(amount)} {currency} from your accounts; nothing was changed.")
                return
            if b.beneficiary_id == old.beneficiary_id and amount == old.amount and currency == old.currency:
                t.info.append(f"\"{target.title}\" already has those details; nothing changed.")
                return
            key = "idem_" + sha256_hex(["correct", old.idempotency_key, b.beneficiary_id, amount, currency])[:24]
            same = b.beneficiary_id == old.beneficiary_id
            new: ProposedAction = PaymentAction(
                action_id=self._aid(key), idempotency_key=key, from_account_id=acc.account_id,
                beneficiary_id=b.beneficiary_id, iban=b.iban, amount=amount, currency=currency,  # type: ignore[arg-type]
                obligation_id=old.obligation_id if same else None, memo=old.memo if same else "")
            title = self._pay_title(new, b)  # type: ignore[arg-type]
            self._audit_action(s, new, title)
        elif isinstance(old, FxAction):
            if not is_whole_cents(intent.amount):  # type: ignore[arg-type]
                t.info.append(_sub_cent_question(intent.amount, old.quote.buy_currency, "convert"))  # type: ignore[arg-type]
                return
            amount = _q(intent.amount)  # type: ignore[arg-type]
            if amount == old.quote.buy_amount:
                t.info.append(f"\"{target.title}\" already has that amount; nothing changed.")
                return
            key = "idem_" + sha256_hex(["correct", old.idempotency_key, amount])[:24]
            fx = self._expand_fx(s, 0, 0, intent, old.quote.sell_currency, old.quote.buy_currency, amount, out, t,
                                 key=key)
            if fx is None:
                return
            new, title = fx, out[-1][2]
        else:
            t.info.append("I can only change the amount or the payee of a pending payment or conversion.")
            return
        self._reject_rec(s, target, f"corrected_to:{new.action_id}")
        rec = _Rec(action=new, intent={"correct": intent, "replaces": old.action_id}, title=title,
                   reserve=target.reserve, plan_id=target.plan_id, plan_pos=target.plan_pos)
        earlier = self._earlier_pending(s, rec)
        sims = self._simulate(s, [*(r.action for r in earlier), new])
        rec.sim = sims[-1]
        s.records[new.action_id] = rec
        r = self._decide(s, rec, t.events)
        if r is not None:
            t.receipts.append(r)
        t.views += [self._view(target), self._view(rec)]
        t.info.append(f"Changed: I cancelled \"{target.title}\" and prepared \"{title}\" instead.")

    # ------------------------------------------------------------------ affordability (simulation only)
    def _afford(self, s: _Session, intent: AffordabilityCheck, t: _Turn) -> None:
        accs = self.ledger.get_accounts(s.user_id)
        cur_of = {a.account_id: a.currency for a in accs}
        eur = self._account(s, "EUR")
        if eur is None:
            t.info.append("You have no EUR account, so I cannot project that.")
            return
        reserve = _q(intent.keep_reserve if intent.keep_reserve is not None else params.DEMO_RESERVE_EUR)
        bens = {b.beneficiary_id: b for b in self.ledger.get_beneficiaries(s.user_id)}
        pays: list[tuple[PaymentAction, str]] = []
        kinds_found: list[str] = []
        for kind in intent.obligation_kinds:
            for o in self.ledger.get_obligations(s.user_id):
                b, acc = bens.get(o.beneficiary_id), self._account(s, o.currency)
                if o.kind != kind or b is None or acc is None:
                    continue
                pays.append((PaymentAction(action_id=f"proj_{o.obligation_id}", idempotency_key=f"proj_{o.obligation_id}",
                                           from_account_id=acc.account_id, beneficiary_id=b.beneficiary_id, iban=b.iban,
                                           amount=_q(o.amount), currency=o.currency, obligation_id=o.obligation_id),
                             f"{o.kind.capitalize()} to {b.display_name}"))
                kinds_found.append(o.kind)
        n_bills = len(pays)
        unknown = ""
        extra_label = ""
        if intent.amount is not None and not is_whole_cents(intent.amount):
            q = _sub_cent_question(intent.amount, intent.currency)
            t.info.append(q)
            self._audit("clarify", s, question=q)
            return
        if intent.keep_reserve is not None and not is_whole_cents(intent.keep_reserve):
            q = _sub_cent_question(intent.keep_reserve, "EUR", "keep")
            t.info.append(q)
            self._audit("clarify", s, question=q)
            return
        if intent.amount is not None and intent.amount > 0:
            acc = self._account(s, intent.currency)
            if acc is None:
                t.info.append(f"You have no {intent.currency} account, so I cannot project that payment.")
                return
            ref = (intent.beneficiary_ref or "").strip()
            b = self.ledger.resolve_beneficiary(s.user_id, ref) if ref else None
            if ref and b is None:
                unknown = f" Note: \"{ref}\" is not one of your saved payees; you would need to add them first."
            extra_label = f"{_m(intent.amount)} {intent.currency}" + (f" to {self._who(b, ref)}" if b else
                                                                      (f" to {ref}" if ref else ""))
            pays.append((PaymentAction(action_id="proj_extra", idempotency_key="proj_extra",
                                       from_account_id=acc.account_id,
                                       beneficiary_id=b.beneficiary_id if b else "unregistered",
                                       iban=b.iban if b else "", amount=_q(intent.amount), currency=intent.currency),
                         "Payment of " + extra_label))
        if not pays:
            q = ("Which payment should I check? For example: \"Can I afford to send 300 EUR to mom after my bills?\""
                 if not intent.obligation_kinds else "I found no open obligations of that kind on your account.")
            t.info.append(q)
            self._audit("clarify", s, question=q)
            return
        has_extra = len(pays) > n_bills
        bills_eur = sum((a.amount for a, _ in pays[:n_bills] if a.currency == "EUR"), Decimal("0"))
        all_eur = sum((a.amount for a, _ in pays if a.currency == "EUR"), Decimal("0"))
        needs = [all_eur + reserve - eur.balance]
        if n_bills and has_extra:
            needs.append(bills_eur + reserve - eur.balance)    # FX sized like the real bill plan
        needs.append(all_eur - eur.balance)                     # minimum FX, reserve not kept
        cands: list[Decimal] = []
        for n in needs:
            n = _q(max(n, Decimal("0")))
            if n not in cands:
                cands.append(n)
        best = None
        usd = self._account(s, "USD")
        for need in cands:
            fx = None
            evs: list[SecurityEvent] = []
            if need > 0:
                if usd is None:
                    continue
                quote, _err = self._fx_quote("USD", "EUR", need, evs, s)
                if quote is None:
                    t.events.extend(e for e in evs if e not in t.events)
                    continue
                fx = FxAction(action_id="proj_fx", idempotency_key="proj_fx", from_account_id=usd.account_id,
                              to_account_id=eur.account_id, quote=quote)
            actions: list[ProposedAction] = ([fx] if fx else []) + [a for a, _ in pays]
            sims = self.ledger.simulate(s.user_id, actions)
            cand = (fx, sims, all(x.feasible for x in sims), evs)
            if best is None or cand[2]:
                best = cand
            if cand[2]:
                break
        if best is None:
            t.info.append("I could not get a valid FX quote, so I cannot project this right now.")
            return
        fx, sims, feasible, evs = best
        t.events.extend(e for e in evs if e not in t.events)
        by_ccy = lambda bal: {cur_of.get(k, k): _q(v) for k, v in bal.items() if k in cur_of}  # noqa: E731
        now_b, after_b = by_ccy(sims[0].balances_before), by_ccy(sims[-1].balances_after)
        eur_after = after_b.get("EUR", Decimal("0"))
        usd_after = after_b.get("USD")
        reserve_ok = eur_after >= reserve
        fmt = lambda bal: ", ".join(f"{v:.2f} {c}" for c, v in sorted(bal.items()))  # noqa: E731
        lines = [f"Now: {fmt(now_b)}"]
        off = 0
        if fx is not None:
            qt, sim = fx.quote, sims[0]
            lines.append(f"Convert {_m(qt.sell_amount)} USD into {_m(qt.buy_amount)} EUR (fee {_m(qt.fee)} USD)"
                         + ("" if sim.feasible else f": not possible ({', '.join(sim.errors)})"))
            lines.append(f"Total FX cost: {fx_cost_text(qt)}")
            off = 1
        for i, (a, label) in enumerate(pays):
            sim = sims[off + i]
            left = sim.balances_after.get(a.from_account_id, Decimal("0"))
            lines.append(f"{label}: -{_m(a.amount)} {a.currency}, leaving {_m(left)} {a.currency}"
                         if sim.feasible else f"{label}: not possible ({', '.join(sim.errors)})")
        lines.append(f"After: {fmt(after_b)}")
        lines.append(f"Reserve {_m(reserve)} EUR: " + ("kept" if reserve_ok else f"short by {_m(reserve - eur_after)} EUR"))
        t.projection = Projection(balances_now=now_b, balances_after=after_b, reserve_floor_eur=reserve,
                                  reserve_ok=reserve_ok, feasible=feasible, lines=lines)
        self._audit("projection", s, feasible=feasible, reserve_ok=reserve_ok, reserve_floor_eur=reserve,
                    balances_now=now_b, balances_after=after_b, fx_buy_eur=fx.quote.buy_amount if fx else None)

        bills = " and ".join(dict.fromkeys(kinds_found)) or "your bills"
        fx_note = (f" (after converting {_m(fx.quote.sell_amount)} USD into {_m(fx.quote.buy_amount)} EUR; "
                   f"total FX cost {fx_cost_plain(fx.quote)})" if fx is not None else "")
        usd_note = f" You would still have {_m(usd_after)} USD." if usd_after is not None and fx is not None else ""
        if not feasible:
            what = (f"{bills} plus {extra_label}" if n_bills and has_extra else (bills if n_bills else extra_label))
            ans = (f"No: you do not have enough money for {what}, even after converting your dollars. "
                   f"You would be short by about {_m(max(all_eur - eur.balance - (fx.quote.buy_amount if fx else 0), Decimal('0')))} EUR.")
        elif n_bills and has_extra:
            after_bills = sims[off + n_bills - 1].balances_after.get(eur.account_id, Decimal("0"))
            ans = (f"After {bills} you would have {_m(after_bills)} EUR{fx_note}; sending {extra_label} on top would "
                   f"leave {_m(eur_after)} EUR, {'which keeps' if reserve_ok else 'below'} your {_m(reserve)} EUR reserve. "
                   + ("So yes, you can afford it." if reserve_ok else "You can't do that without dipping into your reserve.")
                   + usd_note)
        elif n_bills:
            ans = (f"After paying {bills} you would have {_m(eur_after)} EUR"
                   + (f" and {_m(usd_after)} USD" if usd_after is not None else "") + f" left{fx_note}. "
                   + (f"That keeps your {_m(reserve)} EUR reserve." if reserve_ok
                      else f"That is {_m(reserve - eur_after)} EUR below your {_m(reserve)} EUR reserve."))
        else:
            ans = (f"Sending {extra_label} would leave you {_m(eur_after)} EUR{fx_note}, "
                   + (f"which keeps your {_m(reserve)} EUR reserve. So yes, you can afford it." if reserve_ok
                      else f"{_m(reserve - eur_after)} EUR below your {_m(reserve)} EUR reserve.")
                   + usd_note)
        t.info.append(ans + unknown + " This is a simulation only: nothing was proposed or sent.")

    # ------------------------------------------------------------------ simulate / policy / execute / receipts
    def _simulate(self, s: _Session, actions: list[ProposedAction]) -> list[SimulationResult]:
        sims = self.ledger.simulate(s.user_id, actions)
        for sim in sims:
            self._audit("simulate", s, action_id=sim.action_id, feasible=sim.feasible, errors=sim.errors,
                        fee=sim.fee, balances_after=sim.balances_after)
        return sims

    def _evaluate(self, s: _Session, rec: _Rec, sim: SimulationResult, verified: bool,
                  events: list[SecurityEvent], flag: bool = True) -> PolicyDecision:
        d = self.policy.evaluate(self._ctx(s, verified, events, rec.reserve), rec.action, sim)
        self._audit("policy", s, action_id=rec.action.action_id, verdict=d.verdict, risk_level=d.risk_level,
                    failed_rules=[r.rule_id for r in d.rules if not r.passed],
                    checks_passed=sum(1 for r in d.rules if r.passed), checks_total=len(d.rules),
                    step_up_verified=verified, policy_version=d.policy_version, final=flag)
        if flag:
            for r in d.rules:
                kind = _FLAG_RULES.get(r.rule_id)
                if kind and not r.passed and kind not in rec.flagged:
                    rec.flagged.add(kind)
                    events.append(self._event(s, kind, rec.action.action_id, r.detail))
        return d

    def _decide(self, s: _Session, rec: _Rec, events: list[SecurityEvent]) -> Receipt | None:
        assert rec.sim is not None
        rec.decision = self._evaluate(s, rec, rec.sim, False, events)
        v = rec.decision.verdict
        if v == Verdict.DENY:
            return self._deny(s, rec)
        if v == Verdict.ALLOW:
            return self._execute(s, rec, None, events)
        rec.status = "awaiting_confirmation"
        rec.creq = self._creq(rec)
        self._audit("await_confirmation", s, action_id=rec.action.action_id, verdict=rec.creq.verdict,
                    risk_level=rec.creq.risk_level, params_hash=rec.creq.params_hash, title=rec.title)
        return None

    def _execute(self, s: _Session, rec: _Rec, conf: Confirmation | None, events: list[SecurityEvent]) -> Receipt:
        res = self.ledger.execute(s.user_id, rec.action, conf)
        if res.status == "replayed":
            events.append(self._event(s, "replay_blocked", rec.action.action_id,
                                      "This request was already executed; no money moved again."))
        rec.status = {"executed": "executed", "replayed": "replayed"}.get(res.status, "failed")
        rec.last = res
        self._audit("execute", s, action_id=rec.action.action_id, status=res.status, ledger_tx_id=res.ledger_tx_id,
                    idempotency_key=rec.action.idempotency_key, error=res.error,
                    confirmation_method=conf.method if conf else None)
        summary = {"executed": "EXECUTED", "replayed": "REPLAYED"}.get(res.status, "FAILED") + f": {rec.title}"
        r = self._append(s, rec, conf, res, summary)
        rec.receipt_id = r.receipt_id
        return r

    def _deny(self, s: _Session, rec: _Rec) -> Receipt:
        rec.status = "denied"
        reason = self._deny_reason(rec)
        res = ExecutionResult(action_id=rec.action.action_id, idempotency_key=rec.action.idempotency_key,
                              status="denied", error=reason)
        rec.last = res
        d = rec.decision
        self._audit("deny", s, action_id=rec.action.action_id, reason=reason,
                    failed_rules=[r.rule_id for r in d.rules if not r.passed] if d else [])
        r = self._append(s, rec, None, res, f"DENIED: {rec.title}. {reason}")
        rec.receipt_id = r.receipt_id
        return r

    def _append(self, s: _Session, rec: _Rec, conf: Confirmation | None, res: ExecutionResult, summary: str) -> Receipt:
        # The store filters receipts by the user_id carried inside the signed intent.
        r = self.receipts.append(action_id=rec.action.action_id,
                                 intent={"user_id": s.user_id, "intent": rec.intent}, action=rec.action,
                                 simulation=rec.sim, decision=rec.decision, confirmation=conf,
                                 result=res, summary=summary)
        self._audit("receipt", s, action_id=rec.action.action_id, receipt_id=r.receipt_id, seq=r.seq,
                    receipt_hash=r.receipt_hash, summary=r.summary)
        return r

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
        return self._deny_sentence(rec)

    @staticmethod
    def _last_error(rec: _Rec) -> str:
        res = rec.last
        if res is None or not res.error:
            return ""
        return {"INSUFFICIENT_FUNDS": "There isn't enough money in the account.",
                "QUOTE_EXPIRED": "The exchange quote expired.",
                "QUOTE_ALREADY_USED": "That exchange quote was already used.",
                "INVALID_IBAN": "The IBAN isn't valid.",
                "PAYEE_NAME_TAKEN": "You already have a payee with that name."}.get(res.error, "The bank refused it.")

    # ------------------------------------------------------------------ helpers
    def _creq(self, rec: _Rec) -> ConfirmationRequest:
        d, sim, a = rec.decision, rec.sim, rec.action
        assert d is not None
        lines: list[str] = []
        if isinstance(a, PaymentAction):
            lines += [f"To the account ending {a.iban[-4:]} (saved for this payee)"]
            if a.memo and a.obligation_id is None:      # AUDIT2-6: the reference sent to the payee is shown
                lines.append(f"Reference sent to the payee: {a.memo}")
        elif isinstance(a, FxAction):
            lines += [f"Rate {a.quote.rate} {a.quote.buy_currency} per {a.quote.sell_currency} "
                      f"(quote {a.quote.quote_id})",
                      f"Cost of this exchange: {fx_cost_text(a.quote)}, included in the "
                      f"{_m(a.quote.sell_amount)} {a.quote.sell_currency} you pay"]
        elif isinstance(a, AddBeneficiaryAction):
            lines += [f"IBAN ends {a.iban[-4:]}", "Payments to this payee need your one-time code for 72 hours"]
        if sim is not None:
            ccy: dict[str, str] = {}
            if isinstance(a, PaymentAction):
                ccy[a.from_account_id] = a.currency
            elif isinstance(a, FxAction):
                ccy = {a.from_account_id: a.quote.sell_currency, a.to_account_id: a.quote.buy_currency}
            lines += [f"{ccy.get(acc, acc)} balance after: {_m(sim.balances_before.get(acc, v))} -> {_m(v)}"
                      for acc, v in sim.balances_after.items() if sim.balances_before.get(acc) != v]
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
            security_events=[*s.events, *[e for e in events if e not in s.events]],
            recent_payments=self.ledger.recent_payments(s.user_id, self.now - params.STRUCTURING_WINDOW))

    def _turn(self, s: _Session, text: str, views: list[ActionView] | None = None,
              events: list[SecurityEvent] | None = None, receipts: list[Receipt] | None = None) -> AgentTurn:
        for e in events or []:
            self._remember(s, e)
        return AgentTurn(session_id=s.session_id, assistant_text=text, actions=views or [],
                         security_events=events or [], receipts=receipts or [],
                         anchor=self._anchor(s.user_id) if receipts else None,
                         pending_question=s.draft.question if s.draft else None)

    def _compose(self, t: _Turn, s: _Session | None = None) -> str:
        out: list[str] = list(t.info)
        seen: set[str] = set()
        for e in t.events:
            if e.event_id in t.explained or e.kind in seen:
                continue
            if e.kind == "beneficiary_mismatch":
                out.append(e.detail)
            elif e.kind == "tool_output_poisoning":
                if not any("exchange" in x for x in t.info):
                    out.append("Careful: the currency provider's reply contained extra text aimed at me. I ignored it "
                               "and used only the price, after checking it against the bank's own rate.")
            elif e.kind in ("jailbreak_attempt", "data_access_denied") and not t.info:
                out.append("I can only show your own accounts and payees." if e.kind == "data_access_denied" else
                           "I can't switch off confirmations, codes or limits; every payment goes through them.")
            else:
                continue
            seen.add(e.kind)
        out += t.notes
        views = [v for v in t.views if v.status != "rejected_by_user"]
        executed = [v for v in views if v.status in ("executed", "replayed")]
        waiting = [v for v in views if v.status == "awaiting_confirmation"]
        denied = [v for v in views if v.status == "denied"]
        failed = [v for v in views if v.status == "failed"]
        for v in executed:
            r = self._sessions_rec(v.action.action_id)
            out.append(f"Done: {self._done(r) if r else v.action.action_id}.")
        has_fx = any(isinstance(v.action, FxAction) for v in waiting)
        for i, v in enumerate(waiting):
            r = self._sessions_rec(v.action.action_id)
            if r is not None and v.confirmation_request is not None:
                out.append(self._plan_line(r, first=i == 0, more=len(waiting) > 1, funds_payments=has_fx))
        for v in denied:
            r = self._sessions_rec(v.action.action_id)
            out.append(self._deny_sentence(r) if r else "That was blocked. Nothing was sent.")
        for v in failed:
            out.append(f"That didn't go through: {self._rec_title(v)}. Nothing was sent.")
        if waiting:
            summary = self._plan_summary(waiting, denied, s)
            if summary:
                out.append(summary)
            if s is not None and self.step_up.locked(s.user_id) and any(
                    v.confirmation_request and v.confirmation_request.verdict == "step_up" for v in waiting):
                out.append(f"Approval by code is locked on your account after {params.MAX_OTP_ATTEMPTS} wrong codes, "
                           "so the steps that need a code can't be approved until the bank unlocks it.")
            out.append("Nothing moves until you approve each step, in this order." if len(waiting) > 1
                       else "Nothing moves until you approve it.")
        if t.question:
            out.append(t.question)
        if not out:
            out.append("I couldn't turn that into an action. Try \"send 50 euros to mom\" or \"pay my rent\".")
        return "\n".join(x for x in out if x)

    # ------------------------------------------------------------------ plain-language building blocks
    def _ben(self, s_user: str | None, bid: str) -> Beneficiary | None:
        if s_user is None:
            return None
        return next((b for b in self.ledger.get_beneficiaries(s_user) if b.beneficiary_id == bid), None)

    def _user_of(self, rec: _Rec) -> str | None:
        for s in self._sessions.values():
            if rec.action.action_id in s.records and s.records[rec.action.action_id] is rec:
                return s.user_id
        return None

    def _plain(self, rec: _Rec) -> str:
        """What the step does, in words: 'send 1,200.00 EUR to Université Paris-Saclay'."""
        a = rec.action
        if isinstance(a, PaymentAction):
            b = self._ben(self._user_of(rec), a.beneficiary_id)
            return f"send {_m(a.amount)} {a.currency} to {b.display_name if b else a.beneficiary_id}"
        if isinstance(a, FxAction):
            q = a.quote
            return f"convert {_m(q.sell_amount)} {q.sell_currency} into {_m(q.buy_amount)} {q.buy_currency}"
        if isinstance(a, AddBeneficiaryAction):
            return f"add {a.display_name} as a payee"
        return rec.title

    def _done(self, rec: _Rec) -> str:
        p = self._plain(rec)
        return {"send": "sent", "convert": "converted", "add": "added"}.get(p.split(" ", 1)[0], p.split(" ", 1)[0]) + \
            " " + p.split(" ", 1)[1]

    def _plan_line(self, rec: _Rec, *, first: bool, more: bool, funds_payments: bool) -> str:
        a, cr = rec.action, rec.creq
        if isinstance(a, FxAction):
            q = a.quote
            what = (f"{'First, buy' if first and more else 'Buy'} {_m(q.buy_amount)} {q.buy_currency} for "
                    f"{_m(q.sell_amount)} {q.sell_currency} at {q.rate} {q.buy_currency} per {q.sell_currency}"
                    + (", so the euro payments can go through." if more and funds_payments else ".")
                    + f" Cost of this exchange: {fx_cost_text(q)}, included in that price.")
        elif isinstance(a, PaymentAction):
            b = self._ben(self._user_of(rec), a.beneficiary_id)
            name = b.display_name if b else a.beneficiary_id
            if a.obligation_id and b is not None:
                kind = a.memo.split(" ", 1)[0] if a.memo else b.category
                what = f"{kind.capitalize()}: {_m(a.amount)} {a.currency} to {name}."
            else:
                what = f"Send {_m(a.amount)} {a.currency} to {name}."
        elif isinstance(a, AddBeneficiaryAction):
            what = f"Add {a.display_name} as a payee (account ending {a.iban[-4:]})."
        else:
            what = rec.title + "."
        if cr is None or cr.verdict == "confirm":
            return f"{what} One tap to approve."
        reasons = self._step_up_reasons(rec)
        if isinstance(a, AddBeneficiaryAction) and not reasons:
            return f"{what} Adding a payee always needs your one-time code."
        why = _and(reasons) if reasons else "this step is above what you can approve with one tap"
        return f"{what} {why[:1].upper() + why[1:]}, so I need your one-time code for this one."

    def _step_up_reasons(self, rec: _Rec) -> list[str]:
        d, a = rec.decision, rec.action
        if d is None:
            return []
        user = self._user_of(rec)
        out: list[str] = []
        for r in d.rules:
            if r.passed or r.severity != RiskLevel.R3:
                continue
            if r.rule_id == "BENEFICIARY_AGE" and isinstance(a, PaymentAction):
                b = self._ben(user, a.beneficiary_id)
                if b is not None:
                    hours = int((self.now - b.added_at).total_seconds() // 3600)
                    ago = f"{hours} hour{'s' if hours != 1 else ''}" if hours >= 1 else "less than an hour"
                    out.append(f"you added {b.display_name} only {ago} ago, and payments to newly added accounts are "
                               "the most common target of invoice and rental scams")
            elif r.rule_id == "AMOUNT_TIER":
                out.append(f"it is above the {_m(params.R2_MAX_PAYMENT_EUR)} EUR you can approve with one tap")
            elif r.rule_id == "FX_TIER":
                out.append(f"it converts more than the {_m(params.R2_MAX_FX_EUR)} EUR you can approve with one tap")
            elif r.rule_id == "STRUCTURING" and isinstance(a, PaymentAction) and user is not None:
                b = self._ben(user, a.beneficiary_id)
                since = self.now - params.STRUCTURING_WINDOW
                prior = sum((p.amount_eur for p in self.ledger.recent_payments(user, since)
                             if p.beneficiary_id == a.beneficiary_id), Decimal("0"))
                total = prior + self.ledger.eur_equivalent(a.amount, a.currency)
                out.append(f"together with what you already sent {b.display_name if b else 'this payee'} in the last "
                           f"24 hours it comes to {_m(total)} EUR, above the {_m(params.STRUCTURING_THRESHOLD_EUR)} EUR "
                           "you can send one tap at a time")
        return list(dict.fromkeys(out))

    def _plan_summary(self, waiting: list[ActionView], denied: list[ActionView], s: _Session | None) -> str:
        if denied or s is None:
            return ""
        last = waiting[-1].simulation
        if last is None or not last.feasible:
            return ""
        accs = {a.account_id: a for a in self.ledger.get_accounts(s.user_id)}
        first = waiting[0].simulation
        before = first.balances_before if first is not None else {}
        changed = [(accs[k].currency, v) for k, v in last.balances_after.items()
                   if k in accs and before.get(k) != v]
        if not changed:
            return ""
        changed.sort(key=lambda cv: cv[0] != "EUR")
        n = len(waiting)
        lead = {1: "After this", 2: "After both steps", 3: "After all three steps", 4: "After all four steps"}.get(
            n, f"After all {n} steps")
        cur0, v0 = changed[0]
        text = f"{lead} you will have {_m(v0)} {cur0} left"
        reserve = next((self._sessions_rec(v.action.action_id).reserve for v in waiting  # type: ignore[union-attr]
                        if self._sessions_rec(v.action.action_id) is not None
                        and self._sessions_rec(v.action.action_id).reserve), None)  # type: ignore[union-attr]
        if reserve is not None and cur0 == "EUR":
            text += ", exactly your reserve" if _q(v0) == _q(reserve) else f", above your {_m(reserve)} EUR reserve"
        rest = [f"{_m(v)} {c}" for c, v in changed[1:]]
        return text + (f", and {_and(rest)}." if rest else ".")

    def _deny_sentence(self, rec: _Rec) -> str:
        a, d = rec.action, rec.decision
        user = self._user_of(rec)
        if isinstance(a, PaymentAction):
            b = self._ben(user, a.beneficiary_id)
            head = f"I can't send {_m(a.amount)} {a.currency} to {b.display_name if b else 'that payee'}"
            tail = "Nothing was sent."
        elif isinstance(a, FxAction):
            head = f"I can't make this conversion ({_m(a.quote.buy_amount)} {a.quote.buy_currency})"
            tail = "Nothing was converted."
        elif isinstance(a, AddBeneficiaryAction):
            head = f"I can't add {a.display_name} as a payee"
            tail = "Nothing was saved."
        else:
            head, tail = "I can't do that", "Nothing was sent."
        reasons: list[str] = []
        failing = [r for r in (d.rules if d else []) if not r.passed and r.severity == RiskLevel.R4]
        ids = {r.rule_id for r in failing}
        for r in failing:
            why = self._plain_reason(rec, r, ids, user)
            if why and why not in reasons:
                reasons.append(why)
        if not reasons:
            reasons.append("one of the safety checks failed")
        return f"{head}: {_and(reasons)}. {tail}"

    def _plain_reason(self, rec: _Rec, r: RuleResult, ids: set[str], user: str | None) -> str:
        a, sim = rec.action, rec.sim
        rid = r.rule_id
        if rid == "SIM_FEASIBLE":
            errs = sim.errors if sim is not None else []
            if "INSUFFICIENT_FUNDS" in errs and user is not None:
                accs = self.ledger.get_accounts(user)
                src = a.from_account_id if isinstance(a, (PaymentAction, FxAction)) else None
                main = next((x for x in accs if x.account_id == src), None)
                if main is not None:
                    others = [f"{_m(x.balance)} {x.currency}" for x in accs if x.account_id != src]
                    return f"you have {_m(main.balance)} {main.currency}" + (f" (plus {_and(others)})" if others else "")
                return "there isn't enough money in the account"
            if any(e.startswith("QUOTE_") for e in errs):
                return "the exchange quote is no longer valid, so ask me again for a fresh one"
            if "CURRENCY_MISMATCH" in errs:
                return "that account is in a different currency"
            if "UNKNOWN_ACCOUNT" in errs:
                return "that account isn't one of yours"
            if "INVALID_AMOUNT" in errs:
                return "the amount has to be more than zero"
            if "INVALID_IBAN" in errs or "PAYEE_NAME_TAKEN" in errs:
                return ""                     # explained by IBAN_VALID / PAYEE_NAME_UNIQUE
            return "the bank could not carry it out"
        if rid == "MAX_SINGLE_PAYMENT":
            return f"single payments are capped at {_m(params.MAX_SINGLE_PAYMENT_EUR)} EUR"
        if rid == "DAILY_LIMIT":
            return (f"together with what you've already sent today it would go over your "
                    f"{_m(params.DAILY_OUTFLOW_LIMIT_EUR)} EUR daily limit")
        if rid == "RESERVE_FLOOR":
            left = None
            if sim is not None and user is not None:
                eur_ids = {x.account_id for x in self.ledger.get_accounts(user) if x.currency == "EUR"}
                left = sum((v for k, v in sim.balances_after.items() if k in eur_ids), Decimal("0"))
            floor = rec.reserve
            if left is not None and floor is not None:
                return f"it would leave you {_m(left)} EUR, below the {_m(floor)} EUR you asked to keep"
            return "it would take you below the reserve you asked to keep"
        if rid == "UNTRUSTED_ORIGIN":
            return "its details came from a document, not from you"
        if rid == "SANCTIONS_SCREEN":
            det = r.detail
            if "manual sanctions review" in det:
                return ("the name uses letters I can't check against the sanctions list, so the bank has to review it "
                        "by hand first")
            if "matches sanctions watchlist" in det or "on the sanctions watchlist" in det:
                return "the payee matches an entry on the sanctions list, so the bank can't deal with it"
            if "IBAN_VALID" in ids:
                return ""
            return "the sanctions check could not be completed"
        if rid == "IBAN_VALID":
            problem = iban_problem(getattr(a, "iban", "")) or ""
            if "check digits" in problem:
                why = "its check digits don't add up, which usually means a typo"
            elif "plain letters" in problem:
                why = "it contains characters other than plain letters A-Z and digits 0-9"
            elif "characters, this one has" in problem:
                why = problem.replace("a ", "a ", 1)
            else:
                why = "it doesn't have the format of an IBAN"
            return f"that IBAN isn't valid ({why}). Please copy it again from your payee's bank details"
        if rid == "PAYEE_NAME_UNIQUE":
            return "you already have a payee with a name that looks the same"
        if rid == "FX_PRICE_CHECK":
            return "the exchange quote didn't match the bank's own rate"
        if rid == "BENEFICIARY_REGISTERED":
            return "that payee isn't one of your saved payees"
        if rid == "BENEFICIARY_NOT_BLOCKED":
            return "that payee is blocked on your account"
        if rid == "IBAN_MATCHES_REGISTRY":
            return "the account doesn't match the one saved for that payee"
        if rid == "TENANT_ISOLATION":
            return "that account or payee isn't yours"
        if rid == "STEP_UP_LOCKOUT":
            return (f"{params.MAX_OTP_ATTEMPTS} wrong one-time codes were entered, so approval by code is locked on "
                    "your account")
        if rid == "POLICY_ERROR":
            return "a safety check could not be completed"
        return "one of the safety checks failed"

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
            self._audit("security_event", s, kind=e.kind, source=e.source, detail=e.detail, event_id=e.event_id)

    # ------------------------------------------------------------------ audit
    def _audit(self, step: str, s: _Session, action_id: str | None = None, **fields: Any) -> None:
        if self.audit_sink is None:
            return
        rec: dict[str, Any] = {"step": step, "session_id": s.session_id, "turn": s.turn}
        if action_id is not None:
            rec["action_id"] = action_id
        rec.update(_safe(fields))
        try:
            self.audit_sink(rec)
        except Exception:   # an audit consumer must never break the money path
            pass

    def _audit_action(self, s: _Session, a: ProposedAction, title: str) -> None:
        self._audit("expand", s, action_id=a.action_id, action_type=a.action_type, title=title,
                    action=a.model_dump(mode="python"))

    @staticmethod
    def _aid(idem: str) -> str:
        # deterministic per (session, message number): replaying a session's message sequence gives the same ids
        return "act_" + idem[len("idem_"):][:16]

    @staticmethod
    def _idem(s: _Session, idx: int, intent: Any, step: int) -> str:
        # s.turn makes each user message a new request: a deliberate second "send 50 to mom" is a second payment,
        # while re-confirming the SAME action reuses its key and is replayed by the ledger (money moves once).
        return "idem_" + sha256_hex([s.session_id, s.turn, idx, intent, step])[:24]


def _and(items: list[str]) -> str:
    items = [x for x in items if x]
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


_RELATION_WORDS = {"my", "the", "a", "friend", "buddy", "pal", "mate", "colleague", "neighbour", "neighbor",
                   "roommate", "flatmate", "classmate"}


def _display_ref(ref: str) -> str:
    """'friend ahmed' -> 'Ahmed' (how a person would write the name back)."""
    words = [w for w in ref.split() if w.lower() not in _RELATION_WORDS] or ref.split()
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def build_orchestrator(ledger: Ledger, receipts: ReceiptStore, policy: PolicyEngine, guard: ContentGuard,
                       parser: IntentParser | None = None, now: datetime = params.DEMO_NOW,
                       audit_sink: AuditSink | None = None, step_up: StepUpAttempts | None = None):
    """`step_up`: the per-customer wrong-code store (defaults to the ledger's, so all sessions share it)."""
    return _Orchestrator(ledger, receipts, policy, guard, parser, now, audit_sink, step_up)
