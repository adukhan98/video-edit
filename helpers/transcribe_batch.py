"""Batch-transcribe every video in a directory.

Walks <videos_dir> for common video extensions and transcribes each with the
same engine choice as transcribe.py (ElevenLabs Scribe when a key exists,
otherwise local whisper.cpp). Scribe runs 4 uploads in parallel; whisper runs
one file at a time by default (it already uses the whole GPU).

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --engine whisper --vocab "Acme, Rocketship"
    python helpers/transcribe_batch.py <videos_dir> --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --edit-dir /custom/edit
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe import resolve_engine, transcribe_one, transcript_path  # noqa: E402


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts"}


def find_videos(videos_dir: Path) -> list[Path]:
    return sorted(
        p for p in videos_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument("--edit-dir", type=Path, default=None, help="Edit output directory (default: <videos_dir>/edit)")
    ap.add_argument("--engine", choices=["auto", "elevenlabs", "whisper"], default="auto")
    ap.add_argument("--workers", type=int, default=None, help="Parallel workers (default: 4 Scribe, 1 whisper)")
    ap.add_argument("--language", type=str, default=None, help="ISO language code, or 'auto'")
    ap.add_argument("--model", type=str, default=None, help="whisper model (default medium.en)")
    ap.add_argument("--vocab", type=str, default=None, help="whisper: brand/product names to spell correctly")
    ap.add_argument("--num-speakers", type=int, default=None, help="elevenlabs: number of speakers")
    ap.add_argument("--audio-track", type=int, default=0, help="Zero-based audio track (OBS: 0 = game, 1 = mic)")
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")
    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")
    pending = [v for v in videos if not transcript_path(edit_dir, v, args.audio_track).exists()]
    print(f"found {len(videos)} videos ({len(videos) - len(pending)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    engine, api_key = resolve_engine(args.engine)
    workers = args.workers or (4 if engine == "elevenlabs" else 1)
    print(f"transcribing {len(pending)} files with {engine} ({workers} worker{'s' if workers > 1 else ''})")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v, edit_dir=edit_dir, api_key=api_key, language=args.language,
                num_speakers=args.num_speakers, verbose=False, audio_track=args.audio_track,
                engine=engine, model=args.model, vocab=args.vocab,
            ): v
            for v in pending
        }
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                out = fut.result()
                print(f"  + {v.stem}  →  {out.name}", flush=True)
            except Exception as e:
                errors.append((v, str(e)))
                print(f"  x {v.stem}  FAILED: {e}", flush=True)

    print(f"\ndone in {time.time() - t0:.1f}s")
    if errors:
        print(f"{len(errors)} failures:")
        for v, msg in errors:
            print(f"  {v.name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
