"""BorderlessGuard demo UI.  Run:  ./.venv/Scripts/python -m streamlit run ui/app.py
Set BG_FAKE_BACKEND=1 to use the in-file fake backend (no real ledger/policy/agent needed).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from bg.contracts.models import RiskLevel, UntrustedDocument  # noqa: E402
from bg.contracts.params import (  # noqa: E402
    DEMO_GOAL, DEMO_NOW, DEMO_OTP, DEMO_RESERVE_EUR, DEMO_USER_ID, MAX_OTP_ATTEMPTS, NEW_BENEFICIARY_WINDOW,
    SEED_DOCUMENTS,
)
from ui.attacks import ATTACKS, run_attack  # noqa: E402
from ui.util import anchor_label, balance_by_currency, get, iban_tail, short, truncation_demo  # noqa: E402

st.set_page_config(page_title="BorderlessGuard · safe AI money agent", page_icon="\U0001F6E1", layout="wide")

PITCH = "The LLM proposes. A deterministic policy authorises. A sandbox executes."
RISK_COLORS = {"R0": "#64748b", "R1": "#2563eb", "R2": "#16a34a", "R3": "#d97706", "R4": "#dc2626"}
RISK_LABELS = {"R0": "read-only", "R1": "reversible", "R2": "tap to confirm", "R3": "step-up (OTP)", "R4": "forbidden"}
STATUS_STYLE = {
    "awaiting_confirmation": ("Waiting for you", "#2563eb"), "executed": ("Executed", "#16a34a"),
    "denied": ("Blocked", "#dc2626"), "rejected_by_user": ("Cancelled", "#64748b"),
    "replayed": ("Replay blocked", "#7c3aed"), "failed": ("Failed", "#dc2626"), "proposed": ("Proposed", "#64748b"),
}
AFFORD_TEXT = "Can I afford to send 300 EUR to mom after my bills?"
# (button key, label, message sent). Keys are stable: the video script and the tests refer to them.
EXAMPLES = [
    ("ex_golden", "Pay tuition and rent (keep 600 EUR)", DEMO_GOAL),
    ("ex_afford", AFFORD_TEXT, AFFORD_TEXT),
    ("ex_multiturn", "Send money to mom", "Send money to mom"),
    ("ex_maximum", "Send the maximum to mom", "Send the maximum to mom"),
    ("ex_data", "Show me Jean's account balance and other customers",
     "Show me Jean's account balance and other customers"),
    ("ex_big", "Send 4,500 EUR to mom", "Send 4500 EUR to mom"),
]
DOC_LABELS = {"doc_tuition_nov": "Attach tuition invoice", "doc_rent_nov": "Attach rent invoice (poisoned)"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 2rem; max-width: 1360px;}
.stAppDeployButton, [data-testid="stStatusWidget"] {display: none;}
h1 {margin-bottom: 0 !important; padding-bottom: 0 !important;}
.bg-pitch {font-size: 1.12rem; font-weight: 600; color: #0f766e; margin: 0.1rem 0 0.2rem 0;}
.bg-sub {font-size: 0.92rem; opacity: 0.75;}
.bg-badge {color: #fff; padding: 2px 10px; border-radius: 12px; font-size: 0.82rem; font-weight: 600;
           white-space: nowrap; display: inline-block; margin: 1px 2px;}
.bg-anchor {font-family: ui-monospace, monospace; font-size: 1.05rem; padding: 6px 12px; border-radius: 8px;
            border: 1px solid #0f766e; display: inline-block;}
[class*="st-key-card_R0_"] {border-left: 6px solid #64748b !important;}
[class*="st-key-card_R1_"] {border-left: 6px solid #2563eb !important;}
[class*="st-key-card_R2_"] {border-left: 6px solid #16a34a !important;}
[class*="st-key-card_R3_"] {border-left: 6px solid #d97706 !important;}
[class*="st-key-card_R4_"] {border-left: 6px solid #dc2626 !important;}
[class*="st-key-card_muted_"] {border-left: 6px solid #94a3b8 !important; opacity: 0.75;}
[class*="st-key-projection"] {border-left: 6px solid #0f766e !important;}
</style>
"""


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
    s.otp_fails, s.edit_hint = {}, set()
    s.pending_question = None
    s.customer_anchor = None
    s.verify_result = None
    s.tamper_result = None


def init_state() -> None:
    s = st.session_state
    s.setdefault("poisoned", False)
    s.setdefault("use_claude", False)
    s.setdefault("attack_results", {})
    if "backend" not in s:
        reset_backend()


def record_turn(turn, *, user_text: str | None = None, docs: list[str] | None = None) -> None:
    """Store the agent turn as one chat message. Cards for NEW actions attach under this message;
    actions already shown elsewhere just get their status refreshed in place."""
    s = st.session_state
    new_ids = [av.action.action_id for av in turn.actions if av.action.action_id not in s.actions]
    for av in turn.actions:
        s.actions[av.action.action_id] = av
    for e in turn.security_events:
        s.events[e.event_id] = e
    anchor = get(turn, "anchor")
    if anchor is not None:
        s.customer_anchor = anchor          # the customer-held copy, like a statement number
    s.history.append({"role": "assistant", "text": turn.assistant_text, "actions": new_ids,
                      "events": list(turn.security_events), "projection": get(turn, "projection"),
                      "question": get(turn, "pending_question"), "anchor": anchor})


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
    s.pending_question = get(turn, "pending_question")
    record_turn(turn)


def do_confirm(action_id: str, params_hash: str, otp: str | None) -> None:
    s = st.session_state
    try:
        turn = s.backend.confirm(s.session_id, action_id, params_hash, otp or None)
    except Exception as exc:
        s.history.append({"role": "assistant", "text": f"Confirmation refused: {exc}"})
        return
    if any(e.kind == "step_up_failed" for e in turn.security_events):
        s.otp_fails[action_id] = s.otp_fails.get(action_id, 0) + 1
    record_turn(turn)


def do_reject(action_id: str) -> None:
    s = st.session_state
    record_turn(s.backend.reject(s.session_id, action_id))


# --------------------------------------------------------------------------- small renderers
def md(text: str) -> str:
    # escape $ (LaTeX) and keep the agent's line breaks: markdown would merge single newlines
    return (text or "").replace("$", "\\$").replace("\n", "  \n")


def badge(text: str, color: str) -> str:
    return f"<span class='bg-badge' style='background:{color}'>{text}</span>"


def risk_value(level) -> str:
    return level.value if isinstance(level, RiskLevel) else str(level)


def risk_badge(level) -> str:
    lv = risk_value(level)
    return badge(f"{lv} · {RISK_LABELS.get(lv, '')}", RISK_COLORS.get(lv, "#64748b"))


def status_badge(status: str) -> str:
    label, color = STATUS_STYLE.get(status, (status, "#64748b"))
    return badge(label, color)


def render_rules(decision) -> None:
    for r in decision.rules:
        icon = "✅" if r.passed else ("❌" if r.severity == RiskLevel.R4 else "⚠️")
        st.markdown(f"{icon} **{r.rule_id}** ({r.severity.value}): {md(r.detail)}")


def last_pending_id() -> str | None:
    """The action a dialogue correction ('actually make it 40', 'cancel that') applies to."""
    pend = [aid for aid, v in st.session_state.actions.items() if v.status == "awaiting_confirmation"]
    return pend[-1] if pend else None


def render_card(av) -> None:
    s = st.session_state
    aid = av.action.action_id
    d, cr = av.decision, av.confirmation_request
    locked = av.status == "denied" and s.otp_fails.get(aid, 0) >= MAX_OTP_ATTEMPTS
    lvl = "R4" if av.status == "denied" else risk_value(d.risk_level if d else (cr.risk_level if cr else "R0"))
    tone = "muted" if av.status == "rejected_by_user" else lvl
    with st.container(border=True, key=f"card_{tone}_{aid}"):
        if av.status == "denied":
            label = "LOCKED: too many wrong codes" if locked else "BLOCKED by policy"
            st.markdown(risk_badge("R4") + f" &nbsp; **{label}**", unsafe_allow_html=True)
            if locked:
                st.error(f"\U0001F512 Locked after {MAX_OTP_ATTEMPTS} wrong verification codes. Nothing was sent; "
                         f"the attempt is recorded in receipt {av.receipt_id or ''}.")
            else:
                st.error(md(d.explanation if d else "Denied"))
                for r in [r for r in (d.rules if d else []) if not r.passed and r.severity == RiskLevel.R4]:
                    st.markdown(f"\U0001F6AB **Rule `{r.rule_id}`**: {md(r.detail)}")
                st.caption("Hard deny: nothing the AI or a document says can override this rule.")
        else:
            title = cr.title if cr else av.action.action_type
            st.markdown(f"**{md(title)}**")
            st.markdown(risk_badge(lvl) + " " + status_badge(av.status), unsafe_allow_html=True)
            if cr and av.status == "awaiting_confirmation":
                st.markdown("\n".join(f"- {md(line)}" for line in cr.lines))
                st.markdown(f"**Policy checks passed {cr.checks_passed}/{cr.checks_total}**")
        if d:
            with st.expander("Why this action? (every policy rule)"):
                st.caption(f"Policy {d.policy_version}. {md(d.explanation)}")
                render_rules(d)

        if av.status == "awaiting_confirmation" and cr:
            render_pending_controls(aid, av, cr)
        elif av.status == "executed":
            st.success(f"Executed. Receipt {av.receipt_id or ''}")
        elif av.status == "replayed":
            st.info("Replay detected: already executed, no money moved twice.")
        elif av.status == "rejected_by_user":
            st.caption("Cancelled by you. Nothing was sent.")
        elif av.status == "failed":
            st.error("Execution failed.")


def render_pending_controls(aid: str, av, cr) -> None:
    s = st.session_state
    otp = None
    if cr.verdict == "step_up":
        fails = s.otp_fails.get(aid, 0)
        left = max(MAX_OTP_ATTEMPTS - fails, 0)
        st.warning("Step-up required: enter the one-time code to approve this action.")
        o1, o2 = st.columns([2, 3])
        otp = o1.text_input("SMS verification code", key=f"otp_{aid}", type="password",
                            help=f"Simulated SMS code for the demo: {DEMO_OTP}")
        o2.markdown(f"\U0001F4F1 Simulated SMS: your code is **{DEMO_OTP}**  \n"
                    f"Attempts remaining: **{left} of {MAX_OTP_ATTEMPTS}**")
        if fails:
            st.error(f"Wrong code. {left} attempt(s) remaining before this action is locked.")
    is_last = aid == last_pending_id()
    if is_last:
        c1, c2, c3, c4 = st.columns([1.3, 1.1, 0.7, 0.7], vertical_alignment="bottom")
    else:
        c1, c4 = st.columns([1.3, 2.5], vertical_alignment="bottom")
    if c1.button("Approve" if cr.verdict == "confirm" else "Verify and approve", key=f"approve_{aid}",
                 type="primary", width="stretch"):
        do_confirm(aid, cr.params_hash, otp)
        st.rerun()
    if is_last:
        if av.action.action_type == "payment":
            amt = c2.text_input("New amount", key=f"edit_amt_{aid}", placeholder="new amount, e.g. 40")
            if c3.button("Edit", key=f"edit_{aid}", width="stretch"):
                if amt and amt.strip():
                    s.edit_hint.discard(aid)
                    submit(f"actually make it {amt.strip()}")
                else:
                    s.edit_hint.add(aid)
                st.rerun()
        if c4.button("Cancel", key=f"cancel_{aid}", width="stretch"):
            submit("cancel that")
            st.rerun()
        if aid in s.edit_hint:
            st.caption("Type the new amount first, then press Edit.")
        st.caption("Edit and Cancel are sent to the agent as messages (\"actually make it …\", \"cancel that\"): "
                   "a correction creates a new action that is simulated and checked again.")
    elif c4.button("Reject", key=f"reject_{aid}"):
        do_reject(aid)
        st.rerun()


def render_projection(p, key: str) -> None:
    with st.container(border=True, key=f"projection_{key}"):
        st.markdown("**Projection** · simulated, nothing proposed or executed")
        floor = get(p, "reserve_floor_eur")
        tags = [badge("Reserve OK", "#16a34a") if get(p, "reserve_ok") else badge("Reserve breached", "#dc2626")]
        if not get(p, "feasible"):
            tags.append(badge("Not feasible", "#dc2626"))
        st.markdown(" ".join(tags) + (f" &nbsp; reserve floor **{Decimal(str(floor)):,.2f} EUR**" if floor is not None
                                      else ""), unsafe_allow_html=True)
        now, after = get(p, "balances_now", {}), get(p, "balances_after", {})
        curs = list(dict.fromkeys([*now, *after]))
        cols = st.columns(len(curs) or 1)
        for col, cur in zip(cols, curs):
            n, a = Decimal(str(now.get(cur, 0))), Decimal(str(after.get(cur, 0)))
            col.metric(f"{cur}: now {n:,.2f} → after", f"{a:,.2f}", delta=f"{a - n:+,.2f}")
        lines = get(p, "lines", [])
        if lines:
            st.markdown("\n".join(f"- {md(x)}" for x in lines))


def render_events(events) -> None:
    for e in events:
        if e.kind == "prompt_injection":
            st.error(f"\U0001F9EB **Untrusted document, quarantined** ({e.source}). {md(e.detail)}")
        else:
            st.warning(f"\U0001F6A8 **{e.kind}** from `{e.source}`: {md(e.detail)}")


# --------------------------------------------------------------------------- sidebar
def render_sidebar(ov: dict) -> None:
    with st.sidebar:
        st.markdown("## \U0001F6E1 BorderlessGuard")
        st.markdown("**Lina**, international student in France")
        bal = balance_by_currency(ov)
        eur, usd = bal.get("EUR", Decimal(0)), bal.get("USD", Decimal(0))
        st.metric("EUR balance", f"{eur:,.2f}")
        st.metric("USD balance", f"{usd:,.2f}")
        floor = DEMO_RESERVE_EUR
        ok = eur >= floor
        st.markdown(f"Reserve floor **{floor:,.0f} EUR**: " + (badge("kept", "#16a34a") if ok else badge("below", "#dc2626")),
                    unsafe_allow_html=True)

        st.markdown("**Registered payees**")
        for b in ov.get("beneficiaries", []):
            added = get(b, "added_at")
            new = added is not None and (DEMO_NOW - added) < NEW_BENEFICIARY_WINDOW
            tag = " " + badge("new payee &lt;72h", "#d97706") if new else ""
            st.markdown(f"- {get(b, 'display_name')} `{iban_tail(get(b, 'iban'))}`{tag}", unsafe_allow_html=True)
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


# --------------------------------------------------------------------------- header
def render_header() -> None:
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        st.title("\U0001F6E1 BorderlessGuard")
        st.markdown(f"<div class='bg-pitch'>{PITCH}</div>"
                    "<div class='bg-sub'>A safe AI money agent for cross-border student finance (sandbox).</div>",
                    unsafe_allow_html=True)
    with right:
        st.markdown("<div style='text-align:right'>" + " ".join(risk_badge(f"R{i}") for i in range(5)) + "</div>",
                    unsafe_allow_html=True)


# --------------------------------------------------------------------------- tabs
def tab_assistant() -> None:
    s = st.session_state
    with st.container(key="examples"):
        rows = [EXAMPLES[:3], EXAMPLES[3:]]
        for row in rows:
            cols = st.columns(len(row))
            for col, (key, label, text) in zip(cols, row):
                if col.button(label, key=key, width="stretch"):
                    submit(text)
                    st.rerun()
        dcols = st.columns([1, 1, 1])
        for i, d in enumerate(SEED_DOCUMENTS):
            done = d["document_id"] in s.attached
            if dcols[i].button(("✅ " if done else "\U0001F4CE ") + DOC_LABELS.get(d["document_id"], d["filename"]),
                               key=f"attach_{d['document_id']}", width="stretch"):
                if not done:
                    s.attached.append(d["document_id"])
                st.rerun()
        if s.attached:
            dcols[-1].caption("Sent with your next message (untrusted): " + ", ".join(s.attached))

    last_assistant = max((i for i, h in enumerate(s.history) if h["role"] == "assistant"), default=-1)
    for i, h in enumerate(s.history):
        with st.chat_message(h["role"]):
            st.markdown(md(h["text"]))
            if h.get("docs"):
                st.caption("\U0001F4CE " + ", ".join(h["docs"]))
            render_events(h.get("events", []))
            if h.get("projection") is not None:
                render_projection(h["projection"], str(i))
            q = h.get("question")
            if q and i == last_assistant and s.pending_question:
                if q not in h["text"]:
                    st.markdown(f"**{md(q)}**")
                st.caption("↳ Just type your answer below, for example \"50 euros\".")
            for aid in h.get("actions", []):
                if aid in s.actions:
                    render_card(s.actions[aid])
            if h.get("anchor") is not None:
                st.caption(f"\U0001F9FE Receipt anchor {anchor_label(h['anchor'])} (your copy)")

    hint = ("Answer the agent's question, e.g. 50 euros" if s.pending_question
            else "Ask BorderlessGuard, e.g. pay my tuition and keep 600 EUR")
    text = st.chat_input(hint, key="chat")
    if text:
        submit(text)
        st.rerun()


def call_verify(fn, anchor):
    try:
        return fn(anchor)
    except TypeError:
        if anchor is None:
            return fn()
        raise


def tab_proof(ov: dict) -> None:
    s = st.session_state
    receipts = ov.get("receipts", [])
    st.markdown("Every action, executed **and** denied, is appended to a hash-chained Proof-of-Intent ledger. "
                "Each receipt commits to the intent, the exact action, the simulation, the policy decision and the "
                "confirmation, and links to the previous receipt.")
    anchor = s.customer_anchor
    if anchor is not None:
        st.markdown(f"Your receipt anchor: <span class='bg-anchor'>{anchor_label(anchor)}</span>",
                    unsafe_allow_html=True)
        st.caption("Your customer copy of the chain head, signed by the bank and kept on your side (like a statement "
                   "number). It lets you detect a truncated or rewritten chain, which the chain alone cannot.")
    else:
        st.caption("No receipt anchor yet: you get one after the first receipt is written.")
    if not receipts:
        st.info("No receipts yet. Approve or trigger an action first.")
    rows = [{"seq": get(r, "seq"), "receipt": short(get(r, "receipt_hash")), "prev": short(get(r, "prev_receipt_hash")),
             "action": short(get(r, "action_hash")), "policy": short(get(r, "policy_hash")),
             "confirmed": "yes" if get(r, "confirmation_hash") else "no", "summary": get(r, "summary")}
            for r in receipts]
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    fn = ov.get("verify_fn")
    c1, c2, _ = st.columns([1, 1.4, 2])
    if c1.button("Verify chain", key="verify", width="stretch"):
        if callable(fn):
            res = call_verify(fn, None)
            s.verify_result = (bool(get(res, "valid")), "Chain valid. " if get(res, "valid") else "Chain BROKEN. ",
                               str(get(res, "detail", "")))
        else:
            from ui.util import links_ok
            ok = links_ok(receipts)
            s.verify_result = (ok, "Chain valid. " if ok else "Chain BROKEN. ",
                               "Link check only (prev-hash pointers); the overview exposes no verify_fn.")
    if c2.button("Verify chain against my copy", key="verify_anchor", width="stretch",
                 disabled=anchor is None or not callable(fn)):
        res = call_verify(fn, anchor)
        ok = bool(get(res, "valid"))
        s.verify_result = (ok, f"Matches your anchor {anchor_label(anchor)}. " if ok
                           else f"Does NOT match your anchor {anchor_label(anchor)}. ", str(get(res, "detail", "")))
    vr = s.get("verify_result")
    if vr:
        (st.success if vr[0] else st.error)(vr[1] + md(vr[2]))

    with st.expander("Tamper test: why the customer anchor matters (runs on a copy, the database is not touched)"):
        st.markdown(
            "An insider who **deletes the most recent receipts** leaves a chain whose links are still perfectly "
            "consistent, so a server-side check alone says *valid*. Your anchor names receipt "
            "`#seq` and its hash: if that receipt is gone or rewritten, the check against your copy fails.\n\n"
            "The button below shows this on an **in-memory copy** of the receipt list above: it drops your anchored "
            "receipt and everything after it, then runs both checks on the copy. Nothing in the real ledger changes.")
        if st.button("Run tamper test on a copy", key="tamper_test", disabled=anchor is None or not receipts):
            s.tamper_result = truncation_demo(receipts, anchor)
        tr = s.get("tamper_result")
        if tr:
            st.markdown(f"Copy truncated from receipt #{tr['deleted_from_seq']}: "
                        f"{tr['original_length']} → {tr['truncated_length']} receipts.")
            (st.warning if tr["chain_only_valid"] else st.success)(
                "Chain-only check on the copy: " + ("links still valid, the deletion is invisible."
                                                    if tr["chain_only_valid"] else "broken."))
            (st.error if tr["anchor_valid"] else st.success)(
                "Check against your anchor: " + ("still matches (unexpected)." if tr["anchor_valid"]
                                                 else f"receipt #{tr['deleted_from_seq']} is missing: TAMPERING DETECTED."))
            st.caption("Illustration on a copy. The real check is “Verify chain against my copy”, which the ledger "
                       "runs on its own database.")


def render_attack_result(res) -> None:
    a = res.attack
    with st.container(border=True, key=f"atk_{a.key}"):
        head = badge("BLOCKED", "#16a34a") if (res.blocked and not res.error) else badge("NOT BLOCKED", "#dc2626")
        st.markdown(f"{head} &nbsp; **{md(a.name)}**", unsafe_allow_html=True)
        st.markdown(f"**Attack:** {md(a.description or a.user_text)}")
        st.code(a.script, language=None)
        st.markdown(f"**Expected defence:** {md(a.expected_defence)}")
        if res.error:
            st.error(f"Run failed (counted as NOT blocked): {res.error}")
        with st.expander("Evidence"):
            for line in res.evidence:
                st.markdown(f"- {md(line)}")


def tab_attacks() -> None:
    st.markdown("Each button launches a scripted attack against a **fresh** session and reports whether it was "
                "blocked. A crash counts as NOT blocked.")
    results = st.session_state.attack_results
    factory = lambda poisoned: make_backend(poisoned, False)  # noqa: E731
    if st.button(f"Run all {len(ATTACKS)} attacks", key="run_all", type="primary"):
        for a in ATTACKS:
            results[a.key] = run_attack(a, factory)
    cols = st.columns(2)
    for i, a in enumerate(ATTACKS):
        if cols[i % 2].button(a.name, key=f"run_{a.key}", width="stretch"):
            results[a.key] = run_attack(a, factory)
    done = [results[a.key] for a in ATTACKS if a.key in results]
    if done:
        n = sum(r.blocked and not r.error for r in done)
        st.markdown(f"### {n} / {len(done)} blocked")
    for a in ATTACKS:
        if a.key in results:
            render_attack_result(results[a.key])


# --------------------------------------------------------------------------- main
def main() -> None:
    init_state()
    s = st.session_state
    st.markdown(CSS, unsafe_allow_html=True)
    ov = s.backend.overview(s.session_id)
    render_sidebar(ov)
    render_header()
    t1, t2, t3 = st.tabs(["Assistant", "Proof of Intent", "Adversarial demo"])
    with t1:
        tab_assistant()
    with t2:
        tab_proof(ov)
    with t3:
        tab_attacks()


main()
