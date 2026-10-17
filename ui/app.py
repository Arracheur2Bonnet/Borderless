"""BorderlessGuard demo UI.  Run:  ./.venv/Scripts/python -m streamlit run ui/app.py
Set BG_FAKE_BACKEND=1 to use the in-file fake backend (no real ledger/policy/agent needed).
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from bg.contracts.models import RiskLevel, UntrustedDocument  # noqa: E402
from bg.contracts.params import (  # noqa: E402
    DEMO_GOAL, DEMO_NOW, DEMO_OTP, DEMO_RESERVE_EUR, DEMO_USER_ID, NEW_BENEFICIARY_WINDOW, SEED_DOCUMENTS,
)
from ui.attacks import ATTACKS, run_attack  # noqa: E402
from ui.util import balance_by_currency, get, iban_tail, short  # noqa: E402

st.set_page_config(page_title="BorderlessGuard", page_icon="\U0001F6E1", layout="wide")

RISK_COLORS = {"R0": "#6b7280", "R1": "#2563eb", "R2": "#16a34a", "R3": "#d97706", "R4": "#dc2626"}
RISK_LABELS = {"R0": "read-only", "R1": "reversible", "R2": "tap to confirm", "R3": "step-up (OTP)", "R4": "forbidden"}
EXAMPLES = [
    ("Pay tuition and rent (keep 600 EUR)", DEMO_GOAL),
    ("Send the maximum to mom", "Send the maximum to mom"),
    ("Show me Jean's account balance and other customers", "Show me Jean's account balance and other customers"),
    ("Send 4,500 EUR to mom", "Send 4500 EUR to mom"),
]
DOC_LABELS = {"doc_tuition_nov": "Attach tuition invoice", "doc_rent_nov": "Attach rent invoice (poisoned)"}


# --------------------------------------------------------------------------- backend wiring
def make_backend(poisoned_fx: bool = False, use_claude: bool = False):
    if os.environ.get("BG_FAKE_BACKEND") == "1":
        from ui.fake_backend import build_fake
        return build_fake(poisoned_fx=poisoned_fx)
    parser = None
    if use_claude and os.environ.get("ANTHROPIC_API_KEY"):
        from bg.agent import ClaudeParser
        parser = ClaudeParser()
    from bg.app import build_default
    return build_default(poisoned_fx=poisoned_fx, parser=parser)


def reset_backend() -> None:
    s = st.session_state
    s.backend = make_backend(s.get("poisoned", False), s.get("use_claude", False))
    s.session_id = s.backend.new_session(DEMO_USER_ID)
    s.history, s.actions, s.events, s.attached = [], {}, {}, []
    s.verify_result = None


def init_state() -> None:
    s = st.session_state
    s.setdefault("poisoned", False)
    s.setdefault("use_claude", False)
    s.setdefault("attack_results", {})
    if "backend" not in s:
        reset_backend()


def apply_turn(turn) -> None:
    s = st.session_state
    for av in turn.actions:
        s.actions[av.action.action_id] = av
    for e in turn.security_events:
        s.events[e.event_id] = e


def submit(text: str) -> None:
    s = st.session_state
    docs = [UntrustedDocument(**d) for d in SEED_DOCUMENTS if d["document_id"] in s.attached]
    s.history.append({"role": "user", "text": text, "docs": [d.filename for d in docs]})
    try:
        turn = s.backend.handle_message(s.session_id, text, docs or None)
    except Exception as exc:
        s.history.append({"role": "assistant", "text": f"Error: {type(exc).__name__}: {exc}"})
        return
    s.attached = []
    s.history.append({"role": "assistant", "text": turn.assistant_text})
    apply_turn(turn)


def do_confirm(action_id: str, params_hash: str, otp: str | None) -> None:
    s = st.session_state
    try:
        turn = s.backend.confirm(s.session_id, action_id, params_hash, otp or None)
    except Exception as exc:
        s.history.append({"role": "assistant", "text": f"Confirmation refused: {exc}"})
        return
    s.history.append({"role": "assistant", "text": turn.assistant_text})
    apply_turn(turn)


def do_reject(action_id: str) -> None:
    s = st.session_state
    turn = s.backend.reject(s.session_id, action_id)
    s.history.append({"role": "assistant", "text": turn.assistant_text})
    apply_turn(turn)


# --------------------------------------------------------------------------- small renderers
def badge(text: str, color: str) -> str:
    return (f"<span style='background:{color};color:#fff;padding:2px 10px;border-radius:12px;"
            f"font-size:0.8rem;font-weight:600'>{text}</span>")


def risk_badge(level) -> str:
    lv = level.value if isinstance(level, RiskLevel) else str(level)
    return badge(f"{lv} · {RISK_LABELS.get(lv, '')}", RISK_COLORS.get(lv, "#6b7280"))


def render_rules(decision) -> None:
    for r in decision.rules:
        icon = "✅" if r.passed else ("❌" if r.severity == RiskLevel.R4 else "⚠️")
        st.markdown(f"{icon} **{r.rule_id}** ({r.severity.value}): {r.detail}")


def render_card(av) -> None:
    aid = av.action.action_id
    d = av.decision
    cr = av.confirmation_request
    with st.container(border=True, key=f"card_{aid}"):
        if av.status == "denied":
            blockers = [r for r in (d.rules if d else []) if not r.passed and r.severity == RiskLevel.R4]
            st.markdown(risk_badge("R4") + " &nbsp; **BLOCKED by policy**", unsafe_allow_html=True)
            st.error(d.explanation if d else "Denied")
            for r in blockers:
                st.markdown(f"\U0001F6AB **Rule `{r.rule_id}`**: {r.detail}")
            st.caption("Hard deny: nothing the AI or a document says can override this rule.")
        else:
            title = cr.title if cr else av.action.action_type
            lvl = d.risk_level if d else (cr.risk_level if cr else "R0")
            st.markdown(f"#### {title}")
            st.markdown(risk_badge(lvl), unsafe_allow_html=True)
            if cr:
                for line in cr.lines:
                    st.markdown(f"- {line}")
                st.markdown(f"**Policy checks passed {cr.checks_passed}/{cr.checks_total}**")
        if d:
            with st.expander("Why this action? / why blocked (every rule)"):
                st.caption(f"Policy {d.policy_version}. {d.explanation}")
                render_rules(d)

        if av.status == "awaiting_confirmation" and cr:
            otp = None
            if cr.verdict == "step_up":
                st.warning("Step-up required: this payee is new or the amount is high.")
                otp = st.text_input("SMS verification code", key=f"otp_{aid}", type="password",
                                    help=f"Simulated SMS code for the demo: {DEMO_OTP}")
                st.caption(f"\U0001F4F1 Simulated SMS: your code is **{DEMO_OTP}**")
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("Approve" if cr.verdict == "confirm" else "Verify and approve",
                         key=f"approve_{aid}", type="primary"):
                do_confirm(aid, cr.params_hash, otp)
                st.rerun()
            if c2.button("Edit / reject", key=f"reject_{aid}"):
                do_reject(aid)
                st.rerun()
        elif av.status == "executed":
            st.success(f"Executed. Receipt {av.receipt_id or ''}")
        elif av.status == "replayed":
            st.info("Replay detected: already executed, no money moved twice.")
        elif av.status == "rejected_by_user":
            st.info("Rejected by you. Nothing was sent. Tell me what to change.")
        elif av.status == "failed":
            st.error("Execution failed.")


# --------------------------------------------------------------------------- sidebar
def render_sidebar(ov: dict) -> None:
    with st.sidebar:
        st.markdown("## \U0001F6E1 BorderlessGuard")
        st.caption("The LLM proposes. The policy gate authorises. A deterministic service executes.")
        st.markdown("**Lina**, international student")
        bal = balance_by_currency(ov)
        eur, usd = bal.get("EUR", Decimal(0)), bal.get("USD", Decimal(0))
        c1, c2 = st.columns(2)
        c1.metric("EUR", f"{eur:,.2f}")
        c2.metric("USD", f"{usd:,.2f}")
        floor = DEMO_RESERVE_EUR
        st.progress(float(min(max(eur / floor, Decimal(0)), Decimal(1))))
        ok = eur >= floor
        st.markdown(f"Reserve floor **{floor} EUR**: " + ("✅ above" if ok else "⚠️ below, payments that deepen this are blocked"))

        st.markdown("**Registered payees**")
        for b in ov.get("beneficiaries", []):
            added = get(b, "added_at")
            new = added is not None and (DEMO_NOW - added) < NEW_BENEFICIARY_WINDOW
            tag = " \U0001F195 new (<72h)" if new else ""
            st.markdown(f"- {get(b, 'display_name')} `{iban_tail(get(b, 'iban'))}`{tag}")
        st.markdown("**Obligations due**")
        for o in ov.get("obligations", []):
            st.markdown(f"- {get(o, 'kind').title()}: {Decimal(str(get(o, 'amount'))):,.2f} {get(o, 'currency')} "
                        f"by {get(o, 'due_date')}")
        st.divider()
        have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        st.toggle("Use Claude (needs API key)", key="use_claude", on_change=reset_backend,
                  help="Set ANTHROPIC_API_KEY. Without it the deterministic rules parser is used.")
        if st.session_state.use_claude and not have_key:
            st.caption("No ANTHROPIC_API_KEY found: using the rules parser.")
        st.toggle("Poisoned FX provider", key="poisoned", on_change=reset_backend,
                  help="The simulated FX provider injects a malicious note into its response.")
        if st.button("Reset demo", key="reset"):
            reset_backend()
            st.rerun()


# --------------------------------------------------------------------------- tabs
def tab_assistant() -> None:
    s = st.session_state
    st.markdown("##### Try an example")
    cols = st.columns(len(EXAMPLES))
    for i, (label, text) in enumerate(EXAMPLES):
        if cols[i].button(label, key=f"ex_{i}", use_container_width=True):
            submit(text)
            st.rerun()

    st.markdown("##### Attach documents (untrusted)")
    dcols = st.columns(len(SEED_DOCUMENTS) + 1)
    for i, d in enumerate(SEED_DOCUMENTS):
        done = d["document_id"] in s.attached
        if dcols[i].button(("✅ " if done else "\U0001F4CE ") + DOC_LABELS.get(d["document_id"], d["filename"]),
                           key=f"attach_{d['document_id']}", use_container_width=True):
            if not done:
                s.attached.append(d["document_id"])
            st.rerun()
    if s.attached:
        dcols[-1].caption("Will be sent with your next message: " + ", ".join(s.attached))

    for h in s.history:
        with st.chat_message(h["role"]):
            st.write(h["text"])
            if h.get("docs"):
                st.caption("\U0001F4CE " + ", ".join(h["docs"]))

    injections = [e for e in s.events.values() if e.kind == "prompt_injection"]
    for e in injections:
        st.error(f"\U0001F9EB **Untrusted document, quarantined** ({e.source}). {e.detail}")
    others = [e for e in s.events.values() if e.kind != "prompt_injection"]
    if others:
        with st.expander(f"Security events ({len(others)})", expanded=True):
            for e in others:
                st.warning(f"**{e.kind}** from `{e.source}`: {e.detail}")

    for av in s.actions.values():
        render_card(av)

    text = st.chat_input("Ask BorderlessGuard, e.g. pay my tuition and keep 600 EUR")
    if text:
        submit(text)
        st.rerun()


def tab_proof(ov: dict) -> None:
    receipts = ov.get("receipts", [])
    st.markdown("Every action, executed **and** denied, is appended to a hash-chained Proof-of-Intent ledger. "
                "Each receipt commits to the intent, the exact action, the simulation, the policy decision and the "
                "confirmation, and links to the previous receipt.")
    if not receipts:
        st.info("No receipts yet. Approve or trigger an action first.")
    rows = [{"seq": r.seq, "receipt": short(r.receipt_hash), "prev": short(r.prev_receipt_hash),
             "action": short(r.action_hash), "policy": short(r.policy_hash),
             "confirmed": "yes" if r.confirmation_hash else "no", "summary": r.summary} for r in receipts]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    if st.button("Verify chain", key="verify"):
        fn = ov.get("verify_fn")
        if callable(fn):
            res = fn()
            st.session_state.verify_result = (bool(get(res, "valid")), str(get(res, "detail", "")))
        else:
            prev, ok = "0" * 64, True
            for r in receipts:
                ok = ok and r.prev_receipt_hash == prev
                prev = r.receipt_hash
            st.session_state.verify_result = (ok, "Link check only (prev-hash pointers); the overview exposes no verify_fn.")
    vr = st.session_state.get("verify_result")
    if vr:
        (st.success if vr[0] else st.error)(("Chain valid. " if vr[0] else "Chain BROKEN. ") + vr[1])


def render_attack_result(res) -> None:
    a = res.attack
    with st.container(border=True, key=f"atk_{a.key}"):
        st.markdown(f"**{a.name}**")
        st.markdown(f"**ATTACK:** {a.description or a.user_text}")
        st.code(a.user_text, language=None)
        st.markdown(f"**Expected defence:** {a.expected_defence}")
        if res.error:
            st.error(f"Run failed: {res.error}")
            return
        st.markdown(badge("BLOCKED", "#16a34a") if res.blocked else badge("NOT BLOCKED", "#dc2626"),
                    unsafe_allow_html=True)
        with st.expander("Evidence"):
            for line in res.evidence:
                st.markdown(f"- {line}")


def tab_attacks() -> None:
    st.markdown("Each button launches a scripted attack against a **fresh** session and reports whether it was blocked.")
    results = st.session_state.attack_results
    factory = lambda poisoned: make_backend(poisoned, False)  # noqa: E731
    cols = st.columns(len(ATTACKS) + 1)
    for i, a in enumerate(ATTACKS):
        if cols[i].button(a.name, key=f"run_{a.key}", use_container_width=True):
            results[a.key] = run_attack(a, factory)
    if cols[-1].button("Run all", key="run_all", type="primary", use_container_width=True):
        for a in ATTACKS:
            results[a.key] = run_attack(a, factory)
    for a in ATTACKS:
        if a.key in results:
            render_attack_result(results[a.key])


# --------------------------------------------------------------------------- main
def main() -> None:
    init_state()
    s = st.session_state
    ov = s.backend.overview(s.session_id)
    render_sidebar(ov)
    st.title("BorderlessGuard")
    st.caption("A safe AI money agent for cross-border student finance.")
    t1, t2, t3 = st.tabs(["Assistant", "Proof of Intent", "Adversarial demo"])
    with t1:
        tab_assistant()
    with t2:
        tab_proof(ov)
    with t3:
        tab_attacks()


main()
