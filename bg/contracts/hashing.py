"""Canonical hashing shared by every component. FROZEN.

Same object -> same hash, on every machine. Used for params_hash (binds a
confirmation to exact parameters) and for every field of the Proof-of-Intent receipt.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

GENESIS_HASH = "0" * 64


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, Decimal):
        # 850 and 850.00 must hash the same
        return format(value.normalize(), "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def params_hash(action: BaseModel) -> str:
    """Hash of an action's parameters, excluding nothing: any change invalidates approval."""
    return sha256_hex(action)
