"""Record the real app for the demo video: one continuous screen recording of the live Streamlit app, with markers.

    ./.venv/Scripts/python scripts/demo_record.py

Starts `streamlit run ui/app.py` headless on the REAL backend (rules parser, offline, sandbox ledger) and drives it with
Playwright at human pace: a visible pointer moves to each control, text is typed, results are left on screen. Every
frame comes from Chromium's own screencast (no mock-up, no screenshot montage).

Writes to media/launch-video/public/rec/ (generated, not tracked):
  - demo.mp4       1920x1080, 30 fps, H.264 (CRF 16), the whole take
  - markers.json   {scene: {start, end}} in seconds of demo.mp4, plus element boxes (video pixels) for the zooms
The video composition (media/launch-video/src/demo/) cuts its scenes from these markers.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.async_api import Locator, Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "media" / "launch-video" / "public" / "rec"
FRAMES = ROOT / "out" / "rec_frames"
PORT = 8613
URL = f"http://localhost:{PORT}"
VIEW = {"width": 1600, "height": 900}
DSF = 1.2                                    # 1600x900 CSS px -> 1920x1080 video px; the UI reads at 120 %
GOAL = "Pay my November tuition and rent, but keep at least 600 euros for living expenses."
VOP = "Add Jean Dupont Orsay as a payee with IBAN ES91 2100 0418 4502 0005 1332"

# a pointer drawn in the page (headless Chromium has no visible cursor); it only follows the real mouse events
POINTER_JS = r"""
(() => {
  const mk = () => {
    if (document.getElementById('__rec_ptr')) return;
    const s = document.createElement('style');
    s.textContent = `#__rec_ptr{position:fixed;left:0;top:0;width:26px;height:34px;z-index:2147483647;pointer-events:none;
      transform:translate(-100px,-100px);transition:none}
      .__rec_ring{position:fixed;width:40px;height:40px;margin:-20px 0 0 -20px;border-radius:50%;border:3px solid #2D5BD7;
      z-index:2147483646;pointer-events:none;animation:__rr .5s ease-out forwards}
      @keyframes __rr{from{opacity:.75;transform:scale(.4)}to{opacity:0;transform:scale(1.4)}}`;
    document.head.appendChild(s);
    const p = document.createElement('div');
    p.id = '__rec_ptr';
    p.innerHTML = '<svg width="26" height="34" viewBox="0 0 20 26"><path d="M1.5 1.5 L1.5 20.5 L6.4 15.8 L9.7 23.4 L13 22 L9.8 14.6 L16.6 14.6 Z" fill="#16171A" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.body.appendChild(p);
    addEventListener('mousemove', e => { p.style.transform = `translate(${e.clientX - 2}px, ${e.clientY - 2}px)`; }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div'); r.className = '__rec_ring';
      r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.body.appendChild(r); setTimeout(() => r.remove(), 600);
    }, true);
  };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', mk); else mk();
})();
"""


class Recorder:
    def __init__(self) -> None:
        self.frames: list[tuple[float, str]] = []
        self.t0: float | None = None
        self.markers: dict[str, dict] = {}
        self.boxes: dict[str, list[dict]] = {}
        self.cur = (VIEW["width"] / 2, VIEW["height"] / 2)
        self.page: Page | None = None
        self.cdp = None

    # ---------------------------------------------------------------- screencast
    async def start(self, page: Page) -> None:
        self.page = page
        self.cdp = await page.context.new_cdp_session(page)
        n = 0

        def on_frame(ev: dict) -> None:
            nonlocal n
            ts = ev["metadata"]["timestamp"]
            if self.t0 is None:
                self.t0 = ts
            path = FRAMES / f"f{n:06d}.jpg"
            path.write_bytes(base64.b64decode(ev["data"]))
            self.frames.append((ts, path.name))
            n += 1
            asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]}))

        self.cdp.on("Page.screencastFrame", on_frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920,
                                                     "maxHeight": 1080, "everyNthFrame": 1})
        await page.wait_for_timeout(500)

    async def stop(self) -> float:
        await self.page.wait_for_timeout(400)
        await self.cdp.send("Page.stopScreencast")
        return self.now()

    def now(self) -> float:
        return (self.frames[-1][0] - self.t0) if self.frames else 0.0

    def wall(self) -> float:
        # screencast timestamps are wall-clock seconds; frames only arrive on change, so use time.time() for markers
        return time.time() - self.t0 if self.t0 else 0.0

    def mark(self, scene: str, edge: str) -> None:
        self.markers.setdefault(scene, {})[edge] = round(self.wall(), 3)
        print(f"  {edge:5s} {scene:12s} {self.wall():7.2f}s")

    async def box(self, scene: str, name: str, loc: Locator) -> None:
        try:
            b = await loc.first.bounding_box()
        except Exception:
            b = None
        if b and b["width"] > 0:
            self.boxes.setdefault(scene, []).append({"name": name, "t": round(self.wall(), 3),
                                                     "box": [round(v * DSF, 1) for v in (b["x"], b["y"], b["width"], b["height"])]})

    # ---------------------------------------------------------------- human-paced input
    async def move_to(self, x: float, y: float, speed: float = 900.0) -> None:
        dx, dy = x - self.cur[0], y - self.cur[1]
        dist = (dx * dx + dy * dy) ** 0.5
        steps = max(6, int(dist / speed * 60))
        await self.page.mouse.move(x, y, steps=steps)
        self.cur = (x, y)
        await self.page.wait_for_timeout(120)

    async def point(self, loc: Locator, dx: float = 0.0, dy: float = 0.0) -> None:
        b = await loc.first.bounding_box()
        await self.move_to(b["x"] + b["width"] / 2 + dx, b["y"] + b["height"] / 2 + dy)

    async def click(self, loc: Locator, settle_after: bool = True) -> None:
        await loc.first.scroll_into_view_if_needed()
        await self.point(loc)
        await self.page.wait_for_timeout(180)
        await self.page.mouse.down()
        await self.page.wait_for_timeout(70)
        await self.page.mouse.up()
        if settle_after:
            await settle(self.page)

    async def type(self, text: str, delay: int = 48) -> None:
        await self.page.keyboard.type(text, delay=delay)

    async def scroll(self, dy: float, step: float = 60.0) -> None:
        """Smooth wheel scroll of the main pane (the pointer must be over it)."""
        n = max(1, int(abs(dy) / step))
        for _ in range(n):
            await self.page.mouse.wheel(0, dy / n)
            await self.page.wait_for_timeout(16)
        await self.page.wait_for_timeout(250)

    async def scroll_to(self, loc: Locator, top: float = 90.0) -> None:
        """Bring `loc` to `top` CSS px below the top of the window, smoothly."""
        if self.cur[0] < 330:                               # the pointer must be over the main pane to scroll it
            await self.move_to(980, 520)
        b = await loc.first.bounding_box()
        if b is None:
            return
        await self.scroll(b["y"] - top)

    async def hold(self, ms: int) -> None:
        await self.page.wait_for_timeout(ms)


async def settle(page: Page) -> None:
    await page.wait_for_timeout(600)
    await page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60000)
    await page.wait_for_function("() => !document.querySelector('[data-testid=\"stSpinner\"]')", timeout=60000)
    await page.wait_for_timeout(500)


def card(page: Page, text: str) -> Locator:
    return page.locator("[class*='st-key-card_']").filter(has_text=text).last


def alert(page: Page, text: str) -> Locator:
    return page.get_by_test_id("stAlert").filter(has_text=text).last


async def story(r: Recorder, page: Page) -> None:
    side = page.get_by_test_id("stSidebar")
    chat = page.get_by_test_id("stChatInputTextArea")

    # --- account
    r.mark("account", "start")
    await r.hold(2600)
    await r.point(side.locator(".bg-ledger"), dx=-40)
    await r.box("account", "balances", side.locator(".bg-ledger"))
    await r.hold(4200)
    landlord = side.locator(".bg-payee").filter(has_text="Jean Dupont").first
    await r.point(landlord, dx=-30)
    await r.box("account", "payees", landlord)
    await r.hold(4600)
    r.mark("account", "end")

    # --- request: attach the forged invoice, type one sentence
    r.mark("request", "start")
    await r.click(page.get_by_role("button", name="Attach rent invoice"))
    await r.hold(500)
    await r.click(chat, settle_after=False)
    await r.type(GOAL, delay=44)
    await r.hold(700)
    await page.keyboard.press("Enter")
    await settle(page)
    r.mark("request", "end")

    # --- the reply: the hidden instruction found, the document quarantined
    r.mark("quarantine", "start")
    bubble = page.locator("[class*='st-key-bubble_']").first
    await r.scroll_to(bubble, 70)
    await r.box("quarantine", "notice", alert(page, "Untrusted document"))
    await r.point(page.get_by_text("Watch out:", exact=False).first, dx=-200)
    await r.hold(4200)
    await r.point(alert(page, "Untrusted document"), dx=-250)
    await r.hold(5200)
    r.mark("quarantine", "end")

    # --- the hidden note, shown as plain text
    r.mark("note", "start")
    exp_label = page.get_by_text("untrusted: shown as plain text", exact=False).first
    await r.click(exp_label, settle_after=False)
    await r.hold(900)
    exp = page.get_by_test_id("stExpander").filter(has_text="untrusted: shown as plain text").first
    await r.scroll_to(exp, 120)
    await r.box("note", "doc", exp)
    await r.hold(3800)
    await r.click(exp_label, settle_after=False)
    await r.hold(700)
    r.mark("note", "end")

    # --- the plan: three cards, the rules of one card
    r.mark("plan", "start")
    fx = card(page, "Buy 2,200.00 EUR")
    await r.scroll_to(fx, 70)
    await r.box("plan", "fx", fx)
    await r.point(fx.locator(".bg-amount"), dx=-60)
    await r.hold(3200)
    why = fx.get_by_text("Why this action?", exact=False).first
    await r.click(why, settle_after=False)
    await r.hold(900)
    await r.scroll_to(why, 70)
    await r.box("plan", "rules", fx.locator(".bg-rules"))
    await r.hold(4400)
    await r.click(why, settle_after=False)
    await r.hold(600)
    await r.scroll(420)
    await r.hold(2300)
    await r.scroll(420)
    await r.hold(2700)
    r.mark("plan", "end")

    # --- approvals: one tap, one tap
    r.mark("taps", "start")
    await r.scroll_to(fx, 70)
    await r.hold(600)
    await r.click(fx.get_by_role("button", name="Approve", exact=True))
    await r.hold(900)
    tu = card(page, "Université Paris-Saclay")
    await r.scroll_to(tu, 70)
    await r.hold(700)
    await r.click(tu.get_by_role("button", name="Approve", exact=True))
    await r.hold(900)
    r.mark("taps", "end")

    # --- the one-time code for the new landlord
    r.mark("code", "start")
    rent = card(page, "Jean Dupont")
    await r.scroll_to(rent, 70)
    await r.box("code", "rent", rent)
    await r.point(rent.get_by_text("New payee", exact=False).first, dx=-80)
    await r.hold(3600)
    box_code = rent.get_by_role("textbox", name="SMS verification code")
    await r.click(box_code, settle_after=False)
    await r.type("246810", delay=160)
    await r.hold(2400)
    await r.click(rent.get_by_role("button", name="Verify and approve"))
    r.mark("code", "end")

    # --- done: 600.00 EUR left
    r.mark("done", "start")
    done = card(page, "Pay 850.00 EUR to Jean Dupont")
    await r.scroll_to(done, 160)
    await r.hold(900)
    await r.point(side.locator(".bg-ledger .bg-row").first, dx=-40)
    await r.box("done", "eur", side.locator(".bg-ledger"))
    await r.hold(3000)
    r.mark("done", "end")

    # --- receipts and the check against her signed copy
    r.mark("receipts", "start")
    await r.click(page.get_by_role("tab", name="Receipts"))
    await r.hold(1200)
    await r.point(page.locator(".bg-table"), dy=-20)
    await r.hold(1500)
    await r.click(page.get_by_role("button", name="Check against my copy"))
    await r.box("receipts", "result", alert(page, "Matches your copy"))
    await r.point(alert(page, "Matches your copy"), dx=-300)
    await r.hold(3200)
    r.mark("receipts", "end")

    # --- the deletion test, on a copy
    r.mark("deletion", "start")
    await r.click(page.get_by_text("What if someone deletes receipts?", exact=False).first, settle_after=False)
    await r.hold(900)
    await r.click(page.get_by_role("button", name="Run the deletion test on a copy"))
    tamper = page.get_by_test_id("stExpander").filter(has_text="What if someone deletes receipts?").first
    await r.scroll_to(tamper, 70)
    await r.box("deletion", "test", tamper)
    await r.point(alert(page, "Chain-only check"), dx=-250)
    await r.hold(2200)
    await r.point(alert(page, "Check against your copy"), dx=-250)
    await r.hold(3200)
    r.mark("deletion", "end")

    # --- a correction is a new action
    await r.click(page.get_by_role("tab", name="Assistant"))
    await r.click(page.get_by_role("button", name="Reset demo"))
    r.mark("correction", "start")
    await r.click(chat, settle_after=False)
    await r.type("Send 50 euros to mom", delay=55)
    await r.hold(400)
    await page.keyboard.press("Enter")
    await settle(page)
    await r.hold(1800)
    await r.click(chat, settle_after=False)
    await r.type("Actually, make it 40", delay=55)
    await r.hold(400)
    await page.keyboard.press("Enter")
    await settle(page)
    msgs = page.get_by_test_id("stChatMessage")
    await r.scroll_to(msgs.last, 120)
    await r.box("correction", "reply", msgs.last)
    await r.hold(3800)
    r.mark("correction", "end")

    # --- a new payee whose name does not match the bank's record
    await r.click(page.get_by_role("button", name="Reset demo"))
    r.mark("payee", "start")
    await r.click(chat, settle_after=False)
    await r.type(VOP, delay=40)
    await r.hold(400)
    await page.keyboard.press("Enter")
    await settle(page)
    asked = page.get_by_text(VOP[:28], exact=False).first
    await r.scroll_to(asked, 80)
    pc = card(page, "Add payee Jean Dupont Orsay")
    await r.box("payee", "card", pc)
    await r.point(page.get_by_text("Warning: the name", exact=False).first, dx=-150)
    await r.hold(2600)
    await r.point(pc.locator(".bg-kv .check"), dx=-60)
    await r.hold(2800)
    r.mark("payee", "end")

    # --- above the cap: refused, nothing can unlock it
    await r.click(page.get_by_role("button", name="Reset demo"))
    r.mark("limit", "start")
    await r.click(page.get_by_role("button", name="Send 4,500 EUR to mom"))
    msgs = page.get_by_test_id("stChatMessage")
    await r.scroll_to(msgs.last, 90)
    blocked = page.locator("[class*='st-key-card_']").last
    await r.box("limit", "card", blocked)
    await r.point(blocked.locator(".bg-card-meta"), dx=-80)
    await r.hold(5600)
    r.mark("limit", "end")

    # --- seven known attacks, replayed live
    r.mark("attacks", "start")
    await r.click(page.get_by_role("tab", name="Attack test"))
    await r.hold(900)
    await r.click(page.get_by_role("button", name="Run all 7 attacks"))
    await r.hold(800)
    await r.box("attacks", "list", page.locator("[class*='st-key-attack_']").first)
    await r.point(page.get_by_text("blocked", exact=False).filter(has_text="/").first, dx=-60)
    await r.hold(2200)
    await r.move_to(980, 560)
    await r.scroll(500)
    await r.hold(1800)
    await r.scroll(500)
    await r.hold(2200)
    r.mark("attacks", "end")


def encode(frames: list[tuple[float, str]], t0: float, end: float) -> None:
    """Constant 30 fps H.264 from the variable-rate screencast frames, each held until the next one."""
    lst = FRAMES / "frames.txt"
    lines = []
    for i, (ts, name) in enumerate(frames):
        nxt = frames[i + 1][0] if i + 1 < len(frames) else t0 + end
        lines.append(f"file '{name}'\nduration {max(0.001, nxt - ts):.4f}")
    lines.append(f"file '{frames[-1][1]}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", "scale=1920:1080:flags=lanczos,fps=30", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT / "demo.mp4")], check=True, cwd=FRAMES)


async def main() -> int:
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ("BG_PARSER", "BG_MODEL_API_KEY", "BG_FAKE_BACKEND")}
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "ui" / "app.py"), "--server.headless", "true",
         "--server.port", str(PORT), "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = Recorder()
    try:
        end = time.time() + 90
        while time.time() < end:
            try:
                if urllib.request.urlopen(f"{URL}/_stcore/health", timeout=2).read() == b"ok":
                    break
            except Exception:
                await asyncio.sleep(0.5)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context(viewport=VIEW, device_scale_factor=DSF, color_scheme="light")
            await ctx.add_init_script(POINTER_JS)
            page = await ctx.new_page()
            await page.goto(URL)
            await settle(page)
            await page.wait_for_timeout(1500)
            await page.mouse.move(*r.cur)
            await r.start(page)
            await story(r, page)
            end_s = await r.stop()
            await browser.close()
    finally:
        proc.terminate()
    total = max(end_s, r.wall())
    print(f"frames: {len(r.frames)}, take: {total:.1f}s")
    encode(r.frames, r.t0, total)
    (OUT / "markers.json").write_text(json.dumps({"fps": 30, "duration": round(total, 3), "dsf": DSF,
                                                  "scenes": r.markers, "boxes": r.boxes}, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT / 'demo.mp4'} and markers.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
