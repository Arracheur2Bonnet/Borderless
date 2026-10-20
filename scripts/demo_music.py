"""Original background music for the demo video, and its mix under the voice.

    ./.venv/Scripts/python scripts/demo_music.py loop                     # writes media/launch-video/public/music/loop.wav
    ./.venv/Scripts/python scripts/demo_music.py mix IN.mp4 OUT.mp4        # loops it under the whole video, ducked under the voice

The music is synthesised here (no sample, no third-party audio), so it needs no licence: a 32-second loop at 120 BPM in
A minor (Am - F - C - G), with a soft four-on-the-floor kick, off-beat hats, a clap on 2 and 4, a pumping sub bass, a
wide pad and a plucked arpeggio that moves up an octave in the second half. The mix keeps the video stream untouched,
loops the music for the whole length, fades it in and out, ducks it whenever the voice (or a UI sound) plays, and the
final loudness is normalised to -16 LUFS.
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "media" / "launch-video" / "public" / "music"
SR = 48000
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
BARS = 16
N = int(BARS * BAR * SR)
rng = np.random.default_rng(7)
t_all = np.arange(N) / SR


def note(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def place(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0) -> None:
    """Add `sig` at `at` seconds; a tail that runs past the end wraps to the start, so the loop joins without a click."""
    i = int(at * SR) % len(buf)
    k = min(len(sig), len(buf) - i)
    buf[i:i + k] += gain * sig[:k]
    if k < len(sig):
        buf[: len(sig) - k] += gain * sig[k:]


def kick() -> np.ndarray:
    n = int(0.32 * SR)
    t = np.arange(n) / SR
    f = 46 + 74 * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
    click = np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 300) * 0.25
    return body + click


def hat() -> np.ndarray:
    n = int(0.05 * SR)
    x = np.diff(rng.standard_normal(n + 1))                 # first difference: a crude high-pass
    return x * np.exp(-np.arange(n) / SR * 90) * 0.35


def clap() -> np.ndarray:
    n = int(0.18 * SR)
    t = np.arange(n) / SR
    x = np.diff(rng.standard_normal(n + 1)) * 0.5 + rng.standard_normal(n) * 0.5
    env = np.exp(-t * 22) * (1 + 0.6 * (np.sin(2 * np.pi * 90 * t) > 0))
    return x * env * 0.4


def tone(freq: float, dur: float, harmonics: int, decay: float, attack: float = 0.004) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * freq * k * t) / k ** 1.4 for k in range(1, harmonics + 1))
    env = np.minimum(1, t / attack) * np.exp(-t * decay)
    return s * env


# chords (two bars each), as MIDI notes; roots for the bass
CHORDS = [[57, 60, 64], [53, 57, 60], [60, 64, 67], [55, 59, 62]]           # Am, F, C, G
ROOTS = [45, 41, 48, 43]                                                     # A2, F2, C3, G2

drums = np.zeros(N)
bass = np.zeros(N)
pad_l, pad_r = np.zeros(N), np.zeros(N)
arp_l, arp_r = np.zeros(N), np.zeros(N)
hats_l, hats_r = np.zeros(N), np.zeros(N)
K, H, C = kick(), hat(), clap()

for b in range(BARS):
    t0 = b * BAR
    ci = (b // 2) % 4
    for q in range(4):                                       # kick on every beat, clap on 2 and 4, hats off-beat
        place(drums, K, t0 + q * BEAT, 0.95)
        if q in (1, 3):
            place(drums, C, t0 + q * BEAT, 0.55)
        place(hats_l if q % 2 else hats_r, H, t0 + q * BEAT + BEAT / 2, 0.55)
        if b >= 8:                                           # second half: 16th hats for drive
            place(hats_r if q % 2 else hats_l, H, t0 + q * BEAT + BEAT / 4, 0.25)
            place(hats_l if q % 2 else hats_r, H, t0 + q * BEAT + 3 * BEAT / 4, 0.25)
    for e in range(8):                                       # bass: eighth notes on the root, octave on the off-beat
        m = ROOTS[ci] + (12 if e % 2 else 0)
        place(bass, tone(note(m), BEAT / 2 * 0.95, 3, 7.0, 0.006), t0 + e * BEAT / 2, 0.9 if e % 2 == 0 else 0.55)
    if b % 2 == 0:                                           # pad: one chord over two bars, slightly detuned per side
        dur = 2 * BAR
        n = int(dur * SR)
        t = np.arange(n) / SR
        env = np.minimum(1, t / 0.35) * np.minimum(1, (dur - t) / 0.4)
        for m in CHORDS[ci]:
            f = note(m)
            sl = sum(np.sin(2 * np.pi * f * 1.003 * k * t) / k ** 2 for k in range(1, 6))
            sr_ = sum(np.sin(2 * np.pi * f * 0.997 * k * t) / k ** 2 for k in range(1, 6))
            place(pad_l, sl * env, t0, 0.33)
            place(pad_r, sr_ * env, t0, 0.33)
    tones = CHORDS[ci] + [CHORDS[ci][0] + 12]
    up = 12 if b >= 8 else 0
    for s16 in range(16):                                    # plucked arpeggio, sixteenth notes
        m = tones[[0, 1, 2, 3, 2, 1, 2, 3][s16 % 8]] + up
        pl = tone(note(m), 0.22, 4, 16.0)
        place(arp_l if s16 % 2 else arp_r, pl, t0 + s16 * BEAT / 4, 0.42 if s16 % 4 == 0 else 0.3)

# sidechain pump on the pad and the bass: dip after every kick
since = (t_all % BEAT)
pump = 1 - 0.55 * np.exp(-since / 0.11)
bass *= pump
pad_l *= pump
pad_r *= pump

left = 0.95 * drums + 0.55 * bass + 0.42 * pad_l + 0.30 * arp_l + 0.30 * hats_l + 0.10 * arp_r
right = 0.95 * drums + 0.55 * bass + 0.42 * pad_r + 0.30 * arp_r + 0.30 * hats_r + 0.10 * arp_l
mix = np.stack([left, right], axis=1)
mix = np.tanh(mix / np.max(np.abs(mix)) * 1.4) / np.tanh(1.4)       # gentle saturation, no hard clipping
mix *= 10 ** (-1.0 / 20)


def write_loop() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "loop.wav"
    with wave.open(str(path), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())
    print(f"wrote {path} ({BARS} bars at {BPM} BPM = {N / SR:.1f} s, seamless loop)")
    return path


def mix_into(src: Path, dst: Path, music_lufs: float = -20.0) -> None:
    """Loop the music under the whole video, ducked by the voice, then normalise the result (two passes)."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    loop = write_loop()
    norm = OUT / "loop_norm.wav"                             # the loop at a fixed loudness, so its level is predictable
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(loop), "-af", f"loudnorm=I={music_lufs}:TP=-6:LRA=11", "-ar", "48000",
                    str(norm)], check=True)
    dur = float(subprocess.run([ff, "-i", str(src)], capture_output=True, text=True).stderr.split("Duration: ")[1].split(",")[0]
                .split(":")[-1]) + 60 * int(subprocess.run([ff, "-i", str(src)], capture_output=True, text=True).stderr
                                            .split("Duration: ")[1].split(":")[1])
    tmp = dst.with_name(dst.stem + "_premix.mp4")
    fc = (f"[1:a]atrim=0:{dur:.3f},asetpts=N/SR/TB,afade=t=in:d=2.5,afade=t=out:st={dur - 4:.3f}:d=4[m];"
          "[0:a]asplit=2[v][sc];"
          "[m][sc]sidechaincompress=threshold=0.02:ratio=4:attack=25:release=450:knee=4[md];"
          "[v][md]amix=inputs=2:duration=first:normalize=0[out]")
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-stream_loop", "-1", "-i", str(norm), "-filter_complex", fc,
                    "-map", "0:v", "-c:v", "copy", "-map", "[out]", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", str(tmp)], check=True)
    # final loudness: -16 LUFS integrated, true peak -1.5 dBFS (two-pass, linear when possible)
    import json
    import re
    r = subprocess.run([ff, "-hide_banner", "-i", str(tmp), "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f",
                        "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r, re.S).group(0))
    af = (f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(tmp), "-c:v", "copy", "-af", af, "-ar", "48000", "-c:a", "aac", "-b:a",
                    "256k", "-movflags", "+faststart", str(dst)], check=True)
    tmp.unlink()
    print(f"wrote {dst}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "mix":
        mix_into(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        write_loop()
