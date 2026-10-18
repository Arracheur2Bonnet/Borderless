"""White-box audit, round 2, UI (real backend, Streamlit AppTest). See docs/review/security_audit.md, round 2.

Rendering facts checked once in a real Chromium (Playwright) against `streamlit run ui/app.py`, Streamlit 1.66:
* a bare URL or `www.` domain inside st.markdown(esc_md(...)) becomes a clickable <a> (GFM autolink literal);
* a payee named `![p](http://127.0.0.1:8611/pixel.png) Corp` renders as an <img> inside the quick-reply chip
  buttons, and the browser fetched http://127.0.0.1:8611/pixel.png.
These tests assert the markdown source the app hands to Streamlit, which is what decides that rendering.
"""
from __future__ import annotations

import os
import re

import pytest
from streamlit.testing.v1 import AppTest

import bg.app
from bg.contracts.models import Unsupported
from bg.contracts.params import DEMO_OTP
from tests.audit.support import ScriptedParser

APP = os.path.join(os.path.dirname(__file__), "..", "..", "ui", "app.py")
# What GFM turns into a link by itself: a scheme URL or a www. domain that no backslash interrupts.
AUTOLINK = re.compile(r"(?<![\\\w])(?:https?://|www\.)[^\s\\]")
MD_IMAGE_OR_LINK = re.compile(r"(?<!\\)!?\[[^\]]*(?<!\\)\]\(")


def _app(monkeypatch, parser=None) -> AppTest:
    monkeypatch.delenv("BG_FAKE_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BG_PARSER", raising=False)
    if parser is not None:
        monkeypatch.setattr(bg.app, "_parser_from_env", lambda: parser)
    t = AppTest.from_file(APP, default_timeout=60)
    t.run()
    assert not t.exception
    return t


@pytest.mark.xfail(strict=True, reason="AUDIT2-2: esc_md leaves bare URLs autolinkable, so model-written text "
                                       "(and payee names or file names echoed by the agent) becomes a clickable "
                                       "link in the bank's reply")
def test_audit2_2_model_url_is_not_clickable_in_the_chat(monkeypatch):
    phish = "Your account is frozen. Re-verify at https://secure-bank-verify.example/login or www.secure-bank.example"
    at = _app(monkeypatch, ScriptedParser(Unsupported(reason=phish)))
    at.chat_input(key="chat").set_value("hello").run()
    assert not at.exception
    linked = [m.value for m in at.markdown if AUTOLINK.search(m.value)]
    assert not linked, f"clickable link in the chat: {linked[:1]}"


@pytest.mark.xfail(strict=True, reason="AUDIT2-3: quick-reply chip labels put the payee display name into a "
                                       "Markdown-rendering button label unescaped (remote image fetched, links)")
def test_audit2_3_payee_name_markdown_not_rendered_in_chips(monkeypatch):
    at = _app(monkeypatch)
    payload = "![p](https://tracker.example/pixel.png) Corp"
    at.chat_input(key="chat").set_value(f"add payee {payload} with IBAN FR7630006000011234567890189").run()
    aid = next(b.key[len("approve_"):] for b in at.button if b.key and b.key.startswith("approve_"))
    at.text_input(key=f"otp_{aid}").set_value(DEMO_OTP)
    next(b for b in at.button if b.key == f"approve_{aid}").click().run()
    at.chat_input(key="chat").set_value("send money").run()        # agent asks who: one chip per payee
    assert not at.exception
    chips = [b.label for b in at.button if b.key and b.key.startswith("chip_")]
    assert chips, "no quick-reply chips rendered"
    bad = [c for c in chips if MD_IMAGE_OR_LINK.search(c)]
    assert not bad, f"payee name rendered as Markdown image/link in a chip: {bad}"
