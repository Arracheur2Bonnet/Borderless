"""BorderlessGuard demo UI.  Run:  ./.venv/Scripts/python -m streamlit run ui/app.py
Set BG_FAKE_BACKEND=1 to use the in-file fake backend (no real ledger/policy/agent needed).
Open with ?guided=1 to start in guided demo mode.

Security note: every string that can come from outside the trust boundary (agent replies that echo the user,
invoice text, file names, payee names, event details) is rendered through util.esc_md (markdown) or
util.esc_html (the few HTML snippets). Invoice text itself is only ever shown in st.code.
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

from bg.agent.rules_parser import RulesParser  # noqa: E402
from bg.contracts.models import RiskLevel, UntrustedDocument  # noqa: E402
from bg.contracts.params import (  # noqa: E402
    DEMO_GOAL, DEMO_NOW, DEMO_OTP, DEMO_RESERVE_EUR, DEMO_USER_ID, MAX_OTP_ATTEMPTS, NEW_BENEFICIARY_WINDOW,
    SEED_DOCUMENTS,
)
from ui import guided  # noqa: E402
from ui.attacks import ATTACKS, run_attack  # noqa: E402
from ui.theme import ICON, MARK_SMALL, RISK, STATUS, kv_html, load_css, pill  # noqa: E402
from ui.util import (  # noqa: E402
    anchor_label, balance_by_currency, esc_html, esc_md, extra_lines, get, humanize, money, quick_replies,
    receipt_label, short, to_dec, truncation_demo, why_line,
)

st.set_page_config(page_title="BorderlessGuard (sandbox)", page_icon=str(MARK_SMALL) if MARK_SMALL.exists() else None,
                   layout="wide")

# Notices carry an icon as well as words: meaning is never colour alone.
IC_OK, IC_ERR, IC_WARN, IC_INFO = (":material/check_circle:", ":material/error:", ":material/warning:", ":material/info:")

# Said once, in the header. The video closes on a different line.
PITCH = "The AI reads your request. Fixed rules and your approval move the money."
EVENT_PLAIN = {
    "beneficiary_mismatch": ("", "Bank details did not match",
                             "The account number in the message differs from the saved payee. The saved account is used."),
    "tool_output_poisoning": ("", "Exchange-rate feed tried to give orders",
                              "Its extra text was dropped. Only the price was used, and you still approve every step."),
    "replay_blocked": ("", "Duplicate approval ignored", "This was already done. No money moved twice."),
    "data_access_denied": ("", "Not your data", "I can only show your own accounts. The request was logged."),
    "param_tamper": ("", "Approval did not match", "The details changed after you reviewed them, so it was refused."),
    "jailbreak_attempt": ("", "Attempt to override the rules", "Ignored. The rules are enforced by code, not by the AI."),
    "step_up_failed": ("", "Wrong code", "The attempt was counted."),
    "structuring_suspected": ("", "Possible split payment",
                              "Several payments to the same person add up over the one-tap limit, so a code is needed."),
    "sanctions_hit": ("", "Sanctions watchlist match", "This payee cannot be added or paid."),
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
    ("ex_help", "What can you do?", "What can you do?"),
]
DOC_LABELS = {"doc_tuition_nov": "Attach tuition invoice", "doc_rent_nov": "Attach rent invoice (forged)"}
MAX_UPLOAD_CHARS = 20_000

# --------------------------------------------------------------------------- backend wiring
# The "Language model" selector. Every option goes through bg.agent.make_parser(kind, ...); the parser only ever
# PROPOSES typed intents, so switching it changes who understands the sentence, never what is allowed.
PARSER_KINDS = {
    "rules": "Rules only (instant)",
    "cascade": "Smart cascade: rules, then local Qwen",
    "local": "Local model (Ollama)",
    "connected": "Connect a model (OpenAI-compatible)",
}
LOCAL_MODELS = ("qwen2.5:7b-instruct", "qwen2.5:3b-instruct")
MAX_CHAT_CHARS = 2000                    # AUDIT2-11: also enforced by the agent; stated in the input placeholder
FALLBACK_NOTICE = "The selected model did not answer, so the built-in rules handled this message."

try:                                    # the factory lives in the backend; the UI still starts without it
    from bg.agent import make_parser
except ImportError:                     # pragma: no cover - depends on the backend build
    make_parser = None


def _audit_sink(rec: dict) -> None:
    """Remember which parser/tier answered the latest message (shown in Technical details)."""
    if rec.get("step") == "parse" and rec.get("parser"):
        try:
            st.session_state["last_parser"] = str(rec["parser"])
        except Exception:               # no Streamlit session (e.g. a script run outside the app)
            pass


def build_parser(kind: str = "rules", *, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None):
    """(parser, notice). "rules" is always an explicit RulesParser: BG_PARSER cannot turn "Rules only" into a remote
    model behind the label (APPSEC-2)."""
    if kind == "rules":
        return RulesParser(), ""
    if kind == "connected" and not (base_url and model):
        return None, "Enter the base URL and model name, then press Connect. Until then the rules are used."
    if make_parser is None:
        return None, "This build has no model backend: the built-in rules are used."
    try:
        parser = make_parser(kind, model=model or None, base_url=base_url or None, api_key=api_key or None)
    except Exception:                   # never echo the error text: it may hold the key
        parser = None
    if parser is None:                  # the backend refuses what it cannot use safely (e.g. a remote http URL)
        return None, ("The model could not be set up (a remote endpoint must use https), "
                      "so the built-in rules are used.")
    return parser, ""


def make_backend(poisoned_fx: bool = False, parser=None, *, sink=None):
    """Build the orchestrator around `parser` (None = the default rules parser)."""
    if os.environ.get("BG_FAKE_BACKEND") == "1":
        from ui.fake_backend import build_fake
        return build_fake(poisoned_fx=poisoned_fx)
    from bg.app import build_default
    if parser is None:                  # never the BG_PARSER fallback: the UI shows which parser reads the text
        parser = RulesParser()
    return build_default(poisoned_fx=poisoned_fx, parser=parser, audit_sink=sink)


def reset_backend() -> None:
    s = st.session_state
    kind = s.get("parser_kind", "rules")
    if kind not in PARSER_KINDS:
        kind = s.parser_kind = "rules"
    model = s.get("local_model") if kind == "local" else s.get("conn_model") if kind == "connected" else None
    base_url = s.get("conn_base_url") if kind == "connected" else None
    api_key = s.get("conn_api_key") if kind == "connected" else None      # only ever lives in session_state
    parser, s.setup_notice = build_parser(kind, model=model, base_url=base_url, api_key=api_key)
    s.backend = make_backend(s.get("poisoned", False), parser, sink=_audit_sink)
    s.session_id = s.backend.new_session(DEMO_USER_ID)
    s.history, s.actions, s.events, s.attached = [], {}, {}, []
    s.otp_fails, s.edit_hint = {}, set()
    s.pending_question = None
    s.customer_anchor = None
    s.verify_result = None
    s.tamper_result = None
    s.titles = {}
    s.parsers = {}
    s.last_parser = ""
    reset_guided_progress()


def reset_guided_progress() -> None:
    s = st.session_state
    s.g_done, s.g_obs, s.g_plan_idx, s.g_target = 0, {}, None, None


def start_guided() -> None:
    """Toggle callback: a guided run always starts from a fresh sandbox, honest FX provider."""
    s = st.session_state
    if not s.get("guided"):
        return                              # leaving guided mode keeps the current session as it is
    s.poisoned = False
    s.attack_results = {}
    reset_backend()


def init_state() -> None:
    s = st.session_state
    s.setdefault("poisoned", False)
    if "parser_kind" not in s:          # BG_PARSER=local keeps meaning "start on the local model"
        env_kind = os.environ.get("BG_PARSER", "").strip().lower()
        s.parser_kind = env_kind if env_kind in ("cascade", "local") else "rules"
    s.setdefault("local_model", os.environ.get("BG_LOCAL_MODEL", LOCAL_MODELS[0])
                 if os.environ.get("BG_LOCAL_MODEL", LOCAL_MODELS[0]) in LOCAL_MODELS else LOCAL_MODELS[0])
    # the "Connect a model" form starts with the operator's endpoint and model name (no secret): the environment key
    # is then used only if the Base URL stays the one in BG_MODEL_BASE_URL (APPSEC-1)
    s.setdefault("conn_base_url", os.environ.get("BG_MODEL_BASE_URL", ""))
    s.setdefault("conn_model", os.environ.get("BG_MODEL_NAME", ""))
    s.setdefault("setup_notice", "")
    s.setdefault("last_parser", "")
    s.setdefault("parsers", {})
    s.setdefault("attack_results", {})
    s.setdefault("custom_docs", [])
    s.setdefault("upload_gen", 0)
    if "guided" not in s:
        s.guided = st.query_params.get("guided") in ("1", "true", "yes")
    if "backend" not in s:
        reset_backend()


def all_docs() -> list[dict]:
    return [*SEED_DOCUMENTS, *st.session_state.custom_docs]


def doc_names() -> dict[str, str]:
    return {d["document_id"]: d["filename"] for d in all_docs()}


def record_turn(turn) -> None:
    """Store the agent turn as one chat message. Cards for NEW actions attach under this message;
    actions already shown elsewhere just get their status refreshed in place."""
    s = st.session_state
    new_ids = [av.action.action_id for av in turn.actions if av.action.action_id not in s.actions]
    for av in turn.actions:
        s.actions[av.action.action_id] = av
        cr = av.confirmation_request
        if cr is not None and av.action.action_id not in s.titles:
            s.parsers.setdefault(av.action.action_id, s.get("last_parser") or "rules")
            s.titles[av.action.action_id] = cr.title     # kept for the technical details after execution
    for e in turn.security_events:
        s.events[e.event_id] = e
    anchor = get(turn, "anchor")
    if anchor is not None:
        s.customer_anchor = anchor          # the customer-held copy, like a statement number
    s.history.append({"role": "assistant", "text": turn.assistant_text, "actions": new_ids,
                      "events": list(turn.security_events), "projection": get(turn, "projection"),
                      "question": get(turn, "pending_question"), "anchor": anchor,
                      "refused_data": any(e.kind == "data_access_denied" for e in turn.security_events)})


def submit(text: str) -> None:
    s = st.session_state
    docs = [UntrustedDocument(**d) for d in all_docs() if d["document_id"] in s.attached]
    s.history.append({"role": "user", "text": text, "docs": [d.document_id for d in docs]})
    try:
        with st.spinner("Simulating and checking the rules…"):
            turn = s.backend.handle_message(s.session_id, text, docs or None)
    except Exception as exc:
        s.history.append({"role": "assistant", "text": f"Something went wrong ({type(exc).__name__}). Nothing was sent."})
        return
    s.attached = []
    s.pending_question = get(turn, "pending_question")
    record_turn(turn)


def do_confirm(action_id: str, params_hash: str, otp: str | None) -> None:
    s = st.session_state
    try:
        with st.spinner("Checking the rules again and executing in the sandbox…"):
            turn = s.backend.confirm(s.session_id, action_id, params_hash, otp or None)
    except Exception as exc:
        s.history.append({"role": "assistant", "text": f"Approval refused ({type(exc).__name__}). Nothing was sent."})
        return
    if any(e.kind == "step_up_failed" for e in turn.security_events):
        s.otp_fails[action_id] = s.otp_fails.get(action_id, 0) + 1
    record_turn(turn)


def do_reject(action_id: str) -> None:
    s = st.session_state
    record_turn(s.backend.reject(s.session_id, action_id))


# --------------------------------------------------------------------------- small renderers
def risk_value(level) -> str:
    return level.value if isinstance(level, RiskLevel) else str(level)


def risk_badge(level) -> str:
    lv = risk_value(level)
    icon, label, tone = RISK.get(lv, ("eye", lv, "neutral"))
    return pill(icon, f"{label} · {lv}", tone)


def status_badge(status: str) -> str:
    icon, label, tone = STATUS.get(status, ("clock", status, "neutral"))
    return pill(icon, label, tone)


def payees_by_id(ov: dict) -> dict:
    return {get(b, "beneficiary_id"): b for b in ov.get("beneficiaries", [])}


def action_title(av, payees: dict) -> str:
    """Card title built from the action itself (formatted numbers, survives after execution)."""
    a = av.action
    if a.action_type == "payment":
        b = payees.get(a.beneficiary_id)
        name = get(b, "display_name", a.beneficiary_id)
        cat = get(b, "category")
        return f"Pay {money(a.amount, a.currency)} to {name}" + (f" · {cat}" if cat and cat != "other" else "")
    if a.action_type == "fx":
        q = a.quote
        return f"Buy {money(q.buy_amount, q.buy_currency)} with {money(q.sell_amount, q.sell_currency)}"
    if a.action_type == "add_beneficiary":
        return f"Add payee {a.display_name}"
    return humanize(st.session_state.titles.get(a.action_id, a.action_type))


def action_details(av, payees: dict) -> list[tuple[str, str]]:
    a, sim = av.action, av.simulation
    rows: list[tuple[str, str]] = []
    if a.action_type == "payment":
        b = payees.get(a.beneficiary_id)
        rows += [("To", get(b, "display_name", "unknown payee")),
                 ("Account", f"ending {a.iban[-4:]} (your saved payee)"),
                 ("Amount", money(a.amount, a.currency))]
        if sim and a.from_account_id in sim.balances_after:
            rows.append(("EUR after", f"{money(sim.balances_before.get(a.from_account_id, 0))} → "
                                      f"{money(sim.balances_after[a.from_account_id])}"))
    elif a.action_type == "fx":
        q = a.quote
        net = to_dec(q.sell_amount) - to_dec(q.fee)
        pct = f" ({to_dec(q.fee) / net * 100:.2f}%)" if net > 0 else ""
        rows += [("You pay", f"{money(q.sell_amount, q.sell_currency)} (cost included)"),
                 ("You get", money(q.buy_amount, q.buy_currency)),
                 ("Rate", f"{to_dec(q.rate):.4f}".rstrip("0").rstrip(".") + f" {q.buy_currency} per {q.sell_currency}"),
                 ("Cost of this exchange", f"{money(q.fee, q.sell_currency)}{pct}")]
        if sim and a.to_account_id in sim.balances_after:
            rows.append(("EUR after", f"{money(sim.balances_before.get(a.to_account_id, 0))} → "
                                      f"{money(sim.balances_after[a.to_account_id])}"))
    elif a.action_type == "add_beneficiary":
        rows += [("Name", a.display_name), ("Account", f"ending {a.iban[-4:]}"), ("Type", a.category)]
    return rows


def render_rules(decision) -> None:
    """Every rule checked, as a ruled list: status (icon and word), rule id, plain detail. All text escaped."""
    items = []
    for r in decision.rules:
        mark = (pill("check", "Passed", "ok", "sm") if r.passed else
                pill("block", "Failed", "bad", "sm") if r.severity == RiskLevel.R4 else
                pill("key", "Needs a code", "warn", "sm"))
        items.append(f"<li>{mark}<span><code>{esc_html(str(r.rule_id).replace('`', ''))}</code> ({esc_html(r.severity.value)})</span>"
                     f"<span class='d'>{esc_html(humanize(r.detail))}</span></li>")
    st.markdown(f"<ul class='bg-rules'>{''.join(items)}</ul>", unsafe_allow_html=True)


def render_technical(av) -> None:
    a = av.action
    with st.expander("Technical details"):
        rows = [("Action id", a.action_id), ("Idempotency key", a.idempotency_key)]
        if av.confirmation_request:
            rows.append(("Approval bound to (params hash)", av.confirmation_request.params_hash))
        if a.action_type == "fx":
            rows.append(("FX quote", a.quote.quote_id))
        if get(a, "iban"):
            rows.append(("IBAN", a.iban))
        if av.receipt_id:
            rows.append(("Receipt", av.receipt_id))
        if av.decision:
            rows += [("Policy version", av.decision.policy_version),
                     ("Policy explanation", av.decision.explanation)]
        if a.action_id in st.session_state.get("parsers", {}):
            rows.append(("Parser that answered", st.session_state.parsers[a.action_id]))
        if a.action_id in st.session_state.titles:
            rows.append(("Agent's summary", st.session_state.titles[a.action_id]))
        if av.confirmation_request:
            rows += [("Agent's note", line) for line in av.confirmation_request.lines]
        st.markdown("<dl class='bg-tech'>" + "".join(f"<dt>{esc_html(k)}</dt><dd>{esc_html(v)}</dd>" for k, v in rows)
                    + "</dl>", unsafe_allow_html=True)


def last_pending_id() -> str | None:
    """The action a dialogue correction ('actually make it 40', 'cancel that') applies to."""
    pend = [aid for aid, v in st.session_state.actions.items() if v.status == "awaiting_confirmation"]
    return pend[-1] if pend else None


WHY_STYLE = {"✅": ("check", "ok"), "🔑": ("key", "warn"), "⛔": ("block", "bad"), "🔒": ("lock", "bad")}


def amount_hero(av) -> str:
    a = av.action
    if a.action_type == "payment":
        amt, cur = a.amount, a.currency
    elif a.action_type == "fx":
        amt, cur = a.quote.buy_amount, a.quote.buy_currency
    else:
        return ""
    return f"<div class='bg-amount'>{esc_html(f'{to_dec(amt):,.2f}')}<small>{esc_html(cur)}</small></div>"


CARD_PHASE = {"awaiting_confirmation": "pending", "executed": "done", "denied": "blocked", "rejected_by_user": "muted",
              "replayed": "dup", "failed": "failed"}


def render_card(av, payees: dict) -> None:
    """One action. A surface (a card) only while a decision is needed; afterwards it is a flat, ruled record
    (CSS reads the phase and the risk level from the container key)."""
    s = st.session_state
    aid = av.action.action_id
    d, cr = av.decision, av.confirmation_request
    locked = av.status == "denied" and bool(s.otp_fails.get(aid)) and code_budget()[1]
    lvl = "R4" if av.status == "denied" else risk_value(d.risk_level if d else (cr.risk_level if cr else "R0"))
    with st.container(border=False, key=f"card_{CARD_PHASE.get(av.status, 'other')}_{lvl}_{aid}"):
        if s.get("g_target") == ("card", aid):
            st.markdown("<div class='g-target'></div>", unsafe_allow_html=True)
        icon, why = why_line(av, payees, DEMO_NOW, locked=locked)
        why_icon, why_tone = WHY_STYLE.get(icon, ("check", "ok"))
        meta = risk_badge(lvl) + ("" if av.status == "denied" else status_badge(av.status))
        st.markdown(f"<div class='bg-card-meta'>{meta}</div>"
                    f"<div class='bg-card-title'>{esc_html(action_title(av, payees))}</div>{amount_hero(av)}"
                    f"<div class='bg-why {why_tone}'>{ICON[why_icon]}<span>{esc_html(why)}</span></div>",
                    unsafe_allow_html=True)
        if av.status == "denied":
            if locked:
                st.markdown(pill("lock", "LOCKED: too many wrong codes", "bad", "caps"), unsafe_allow_html=True)
                st.error(f"Locked after {MAX_OTP_ATTEMPTS} wrong verification codes. Nothing was sent; "
                         f"the attempt is recorded in receipt {receipt_label(av.receipt_id)}.", icon=IC_ERR)
        else:
            st.markdown(kv_html(action_details(av, payees), extra_lines(av)), unsafe_allow_html=True)
            if d and av.status == "awaiting_confirmation":
                n_ok = sum(r.passed for r in d.rules)
                st.caption(f"Rules passed: {n_ok} of {len(d.rules)}. Simulated first: nothing has moved yet.")
        if d:
            with st.expander("Why this action? (every rule checked)"):
                st.caption(f"Checked by fixed rules, not by the AI. {esc_md(humanize(d.explanation))}")
                render_rules(d)
        render_technical(av)

        if av.status == "awaiting_confirmation" and cr:
            render_pending_controls(aid, av, cr)
        elif av.status == "executed":
            st.success(f"Done in the sandbox. Receipt {receipt_label(av.receipt_id)}", icon=IC_OK)
        elif av.status == "replayed":
            st.info("Already done: duplicate ignored, no money moved twice.", icon=IC_INFO)
        elif av.status == "rejected_by_user":
            st.caption("Cancelled by you. Nothing was sent.")
        elif av.status == "failed":
            st.error("Execution failed. Nothing was sent.", icon=IC_ERR)


def code_budget() -> tuple[int, bool]:
    """(codes left, locked) for the CUSTOMER. The bank counts wrong codes per user, across every session and
    every payment, so re-sending a request never gives a fresh budget."""
    s = st.session_state
    store = getattr(s.backend, "step_up", None)
    if store is not None:
        return store.remaining(DEMO_USER_ID), store.locked(DEMO_USER_ID)
    left = max(MAX_OTP_ATTEMPTS - sum(s.otp_fails.values()), 0)      # backend without a shared store
    return left, left == 0


def render_pending_controls(aid: str, av, cr) -> None:
    s = st.session_state
    otp = None
    code_locked = False
    if cr.verdict == "step_up":
        left, code_locked = code_budget()
        what = "Adding a payee" if av.action.action_type == "add_beneficiary" else "This payment"
        st.warning(f"{what} needs your one-time code.", icon=IC_WARN)
        o1, o2 = st.columns([2, 3])
        otp = o1.text_input("SMS verification code", key=f"otp_{aid}", type="password", disabled=code_locked,
                            help=f"Simulated SMS code for the demo: {DEMO_OTP}")
        o2.markdown(f"Simulated SMS: your code is **{DEMO_OTP}**  \n"
                    f"Attempts remaining: **{left} of {MAX_OTP_ATTEMPTS}** (counted for your account, not per payment)")
        if code_locked:
            st.error(f"Approval by code is locked on your account after {MAX_OTP_ATTEMPTS} wrong codes. "
                     "The bank has to unlock it (in this demo: Reset demo).", icon=IC_ERR)
        elif s.otp_fails.get(aid):
            st.error(f"Wrong code. {left} attempt(s) left before approval by code is locked on your account.", icon=IC_ERR)
    is_last = aid == last_pending_id()
    if is_last:
        c1, c2, c3, c4 = st.columns([1.3, 1.1, 0.7, 0.7], vertical_alignment="bottom")
    else:
        c1, c4 = st.columns([1.3, 2.5], vertical_alignment="bottom")
    if c1.button("Approve" if cr.verdict == "confirm" else "Verify and approve", key=f"approve_{aid}",
                 type="primary", width="stretch", disabled=code_locked):
        do_confirm(aid, cr.params_hash, otp)
        st.rerun()
    if is_last:
        if av.action.action_type == "payment":
            amt = c2.text_input("New amount", key=f"edit_amt_{aid}", placeholder="e.g. 40")
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
        st.caption("You can also type it: \"actually make it 40\" or \"cancel that\".")
    elif c4.button("Reject", key=f"reject_{aid}"):
        do_reject(aid)
        st.rerun()


def render_projection(p, key: str) -> None:
    with st.container(border=False, key=f"projection_{key}"):
        st.markdown(f"<div class='bg-card-heading'>{ICON['chart']}Projection: simulated, nothing proposed or sent</div>",
                    unsafe_allow_html=True)
        floor = get(p, "reserve_floor_eur")
        tags = [pill("check", "Reserve kept", "ok") if get(p, "reserve_ok") else pill("alert", "Reserve breached", "bad")]
        if not get(p, "feasible"):
            tags.append(pill("block", "Not enough money", "bad"))
        st.markdown("<div class='bg-proj-meta'>" + "".join(tags)
                    + (f"<span class='bg-sub'>Your reserve: <b>{esc_html(money(floor))}</b></span>" if floor is not None else "")
                    + "</div>", unsafe_allow_html=True)
        now, after = get(p, "balances_now", {}), get(p, "balances_after", {})
        curs = list(dict.fromkeys([*now, *after]))
        cols = st.columns(len(curs) or 1, gap="large")
        for col, cur in zip(cols, curs):
            n, a = to_dec(now.get(cur, 0)), to_dec(after.get(cur, 0))
            col.metric(f"{cur}: now {n:,.2f} → after", f"{a:,.2f}", delta=f"{a - n:+,.2f}", delta_color="off")
        lines = get(p, "lines", [])
        if lines:
            st.markdown("<ul class='bg-lines'>" + "".join(f"<li>{esc_html(humanize(x))}</li>" for x in lines) + "</ul>",
                        unsafe_allow_html=True)


def render_events(events, names: dict[str, str]) -> None:
    for e in events:
        if e.kind == "prompt_injection":
            fname = names.get(e.source, "the attached document")
            st.error(f"**Untrusted document, quarantined:** {esc_md(fname)}  \n"
                     "It contains hidden instructions addressed to the AI. They were ignored: the document is read "
                     "as data only, and any payment uses the account saved for the payee.", icon=IC_ERR)
        elif e.kind == "step_up_failed":
            continue                       # shown on the card itself (attempt counter)
        else:
            icon, title, plain = EVENT_PLAIN.get(e.kind, ("", "Security check", ""))
            st.warning(f"**{title}.** {plain}", icon=IC_WARN)


def render_event_details(events, names: dict[str, str]) -> None:
    if not events:
        return
    with st.expander("Technical details (security events)"):
        for e in events:
            src = names.get(e.source, e.source)
            st.markdown(f"- `{e.kind}` from {esc_md(src)}: {esc_md(e.detail)}")


# --------------------------------------------------------------------------- sidebar
def render_model_selector() -> None:
    """One "Language model" selector. Changing it rebuilds the backend (a fresh sandbox), like the old toggles."""
    s = st.session_state
    st.selectbox("Language model", list(PARSER_KINDS), format_func=PARSER_KINDS.__getitem__, key="parser_kind",
                 on_change=reset_backend,
                 help="Who reads your sentence. The model only proposes; fixed rules and your approval still "
                      "decide what moves. Changing it restarts the demo.")
    kind = s.parser_kind
    if kind == "rules":
        st.caption("Offline and instant. The built-in parser handles the demo requests.")
    elif kind == "cascade":
        st.caption("Rules answer first. Only what they cannot read goes to a local Qwen 3B, then Qwen 7B (Ollama). "
                   "Nothing leaves this machine.")
    elif kind == "local":
        st.selectbox("Local model", LOCAL_MODELS, key="local_model", on_change=reset_backend)
        st.caption("Runs on this machine through Ollama (localhost). If it is not running, the rules take over.")
    else:
        with st.form("connect_form", border=False):
            st.text_input("Base URL", key="conn_base_url", placeholder="https://host/v1")
            st.text_input("Model name", key="conn_model", placeholder="model name")
            st.text_input("API key", key="conn_api_key", type="password", autocomplete="off",
                          help="Kept in this browser session only. Never shown, never written to disk or logs.")
            st.form_submit_button("Connect", on_click=reset_backend, width="stretch")
        st.caption("Any OpenAI-compatible server: a frontier model or another local one. "
                   "With a remote endpoint your messages leave this machine.")
    if s.get("setup_notice"):
        st.caption(esc_md(s.setup_notice))
    elif kind in ("local", "connected") and (s.get("last_parser") or "").startswith("rules"):
        st.caption(FALLBACK_NOTICE)


def render_sidebar(ov: dict) -> None:
    s = st.session_state
    with st.sidebar:
        bal = balance_by_currency(ov)
        eur, usd = bal.get("EUR", Decimal(0)), bal.get("USD", Decimal(0))
        floor = DEMO_RESERVE_EUR
        ok = eur >= floor
        st.markdown(
            "<div class='bg-account'><div class='bg-side-label'>Account</div><div class='n'>Lina</div>"
            "<div class='d'>International student in France</div></div>"
            "<div class='bg-side-label'>Balances</div>"
            "<div class='bg-ledger'>"
            f"<div class='bg-row'><span class='k'>EUR balance</span><span class='v'>{eur:,.2f}</span></div>"
            f"<div class='bg-row'><span class='k'>USD balance</span><span class='v'>{usd:,.2f}</span></div>"
            f"<div class='bg-row small'><span class='k'>Reserve to keep</span><span class='v'>{esc_html(money(floor))}</span></div>"
            "<div class='bg-row small'><span class='k'>Reserve status</span><span class='v'>"
            + (pill("check", "kept", "ok", "sm") if ok else pill("alert", "below", "bad", "sm")) + "</span></div></div>",
            unsafe_allow_html=True)
        with st.expander("Saved payees and bills due", expanded=not s.guided):
            rows = []
            for b in ov.get("beneficiaries", []):
                added = get(b, "added_at")
                age = (DEMO_NOW - added) if added is not None else None
                new = age is not None and age < NEW_BENEFICIARY_WINDOW
                tag = pill("clock", f"new · added {int(age.total_seconds() // 3600)} h ago", "warn", "sm") if new else ""
                rows.append(f"<div class='bg-payee'><div><div class='nm'>{esc_html(get(b, 'display_name'))}</div>"
                            f"<div class='meta'>ending <code>{esc_html((get(b, 'iban') or '')[-4:])}</code>{tag}</div></div></div>")
            for o in ov.get("obligations", []):
                due = get(o, "due_date")
                due_s = due.strftime('%d %b %Y') if hasattr(due, 'strftime') else str(due)
                rows.append(f"<div class='bg-payee'><div><div class='nm'>{esc_html(str(get(o, 'kind')).title())}</div>"
                            f"<div class='meta'>due {esc_html(due_s)}</div></div>"
                            f"<div class='amt'>{esc_html(money(get(o, 'amount'), get(o, 'currency')))}</div></div>")
            st.markdown("".join(rows), unsafe_allow_html=True)
        st.markdown("<div class='bg-side-label'>Demo controls</div>", unsafe_allow_html=True)
        st.toggle("Guided demo (6 steps)", key="guided", on_change=start_guided,
                  help="One click per step. Every step runs the real agent, rules and sandbox ledger.")
        render_model_selector()
        st.toggle("Lying exchange-rate feed", key="poisoned", on_change=reset_backend,
                  help="The simulated exchange-rate provider slips instructions into its response.")
        if st.button("Reset demo", key="reset", width="stretch",
                     help="Starts a fresh sandbox: balances, receipts and the code lock. In this demo it also stands in "
                          "for the bank's operator unlock, so the lock is shown here, not protective."):
            reset_backend()
            st.rerun()


# --------------------------------------------------------------------------- header
def render_header() -> None:
    """Mark and wordmark (the mark is drawn by CSS next to the title), the sandbox status, the one-line pitch, the
    risk legend. Said once: the pitch is not repeated anywhere else on the page."""
    left, right = st.columns([3, 1.4], vertical_alignment="center")
    with left:
        st.title("BorderlessGuard")
    with right:
        st.markdown("<div style='text-align:right'>" + pill("flask", "SANDBOX · no real money", "neutral", "sandbox")
                    + "</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='bg-pitch'>{esc_html(PITCH)}</div>"
                "<div class='bg-sub'>Pays an international student's bills across currencies, "
                "and asks before anything moves.</div>"
                "<div class='bg-legend'><span class='bg-legend-label'>Risk levels</span>"
                + "".join(risk_badge(lv) for lv in ("R0", "R2", "R3", "R4")) + "</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- guided demo
def g_last_assistant() -> dict | None:
    return next((h for h in reversed(st.session_state.history) if h["role"] == "assistant"), None)


def g_plan_entry() -> dict | None:
    s = st.session_state
    return s.history[s.g_plan_idx] if s.g_plan_idx is not None and s.g_plan_idx < len(s.history) else None


def payees_now() -> dict:
    s = st.session_state
    return payees_by_id(s.backend.overview(s.session_id))


def run_guided_step(step: guided.Step) -> guided.Observation:
    """Drive the REAL backend for one step, then read back what it actually did."""
    s = st.session_state
    if step.key == "ask":
        submit(AFFORD_TEXT)
        s.g_target = ("msg", len(s.history) - 1)
        return guided.check_ask(g_last_assistant())
    if step.key == "plan":
        s.attached = ["doc_rent_nov"]
        submit(DEMO_GOAL)
        s.g_plan_idx = len(s.history) - 1
        s.g_target = ("msg", s.g_plan_idx)
        return guided.check_plan(g_plan_entry(), s.actions)
    if step.key == "tap":
        views = guided.taps_in_plan(g_plan_entry(), s.actions)
        ids = guided.run_ordered(views, lambda v: do_confirm(v.action.action_id, v.confirmation_request.params_hash, None))
        s.g_target = ("card", ids[0]) if ids else None
        return guided.check_ids(ids, s.actions, "approve with a tap", lambda a: action_title(s.actions[a], payees_now()))
    if step.key == "code":
        views = guided.codes_in_plan(g_plan_entry(), s.actions)
        ids = guided.run_ordered(views, lambda v: do_confirm(v.action.action_id, v.confirmation_request.params_hash,
                                                             DEMO_OTP))
        s.g_target = ("card", ids[0]) if ids else None
        return guided.check_ids(ids, s.actions, "approve with a code", lambda a: action_title(s.actions[a], payees_now()))
    if step.key == "proof":
        ov = s.backend.overview(s.session_id)
        fn, anchor = ov.get("verify_fn"), s.customer_anchor
        res = call_verify(fn, anchor) if callable(fn) and anchor is not None else None
        if res is not None:
            set_verify_result(res, anchor)
        s.g_target = None
        return guided.check_proof(res, anchor, len(ov.get("receipts", [])))
    if step.key == "attacks":
        run_attacks(ATTACKS)
        s.g_target = None
        return guided.check_attacks([s.attack_results[a.key] for a in ATTACKS])
    raise ValueError(step.key)


def render_guided() -> None:
    s = st.session_state
    n = len(guided.STEPS)
    with st.container(key="guided_bar"):
        cells = []
        for i, stp in enumerate(guided.STEPS):
            obs = s.g_obs.get(stp.key)
            cls = ("done" if obs.ok else "bad") if obs else ("now" if i == s.g_done else "")
            mark = (ICON["check"] if obs.ok else ICON["x"]) if obs else str(i + 1)
            cur = " aria-current='step'" if cls == "now" else ""
            cells.append(f"<li class='bg-step {cls}'{cur}><span class='bar'></span><span class='t'><span class='n'>{mark}</span>"
                         f"<span>{esc_html(stp.title)}</span></span></li>")
        st.markdown(f"<div class='bg-guided-head'>{ICON['play']}Guided demo</div>"
                    f"<ol class='bg-stepper'>{''.join(cells)}</ol>", unsafe_allow_html=True)
        if s.g_done == 0:
            st.markdown("<div class='bg-caption'>Six clicks tell the whole story. Each step runs the real agent, "
                        "rules and sandbox ledger: nothing is pre-recorded.</div>", unsafe_allow_html=True)
        else:
            stp = guided.STEPS[s.g_done - 1]
            obs = s.g_obs[stp.key]
            st.markdown(f"<div class='bg-caption'>Step {s.g_done}/{n} · {esc_html(stp.caption)}</div>"
                        "<div class='bg-facts'>"
                        + "".join(pill("check" if ok else "x", txt, "ok" if ok else "bad") for ok, txt in obs.facts)
                        + "</div>", unsafe_allow_html=True)
            if not obs.ok:
                st.warning("This step did not go as the story expects. Shown above is what the backend actually "
                           "returned. Restart the guided demo to try again.", icon=IC_WARN)
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        if s.g_done < n:
            nxt = guided.STEPS[s.g_done]
            if c1.button(f"▶ Step {s.g_done + 1}/{n}: {nxt.button}", key="guided_next", type="primary",
                         width="stretch"):
                s.g_obs[nxt.key] = run_guided_step(nxt)
                s.g_done += 1
                st.rerun()
        else:
            c1.success("Guided demo complete. Details: Receipts and Attack test tabs.", icon=IC_OK)
        c2.button("↺ Restart guided demo", key="guided_restart", width="stretch", on_click=start_guided)


def scroll_to_target() -> None:
    """Guided demo only. Measures the sticky bar and tells the scroll container to keep that much clear
    (scroll-padding), so nothing is hidden under the bar when the page scrolls or a control takes focus; then,
    when a step has a result, brings it into view (stage / video). No effect in tests."""
    import streamlit.components.v1 as components
    scroll = bool(st.session_state.get("g_target"))
    nonce = f"{st.session_state.g_done}-{st.session_state.g_target}"      # a new iframe per step, so it runs
    js = ("setTimeout(function(){var d=window.parent.document;var bar=d.querySelector('.st-key-guided_bar');"
          "var sc=bar;while(sc&&!/(auto|scroll)/.test(window.parent.getComputedStyle(sc).overflowY)){sc=sc.parentElement;}"
          "var w=bar?bar.parentElement:null;"       # the sticky wrapper: its top offset + its height = what stays covered
          "var h=w?Math.ceil(parseFloat(window.parent.getComputedStyle(w).top)+w.getBoundingClientRect().height)+16:0;"
          "if(sc){sc.style.scrollPaddingTop=h+'px';}"
          + ("var t=d.querySelector('.g-target');if(t){t.scrollIntoView({behavior:'smooth',block:'start'});}" if scroll else "")
          + "}, 450);")
    components.html("<script>/* " + esc_html(nonce) + " */ " + js + "</script>", height=0)


# --------------------------------------------------------------------------- tabs
def render_examples() -> None:
    """Examples as two ruled lists (a row is one request), then the invoices to attach as quiet outlined buttons."""
    s = st.session_state
    with st.container(key="examples"):
        st.caption("Try an example, or type your own request below.")
        cols = st.columns(2)
        for col, items in zip(cols, (EXAMPLES[:4], EXAMPLES[4:])):
            for key, label, text in items:
                if col.button(label, key=key, width="stretch"):
                    submit(text)
                    st.rerun()
    with st.container(horizontal=True, key="attach"):
        for d in SEED_DOCUMENTS:
            done = d["document_id"] in s.attached
            if st.button(("✓ " if done else "") + DOC_LABELS.get(d["document_id"], d["filename"]),
                         key=f"attach_{d['document_id']}"):
                if not done:
                    s.attached.append(d["document_id"])
                st.rerun()
        with st.popover("Upload your own invoice (.txt)"):
            up = st.file_uploader("Invoice text file. It is treated as untrusted data.", type=["txt"],
                                  key=f"upload_{s.upload_gen}")
            if up is not None:
                raw = up.getvalue()[: MAX_UPLOAD_CHARS * 4]       # at most 4 bytes per kept character (APPSEC-7)
                text = raw.decode("utf-8", errors="replace")[:MAX_UPLOAD_CHARS]
                doc_id = f"upload_{len(s.custom_docs) + 1}"
                s.custom_docs.append({"document_id": doc_id, "source": "upload", "filename": up.name[:120],
                                      "text": text})
                s.attached.append(doc_id)
                s.upload_gen += 1
                st.rerun()


def render_welcome() -> None:
    with st.container(border=False, key="welcome"):
        st.markdown("<div class='bg-welcome-title'>Hi Lina.</div>"
                    "<div class='bg-welcome-text'>Ask me to pay your bills, send money home, convert currency, or "
                    "check \"can I afford…?\" first. I simulate every plan, fixed rules check it, and nothing moves "
                    "until you approve it. Invoices you attach are read as data, never as orders. "
                    "Not sure where to start? Type <b>help</b>.</div>",
                    unsafe_allow_html=True)


def tab_assistant() -> None:
    s = st.session_state
    if not s.history:
        render_welcome()
    if not s.guided:
        render_examples()
    if s.attached:
        names = doc_names()
        st.caption("Sent with your next message, as untrusted data: "
                   + ", ".join(esc_md(names.get(a, a)) for a in s.attached))

    ov_payees = payees_by_id(s.backend.overview(s.session_id))
    names = doc_names()
    docs_by_id = {d["document_id"]: d for d in all_docs()}
    last_assistant = max((i for i, h in enumerate(s.history) if h["role"] == "assistant"), default=-1)
    for i, h in enumerate(s.history):
        with st.chat_message(h["role"], avatar=":material/shield:" if h["role"] == "assistant" else None):
            if s.get("g_target") == ("msg", i):
                st.markdown("<div class='g-target'></div>", unsafe_allow_html=True)
            with st.container(key=f"bubble_{i}"):
                st.markdown(esc_md(humanize(h["text"])) if h["role"] == "assistant" else esc_md(h["text"]))
            for doc_id in h.get("docs", []):
                d = docs_by_id.get(doc_id, {"filename": doc_id, "text": ""})
                with st.expander(f"{esc_md(d['filename'])} (untrusted: shown as plain text)"):
                    st.code(d["text"], language=None, wrap_lines=True)
            render_events(h.get("events", []), names)
            if h.get("projection") is not None:
                render_projection(h["projection"], str(i))
            q = h.get("question")
            if q and i == last_assistant and s.pending_question:
                if q not in h["text"]:
                    st.markdown(f"**{esc_md(humanize(q))}**")
                if not any(w in q.lower() for w in ("who", "receive", "which payee")):
                    st.caption("↳ Just type your answer below, for example \"50 euros\".")
            for aid in h.get("actions", []):
                if aid in s.actions:
                    render_card(s.actions[aid], ov_payees)
            if h["role"] == "assistant" and i == last_assistant:
                render_chips(h, list(ov_payees.values()))
            render_event_details(h.get("events", []), names)
            if h.get("anchor") is not None:
                st.caption(f"Your copy of the latest receipt: {anchor_label(h['anchor'])}")

    hint = ("Answer the agent's question, e.g. 50 euros" if s.pending_question
            else "Ask BorderlessGuard… or correct: \"actually make it 40\", \"cancel that\"")
    text = st.chat_input(f"{hint} (max {MAX_CHAT_CHARS:,} characters)", key="chat", max_chars=MAX_CHAT_CHARS)
    if text:
        submit(text)
        st.rerun()


def render_chips(entry: dict, payees: list) -> None:
    s = st.session_state
    pending = sum(v.status == "awaiting_confirmation" for v in s.actions.values())
    chips = quick_replies(entry, pending_question=s.pending_question, pending_count=pending, payees=payees,
                          tuition_and_rent_text=DEMO_GOAL)
    if not chips:
        return
    with st.container(horizontal=True, key="chips"):
        for j, (label, msg) in enumerate(chips):
            if st.button(label, key=f"chip_{j}",
                         help=f"Sends: \"{esc_md(msg)}\". It is checked like any typed message."):
                submit(msg)
                st.rerun()


def call_verify(fn, anchor):
    try:
        return fn(anchor)
    except TypeError:
        if anchor is None:
            return fn()
        raise


def set_verify_result(res, anchor) -> None:
    ok = bool(get(res, "valid"))
    st.session_state.verify_result = (
        ok, f"Matches your copy (receipt {anchor_label(anchor)}). " if ok
        else f"Does NOT match your copy (receipt {anchor_label(anchor)}). ", str(get(res, "detail", "")))


def tab_proof(ov: dict) -> None:
    s = st.session_state
    receipts = ov.get("receipts", [])
    st.markdown("<div class='bg-lede'>Every action, done <b>or</b> refused, gets a receipt. Each receipt records "
                "what you asked, the exact payment, the simulation, the rules' decision and your approval, and is "
                "linked to the previous one, so history cannot be edited quietly.</div>", unsafe_allow_html=True)
    anchor = s.customer_anchor
    if anchor is not None:
        signed = bool(get(anchor, "signature"))
        st.markdown("<div class='bg-copy'><span class='k'>Your copy of the latest receipt:</span> "
                    f"<span class='bg-anchor'>{esc_html(anchor_label(anchor))}</span>"
                    + (pill("check", "Signed copy", "ok", "sm") if signed else pill("alert", "Unsigned copy", "warn", "sm"))
                    + "</div>", unsafe_allow_html=True)
        st.caption("Signed by the bank and kept on your side, like a statement number. It lets you detect deleted or "
                   "rewritten receipts, which the chain alone cannot.")
    else:
        st.caption("No copy yet: you get one after the first receipt is written.")
    key_info = ov.get("signing_key")
    if key_info:
        if ov.get("demo_key_in_use"):
            st.warning(f"Receipts signed with: {esc_md(key_info)}", icon=IC_WARN)
        else:
            st.caption(f"Receipts signed with: {esc_md(key_info)}")
    if not receipts:
        st.info("No receipts yet. Approve an action (or let the rules refuse one) to create the first receipt.", icon=IC_INFO)
    if receipts:
        body = "".join(
            f"<tr><td class='num'>{esc_html(get(r, 'seq'))}</td><td>{esc_html(humanize(get(r, 'summary')))}</td>"
            f"<td>{pill('check', 'Approved', 'ok', 'sm') if get(r, 'confirmation_hash') else pill('block', 'Refused', 'bad', 'sm')}</td>"
            f"<td class='mono'>{esc_html(short(get(r, 'receipt_hash')))}</td>"
            f"<td class='mono'>{esc_html(short(get(r, 'prev_receipt_hash')))}</td></tr>" for r in receipts)
        st.markdown("<table class='bg-table'><caption>Receipts, oldest first</caption><thead><tr>"
                    "<th scope='col'>#</th><th scope='col'>What happened</th><th scope='col'>Approved by you</th>"
                    "<th scope='col'>Fingerprint</th><th scope='col'>Links to</th></tr></thead><tbody>" + body
                    + "</tbody></table>", unsafe_allow_html=True)
    fn = ov.get("verify_fn")
    c1, c2, _ = st.columns([1, 1.4, 2])
    if c1.button("Check the chain", key="verify", width="stretch", disabled=not receipts):
        if callable(fn):
            res = call_verify(fn, None)
            s.verify_result = (bool(get(res, "valid")), "Chain valid. " if get(res, "valid") else "Chain BROKEN. ",
                               str(get(res, "detail", "")))
        else:
            from ui.util import links_ok
            ok = links_ok(receipts)
            s.verify_result = (ok, "Chain valid. " if ok else "Chain BROKEN. ",
                               "Link check only; the backend exposes no verify function.")
    if c2.button("Check against my copy", key="verify_anchor", type="primary", width="stretch",
                 disabled=anchor is None or not callable(fn)):
        set_verify_result(call_verify(fn, anchor), anchor)
    vr = s.get("verify_result")
    if vr:
        (st.success if vr[0] else st.error)(vr[1] + esc_md(humanize(vr[2])), icon=IC_OK if vr[0] else IC_ERR)

    with st.expander("What if someone deletes receipts? (runs on a copy, the real ledger is not touched)"):
        st.markdown(
            "An insider who **deletes the most recent receipts** leaves a chain whose links still fit together, so a "
            "check of the chain alone says *valid*. Your copy names the latest receipt and its fingerprint: if that "
            "receipt is gone or rewritten, the check against your copy fails.\n\n"
            "The button below shows this on an **in-memory copy** of the receipts above: it drops your latest "
            "receipt and everything after it, then runs both checks on the copy.")
        if st.button("Run the deletion test on a copy", key="tamper_test", disabled=anchor is None or not receipts):
            s.tamper_result = truncation_demo(receipts, anchor)
        tr = s.get("tamper_result")
        if tr:
            st.markdown(f"Copy cut from receipt #{tr['deleted_from_seq']}: "
                        f"{tr['original_length']} → {tr['truncated_length']} receipts.")
            (st.warning if tr["chain_only_valid"] else st.success)(
                "Chain-only check on the copy: " + ("links still fit, the deletion is invisible."
                                                    if tr["chain_only_valid"] else "broken."),
                icon=IC_WARN if tr["chain_only_valid"] else IC_OK)
            (st.error if tr["anchor_valid"] else st.success)(
                "Check against your copy: " + ("still matches (unexpected)." if tr["anchor_valid"]
                                               else f"receipt #{tr['deleted_from_seq']} is missing: TAMPERING DETECTED."),
                icon=IC_ERR if tr["anchor_valid"] else IC_OK)
            st.caption("Illustration on a copy. The real check is \"Check against my copy\", which the ledger runs "
                       "on its own database.")


def run_attacks(attacks) -> None:
    results = st.session_state.attack_results
    factory = lambda poisoned: make_backend(poisoned)  # noqa: E731    (attacks always run on the rules parser)
    with st.spinner("Running attacks, each on a fresh sandbox account…"):
        for a in attacks:
            results[a.key] = run_attack(a, factory)


def render_attack_row(a) -> None:
    """One attack = one ruled row: its name (the button that replays it) and its verdict; once run, the details
    (what the attacker tried, the messages sent, how it was stopped, the evidence) open underneath."""
    results = st.session_state.attack_results
    with st.container(border=False, key=f"attack_{'ran_' if a.key in results else ''}{a.key}"):
        c1, c2 = st.columns([5, 1.5], vertical_alignment="center")
        if c1.button(a.name, key=f"run_{a.key}", width="stretch"):
            run_attacks([a])
        res = results.get(a.key)
        if res is None:
            c2.markdown("<div class='bg-atk-status'><span class='none'>Not run</span></div>", unsafe_allow_html=True)
            return
        head = (pill("check", "BLOCKED", "ok", "caps") if (res.blocked and not res.error)
                else pill("x", "NOT BLOCKED", "bad", "caps"))
        c2.markdown(f"<div class='bg-atk-status'>{head}</div>", unsafe_allow_html=True)
        st.markdown(f"**Attack:** {esc_md(a.plain_description or a.description or a.user_text)}")
        st.code(a.script, language=None, wrap_lines=True)
        st.markdown(f"**How it is stopped:** {esc_md(a.plain_defence or a.expected_defence)}")
        if res.error:
            st.error(f"Run failed (counted as NOT blocked): {esc_md(res.error)}", icon=IC_ERR)
        with st.expander("Evidence"):
            st.markdown(f"**Technical description:** {esc_md(a.description or a.user_text)}")
            st.markdown(f"**Technical defence:** {esc_md(a.expected_defence)}")
            for line in res.evidence:
                st.markdown(f"- {esc_md(humanize(line))}")


def tab_attacks() -> None:
    st.markdown("<div class='bg-lede'>Each attack replays a known attack against a <b>fresh</b> sandbox account and "
                "reports whether it was blocked. A crash counts as NOT blocked. Pick one, or run all of them.</div>",
                unsafe_allow_html=True)
    results = st.session_state.attack_results
    if st.button(f"Run all {len(ATTACKS)} attacks", key="run_all", type="primary"):
        run_attacks(ATTACKS)
    score = st.container()                  # filled after the rows, so a single run is counted in this pass
    with st.container(key="attacklist"):
        for a in ATTACKS:
            render_attack_row(a)
    done = [results[a.key] for a in ATTACKS if a.key in results]
    with score:
        if done:
            n = sum(r.blocked and not r.error for r in done)
            good = n == len(done)
            st.markdown(f"<div class='bg-score {'' if good else 'bad'}'>{n} / {len(done)} blocked</div>"
                        "<div class='bg-score-sub'>Each attack ran on a fresh sandbox account.</div>",
                        unsafe_allow_html=True)
        else:
            st.info("No attack run yet. Click **Run all** or pick one attack.", icon=IC_INFO)


# --------------------------------------------------------------------------- main
def main() -> None:
    init_state()
    s = st.session_state
    st.markdown(load_css(), unsafe_allow_html=True)
    ov = s.backend.overview(s.session_id)
    render_sidebar(ov)
    render_header()
    if s.guided:
        render_guided()
    t1, t2, t3 = st.tabs(["Assistant", "Receipts", "Attack test"])
    with t1:
        tab_assistant()
    with t2:
        tab_proof(s.backend.overview(s.session_id))
    with t3:
        tab_attacks()
    if s.guided:
        scroll_to_target()


main()
