"""Reproducible execution evidence: runs the golden path end to end and prints the proof.

    ./.venv/Scripts/python scripts/golden_path.py          (Windows)
    python scripts/golden_path.py                          (elsewhere)

Offline, no API key, deterministic (fixed clock, content-derived ids). Exits non-zero if any
expectation fails, so it doubles as a smoke test.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from bg.app import build_default  # noqa: E402
from bg.contracts import params  # noqa: E402
from bg.contracts.models import UntrustedDocument  # noqa: E402


def main() -> int:
    orch = build_default()
    sid = orch.new_session(params.DEMO_USER_ID)
    docs = [UntrustedDocument(**d) for d in params.SEED_DOCUMENTS]

    print(f"USER> {params.DEMO_GOAL}\n")
    turn = orch.handle_message(sid, params.DEMO_GOAL, docs)
    print(turn.assistant_text, "\n")
    print("security events:", [e.kind for e in turn.security_events], "\n")

    for view in turn.actions:
        cr = view.confirmation_request
        assert cr is not None, f"{view.action.action_type} not awaiting confirmation"
        otp = params.DEMO_OTP if cr.verdict == "step_up" else None
        label = "step-up (OTP)" if otp else "tap"
        result = orch.confirm(sid, view.action.action_id, cr.params_hash, otp)
        final = next(v for v in result.actions if v.action.action_id == view.action.action_id)
        print(f"CONFIRM [{label}] {cr.title} -> {final.status} (risk {cr.risk_level.value})")

    ov = orch.overview(sid)
    balances = {a["currency"]: a["balance"] for a in ov["accounts"] if a["user_id"] == params.DEMO_USER_ID}
    print("\nfinal balances:", {k: str(v) for k, v in balances.items()})

    print("\nProof-of-Intent chain:")
    prev = "0" * 64
    for r in ov["receipts"]:
        linked = r["prev_receipt_hash"] == prev
        print(f"  #{r['seq']} {r['summary']}\n      receipt {r['receipt_hash'][:16]}... prev {r['prev_receipt_hash'][:16]}... linked={linked}")
        prev = r["receipt_hash"]

    ok = (balances["EUR"] == Decimal("600.00") and balances["USD"] == Decimal("196.74")
          and len(ov["receipts"]) == 3)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
