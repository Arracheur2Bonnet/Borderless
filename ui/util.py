"""Small helpers shared by the UI modules (no streamlit import, so tests can use them)."""
from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bg.contracts.params import (
    DAILY_OUTFLOW_LIMIT_EUR, DEMO_RESERVE_EUR, MAX_OTP_ATTEMPTS, MAX_SINGLE_PAYMENT_EUR, R2_MAX_FX_EUR,
    R2_MAX_PAYMENT_EUR, STRUCTURING_THRESHOLD_EUR,
)


def get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a pydantic model or a dict (overview() may return either)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def balances(overview: dict[str, Any]) -> dict[str, Decimal]:
    """account_id -> balance."""
    return {get(a, "account_id"): Decimal(str(get(a, "balance"))) for a in overview.get("accounts", [])}


def balance_by_currency(overview: dict[str, Any]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for a in overview.get("accounts", []):
        out[str(get(a, "currency"))] = Decimal(str(get(a, "balance")))
    return out


def iban_tail(iban: str, n: int = 4) -> str:
    return "…" + iban[-n:] if iban else ""


def short(h: str | None, n: int = 8) -> str:
    return (h or "")[:n]


# --------------------------------------------------------------------------- escaping (untrusted text)
# Anything that can come from outside the trust boundary (user messages echoed by the agent, invoice text,
# document filenames, payee names typed by the user, security-event details) goes through one of these.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~$])")
_MD_BULLET = re.compile(r"^(\s*)([-+=])", re.M)
_MD_ORDERED = re.compile(r"^(\s*\d+)([.)])", re.M)


def esc_md(text: Any) -> str:
    """Make arbitrary text render literally inside st.markdown: no HTML, no links/images, no emphasis,
    no LaTeX, no Streamlit colour directives. Single newlines are kept as line breaks."""
    s = _MD_SPECIAL.sub(r"\\\1", "" if text is None else str(text))
    # "- x" / "1. x" at a line start would become a list: escape the marker ("\-", "1\.")
    s = _MD_BULLET.sub(lambda m: m.group(1) + "\\" + m.group(2), s)
    s = _MD_ORDERED.sub(lambda m: m.group(1) + "\\" + m.group(2), s)
    return s.replace("\n", "  \n")


def esc_html(text: Any) -> str:
    """For the few places that build HTML (unsafe_allow_html=True): escape every interpolated value."""
    return html.escape("" if text is None else str(text), quote=True)


def unescape_md(text: str) -> str:
    """Inverse of esc_md for tests and plain-text comparisons."""
    return re.sub(r"\\(.)", r"\1", text.replace("  \n", "\n"))


# --------------------------------------------------------------------------- numbers and ids
def to_dec(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def money(amount: Any, currency: str = "EUR") -> str:
    """'1,200.00 EUR': thousands separator, 2 decimals, ISO currency after the number."""
    return f"{to_dec(amount):,.2f} {currency}".strip()


_BIG_NUMBER = re.compile(r"(?<![\w.,])(\d{4,})\.(\d{2})(?!\d)")
_RECEIPT_ID = re.compile(r"\brcpt_0*(\d+)\b")
_QUOTE_ID = re.compile(r"\s*\(quote [\w-]+\)")


def pretty_numbers(text: str) -> str:
    """'2200.00 EUR' -> '2,200.00 EUR'. Only amounts with 2 decimals (never IBAN tails, dates or ids)."""
    return _BIG_NUMBER.sub(lambda m: f"{int(m.group(1)):,}.{m.group(2)}", text or "")


def humanize(text: str) -> str:
    """Display form of backend text: formatted amounts, receipt ids as '#n', quote ids dropped
    (internal ids stay available under 'Technical details')."""
    return _QUOTE_ID.sub("", _RECEIPT_ID.sub(r"#\1", pretty_numbers(text or "")))


def receipt_label(receipt_id: str | None) -> str:
    if not receipt_id:
        return ""
    m = _RECEIPT_ID.search(receipt_id)
    return f"#{m.group(1)}" if m else "#" + receipt_id.rsplit("_", 1)[-1]


def anchor_label(anchor: Any) -> str:
    """'#3 · dc29...b22 · signed' (customer-facing short form of a ReceiptAnchor)."""
    if anchor is None:
        return ""
    h = str(get(anchor, "receipt_hash", ""))
    signed = "signed" if get(anchor, "signature") else "unsigned"
    return f"#{get(anchor, 'seq')} · {h[:4]}...{h[-3:]} · {signed}"


def links_ok(receipts: list[Any]) -> bool:
    """Prev-hash pointer check only (what a server-side chain check sees)."""
    prev = "0" * 64
    for r in receipts:
        if get(r, "prev_receipt_hash") != prev:
            return False
        prev = get(r, "receipt_hash")
    return True


def truncation_demo(receipts: list[Any], anchor: Any) -> dict[str, Any] | None:
    """Explain the anchor check on an IN-MEMORY COPY: drop the anchored receipt and everything after it,
    as an insider deleting recent rows would. Never touches the real store."""
    if anchor is None or not receipts:
        return None
    seq = get(anchor, "seq")
    copy = [r for r in receipts if get(r, "seq") < seq]
    found = any(get(r, "seq") == seq and get(r, "receipt_hash") == get(anchor, "receipt_hash") for r in copy)
    return {"original_length": len(receipts), "truncated_length": len(copy), "deleted_from_seq": seq,
            "chain_only_valid": links_ok(copy), "anchor_valid": found}


# --------------------------------------------------------------------------- plain-language "why"
def _eur(x: Decimal) -> str:
    return f"{x:,.0f} EUR"


R4_PLAIN = {
    "MAX_SINGLE_PAYMENT": f"above the {_eur(MAX_SINGLE_PAYMENT_EUR)} per-payment cap",
    "DAILY_LIMIT": f"it would exceed your {_eur(DAILY_OUTFLOW_LIMIT_EUR)} daily limit",
    "SIM_FEASIBLE": "there is not enough money in the account",
    "RESERVE_FLOOR": f"it would take you below your {_eur(DEMO_RESERVE_EUR)} reserve",
    "SANCTIONS_SCREEN": "the payee is on the sanctions watchlist",
    "IBAN_MATCHES_REGISTRY": "the bank details do not match the registered payee",
    "BENEFICIARY_REGISTERED": "the payee is not in your saved payees",
    "BENEFICIARY_NOT_BLOCKED": "this payee is blocked",
    "TENANT_ISOLATION": "it touches an account that is not yours",
    "UNTRUSTED_ORIGIN": "it was requested by an untrusted document, not by you",
    "FX_PRICE_CHECK": "the exchange price does not match the bank's own quote",
    "IBAN_VALID": "the account number is not a valid IBAN",
    "PAYEE_NAME_UNIQUE": "you already have a payee with this name",
}


def _payee_age_hours(action: Any, payees: dict[str, Any], now: datetime | None) -> int | None:
    b = payees.get(get(action, "beneficiary_id") or "")
    added = get(b, "added_at")
    if added is None or now is None:
        return None
    return int((now - added).total_seconds() // 3600)


def why_line(av: Any, payees: dict[str, Any] | None = None, now: datetime | None = None,
             locked: bool = False) -> tuple[str, str]:
    """(icon, one plain-language sentence) explaining the risk level of an action card.

    Derived only from the policy decision the backend returned (failed rules), never invented."""
    payees = payees or {}
    act, d = get(av, "action"), get(av, "decision")
    status = get(av, "status")
    atype = get(act, "action_type")
    failed = [r for r in (get(d, "rules") or []) if not get(r, "passed")]
    sev = lambda r: getattr(get(r, "severity"), "value", get(r, "severity"))  # noqa: E731
    r4 = [r for r in failed if sev(r) == "R4"]
    r3 = [r for r in failed if sev(r) == "R3"]
    if locked:
        return "🔒", f"Locked after {MAX_OTP_ATTEMPTS} wrong codes: nothing was sent."
    if status == "denied" and r4:
        reasons = [R4_PLAIN.get(get(r, "rule_id"), get(r, "rule_id")) for r in r4]
        return "⛔", "Blocked: " + "; ".join(dict.fromkeys(reasons)) + ". No code or message can unlock this."
    if status == "denied":
        return "⛔", "Blocked by policy. Nothing was sent."
    if r3:
        rid = get(r3[0], "rule_id")
        if rid == "BENEFICIARY_AGE":
            h = _payee_age_hours(act, payees, now)
            when = f"{h} hours ago" if h is not None else "recently"
            return "🔑", f"New payee, added {when}: we ask for a one-time code."
        if rid == "AMOUNT_TIER":
            return "🔑", f"Above the {_eur(R2_MAX_PAYMENT_EUR)} one-tap limit: we ask for a one-time code."
        if rid == "FX_TIER":
            return "🔑", f"Large exchange above {_eur(R2_MAX_FX_EUR)}: we ask for a one-time code."
        if rid == "STRUCTURING":
            return "🔑", (f"Payments to the same person add up to over {_eur(STRUCTURING_THRESHOLD_EUR)} in 24 h "
                          "(possible split payment): we ask for a one-time code.")
        if rid == "NEW_PAYEE_ACTION":
            return "🔑", "Adding a new payee always needs a one-time code."
        return "🔑", "Higher risk: we ask for a one-time code."
    if atype == "fx":
        return "✅", f"Currency exchange under {_eur(R2_MAX_FX_EUR)} at the quoted rate: one tap is enough."
    if atype == "payment":
        h = _payee_age_hours(act, payees, now)
        known = f"Saved payee for {h // 24} days" if h is not None and h >= 72 else "Saved payee"
        return "✅", f"{known}, amount under the {_eur(R2_MAX_PAYMENT_EUR)} one-tap limit: one tap is enough."
    return "✅", "Low risk: one tap is enough."


# --------------------------------------------------------------------------- quick replies
def quick_replies(entry: dict[str, Any], *, pending_question: str | None, pending_count: int,
                  payees: list[Any], tuition_and_rent_text: str) -> list[tuple[str, str]]:
    """Chips offered under the LAST agent message: (label, message sent to the agent).

    Chips never carry an amount the user did not type: amounts are always typed. Every chip is a normal
    chat message, so it goes through the same parser, simulation and policy gate."""
    chips: list[tuple[str, str]] = []
    q = (pending_question or "").lower()
    proj = entry.get("projection")
    if pending_question:
        if "who" in q or "receive" in q or "which payee" in q:
            for b in payees:
                aliases = get(b, "aliases") or []
                ref = aliases[0] if aliases else get(b, "display_name")
                chips.append((f"To {get(b, 'display_name')}", f"to {ref}"))
        chips.append(("Cancel", "cancel"))
    elif proj is not None:
        if get(proj, "reserve_ok"):
            chips.append(("Go ahead: pay tuition and rent", tuition_and_rent_text))
        else:
            chips.append(("Pay tuition and rent only (keep my reserve)", tuition_and_rent_text))
            chips.append(("Pay tuition only", "Pay my tuition"))
    elif entry.get("refused_data"):
        chips.append(("Show my own balances", "Show my overview"))
    elif pending_count >= 2:
        chips.append(("Cancel everything", "cancel everything"))
    elif pending_count == 1:
        chips.append(("Cancel that", "cancel that"))
    return chips
