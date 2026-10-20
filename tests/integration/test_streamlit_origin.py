"""Integration: a real `streamlit run` process. A browser page on another site must not be able to open the app's
WebSocket (cross-site WebSocket hijacking), and a forged Host header must be refused (DNS rebinding). Measured on
Streamlit 1.66 with the repo's .streamlit/config.toml; kept as a test so an upgrade or a config change cannot silently
remove the protection. Needs no model and no network beyond loopback; takes about 10 seconds."""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration
CRLF = chr(13) + chr(10)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py"), "--server.headless", "true",
         "--server.port", str(port), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.time() + 60
        while time.time() < end:
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2).read() == b"ok":
                    break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("streamlit did not start")
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def _status(port: int, origin: str | None, host: str | None = None) -> int:
    headers = {"Host": host} if host else {}
    try:
        with connect(f"ws://127.0.0.1:{port}/_stcore/stream", origin=origin, additional_headers=headers,
                     open_timeout=10):
            return 101
    except InvalidStatus as exc:
        return exc.response.status_code


def _lan_ip() -> str | None:
    """This machine's address on the local network (no packet is sent), or None when it only has loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") else ip


def test_the_app_is_not_reachable_through_the_machines_network_address(app):
    ip = _lan_ip()
    if ip is None:
        pytest.skip("no network address other than loopback on this machine")
    with pytest.raises(OSError):
        socket.create_connection((ip, app), timeout=3)
    socket.create_connection(("127.0.0.1", app), timeout=3).close()


def test_same_site_origins_are_accepted(app):
    assert _status(app, f"http://localhost:{app}") == 101
    assert _status(app, f"http://127.0.0.1:{app}") == 101


@pytest.mark.parametrize("origin", ["https://evil.example", "http://evil.example", "null"])
def test_a_page_on_another_site_cannot_open_the_websocket(app, origin):
    assert _status(app, origin) == 403


def _raw_handshake(port: int, host: str, origin: str | None) -> int:
    """One WebSocket upgrade with exactly one Host header (the websockets client sends two when asked to forge one,
    and Streamlit refuses that with a 400 whatever its configuration says, which would hide a missing allow-list)."""
    import base64
    import os
    lines = ["GET /_stcore/stream HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}", "Sec-WebSocket-Version: 13"]
    if origin:
        lines.append(f"Origin: {origin}")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall((CRLF.join(lines) + CRLF + CRLF).encode())
        return int(sock.recv(200).split(CRLF.encode())[0].split()[1])


@pytest.mark.parametrize("origin", [None, "http://evil.example:{p}", "http://localhost:{p}"])
def test_a_forged_host_header_is_refused(app, origin):
    """DNS rebinding: the browser sends the attacker's name as Host. Refused with 403 (measured; the allow-list in
    .streamlit/config.toml is what does it), even when the Origin is the app's own."""
    assert _raw_handshake(app, f"evil.example:{app}", origin.format(p=app) if origin else None) == 403
    assert _raw_handshake(app, f"localhost:{app}", None) == 101          # the same request with the real name is accepted
