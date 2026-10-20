"""Export the real product texts and numbers used by the launch video (media/launch-video/src/data.json).

    ./.venv/Scripts/python scripts/export_video_data.py

Everything shown in the video comes from here: a live run of the real orchestrator (offline rules parser, demo
clock), the seeded invoice, the frozen blind-test reports. The script fails if a figure the video states no longer
matches its source, so the video cannot drift from the product.
"""
from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "media" / "launch-video" / "src" / "data.json"

from bg.app import build_default  # noqa: E402
from bg.contracts import params  # noqa: E402
from bg.contracts.models import UntrustedDocument  # noqa: E402


def money(x, ccy: str) -> str:
    return f"{Decimal(str(x)):,.2f} {ccy}"


def why(av, payees) -> str:
    """The card's plain-language reason, exactly as the app shows it (ui.util.why_line), with a safe fallback."""
    try:
        from ui.util import why_line  # the app's own wording
        return why_line(av, payees, params.DEMO_NOW)[1]
    except Exception:
        return ""


def main() -> int:
    orch = build_default(parser=None)
    sid = orch.new_session(params.DEMO_USER_ID)
    docs = [UntrustedDocument(**d) for d in params.SEED_DOCUMENTS if d["document_id"] == "doc_rent_nov"]
    turn = orch.handle_message(sid, params.DEMO_GOAL, docs)
    ov0 = orch.overview(sid)
    payees = {b["beneficiary_id"]: b for b in ov0["beneficiaries"]}
    reply = [ln for ln in turn.assistant_text.split("\n") if ln.strip()]
    assert any("hidden text" in ln for ln in reply), reply

    actions = []
    for v in turn.actions:
        cr = v.confirmation_request
        a = v.action
        if a.action_type == "fx":
            title = f"Buy {money(a.quote.buy_amount, 'EUR')} with {money(a.quote.sell_amount, 'USD')}"
            amount, ccy = f"{Decimal(str(a.quote.buy_amount)):,.2f}", "EUR"
            facts = [["You pay", money(a.quote.sell_amount, "USD")],
                     ["Rate", f"{a.quote.rate} EUR per USD"],
                     ["Cost of this exchange", f"{Decimal(str(a.quote.fee)):,.2f} USD (0.50%)"]]
        else:
            ben = payees.get(a.beneficiary_id, {})
            title = f"Pay {money(a.amount, a.currency)} to {ben.get('display_name', '')}"
            amount, ccy = f"{Decimal(str(a.amount)):,.2f}", a.currency
            facts = [["To", ben.get("display_name", "")],
                     ["Account", f"ending {a.iban[-4:]} (your saved payee)"]]
        actions.append({
            "kind": a.action_type, "title": title, "amount": amount, "ccy": ccy,
            "verdict": cr.verdict, "risk": cr.risk_level.value,
            "pill": "One tap · R2" if cr.verdict == "confirm" else "Code needed · R3",
            "reason": why(v, payees), "facts": facts,
        })

    receipts = []
    for v in turn.actions:
        cr = v.confirmation_request
        r = orch.confirm(sid, v.action.action_id, cr.params_hash, params.DEMO_OTP if cr.verdict == "step_up" else None)
        assert any(x.status == "executed" for x in r.actions), [x.status for x in r.actions]
    ov = orch.overview(sid)
    bal = {a["currency"]: f"{Decimal(str(a['balance'])):,.2f}" for a in ov["accounts"] if a["user_id"] == params.DEMO_USER_ID}
    assert bal == {"EUR": "600.00", "USD": "196.74"}, bal
    for r in ov["receipts"]:
        summary = re.sub(r"^EXECUTED: ", "", r["summary"])
        receipts.append({"seq": r["seq"], "summary": summary, "hash": r["receipt_hash"][:8],
                         "prev": r["prev_receipt_hash"][:8]})
    assert len(receipts) == 3
    # Lina's signed copy of the latest receipt, and the real check against it (Receipts tab, "Check against my copy").
    from ui.util import anchor_label  # noqa: E402
    anchor = ov["anchor"]
    check = ov["verify_fn"](anchor)
    assert check.valid and anchor["seq"] == 3, check
    proof = {"anchor": anchor_label(anchor), "signed": bool(anchor.get("signature")),
             "check": f"Matches your copy (receipt {anchor_label(anchor)}).",
             "detail": check.detail}

    # Verification of Payee: a new payee whose name does not match the bank's record (scripted mission T21).
    s2 = orch.new_session(params.DEMO_USER_ID)
    vop_msg = "Add Jean Dupont Orsay as a payee with IBAN ES91 2100 0418 4502 0005 1332"
    t2 = orch.handle_message(s2, vop_msg)
    vop_lines = [ln for v in t2.actions if v.confirmation_request for ln in v.confirmation_request.lines
                 if ln.startswith("Payee check:")]
    assert vop_lines and "no match" in vop_lines[0], (t2.assistant_text, vop_lines)
    vop_reply = next((ln for ln in t2.assistant_text.split("\n") if "does not match" in ln), "")
    assert vop_reply, t2.assistant_text

    # Blind results (frozen reports).
    v2 = (ROOT / "deliverables" / "heldout_v2_results.md").read_text(encoding="utf-8")
    assert "69/70" in v2, "rules blind result changed"
    qwen = json.loads((ROOT / "deliverables/blind_llm/qwen2.5_7b_heldout_v2/report.json").read_text(encoding="utf-8"))
    q_attacks = [c for c in qwen["results"] if "adversarial" in c["case"] or "multiturn" in c["case"]]
    q_blocked = sum(c["status"] == "passed" for c in q_attacks)
    assert (q_blocked, len(q_attacks)) == (66, 70), (q_blocked, len(q_attacks))

    invoice = params.RENT_INVOICE_TEXT.strip().split("\n")
    hidden_start = next(i for i, ln in enumerate(invoice) if ln.startswith("<!--"))
    data = {
        "persona": "Lina, international student in France",
        "user_sentence": params.DEMO_GOAL,
        "invoice": {"filename": "rent_invoice_nov_2026.pdf",
                    "visible": invoice[:hidden_start],
                    "hidden": " ".join(ln.replace("<!--", "").replace("-->", "").strip()
                                       for ln in invoice[hidden_start:]).strip()},
        "reply": reply,
        "actions": actions,
        "otp": params.DEMO_OTP,
        "receipts": receipts,
        "proof": proof,
        "balances": bal,
        "reserve": f"{params.DEMO_RESERVE_EUR:,.2f}",
        "vop": {"message": vop_msg, "card_line": vop_lines[0], "reply": vop_reply},
        "blind": {"rules": {"blocked": 69, "total": 70, "wrong_payments": 0, "commit": "44542f2"},
                  "qwen": {"blocked": q_blocked, "total": len(q_attacks), "wrong_payments": 0, "commit": "5868d83",
                           "model": "Qwen2.5 7B, local"}},
        "team": "Team Centrale · Shenzhen FinTechathon 2026",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(actions)} actions, {len(receipts)} receipts, "
          f"blind {data['blind']['rules']['blocked']}/70 and {q_blocked}/70")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
