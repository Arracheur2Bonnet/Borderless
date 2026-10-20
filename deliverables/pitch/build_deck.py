"""Builds the official presentation file from the HTML keynote.

    ./.venv/Scripts/python deliverables/pitch/build_deck.py [--no-pdf] [--no-render]

Output (deliverables/submission/):
    InternationalAI-TopicC-Centrale-Deck.pptx   18 slides, 16:9
    InternationalAI-TopicC-Centrale-Deck.pdf    same order; the 16 keynote slides are VECTOR pages printed by Playwright
                                                (text stays sharp and selectable), between the official cover and
                                                THANKS pages kept in render/official_cover.pdf and render/official_thanks.pdf

Structure, in this order:
    1       the cover of the official template (docs/official_templates/presentation-template.pptx), with the project
            and team names filled in; the year "2025" painted out of the template artwork
    2..17   the 16 keynote slides, each one a full-bleed 1920x1080 image of the FINAL build state of the slide (slide 1: its hook, see PRINT_BUILD),
            rendered by Playwright from deliverables/pitch/keynote/index.html (so the deck can never drift from the
            keynote), with the speaker notes of notes.js (generated from speaker_script.md by build_keynote.py)
    18      the THANKS slide of the template (year painted out)

The slide images are also written to deliverables/pitch/render/slide_NN.png.
Run build_keynote.py first when the keynote or the speaker script changed: this script only renders and packs.
This file replaces the old 14-slide python-pptx deck (deliverables/pitch/BorderlessGuard_deck.pptx, removed).
"""
from __future__ import annotations

import io
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from pptx import Presentation
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Emu

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KN = HERE / "keynote"
RENDER = HERE / "render"
SUBMIT = ROOT / "deliverables/submission"
TEMPLATE = ROOT / "docs/official_templates/presentation-template.pptx"
NAME = "InternationalAI-TopicC-Centrale-Deck"
PROJECT = "BorderlessGuard"
TEAM_LINE = "Team Centrale \u00b7 International AI Track \u00b7 Topic C \u00b7 2026"
FONT = "Segoe UI"        # the template's HarmonyOS font is not installed on most machines
N_SLIDES = 16
# Build state printed for a slide when it is not the last one. Slide 1 ends on a title card that repeats the official
# cover, so the printed deck shows its hook instead: Lina, the forged invoice and EUR 450 against EUR 2,000.
PRINT_BUILD = {1: 2}


# ============================================================================ 1. render the keynote
def render_slides() -> list[Path]:
    """Final build state of every slide at 1920x1080, without the on-screen chrome (progress bar, counter)."""
    from playwright.sync_api import sync_playwright

    RENDER.mkdir(parents=True, exist_ok=True)
    out = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1).new_page()
        page.goto((KN / "index.html").as_uri())
        page.wait_for_selector("body.ready")
        page.evaluate("document.fonts.ready.then(() => 1)")
        page.add_style_tag(content="#progress,#counter,#navbtns,#goto{display:none!important} body{cursor:none}")
        n = page.evaluate("document.querySelectorAll('.slide').length")
        assert n == N_SLIDES, f"the keynote has {n} slides, expected {N_SLIDES}"
        for i in range(n):
            nb = PRINT_BUILD.get(i + 1) or page.evaluate(f"+document.querySelectorAll('.slide')[{i}].dataset.builds || 0")
            page.evaluate(f"location.hash = '#{i + 1}.{nb}'")
            page.wait_for_timeout(1400)
            f = RENDER / f"slide_{i + 1:02d}.png"
            page.screenshot(path=str(f))
            out.append(f)
        br.close()
    return out


# ============================================================================ 2. speaker notes
def load_notes() -> dict:
    js = (KN / "notes.js").read_text(encoding="utf-8")
    data = json.loads(js[js.index("{"): js.rindex("}") + 1])
    assert len(data) == N_SLIDES
    script = ROOT / "deliverables/pitch/speaker_script.md"
    if script.stat().st_mtime > (KN / "notes.js").stat().st_mtime:
        print("WARNING: speaker_script.md is newer than notes.js: run build_keynote.py first", file=sys.stderr)
    return data


def titles() -> list[str]:
    html = (KN / "index.html").read_text(encoding="utf-8")
    t = re.findall(r'<section class="slide[^"]*"[^>]*data-title="([^"]*)"', html)
    assert len(t) == N_SLIDES
    return [x.replace("&amp;", "&") for x in t]


def note_text(i: int, title: str, d: dict) -> str:
    head = f"Slide {i} \u00b7 {title} \u00b7 script section {d['section']} \u00b7 {d['words']} words, speech {d['from']} to {d['to']}"
    lines = [head, ""]
    for para in d["paras"]:
        lines.append(" ".join(f"[{t}]" if kind == "do" else t for kind, t in para))
    return "\n".join(lines)


# ============================================================================ 3. template artwork: paint out the year
def erase(im: Image.Image, box: tuple[int, int, int, int], feather: int = 10, probe: int = 14) -> Image.Image:
    """Fill box with a horizontal interpolation of the background just left and right of it (the artwork is a dark,
    nearly flat gradient there), then blend the edge. Used only to remove the baked-in "2025"."""
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0

    def smooth(x: np.ndarray, n: int = 25) -> np.ndarray:
        pad = np.pad(x, ((n // 2, n // 2), (0, 0)), mode="edge")
        k = np.ones(n) / n
        return np.stack([np.convolve(pad[:, c], k, mode="valid")[: x.shape[0]] for c in range(x.shape[1])], axis=1)

    left = smooth(a[y0:y1, x0 - probe:x0 - 2].mean(axis=1))
    right = smooth(a[y0:y1, x1 + 2:x1 + probe].mean(axis=1))
    u = np.linspace(0, 1, w)[None, :, None]
    fill = left[:, None, :] * (1 - u) + right[:, None, :] * u
    fill = fill + np.random.default_rng(7).normal(0, 0.8, fill.shape)
    mask = np.zeros(a.shape[:2], np.uint8)
    mask[y0:y1, x0:x1] = 255
    m = np.asarray(Image.fromarray(mask).filter(ImageFilter.GaussianBlur(feather))).astype(np.float32)[..., None] / 255
    full = a.copy()
    full[y0:y1, x0:x1] = fill
    return Image.fromarray(np.clip(a * (1 - m) + full * m, 0, 255).astype(np.uint8))


# pixel boxes of "2025" in the 2933x1650 template backgrounds (layout 1 = cover, layout 3 = THANKS)
YEAR_BOX = {"cover": (185, 372, 735, 560), "thanks": (185, 105, 545, 228)}


def clean_layout_image(layout, which: str) -> None:
    rid = next(r for r, rel in layout.part.rels.items() if rel.reltype.endswith("/image"))
    part = layout.part.related_part(rid)
    im = Image.open(io.BytesIO(part.blob))
    assert im.size == (2933, 1650), im.size
    buf = io.BytesIO()
    erase(im, YEAR_BOX[which]).save(buf, "JPEG", quality=93)
    part._blob = buf.getvalue()


# ============================================================================ 4. the presentation
def set_text(shape, text: str, size_pt: int, bold: bool = False) -> None:
    run = shape.text_frame.paragraphs[0].runs[0]
    run.text = text
    run.font.size = Emu(size_pt * 12700)
    run.font.bold = bold
    rpr = run._r.get_or_add_rPr()
    rpr.set("lang", "en-US")
    for tag in ("latin", "ea", "cs"):
        for el in rpr.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}" + tag):
            el.set("typeface", FONT)
            for att in ("panose", "charset"):
                el.attrib.pop(att, None)


def build_pptx(images: list[Path], notes: dict, ttl: list[str]) -> Path:
    prs = Presentation(str(TEMPLATE))
    assert (prs.slide_width, prs.slide_height) == (12192000, 6858000)
    cover, contents, content, thanks = list(prs.slides)
    cover_layout, thanks_layout = cover.slide_layout, thanks.slide_layout
    blank = next(l for l in prs.slide_layouts if l.name == "\u7a7a\u767d")        # the template's "blank" layout

    # --- cover: project and team names, year painted out
    clean_layout_image(cover_layout, "cover")
    clean_layout_image(thanks_layout, "thanks")
    t_name, t_team = [s for s in cover.shapes if s.has_text_frame]
    set_text(t_name, PROJECT, 44, bold=True)
    set_text(t_team, TEAM_LINE, 20)
    for shp, top, h in ((t_name, 4392951 - 120000, 760000), (t_team, 5260856 + 120000, 420000)):
        shp.text_frame.word_wrap = True            # fixed-width box (the template boxes were sized for 3 characters)
        shp.text_frame.auto_size = MSO_AUTO_SIZE.NONE
        shp.top, shp.width, shp.height = Emu(top), Emu(7400000), Emu(h)

    # --- 16 keynote slides: one full-bleed picture each, with the speaker notes
    new = []
    for i, img in enumerate(images, 1):
        s = prs.slides.add_slide(blank)
        for ph in list(s.placeholders):                  # none of the template placeholders (date, footer, number)
            ph._element.getparent().remove(ph._element)
        pic = s.shapes.add_picture(str(img), 0, 0, prs.slide_width, prs.slide_height)
        pic.name = f"Keynote slide {i}"
        pic._element.nvPicPr.cNvPr.set("descr", f"Slide {i} of the BorderlessGuard keynote: {ttl[i - 1]}")
        s.notes_slide.notes_text_frame.text = note_text(i, ttl[i - 1], notes[str(i)])
        new.append(s)
    cover.notes_slide.notes_text_frame.text = (
        f"{PROJECT} \u00b7 {TEAM_LINE}\n\nSlides 2 to 17 are the 16 slides of the offline HTML keynote "
        "(deliverables/pitch/keynote), final state of each slide. The notes are the speaker script.")

    # --- order: cover, 16 slides, THANKS; drop the template's contents and content slides
    lst = prs.slides._sldIdLst
    el_of = {id(s): el for s, el in zip(prs.slides, list(lst))}
    for s in (contents, content):
        el = el_of[id(s)]
        lst.remove(el)
        prs.part.drop_rel(el.rId)
    for s in [*new, thanks]:
        el = el_of[id(s)]
        lst.remove(el)
        lst.append(el)

    # --- unused layouts (their artwork also carries the year) are removed
    used = {s.slide_layout.part.partname for s in prs.slides}
    for layout in list(prs.slide_layouts):
        if layout.part.partname not in used:
            prs.slide_layouts.remove(layout)

    cp = prs.core_properties
    cp.title = f"{PROJECT}: International AI Track, Topic C"
    cp.author = "Team Centrale"
    cp.last_modified_by = "Team Centrale"           # the organiser template carries a real person's name
    cp.subject = "Presentation deck"
    cp.keywords = "BorderlessGuard; Centrale; International AI Track; Topic C"
    cp.comments = "Slides 2 to 17 are rendered from the offline HTML keynote."
    SUBMIT.mkdir(parents=True, exist_ok=True)
    out = SUBMIT / f"{NAME}.pptx"
    prs.save(str(out))
    return out


def export_pdf() -> Path:
    """Official cover, the 16 slides as vector pages (final build state), official THANKS page; all 960x540 pt."""
    import pymupdf
    from playwright.sync_api import sync_playwright

    pages = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        page.goto((KN / "index.html").as_uri())
        page.wait_for_selector("body.ready")
        page.evaluate("document.fonts.ready.then(() => 1)")
        page.emulate_media(media="screen")
        page.add_style_tag(content="#progress,#counter,#navbtns,#goto,#notes,#help,#blank{display:none!important}"
                                   "*{transition:none!important} @page{size:1920px 1080px;margin:0}")
        for i in range(N_SLIDES):
            nb = PRINT_BUILD.get(i + 1) or page.evaluate(f"+document.querySelectorAll('.slide')[{i}].dataset.builds || 0")
            page.evaluate(f"location.hash = '#{i + 1}.{nb}'")
            page.wait_for_timeout(400)
            pages.append(page.pdf(width="1920px", height="1080px", print_background=True, page_ranges="1"))
        br.close()
    out = pymupdf.open()
    cover, thanks = (pymupdf.open(RENDER / f"official_{k}.pdf") for k in ("cover", "thanks"))
    out.insert_pdf(cover)
    w, h = out[0].rect.width, out[0].rect.height
    for raw in pages:
        src = pymupdf.open("pdf", raw)
        out.new_page(width=w, height=h).show_pdf_page(pymupdf.Rect(0, 0, w, h), src, 0)
    out.insert_pdf(thanks)
    out.set_metadata({"title": f"{PROJECT} · Team Centrale", "author": "Team Centrale", "subject": TEAM_LINE})
    pdf = SUBMIT / f"{NAME}.pdf"
    out.save(pdf, garbage=4, deflate=True)
    return pdf


def main() -> None:
    old = HERE / "BorderlessGuard_deck.pptx"
    if old.exists():
        old.unlink()                       # replaced by the official deck; see keynote/README.md
    images = [RENDER / f"slide_{i:02d}.png" for i in range(1, N_SLIDES + 1)]
    if "--no-render" not in sys.argv or not all(f.is_file() for f in images):
        images = render_slides()
    notes, ttl = load_notes(), titles()
    pptx = build_pptx(images, notes, ttl)
    print(f"wrote {pptx.relative_to(ROOT)} ({pptx.stat().st_size // 1024} KB)")
    if "--no-pdf" not in sys.argv:
        pdf = export_pdf()
        import pymupdf
        n = len(pymupdf.open(pdf))
        assert n == N_SLIDES + 2, f"PDF has {n} pages"
        print(f"wrote {pdf.relative_to(ROOT)} ({pdf.stat().st_size // 1024} KB, {n} pages)")


if __name__ == "__main__":
    main()
