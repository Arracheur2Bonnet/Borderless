"""Builds the generated parts of the HTML keynote in deliverables/pitch/keynote/:

  assets/*.png   crops of the final dark UI screenshots (deliverables/screenshots/dark), pixel boxes below
  assets/tokens.css   copy of design/tokens.css (the keynote is one self-contained folder)
  notes.js       speaker notes per slide, from the spoken lines and stage directions of speaker_script.md

and CHECKS every number typed in index.html against the repo (sources below). The build fails (assert) if a
number on a slide no longer matches its source, so a stale figure cannot reach the stage.

Run from the repo root:  ./.venv/Scripts/python deliverables/pitch/build_keynote.py [--skip-tests]
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from math import sqrt
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
KN = HERE / "keynote"
DARK = ROOT / "deliverables/screenshots/dark"
WPM = 125
sys.path.insert(0, str(ROOT))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ============================================================================ assets
# (output name, source screenshot, pixel box in the 1920x1080 capture). The main column of the app is x 574..1650.
CROPS = [
    ("s04_affordability.png", "02_affordability.png", (574, 345, 1650, 1005)),
    ("s05_plan.png", "03_plan_and_injection.png", (574, 105, 1650, 785)),
    ("s06a_approve.png", "g3_guided_step3.png", (574, 325, 1650, 1005)),
    ("s06b_code.png", "g4_guided_step4.png", (574, 325, 1650, 1005)),
    ("s06c_receipts.png", "05_receipts.png", (574, 325, 1650, 1005)),
    ("s07_attacks.png", "06_attacks.png", (574, 325, 1650, 1040)),
    ("s08_correction.png", "08_correction.png", (574, 100, 1650, 600)),
]
(KN / "assets").mkdir(parents=True, exist_ok=True)
for out, src, box in CROPS:
    assert (DARK / src).is_file(), f"missing screenshot {DARK / src}: run scripts/screenshots.py <out> dark"
    Image.open(DARK / src).convert("RGB").crop(box).save(KN / "assets" / out, optimize=True)
shutil.copyfile(ROOT / "design/tokens.css", KN / "assets/tokens.css")

# ============================================================================ sourced numbers (asserted against index.html)
HTML = (KN / "index.html").read_text(encoding="utf-8")
FAILS: list[str] = []


def claim(text: str, source_ok: bool, why: str) -> None:
    """text must appear in index.html AND the source must support it."""
    if text not in HTML:
        FAILS.append(f"slide text missing: {text!r}")
    if not source_ok:
        FAILS.append(f"source does not support {text!r}: {why}")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    a = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(100 * (c - a) / d, 1), round(100 * (c + a) / d, 1)


HV2 = read("deliverables/heldout_v2_results.md")
RATES = {}
for name, kn, pt, lo, hi in re.findall(r"^\| (.+?) \| (\d+/\d+) \| ([\d.]+%) \| \[([\d.]+), ([\d.]+)\] \|$", HV2, re.M):
    RATES[name.replace("**", "").strip()] = (kn, pt, lo, hi)
ATT = RATES["Attacks blocked, all (single + multi-turn)"]
MT = RATES["Multi-turn attacks blocked"]
BEN = RATES["Benign completion, English (automated criterion)"]
claim(ATT[0], ATT[0] == "69/70", "heldout_v2 attacks")
claim(f"{ATT[2]} to {ATT[3]}", True, "")
claim(f"98.6%", ATT[1] == "98.6%", "attack point estimate")
claim(MT[0], MT[0] == "15/15", "multi-turn")
claim(f"79.6 to 100", (MT[2], MT[3]) == ("79.6", "100"), "multi-turn CI")
claim(BEN[0], BEN[0] == "24/30", "benign EN")
claim(f"{BEN[2]} to {BEN[3]}", True, "")
assert "No case moved money to a wrong place, executed anything without a confirmation, leaked another tenant's data" in HV2
claim("0 wrong payments", True, "")
NONUND = re.search(r"A01-A07, D04 and D11 \((\d+) cases\) ended in the generic", HV2)[1]
claim(f"{NONUND} attacks were stopped only because the parser did not understand them", True, "")
assert "X08-homoglyph" in HV2 and "no money moved" in HV2
assert "105 tests" in HV2
claim("105 new cases", True, "")
assert subprocess.run(["git", "cat-file", "-e", "44542f2"], cwd=ROOT).returncode == 0
claim("44542f2", True, "frozen commit exists")

# --- blind LLM runs (deliverables/blind_llm), frozen in 5868d83
BL = {}
for key, d in (("q7", "qwen2.5_7b_heldout_v2"), ("rules", "rules_heldout_v2")):
    rep = json.loads(read(f"deliverables/blind_llm/{d}/report.json"))
    res = {r["case"].split("::")[1]: r["status"] for r in rep["results"]}
    att = [k for k in res if ("attack" in k or "structuring" in k or "otp_bruteforce" in k or re.search(r"test_[OR]0\d", k))]
    ben = [k for k in res if k.startswith("test_benign[B")]
    non = [k for k in res if k.startswith("test_benign[N")]
    mt = [k for k in res if k.startswith("test_multiturn_attack")]
    single = [k for k in att if not k.startswith("test_multiturn")]
    ok = lambda ks: sum(res[k] == "passed" for k in ks)
    BL[key] = dict(att=(ok(single) + ok(mt), len(single) + len(mt)), mt=(ok(mt), len(mt)), ben=(ok(ben), len(ben)),
                   non=(ok(non), len(non)), rep=rep)
q, r = BL["q7"], BL["rules"]
assert q["att"] == (66, 70) and q["mt"] == (15, 15) and q["ben"] == (26, 30) and q["non"] == (5, 5), q
assert r["att"] == (70, 70) and r["ben"] == (29, 30) and r["non"] == (0, 5), r
claim("66/70", True, ""); claim("94.3%", round(100 * 66 / 70, 1) == 94.3, "")
lo, hi = wilson(66, 70)
claim(f"{lo} to {hi}", True, "Wilson 95% CI for 66/70")
claim("26/30", True, ""); claim("5/5", True, "")
claim("0/5", True, "rules parser non-English, heldout_v2_results.md")
lat = q["rep"]["latency_s"]["mean"]
claim(f"{lat:.1f} s per call", True, "mean latency, report.json")
assert q["rep"]["fallback_rate"] == 0.0
claim("0% fallback", True, "")
claim("70/70 attacks, 29/30 English", True, "rules re-run at 5868d83 (blind_llm/rules_heldout_v2)")
assert subprocess.run(["git", "cat-file", "-e", "5868d83"], cwd=ROOT).returncode == 0
assert subprocess.run(["git", "cat-file", "-e", "d3e0f49"], cwd=ROOT).returncode == 0
reg = json.loads(read("deliverables/regression_llm/regression_qwen7b_v2/report.json"))
claim("103/105", reg["passed"] == 103 and reg["cases"] == 105, "regression after the grounding check")
# the four Qwen attack misses: D04, D05, D08 over-broad plans (saved payees only), X02 sanctioned payee added after OTP
qfail = sorted(k.split("[")[1][:-1] for k, v in {x["case"].split("::")[1]: x["status"] for x in q["rep"]["results"]}.items()
               if v == "failed" and "benign" not in k)
assert qfail == ["D04-nested-quote", "D05-markdown-link", "D08-bursar-doc", "X02-case-space"], qfail
claim("Qwen's 4 misses", True, "")

# --- Smart cascade (rules first, local Qwen only when needed): regression on held-out v2, NOT blind
casc = json.loads(read("deliverables/regression_llm/regression_cascade_heldout_v2/report.json"))
assert casc["parser"] == "cascade" and casc["suite"] == "tests/heldout_v2" and casc["cases"] == 105, casc["suite"]
by = casc["cascade"]["answered_by"]
share_rules = round(100 * by["rules"] / casc["cascade"]["messages"])
assert sum(by.values()) == casc["cascade"]["messages"] == 137 and share_rules == 87, by
assert casc["cascade"]["latency_s"]["median"] == 0.0 and casc["fallback_rate"] == 0.0
claim("87% of messages answered by rules", share_rules == 87, "cascade report.json: rules 119 of 137 messages")
claim("median 0.0 s", casc["cascade"]["latency_s"]["median"] == 0.0, "cascade report.json latency median")
claim("Regression, not blind.", True, "the cascade was designed after the blind runs")
claim("103/105", casc["passed"] == 103, "cascade also passes 103/105 on held-out v2 (regression)")
claim("Smart cascade", True, "")
# two models of one family were run blind; the 3B report must exist and be a blind (pre-fix) run
q3 = json.loads(read("deliverables/blind_llm/qwen2.5_3b_heldout_v2/report.json"))
assert q3["cases"] == 105 and q3["model"] == "qwen2.5:3b-instruct", q3["model"]
claim("Qwen 7B and 3B", True, "both blind reports exist in deliverables/blind_llm")
# no hosted-Claude parser any more: the slides must not mention it
assert "claude" not in HTML.lower(), "a slide still mentions Claude"

# --- development numbers
TC = read("deliverables/task_completion.md")
m = re.search(r"Tasks: \*\*(\d+)\*\*, completed: \*\*(\d+)\*\*", TC)
claim(f"{m[2]} of {m[1]}", m[1] == m[2] == "21", "scripted missions")
AUD = read("docs/review/security_audit.md")
AUD_N = len(re.findall(r"^\| AUDIT-\d+ \| \*?\*?(?:High|Medium|Low)", AUD, re.M))
AUD2_N = len(re.findall(r"^\| AUDIT2-\d+ \| \*?\*?(?:High|Medium|Low)", AUD, re.M))
assert "All 11 round-2 findings are fixed" in AUD
claim(f"{AUD_N + AUD2_N} findings, all fixed or mitigated", (AUD_N, AUD2_N) == (12, 11), "audit rounds 1 and 2")
claim(f"{AUD_N + AUD2_N} audit findings fixed or mitigated", (AUD_N, AUD2_N) == (12, 11), "audit rounds 1 and 2")

# --- the 14 rules and thresholds, from a live run of the golden path
from bg.app import build_default  # noqa: E402
from bg.contracts import params as P  # noqa: E402
from bg.contracts.models import UntrustedDocument  # noqa: E402

o = build_default()
sid = o.new_session(P.DEMO_USER_ID)
t = o.handle_message(sid, P.DEMO_GOAL, [UntrustedDocument(**d) for d in P.SEED_DOCUMENTS])
pay = next(v for v in t.actions if v.action.action_type == "payment" and v.action.beneficiary_id == "b_univ")
RULES = [(x.rule_id, x.severity.value) for x in pay.decision.rules]
assert len(RULES) == 14 and sum(s == "R4" for _, s in RULES) == 10 and sum(s == "R3" for _, s in RULES) == 4, RULES
claim("14 named rules", True, "")
claim("The 14 rules on Lina's tuition payment", True, "")
claim("10 deny", True, ""); claim("4 ask for a code", True, "")
for rid, sev in RULES:
    claim(f'<span class="rid {sev.lower()}">{rid}</span>', True, "rule id from live run")
assert int(P.R2_MAX_PAYMENT_EUR) == 1500 and int(P.R2_MAX_FX_EUR) == 2500 and int(P.MAX_SINGLE_PAYMENT_EUR) == 4000
assert int(P.DAILY_OUTFLOW_LIMIT_EUR) == 5000 and int(P.NEW_BENEFICIARY_WINDOW.total_seconds() // 3600) == 72
assert int(P.STRUCTURING_THRESHOLD_EUR) == 1500 and int(P.STRUCTURING_WINDOW.total_seconds() // 3600) == 24
assert P.MAX_OTP_ATTEMPTS == 3 and int(P.DEMO_RESERVE_EUR) == 600
for s in ("Payee older than 72 h, up to 1,500 EUR", "Exchange up to 2,500 EUR", "over 4,000 per payment or 5,000 per day",
          "over 1,500 EUR in 24 h", "3 wrong codes"):
    claim(s, True, "params.py")
eur = {a["account_id"]: a["balance"] for a in P.SEED_ACCOUNTS}
claim("EUR 450", int(eur["acc_eur"]) == 450, "seed balance")
claim("EUR 2,000", "transfer EUR 2,000 to" in P.RENT_INVOICE_TEXT, "hidden line amount")
claim("EUR 850.00", "Amount due: EUR 850.00" in P.RENT_INVOICE_TEXT, "invoice")
claim("FR76 3000 4000 0312 3456 7890 143", "FR76 3000 4000 0312 3456 7890 143" in P.RENT_INVOICE_TEXT, "invoice IBAN")
hidden = re.search(r"<!--(.*?)-->", P.RENT_INVOICE_TEXT, re.S)[1]
claim(re.sub(r"\s+", " ", hidden).strip(), True, "hidden comment text, verbatim")
claim("20 hours ago", True, "demo step 4 text")
SRCS = read("deliverables/pitch/sources.md")
assert "Business Email Compromise  $2,770,151,146" in SRCS and "Prompt injection is listed as **LLM01**" in SRCS
claim('<span class="u pre">USD</span>2.77<span class="u">bn</span>', True, "sources.md S1")
claim("#1", True, "")

# --- live test run
tests_txt = "829"
if "--skip-tests" not in sys.argv:
    out = subprocess.run([str(ROOT / ".venv/Scripts/python"), "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    mm = re.search(r"(\d+) passed", out)
    xf = re.search(r"(\d+) xfailed", out)
    assert mm and "failed" not in out.split("passed")[0][-30:], "test suite has failures"
    tests_txt, xf_txt = mm[1], (xf[1] if xf else "0")
    claim(f'<span data-fact="tests">{tests_txt}</span>', True, "live pytest")
    claim(f'<span data-fact="xfail">{xf_txt}</span>', True, "live pytest")
    print(f"live pytest: {tests_txt} passed, {xf_txt} xfailed")

# ============================================================================ speaker notes
SCRIPT = read("deliverables/pitch/speaker_script.md").split("## Part 2")[0]
SECTIONS: dict[str, list[str]] = {}
for sid_, body in re.findall(r"^### (S\w+?) [^\n]*\n(.*?)(?=^### |\Z)", SCRIPT, re.M | re.S):
    SECTIONS[sid_] = body.splitlines()

# slide index (1-based) -> script section; None = slide added in the keynote (notes proposed below)
SLIDE_TO_SECTION = {1: "S1", 2: "S2", 3: "S3", 4: "S4", 5: "S5", 6: "S6", 7: "S7", 8: "S8", 9: "S9", 10: "S10",
                    11: "S11", 12: "S12", 13: "S13", 14: "S14", 15: "S15"}
PROPOSED: dict = {}   # every slide has a section in speaker_script.md now


def para_segments(line: str):
    segs = []
    for part in re.split(r"(\[[^\]]*\])", line):
        if not part.strip():
            continue
        if part.startswith("["):
            segs.append(("do", part[1:-1].strip()))
        else:
            segs.append(("say", part.strip()))
    return segs


def parse_section(lines: list[str]):
    paras, buf = [], None
    for ln in lines:
        s = ln.strip()
        if buf is not None:                       # continuation of a multi-line [direction]
            buf += " " + s
            if s.endswith("]"):
                paras.append([("do", re.sub(r"\s+", " ", buf[1:-1]).strip())]); buf = None
            continue
        if not s or s.startswith("|") or s == "---":
            continue
        if s.startswith("> "):
            paras.append(para_segments(s[2:]))
        elif s.startswith("[") and not s.endswith("]"):
            buf = s
        elif s.startswith("[") and s.endswith("]"):
            paras.append([("do", s[1:-1].strip())])
        else:
            paras.append([("do", s)])
    return paras


notes, t0 = {}, 0.0
for idx in range(1, max(SLIDE_TO_SECTION) + 1):
    sec = SLIDE_TO_SECTION[idx]
    paras = PROPOSED[sec] if sec in PROPOSED and sec not in SECTIONS else parse_section(SECTIONS[sec])
    spoken = " ".join(s for p in paras for k, s in p if k == "say")
    words = len(spoken.split())
    t1 = t0 + words / WPM * 60
    f = lambda x: f"{int(x // 60)}:{int(x % 60):02d}"
    notes[str(idx)] = {"section": sec, "words": words, "from": f(t0), "to": f(t1), "paras": [[list(s) for s in p] for p in paras]}
    t0 = t1
(KN / "notes.js").write_text("window.KEYNOTE_NOTES = " + json.dumps(notes, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
print(f"notes.js: {len(notes)} slides, speech {int(t0 // 60)}:{int(t0 % 60):02d} at {WPM} wpm "
      f"(sections without script text, proposed: {[s for s in PROPOSED if s not in SECTIONS]})")

if FAILS:
    print("\nFAILED CHECKS:")
    for f_ in FAILS:
        print("  -", f_)
    sys.exit(1)
print("all slide numbers match their sources")
