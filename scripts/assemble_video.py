"""Assemble the official demo video: the launch film, then the recorded demo, as one MP4 with English subtitles.

    ./.venv/Scripts/python scripts/assemble_video.py --demo PATH/TO/recorded_demo.mp4
        [--film media/launch-video/out/borderlessguard-launch.mp4]
        [--demo-srt deliverables/demo/demo.en.srt] [--out media/final/BorderlessGuard-demo-video.mp4]

- Both parts are normalised to 1920x1080, 30 fps, H.264 (CRF 18) and AAC 48 kHz stereo. A part without sound
  (the film before its voice-over is recorded) gets silence, so the two parts always join cleanly.
- Subtitles: the film's SRT (written by `npm run captions` in media/launch-video) followed by the demo's SRT, shifted
  by the film's length. Written next to the video as <name>.en.srt.
- The competition accepts at most 5 minutes. The script prints the total and fails if it is longer.

It uses the ffmpeg binary that ships with imageio-ffmpeg (already in the project's virtual environment).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
LIMIT_S = 5 * 60
PAPER = "0xf6f5f1"


def probe(path: Path) -> tuple[float, bool]:
    """(duration in seconds, has an audio stream), read from ffmpeg's own report."""
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        raise SystemExit(f"cannot read {path}: is it a video file?\n{out[-400:]}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s), bool(re.search(r"Stream #\d+:\d+.*Audio:", out))


def srt_cues(path: Path) -> list[tuple[float, float, str]]:
    def sec(t: str) -> float:
        h, m, rest = t.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.strip().splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = [x.strip() for x in lines[1].split("-->")]
            cues.append((sec(a), sec(b), "\n".join(lines[2:])))
    return cues


def ts(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", required=True, type=Path, help="the recorded demo (any format ffmpeg reads)")
    ap.add_argument("--film", type=Path, default=ROOT / "media/launch-video/out/borderlessguard-launch.mp4")
    ap.add_argument("--film-srt", type=Path, default=ROOT / "media/launch-video/out/borderlessguard-launch.en.srt")
    ap.add_argument("--demo-srt", type=Path, default=ROOT / "deliverables/demo/demo.en.srt")
    ap.add_argument("--out", type=Path, default=ROOT / "media/final/BorderlessGuard-demo-video.mp4")
    a = ap.parse_args()

    for p in (a.film, a.demo):
        if not p.exists():
            raise SystemExit(f"missing: {p}")
    d_film, film_audio = probe(a.film)
    d_demo, demo_audio = probe(a.demo)
    total = d_film + d_demo
    print(f"film {d_film:.1f} s ({'sound' if film_audio else 'silent'}), demo {d_demo:.1f} s "
          f"({'sound' if demo_audio else 'silent'}), total {total:.1f} s")
    if total > LIMIT_S:
        raise SystemExit(f"too long: {total:.1f} s is over the 5-minute limit; shorten the demo recording")

    cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(a.film), "-i", str(a.demo)]
    silent = []
    for i, (has, d) in enumerate(((film_audio, d_film), (demo_audio, d_demo))):
        if not has:
            silent.append(i)
            cmd += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    v = ("scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=" + PAPER
         + ",fps=30,format=yuv420p,setsar=1")
    graph, k = [], 2
    for i in range(2):
        graph.append(f"[{i}:v]{v}[v{i}]")
        src = f"{k}:a" if i in silent else f"{i}:a"
        if i in silent:
            k += 1
        graph.append(f"[{src}]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    graph.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(a.out)]
    subprocess.run(cmd, check=True)

    cues = []
    if a.film_srt.exists():
        cues += srt_cues(a.film_srt)
    else:
        print(f"note: no film subtitles at {a.film_srt} (run `npm run captions` in media/launch-video)")
    if a.demo_srt.exists():
        cues += [(s + d_film, e + d_film, t) for s, e, t in srt_cues(a.demo_srt)]
    else:
        print(f"note: no demo subtitles at {a.demo_srt}")
    if cues:
        srt = a.out.with_suffix(".en.srt")
        srt.write_text("\n".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n" for i, (s, e, t) in enumerate(cues, 1)), encoding="utf-8")
        print(f"wrote {srt}")
    d_out, _ = probe(a.out)
    print(f"wrote {a.out}: {d_out:.1f} s (limit {LIMIT_S} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
