"""Hear every cut by reading it: transcribe the audio across each join of an EDL.

Word-level timestamps (especially local whisper) are loose by 0.1-0.4 s, so a cut that
removes a filler ("So", "basically", "Great.") can clip the next word or leave half of the
filler behind. For every join this concatenates the last ~1.5 s before the cut with the
first ~1.5 s after it (20 ms fades, like render.py) and transcribes just that snippet —
short clips transcribe reliably. Compare each line with the words you meant to keep.

Usage:
    python helpers/verify_cuts.py <edit>/edl.json                  # every join
    python helpers/verify_cuts.py --source clip.MOV --probe 22.3:23.04,23.44:24.6
        # try candidate cut points before writing them into the EDL (repeat --probe)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe import find_model, model_download_command  # noqa: E402

CTX = 1.5


def join_text(source: str, pieces: list[tuple[float, float]], model: Path, audio_track: int = 0) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "j.wav"
        parts, labels = [], []
        for i, (a, b) in enumerate(pieces):
            d = max(0.05, b - a)
            parts.append(f"[0:a:{audio_track}]atrim={a}:{b},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.02,afade=t=out:st={max(0, d - 0.02):.3f}:d=0.02[a{i}]")
            labels.append(f"[a{i}]")
        fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(pieces)}:v=0:a=1,apad=pad_dur=0.4[o]"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-filter_complex", fc, "-map", "[o]",
                        "-ac", "1", "-ar", "16000", str(wav)], check=True)
        r = subprocess.run(["whisper-cli", "-m", str(model), "-f", str(wav), "-np", "-nt"],
                           capture_output=True, text=True)
    return " ".join(r.stdout.split())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl", type=Path, nargs="?")
    ap.add_argument("--source", help="source video for --probe")
    ap.add_argument("--probe", action="append", default=[],
                    help="a1:b1,a2:b2[,...] — pieces to join and transcribe (source seconds)")
    ap.add_argument("--context", type=float, default=CTX)
    ap.add_argument("--model", default="medium.en")
    ap.add_argument("--audio-track", type=int, default=0)
    args = ap.parse_args()

    model = find_model(args.model)
    if not model:
        sys.exit(f"whisper model {args.model} not found. {model_download_command(args.model)}")

    if args.probe:
        if not args.source:
            ap.error("--probe needs --source")
        for p in args.probe:
            pieces = [tuple(float(x) for x in seg.split(":")) for seg in p.split(",")]
            print(f"{p:>28}  ->  {join_text(args.source, pieces, model, args.audio_track)}")
        return

    if not args.edl:
        ap.error("edl or --probe required")
    edl = json.loads(args.edl.read_text())
    rs = edl["ranges"]
    track = int(edl.get("audio_track", args.audio_track))
    for i in range(len(rs) - 1):
        a, b = rs[i], rs[i + 1]
        if a["source"] != b["source"]:
            continue
        src = edl["sources"][a["source"]]
        pieces = [(max(float(a["start"]), float(a["end"]) - args.context), float(a["end"])),
                  (float(b["start"]), min(float(b["end"]), float(b["start"]) + args.context))]
        print(f"[{i:2d}|{i + 1:2d}] {float(a['end']):8.2f} -> {float(b['start']):8.2f}  "
              f"{join_text(src, pieces, model, track)}")


if __name__ == "__main__":
    main()
