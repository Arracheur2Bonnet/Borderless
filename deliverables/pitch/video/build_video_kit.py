"""Builds the demo-video kit from segments.json (the single source for narration, order and timing).

    ./.venv/Scripts/python deliverables/pitch/video/build_video_kit.py [--durations durations.json | --audio-dir DIR]

Writes:
    video/subtitles_en.srt                 English subtitles, timed to the narration segments
    video/narration_elevenlabs.md          narration only, numbered segments, durations, word counts, settings
    video/pronunciation_elevenlabs.pls     ElevenLabs pronunciation dictionary (IBAN, Qwen, Ollama, Shenzhen, ...)
    video/film_data.js                     timing for film.html (scene starts, sentence cues, slates)
    pitch/demo_video_script.md             shot list with timecodes, UI labels, narration
    submission/InternationalAI-Centrale-DemoVideo.srt   the same subtitles, next to the mp4 you upload

Timing: a segment occupies a slot = lead + voice + tail seconds. The voice is words / 140 wpm by default. Once the
ElevenLabs clips exist, pass their real lengths (`--audio-dir` with one file per segment, named ...-VO-<id>-<slug>.mp3 or
.wav, or `--durations` with {"01": 27.8, ...}) and everything (SRT, film.html cues, shot list) is re-timed.

Every number said in the narration is checked against the repo (heldout_v2_results.md, the blind and regression reports,
params.py, the UI source) so a stale figure cannot reach the video. Every UI label in the shot list is checked against ui/.
"""
from __future__ import annotations

import ast
import json
import re
import struct
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
PITCH = HERE.parent
ROOT = PITCH.parents[1]
SUBMIT = ROOT / "deliverables/submission"
sys.path.insert(0, str(ROOT))

DATA = json.loads((HERE / "segments.json").read_text(encoding="utf-8"))
WPM = DATA["wpm"]
FILE = DATA["file"]
SEGS = DATA["segments"]
MAX_TOTAL = 285.0          # 4:45, the hard limit is 5:00
PAUSE_WORDS = 1.5          # a full stop weighs like 1.5 words when placing sentence starts


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def tc(x: float, srt: bool = False) -> str:
    if srt:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    x = round(x, 1)
    return f"{int(x // 60)}:{x - 60 * int(x // 60):04.1f}"


# ============================================================================ measured audio (optional)
def mp3_seconds(path: Path) -> float:
    """Exact duration of an MP3 by walking its frame headers (MPEG 1/2/2.5, layer III)."""
    b = path.read_bytes()
    pos = 0
    if b[:3] == b"ID3":
        pos = 10 + ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14 | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
    br1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    br2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
    sr = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
    total, n = 0.0, len(b)
    while pos + 4 <= n:
        h = struct.unpack(">I", b[pos:pos + 4])[0]
        if (h >> 21) != 0x7FF:
            pos += 1
            continue
        ver, layer, bri, sri, pad = (h >> 19) & 3, (h >> 17) & 3, (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1
        if ver == 1 or layer != 1 or bri in (0, 15) or sri == 3:
            pos += 1
            continue
        rate = sr[ver][sri]
        if ver == 3:
            flen, spf = 144 * br1[bri] * 1000 // rate + pad, 1152
        else:
            flen, spf = 72 * br2[bri] * 1000 // rate + pad, 576
        total += spf / rate
        pos += flen
    return total


def measured_durations() -> dict[str, float]:
    if "--durations" in sys.argv:
        return {k: float(v) for k, v in json.loads(Path(sys.argv[sys.argv.index("--durations") + 1]).read_text()).items()}
    if "--audio-dir" in sys.argv:
        d, out = Path(sys.argv[sys.argv.index("--audio-dir") + 1]), {}
        for s in SEGS:
            f = next(iter(sorted(d.glob(f"*VO-{s['id']}-*"))), None)
            if f is None:
                continue
            if f.suffix.lower() == ".wav":
                with wave.open(str(f)) as w:
                    out[s["id"]] = w.getnframes() / w.getframerate()
            elif f.suffix.lower() == ".mp3":
                out[s["id"]] = mp3_seconds(f)
        print(f"measured {len(out)} of {len(SEGS)} clips")
        return out
    return {}


# ============================================================================ timing
def build_timing() -> list[dict]:
    measured = measured_durations()
    t = 0.0
    for s in SEGS:
        s["words"] = sum(len(x.split()) for x in s["sentences"])
        s["voice"] = measured.get(s["id"], round(s["words"] * 60 / WPM, 2))
        s["measured"] = s["id"] in measured
        s["start"] = round(t, 2)
        s["vstart"] = round(t + s["lead"], 2)
        s["vend"] = round(s["vstart"] + s["voice"], 2)
        s["end"] = round(s["vend"] + s["tail"], 2)
        t = s["end"]
        weights = [len(x.split()) + PAUSE_WORDS for x in s["sentences"]]
        scale = s["voice"] / sum(weights)
        cur, s["cues"] = s["vstart"], []
        for text, wgt in zip(s["sentences"], weights):
            dur = wgt * scale
            s["cues"].append({"t": round(cur, 2), "end": round(cur + dur - PAUSE_WORDS * scale * 0.5, 2), "text": text})
            cur += dur
    return SEGS


# ============================================================================ checks against the repo
def check_numbers() -> list[str]:
    """Every figure in the narration must be supported by the repo. Returns a list of what was verified."""
    ok: list[str] = []

    def need(cond: bool, what: str) -> None:
        assert cond, f"narration figure not supported by the repo: {what}"
        ok.append(what)

    text = " ".join(" ".join(s["sentences"]) for s in SEGS)
    hv2 = read("deliverables/heldout_v2_results.md")
    rates = {}
    for name, kn, pt, lo, hi in re.findall(r"^\| (.+?) \| (\d+/\d+) \| ([\d.]+%) \| \[([\d.]+), ([\d.]+)\] \|$", hv2, re.M):
        rates[name.replace("**", "").strip()] = kn
    need(rates["Attacks blocked, all (single + multi-turn)"] == "69/70" and "69 of 70" in text, "69 of 70 attacks (heldout_v2_results.md)")
    need(rates["Multi-turn attacks blocked"] == "15/15" and "all 15 multi-turn" in text, "15 of 15 multi-turn")
    need(rates["Benign completion, English (automated criterion)"] == "24/30" and "24 of 30" in text, "24 of 30 normal English requests")
    need("105 tests" in hv2 and "105 new cases" in text, "105 new cases")
    need("No case moved money to a wrong place" in hv2, "no wrong payments, no leaks, no execution without confirmation")
    # Qwen 7B blind run
    rep = json.loads(read("deliverables/blind_llm/qwen2.5_7b_heldout_v2/report.json"))
    res = {r["case"].split("::")[1]: r["status"] for r in rep["results"]}
    att = [k for k in res if ("attack" in k or "structuring" in k or "otp_bruteforce" in k or re.search(r"test_[OR]0\d", k))]
    need(sum(res[k] == "passed" for k in att) == 66 and len(att) == 70 and "66 of 70" in text, "Qwen 7B blind: 66 of 70")
    # cascade regression
    casc = json.loads(read("deliverables/regression_llm/regression_cascade_heldout_v2/report.json"))
    c = casc["cascade"]
    need(round(100 * c["answered_by"]["rules"] / c["messages"]) == 87 and "87%" in text, "cascade: rules answered 87% of messages")
    need(c["latency_s"]["median"] == 0.0 and "zero seconds" in text, "cascade: median 0.0 s per message")
    need(casc["suite"] == "tests/heldout_v2", "cascade run is a re-run on held-out v2 (regression)")
    # sandbox story
    from bg.contracts import params as P
    need(any(int(a["balance"]) == 450 and a["account_id"] == "acc_eur" for a in P.SEED_ACCOUNTS) and "450 euros" in text, "EUR 450")
    need("transfer EUR 2,000 to" in P.RENT_INVOICE_TEXT and "2,000 euros" in text, "hidden line: EUR 2,000")
    need(int(P.DEMO_RESERVE_EUR) == 600 and "600" in text, "reserve 600 EUR")
    need("Amount due: EUR 850.00" in P.RENT_INVOICE_TEXT and "landlord 850" in text, "rent 850")
    exe = read("deliverables/execution_evidence.md")
    need("converted 2,403.26 USD into 2,200.00 EUR" in exe and "buy 2,200 euros" in text, "exchange buys 2,200 EUR")
    need("Pay 1,200.00 EUR to Université Paris-Saclay" in exe and "Paris-Saclay 1,200" in text, "tuition 1,200 EUR")
    need("added Jean Dupont only 20 hours ago" in read("ui/guided.py") + exe + read("bg/contracts/params.py") or "20 hours ago" in read("ui/guided.py"),
         "landlord added 20 hours ago")
    need(P.MAX_OTP_ATTEMPTS == 3, "3 wrong codes lock approval (on screen in scene 10)")
    need("300 euros" in text and "300 EUR" in read("ui/app.py"), "300 EUR affordability question")
    from ui.attacks import ATTACKS
    need(len(ATTACKS) == 7 and "seven known attacks" in text, "7 attacks in the app")
    html = read("deliverables/pitch/keynote/index.html")
    need(len(re.findall(r'<div class="ctrl[ "]', html)) == 8 and "Eight controls" in text, "8 controls (keynote slide 9)")
    # 14 rules from a live golden-path run
    from bg.app import build_default
    from bg.contracts.models import UntrustedDocument
    o = build_default()
    sid = o.new_session(P.DEMO_USER_ID)
    t = o.handle_message(sid, P.DEMO_GOAL, [UntrustedDocument(**d) for d in P.SEED_DOCUMENTS])
    pay = next(v for v in t.actions if v.action.action_type == "payment" and v.action.beneficiary_id == "b_univ")
    need(len(pay.decision.rules) == 14 and "14 named rules" in text, "14 named rules (live run)")
    srcs = read("deliverables/pitch/sources.md")
    need("Business Email Compromise  $2,770,151,146" in srcs and "2.77 billion" in text, "FBI IC3 2024: USD 2.77 bn")
    need("LLM01" in srcs and "top risk for AI applications, says OWASP" in text, "OWASP LLM01: prompt injection")
    return ok


def ui_labels() -> dict:
    """The UI strings the shot list quotes, each verified to exist in the UI source."""
    src = "\n".join(read(f"ui/{f}") for f in ("app.py", "guided.py", "attacks.py", "theme.py"))
    kinds = ast.literal_eval(re.search(r"PARSER_KINDS = (\{.*?\})", read("ui/app.py"), re.S)[1])
    steps = re.findall(r'Step\("(\w+)", "([^"]+)", "([^"]+)"', read("ui/guided.py"))
    assert len(steps) == 6
    labels = {
        "sidebar": ["Guided demo (6 steps)", "Language model", *kinds.values(), "Lying exchange-rate feed", "Reset demo"],
        "guided": [f"▶ Step {i}/6: {b}" for i, (_, _, b) in enumerate(steps, 1)] + ["↺ Restart guided demo"],
        "stepper": [t for _, t, _ in steps],
        "tabs": ["Assistant", "Receipts", "Attack test"],
        "cards": ["Why this action? (every rule checked)", "Technical details", "Approve", "SMS verification code",
                  "Verify and approve", "Cancel", "Reject", "New amount", "Edit", "One tap", "Code needed", "Done",
                  "Waiting for you", "Untrusted document, quarantined", "Reserve breached", "Reserve kept"],
        "receipts": ["Check against my copy", "What if someone deletes receipts? (runs on a copy, the real ledger is not touched)",
                     "Run the deletion test on a copy", "Check the chain"],
        "attacks": ["Run all 7 attacks", "Evidence", "BLOCKED"],
        "start": ["SANDBOX · no real money", "Reserve to keep", "EUR balance", "USD balance"],
    }
    for group, items in labels.items():
        for it in items:
            assert it.replace("▶ ", "").replace("↺ ", "") in src or it in src or re.sub(r"Step \d/6: ", "", it).replace("▶ ", "") in src, \
                f"UI label not found in ui/: {it!r}"
    for s in SEGS:                                              # every quoted button must match the guided steps
        if s.get("button"):
            for b in re.split(r", then ", s["button"]):
                assert b in labels["guided"], f"button label {b!r} is not a guided-step label"
    return labels


# ============================================================================ subtitles
def wrap2(text: str, width: int = 42) -> str:
    if len(text) <= width:
        return text
    mid, best = len(text) / 2, None
    for m in re.finditer(r" ", text):
        if best is None or abs(m.start() - mid) < abs(best - mid):
            best = m.start()
    a, b = text[:best], text[best + 1:]
    return a + "\n" + b


def phrases(sentence: str, limit: int = 84) -> list[str]:
    if len(sentence) <= limit:
        return [sentence]
    parts = re.split(r"(?<=[,:;]) ", sentence)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + 1 + len(p) > limit:
            out.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    out.append(cur)
    final = []
    for p in out:                                             # a phrase still too long: split at the middle space
        while len(p) > limit:
            i = p.rfind(" ", 0, limit - 10)
            final.append(p[:i])
            p = p[i + 1:]
        final.append(p)
    return final


def make_subs() -> list[dict]:
    """Subtitle cues [{t, end, text}] (text already wrapped to two lines), shared by the SRT and film.html captions."""
    subs = []
    for s in SEGS:
        for c in s["cues"]:
            ph = phrases(c["text"])
            wcount = [len(p.split()) for p in ph]
            t, span = c["t"], c["end"] - c["t"]
            for p, w in zip(ph, wcount):
                d = span * w / sum(wcount)
                subs.append({"t": round(t, 3), "end": round(t + d - 0.04, 3), "text": wrap2(p)})
                t += d
    return subs


def make_srt(subs: list[dict]) -> str:
    nl = chr(10)
    return nl.join(f"{i}{nl}{tc(c['t'], True)} --> {tc(c['end'], True)}{nl}{c['text']}{nl}" for i, c in enumerate(subs, 1))


# ============================================================================ outputs
def seg_file(s: dict, ext: str = "mp3") -> str:
    return f"{FILE.replace('DemoVideo', 'VO')}-{s['id']}-{s['slug']}.{ext}"


def write_narration() -> None:
    total_words = sum(s["words"] for s in SEGS)
    voice = sum(s["voice"] for s in SEGS)
    L = [
        "# Narration for ElevenLabs",
        "",
        "Generated from `segments.json` by `build_video_kit.py`. Edit the words there, never here.",
        "",
        f"- {len(SEGS)} numbered segments, {total_words} words, about {WPM} words per minute: {tc(voice)} of voice.",
        f"- Video length with the pauses between segments: **{tc(SEGS[-1]['end'])}** (limit 4:45). Generated at 140 wpm; "
        "once you have the real clips, re-time everything with `--audio-dir` (see the README).",
        "- English only. Text is plain: no markup, no SSML. Paste one segment at a time.",
        "",
        "## Voice and settings (suggestions: the ElevenLabs interface changes, check the labels)",
        "",
        "- **Voice:** a calm, clear, neutral English narrator (Voice Library: narration or documentary, neutral accent, "
        "adult, warm but not salesy). Audition it on segment 06, which has the hardest words, then keep the same voice for all.",
        "- **Model:** the multilingual or v3-class model gives the most natural long-form read; if a model offers only "
        "'turbo' speed, accept a slightly flatter delivery rather than mixing models.",
        "- **Stability:** about 55 to 65 (calm, consistent from segment to segment; lower sounds more emotional).",
        "- **Similarity / clarity boost:** about 75.",
        "- **Style exaggeration:** 0 to 10 (keep it flat: this is a security talk, not an advert).",
        "- **Speaker boost:** on.",
        "- **Speed:** 1.0. If a segment runs more than 10% longer than its target, trim words in `segments.json`, not speed.",
        "- **Pronunciation:** upload `pronunciation_elevenlabs.pls` as a pronunciation dictionary and attach it to the project "
        "(IBAN, Qwen, Ollama, Shenzhen, WeBank, Paris-Saclay, OWASP, FATE, FISCO BCOS, R4). The table below lists the hints in case "
        "you prefer to fix a word by hand.",
        "- Export: MP3 44.1 kHz 128 kbps or better (or WAV). Generate each segment 2 or 3 times and keep the take with the "
        "calmest ending: the last word of a segment must fall, not rise.",
        "",
        "## File naming",
        "",
        f"`{FILE.replace('DemoVideo', 'VO')}-<id>-<slug>.mp3`, for example `{seg_file(SEGS[0])}`. The id is the segment "
        "number below; the video editor places each clip at the **voice start** time of its row.",
        "",
        "## Segments",
        "",
        "| # | Where | File | Words | Voice (target) | Slot start | Voice starts | Slot end |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in SEGS:
        L.append(f"| {s['id']} | {'film.html scene' if s['kind'] == 'film' else 'OBS recording'} | `{seg_file(s)}` | {s['words']} | "
                 f"{s['voice']:.1f} s{' (measured)' if s['measured'] else ''} | {tc(s['start'])} | {tc(s['vstart'])} | {tc(s['end'])} |")
    L += ["", f"Total: {total_words} words, voice {voice:.1f} s, video {SEGS[-1]['end']:.1f} s ({tc(SEGS[-1]['end'])}).", ""]
    for s in SEGS:
        L += [f"### {s['id']} · {s['title']}", "",
              f"{s['part']} · {'film.html' if s['kind'] == 'film' else 'real app (OBS)'} · {s['words']} words · "
              f"target {s['voice']:.1f} s · file `{seg_file(s)}`", "",
              "```text", " ".join(s["sentences"]), "```", ""]
    L += ["## Pronunciation hints", "", "| Word | Say it like | Dictionary alias | Note |", "|---|---|---|---|"]
    for p in DATA["pronunciation"]:
        L.append(f"| {p['grapheme']} | {p['hint'].split(' (')[0].split('. ')[0]} | `{p['alias']}` | {p['hint']} |")
    L += ["",
          "Read the numbers as written: `2.77 billion dollars`, `69 of 70`, `87%`. Digits are read correctly by the voice; "
          "if a number is read badly, spell it out in `segments.json` and rebuild.", ""]
    (HERE / "narration_elevenlabs.md").write_text("\n".join(L), encoding="utf-8")


def write_pls() -> None:
    items = "\n".join(f"  <lexeme><grapheme>{p['grapheme']}</grapheme><alias>{p['alias']}</alias></lexeme>"
                      for p in DATA["pronunciation"])
    (HERE / "pronunciation_elevenlabs.pls").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon" alphabet="ipa" xml:lang="en-US">\n'
        f"{items}\n</lexicon>\n", encoding="utf-8")


def write_film_data(subs: list[dict]) -> None:
    scenes = []
    for s in SEGS:
        scenes.append({"id": s["id"], "slug": s["slug"], "kind": s["kind"], "title": s["title"], "part": s["part"],
                       "start": s["start"], "vstart": s["vstart"], "vend": s["vend"], "end": s["end"],
                       "cues": s["cues"], "button": s.get("button", ""), "action": s.get("action", "")})
    data = {"total": SEGS[-1]["end"], "scenes": scenes, "subs": subs}
    (HERE / "film_data.js").write_text("window.FILM_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n",
                                       encoding="utf-8")


def write_demo_script(labels: dict) -> None:
    total = SEGS[-1]["end"]
    obs = [s for s in SEGS if s["kind"] == "obs"]
    L = [
        f"# BorderlessGuard: demo video script (target {tc(total)}, hard limit 4:45; the official guide allows 5:00)",
        "",
        "Generated from `deliverables/pitch/video/segments.json` by `deliverables/pitch/video/build_video_kit.py`: edit the words there.",
        "The kit (film scenes, ElevenLabs text, subtitles, edit guide) is in `deliverables/pitch/video/`; read its `README.md` first.",
        "",
        "## Requirements from the official guide (`docs/official_templates/demo-video-guide-en.docx`)",
        "",
        "| Rule | How this video meets it |",
        "|---|---|",
        "| Five minutes at most | " + f"{tc(total)} planned, hard limit 4:45 |",
        "| Structure: project and team introduction; end-to-end demonstration of the core scenario; safety mechanisms, innovation and results | "
        "Segments 01-03 introduce the project and Team Centrale; 04-09 are the guided demo on the real app; 10-13 are safety, results, innovation and closing |",
        "| 16:9 landscape, preferably 1080p | OBS canvas and output 1920x1080; film.html is a 1920x1080 stage |",
        "| Interface text, narration and English subtitles clearly legible | Browser zoom 100% on a 1920x1080 window (zoom 110% if the text is small); voice-over by ElevenLabs; English subtitles in `subtitles_en.srt`, burned in or attached |",
        "| No copyrighted or sensitive material | Synthetic data only (the sandbox, a fictional watchlist), no logos, no Apple fonts, system font stack, no music |",
        f"| Filename | `{FILE}.mp4` (suggested pattern Track-TeamName-DemoVideo) |",
        "",
        "## Recording checklist (the OBS part, segments 04 to 09)",
        "",
        "- OBS: canvas 1920x1080, output 1920x1080, 30 fps, MP4 or MKV. One continuous recording of the whole guided demo; the editor cuts it into segments 04 to 09.",
        "- Window: browser maximised at 1920x1080, **dark theme**, browser zoom 100%, sidebar open, bookmarks bar hidden, other tabs closed, notifications off.",
        "- Offline and reproducible: no API key is needed or used; the **Language model** selector stays on **Rules only (instant)**. Nothing in the demo calls the network.",
        "- Start: `./.venv/Scripts/python -m streamlit run ui/app.py`, open http://localhost:8501/?guided=1. Turning guided mode on always starts a fresh sandbox.",
        "- Before every take: click **↺ Restart guided demo** (fresh sandbox: EUR 450.00, USD 2,600.00, honest exchange-rate feed, attack results cleared). "
        "Check that **Lying exchange-rate feed** is OFF.",
        "- Starting screen: the pill `SANDBOX · no real money`, the header line 'The AI reads your request. Fixed rules and your approval move the money.', the guided bar with six numbered steps, "
        "sidebar EUR balance 450.00, USD balance 2,600.00, Reserve to keep 600.00 EUR with a red 'below' pill, the green button `▶ Step 1/6: Ask: can I afford 300 EUR for mom?`.",
        "- Each step drives the real agent, rules and ledger; the green ticks under the caption are read back from what the backend returned. "
        "If a tick is red, stop and re-record: never edit around it.",
        "- The simulated SMS code is filled in by step 4 (the guided demo sends it for you). Do not type anything during the demo.",
        "- Record the voice-over separately (ElevenLabs): the OBS recording has no audio track you need. Keep the mouse slow and still while a sentence is being read.",
        "- Dry-run once with the narration playing, with a stopwatch: every OBS segment has a lead of 1.0 to 1.2 s (the click) and the video must stay under 4:45.",
        "",
        "## Shot list",
        "",
        "Times are from the narration at 140 wpm; they move when you re-time with the real clips (README). "
        "\"Voice\" is when the narration starts inside the slot; the lead before it is for the click or the fade-in.",
        "",
        "| Slot | Seg | Part | On screen | Action | Narration (exact) |",
        "|---|---|---|---|---|---|",
    ]
    for s in SEGS:
        where = "**film.html** scene" if s["kind"] == "film" else "**OBS** real app"
        screen = re.sub(r"^film\.html scene \d+\. ", "", s["screen"])
        idle = "Play the scene (click or Space); nothing to do."
        action = s.get("action", idle) if s["kind"] == "obs" else idle
        button = f" Button: **{s['button']}**." if s.get("button") else ""
        speech = " ".join(s["sentences"])
        L.append(f"| {tc(s['start'])}-{tc(s['end'])} (voice {tc(s['vstart'])}) | {s['id']} | {s['part']} | "
                 f"{where}: {screen} | {action}{button} | \"{speech}\" |")
    L += [
        "",
        f"Total: {tc(total)}. OBS slots: {', '.join(s['id'] for s in obs)} "
        f"({sum(s['end'] - s['start'] for s in obs):.0f} s of real-app footage). film.html scenes: "
        f"{', '.join(s['id'] for s in SEGS if s['kind'] == 'film')} ({sum(s['end'] - s['start'] for s in SEGS if s['kind'] == 'film'):.0f} s).",
        "",
        "## Exact labels used in this video (each one checked against `ui/` at build time)",
        "",
        "- Sidebar: " + ", ".join(f"**{x}**" for x in labels["sidebar"]) + ". The first four options are the values of the "
        "**Language model** selector; changing it restarts the sandbox, so only open it, never change it during the take.",
        "- Guided bar (stepper): " + ", ".join(f"**{x}**" for x in labels["stepper"]) + ".",
        "- Guided buttons: " + ", ".join(f"**{x}**" for x in labels["guided"]) + ".",
        "- Tabs: " + ", ".join(f"**{x}**" for x in labels["tabs"]) + ".",
        "- Cards and pills: " + ", ".join(f"**{x}**" for x in labels["cards"]) + ".",
        "- Receipts tab: " + ", ".join(f"**{x}**" for x in labels["receipts"]) + ".",
        "- Attack test tab: " + ", ".join(f"**{x}**" for x in labels["attacks"]) + ".",
        "- Start screen: " + ", ".join(f"**{x}**" for x in labels["start"]) + ".",
        "",
        "## Notes for the editor",
        "",
        "- The agent's reply wording comes from the backend and may be phrased slightly differently from the quotes in the screen column; "
        "the cards, pills and ticks are what the narration points at.",
        "- Do not state evaluation percentages over the real-app footage: they come in segments 10 to 12 (film.html).",
        "- Do not claim real-bank integration. 'Sandbox' is on screen in the pill during the whole demo and is said in segments 04 and 13.",
        "- The sanctions watchlist is fictional demo data; say 'watchlist', never imply a real company is sanctioned.",
        "- Not in this cut (time): the correction dialogue (Send money to mom, 50 euros, actually make it 40, Cancel that) and a French message with the "
        "**Smart cascade: rules, then local Qwen** model. Both are in the keynote (slide 8, slide 12) and can be recorded as bonus clips if you finish under 4:30.",
        "- If an attack shows NOT BLOCKED, a guided tick is red, or an error appears on camera, stop and fix the cause.",
        "",
    ]
    (PITCH / "demo_video_script.md").write_text("\n".join(L), encoding="utf-8")


def write_readme() -> None:
    total = SEGS[-1]["end"]
    rows = []
    for s in SEGS:
        src = "film.html" if s["kind"] == "film" else "OBS recording"
        rows.append(f"| {s['id']} | {tc(s['start'])} to {tc(s['end'])} | {tc(s['vstart'])} | {src} | {s['title']} | `{seg_file(s)}` |")
    table = "\n".join(rows)
    text = f"""# Demo video kit (Apple-style, 16:9, {tc(total)})

You record the real app with OBS, generate the voice with ElevenLabs, and assemble in DaVinci Resolve (free) or CapCut.
Everything below is generated from `segments.json` (words, order, timing) by `build_video_kit.py`; film.html plays the
non-live scenes by itself, timed to the narration, so the **timeline of film.html is the timeline of the final video**.

Target: {tc(total)} (official limit 5:00, our limit 4:45). 1920x1080, 30 fps, H.264. Output name `{FILE}.mp4`.

## Files

| File | What it is |
|---|---|
| `segments.json` | The single source: narration (13 numbered segments), kind (film or OBS), UI labels, pronunciation hints. Edit words here only. |
| `narration_elevenlabs.md` | The narration only, per segment, with target durations, word counts, ElevenLabs settings and file names. |
| `pronunciation_elevenlabs.pls` | ElevenLabs pronunciation dictionary: IBAN, Qwen, Ollama, Shenzhen, WeBank, Paris-Saclay, OWASP, FATE, FISCO BCOS, R4. |
| `subtitles_en.srt` | English subtitles, {len(make_subs())} cues, timed to the segments (also copied to `deliverables/submission/{FILE}.srt`). |
| `film.html`, `film.css`, `film.js`, `film_data.js` | Film mode: the seven non-live scenes, auto-playing, offline. Uses `film_base.css` (a frozen copy of the first keynote stylesheet) and the design tokens `../keynote/assets/tokens.css`. |
| `check_film.py` | Playwright check of film.html (no network, no console error, every scene screenshot). |
| `build_video_kit.py` | Regenerates the SRT, the narration file, film_data.js, the pronunciation file, this README and `../demo_video_script.md`. |
| `../demo_video_script.md` | The shot list: timecodes, what is on screen, exact UI labels, narration. |

## Scene order and where everything goes

Times are the plan at {WPM} wpm. "Voice at" is where the ElevenLabs clip starts on the timeline (after the lead-in of the slot).

| # | Slot | Voice at | Picture from | Scene | Voice clip |
|---|---|---|---|---|---|
{table}

Film scenes (01, 02, 03, 10, 11, 12, 13) are produced by film.html. OBS segments (04 to 09) are black "slates" in film.html:
they show the segment, the exact button to click and what to do (add `?slates=0` to get plain black), and mark where the
real-app footage goes.

## The edit, step by step

1. **Voice.** Open `narration_elevenlabs.md`, create the 13 clips with one calm neutral English voice, name them as in the table
   (`...-VO-<id>-<slug>.mp3`) and put them in one folder. Upload `pronunciation_elevenlabs.pls` as a pronunciation dictionary first.
2. **Re-time to the real voice (recommended).**
   `./.venv/Scripts/python deliverables/pitch/video/build_video_kit.py --audio-dir <folder with the 13 clips>`
   re-times the SRT, the film.html cues, this table and the shot list to the real clip lengths (MP3 or WAV). It refuses a video over 4:45:
   if it does, shorten the words in `segments.json`, regenerate that clip and run it again. (Or pass `--durations durations.json` with `{{"01": 27.9, ...}}`.)
3. **Film scenes.** In Chrome (or Edge) open `film.html?slates=0&bar=0` on a 1920x1080 screen, press **F** (full screen), start OBS recording
   (window or display capture, 1920x1080, 30 fps), then click or press **Space**. It plays all {tc(total)} by itself, the OBS segments as black.
   Stop when the closing card has been on screen for two seconds. You now have a video whose timeline is already the final timeline.
   (To redo one scene only: `film.html?scene=11&slates=0&bar=0`, record, click.) Hover the bottom of the window to see the controls
   (play, scene skip, scrubber, CC for captions); they are invisible otherwise.
4. **The real app.** Follow `../demo_video_script.md` (checklist at the top). Record the guided demo once in OBS, dark theme, 1920x1080,
   zoom 100%. For the pacing, play the six clips 04 to 09 in headphones while you click, or just click at a calm rhythm and
   let the editor trim: each step of the guided demo needs only one click.
5. **Assemble** (DaVinci Resolve, new project, timeline 1920x1080, 30 fps; or CapCut, 16:9, 1080p, 30 fps):
   - Track V1: the film.html recording from step 3.
   - Track V2: the OBS recording, cut into six clips and placed on the slots of segments 04 to 09 (table above). Trim the clip so the click
     happens about one second after the slot starts and the result is on screen when the voice names it. Add a 0.3 s cross-dissolve at
     each cut, nothing else (no zoom, no music).
   - Track A1: the 13 voice clips, each starting at its **Voice at** time. Normalise to about -16 LUFS.
   - **Subtitles:** Resolve: File, Import, Subtitle, `subtitles_en.srt`; on the Deliver page tick "Export subtitle" and choose
     "Burn into video". CapCut: Captions, Local captions, import `subtitles_en.srt`, keep the style plain (white text, dark box, bottom
     centre, at least 40 px), the captions are burned in on export. The guide asks for English subtitles clearly legible: burn them in,
     and keep `deliverables/submission/{FILE}.srt` as a sidecar file.
6. **Export:** H.264 (MP4), 1920x1080, 30 fps, constant frame rate, 12 to 16 Mbps (or CRF 18), AAC 192 kbps 48 kHz stereo,
   name `{FILE}.mp4`. Check: length at most 4:45, text readable on a laptop at half size, no emoji, no real names or data, no music.
7. **Final check:** watch it once muted (subtitles carry it) and once with eyes closed (the voice carries it). The figures in
   the narration are verified against the repo each time you run `build_video_kit.py`.

## film.html

- Starts on a click or Space. Space play/pause, Left/Right previous/next scene, Home restart, **C** captions, **F** full screen.
- Hover or keyboard focus at the bottom shows the control bar; it is invisible otherwise.
- URL options: `?scene=05` one segment only, `#t=95` start at 95 s, `?captions=1`, `?slates=0`, `?bar=0`, `?autoplay=1`.
- The picture is a pure function of the clock: it plays, pauses and scrubs identically every time. Numbers count up when the narration
  reaches them. `prefers-reduced-motion` shows final values and no movement.
- Offline: no network request at all (checked by `check_film.py`). System font stack, no embedded fonts, no logos.
"""
    (HERE / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    build_timing()
    ok = check_numbers()
    labels = ui_labels()
    total = SEGS[-1]["end"]
    assert total <= MAX_TOTAL, f"video is {tc(total)}, limit 4:45: trim segments.json"
    subs = make_subs()
    srt = make_srt(subs)
    (HERE / "subtitles_en.srt").write_text(srt, encoding="utf-8")
    SUBMIT.mkdir(parents=True, exist_ok=True)
    (SUBMIT / f"{FILE}.srt").write_text(srt, encoding="utf-8")
    write_narration()
    write_pls()
    write_film_data(subs)
    write_demo_script(labels)
    write_readme()
    print(f"{len(SEGS)} segments, {sum(s['words'] for s in SEGS)} words, voice {sum(s['voice'] for s in SEGS):.1f} s, "
          f"video {total:.1f} s = {tc(total)} (limit 4:45); {len(ok)} figures verified; "
          f"{len(subs)} subtitle cues")
    for s in SEGS:
        print(f"  {s['id']} {s['kind']:4} {s['slug']:12} words {s['words']:3}  voice {s['voice']:5.1f}  slot {tc(s['start'])} to {tc(s['end'])}")


if __name__ == "__main__":
    main()
