"""Audio energy envelope and quiet-point snapping.

Shared by transcribe.py (the local engine trims each word's end to where its
energy actually stops) and useful on its own to nudge a cut edge onto the
quietest 10 ms near it — the cleanest place to cut.

Usage:
    python helpers/audio_env.py snap <video> 12.40 18.95 [--window 0.25]
    python helpers/audio_env.py profile <video> 12.0 13.0
    python helpers/audio_env.py islands <video> [--start 0 --end 60]     # real speech spans
    python helpers/audio_env.py tighten <video> 5.22-10.36 13.64-15.44  # drop pauses >= 160 ms
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


def speech_mask(env: np.ndarray, threshold: float | None = None, margin: float = 6.0) -> tuple[np.ndarray, float]:
    """Frames louder than the threshold (default: noise floor + margin dB)."""
    th = threshold if threshold is not None else noise_floor(env) + margin
    return env > th, th


def islands(env: np.ndarray, mask: np.ndarray, merge: float = 0.35, min_len: float = 0.08,
            t0: float = 0.0, t1: float | None = None) -> list[tuple[float, float]]:
    """Speech islands: runs of loud frames, merging gaps shorter than `merge` s."""
    i0 = int(t0 / HOP_S)
    i1 = len(env) if t1 is None else min(len(env), int(t1 / HOP_S))
    runs: list[list[float]] = []
    i = i0
    while i < i1:
        if mask[i]:
            j = i
            while j < i1 and mask[j]:
                j += 1
            a, b = i * HOP_S, j * HOP_S
            if runs and a - runs[-1][1] < merge:
                runs[-1][1] = b
            else:
                runs.append([a, b])
            i = j
        else:
            i += 1
    return [(round(a, 3), round(b, 3)) for a, b in runs if b - a >= min_len]


def tighten(env: np.ndarray, mask: np.ndarray, spans: list[tuple[float, float]], min_gap: float = 0.16,
            keep_before: float = 0.04, keep_after: float = 0.03) -> list[tuple[float, float]]:
    """Split each kept span at internal pauses >= min_gap, keeping a little air on both sides.

    Whisper timestamps hide these pauses inside words; the envelope does not.
    """
    out: list[tuple[float, float]] = []
    for s, e in spans:
        cur = s
        i, i1 = int(s / HOP_S), min(len(env), int(e / HOP_S))
        while i < i1:
            if not mask[i]:
                j = i
                while j < i1 and not mask[j]:
                    j += 1
                g0, g1 = i * HOP_S, j * HOP_S
                if g1 - g0 >= min_gap and g0 > s + 0.05 and g1 < e - 0.05:
                    out.append((round(cur, 3), round(g0 + keep_before, 3)))
                    cur = g1 - keep_after
                i = j
            else:
                i += 1
        out.append((round(cur, 3), round(e, 3)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Energy envelope tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i_ = sub.add_parser("islands", help="Speech islands (where talking really happens) — catches pauses "
                                        "that whisper smears into a single long word")
    i_.add_argument("video", type=Path)
    i_.add_argument("--start", type=float, default=0.0)
    i_.add_argument("--end", type=float)
    i_.add_argument("--threshold", type=float, help="dBFS (default: noise floor + --margin)")
    i_.add_argument("--margin", type=float, default=8.0, help="dB above the noise floor that counts as speech")
    i_.add_argument("--merge", type=float, default=0.35, help="merge gaps shorter than this (s)")
    i_.add_argument("--audio-track", type=int, default=0)
    t_ = sub.add_parser("tighten", help="Split kept spans at internal pauses -> tight jump-cut ranges (JSON)")
    t_.add_argument("video", type=Path)
    t_.add_argument("spans", nargs="+", help="start-end in source seconds, e.g. 5.22-10.36")
    t_.add_argument("--min-gap", type=float, default=0.16)
    t_.add_argument("--threshold", type=float)
    t_.add_argument("--margin", type=float, default=12.0,
                    help="dB above the floor; higher than islands' so short breaths between words count as gaps")
    t_.add_argument("--audio-track", type=int, default=0)
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
    elif args.cmd == "islands":
        mask, th = speech_mask(env, args.threshold, args.margin)
        isl = islands(env, mask, args.merge, t0=args.start, t1=args.end)
        print(f"# threshold {th:.1f} dBFS (floor {floor:.1f}); {len(isl)} islands")
        for a, b in isl:
            print(f"{a:8.2f} - {b:8.2f}  ({b - a:5.2f} s)")
    elif args.cmd == "tighten":
        import json
        mask, th = speech_mask(env, args.threshold, args.margin)
        spans = [tuple(float(x) for x in s.split("-", 1)) for s in args.spans]
        ranges = tighten(env, mask, spans, args.min_gap)
        print(json.dumps([list(r) for r in ranges]))
        print(f"# {len(ranges)} ranges, {sum(b - a for a, b in ranges):.2f} s kept (threshold {th:.1f} dBFS)")
    else:
        i0, i1 = int(args.start / HOP_S), int(args.end / HOP_S)
        print(" ".join(f"{env[i]:.0f}" for i in range(i0, min(i1, len(env)), 5)))


if __name__ == "__main__":
    main()
