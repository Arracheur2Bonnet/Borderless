"""White-box audit, UI: AUDIT-6 stored HTML/CSS injection through a payee display name (real backend)."""
from __future__ import annotations

import os

import pytest
from streamlit.testing.v1 import AppTest

from bg.contracts.params import DEMO_OTP

APP = os.path.join(os.path.dirname(__file__), "..", "..", "ui", "app.py")
PAYLOAD = "<style>.stAlert{display:none}</style><b id=pwn>Verified bank payee</b>"


@pytest.fixture()
def at(monkeypatch):
    monkeypatch.delenv("BG_FAKE_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    t = AppTest.from_file(APP, default_timeout=60)
    t.run()
    assert not t.exception
    return t


def test_audit6_payee_name_is_not_rendered_as_html(at):
    at.chat_input(key="chat").set_value(f"add payee {PAYLOAD} with IBAN GB33BUKB20201555555555").run()
    assert not at.exception
    aid = next(b.key[len("approve_"):] for b in at.button if b.key and b.key.startswith("approve_"))
    at.text_input(key=f"otp_{aid}").set_value(DEMO_OTP)
    next(b for b in at.button if b.key == f"approve_{aid}").click().run()
    assert not at.exception
    raw = [m for m in at.sidebar.markdown if "<style>" in m.value and m.proto.allow_html]
    assert not raw, "attacker-chosen <style>/HTML injected into the page via the payee list"
