import os

import pytest
from streamlit.testing.v1 import AppTest

from bg.contracts.params import DEMO_OTP, MAX_OTP_ATTEMPTS
from ui.util import unescape_md

APP = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ui", "app.py")


@pytest.fixture()
def at(monkeypatch):
    monkeypatch.setenv("BG_FAKE_BACKEND", "1")
    t = AppTest.from_file(APP, default_timeout=30)
    t.run()
    assert not t.exception
    return t


def _btn(t, key):
    return next(b for b in t.button if b.key == key)


def _keys(t, prefix):
    return [b.key for b in t.button if b.key and b.key.startswith(prefix)]


def _text(t):
    """All visible markdown/caption text, with esc_md escaping undone (what the viewer reads)."""
    return unescape_md(" ".join(m.value for m in t.markdown) + " " + " ".join(c.value for c in t.caption))


def _say(t, text):
    t.chat_input(key="chat").set_value(text).run()
    assert not t.exception


def test_first_render(at):
    assert any("BorderlessGuard" in x.value for x in at.title)
    assert len(at.tabs) == 3
    text = _text(at)
    assert "The AI reads your request. Fixed rules and your approval move the money." in text
    assert text.count("Fixed rules and your approval move the money") == 1     # slogan once, in the header
    assert "SANDBOX · no real money" in text
    for k in ("ex_golden", "ex_afford", "ex_multiturn", "ex_maximum", "ex_data", "ex_big"):
        assert _btn(at, k)
    assert _btn(at, "ex_afford").label == "Can I afford to send 300 EUR to mom after my bills?"


def test_chat_bubbles_and_cards_under_agent_message(at):
    _btn(at, "ex_golden").click().run()
    assert not at.exception
    roles = [c.name for c in at.chat_message]
    assert roles == ["user", "assistant"]
    assert len(_keys(at, "approve_")) == 3
    # the cards render inside the assistant bubble
    assert len([b for b in at.chat_message[1].button if b.key.startswith("approve_")]) == 3
    assert any(t.key and t.key.startswith("otp_") for t in at.text_input)
    assert "Rules passed: " in _text(at)


def test_approve_flow_otp_and_anchor(at):
    _btn(at, "ex_golden").click().run()
    aids = [k[len("approve_"):] for k in _keys(at, "approve_")]
    _btn(at, f"approve_{aids[0]}").click().run()  # FX
    _btn(at, f"approve_{aids[1]}").click().run()  # tuition
    next(t for t in at.text_input if t.key == f"otp_{aids[2]}").input(DEMO_OTP)
    _btn(at, f"approve_{aids[2]}").click().run()  # rent, step-up
    assert not at.exception
    assert not _keys(at, "approve_")
    assert len(at.success) >= 3
    assert at.session_state.customer_anchor.seq == 3
    assert "Your copy of the latest receipt:" in _text(at) and "#3 · " in _text(at) and "· signed" in _text(at)
    _btn(at, "verify_anchor").click().run()
    assert any("Matches your copy (receipt #3" in s.value for s in at.success)
    _btn(at, "verify").click().run()
    assert any("Chain valid" in s.value for s in at.success)


def test_tamper_test_runs_on_a_copy(at):
    _btn(at, "ex_big").click().run()          # a denial writes a receipt -> anchor #1
    _btn(at, "tamper_test").click().run()
    assert not at.exception
    assert any("TAMPERING DETECTED" in s.value for s in at.success)
    assert any("deletion is invisible" in w.value for w in at.warning)
    # the real chain is untouched
    _btn(at, "verify_anchor").click().run()
    assert any("Matches your copy (receipt #1" in s.value for s in at.success)


def test_otp_attempts_and_lockout(at):
    _btn(at, "ex_golden").click().run()
    rent = _keys(at, "approve_")[2][len("approve_"):]
    assert f"Attempts remaining: **{MAX_OTP_ATTEMPTS} of {MAX_OTP_ATTEMPTS}**" in _text(at)
    for i in range(1, MAX_OTP_ATTEMPTS):
        next(t for t in at.text_input if t.key == f"otp_{rent}").input("000000")
        _btn(at, f"approve_{rent}").click().run()
        assert f"Attempts remaining: **{MAX_OTP_ATTEMPTS - i} of {MAX_OTP_ATTEMPTS}**" in _text(at)
    next(t for t in at.text_input if t.key == f"otp_{rent}").input("000000")
    _btn(at, f"approve_{rent}").click().run()
    assert not at.exception
    assert f"approve_{rent}" not in _keys(at, "approve_")
    assert "LOCKED: too many wrong codes" in _text(at)
    assert any("Locked after 3 wrong verification codes" in e.value for e in at.error)


def test_r4_denial_shows_rule(at):
    _btn(at, "ex_big").click().run()
    assert not at.exception
    text = _text(at)
    assert "MAX_SINGLE_PAYMENT" in text and "No code or message can unlock this." in text
    assert "above the 4,000 EUR per-payment cap" in text          # plain-language one-liner


def test_injection_badge(at):
    _btn(at, "attach_doc_rent_nov").click().run()
    _btn(at, "ex_golden").click().run()
    assert any("quarantined" in e.value for e in at.error)


def test_clarify_has_no_cards(at):
    _btn(at, "ex_maximum").click().run()
    assert not _keys(at, "approve_")


def test_multi_turn_pending_question(at):
    _btn(at, "ex_multiturn").click().run()
    assert at.session_state.pending_question
    assert "Just type your answer below" in _text(at)
    assert not _keys(at, "approve_")
    _say(at, "50 euros")
    assert at.session_state.pending_question is None
    assert [c.name for c in at.chat_message] == ["user", "assistant", "user", "assistant"]
    assert len(_keys(at, "approve_")) == 1
    assert "Pay 50.00 EUR to Samira Haddad" in _text(at)


def test_projection_card(at):
    _btn(at, "ex_afford").click().run()
    assert not at.exception
    assert not _keys(at, "approve_")          # an affordability answer never proposes an action
    text = _text(at)
    assert "Projection" in text and "Reserve breached" in text
    labels = {m.label: m.value for m in at.metric}
    assert labels["EUR: now 450.00 → after"] == "300.00"
    assert "USD: now 2,600.00 → after" in labels
    assert "Send 300.00 EUR to Samira Haddad" in text


def test_correction_edit_and_cancel_go_through_dialogue(at):
    _btn(at, "ex_golden").click().run()
    aids = [k[len("approve_"):] for k in _keys(at, "approve_")]
    rent = aids[2]
    # only the most recent pending action carries Edit / Cancel; older ones get a plain Reject
    assert f"edit_{rent}" in _keys(at, "edit_") and f"cancel_{rent}" in _keys(at, "cancel_")
    assert f"reject_{aids[0]}" in _keys(at, "reject_")
    next(t for t in at.text_input if t.key == f"edit_amt_{rent}").input("40")
    _btn(at, f"edit_{rent}").click().run()
    assert not at.exception
    users = [m for m in at.chat_message if m.name == "user"]
    assert "actually make it 40" in users[-1].markdown[0].value
    assert at.session_state.actions[rent].status == "rejected_by_user"
    new = [k for k in _keys(at, "approve_") if k[len("approve_"):] not in aids]
    assert len(new) == 1 and "Pay 40.00 EUR to Jean Dupont" in _text(at)
    new_id = new[0][len("approve_"):]
    _btn(at, f"cancel_{new_id}").click().run()
    assert "cancel that" in [m for m in at.chat_message if m.name == "user"][-1].markdown[0].value
    assert at.session_state.actions[new_id].status == "rejected_by_user"
    assert len(_keys(at, "approve_")) == 2


def test_adversarial_tab_all_blocked(at):
    _btn(at, "run_all").click().run()
    assert not at.exception
    text = _text(at)
    assert text.count("NOT BLOCKED") == 0 and text.count("BLOCKED") >= 7
    assert "7 / 7 blocked" in text


def test_new_attacks_individually(at):
    _btn(at, "run_structuring").click().run()
    _btn(at, "run_sanctions").click().run()
    assert not at.exception
    text = _text(at)
    assert "structuring_suspected raised: True" in text
    assert "sanctions_hit raised: True" in text
    assert "2 / 2 blocked" in text and "NOT BLOCKED" not in text
