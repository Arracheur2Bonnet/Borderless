"""Original UI sounds for the demo video, synthesised here (no third-party audio): a soft whoosh for cuts, a low thud for
a refusal or a quarantine, a two-note chime for a verified result. Written to media/launch-video/public/sfx/.

    ./.venv/Scripts/python scripts/demo_sfx.py
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "media" / "launch-video" / "public" / "sfx"
SR = 48000
rng = np.random.default_rng(20261020)


def write(name: str, x: np.ndarray, peak_db: float) -> None:
    x = x / max(1e-9, np.max(np.abs(x))) * (10 ** (peak_db / 20))
    stereo = np.stack([x, x], axis=1)
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((stereo * 32767).astype("<i2").tobytes())


def lowpass(x: np.ndarray, cutoff: np.ndarray) -> np.ndarray:
    """One-pole low-pass with a time-varying cutoff (Hz)."""
    y = np.zeros_like(x)
    a = np.exp(-2 * np.pi * cutoff / SR)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a[i]) * x[i] + a[i] * acc
        y[i] = acc
    return y


# whoosh: filtered noise whose cutoff rises then falls, with a soft swell
n = int(0.42 * SR)
t = np.linspace(0, 1, n)
noise = rng.standard_normal(n)
cut = 300 + 3200 * np.sin(np.pi * t) ** 2
env = np.sin(np.pi * t) ** 1.6
write("whoosh.wav", lowpass(noise, cut) * env, -20)

# thud: a short low sine drop with a little noise transient
n = int(0.32 * SR)
t = np.arange(n) / SR
f = 95 * np.exp(-t * 5) + 48
phase = 2 * np.pi * np.cumsum(f) / SR
thud = np.sin(phase) * np.exp(-t * 11) + 0.25 * lowpass(rng.standard_normal(n), np.full(n, 1800.0)) * np.exp(-t * 60)
write("thud.wav", thud, -9)

# chime: two soft bell tones (C6, G6) with a quick decay
n = int(0.75 * SR)
t = np.arange(n) / SR
bell = lambda fr, d: np.sin(2 * np.pi * fr * t) * np.exp(-t * d) + 0.18 * np.sin(2 * np.pi * fr * 2.01 * t) * np.exp(-t * d * 1.6)  # noqa: E731
chime = bell(1046.5, 7.5) + 0.8 * np.concatenate([np.zeros(int(0.07 * SR)), bell(1568.0, 7.5)[: n - int(0.07 * SR)]])
chime *= np.minimum(1, t / 0.004)
write("chime.wav", chime, -12)
print("wrote", sorted(p.name for p in OUT.glob("*.wav")))
