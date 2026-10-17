"""Rule-based ContentGuard: trust-boundary filter for documents, user messages, tool outputs.

Detection is a documented list of named regex patterns (below), no LLM. A pattern name
is what ends up in ``DocumentScan.matched_patterns``. Matching is case-insensitive and
runs on text with zero-width characters removed, so ``ig​nore`` is still caught.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from bg.contracts.models import DocumentScan, SecurityEvent, UntrustedDocument
from bg.contracts.params import DEMO_NOW

_F = re.IGNORECASE
_GAP = r"(?:\w+[\s,]+){0,4}?"   # up to four filler words ("your previous", "all of the")

# --------------------------------------------------------------------------- shared injection patterns
# (name, regex). Used on documents and on tool-output text fields.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "ignore all previous instructions", "disregard your spending limits", "forget the rules"
    ("instruction_override", re.compile(
        r"\b(?:ignore|disregard|forget|override|overrule|bypass|circumvent)\s+" + _GAP +
        r"(?:instructions?|rules?|limits?|limitations?|guidelines?|prompts?|directives?|"
        r"polic(?:y|ies)|restrictions?|constraints?|safeguards?|safety)\b", _F)),
    # "disregard everything above"
    ("instruction_override_context", re.compile(
        r"\b(?:ignore|disregard|forget)\s+(?:everything|anything|all)\s+(?:above|before|previously|prior|"
        r"you\s+(?:were|have been)\s+told)", _F)),
    ("system_prompt_reference", re.compile(r"\bsystem[\s_-]*prompt\b|\bdeveloper\s+message\b", _F)),
    # "SYSTEM OVERRIDE", "[SYSTEM]", "new instructions:", "pre-approved"
    ("fake_system_message", re.compile(
        r"\bsystem\s+override\b|\[\s*system\s*\]|\bnew\s+instructions?\s*:|\bpre-?approved\b|"
        r"\badmin(?:istrator)?\s+override\b", _F)),
    # role-play / impersonation of the assistant
    ("role_impersonation", re.compile(
        r"\byou\s+are\s+now\b|\bpretend\s+(?:to\s+be|you\s+are)\b|\bact\s+as\s+(?:if\s+you\s+(?:were|are)\s+)?"
        r"(?:the\s+|an?\s+|my\s+)?(?:ai\b|assistant|llm|bank\s+(?:agent|employee)|compliance|administrator|admin)|"
        r"\bi\s+am\s+(?:the|your)\s+(?:ai|assistant|developer|administrator)\b|\bfrom\s+now\s+on\s+you\b", _F)),
    # the text speaks to the AI instead of to a human reader
    ("addressed_to_ai", re.compile(
        r"\b(?:note|message|instructions?|attention|notice|important|hey|hello|dear)\s*(?:to|for|:)?\s*"
        r"(?:the\s+|my\s+)?(?:ai|llm|assistant|language\s+model|chatbot|ai\s+agent)\b|"
        r"\bai\s+assistant\b|\bif\s+you\s+are\s+an?\s+(?:ai|llm|language\s+model|assistant)\b", _F)),
    ("skip_confirmation", re.compile(
        r"\b(?:do\s+not|don'?t|never|without)\s+(?:ask(?:ing)?\s+)?(?:the\s+user\s+)?(?:for\s+)?"
        r"(?:any\s+)?(?:confirmation|confirming|verification|approval|otp|step-?up)\b|"
        r"\bskip\s+(?:the\s+)?(?:confirmation|verification|approval|otp|step-?up|checks?)\b|"
        r"\bno\s+need\s+to\s+(?:confirm|verify|ask)\b", _F)),
    # an instruction to move money inside a document ("transfer EUR 2,000", "send 500 euros")
    ("transfer_request", re.compile(
        r"\b(?:transfer|send|wire|remit|move)\s+(?:the\s+|a\s+total\s+of\s+)?"
        r"(?:(?:EUR|USD|€|\$)\s*\d[\d.,]*|\d[\d.,]*\s*(?:EUR|USD|€|\$|euros?|dollars?))", _F)),
    ("limit_override", re.compile(
        r"\b(?:raise|increase|remove|lift|disable|bypass)\s+(?:\w+\s+){0,2}(?:limits?|reserve|floor)\b", _F)),
    # French variants: "ignorez vos instructions", "virez 2000 EUR", "sans demander confirmation"
    ("instruction_override_fr", re.compile(
        r"\b(?:ignore[zr]?|oubli(?:e[zr]?|ez)|contourne[zr]?|d[ée]sactive[zr]?)\s+" + _GAP +
        r"(?:instructions?|r[èe]gles?|limites?|consignes?|restrictions?|plafonds?|s[ée]curit[ée])\b", _F)),
    ("addressed_to_ai_fr", re.compile(
        r"\b(?:note|message|instructions?|attention|avis)\s+(?:pour|[àa])\s+l['’]?\s*(?:assistant|ia\b|agent)", _F)),
    ("transfer_request_fr", re.compile(
        r"\b(?:vire[zr]?|transf[èe]re[zr]?|envoie[zr]?|verse[zr]?)\s+(?:imm[ée]diatement\s+)?"
        r"(?:(?:EUR|USD|€|\$)\s*\d[\d.,]*|\d[\d.,]*\s*(?:EUR|USD|€|\$|euros?|dollars?))", _F)),
    ("skip_confirmation_fr", re.compile(
        r"\bsans\s+(?:demander\s+)?(?:de\s+|la\s+)?(?:confirmation|v[ée]rification|validation|otp)\b", _F)),
]

# Hidden HTML comment: always a finding when it addresses an AI / contains instruction words.
_HTML_COMMENT = re.compile(r"<!--[\s\S]*?(?:-->|$)")
_COMMENT_AI = re.compile(
    r"\b(?:ai|llm|assistant|chatbot|language\s+model|instructions?|ignore|disregard|system|transfer|send)\b", _F)
_ZERO_WIDTH = re.compile("[​-‏⁠-⁤﻿‪-‮­]")

# --------------------------------------------------------------------------- user-message patterns
_USER_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    # (event kind, pattern name, regex)
    ("jailbreak_attempt", "ignore_rules", re.compile(
        r"\b(?:ignore|disregard|forget|bypass|override|circumvent)\s+" + _GAP +
        r"(?:instructions?|rules?|limits?|limitations?|guidelines?|restrictions?|constraints?|"
        r"polic(?:y|ies)|safeguards?|safety)\b", _F)),
    ("jailbreak_attempt", "no_limits_roleplay", re.compile(
        r"\bpretend\s+(?:you|that\s+you|to)\b|\byou\s+are\s+now\b|\bdeveloper\s+mode\b|\bjailbreak\b|"
        r"\bdo\s+anything\s+now\b|\byou\s+have\s+no\s+(?:limits?|restrictions?|rules?)\b", _F)),
    ("jailbreak_attempt", "reveal_system_prompt", re.compile(
        r"\b(?:reveal|show|print|display|repeat|tell|give|leak|output|share|read\s+out)\b[^.\n]{0,30}?"
        r"\b(?:system\s+prompt|hidden\s+prompt|(?:your|the)\s+(?:initial\s+|original\s+|hidden\s+)?"
        r"(?:instructions|prompt|rules|guidelines))\b", _F)),
    ("jailbreak_attempt", "disable_confirmation", re.compile(
        r"\b(?:disable|turn\s+off|skip|bypass|remove|switch\s+off|stop)\s+(?:the\s+|my\s+|all\s+)?"
        r"(?:confirmation|confirmations|otp|step-?up|2fa|verification|approval|security\s+checks?|"
        r"safeguards?|limits?|daily\s+limit)\b|"
        r"\b(?:without|no\s+need\s+for)\s+(?:asking\s+(?:me\s+)?for\s+|any\s+)?(?:confirmation|otp|step-?up|verification)\b|"
        r"\b(?:don'?t|do\s+not|never|stop)\s+(?:ask|asking)\s+(?:me\s+)?(?:for\s+)?"
        r"(?:confirmation|to\s+confirm|otp|verification)\b", _F)),
    ("data_access_denied", "other_customers_data", re.compile(
        r"\b(?:other|another|different|all|every)\s+(?:bank\s+)?(?:customers?|users?|clients?|account\s+holders?)"
        r"(?:'s|s')?\s+(?:accounts?|balances?|data|transactions?|details|iban)|"
        r"\b(?:accounts?|balances?|transactions?)\s+(?:of|belonging\s+to)\s+(?:another|other|a\s+different|someone\s+else)|"
        r"\b(?:someone|somebody)\s+else'?s\s+(?:accounts?|balances?|transactions?)|"
        r"\bu_other\b|\bacc_other\w*\b|\bb_other\b|"
        # bulk enumeration of the whole system, not of the user's own data
        r"\b(?:every|all)\s+(?:the\s+)?(?:users?|customers?|clients?)\b|\b(?:in|from)\s+the\s+database\b", _F)),
    # attempts to extract credentials or internals
    ("jailbreak_attempt", "reveal_secrets", re.compile(
        r"\b(?:your|configured\s+with|reveal|dump|leak|print|show)\b[^.\n]{0,30}?"
        r"\b(?:api\s+keys?|secrets?|credentials|private\s+keys?|passwords?|access\s+tokens?)\b|"
        r"\b(?:api\s+keys?|secrets?|credentials|private\s+keys?)\b[^.\n]{0,40}?"
        r"\b(?:configured|stored|you\s+(?:have|use|hold)|are\s+you\s+using)\b|"
        r"\b(?:debug|admin|maintenance|god|sudo|root)\s+mode\b|\binternal\s+state\b|\bhidden\s+tools?\b", _F)),
    # social-engineering claim that approval already happened elsewhere
    ("jailbreak_attempt", "claimed_preapproval", re.compile(
        r"\b(?:pre-?approved|pre-?authori[sz]ed|already\s+(?:approved|authori[sz]ed|cleared))\b|"
        r"\b(?:compliance|the\s+bank|my\s+bank|security)\s+(?:has|have)\s+(?:approved|cleared|authori[sz]ed)\b|"
        r"\bno\s+(?:further\s+|more\s+)?(?:confirmation|verification|otp)\s+(?:is\s+)?(?:needed|required|necessary)\b", _F)),
]


def _short(text: str, n: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 3] + "..."


class RuleBasedContentGuard:
    """Implements ``ContentGuard``. Deterministic, offline."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: DEMO_NOW)
        self._counter = 0

    # ------------------------------------------------------------------ events
    def _event(self, kind: str, source: str, detail: str, at: datetime | None = None) -> SecurityEvent:
        self._counter += 1
        digest = hashlib.sha256(f"{kind}|{source}|{detail}".encode("utf-8")).hexdigest()[:8]
        return SecurityEvent(event_id=f"evt_{self._counter:04d}_{digest}", kind=kind,  # type: ignore[arg-type]
                             source=source, detail=detail, at=at or self._clock())

    # ------------------------------------------------------------------ documents
    @staticmethod
    def _match_spans(text: str) -> list[tuple[str, int, int]]:
        spans = []
        for name, rx in _INJECTION_PATTERNS:
            for m in rx.finditer(text):
                spans.append((name, m.start(), m.end()))
        return spans

    def scan_document(self, doc: UntrustedDocument) -> DocumentScan:
        raw = doc.text
        findings: list[str] = []
        if _ZERO_WIDTH.search(raw):
            findings.append("zero_width_characters")
        text = _ZERO_WIDTH.sub("", raw)

        comment_spans = [(m.start(), m.end()) for m in _HTML_COMMENT.finditer(text)]
        if any(_COMMENT_AI.search(text[s:e]) for s, e in comment_spans):
            findings.append("hidden_html_comment_to_ai")

        # Patterns are matched on the full text (comments included: that is where attackers hide).
        for name, _s, _e in self._match_spans(text):
            if name not in findings:
                findings.append(name)

        # Visible text = comments blanked out (newlines kept). Lines touching a pattern match are dropped.
        chars = list(text)
        for s, e in comment_spans:
            for i in range(s, e):
                if chars[i] != "\n":
                    chars[i] = " "
        visible = "".join(chars)
        bad = [(s, e) for _n, s, e in self._match_spans(visible)]
        lines, pos = [], 0
        for line in visible.split("\n"):
            start, end = pos, pos + len(line)
            pos = end + 1
            if not any(s < end + 1 and e > start for s, e in bad):
                lines.append(line)

        detected = bool(findings)
        return DocumentScan(
            document_id=doc.document_id, injection_detected=detected,
            matched_patterns=findings, extracted=self._extract(lines), quarantined=detected)

    @staticmethod
    def _extract(lines: list[str]) -> dict[str, str]:
        """Plain data from visible, non-injected lines only. Never executed, never trusted."""
        out: dict[str, str] = {}
        ibans: list[str] = []
        for line in lines:
            m = re.search(r"\b(?:amount(?:\s+due)?|total(?:\s+due)?|balance\s+due)\b[^:\d]*:?\s*"
                          r"(EUR|USD|€|\$)?\s*(\d[\d.,]*)\s*(EUR|USD|€|\$)?", line, _F)
            if m and "amount" not in out:
                num = RuleBasedContentGuard._number(m.group(2))
                if num is not None:
                    out["amount"] = num
                    cur = (m.group(1) or m.group(3) or "").upper().replace("€", "EUR").replace("$", "USD")
                    if cur:
                        out["currency"] = cur
            m = re.search(r"\bdue\s+date\b\s*:?\s*(\d{4}-\d{2}-\d{2})", line, _F)
            if m and "due_date" not in out:
                out["due_date"] = m.group(1)
            m = re.search(r"\bIBAN\b\s*:?\s*([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30})", line, re.I)
            if m:
                iban = "".join(m.group(1).split()).upper()
                if iban not in ibans:
                    ibans.append(iban)
        if len(ibans) == 1:      # several different IBANs in visible text -> ambiguous, extract none
            out["iban"] = ibans[0]
        return out

    @staticmethod
    def _number(s: str) -> str | None:
        s = s.rstrip(".,")
        if "," in s and "." in s:
            s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
        elif "," in s:
            head, _, tail = s.rpartition(",")
            s = head.replace(",", "") + "." + tail if len(tail) == 2 else s.replace(",", "")
        try:
            return format(Decimal(s), "f")
        except InvalidOperation:
            return None

    # ------------------------------------------------------------------ user message
    def scan_user_message(self, text: str, at: datetime | None = None) -> list[SecurityEvent]:
        clean = _ZERO_WIDTH.sub("", text)
        events: list[SecurityEvent] = []
        for kind, name, rx in _USER_PATTERNS:
            m = rx.search(clean)
            if m:
                events.append(self._event(kind, "user_message", f"{name}: {_short(m.group(0), 80)!r}", at))
        return events

    # ------------------------------------------------------------------ tool outputs
    def sanitize_tool_output(self, tool_name: str, payload: dict[str, Any], schema: type[BaseModel],
                             at: datetime | None = None) -> tuple[BaseModel | None, list[SecurityEvent]]:
        events: list[SecurityEvent] = []
        if not isinstance(payload, dict):
            return None, [self._event("tool_output_poisoning", tool_name,
                                      f"payload is {type(payload).__name__}, expected an object", at)]
        allowed = set(schema.model_fields)
        kept: dict[str, Any] = {}
        for key, value in payload.items():
            if key in allowed:
                kept[key] = value
                continue
            text = _short(value)
            why = ""
            if self._looks_like_instruction(str(value)):
                why = " (reads like an instruction to the assistant)"
            events.append(self._event(
                "tool_output_poisoning", tool_name,
                f"unexpected field {str(key)!r} dropped from {tool_name} output{why}: {text!r}", at))

        for key, value in kept.items():
            if isinstance(value, str) and self._looks_like_instruction(value):
                events.append(self._event(
                    "tool_output_poisoning", tool_name,
                    f"field {key!r} of {tool_name} output reads like an instruction: {_short(value)!r}", at))
                return None, events
        try:
            model = schema.model_validate(kept)
        except ValidationError as exc:
            bad = sorted({".".join(str(p) for p in err["loc"]) or "<root>" for err in exc.errors()})
            events.append(self._event(
                "tool_output_poisoning", tool_name,
                f"{tool_name} output failed schema validation on field(s): {', '.join(bad)}", at))
            return None, events
        return model, events

    @staticmethod
    def _looks_like_instruction(text: str) -> bool:
        clean = _ZERO_WIDTH.sub("", text)
        if _ZERO_WIDTH.search(text) or _HTML_COMMENT.search(clean):
            return True
        return any(rx.search(clean) for _n, rx in _INJECTION_PATTERNS)
