"""Open the keynote from file:// in Chromium (Playwright), walk every slide and build, and save PNGs.

    ./.venv/Scripts/python scripts/check_keynote.py <out_dir> [WxH ...]      (default sizes: 1920x1080 1366x768)

Checks, per size: no network request leaves the page, no console error, no page scrollbars, no element of the
current slide overflows the 1920x1080 stage or its own box, and keyboard navigation works (arrows, Space, Home, End,
digits + Enter, O overview, N notes, B blank). Exit code 1 on any problem.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "deliverables/pitch/keynote/index.html").as_uri()
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "keynote_shots"
SIZES = [tuple(int(v) for v in a.split("x")) for a in sys.argv[2:]] or [(1920, 1080), (1366, 768)]

OVERFLOW_JS = """() => {
  const s = document.querySelector('.slide.active'); const bad = [];
  const st = document.getElementById('stage').getBoundingClientRect();
  const k = st.width / 1920;
  for (const e of s.querySelectorAll('*')) {
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0 || e.closest('svg')) continue;
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const x2 = (r.right - st.left) / k, y2 = (r.bottom - st.top) / k, x1 = (r.left - st.left) / k, y1 = (r.top - st.top) / k;
    if (x2 > 1920 + 1 || y2 > 1080 + 1 || x1 < -1 || y1 < -1) bad.push(['outside', e.className || e.tagName, Math.round(x1), Math.round(y1), Math.round(x2), Math.round(y2)]);
    if (e.scrollWidth > e.clientWidth + 2 && cs.display !== 'inline' && cs.overflowX !== 'visible') bad.push(['clipped-x', e.className || e.tagName]);
    if (e.children.length === 0 && e.scrollWidth > e.clientWidth + 2 && cs.display !== 'inline') bad.push(['text-wider-than-box', e.className || e.tagName, e.textContent.slice(0, 40)]);
  }
  // the slide content must fit above the bottom padding (92px) of the stage
  const kids = [...s.children].filter(c => getComputedStyle(c).position !== 'absolute');
  let maxb = 0; for (const c of kids) { const r = c.getBoundingClientRect(); maxb = Math.max(maxb, (r.bottom - st.top) / k); }
  if (maxb > 1080 - 40) bad.push(['content-bottom', Math.round(maxb)]);
  return bad;
}"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        for (w, h) in SIZES:
            ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = ctx.new_page()
            requests, errors = [], []
            page.on("request", lambda r: requests.append(r.url))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(INDEX)
            page.wait_for_selector("body.ready")
            n = page.evaluate("document.querySelectorAll('.slide').length")
            tag = f"{w}x{h}"
            # scrollbars
            sb = page.evaluate("[document.documentElement.scrollWidth - innerWidth, document.documentElement.scrollHeight - innerHeight]")
            if any(v > 0 for v in sb):
                problems.append(f"{tag}: page scrolls {sb}")
            for i in range(n):
                nb = page.evaluate(f"+document.querySelectorAll('.slide')[{i}].dataset.builds || 0")
                page.evaluate(f"location.hash = '#{i + 1}.{nb}'")
                page.wait_for_timeout(700)
                page.screenshot(path=str(OUT / f"{tag}_slide{i + 1:02d}.png"))
                bad = page.evaluate(OVERFLOW_JS)
                for b in bad:
                    problems.append(f"{tag} slide {i + 1}: {b}")
                if nb:                       # also the first state (build 0)
                    page.evaluate(f"location.hash = '#{i + 1}'")
                    page.wait_for_timeout(700)
                    page.screenshot(path=str(OUT / f"{tag}_slide{i + 1:02d}_b0.png"))
            # ---- keyboard navigation, scripted
            page.goto(INDEX + "#1")
            page.wait_for_selector("body.ready")
            cur = lambda: page.evaluate("document.getElementById('counter').textContent")
            page.keyboard.press("ArrowRight"); assert cur() == "1 / 16", cur()      # first build, same slide
            for _ in range(3):                       # builds 2 and 3 of slide 1, then slide 2
                page.keyboard.press("ArrowRight")
            page.wait_for_timeout(100)
            ok = cur() == "2 / 16"
            page.keyboard.press("ArrowLeft"); ok &= cur() == "1 / 16"
            page.keyboard.press("End"); ok &= cur() == "16 / 16"
            page.keyboard.press("Home"); ok &= cur() == "1 / 16"
            page.keyboard.type("11"); page.keyboard.press("Enter"); ok &= cur() == "11 / 16"
            page.keyboard.press("Space"); ok &= page.evaluate("location.hash") == "#11.1"
            page.keyboard.press("n"); ok &= page.evaluate("document.body.classList.contains('notes')")
            page.wait_for_timeout(600); page.screenshot(path=str(OUT / f"{tag}_notes.png"))
            page.keyboard.press("n"); ok &= not page.evaluate("document.body.classList.contains('notes')")
            page.keyboard.press("b"); ok &= page.evaluate("document.body.classList.contains('blanked')")
            page.keyboard.press("b")
            page.keyboard.press("o"); ok &= page.evaluate("document.body.classList.contains('ov')")
            page.wait_for_timeout(500); page.screenshot(path=str(OUT / f"{tag}_overview.png"))
            sb2 = page.evaluate("[document.getElementById('viewport').scrollHeight - innerHeight]")
            if sb2[0] > 2:
                problems.append(f"{tag}: overview scrolls by {sb2[0]}px")
            page.keyboard.press("ArrowRight"); page.keyboard.press("Enter")
            ok &= not page.evaluate("document.body.classList.contains('ov')") and cur() == "12 / 16"
            page.keyboard.press("f")
            page.keyboard.press("?"); page.wait_for_timeout(300); page.screenshot(path=str(OUT / f"{tag}_help.png"))
            if not ok:
                problems.append(f"{tag}: keyboard navigation check failed (last counter {cur()})")
            ext = [u for u in requests if not u.startswith("file:") and not u.startswith("data:")]
            if ext:
                problems.append(f"{tag}: network requests {ext}")
            if errors:
                problems.append(f"{tag}: console errors {errors}")
            print(f"{tag}: {n} slides, keyboard {'ok' if ok else 'FAILED'}, requests outside file://: {len(ext)}, errors: {len(errors)}")
            ctx.close()
        br.close()
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
