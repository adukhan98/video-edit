"""Transcribe a video to word-level JSON (Scribe-shaped) — hosted or fully local.

Engines:
  elevenlabs  ElevenLabs Scribe: verbatim (keeps "um"/"uh"), speaker diarization,
              audio events, accurate word timing. Needs ELEVENLABS_API_KEY.
  whisper     Local whisper.cpp (`whisper-cli`) with DTW token timestamps. Free and
              offline; one speaker; word starts are shifted 0.12 s earlier (DTW lands
              ~0.1-0.15 s after the real onset — measured) and word ends are trimmed to
              where the speech energy stops. Brand names passed with --vocab bias spelling.
  auto        (default) elevenlabs when a key exists, otherwise whisper.

Both write the same shape to <edit_dir>/transcripts/<video_stem>.json, so every
other helper works the same either way. Cached: an existing transcript is never
redone (delete the file to force it).

Usage:
    python helpers/transcribe.py <video>
    python helpers/transcribe.py <video> --engine whisper --vocab "Muse, Jolly, OpenClaw"
    python helpers/transcribe.py <video> --language es            # whisper uses a multilingual model
    python helpers/transcribe.py <video> --num-speakers 2         # elevenlabs only
    python helpers/transcribe.py <video> --audio-track 1          # OBS: mic on track 1
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_env import HOP_S, envelope_db, extract_wav, noise_floor, read_wav_mono  # noqa: E402


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"

WHISPER_BINS = ("whisper-cli", "whisper-cpp")
DTW_PRESETS = {
    "tiny": "tiny", "tiny.en": "tiny.en", "base": "base", "base.en": "base.en",
    "small": "small", "small.en": "small.en", "medium": "medium", "medium.en": "medium.en",
    "large-v1": "large.v1", "large-v2": "large.v2", "large-v3": "large.v3",
    "large-v3-turbo": "large.v3.turbo",
}
DTW_SHIFT_S = 0.12
# An initial prompt full of disfluencies keeps Whisper from silently dropping fillers,
# which the editor needs to see in order to cut them.
VERBATIM_PROMPT = "Umm, so, uh, I was like, you know... Hmm. Okay, so, um, here's the thing."


# -------- shared ---------------------------------------------------------------


def find_api_key() -> str | None:
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "ELEVENLABS_API_KEY" and v.strip().strip('"').strip("'"):
                    return v.strip().strip('"').strip("'")
    return os.environ.get("ELEVENLABS_API_KEY") or None


def load_api_key() -> str:
    key = find_api_key()
    if not key:
        sys.exit("ELEVENLABS_API_KEY not found in .env or environment")
    return key


def resolve_engine(engine: str = "auto") -> tuple[str, str | None]:
    """→ (engine, api_key or None)."""
    key = find_api_key()
    if engine == "elevenlabs":
        if not key:
            sys.exit("--engine elevenlabs needs ELEVENLABS_API_KEY (in the skill's .env or the environment)")
        return "elevenlabs", key
    if engine == "whisper":
        return "whisper", None
    return ("elevenlabs", key) if key else ("whisper", None)


def count_audio_tracks(video_path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


def peak_dbfs(wav_path: Path) -> float:
    """Peak level of a 16-bit PCM wav, in dBFS. -inf for digital silence."""
    peak = 0
    with wave.open(str(wav_path), "rb") as w:
        # A chunk at a time: batch mode runs several of these at once, and a two-hour
        # take is 230 MB of 16 kHz mono before the array copy doubles it.
        while frames := w.readframes(1 << 16):
            samples = array.array("h", frames)
            peak = max(peak, max(samples), -min(samples))
    return 20 * math.log10(peak / 32768) if peak > 0 else float("-inf")


def extract_audio(video_path: Path, dest: Path, audio_track: int = 0) -> None:
    extract_wav(video_path, dest, audio_track)


def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    """Where a video's transcript lands.

    The track belongs in the name, or a rerun with --audio-track hands back the transcript of
    the track it is meant to replace. Track 0 keeps the plain name. Batch mode tests its cache
    with this too — one function, so the two cannot drift apart.
    """
    suffix = "" if audio_track == 0 else f".track{audio_track}"
    return edit_dir / "transcripts" / f"{video.stem}{suffix}.json"


# -------- ElevenLabs Scribe ------------------------------------------------------


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language and language != "auto":
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")
    payload = resp.json()
    if isinstance(payload, dict):
        payload["engine"] = "elevenlabs-scribe"
    return payload


# -------- local whisper.cpp ------------------------------------------------------


def default_whisper_model(language: str | None) -> str:
    return "medium.en" if language in (None, "en") else "medium"


def model_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("WHISPER_MODEL_DIR"):
        dirs.append(Path(os.environ["WHISPER_MODEL_DIR"]).expanduser())
    home = Path.home()
    dirs += [
        home / ".cache" / "video-edit" / "models",
        home / ".cache" / "hyperframes" / "whisper" / "models",
        home / ".cache" / "whisper.cpp",
        Path("/opt/homebrew/share/whisper-cpp"),
        Path("/usr/local/share/whisper-cpp"),
    ]
    return dirs


def find_model(model: str) -> Path | None:
    env = os.environ.get("WHISPER_MODEL")
    if env and Path(env).expanduser().exists():
        return Path(env).expanduser()
    for d in model_dirs():
        p = d / f"ggml-{model}.bin"
        if p.exists():
            return p
    return None


def model_download_command(model: str) -> str:
    dest = Path.home() / ".cache" / "video-edit" / "models" / f"ggml-{model}.bin"
    return (f"mkdir -p '{dest.parent}' && curl -L -o '{dest}' "
            f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model}.bin")


def run_whisper_cpp(wav: Path, model: str, language: str | None, prompt: str | None) -> dict:
    binary = next((shutil.which(b) for b in WHISPER_BINS if shutil.which(b)), None)
    if not binary:
        raise RuntimeError("whisper.cpp not found. Install it: brew install whisper-cpp "
                           "(Linux: build from github.com/ggml-org/whisper.cpp) — or set ELEVENLABS_API_KEY.")
    model_path = find_model(model)
    if not model_path:
        raise RuntimeError(f"whisper model ggml-{model}.bin not found in {', '.join(map(str, model_dirs()[:3]))}.\n"
                           f"Download it (ask the user first, ~1.5 GB for medium):\n  {model_download_command(model)}")
    lang = language if language and language != "auto" else ("en" if model.endswith(".en") else "auto")
    with tempfile.TemporaryDirectory() as tmp:
        out_base = Path(tmp) / "out"
        cmd = [binary, "-m", str(model_path), "-f", str(wav), "-l", lang, "-ojf", "-of", str(out_base), "-np"]
        if model in DTW_PRESETS:
            cmd += ["-dtw", DTW_PRESETS[model], "-nfa"]   # DTW needs flash attention off
        if prompt:
            cmd += ["--prompt", prompt]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
        data = json.loads((Path(tmp) / "out.json").read_text())
    data["_model"] = model
    data["_dtw"] = model in DTW_PRESETS
    return data


PUNCT_GLUE = set(".,!?;:'\"%)]")


def whisper_to_words(data: dict, env: list | None, duration: float, shift: float = DTW_SHIFT_S) -> list[dict]:
    """whisper.cpp -ojf output → [{text, start, end, type}] with real word boundaries."""
    use_dtw = bool(data.get("_dtw"))
    toks: list[tuple[str, float, float]] = []
    for seg in data.get("transcription", []):
        for t in seg.get("tokens", []):
            text = t.get("text", "")
            if text.startswith("[_"):          # [_BEG_], [_TT_123] specials
                continue
            if use_dtw:
                if t.get("t_dtw", -1) < 0:
                    continue
                ts = t["t_dtw"] / 100.0
            else:
                ts = (t.get("offsets") or {}).get("from", 0) / 1000.0
            toks.append((text, ts, float(t.get("p", 1.0))))

    words: list[dict] = []
    for text, ts, p in toks:
        if not words or text.startswith(" "):
            if text.strip():
                words.append({"text": text.strip(), "start": ts, "p": [p]})
        else:
            words[-1]["text"] += text
            words[-1]["p"].append(p)

    merged: list[dict] = []
    open_note: dict | None = None
    for w in words:
        if open_note is not None:                       # inside "(upbeat music)"
            open_note["text"] += " " + w["text"]
            if w["text"].endswith((")", "]")):
                merged.append(open_note)
                open_note = None
            continue
        if w["text"][0] in "([" and not w["text"].endswith((")", "]")):
            open_note = w
            continue
        if merged and all(c in PUNCT_GLUE for c in w["text"]):
            merged[-1]["text"] += w["text"]
            continue
        merged.append(w)
    if open_note is not None:
        merged.append(open_note)

    out: list[dict] = []
    for w in merged:
        t = w["text"]
        if re.fullmatch(r"\[.*\]|\(.*\)|\*.*\*", t):
            if "BLANK" in t.upper():
                continue
            w["type"] = "audio_event"
            w["text"] = "(" + t.strip("[]()* ").lower() + ")"
        else:
            w["type"] = "word"
        out.append(w)

    prev = -1.0
    for w in out:
        s = max(0.0, w["start"] - (shift if use_dtw else 0.0))
        s = max(s, prev + 0.01)
        w["start"] = s
        prev = s

    floor = noise_floor(env) if env is not None and len(env) else -90.0
    thr = min(-30.0, max(-55.0, floor + 15.0))
    for i, w in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else min(duration, w["start"] + 1.2)
        end = nxt
        if env is not None and len(env):
            j = min(len(env) - 1, int(nxt / HOP_S) - 1)
            lo = int(w["start"] / HOP_S) + 8
            while j > lo and env[j] < thr:
                j -= 1
            end = min(nxt, (j + 1) * HOP_S + 0.03)
        w["end"] = max(end, w["start"] + 0.05)
        ps = w.pop("p", None)
        w["confidence"] = round(sum(ps) / len(ps), 3) if ps else None
    return out


def to_scribe_shape(words: list[dict], language: str, meta: dict) -> dict:
    res: list[dict] = []
    prev_end = None
    for w in words:
        if prev_end is not None and w["start"] > prev_end:
            res.append({"text": " ", "start": round(prev_end, 3), "end": round(w["start"], 3),
                        "type": "spacing", "speaker_id": "speaker_0"})
        res.append({"text": w["text"], "start": round(w["start"], 3), "end": round(w["end"], 3),
                    "type": w["type"], "speaker_id": "speaker_0", "confidence": w.get("confidence")})
        prev_end = w["end"]
    return {
        "language_code": language,
        "text": " ".join(w["text"] for w in words if w["type"] == "word"),
        "words": res,
        **meta,
    }


def call_whisper(
    wav: Path,
    model: str | None = None,
    language: str | None = None,
    vocab: str | None = None,
    verbatim: bool = True,
) -> dict:
    model = model or default_whisper_model(language)
    if model.endswith(".en") and language not in (None, "en", "auto"):
        raise RuntimeError(f"model {model} is English-only and would translate {language} audio; "
                           f"use --model medium (or large-v3-turbo)")
    english = model.endswith(".en") or language == "en"
    prompt_parts = []
    if vocab:
        prompt_parts.append(vocab.strip().rstrip(".") + ".")
    if verbatim and english:
        prompt_parts.append(VERBATIM_PROMPT)
    data = run_whisper_cpp(wav, model, language, " ".join(prompt_parts) or None)
    y, sr = read_wav_mono(wav)
    env = envelope_db(y, sr)
    words = whisper_to_words(data, env, duration=len(y) / sr)
    lang = (data.get("result") or {}).get("language") or language or ("en" if english else "")
    return to_scribe_shape(words, lang, {
        "engine": "whisper.cpp", "model": model, "dtw": bool(data.get("_dtw")),
        "dtw_shift_s": DTW_SHIFT_S if data.get("_dtw") else 0.0, "vocab": vocab,
    })


# -------- one file ---------------------------------------------------------------


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    audio_track: int = 0,
    engine: str | None = None,
    model: str | None = None,
    vocab: str | None = None,
    verbatim: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns the existing path immediately if the transcript already exists.
    """
    engine = engine or ("elevenlabs" if api_key else "whisper")
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)
    n_tracks = count_audio_tracks(video)
    if n_tracks == 0:
        raise RuntimeError(f"{video.name} has no audio track — nothing to transcribe")
    if n_tracks > 1 and verbose:
        print(f"  note: {video.name} has {n_tracks} audio tracks, using track "
              f"{audio_track + 1} (--audio-track to change)", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio, audio_track)

        # Transcribing silence costs time (or money) and returns nothing, so catch
        # the wrong-track case first.
        peak = peak_dbfs(audio)
        if peak < -60.0:
            raise RuntimeError(
                f"track {audio_track + 1} of {video.name} is silent "
                f"(peak {peak:.1f} dBFS) - not transcribing. "
                + (f"The file has {n_tracks} audio tracks; try --audio-track "
                   + " or ".join(str(i) for i in range(n_tracks) if i != audio_track) + "."
                   if n_tracks > 1 else "Check the source audio.")
            )

        if engine == "elevenlabs":
            if not api_key:
                raise RuntimeError("elevenlabs engine needs an API key")
            if verbose:
                print(f"  uploading {video.stem}.wav ({audio.stat().st_size / 1048576:.1f} MB) to Scribe", flush=True)
            payload = call_scribe(audio, api_key, language, num_speakers)
        else:
            if verbose:
                print(f"  whisper.cpp ({model or default_whisper_model(language)}) on {video.stem}.wav", flush=True)
            payload = call_whisper(audio, model=model, language=language, vocab=vocab, verbatim=verbatim)

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0
    if verbose:
        n = len([w for w in payload.get("words", []) if w.get("type") == "word"])
        print(f"  saved: {out_path.name} ({out_path.stat().st_size / 1024:.1f} KB, {n} words) in {dt:.1f}s")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video to word-level JSON")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument("--edit-dir", type=Path, default=None, help="Edit output directory (default: <video_parent>/edit)")
    ap.add_argument("--engine", choices=["auto", "elevenlabs", "whisper"], default="auto")
    ap.add_argument("--language", type=str, default=None,
                    help="ISO code (e.g. 'en', 'es'), or 'auto'. Omit for English with whisper / auto-detect with Scribe.")
    ap.add_argument("--model", type=str, default=None, help="whisper model (default medium.en; medium for other languages)")
    ap.add_argument("--vocab", type=str, default=None,
                    help="whisper: comma-separated brand/product names to spell correctly")
    ap.add_argument("--no-verbatim-prompt", action="store_true", help="whisper: do not bias toward keeping fillers")
    ap.add_argument("--num-speakers", type=int, default=None, help="elevenlabs: number of speakers, when known")
    ap.add_argument(
        "--audio-track", type=int, default=0,
        help="Zero-based audio track to transcribe. OBS writes the game on track 0 and the mic on track 1.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")
    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    engine, api_key = resolve_engine(args.engine)
    try:
        transcribe_one(
            video=video, edit_dir=edit_dir, api_key=api_key, language=args.language,
            num_speakers=args.num_speakers, audio_track=args.audio_track, engine=engine,
            model=args.model, vocab=args.vocab, verbatim=not args.no_verbatim_prompt,
        )
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
