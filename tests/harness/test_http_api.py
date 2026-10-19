"""P1-05: the localhost HTTP API replays mission T01 and refuses bad input. Server on an ephemeral port, in a thread."""
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from bg.harness import Harness
from bg.http_api import MAX_BODY_BYTES, make_server


@pytest.fixture()
def api():
    server = make_server("127.0.0.1", 0, Harness())
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_health(api):
    st, body = call(api, "GET", "/health")
    assert st == 200 and body["status"] == "ok" and body["parser"] == "rules"


def test_t01_through_http_ends_at_600_196_74_and_three_receipts(api):
    st, s = call(api, "POST", "/session", {"user": "u_lina"})
    assert st == 201 and s["ok"] and s["user"] == "u_lina"
    sid = s["session"]
    st, m = call(api, "POST", "/message", {
        "session": sid, "text": "Pay my November tuition and rent, but keep at least 600 euros for living expenses."})
    assert st == 200 and [a["type"] for a in m["actions"]] == ["fx", "payment", "payment"]
    assert "Cost of this exchange: 11.96 USD (0.50%)" in m["assistant_text"]
    assert any(e["kind"] == "prompt_injection" for e in m["events"])
    for otp in (None, None, "246810"):
        st, c = call(api, "POST", "/confirm", {"session": sid, "action": "last", **({"otp": otp} if otp else {})})
        assert st == 200 and c["actions"][0]["status"] == "executed", c
    st, ov = call(api, "GET", f"/overview?session={sid}")
    assert st == 200 and ov["balances"] == {"EUR": "600.00", "USD": "196.74"}
    assert ov["receipt_ids"] == ["rcpt_0001", "rcpt_0002", "rcpt_0003"] and ov["chain_valid"] is True
    assert "246810" not in json.dumps(ov) and "****0143" in json.dumps(ov)


def test_reject_leaves_the_balances_alone(api):
    _, s = call(api, "POST", "/session", {})
    sid = s["session"]
    call(api, "POST", "/message", {"session": sid, "text": "send 100 euros to jean"})
    st, r = call(api, "POST", "/reject", {"session": sid})
    assert st == 200 and r["actions"][0]["status"] == "rejected_by_user" and r["balances"]["EUR"] == "450.00"
    assert r["receipts"] == []


def test_wrong_otp_is_refused_and_the_step_keeps_waiting(api):
    _, s = call(api, "POST", "/session", {"user": "u_lina"})
    sid = s["session"]
    call(api, "POST", "/message", {"session": sid, "text": "send 100 euros to jean"})
    st, r = call(api, "POST", "/confirm", {"session": sid, "action": "last", "otp": "111111"})
    assert st == 200 and r["actions"][0]["status"] == "awaiting_confirmation" and r["balances"]["EUR"] == "450.00"
    assert any(e["kind"] == "step_up_failed" for e in r["events"]) and r["receipts"] == []
    assert "111111" not in json.dumps(r)


def test_bad_json_is_a_400(api):
    st, b = call(api, "POST", "/message", raw=b"{not json")
    assert st == 400 and b["ok"] is False and "JSON" in b["error"]
    assert call(api, "POST", "/message", raw=b"[1, 2]")[0] == 400
    assert call(api, "POST", "/message", raw=b"\xff\xfe")[0] == 400


def test_field_validation(api):
    _, s = call(api, "POST", "/session", {})
    sid = s["session"]
    assert call(api, "POST", "/message", {"session": sid, "text": 42})[0] == 400
    assert call(api, "POST", "/message", {"session": ["x"], "text": "hi"})[0] == 404
    assert call(api, "POST", "/confirm", {"session": sid, "otp": 123456})[0] == 400
    assert call(api, "POST", "/confirm", {"session": sid, "action": "last"})[0] == 409      # nothing waiting
    assert call(api, "POST", "/confirm", {"session": sid, "action": "act_nope"})[0] == 404
    assert call(api, "POST", "/session", {"user": "u_nobody"})[0] == 400


def test_oversize_body_is_a_413(api):
    big = json.dumps({"session": "x", "text": "a" * (MAX_BODY_BYTES + 10)}).encode("utf-8")
    st, b = call(api, "POST", "/message", raw=big)
    assert st == 413 and "larger" in b["error"]


def test_unknown_session_is_a_404(api):
    assert call(api, "POST", "/message", {"session": "s_999_deadbeef", "text": "hi"})[0] == 404
    assert call(api, "GET", "/overview?session=s_999_deadbeef")[0] == 404
    assert call(api, "GET", "/overview")[0] == 404
    assert call(api, "POST", "/confirm", {"session": "nope", "action": "last", "otp": "246810"})[0] == 404


def test_content_type_methods_and_paths(api):
    assert call(api, "POST", "/message", {"session": "x", "text": "hi"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert call(api, "GET", "/message")[0] == 405
    assert call(api, "POST", "/health", {})[0] == 405
    assert call(api, "GET", "/nothing")[0] == 404
    assert call(api, "PUT", "/message", {})[0] == 405


def test_missing_content_length_is_a_411(api):
    host, port = api.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(b"POST /message HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n\r\n")
        assert b" 411 " in sock.recv(4096).split(b"\r\n")[0] + b" "


def test_foreign_host_header_is_refused(api):
    assert call(api, "GET", "/health", headers={"Host": "evil.example"})[0] == 403


def test_token_is_enforced_when_configured():
    server = make_server("127.0.0.1", 0, Harness(), token="s3cret")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://%s:%d" % server.server_address[:2]
    try:
        assert call(base, "GET", "/health")[0] == 401
        assert call(base, "GET", "/health", headers={"Authorization": "Bearer wrong"})[0] == 401
        st, b = call(base, "GET", "/health", headers={"Authorization": "Bearer s3cret"})
        assert st == 200 and "s3cret" not in json.dumps(b)
    finally:
        server.shutdown()
        server.server_close()


def test_non_loopback_host_needs_an_explicit_opt_in():
    with pytest.raises(ValueError):
        make_server("0.0.0.0", 0)
    make_server("0.0.0.0", 0, allow_remote=True).server_close()


def test_responses_carry_no_traceback_or_secret(api):
    _, s = call(api, "POST", "/session", {})
    st, r = call(api, "POST", "/message", {"session": s["session"], "text": "my code is 246810, send 20 euros to mom"})
    blob = json.dumps(r)
    assert "Traceback" not in blob and "BG_RECEIPT_KEY" not in blob
