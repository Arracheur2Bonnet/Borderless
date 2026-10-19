"""Open film.html from file:// in Chromium (Playwright), seek through every scene and save PNGs.

    ./.venv/Scripts/python deliverables/pitch/video/check_film.py <out_dir> [WxH]      (default 1920x1080)

Per scene: a screenshot shortly after the voice starts, and one at the end of the voice (every cue shown). Checks: no
network request leaves file://, no console error, no page scrollbar, nothing of the active scene sticks out of the
1920x1080 stage, the start card hides after a click, the control bar is invisible until hover, and playback advances.
Exit code 1 on any problem.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
URL = (HERE / "film.html").as_uri()
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "film_shots"
W, H = (int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else "1920x1080").split("x"))

OVERFLOW_JS = """() => {
  const s = document.querySelector('.scene.active'); const bad = [];
  const st = document.getElementById('stage').getBoundingClientRect(); const k = st.width / 1920;
  for (const e of s.querySelectorAll('*')) {
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0 || e.closest('svg')) continue;
    if (e.closest('.b:not(.on)')) continue;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const x2 = (r.right - st.left) / k, y2 = (r.bottom - st.top) / k, x1 = (r.left - st.left) / k, y1 = (r.top - st.top) / k;
    if (x2 > 1921 || y2 > 1081 || x1 < -1 || y1 < -1) bad.push(['outside', String(e.className || e.tagName), Math.round(x1), Math.round(y1), Math.round(x2), Math.round(y2)]);
    if (e.children.length === 0 && e.scrollWidth > e.clientWidth + 2 && cs.display !== 'inline') bad.push(['text-wider-than-box', String(e.className || e.tagName), e.textContent.slice(0, 40)]);
  }
  let maxb = 0;
  for (const c of s.children) { if (getComputedStyle(c).position === 'absolute') continue; const r = c.getBoundingClientRect(); maxb = Math.max(maxb, (r.bottom - st.top) / k); }
  if (maxb > 1080 - 40) bad.push(['content-bottom', Math.round(maxb)]);
  return bad;
}"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_context(viewport={"width": W, "height": H}, device_scale_factor=1).new_page()
        requests, errors = [], []
        page.on("request", lambda r: requests.append(r.url))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(URL)
        page.wait_for_selector("body.ready")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "00_start.png"))
        scenes = page.evaluate("film.scenes.map(s => ({id: s.id, kind: s.kind, vstart: s.vstart, vend: s.vend, start: s.start}))")
        sb = page.evaluate("[document.documentElement.scrollWidth - innerWidth, document.documentElement.scrollHeight - innerHeight]")
        if any(v > 0 for v in sb):
            problems.append(f"page scrolls {sb}")
        # control bar is invisible until hover
        op = page.evaluate("getComputedStyle(document.getElementById('bar')).opacity")
        if float(op) > 0.01:
            problems.append(f"control bar visible without hover (opacity {op})")
        # start with a click, playback advances
        page.mouse.click(W // 2, H // 2)
        page.wait_for_timeout(1500)
        t1 = page.evaluate("film.time()")
        if not 0.8 < t1 < 2.5:
            problems.append(f"playback did not advance after the click (t={t1})")
        if page.evaluate("getComputedStyle(document.getElementById('start')).visibility") != "hidden":
            problems.append("start card still visible after the click")
        page.keyboard.press("Space")
        paused_t = page.evaluate("film.time()")
        page.wait_for_timeout(600)
        if abs(page.evaluate("film.time()") - paused_t) > 0.05:
            problems.append("Space did not pause")
        page.mouse.move(W // 2, H - 40)
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "01_bar_hover.png"))
        if float(page.evaluate("getComputedStyle(document.getElementById('bar')).opacity")) < 0.9:
            problems.append("control bar did not appear on hover")
        page.mouse.move(W // 2, H // 3)
        page.keyboard.press("c")
        for s in scenes:
            for tag, tt in (("a", s["vstart"] + 1.5), ("z", s["vend"] + 0.05)):
                page.evaluate(f"film.pause(); film.seek({tt})")
                page.wait_for_timeout(1700)
                page.screenshot(path=str(OUT / f"s{s['id']}_{tag}.png"))
                if tag == "z" and s["kind"] == "film":
                    for b in page.evaluate(OVERFLOW_JS):
                        problems.append(f"scene {s['id']}: {b}")
        # full run in real time is not repeated here: seek is a pure function of t, play() only advances t
        ext = [u for u in requests if not u.startswith("file:") and not u.startswith("data:")]
        if ext:
            problems.append(f"network requests {ext}")
        if errors:
            problems.append(f"console errors {errors}")
        total = page.evaluate("film.total")
        print(f"{W}x{H}: {len(scenes)} scenes, total {total:.1f} s, requests outside file://: {len(ext)}, errors: {len(errors)}")
        br.close()
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
