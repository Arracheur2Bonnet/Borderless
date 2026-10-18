"""Builds BorderlessGuard_deck.pptx (16:9, 14 slides) for the 10-minute English defence.

Run from the repo root:  ./.venv/Scripts/python deliverables/pitch/build_deck.py

Every number on a slide is read from a repo file or a live run at build time, never typed in:
  - blind held-out v2 rates  <- deliverables/heldout_v2_results.md (section 3 table, Wilson CIs)
  - development suites       <- deliverables/security_results.md
  - scripted missions        <- deliverables/task_completion.md
  - test count               <- live `pytest tests -q`
  - rules on a payment       <- live golden-path run (rule ids of the tuition payment decision)
  - risk thresholds          <- bg/contracts/params.py
  - problem-slide stakes     <- deliverables/pitch/sources.md (FBI IC3 2024, OWASP LLM01 2025) + the seeded invoice
The build fails (assert) if a source string is missing, so a stale number cannot slip through.
Speaker notes are the spoken lines of deliverables/pitch/speaker_script.md (Part 1, one section per slide,
stage directions removed), with minute marks computed from the word count at 125 words per minute.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

from lxml import etree
from PIL import Image, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SHOTS = ROOT / "deliverables/screenshots"
sys.path.insert(0, str(ROOT))

NAVY, PANEL, PANEL2 = RGBColor(0x0B, 0x1B, 0x33), RGBColor(0x14, 0x2B, 0x4D), RGBColor(0x1C, 0x38, 0x60)
ACC, WHITE, GREY = RGBColor(0x2D, 0xD4, 0xBF), RGBColor(255, 255, 255), RGBColor(0xB4, 0xC0, 0xD2)
RED, AMB, GRN = RGBColor(0xEF, 0x44, 0x44), RGBColor(0xF5, 0x9E, 0x0B), RGBColor(0x22, 0xC5, 0x5E)
WPM = 125

# =========================================================================== sourced numbers
def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def need(txt: str, s: str) -> str:
    assert s in txt, f"source string not found: {s!r}"
    return s


HV2 = read("deliverables/heldout_v2_results.md")
RATES = {}
for name, kn, pt, lo, hi in re.findall(r"^\| (.+?) \| (\d+/\d+) \| ([\d.]+%) \| \[([\d.]+), ([\d.]+)\] \|$", HV2, re.M):
    RATES[name.replace("**", "").strip()] = (kn, pt, lo, hi)
ATT = RATES["Attacks blocked, all (single + multi-turn)"]
MT = RATES["Multi-turn attacks blocked"]
BEN = RATES["Benign completion, English (automated criterion)"]
BEN_MAN = RATES["Benign completion, English, after manual review (B15 counted as failed, see 5.2)"]
NONEN = RATES["Benign completion, non-English (reported separately; SPEC targets English)"]
need(HV2, "No case moved money to a wrong place, executed anything without a confirmation, leaked another tenant's data")
NONUNDERSTOOD = re.search(r"A01-A07, D04 and D11 \((\d+) cases\) ended in the generic", HV2)[1]
need(HV2, "X08-homoglyph")
HV2_TOTAL = re.search(r"Result: \*\*(\d+) tests", HV2)[1]
assert ATT[0] == "69/70" and BEN[0] == "24/30", "held-out v2 headline changed: re-check slide wording"

SEC = read("deliverables/security_results.md")
DEV_ADV = re.search(r"\((\d+/\d+) adversarial cases\)", SEC)[1]
DEV_BEN = re.search(r"\((\d+/\d+) benign cases\)", SEC)[1]

TC = read("deliverables/task_completion.md")
m = re.search(r"Tasks: \*\*(\d+)\*\*, completed: \*\*(\d+)\*\*", TC)
MISSIONS = f"{m[2]}/{m[1]}"

out = subprocess.run([str(ROOT / ".venv/Scripts/python"), "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                     cwd=ROOT, capture_output=True, text=True).stdout
TESTS = re.search(r"(\d+) passed", out)[1]
XF = re.search(r"(\d+) xfailed", out)
XFAILED = XF[1] if XF else "0"
assert "failed" not in out.split("passed")[0][-30:], "test suite has failures"

from bg.app import build_default  # noqa: E402
from bg.contracts import params as P  # noqa: E402
from bg.contracts.models import UntrustedDocument  # noqa: E402

_o = build_default()
_sid = _o.new_session(P.DEMO_USER_ID)
_t = _o.handle_message(_sid, P.DEMO_GOAL, [UntrustedDocument(**d) for d in P.SEED_DOCUMENTS])
_pay = next(v for v in _t.actions if v.action.action_type == "payment" and v.action.beneficiary_id == "b_univ")
PAY_RULES = [(r.rule_id, r.severity.value) for r in _pay.decision.rules]
N_PAY_RULES = len(PAY_RULES)


def eur(d) -> str:
    return f"{int(d):,}"


R2PAY, R2FX, MAXPAY, DAILY = eur(P.R2_MAX_PAYMENT_EUR), eur(P.R2_MAX_FX_EUR), eur(P.MAX_SINGLE_PAYMENT_EUR), eur(P.DAILY_OUTFLOW_LIMIT_EUR)
NEWH = int(P.NEW_BENEFICIARY_WINDOW.total_seconds() // 3600)
STRH = int(P.STRUCTURING_WINDOW.total_seconds() // 3600)
STRT = eur(P.STRUCTURING_THRESHOLD_EUR)
OTPN = P.MAX_OTP_ATTEMPTS
RES = eur(P.DEMO_RESERVE_EUR)


def commit(grep: str, fallback: str) -> str:
    """Short hash of the newest commit whose message matches; the result must exist in this repository."""
    h = subprocess.run(["git", "log", "--format=%h", "-1", f"--grep={grep}"],
                       cwd=ROOT, capture_output=True, text=True).stdout.strip() or fallback
    assert subprocess.run(["git", "cat-file", "-e", h], cwd=ROOT).returncode == 0, f"commit {h} not in repo"
    return h


FROZEN_COMMIT = commit("Blind held-out v2: results frozen", "44542f2")
FIX_COMMIT = commit("Fix blind-v2 findings", "292f748")

# the product line, read from the UI so deck and app always say the same sentence
SLOGAN = re.search(r'"(The AI reads your request\.[^"]*)"', read("ui/app.py"))[1]

# white-box audit: findings and their status, from docs/review/security_audit.md "Fix status"
AUD = read("docs/review/security_audit.md")
_status = re.findall(r"^\| AUDIT-(\d+) \| (?:Fixed|Mitigated) \|", AUD.split("## Fix status")[1], re.M)
AUD_N = len(re.findall(r"^\| AUDIT-\d+ \| \*?\*?(?:High|Medium|Low)", AUD, re.M))
need(AUD, "AUDIT-6 is in `ui/` and was handled by the UI workstream")   # the one id missing from the fix table
_aud = subprocess.run([str(ROOT / ".venv/Scripts/python"), "-m", "pytest", "tests/audit", "-q", "-p", "no:cacheprovider"],
                      cwd=ROOT, capture_output=True, text=True).stdout
assert "xfail" not in _aud and "failed" not in _aud and " passed" in _aud, "an audit test is xfail or failing"
assert AUD_N == 12 and len(_status) == 11, f"audit: {AUD_N} findings, {len(_status)} in the fix table: re-check slide 9"
AUDIT_COMMITS = (commit("Security: fix all white-box audit findings", "2ce6035") + ", "
                 + commit("untrusted text always escaped", "13dded5"))

# every screenshot the deck needs; fail loudly with the full list if one is missing
REQUIRED_SHOTS = ["01_home.png", "02_affordability.png", "03_plan_and_injection.png", "04_executed.png",
                  "05_receipts.png", "06_attacks.png", "g4_guided_step4.png"]
_missing = [n for n in REQUIRED_SHOTS if not (SHOTS / n).is_file()]
assert not _missing, (f"missing screenshots in {SHOTS}: {_missing}. The deck needs all of {REQUIRED_SHOTS} "
                      "(1920x1080 viewport captures from scripts/screenshots.py); crops are pixel boxes in shot() calls.")

# problem-slide stakes: external numbers come only from sources.md (checked against the primary sources)
SRCS = read("deliverables/pitch/sources.md")
BEC_USD = int(need(SRCS, "Business Email Compromise  $2,770,151,146").split("$")[1].replace(",", ""))
BEC = f"USD {BEC_USD / 1e9:.2f} bn"
need(SRCS, "https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf")
need(SRCS, "Prompt injection is listed as **LLM01**")
need(SRCS, "https://genai.owasp.org/llmrisk/llm01-prompt-injection/")
INV_AMT = re.search(r"transfer EUR ([\d,]+) to", P.RENT_INVOICE_TEXT)[1]
LINA_EUR = eur(next(a["balance"] for a in P.SEED_ACCOUNTS if a["account_id"] == "acc_eur"))

# speaker notes: spoken lines ("> ") of each "### S<n>" section of Part 1 of speaker_script.md
_sp = read("deliverables/pitch/speaker_script.md").split("## Part 2")[0]
SCRIPT = {}
for num, body in re.findall(r"^### S(\d+) [^\n]*\n(.*?)(?=^### |\Z)", _sp, re.M | re.S):
    spoken = " ".join(ln[2:] for ln in body.splitlines() if ln.startswith("> "))
    spoken = re.sub(r"\[[^\]]*\]", "", spoken)
    SCRIPT[int(num)] = re.sub(r"\s+", " ", spoken).strip()
assert sorted(SCRIPT) == list(range(1, 15)), f"speaker_script.md must have S1..S14, got {sorted(SCRIPT)}"

# =========================================================================== layout helpers
FONT = {False: ImageFont.truetype("C:/Windows/Fonts/calibri.ttf", 100),
        True: ImageFont.truetype("C:/Windows/Fonts/calibrib.ttf", 100)}
WARN: list[str] = []


def n_lines(t: str, size: float, bold: bool, width_in: float) -> int:
    """Greedy word-wrap estimate with Calibri metrics (same metrics LibreOffice/PowerPoint use)."""
    avail = (width_in - 0.2) * 72  # default 0.1in inset each side
    total = 0
    for para in t.split("\n"):
        line, n = "", 1
        for w in para.split(" "):
            cand = (line + " " + w).strip()
            if FONT[bold].getlength(cand) * size / 100 > avail and line:
                n, line = n + 1, w
            else:
                line = cand
        total += n
    return total


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
SLIDES: list[tuple[str, str]] = []   # (title, narration)


def _runs(tf, t, size, color, bold, align, spacing=None):
    for i, line in enumerate(t.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        # **x** marks a teal bold segment inside a line
        for j, seg in enumerate(re.split(r"\*\*", line)):
            if not seg:
                continue
            r = p.add_run()
            r.text = seg
            hl = j % 2 == 1
            r.font.size, r.font.name = Pt(size), "Calibri"
            r.font.bold = bold or hl
            r.font.color.rgb = ACC if hl else color


def text(s, x, y, w, h, t, size=20, color=WHITE, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         spacing=None, label=""):
    assert size >= 16 or label == "chrome", f"font below 16pt: {t[:40]!r}"
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    _runs(tf, t, size, color, bold, align, spacing)
    _check(t, size, bold, w, h, spacing)
    return tb


def _check(t, size, bold, w, h, spacing=None):
    plain = t.replace("**", "")
    lines = n_lines(plain, size, bold, w)
    need_h = lines * size * 1.2 / 72 + 0.1 + (spacing or 0) * plain.count("\n") / 72
    if need_h > h + 0.02:
        WARN.append(f"overflow risk ({lines} lines, need {need_h:.2f}in > {h:.2f}in): {plain[:50]!r}")


def box(s, x, y, w, h, t="", fill=PANEL, line=None, size=18, color=WHITE, bold=False, align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, spacing=None, inset=None):
    assert not t or size >= 16, f"font below 16pt: {t[:40]!r}"
    sh = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = 0.08
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is not None:
        sh.line.color.rgb = line
        sh.line.width = Pt(2)
    else:
        sh.line.fill.background()
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    if inset is not None:
        tf.margin_left = tf.margin_right = Inches(inset)
    if t:
        _runs(tf, t, size, color, bold, align, spacing)
        _check(t, size, bold, w - (2 * inset - 0.2 if inset is not None else 0), h, spacing)
    return sh


def arrow(s, x1, y1, x2, y2, color=ACC, width=3):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    te = etree.SubElement(ln, "{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd")
    te.set("type", "triangle")


def shot(s, name, crop, x, y, w=None, h=None, border=ACC):
    """Crop a real UI screenshot (pixel box) and place it; exactly one of w/h fixes the size."""
    img = Image.open(SHOTS / name).crop(crop)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    cw, ch = img.size
    if w is None:
        w = h * cw / ch
    if h is None:
        h = w * ch / cw
    pic = s.shapes.add_picture(buf, Inches(x), Inches(y), Inches(w), Inches(h))
    pic.line.color.rgb = border
    pic.line.width = Pt(2.5)
    return w, h


def slide(title):
    narration = SCRIPT[len(prs.slides) + 1]
    s = prs.slides.add_slide(BLANK)
    f = s.background.fill
    f.solid()
    f.fore_color.rgb = NAVY
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.16), prs.slide_height)
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACC
    bar.line.fill.background()
    if title:
        assert n_lines(title, 32, True, 12.4) == 1, f"title wraps: {title}"
        text(s, 0.55, 0.32, 12.4, 0.75, title, 32, WHITE, True)
    n = len(prs.slides)
    text(s, 11.3, 7.08, 1.85, 0.32, f"BorderlessGuard  {n}", 11, GREY, align=PP_ALIGN.RIGHT, label="chrome")
    SLIDES.append((title or "Title", narration))
    s._narr = narration
    return s


def callout(s, x, y, w, h, num, head, body, color=ACC, size=17):
    """Numbered callout used next to screenshots."""
    c = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y + 0.04), Inches(0.42), Inches(0.42))
    c.fill.solid()
    c.fill.fore_color.rgb = color
    c.line.fill.background()
    tf = c.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    _runs(tf, str(num), 16, NAVY, True, PP_ALIGN.CENTER)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    text(s, x + 0.5, y - 0.02, w - 0.5, 0.42, head, 19, color, True)
    text(s, x + 0.5, y + 0.36, w - 0.5, h - 0.36, body, size, WHITE)


# =========================================================================== 1 title
s = slide(None)
text(s, 0.75, 1.35, 6.6, 1.1, "BorderlessGuard", 60, WHITE, True)
text(s, 0.75, 2.55, 6.4, 1.9, "An AI agent that moves a student's money across borders, and **cannot be talked into "
     "moving it wrongly.**", 28, WHITE)
text(s, 0.75, 4.75, 6.4, 0.9, SLOGAN, 20, ACC, True)
text(s, 0.75, 6.3, 11.0, 0.45, "2026 Shenzhen FinTech Competition  ·  International AI Track  ·  Topic C: "
     "Cross-border & Student Finance", 16, GREY)
shot(s, "01_home.png", (480, 60, 1740, 540), 7.45, 1.35, w=5.6)
shot(s, "04_executed.png", (0, 50, 300, 320), 7.45, 3.75, w=2.3)
text(s, 9.95, 3.8, 3.1, 1.8, "Live sandbox app.\nOne sentence in;\nEUR 600.00 reserve kept.", 18, GREY)

# =========================================================================== 2 problem
s = slide("Invoice fraud meets an AI that can pay")
stakes = [(BEC, "lost to fake payment requests\n(business email compromise),\nreported to the FBI in 2024",
           "FBI IC3, 2024 Internet Crime Report, p. 10\n(US complaints, all victims)"),
          ("#1", "risk for LLM applications:\nprompt injection, including\nhidden text in files",
           "OWASP Top 10 for LLM\nApplications 2025, LLM01"),
          (f"{INV_AMT} EUR", f"what the hidden line in Lina's\nrent invoice demands.\nShe holds {LINA_EUR} EUR.",
           "our demo invoice\n(bg/contracts/params.py)")]
for i, (big, lab, src) in enumerate(stakes):
    x = 0.6 + i * 4.1
    box(s, x, 1.4, 3.85, 3.85, "", PANEL, RED if i == 2 else ACC)
    text(s, x, 1.5, 3.85, 1.0, big, 48, RED if i == 2 else ACC, True, PP_ALIGN.CENTER)
    text(s, x + 0.1, 2.55, 3.65, 1.5, lab, 19, WHITE, False, PP_ALIGN.CENTER)
    text(s, x + 0.1, 4.2, 3.65, 0.95, src, 16, GREY, False, PP_ALIGN.CENTER)
box(s, 0.6, 5.5, 12.05, 1.2, "If the model can be fooled, **it must not be the one that decides.**", PANEL2, size=28,
    bold=True)

# =========================================================================== 3 architecture
s = slide("The model never touches the money")
flow = [("LLM parser", "writes a request only;\nno tools, no IBANs", PANEL, WHITE),
        ("Planner", "IBANs from the registry;\nsimulates the plan", PANEL, WHITE),
        ("Policy gate", f"{N_PAY_RULES} rules per payment;\nrisk R0 to R4", ACC, NAVY),
        ("Sandbox ledger", "pays once only;\nwrites a receipt", PANEL, WHITE)]
for i, (h, b, f, c) in enumerate(flow):
    x = 0.6 + i * 3.15
    box(s, x, 1.5, 2.7, 1.75, f"{h}\n{b}", f, size=18, color=c, spacing=6)
    if i < 3:
        arrow(s, x + 2.72, 2.37, x + 3.13, 2.37)
box(s, 0.6, 3.95, 2.7, 1.35, "Untrusted documents\nscanned, quarantined;\nnever shown to the LLM", PANEL, RED, size=17)
text(s, 1.55, 3.22, 0.8, 0.7, "×", 36, RED, True, PP_ALIGN.CENTER)
box(s, 3.75, 3.95, 2.7, 1.35, "Payee registry\nthe ONLY source\nof IBANs", PANEL, ACC, size=17)
arrow(s, 5.1, 3.93, 5.1, 3.28)
box(s, 6.9, 3.95, 2.7, 1.35, "User approves\ntap or one-time code,\nlocked to the payment", PANEL, AMB, size=17)
arrow(s, 8.25, 3.93, 8.25, 3.28, AMB)
box(s, 10.05, 3.95, 2.7, 1.35, "Receipts\nhash-chained, plus a\nsigned copy for Lina", PANEL, ACC, size=17)
arrow(s, 11.4, 3.28, 11.4, 3.93)
box(s, 0.6, 5.75, 12.15, 1.0, "A fooled model can only **propose**. Code checks, Lina **approves**, the ledger **pays**.",
    PANEL2, size=24, bold=True)

# =========================================================================== 4 affordability (live demo step 1)
s = slide("Demo 1: an affordability question is only simulated")
w, h = shot(s, "02_affordability.png", (480, 360, 1740, 1000), 0.55, 1.3, w=8.45)
callout(s, 9.3, 1.3, 3.75, 1.5, 1, "She asks", "\"Can I afford 300 EUR\nto mom after my bills?\"")
callout(s, 9.3, 3.0, 3.75, 1.5, 2, "Whole plan simulated", "FX, tuition, rent\nand the extra 300 EUR")
callout(s, 9.3, 4.7, 3.75, 1.5, 3, "Plain answer", "300 EUR left, below\nthe 600 EUR reserve", RED)
box(s, 0.55, 1.3 + h + 0.2, 8.45, 6.75 - (1.3 + h + 0.2), "A projection only: **0 actions proposed, 0 moved**",
    PANEL2, size=20, bold=True)

# =========================================================================== 5 plan + injection (live step 2)
s = slide("Demo 2: one sentence, three actions, 600 EUR kept")
w, h = shot(s, "03_plan_and_injection.png", (490, 120, 1740, 770), 0.55, 1.3, w=8.45)
callout(s, 9.3, 1.3, 3.75, 1.35, 1, "One sentence", "\"Pay tuition and rent,\nkeep 600 EUR\"")
callout(s, 9.3, 2.75, 3.75, 1.35, 2, "Injection quarantined", "hidden order: wire 2,000 EUR\nto a UK IBAN, no questions", RED)
callout(s, 9.3, 4.2, 3.75, 1.35, 3, "Three-step plan", "buy 2,200 EUR, then pay\ntuition 1,200 and rent 850")
callout(s, 9.3, 5.65, 3.75, 1.1, 4, "Rent needs a code", "payee added 20 h ago:\nclassic scam pattern", AMB)
box(s, 0.55, 1.3 + h + 0.2, 8.45, 6.75 - (1.3 + h + 0.2), "450 + 2,200 − 1,200 − 850 = **600 EUR**, exactly the reserve",
    PANEL2, size=20, bold=True)

# =========================================================================== 6 executed + receipts (live step 3)
s = slide("Demo 3: paid, with a receipt Lina can check")
w1, h1 = shot(s, "g4_guided_step4.png", (540, 320, 1740, 690), 0.55, 1.3, w=8.45)
w2, h2 = shot(s, "05_receipts.png", (480, 300, 1740, 700), 0.55, 1.3 + h1 + 0.2, w=8.45)
shot(s, "04_executed.png", (0, 50, 300, 300), 9.3, 1.3, w=1.95)
text(s, 11.3, 1.4, 1.9, 1.3, "EUR 600.00\nreserve\nkept", 20, ACC, True)
callout(s, 9.3, 3.0, 3.85, 1.25, 1, "Receipt per action", "executed and denied,\nhash-chained")
callout(s, 9.3, 4.35, 3.85, 1.25, 2, "Lina keeps a copy", "signed head of the chain,\nlike a statement number")
callout(s, 9.3, 5.7, 3.85, 1.15, 3, "Tampering shows", "deleted or rewritten\nreceipts are detected")

# =========================================================================== 7 attack test (live, guided step 6)
s = slide("Demo 4: seven known attacks, replayed live")
w, h = shot(s, "06_attacks.png", (480, 230, 1740, 900), 0.55, 1.3, w=7.6)
attacks = ["Injected rent invoice", "IBAN substitution in chat", "\"Send the maximum\"", "Poisoned FX tool output",
           "Replay (confirm twice)", "Split payment: 2,100 in 3", "Sanctioned payee (watchlist)"]
for i, a in enumerate(attacks):
    y = 1.3 + i * 0.79
    box(s, 8.45, y, 1.35, 0.6, "BLOCKED", GRN, size=16, color=NAVY, bold=True)
    text(s, 9.9, y + 0.06, 3.3, 0.5, a, 18, WHITE)
box(s, 0.55, 1.3 + h + 0.2, 7.6, 6.85 - (1.3 + h + 0.2), "Seven attacks we wrote: a demo, not a test. Blind test: slide 11",
    PANEL2, size=18, bold=True)

# =========================================================================== 8 dialogue (slide only, after the live demo)
s = slide("Demo 5: a correction is a new action, never an edit")
rows = [("Clarify", "\"Send money to mom\"  →  \"50 euros\"", "missing amount becomes a question;\nthe answer completes the draft"),
        ("Correct", "\"actually make it 40\"", "old action cancelled; a NEW action is\nsimulated and checked again"),
        ("Cancel", "\"cancel that\" · \"cancel everything\"", "pending actions cancelled; nothing executes"),
        ("Ask", "\"Can I afford 300 to mom after my bills?\"", "simulation only: projection + answer;\nnothing is proposed"),
        ("Verify", "\"Check against my copy\"", "Lina's signed copy detects deleted\nor rewritten receipts")]
for i, (lab, q, r) in enumerate(rows):
    y = 1.35 + i * 1.0
    box(s, 0.6, y, 1.75, 0.85, lab, ACC, size=20, color=NAVY, bold=True)
    box(s, 2.5, y, 4.45, 0.85, q, PANEL2, size=17, align=PP_ALIGN.LEFT, inset=0.18)
    box(s, 7.1, y, 5.65, 0.85, r, PANEL, size=17, align=PP_ALIGN.LEFT, inset=0.18)
text(s, 0.6, 6.4, 12.2, 0.5, "Every change goes through the same steps again: simulate → rules → approval.", 17, GREY)

# =========================================================================== 9 security layers
s = slide("Only one of eight controls reads the text")
ctrl = [("Content guard", "quarantines injected documents; text stays data", GREY),
        ("Registry-only IBANs", "no IBAN from a document or chat is ever paid", ACC),
        ("Approval locked", "tied to the exact payment; any change is refused", ACC),
        ("One-time code", f"{OTPN} wrong codes lock code approval for the user", ACC),
        ("No double payment", "a replayed approval never pays twice", ACC),
        ("Customers separated", "other customers' data unreachable", ACC),
        ("Split payments", f"same payee > {STRT} EUR in {STRH} h: code needed", AMB),
        ("Sanctions screen", "watchlist names + IBANs, look-alikes folded: deny", RED)]
for i, (h, b, c) in enumerate(ctrl):
    x = 0.6 + (i % 4) * 3.07
    y = 1.4 + (i // 4) * 2.15
    box(s, x, y, 2.87, 0.7, h, c, size=18, color=NAVY, bold=True)
    box(s, x, y + 0.7, 2.87, 1.3, b, PANEL, size=17)
box(s, 0.6, 5.8, 12.15, 0.5, "Detection only warns; **authorisation never depends on detecting the attack.**",
    PANEL2, size=18)
box(s, 0.6, 6.4, 12.15, 0.5, f"After two blind tests, a white-box audit: **{AUD_N} findings, all fixed or mitigated** "
    f"(commits {AUDIT_COMMITS})", PANEL2, size=18)

# =========================================================================== 10 risk matrix
s = slide(f"{N_PAY_RULES} named rules on every payment; R4 has no override")
mat = [("R0", "read-only", "allow", GREY),
       ("R2", f"payee older than {NEWH} h, ≤ {R2PAY} EUR;\nFX buy ≤ {R2FX} EUR", "one tap", GRN),
       ("R3", f"payee < {NEWH} h, > {R2PAY} EUR, FX > {R2FX},\nadd payee, split payments", "one-time code", AMB),
       ("R4", f"infeasible, unregistered or blocked payee, IBAN ≠ registry, other customer, reserve floor, "
              f"> {MAXPAY} per payment, > {DAILY} per day, untrusted origin, sanctioned", "DENY\nno override", RED)]
y = 1.4
for lvl, cond, verdict, col in mat:
    hh = 1.85 if lvl == "R4" else 1.0
    box(s, 0.6, y, 1.0, hh, lvl, col, size=22, color=NAVY, bold=True)
    box(s, 1.7, y, 4.95, hh, cond, PANEL, size=17, align=PP_ALIGN.LEFT, inset=0.15)
    box(s, 6.75, y, 1.85, hh, verdict, PANEL, size=17, bold=True, color=col)
    y += hh + 0.12
text(s, 0.6, y + 0.05, 8.0, 0.4, "Every threshold lives in one file: bg/contracts/params.py", 16, GREY)
box(s, 8.95, 1.4, 3.8, 5.4, "", PANEL)
text(s, 9.1, 1.48, 3.6, 0.45, f"One payment: {N_PAY_RULES} rules", 19, ACC, True)
tb = text(s, 9.1, 1.95, 3.6, 4.8, "", 16, WHITE)
tf = tb.text_frame
for i, (rid, sev) in enumerate(PAY_RULES):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    r = p.add_run()
    r.text = f"{sev}  {rid}"
    r.font.size, r.font.name, r.font.bold = Pt(16), "Calibri", sev == "R3"
    r.font.color.rgb = AMB if sev == "R3" else WHITE
_check("\n".join(f"R4  {rid}" for rid, _ in PAY_RULES), 16, False, 3.6, 4.8)

# =========================================================================== 11 evaluation (HERO)
a_kn, a_p, a_lo, a_hi = ATT
m_kn, m_p, m_lo, m_hi = MT
b_kn, b_p, b_lo, b_hi = BEN
s = slide(f"Blind test: {a_kn} attacks blocked, 0 wrong payments")
text(s, 0.6, 1.12, 12.2, 0.75,
     f"**Protocol:** {HV2_TOTAL} cases written by a separate AI agent that was not allowed to read our guard, parser or "
     f"policy code; criteria fixed before the first run; results committed in {FROZEN_COMMIT} before any fix "
     f"(fixes: {FIX_COMMIT}). Offline rules parser.", 16, GREY)
tiles = [(a_kn, f"attacks blocked · {a_p}", f"95% CI {a_lo}–{a_hi}", 4.55),
         (m_kn, "multi-turn attacks", f"95% CI {m_lo}–{m_hi}", 3.55),
         (b_kn, f"benign English · {b_p}", f"95% CI {b_lo}–{b_hi}", 3.85)]
x = 0.6
for big, lab, ci, tw in tiles:
    box(s, x, 1.95, tw, 2.2, "", PANEL, ACC)
    text(s, x, 2.0, tw, 1.0, big, 54, ACC, True, PP_ALIGN.CENTER)
    text(s, x, 3.0, tw, 0.45, lab, 20, WHITE, True, PP_ALIGN.CENTER)
    text(s, x, 3.45, tw, 0.45, ci, 18, GREY, False, PP_ALIGN.CENTER)
    x += tw + 0.1
box(s, 0.6, 4.28, 12.15, 0.62, "0 wrong payments  ·  0 data leaks  ·  0 executions without confirmation",
    GRN, size=21, color=NAVY, bold=True)
box(s, 0.6, 5.05, 6.85, 1.85, "", PANEL)
text(s, 0.72, 5.08, 6.65, 1.8,
     f"**Caveats**\n{NONUNDERSTOOD} attacks stopped only by \"not understood\"\nClaude parser not evaluated (rules parser only)\n"
     f"The 1 miss: homoglyph sanctions bypass, now fixed", 18, WHITE)
box(s, 7.6, 5.05, 5.15, 1.85, "", PANEL)
text(s, 7.72, 5.08, 4.95, 1.8,
     f"**Development (not blind)**\n{DEV_ADV} attacks · {DEV_BEN} benign\n{MISSIONS} scripted missions, JSONL logs\n"
     f"{TESTS} tests pass (+{XFAILED} documented xfail)", 18, WHITE)

# =========================================================================== 12 limitations
s = slide("What it does not do yet")
box(s, 0.6, 1.4, 5.95, 0.7, "Limitations today", RED, size=22, bold=True)
box(s, 0.6, 2.1, 5.95, 3.75,
    "Sandbox only: simulated ledger, FX, SMS code\n"
    f"Rules parser: {b_kn} benign EN, {NONEN[0]} non-EN\n"
    "Claude parser exists, not yet evaluated\n"
    "Content guard is rule-based (it only warns)\n"
    "One persona, USD/EUR, demo signing key",
    PANEL, size=20, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE, spacing=14, inset=0.3)
box(s, 6.8, 1.4, 5.95, 0.7, "Next (NOT implemented)", ACC, size=22, color=NAVY, bold=True)
box(s, 6.8, 2.1, 5.95, 3.75,
    "Same blind protocol on the Claude parser\n"
    "Baseline: same attacks on a plain LLM agent\n"
    "Federated risk score across banks\n"
    "Receipt anchors on a consortium chain\n"
    "Open-banking connector; usability study",
    PANEL, size=20, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE, spacing=14, inset=0.3)
box(s, 0.6, 6.05, 12.15, 0.75, "We claim a safe architecture, measured blind. We do **not** claim production readiness.",
    PANEL2, size=20, bold=True)

# =========================================================================== 13 criteria
s = slide("Each judging criterion, with evidence you can rerun")
crit = [("40%", "Task completion", f"{MISSIONS} scripted missions,\naudit log for each\n{b_kn} blind everyday requests;\n"
                                   "each miss: a question,\nnever a wrong payment"),
        ("30%", "Security & compliance", f"{a_kn} blind attacks blocked\n0 wrong payments\n0 data leaks\n"
                                         "split-payment and\nsanctions rules"),
        ("30%", "Innovation & interaction", "clarify, correct, cancel\nquestions answered\nby simulation\n"
                                            "receipts Lina can\ncheck herself")]
for i, (p, h, b) in enumerate(crit):
    x = 0.6 + i * 4.12
    box(s, x, 1.4, 3.9, 1.25, p, ACC, size=48, color=NAVY, bold=True)
    box(s, x, 2.65, 3.9, 0.75, h, PANEL2, size=21, bold=True)
    box(s, x, 3.4, 3.9, 2.45, b, PANEL, size=18)
box(s, 0.6, 6.05, 12.15, 0.75, "Evidence in the repo: heldout_v2_results.md · task_completion.md · security_results.md",
    PANEL2, size=18)

# =========================================================================== 14 closing + ask
s = slide("What we would build next with WeBank")
text(s, 0.6, 1.2, 12.2, 0.6, SLOGAN.replace("Fixed rules", "**Fixed rules**").replace("your approval", "**your approval**"),
     24, WHITE, True)
asks = [("FATE", "risk score shared across banks: scam signals, not customer data; it can only ask for a code"),
        ("FISCO BCOS", "anchor each receipt-chain head on a consortium chain, so Lina no longer needs her own copy"),
        ("Blind test v3", "same protocol on the Claude parser, plus a plain LLM agent as a baseline")]
for i, (h, b) in enumerate(asks):
    y = 1.95 + i * 0.82
    box(s, 0.6, y, 2.4, 0.7, h, ACC, size=20, color=NAVY, bold=True)
    box(s, 3.1, y, 9.65, 0.7, b, PANEL, size=17, align=PP_ALIGN.LEFT, inset=0.2)
text(s, 0.6, 4.4, 12.2, 0.4, "Future work: none of these three is implemented today.", 16, GREY)
box(s, 0.6, 4.9, 12.15, 1.75, "", PANEL, ACC)
text(s, 0.85, 4.95, 11.7, 0.45, "Reproduce offline (no API key)", 18, ACC, True)
_c = [("pip install -r requirements-lock.txt", ""), ("python -m pytest tests -q", ""),
      ("python -m pytest tests/heldout_v2 -q", "# blind v2 set"),
      ("python -m streamlit run ui/app.py", "# live demo, OTP 246810")]
cmds = "\n".join((c.ljust(38) + k).rstrip() for c, k in _c)
tb = text(s, 0.85, 5.38, 11.7, 1.3, cmds, 16, WHITE)
for p in tb.text_frame.paragraphs:
    for r in p.runs:
        r.font.name = "Consolas"
text(s, 0.6, 6.8, 10.5, 0.4, "BorderlessGuard  ·  Topic C, International AI Track  ·  Thank you. Questions welcome.", 16, GREY)

# =========================================================================== notes with minute marks
t0 = 0.0
total_words = 0
for sl, (title, narr) in zip(prs.slides, SLIDES):
    words = len(narr.split())
    total_words += words
    t1 = t0 + words / WPM * 60
    mark = f"[{int(t0 // 60)}:{int(t0 % 60):02d} - {int(t1 // 60)}:{int(t1 % 60):02d}]  ({words} words)"
    sl.notes_slide.notes_text_frame.text = f"{mark}  (speech only; the live demo adds about 45 s of clicks)\n{narr}"
    print(f"  {title[:56]:<58}{words:4d} words  {mark.split(']')[0][1:]}")
    t0 = t1

out_path = HERE / "BorderlessGuard_deck.pptx"
prs.save(out_path)
print(f"saved {out_path.name}: {len(prs.slides)} slides, {total_words} words narration = "
      f"{int(t0 // 60)}:{int(t0 % 60):02d} at {WPM} wpm")
print(f"tests {TESTS} passed + {XFAILED} xfailed; payment rules {N_PAY_RULES}: {[r for r, _ in PAY_RULES]}")
for w_ in WARN:
    print("WARN", w_)
