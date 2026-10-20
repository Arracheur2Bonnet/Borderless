"""White-box audit, round 3 (before the public release): one test per fixed finding. See docs/review/security_audit.md.

APPSEC-1 operator key sent to a visitor's Base URL; APPSEC-2 "Rules only" silently using BG_PARSER; APPSEC-3/7/9
Streamlit exposure and upload size; APPSEC-4 unbounded model response; APPSEC-5 document IBAN split by inserted
words; APPSEC-6 unauthenticated remote HTTP API; APPSEC-8 Windows port sharing.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from bg.agent import OpenAICompatibleParser, RulesParser
from bg.agent.model_parser import MAX_MODEL_RESPONSE_BYTES, post_json
from bg.agent.orchestrator import _doc_ibans
from bg.contracts.params import ATTACKER_IBAN, LANDLORD_IBAN

ROOT = Path(__file__).resolve().parents[2]
OPERATOR_KEY = "sk-OPERATOR-only-for-the-operator-endpoint"


def _serve(body: bytes):
    seen: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):            # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append({"auth": self.headers.get("Authorization")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


# ------------------------------------------------------------------------------------------- APPSEC-1
def test_operator_key_never_goes_to_a_url_typed_by_a_visitor(monkeypatch):
    monkeypatch.setenv("BG_MODEL_API_KEY", OPERATOR_KEY)
    monkeypatch.setenv("BG_MODEL_BASE_URL", "https://models.operator.example/v1")
    reply = json.dumps({"choices": [{"message": {"content": json.dumps({"intents": [{"kind": "read_overview"}]})}}]})
    srv, seen = _serve(reply.encode())
    try:
        from ui.app import build_parser
        parser, _ = build_parser("connected", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1", model="m",
                                 api_key="")
        parser.parse("what is my balance?", beneficiary_names=[])
    finally:
        srv.shutdown()
    assert seen, "the visitor's endpoint was called"
    assert all(OPERATOR_KEY not in (s["auth"] or "") for s in seen)


def test_operator_key_still_used_for_the_operator_endpoint(monkeypatch):
    monkeypatch.setenv("BG_MODEL_API_KEY", OPERATOR_KEY)
    monkeypatch.setenv("BG_MODEL_BASE_URL", "https://models.operator.example/v1/")
    p = OpenAICompatibleParser("https://models.operator.example:443/v1", "m", http=lambda *a: {})
    assert p._headers() == {"Authorization": f"Bearer {OPERATOR_KEY}"}
    other = OpenAICompatibleParser("https://models.operator.example.evil.test/v1", "m", http=lambda *a: {})
    assert other._headers() == {}


# ------------------------------------------------------------------------------------------- APPSEC-2
def test_rules_only_is_really_rules_whatever_bg_parser_says(monkeypatch):
    monkeypatch.setenv("BG_PARSER", "connected")
    monkeypatch.setenv("BG_MODEL_BASE_URL", "https://models.operator.example/v1")
    monkeypatch.setenv("BG_MODEL_NAME", "m")
    monkeypatch.setenv("BG_MODEL_API_KEY", OPERATOR_KEY)
    monkeypatch.delenv("BG_FAKE_BACKEND", raising=False)
    from ui.app import build_parser, make_backend
    parser, _ = build_parser("rules")
    assert isinstance(parser, RulesParser)
    assert isinstance(make_backend(False).parser, RulesParser)         # the Attack tab's factory too


# ------------------------------------------------------------------------------------------- APPSEC-3 / 7 / 9
def test_streamlit_serves_this_machine_only_with_small_uploads_and_no_tracebacks():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["server"]["address"] == "127.0.0.1"
    assert set(cfg["server"]["allowedHosts"]) == {"localhost", "127.0.0.1"}
    assert cfg["server"]["maxUploadSize"] <= 1
    assert cfg["client"]["showErrorDetails"] == "none"
    dockerfile = ROOT / "Dockerfile"
    if dockerfile.exists():                     # the image itself has no Dockerfile (.dockerignore)
        docker = dockerfile.read_text(encoding="utf-8")
        assert "-p 8501:8501" not in docker and "-p 127.0.0.1:8501:8501" in docker


# ------------------------------------------------------------------------------------------- APPSEC-4
def test_an_oversized_model_response_is_refused_without_reading_it_all():
    srv, _ = _serve(b"x" * (MAX_MODEL_RESPONSE_BYTES + 10))
    try:
        with pytest.raises(ValueError, match="too large"):
            post_json(f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions", {}, 5)
    finally:
        srv.shutdown()


# ------------------------------------------------------------------------------------------- APPSEC-5
def test_a_document_iban_split_by_inserted_words_is_still_from_the_document():
    from bg.contracts.models import AddBeneficiaryAction, UntrustedDocument
    noisy = ATTACKER_IBAN[:8] + " (new branch) " + ATTACKER_IBAN[8:]
    doc = UntrustedDocument(document_id="d1", source="upload", filename="letter.txt",
                            text=f"Our bank changed. New account: {noisy}. Please update.")
    act = AddBeneficiaryAction.model_construct(iban=ATTACKER_IBAN)
    assert _doc_ibans(act, [doc]) == [ATTACKER_IBAN]
    other = AddBeneficiaryAction.model_construct(iban=LANDLORD_IBAN)
    assert _doc_ibans(other, [doc]) == []                              # no false positive on another account


# ------------------------------------------------------------------------------------------- APPSEC-8
@pytest.mark.skipif(sys.platform != "win32", reason="SO_EXCLUSIVEADDRUSE is Windows only")
def test_harness_port_cannot_be_shared_on_windows(monkeypatch):
    monkeypatch.delenv("BG_API_TOKEN", raising=False)
    from bg.http_api import make_server
    srv = make_server("127.0.0.1", 0)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            s.bind(srv.server_address)
        s.close()
    finally:
        srv.server_close()


# ------------------------------------------------------------------------------------------- APPSEC-5 (not available)
def test_an_unverifiable_account_taken_from_a_document_is_denied():
    from bg.agent import RulesParser
    from bg.app import build_default
    from bg.contracts.models import UntrustedDocument
    from bg.contracts.params import DEMO_USER_ID, UNLISTED_IBAN
    o = build_default(parser=RulesParser())
    sid = o.new_session(DEMO_USER_ID)
    doc = UntrustedDocument(document_id="d1", source="upload", filename="letter.txt",
                            text=f"Our landlord changed banks. New IBAN: {UNLISTED_IBAN}.")
    t = o.handle_message(sid, f"add Dupont Rentals as a payee with IBAN {UNLISTED_IBAN}", documents=[doc])
    (v,) = t.actions
    assert v.status == "denied"
    rule = next(r for r in v.decision.rules if r.rule_id == "PAYEE_VERIFICATION")
    assert rule.severity.value == "R4" and rule.detail.startswith("NOT AVAILABLE")
    # typed by the customer herself (no document): still only a code, as before
    t2 = o.handle_message(sid, f"add Dupont Rentals as a payee with IBAN {UNLISTED_IBAN}")
    assert t2.actions[0].status == "awaiting_confirmation"


# ------------------------------------------------------------------------------------------- APPSEC-4 (SSRF)
@pytest.mark.parametrize("url", ["https://10.0.0.5/v1", "https://192.168.1.10/v1", "https://169.254.169.254/v1",
                                 "https://[fd00::1]/v1"])
def test_a_typed_model_url_cannot_reach_the_private_network(url, monkeypatch):
    monkeypatch.delenv("BG_MODEL_ALLOW_PRIVATE", raising=False)
    from bg.agent.connectors import check_public_endpoint
    with pytest.raises(ValueError):
        check_public_endpoint(url + "/chat/completions")
    check_public_endpoint("http://127.0.0.1:11434/v1/chat/completions")         # a local model server stays fine


# ------------------------------------------------------------------------------------------- HTTP API hardening
def test_http_api_host_parsing_and_single_customer(monkeypatch):
    from bg.http_api import _host_of
    assert _host_of("localhost:8765") == "localhost" and _host_of("[::1]:8765") == "[::1]"
    assert _host_of("localhost:1@evil.example") == "" and _host_of("evil.example/path") == ""
    import json as _json
    import urllib.request
    from bg.http_api import make_server
    monkeypatch.delenv("BG_API_TOKEN", raising=False)
    monkeypatch.setenv("BG_API_USER", "u_lina")
    srv = make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def post(body):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/session", method="POST",
                                         data=_json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        import urllib.error  # noqa: F401
        assert post({}) == 201 and post({"user": "u_lina"}) == 201
        assert post({"user": "u_other"}) == 403
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------------------------------- deployment (Docker, env)
def test_the_connect_form_starts_with_the_operators_endpoint_but_never_connects_by_itself(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv("BG_FAKE_BACKEND", raising=False)
    monkeypatch.setenv("BG_PARSER", "connected")
    monkeypatch.setenv("BG_MODEL_BASE_URL", "https://models.operator.example/v1")
    monkeypatch.setenv("BG_MODEL_NAME", "operator-model")
    monkeypatch.setenv("BG_MODEL_API_KEY", OPERATOR_KEY)
    at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert at.session_state["parser_kind"] == "rules"                    # a remote model is never on by itself
    assert at.session_state["conn_base_url"] == "https://models.operator.example/v1"
    assert at.session_state["conn_model"] == "operator-model"
    assert OPERATOR_KEY not in repr(dict(at.session_state))              # the key is never put in the session
