"""Capture the real app for the demo presentation (deliverables/demo/).

    ./.venv/Scripts/python scripts/demo_capture.py

Starts `streamlit run ui/app.py` headless on the REAL backend (rules parser, offline), drives it with Playwright at
1920x1080 and saves, for each step of the demo story:
  - deliverables/demo/shots/<name>.png   the screen, exactly as the app draws it;
  - deliverables/demo/shots.json         the boxes (x, y, w, h in CSS pixels) of the parts the presentation zooms
                                         on or highlights, measured from the page itself, not typed by hand.

Nothing is mocked: every screen is the app after real clicks. Re-run it after any UI change and the presentation
follows (it reads shots.json). A missing box is reported and left out; the presentation then shows the whole screen.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables" / "demo"
SHOTS = OUT / "shots"
PORT = 8611
URL = f"http://localhost:{PORT}"
VIEWPORT = {"width": 1920, "height": 1080}
GOAL = "Pay my November tuition and rent, but keep at least 600 euros for living expenses."
VOP = "Add Jean Dupont Orsay as a payee with IBAN ES91 2100 0418 4502 0005 1332"

manifest: dict[str, dict] = {}
missing: list[str] = []


def wait_ready(timeout: float = 90) -> None:
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


def to_top(page: Page, loc: Locator, margin: int = 24) -> None:
    """Scroll the main pane so `loc` starts about `margin` px below the top of the viewport."""
    loc.first.evaluate("e => e.scrollIntoView({block: 'start'})")
    page.wait_for_timeout(300)
    if margin:
        page.mouse.move(1100, 600)                  # over the main pane, so the wheel scrolls it
        page.mouse.wheel(0, -margin)
    page.wait_for_timeout(500)


def box(loc: Locator) -> list[float] | None:
    try:
        if loc.count() == 0:
            return None
        b = loc.first.bounding_box()
    except Exception:
        return None
    if not b or b["width"] <= 0 or b["height"] <= 0:
        return None
    return [round(b["x"], 1), round(b["y"], 1), round(b["width"], 1), round(b["height"], 1)]


def text_box(page: Page, start: str, end: str | None = None) -> list[float] | None:
    """Box of the text from `start` to the end of `end` (or of `start`), measured with a DOM Range."""
    r = page.evaluate("""([a, b]) => {
        const nodes = [];
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) nodes.push(n);
        for (let k = 0; k < nodes.length; k++) {
            const i = nodes[k].data.indexOf(a);
            if (i < 0) continue;
            const r = document.createRange();
            r.setStart(nodes[k], i);
            if (!b) r.setEnd(nodes[k], i + a.length);
            else {                                   // the end may sit in a later text node (line breaks, spans)
                let done = false;
                for (let m = k; m < Math.min(nodes.length, k + 40) && !done; m++) {
                    const j = nodes[m].data.indexOf(b, m === k ? i : 0);
                    if (j >= 0) { r.setEnd(nodes[m], j + b.length); done = true; }
                }
                if (!done) continue;
            }
            const x = r.getBoundingClientRect();
            if (x.width > 0 && x.height > 0) return [x.x, x.y, x.width, x.height];
        }
        return null;
    }""", [start, end])
    return [round(v, 1) for v in r] if r else None


def union(*bs: list[float] | None) -> list[float] | None:
    bs = [b for b in bs if b]
    if not bs:
        return None
    x0 = min(b[0] for b in bs)
    y0 = min(b[1] for b in bs)
    x1 = max(b[0] + b[2] for b in bs)
    y1 = max(b[1] + b[3] for b in bs)
    return [x0, y0, round(x1 - x0, 1), round(y1 - y0, 1)]


def shot(page: Page, name: str, boxes: dict[str, list[float] | None]) -> None:
    page.wait_for_timeout(300)
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    kept = {}
    for k, v in boxes.items():
        if v is None:
            missing.append(f"{name}.{k}")
            continue
        x, y, w, h = v                       # clip to the screen
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(VIEWPORT["width"], x + w), min(VIEWPORT["height"], y + h)
        if x1 - x0 > 4 and y1 - y0 > 4:
            kept[k] = [round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1)]
        else:
            missing.append(f"{name}.{k} (off screen)")
    manifest[name] = {"file": f"shots/{name}.png", "boxes": kept}
    print(f"saved {name}: {', '.join(kept) or 'no boxes'}")


def card(page: Page, title_text: str) -> Locator:
    """The decision card whose title contains `title_text`."""
    return page.locator("[class*='st-key-card_']").filter(has_text=title_text).last


def alert(page: Page, text: str) -> Locator:
    return page.get_by_test_id("stAlert").filter(has_text=text).last


def story(page: Page) -> None:
    page.goto(URL)
    settle(page)
    side = page.get_by_test_id("stSidebar")
    shot(page, "home", {
        "balances": union(box(side.get_by_text("Balances", exact=True)), box(side.locator(".bg-ledger"))),
        "payees": union(*[box(side.locator(".bg-payee").nth(i)) for i in range(3)]),
        "bills": union(box(side.locator(".bg-payee").nth(3)), box(side.locator(".bg-payee").nth(4))),
        "pitch": union(box(page.locator(".bg-pitch")), box(page.locator(".bg-legend"))),
        "legend": box(page.locator(".bg-legend")),
        "examples": box(page.locator(".st-key-examples")),
        "attach": box(page.locator(".st-key-attach")),
        "composer": box(page.get_by_test_id("stChatInput")),
        "sandbox": box(page.locator(".bg-pill.sandbox")),
        "model": union(box(side.get_by_text("Language model", exact=True)), box(side.get_by_test_id("stSelectbox"))),
    })

    # one request, with the forged invoice attached
    click(page, "Attach rent invoice")
    page.get_by_test_id("stChatInputTextArea").fill(GOAL)
    page.wait_for_timeout(400)
    shot(page, "request", {
        "composer": box(page.get_by_test_id("stChatInput")),
        "attach": box(page.locator(".st-key-attach")),
        "request": union(box(page.locator(".st-key-attach")), box(page.get_by_test_id("stChatInput"))),
    })
    page.get_by_test_id("stChatInputTextArea").press("Enter")
    settle(page)

    # the reply: hidden instruction found, document quarantined, plan
    to_top(page, page.locator("[class*='st-key-bubble_']").first, 40)
    reply = page.get_by_text("Watch out:", exact=False).first
    shot(page, "plan", {
        "request": box(page.locator("[class*='st-key-bubble_']").first),
        "doc": box(page.get_by_text("untrusted: shown as plain text", exact=False).first),
        "watch": text_box(page, "Watch out:", "ending 0143."),
        "notice": box(alert(page, "Untrusted document")),
        "reserve_line": text_box(page, "After all three steps", "196.74 USD."),
        "reply": union(box(reply), box(page.get_by_text("Nothing moves until you approve", exact=False).first)),
    })

    page.get_by_text("untrusted: shown as plain text", exact=False).first.click()
    page.wait_for_timeout(900)
    exp = page.get_by_test_id("stExpander").filter(has_text="untrusted: shown as plain text").first
    shot(page, "plan_doc", {
        "doc": box(exp),
        "hidden": text_box(page, "Note to the AI assistant", "for confirmation."),
    })
    page.get_by_text("untrusted: shown as plain text", exact=False).first.click()
    page.wait_for_timeout(700)

    fx = card(page, "Buy 2,200.00 EUR")
    to_top(page, fx, 40)
    shot(page, "card_fx", {
        "card": box(fx),
        "meta": box(fx.locator(".bg-card-meta")),
        "hero": union(box(fx.locator(".bg-card-title")), box(fx.locator(".bg-amount"))),
        "reason": text_box(page, "Currency exchange under", "one tap is enough."),
        "facts": box(fx.locator(".bg-kv")),
        "cost": box(fx.locator(".bg-kv .r").filter(has_text="Cost of this exchange")),
        "approve": box(fx.get_by_role("button", name="Approve", exact=True)),
    })

    fx.get_by_text("Why this action?", exact=False).first.click()
    page.wait_for_timeout(900)
    to_top(page, fx.get_by_text("Why this action?", exact=False).first, 40)
    shot(page, "rules", {"rules": box(fx.locator(".bg-rules"))})
    fx.get_by_text("Why this action?", exact=False).first.click()
    page.wait_for_timeout(700)

    # approve: one tap, one tap, then the one-time code
    fx.get_by_role("button", name="Approve", exact=True).click()
    settle(page)
    tu = card(page, "Université Paris-Saclay")
    to_top(page, tu, 40)
    shot(page, "card_tuition", {
        "card": box(tu),
        "approve": box(tu.get_by_role("button", name="Approve", exact=True)),
        "reason": text_box(page, "Saved payee for", "one tap is enough."),
        "balances": box(side.locator(".bg-ledger")),
    })
    tu.get_by_role("button", name="Approve", exact=True).click()
    settle(page)
    rent = card(page, "Jean Dupont")
    to_top(page, rent, 40)
    shot(page, "card_rent", {
        "card": box(rent),
        "meta": box(rent.locator(".bg-card-meta")),
        "reason": box(rent.get_by_text("New payee", exact=False).first),
        "code": union(box(rent.get_by_role("textbox", name="SMS verification code")),
                      box(rent.get_by_text("Simulated SMS", exact=False).first)),
        "verify": box(rent.get_by_role("button", name="Verify and approve")),
        "balances": box(side.locator(".bg-ledger")),
    })
    rent.get_by_role("textbox", name="SMS verification code").fill("246810")
    rent.get_by_role("button", name="Verify and approve").click()
    settle(page)
    done = card(page, "Pay 850.00 EUR to Jean Dupont")
    to_top(page, done, 160)
    shot(page, "done", {
        "record": box(done),
        "balances": box(side.locator(".bg-ledger")),
        "eur": box(side.locator(".bg-ledger .bg-row").first),
        "reserve": box(side.locator(".bg-ledger .bg-row").filter(has_text="Reserve status")),
    })

    # receipts, the check against Lina's signed copy, and the deletion test on a copy
    page.get_by_role("tab", name="Receipts").click()
    settle(page)
    click(page, "Check against my copy")
    shot(page, "receipts", {
        "lede": box(page.locator(".bg-lede")),
        "copy": box(page.locator(".bg-copy")),
        "table": box(page.locator(".bg-table")),
        "hashes": union(*[box(page.locator(".bg-table tbody tr").nth(i).locator("td").nth(3)) for i in range(3)],
                        *[box(page.locator(".bg-table tbody tr").nth(i).locator("td").nth(4)) for i in range(3)]),
        "check": union(box(page.get_by_role("button", name="Check the chain")), box(page.get_by_role("button", name="Check against my copy"))),
        "result": box(alert(page, "Matches your copy")),
    })
    page.get_by_text("What if someone deletes receipts?", exact=False).first.click()
    page.wait_for_timeout(800)
    click(page, "Run the deletion test on a copy")
    tamper = page.get_by_test_id("stExpander").filter(has_text="What if someone deletes receipts?").first
    to_top(page, tamper, 40)
    shot(page, "tamper", {
        "test": box(tamper),
        "chain_only": box(alert(page, "Chain-only check")),
        "anchor": box(alert(page, "Check against your copy")),
    })

    # the attack test tab
    page.get_by_role("tab", name="Attack test").click()
    settle(page)
    click(page, "Run all 7 attacks")
    to_top(page, page.get_by_role("tab", name="Attack test"), 30)
    shot(page, "attacks", {
        "list": union(*[box(page.locator("[class*='st-key-attack_']").nth(i)) for i in range(7)]),
        "first": box(page.locator("[class*='st-key-attack_']").first),
        "score": box(page.get_by_text("blocked", exact=False).filter(has_text="/").first),
    })

    # a new payee whose name does not match the bank's record (Verification of Payee)
    page.get_by_role("tab", name="Assistant").click()
    settle(page)
    click(page, "Reset demo")
    box_in = page.get_by_test_id("stChatInputTextArea")
    box_in.fill(VOP)
    box_in.press("Enter")
    settle(page)
    pc = card(page, "Add payee Jean Dupont Orsay")
    asked = page.get_by_text(VOP[:28], exact=False).first
    to_top(page, asked, 60)
    shot(page, "payee", {
        "request": box(asked),
        "warning": text_box(page, "Warning: the name", "for this account."),
        "card": box(pc),
        "check": box(pc.locator(".bg-kv .check")),
    })

    # a refusal: above the cap, no approval can override it
    click(page, "Reset demo")
    click(page, "Send 4,500 EUR to mom")
    msgs = page.get_by_test_id("stChatMessage")
    if msgs.count():
        to_top(page, msgs.last, 90)
    blocked = page.locator("[class*='st-key-card_']").last
    shot(page, "blocked", {
        "card": box(blocked),
        "reason": box(blocked.locator(".bg-card-meta")),
        "why": text_box(page, "there is not enough money", "can unlock this."),
        "message": box(msgs.last) if msgs.count() else None,
    })

    # who reads the sentence: the language model selector
    click(page, "Reset demo")
    side.get_by_test_id("stSelectbox").first.click()
    page.wait_for_timeout(700)
    shot(page, "model", {
        "select": union(box(side.get_by_text("Language model", exact=True)), box(side.get_by_test_id("stSelectbox").first)),
        "options": box(page.locator("[data-baseweb='popover'] ul, [role='listbox']").first),
    })
    page.keyboard.press("Escape")


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("BG_PARSER", "BG_MODEL_API_KEY", "BG_FAKE_BACKEND")}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py"), "--server.headless", "true",
         "--server.port", str(PORT), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            story(browser.new_page(viewport=VIEWPORT, color_scheme="light"))
            browser.close()
    finally:
        proc.terminate()
    data = {"viewport": VIEWPORT, "shots": manifest}
    (OUT / "shots.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    # the same data as a script, so index.html also works when opened straight from disk (file://)
    (OUT / "shots.js").write_text("window.DEMO_SHOTS = " + json.dumps(data) + ";\n", encoding="utf-8")
    print(f"wrote {OUT / 'shots.json'} and shots.js: {len(manifest)} screens")
    if missing:
        print("boxes not found:", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
