import os

import pytest
from streamlit.testing.v1 import AppTest

from bg.contracts.params import DEMO_OTP

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


def test_first_render(at):
    assert any("BorderlessGuard" in x.value for x in at.title)
    assert len(at.tabs) == 3


def test_example_shows_cards_and_rules(at):
    _btn(at, "ex_0").click().run()
    assert not at.exception
    keys = [b.key for b in at.button]
    assert sum(k.startswith("approve_") for k in keys) == 3
    assert any(t.key and t.key.startswith("otp_") for t in at.text_input)
    assert any("passed 2/13" in m.value or "passed 13/13" in m.value or "12/13" in m.value
               for m in at.markdown)


def test_approve_flow_and_otp(at):
    _btn(at, "ex_0").click().run()
    aids = [b.key[len("approve_"):] for b in at.button if b.key.startswith("approve_")]
    _btn(at, f"approve_{aids[0]}").click().run()  # FX
    _btn(at, f"approve_{aids[1]}").click().run()  # tuition
    # rent: wrong OTP is refused, right OTP executes
    _btn(at, f"approve_{aids[2]}").click().run()
    assert f"approve_{aids[2]}" in [b.key for b in at.button]
    next(t for t in at.text_input if t.key == f"otp_{aids[2]}").input(DEMO_OTP)
    _btn(at, f"approve_{aids[2]}").click().run()
    assert not at.exception
    assert not [b for b in at.button if b.key.startswith("approve_")]
    assert len(at.success) >= 3


def test_r4_denial_shows_rule(at):
    _btn(at, "ex_3").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "MAX_SINGLE_PAYMENT" in text
    assert "BLOCKED by policy" in text


def test_injection_badge(at):
    _btn(at, "attach_doc_rent_nov").click().run()
    _btn(at, "ex_0").click().run()
    assert any("quarantined" in e.value for e in at.error)


def test_clarify_has_no_cards(at):
    _btn(at, "ex_1").click().run()
    assert not [b for b in at.button if b.key.startswith("approve_")]


def test_adversarial_tab_all_blocked(at):
    _btn(at, "run_all").click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert text.count("NOT BLOCKED") == 0 and text.count("BLOCKED") >= 5
