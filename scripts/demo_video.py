"""Build the official demo video: plan the edit, fit the ElevenLabs voice-over, write subtitles, render.

    ./.venv/Scripts/python scripts/demo_video.py plan                 # timings from the narration (150 wpm) or the measured voice
    ./.venv/Scripts/python scripts/demo_video.py sync VOICE.mp3       # split one ElevenLabs file into the 20 cues, then plan
    ./.venv/Scripts/python scripts/demo_video.py render [--out FILE]  # plan, render with Remotion, write the .en.srt

Inputs: deliverables/pitch/voiceover_script.txt (20 paragraphs = 20 cues), media/launch-video/public/rec/markers.json
(scripts/demo_record.py: the real app, recorded), media/launch-video/public/vo/NN.wav (written by `sync`).
Output: media/launch-video/src/demo/plan.json (read by the DemoVideo composition), media/launch-video/out/demo-video.mp4
and its English subtitles. Each scene lasts as long as its footage AND its narration need: a longer voice only
lengthens a scene (the footage holds on its last frame), it is never sped up or cut.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
FILM = ROOT / "media" / "launch-video"
SCRIPT = ROOT / "deliverables" / "pitch" / "voiceover_script.txt"
MARKERS = FILM / "public" / "rec" / "markers.json"
VO_DIR = FILM / "public" / "vo"
PLAN = FILM / "src" / "demo" / "plan.json"
FPS = 30
WPM = 150                      # estimate before the real voice exists
LEAD = 0.35                    # seconds between a scene cut and its first word
PAD = 0.9                      # seconds the picture stays after the last word of a scene
MAX_SECONDS = 300
STAGE = {"x": 173, "y": 58, "w": 1574, "h": 886}
TEAM, TOPIC = "Team Centrale", "International AI Track, Topic C"
TERMINAL = [
    "$ python scripts/verify_all.py",
    "",
    "PASS  pytest (full suite)      70.0s  1379 passed, 7 xfailed",
    "PASS  scripted missions         0.5s  21/21 missions completed",
    "PASS  golden path               0.4s  RESULT: PASS (EUR 600.00, USD 196.74, 3 receipts)",
    "PASS  security report           1.2s  attacks blocked 69/69, benign completed 29/29",
    "PASS  evidence reproducible     0.1s  regenerated evidence identical to committed files",
    "",
    "ALL CHECKS PASSED: 5 passed, 0 failed, 0 skipped.",
]

# id, kind, cues (1-based), recording marker, chapter, minimum seconds, zoom target (box name, max scale), callout
SCENES = [
    ("hook", "hook", [1, 2], None, "The threat", 16.0, None, None),
    ("brand", "brand", [3], None, "BorderlessGuard", 8.0, None, None),
    ("account", "rec", [4], "account", "Live app: Lina's account", 0, ("balances+payees", 1.25), None),
    ("request", "rec", [5], "request", "Live app: one request", 0, None, None),
    ("quarantine", "rec", [6], "quarantine", "Live app: the hidden instruction", 0, ("notice", 1.25),
     ("Document quarantined", "warn")),
    ("note", "rec", [7], "note", "Live app: the hidden instruction", 0, ("doc", 1.2), None),
    ("plan", "rec", [8], "plan", "Live app: a plan, simulated first", 0, None, ("Nothing moves until she approves", "info")),
    ("taps", "rec", [9], "taps", "Live app: approval in proportion to risk", 0, None, ("Known payees: one tap", "ok")),
    ("code", "rec", [10], "code", "Live app: approval in proportion to risk", 0, ("rent", 1.18),
     ("New payee: one-time code", "warn")),
    ("done", "rec", [11], "done", "Live app: approval in proportion to risk", 0, None, ("600.00 EUR left: reserve kept", "ok")),
    ("receipts", "rec", [12], "receipts", "Live app: receipts", 0, ("result", 1.2), ("Matches her signed copy", "ok")),
    ("deletion", "rec", [13], "deletion", "Live app: receipts", 0, ("test", 1.15), ("Deletion caught by her copy", "ok")),
    ("correction", "rec", [14], "correction", "Live app: a correction", 0, ("reply", 1.15), None),
    ("payee", "rec", [15], "payee", "Live app: payee check", 0, ("card", 1.15), ("Name does not match: she is warned", "warn")),
    ("limit", "rec", [16], "limit", "Live app: hard limit", 0, ("card", 1.15), ("Denied: no code can override it", "deny")),
    ("attacks", "rec", [17], "attacks", "Live app: attack test", 0, None, ("7 / 7 blocked", "ok")),
    ("measured", "measured", [18], None, "Measured blind", 16.0, None, None),
    ("evidence", "terminal", [19], None, "Execution evidence", 10.0, None, None),
    ("close", "payoff", [20], None, "", 11.0, None, None),
]

# spoken form -> subtitle form (digits read faster than words)
SUBS_FORM = [
    ("four hundred and fifty euros", "450 euros"), ("two thousand six hundred dollars", "2,600 dollars"),
    ("two thousand two hundred euros", "2,200 euros"), ("two thousand euros", "2,000 euros"),
    ("six hundred euros", "600 euros"), ("twenty hours", "20 hours"), ("fifty euros", "50 euros"),
    ("make it forty", "make it 40"), ("four thousand euro", "4,000 euro"), ("seven known attacks", "7 known attacks"),
    ("All seven", "All 7"), ("sixty-nine of seventy", "69 of 70"), ("sixty-six of seventy", "66 of 70"),
    ("twenty-one scripted missions", "21 scripted missions"),
]


def cues() -> list[str]:
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", SCRIPT.read_text(encoding="utf-8")) if p.strip()]
    if len(paras) != 20:
        raise SystemExit(f"{SCRIPT} must have 20 paragraphs (one per cue); found {len(paras)}")
    return paras


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / float(w.getframerate())


def voice_lengths(texts: list[str]) -> tuple[list[float], bool]:
    have = [VO_DIR / f"{i:02d}.wav" for i in range(1, 21)]
    if all(p.exists() for p in have):
        return [wav_seconds(p) for p in have], True
    return [len(t.split()) / WPM * 60 for t in texts], False


def subtitle_chunks(text: str, limit: int = 84) -> list[str]:
    for a, b in SUBS_FORM:
        text = text.replace(a, b)
    parts = re.split(r"(?<=[.!?:;])\s+", text)
    out: list[str] = []
    for part in parts:
        while len(part) > limit:                             # split a long sentence at the comma nearest the middle
            cut = [m.start() for m in re.finditer(r",\s", part)]
            mid = len(part) / 2
            k = min(cut, key=lambda c: abs(c - mid)) + 1 if cut else part.rfind(" ", 0, limit)
            out.append(part[:k].strip())
            part = part[k:].strip()
        if part:
            if out and len(out[-1]) + len(part) + 1 <= limit * 0.62:
                out[-1] = out[-1] + " " + part            # glue a very short sentence to the previous one
            else:
                out.append(part)
    return out


def zoom_keys(box: list[float] | None, rel_f: int, dur: int, smax: float) -> list[dict]:
    if not box:
        return [{"f": 0, "s": 1, "x": 960, "y": 540}]
    x, y, w, h = box
    s = max(1.0, min(smax, 0.86 * 1920 / max(w, 1), 0.86 * 1080 / max(h, 1)))
    cx = min(max(x + w / 2, 960 / s), 1920 - 960 / s)
    cy = min(max(y + h / 2, 540 / s), 1080 - 540 / s)
    f0 = max(6, min(rel_f + 6, dur - 70))
    k = [{"f": 0, "s": 1, "x": 960, "y": 540}, {"f": f0, "s": 1, "x": 960, "y": 540},
         {"f": f0 + 40, "s": round(s, 3), "x": round(cx, 1), "y": round(cy, 1)}]
    if dur - 24 > f0 + 40:
        k += [{"f": dur - 24, "s": round(s, 3), "x": round(cx, 1), "y": round(cy, 1)}, {"f": dur - 2, "s": 1, "x": 960, "y": 540}]
    return k


# seconds of picture left for the jury to read the evidence after the narration of a scene (scaled to fit the target)
HOLD = {"quarantine": 1.5, "note": 1.0, "plan": 1.0, "code": 1.0, "receipts": 1.2, "deletion": 1.2, "correction": 0.8,
        "payee": 1.0, "limit": 1.0, "attacks": 1.6, "measured": 1.6, "evidence": 1.0}
TARGET = 280.0                 # 4:40, in the 4:30-4:50 window asked for; never above MAX_SECONDS


def plan() -> dict:
    texts = cues()
    vo, measured = voice_lengths(texts)
    mk = json.loads(MARKERS.read_text(encoding="utf-8"))
    best = None
    for k10 in range(0, 16):                         # hold factor 0.0 .. 1.5: closest to TARGET, never over the limit
        cand = _build(texts, vo, measured, mk, k10 / 10)
        if cand["total"] / FPS > MAX_SECONDS - 3:
            continue
        if best is None or abs(cand["total"] / FPS - TARGET) < abs(best["total"] / FPS - TARGET):
            best = cand
    if best is None:
        raise SystemExit("even without the reading holds the video would pass 5:00: use a faster voice (Speed 1.05)")
    out = best
    total = out["total"]
    PLAN.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    words = sum(len(x.split()) for x in texts)
    print(f"plan: {len(out['scenes'])} scenes, {total / FPS:.1f}s ({int(total / FPS // 60)}:{int(total / FPS % 60):02d}), "
          f"{words} words, voice {'measured' if measured else f'estimated at {WPM} wpm'}, reading holds x{out['hold_k']}")
    sys.path.insert(0, str(ROOT / "scripts"))
    import demo_storyboard                              # the storyboard's timecodes follow the plan
    demo_storyboard.write(out, texts)
    return out


def _build(texts: list[str], vo: list[float], measured: bool, mk: dict, hold_k: float) -> dict:
    rec_scenes, rec_boxes = mk["scenes"], mk.get("boxes", {})
    scenes, voice, subs = [], [], []
    t = 0.0
    for sid, kind, cue_ids, marker, chapter, min_s, zoom, callout in SCENES:
        s = {"id": sid, "kind": kind, "from": round(t * FPS), "chapter": chapter}
        if kind == "hook":
            a = max(7.0, LEAD + vo[0] + 0.5)
            b = max(8.5, 0.25 + vo[1] + PAD)
            dur = max(min_s, a + b)
            s["a"] = round(a * FPS)
            starts = [t + LEAD, t + a + 0.25]
        else:
            need = LEAD + sum(vo[c - 1] for c in cue_ids) + 0.25 * (len(cue_ids) - 1) + PAD + HOLD.get(sid, 0) * hold_k
            dur = max(min_s, need)
            starts = [t + LEAD]
            if kind == "rec":
                m = rec_scenes[marker]
                clip = m["end"] - m["start"]
                s["rec"] = {"start": round(m["start"] * FPS), "end": round(m["end"] * FPS)}
                dur = max(dur, clip + 0.2)
        dur_f = round(dur * FPS)
        s["dur"] = dur_f
        if kind == "brand":                             # the team card appears when the narration says "We are Team Centrale"
            words = texts[cue_ids[0] - 1].split()
            k = next((i for i, w in enumerate(words) if w == "We"), len(words) // 2)
            s["team_from"] = round((LEAD + vo[cue_ids[0] - 1] * k / len(words)) * FPS)
        if kind == "measured":                          # the Qwen result lands when the narration names it
            words = texts[cue_ids[0] - 1].split()
            k = next((i for i in range(len(words) - 2) if words[i + 2].startswith("Qwen")), len(words) // 2)
            said = (LEAD + vo[cue_ids[0] - 1] * k / len(words)) * FPS
            s["warp"] = round(min(1.0, max(0.55, 290 / max(1.0, said))), 3)
        if kind == "payoff":                            # the three lines rise as the voice says them
            words = texts[cue_ids[0] - 1].split()
            k = next((i for i in range(len(words) - 2) if words[i:i + 3] == ["The", "AI", "reads."]), 0)
            s["principle_from"] = round((LEAD + vo[cue_ids[0] - 1] * k / len(words)) * FPS)
            s["dur"] = dur_f = max(dur_f, s["principle_from"] + 230)
        if kind == "rec":
            box, rel = None, 0
            if zoom:
                found = [b for b in rec_boxes.get(marker, []) if b["name"] in zoom[0].split("+")]
                if found:                                   # one box, or the union of several ("balances+payees")
                    x0 = min(b["box"][0] for b in found)
                    y0 = min(b["box"][1] for b in found)
                    x1 = max(b["box"][0] + b["box"][2] for b in found)
                    y1 = max(b["box"][1] + b["box"][3] for b in found)
                    box = [x0, y0, x1 - x0, y1 - y0]
                    rel = round((min(b["t"] for b in found) - rec_scenes[marker]["start"]) * FPS)
            s["zoom"] = zoom_keys(box, rel, dur_f, zoom[1] if zoom else 1.0)
        if callout:
            c_from = 24 if kind != "rec" or not zoom else max(24, s["zoom"][1]["f"] + 30)
            c_from = min(c_from, max(0, dur_f - 150))
            s["callout"] = {"text": callout[0], "tone": callout[1], "from": c_from, "dur": min(150, dur_f - c_from - 8)}
        for c, st in zip(cue_ids, starts):
            voice.append({"id": f"{c:02d}", "from": round(st * FPS), "src": f"vo/{c:02d}.wav"})
            chunks = subtitle_chunks(texts[c - 1])
            total_chars = sum(len(x) for x in chunks)
            u = st
            for ch in chunks:
                d = vo[c - 1] * len(ch) / total_chars
                subs.append({"from": round(u * FPS), "to": round((u + d) * FPS), "text": ch})
                u += d
        scenes.append(s)
        t += dur_f / FPS
    total = round(t * FPS) + FPS                        # one second of the closing frame
    scenes[-1]["dur"] += FPS
    return {"fps": FPS, "total": total, "stage": STAGE, "rec": "rec/demo.mp4", "scenes": scenes,
            "voice": voice if measured else [], "subs": subs, "terminal": TERMINAL, "team": TEAM, "topic": TOPIC,
            "voice_measured": measured, "hold_k": hold_k}


def srt(p: dict, path: Path) -> None:
    def ts(f: int) -> str:
        ms = round(f / FPS * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    lines = []
    for i, s in enumerate(p["subs"], 1):
        lines += [str(i), f"{ts(s['from'])} --> {ts(s['to'])}", s["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {path}")


def sync(audio: Path) -> None:
    """Split one ElevenLabs file into the 20 cues at the pauses that best match the paragraph breaks."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    VO_DIR.mkdir(parents=True, exist_ok=True)
    full = VO_DIR / "_full.wav"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "44100", str(full)], check=True)
    total = wav_seconds(full)
    r = subprocess.run([ff, "-hide_banner", "-i", str(full), "-af", "silencedetect=noise=-40dB:d=0.25", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    sil = [(a, b) for a, b in zip(starts, ends)]
    head = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    tail = sil[-1][0] if sil and sil[-1][1] >= total - 0.05 else total
    inner = [x for x in sil if x[0] > head + 0.2 and x[1] < tail - 0.2]
    texts = cues()
    w = [len(x.split()) for x in texts]
    speech = tail - head
    cuts: list[tuple[float, float]] = []
    prev = head
    for k in range(1, 20):
        # where paragraph k should end, re-anchored on the cut before it (the pace of the remaining words)
        span = (tail - prev) * w[k - 1] / sum(w[k - 1:])
        expect = prev + span
        cand = [x for x in inner if x[0] > prev + 0.45 * span and x[0] < prev + 1.8 * span + 1.0]
        if not cand:
            cand = [x for x in inner if x[0] > prev + 0.6]
        if not cand:
            raise SystemExit(f"no pause found for the break before paragraph {k + 1}")
        # the paragraph break is the long pause near where the words say it should be
        best = min(cand, key=lambda x: abs((x[0] + x[1]) / 2 - expect) - 2.5 * (x[1] - x[0]))
        cuts.append(best)
        prev = best[1]
    _ = speech
    bounds = [(head, cuts[0][0])] + [(cuts[i][1], cuts[i + 1][0]) for i in range(18)] + [(cuts[18][1], tail)]
    for i, (a, b) in enumerate(bounds, 1):
        a2, b2 = max(0.0, a - 0.05), min(total, b + 0.12)
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(full), "-ss", f"{a2:.3f}", "-to", f"{b2:.3f}",
                        "-af", "afade=t=in:d=0.02,areverse,afade=t=in:d=0.04,areverse", str(VO_DIR / f"{i:02d}.wav")],
                       check=True)
        pace = w[i - 1] / max(0.1, b2 - a2) * 60
        flag = "   <-- check this cut" if not 105 <= pace <= 205 else ""
        print(f"cue {i:02d}: {a2:7.2f}-{b2:7.2f}s ({b2 - a2:5.2f}s, {w[i - 1]} words, {pace:.0f} wpm)  "
              f"{texts[i - 1][:50]}{flag}")
    full.unlink()


def render(out: Path) -> None:
    p = plan()
    remotion = FILM / "node_modules" / ".bin" / ("remotion.cmd" if sys.platform == "win32" else "remotion")
    tmp = FILM / "out" / "demo-video.mp4"
    subprocess.run([str(remotion), "render", "src/index.ts", "DemoVideo", str(tmp), "--codec=h264", "--crf=18",
                    "--pixel-format=yuv420p", "--audio-codec=aac"], check=True, cwd=FILM)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve() != tmp.resolve():
        out.write_bytes(tmp.read_bytes())
    srt(p, out.with_suffix(".en.srt"))
    print(f"wrote {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("sync")
    s.add_argument("audio", type=Path)
    r = sub.add_parser("render")
    r.add_argument("--out", type=Path, default=FILM / "out" / "demo-video.mp4")
    a = ap.parse_args()
    if a.cmd == "plan":
        srt(plan(), FILM / "out" / "demo-video.en.srt")
    elif a.cmd == "sync":
        sync(a.audio)
        srt(plan(), FILM / "out" / "demo-video.en.srt")
    else:
        render(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
