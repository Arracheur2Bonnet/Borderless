"""UI: the "Language model" selector (backend factory monkeypatched), link-proof escaping, input cap."""
import os
import re

import pytest
from streamlit.testing.v1 import AppTest

import bg.agent
from bg.agent import RulesParser
from ui import app as ui_app
from ui.util import esc_md, quick_replies, unescape_md

APP = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ui", "app.py")
LABELS = ["Rules only (instant)", "Smart cascade: rules, then local Qwen", "Local model (Ollama)",
          "Connect a model (OpenAI-compatible)"]
AFFORD = "Can I afford to send 300 EUR to mom after my bills?"


class _Labelled:
    """Behaves like the rules parser but reports another tier name (like a model that answered)."""

    def __init__(self, label):
        self.label, self.inner = label, RulesParser()

    def parse(self, text, **kw):
        return self.inner.parse(text, **kw).model_copy(update={"parser": self.label})


def _app(monkeypatch, factory=None):
    monkeypatch.delenv("BG_FAKE_BACKEND", raising=False)
    monkeypatch.delenv("BG_PARSER", raising=False)
    monkeypatch.delenv("BG_LOCAL_MODEL", raising=False)
    calls = []

    def fake_make_parser(kind, *, model=None, base_url=None, api_key=None):
        calls.append({"kind": kind, "model": model, "base_url": base_url, "api_key": api_key})
        return factory(kind) if factory else RulesParser()

    monkeypatch.setattr(bg.agent, "make_parser", fake_make_parser, raising=False)
    t = AppTest.from_file(APP, default_timeout=60)
    t.run()
    assert not t.exception
    return t, calls


def _rendered(t):
    return " ".join([m.value for m in t.markdown] + [c.value for c in t.caption] + [e.value for e in t.error]
                    + [e.value for e in t.warning] + [e.value for e in t.info])


def _say(t, text):
    t.chat_input(key="chat").set_value(text).run()
    assert not t.exception


# ------------------------------------------------------------------ the selector
def test_one_language_model_selector_replaces_the_toggles(monkeypatch):
    t, calls = _app(monkeypatch)
    sel = t.selectbox(key="parser_kind")
    assert sel.label == "Language model"
    assert sel.value == "rules"
    assert list(sel.options) == LABELS
    toggles = [x.label for x in t.toggle]
    assert not any("Claude" in x or "Qwen" in x for x in toggles), toggles
    assert "Guided demo (6 steps)" in toggles and "Lying exchange-rate feed" in toggles
    assert calls == []                                    # rules is the default path: no factory call


def test_switching_to_cascade_rebuilds_the_backend(monkeypatch):
    t, calls = _app(monkeypatch)
    _say(t, AFFORD)
    assert len(t.session_state.history) > 0
    t.selectbox(key="parser_kind").select("cascade").run()
    assert not t.exception
    assert calls[-1]["kind"] == "cascade"
    assert t.session_state.history == []                  # fresh sandbox, like the old toggles


def test_local_model_picker(monkeypatch):
    t, calls = _app(monkeypatch)
    t.selectbox(key="parser_kind").select("local").run()
    assert calls[-1] == {"kind": "local", "model": "qwen2.5:7b-instruct", "base_url": None, "api_key": None}
    pick = t.selectbox(key="local_model")
    assert list(pick.options) == ["qwen2.5:7b-instruct", "qwen2.5:3b-instruct"]
    pick.select("qwen2.5:3b-instruct").run()
    assert calls[-1]["kind"] == "local" and calls[-1]["model"] == "qwen2.5:3b-instruct"


def test_connected_model_fields_and_key_is_never_echoed(monkeypatch):
    t, calls = _app(monkeypatch)
    t.selectbox(key="parser_kind").select("connected").run()
    assert not calls                                      # nothing is built until "Connect"
    t.text_input(key="conn_base_url").set_value("http://localhost:1234/v1")
    t.text_input(key="conn_model").set_value("my-model")
    key_field = t.text_input(key="conn_api_key")
    assert key_field.proto.type == 1                      # PASSWORD
    key_field.set_value("sk-SECRET-123")
    next(b for b in t.button if b.label == "Connect").click().run()
    assert not t.exception
    assert calls[-1] == {"kind": "connected", "model": "my-model", "base_url": "http://localhost:1234/v1",
                         "api_key": "sk-SECRET-123"}
    _say(t, "Send money to mom")
    assert "sk-SECRET-123" not in _rendered(t)


def test_unreachable_model_falls_back_with_a_calm_notice(monkeypatch):
    t, _ = _app(monkeypatch, lambda kind: _Labelled("rules"))     # what the backend does when Ollama is down
    t.selectbox(key="parser_kind").select("local").run()
    assert ui_app.FALLBACK_NOTICE not in _rendered(t)             # nothing said before a message needs it
    _say(t, AFFORD)
    assert ui_app.FALLBACK_NOTICE in _rendered(t)


def test_no_fallback_notice_when_the_model_answers(monkeypatch):
    t, _ = _app(monkeypatch, lambda kind: _Labelled("local"))
    t.selectbox(key="parser_kind").select("local").run()
    _say(t, AFFORD)
    assert ui_app.FALLBACK_NOTICE not in _rendered(t)


def test_cascade_answered_by_rules_is_normal_not_a_fallback(monkeypatch):
    t, _ = _app(monkeypatch, lambda kind: _Labelled("rules"))
    t.selectbox(key="parser_kind").select("cascade").run()
    _say(t, AFFORD)
    assert ui_app.FALLBACK_NOTICE not in _rendered(t)


def test_factory_failure_keeps_the_app_working_on_rules(monkeypatch):
    def boom(kind):
        raise ValueError("bad url with sk-LEAK")

    t, _ = _app(monkeypatch, boom)
    t.selectbox(key="parser_kind").select("connected").run()
    assert "press Connect" in _rendered(t)               # fields empty: asked to fill them, factory not called
    t.text_input(key="conn_base_url").set_value("http://localhost:1234/v1")
    t.text_input(key="conn_model").set_value("m")
    t.text_input(key="conn_api_key").set_value("sk-LEAK")
    next(b for b in t.button if b.label == "Connect").click().run()
    assert not t.exception
    assert "could not be set up" in _rendered(t)
    text = _rendered(t)
    assert "sk-LEAK" not in text
    _say(t, "Send 50 euros to mom")
    assert not t.exception and t.session_state.history


def test_technical_details_name_the_tier_that_answered(monkeypatch):
    t, _ = _app(monkeypatch, lambda kind: _Labelled("local"))
    t.selectbox(key="parser_kind").select("local").run()
    _say(t, "Send 50 euros to mom")
    assert any("Parser that answered" in m.value and "local" in m.value for m in t.markdown)


# ------------------------------------------------------------------ input cap
def test_chat_input_is_capped_and_says_so(monkeypatch):
    t, _ = _app(monkeypatch)
    ci = t.chat_input(key="chat")
    assert ci.proto.max_chars == 2000
    assert "2,000 characters" in ci.placeholder


# ------------------------------------------------------------------ links and images from untrusted text
@pytest.mark.parametrize("raw", [
    "Re-verify at https://secure-bank-verify.example/login now",
    "go to HTTP://evil.example or www.evil.example",
    "write to attacker@evil.example or mailto:attacker@evil.example",
    "ftp://evil.example/x",
])
def test_esc_md_breaks_autolinks(raw):
    out = esc_md(raw)
    assert not re.search(r"(?<![\\\w])(?:https?://|ftp://|www\.|mailto:)", out, re.I), out
    assert "@" not in out.replace("\\@", "")
    assert unescape_md(out) == raw


def test_chip_labels_do_not_render_payee_markdown():
    class B:
        display_name = "![p](https://tracker.example/pixel.png) Corp"
        aliases = []

    chips = quick_replies({}, pending_question="Who should receive it?", pending_count=0, payees=[B()],
                          tuition_and_rent_text="")
    label, msg = chips[0]
    assert label.startswith("To ")
    assert not re.search(r"(?<!\\)!?\[[^\]]*(?<!\\)\]\(", label), label
    assert unescape_md(label) == "To " + B.display_name
    assert msg == "to " + B.display_name              # the message sent stays raw text: it is parsed, not rendered
    assert not re.search(r"(?<!\\)!?\[[^\]]*(?<!\\)\]\(", f'Sends: "{esc_md(msg)}"')


def test_backend_refusal_shows_a_calm_notice(monkeypatch):
    t, _ = _app(monkeypatch, lambda kind: None)          # make_parser returns None for what it will not use
    t.selectbox(key="parser_kind").select("connected").run()
    t.text_input(key="conn_base_url").set_value("http://remote.example/v1")
    t.text_input(key="conn_model").set_value("m")
    next(b for b in t.button if b.label == "Connect").click().run()
    assert not t.exception
    assert "must use https" in _rendered(t)
    _say(t, "Send 50 euros to mom")
    assert not t.exception and t.session_state.history
