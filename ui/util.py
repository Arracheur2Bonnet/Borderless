"""Small helpers shared by the UI modules (no streamlit import, so tests can use them)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


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
