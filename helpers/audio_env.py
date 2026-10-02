"""Audio energy envelope and quiet-point snapping.

Shared by transcribe.py (the local engine trims each word's end to where its
energy actually stops) and useful on its own to nudge a cut edge onto the
quietest 10 ms near it — the cleanest place to cut.

Usage:
    python helpers/audio_env.py snap <video> 12.40 18.95 [--window 0.25]
    python helpers/audio_env.py profile <video> 12.0 13.0
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

HOP_S = 0.01   # one envelope value per 10 ms


def extract_wav(video: Path, dest: Path, audio_track: int = 0) -> None:
    """Mono 16 kHz PCM — what both transcription engines want."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-map", f"0:a:{audio_track}",
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: expected 16-bit PCM")
    y = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        y = y.reshape(-1, ch).mean(axis=1)
    return y, sr


def envelope_db(y: np.ndarray, sr: int, smooth: int = 5) -> np.ndarray:
    """RMS level per 10 ms in dBFS, lightly smoothed."""
    hop = max(1, int(sr * HOP_S))
    n = len(y) // hop
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-9)
    if smooth > 1:
        db = np.convolve(db, np.ones(smooth) / smooth, mode="same")
    return db


def video_envelope(video: Path, audio_track: int = 0) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        extract_wav(video, wav, audio_track)
        y, sr = read_wav_mono(wav)
    return envelope_db(y, sr)


def noise_floor(env: np.ndarray) -> float:
    return float(np.percentile(env, 10)) if env.size else -90.0


def snap(env: np.ndarray, t: float, window: float = 0.25) -> tuple[float, float]:
    """Quietest 10 ms frame within ±window of t → (time, dB)."""
    i0 = max(0, int((t - window) / HOP_S))
    i1 = min(len(env), int((t + window) / HOP_S) + 1)
    if i1 <= i0:
        return t, float("nan")
    i = i0 + int(np.argmin(env[i0:i1]))
    return i * HOP_S, float(env[i])


def main() -> None:
    ap = argparse.ArgumentParser(description="Energy envelope tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snap", help="Nearest quiet point to each time")
    s.add_argument("video", type=Path)
    s.add_argument("times", type=float, nargs="+")
    s.add_argument("--window", type=float, default=0.25)
    s.add_argument("--audio-track", type=int, default=0)
    p = sub.add_parser("profile", help="Print the level every 50 ms in a range")
    p.add_argument("video", type=Path)
    p.add_argument("start", type=float)
    p.add_argument("end", type=float)
    p.add_argument("--audio-track", type=int, default=0)
    args = ap.parse_args()

    env = video_envelope(args.video, args.audio_track)
    floor = noise_floor(env)
    if args.cmd == "snap":
        for t in args.times:
            st, db = snap(env, t, args.window)
            print(f"{t:8.3f} -> {st:8.3f}  ({db:.1f} dB, floor {floor:.1f} dB)")
    else:
        i0, i1 = int(args.start / HOP_S), int(args.end / HOP_S)
        print(" ".join(f"{env[i]:.0f}" for i in range(i0, min(i1, len(env)), 5)))


if __name__ == "__main__":
    main()
