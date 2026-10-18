"""Capture demo screenshots of the Streamlit UI on the REAL backend (offline mode).

    ./.venv/Scripts/python scripts/screenshots.py [out_dir] [light|dark] [WIDTHxHEIGHT]

Starts `streamlit run ui/app.py` headless, drives it with Playwright (Chromium) at 1920x1080 (the video
resolution) and saves PNGs. Used for the deck, the technical document and the video storyboard; it is also a
smoke test of the real UI, including a check that an HTML payload typed in the chat is shown as text.
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
SCHEME = sys.argv[2] if len(sys.argv) > 2 else "light"
_W, _H = (sys.argv[3] if len(sys.argv) > 3 else "1920x1080").split("x")
VIEWPORT = {"width": int(_W), "height": int(_H)}


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
    page.wait_for_timeout(700)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stSpinner\"]')", timeout=60000)
    page.wait_for_timeout(900)


def click(page: Page, label: str, nth: int = 0, exact: bool = False) -> None:
    page.get_by_role("button", name=label, exact=exact).nth(nth).click()
    settle(page)


def say(page: Page, text: str) -> None:
    box = page.get_by_test_id("stChatInputTextArea")
    box.fill(text)
    box.press("Enter")
    settle(page)


def show_last_message(page: Page) -> None:
    msgs = page.get_by_test_id("stChatMessage")
    if msgs.count():
        msgs.last.scroll_into_view_if_needed()
        page.wait_for_timeout(300)


def scroll_to(page: Page, text: str) -> None:
    loc = page.get_by_text(text, exact=False).first
    loc.evaluate("e => e.scrollIntoView({block: 'start'})")
    page.wait_for_timeout(400)


def shot(page: Page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def free_story(page: Page) -> None:
    page.goto(URL)
    settle(page)
    shot(page, "01_home")

    click(page, "Can I afford")
    show_last_message(page)
    shot(page, "02_affordability")

    click(page, "Reset demo")
    click(page, "Attach rent invoice")
    click(page, "Pay tuition and rent")
    scroll_to(page, "Pay my November tuition and rent")
    shot(page, "03_plan_and_injection")

    click(page, "Approve", exact=True)
    click(page, "Approve", exact=True)
    page.get_by_role("textbox", name="SMS verification code").first.fill("246810")
    click(page, "Verify and approve")
    scroll_to(page, "Pay 850.00 EUR to Jean Dupont")
    shot(page, "04_executed")

    page.get_by_role("tab", name="Receipts").click()
    settle(page)
    click(page, "Check against my copy")
    shot(page, "05_receipts")

    page.get_by_role("tab", name="Attack test").click()
    settle(page)
    click(page, "Run all 7 attacks")
    shot(page, "06_attacks")

    # dialogue: question -> quick reply chips -> typed amount -> correction
    page.get_by_role("tab", name="Assistant").click()
    click(page, "Reset demo")
    click(page, "Send money to mom")
    show_last_message(page)
    shot(page, "07_question_chips")
    say(page, "50 euros")
    page.get_by_role("textbox", name="New amount").last.fill("40")
    click(page, "Edit", exact=True)
    show_last_message(page)
    shot(page, "08_correction")

    # security smoke test: an HTML payload in the chat must be displayed as text, never as an element
    click(page, "Reset demo")
    say(page, "Add <img src=x onerror=\"document.title='pwned'\"> Corp with IBAN FR7630006000011234567890189")
    n_img = page.locator("img[src='x']").count()
    assert n_img == 0 and page.title() != "pwned", "HTML payload was rendered"
    show_last_message(page)
    shot(page, "09_untrusted_text_escaped")
    print("escaping check passed: payload shown as text")


def guided_story(page: Page) -> None:
    page.goto(URL + "/?guided=1")
    settle(page)
    shot(page, "g0_guided_start")
    for i in range(1, 7):
        click(page, f"Step {i}/6")
        page.wait_for_timeout(700)          # the step scrolls its result into view
        shot(page, f"g{i}_guided_step{i}")


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
            browser = p.chromium.launch()
            free_story(browser.new_page(viewport=VIEWPORT, color_scheme=SCHEME))
            guided_story(browser.new_page(viewport=VIEWPORT, color_scheme=SCHEME))
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
