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
