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
    for a, otp in zip(m["actions"], (None, None, "246810")):                    # the approval carries the hash shown
        st, c = call(api, "POST", "/confirm", {"session": sid, "action": a["id"], "params_hash": a["params_hash"],
                                               **({"otp": otp} if otp else {})})
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
    _, m = call(api, "POST", "/message", {"session": sid, "text": "send 100 euros to jean"})
    st, r = call(api, "POST", "/confirm", {"session": sid, "action": "last", "otp": "111111",
                                           "params_hash": m["actions"][0]["params_hash"]})
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
    assert call(api, "POST", "/confirm", {"session": sid, "action": "last"})[0] == 400      # the hash is required
    assert call(api, "POST", "/confirm", {"session": sid, "action": "last", "params_hash": "abc"})[0] == 409   # nothing waiting
    assert call(api, "POST", "/confirm", {"session": sid, "action": "act_nope", "params_hash": "abc"})[0] == 404
    assert call(api, "POST", "/session", {"user": "u_nobody"})[0] == 400


def test_oversize_body_is_a_413(api):
    big = json.dumps({"session": "x", "text": "a" * (MAX_BODY_BYTES + 10)}).encode("utf-8")
    st, b = call(api, "POST", "/message", raw=big)
    assert st == 413 and "larger" in b["error"]


def test_unknown_session_is_a_404(api):
    assert call(api, "POST", "/message", {"session": "s_999_deadbeef", "text": "hi"})[0] == 404
    assert call(api, "GET", "/overview?session=s_999_deadbeef")[0] == 404
    assert call(api, "GET", "/overview")[0] == 404
    assert call(api, "POST", "/confirm", {"session": "nope", "action": "last", "otp": "246810", "params_hash": "x"})[0] == 404


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


def test_non_loopback_host_needs_an_explicit_opt_in_and_a_token(monkeypatch):
    monkeypatch.delenv("BG_API_TOKEN", raising=False)
    with pytest.raises(ValueError):
        make_server("0.0.0.0", 0)
    with pytest.raises(ValueError):                                  # APPSEC-6: no unauthenticated remote mode
        make_server("0.0.0.0", 0, allow_remote=True)
    make_server("0.0.0.0", 0, allow_remote=True, token="t0ken-for-test").server_close()


def test_responses_carry_no_traceback_or_secret(api):
    _, s = call(api, "POST", "/session", {})
    st, r = call(api, "POST", "/message", {"session": s["session"], "text": "my code is 246810, send 20 euros to mom"})
    blob = json.dumps(r)
    assert "Traceback" not in blob and "BG_RECEIPT_KEY" not in blob


# ------------------------------------------------------------------------------------------- phase 2 hardening
def _serve(**kw):
    server = make_server("127.0.0.1", 0, Harness(), **kw)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def test_session_ids_are_random_and_the_internal_id_is_not_accepted(api):
    ids = {call(api, "POST", "/session", {})[1]["session"] for _ in range(5)}
    assert len(ids) == 5 and all(len(i) >= 20 and not i.startswith("s_") for i in ids)
    assert call(api, "POST", "/message", {"session": "s_1_cb8ccc36", "text": "hi"})[0] == 404     # the old, guessable form


def test_an_approval_is_bound_to_the_hash_shown_with_the_action(api):
    _, s = call(api, "POST", "/session", {})
    sid = s["session"]
    _, m = call(api, "POST", "/message", {"session": sid, "text": "send 50 euros to mom"})
    a = m["actions"][0]
    assert a["status"] == "awaiting_confirmation" and len(a["params_hash"]) == 64
    st, b = call(api, "POST", "/confirm", {"session": sid, "action": a["id"], "params_hash": "0" * 64})
    assert st == 409 and "does not match" in b["error"]
    _, ov = call(api, "GET", f"/overview?session={sid}")
    assert ov["balances"]["EUR"] == "450.00"                                     # nothing moved
    st, c = call(api, "POST", "/confirm", {"session": sid, "action": a["id"], "params_hash": a["params_hash"]})
    assert st == 200 and c["actions"][0]["status"] == "executed"


def test_a_customer_token_opens_and_uses_only_its_own_customers_sessions():
    lina, other = "t" * 20 + "-lina", "t" * 20 + "-other"
    server, base = _serve(user_tokens={lina: "u_lina", other: "u_other"})
    try:
        as_lina, as_other = {"Authorization": f"Bearer {lina}"}, {"Authorization": f"Bearer {other}"}
        assert call(base, "POST", "/session", {}, headers={})[0] == 401
        assert call(base, "POST", "/session", {}, headers={"Authorization": "Bearer nope"})[0] == 401
        st, s = call(base, "POST", "/session", {}, headers=as_lina)                 # no user given: the token's customer
        assert st == 201 and s["user"] == "u_lina"
        assert call(base, "POST", "/session", {"user": "u_other"}, headers=as_lina)[0] == 403
        _, so = call(base, "POST", "/session", {}, headers=as_other)
        assert so["user"] == "u_other"
        # a token never reaches another customer's session, and cannot tell that it exists
        assert call(base, "POST", "/message", {"session": so["session"], "text": "hi"}, headers=as_lina)[0] == 404
        assert call(base, "GET", f"/overview?session={so['session']}", headers=as_lina)[0] == 404
        assert call(base, "GET", f"/overview?session={s['session']}", headers=as_lina)[0] == 200
    finally:
        server.shutdown(); server.server_close()


def test_the_session_table_is_bounded(monkeypatch):
    import bg.http_api as api_mod
    monkeypatch.setattr(api_mod, "MAX_SESSIONS", 3)
    server, base = _serve()
    try:
        codes = [call(base, "POST", "/session", {})[0] for _ in range(4)]
        assert codes == [201, 201, 201, 429]
    finally:
        server.shutdown(); server.server_close()


def test_user_token_spec_is_validated():
    from bg.http_api import parse_user_tokens
    assert parse_user_tokens("a" * 16 + "=u_lina") == {"a" * 16: "u_lina"}
    for bad in ("short=u_lina", "a" * 16 + "=u_nobody", "a" * 16):
        with pytest.raises(ValueError):
            parse_user_tokens(bad)
    with pytest.raises(ValueError):
        make_server("0.0.0.0", 0, Harness(), allow_remote=True, token=None, user_tokens={})
    make_server("0.0.0.0", 0, Harness(), allow_remote=True, user_tokens={"a" * 16: "u_lina"}).server_close()


def test_the_code_lock_freezes_a_one_tap_approval_too(api):
    """Three wrong codes on the rent card; an R2 one-tap on another card is refused while the lock is on."""
    _, s = call(api, "POST", "/session", {"user": "u_lina"})
    sid = s["session"]
    _, rent = call(api, "POST", "/message", {"session": sid, "text": "send 100 euros to jean"})       # R3: needs a code
    r3 = rent["actions"][0]
    for _ in range(3):
        call(api, "POST", "/confirm", {"session": sid, "action": r3["id"], "otp": "000000", "params_hash": r3["params_hash"]})
    _, mom = call(api, "POST", "/message", {"session": sid, "text": "send 20 euros to mom"})          # R2: one tap
    r2 = next(a for a in mom["actions"] if a["status"] == "awaiting_confirmation")
    st, out = call(api, "POST", "/confirm", {"session": sid, "action": r2["id"], "params_hash": r2["params_hash"]})
    assert st == 200 and out["actions"][0]["status"] != "executed" and out["balances"]["EUR"] == "450.00"
    assert "locked" in out["assistant_text"].lower()


def test_one_customer_cannot_use_up_the_session_budget_of_the_others(monkeypatch):
    """Independent review P2-6: with a server-wide cap only, a token holder could lock every other customer out."""
    import bg.http_api as api_mod
    monkeypatch.setattr(api_mod, "MAX_SESSIONS_PER_USER", 2)
    lina, other = "t" * 20 + "-lina", "t" * 20 + "-other"
    server, base = _serve(user_tokens={lina: "u_lina", other: "u_other"})
    try:
        as_lina, as_other = {"Authorization": f"Bearer {lina}"}, {"Authorization": f"Bearer {other}"}
        assert [call(base, "POST", "/session", {}, headers=as_other)[0] for _ in range(3)] == [201, 201, 429]
        assert call(base, "POST", "/session", {}, headers=as_lina)[0] == 201
    finally:
        server.shutdown(); server.server_close()
