"""Capture demo screenshots of the Streamlit UI on the REAL backend (offline mode).

    ./.venv/Scripts/python scripts/screenshots.py [out_dir]

Starts `streamlit run ui/app.py` headless, drives it with Playwright (Chromium) and saves PNGs.
Used for the deck and the technical document; also a smoke test of the real UI.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "deliverables" / "screenshots"
PORT = 8599
URL = f"http://localhost:{PORT}"


def wait_ready(timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if urllib.request.urlopen(f"{URL}/_stcore/health", timeout=2).read() == b"ok":
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("streamlit did not start")


def settle(page: Page) -> None:
    page.wait_for_timeout(600)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=30000)
    page.wait_for_timeout(500)


def click(page: Page, label: str, nth: int = 0) -> None:
    page.get_by_role("button", name=label, exact=False).nth(nth).click()
    settle(page)


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print("saved", name)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "BG_FAKE_BACKEND")}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py"), "--server.headless", "true",
         "--server.port", str(PORT), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready()
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1600, "height": 1000})
            page.goto(URL)
            settle(page)
            shot(page, "01_home")

            click(page, "Can I afford")
            shot(page, "02_affordability")

            click(page, "Reset demo")
            click(page, "Attach rent invoice")
            click(page, "Pay tuition and rent")
            shot(page, "03_plan_and_injection")

            click(page, "Approve")
            click(page, "Approve")
            page.get_by_role("textbox", name="SMS verification code").first.fill("246810")
            click(page, "Verify and approve")
            shot(page, "04_executed")

            page.get_by_role("tab", name="Proof").click()
            settle(page)
            click(page, "Verify chain against my copy")
            shot(page, "05_proof_of_intent")

            page.get_by_role("tab", name="Adversarial").click()
            settle(page)
            click(page, "Run all")
            shot(page, "06_adversarial")
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
