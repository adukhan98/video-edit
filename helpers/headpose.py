"""Head pose per frame (macOS Vision) — find look-down / look-up moments at cut edges.

Talking-head creators often read notes between lines: the head drops before a pause
and comes back up as the next line starts. Those half-second head moves at a cut edge
read as sloppy even when the audio cut is clean. This measures head pitch per frame
(Apple Vision face rectangles, revision 3) so you can trim the edges or cover them.

pitch is in radians: ~0 = looking at the lens, > +0.15 = looking down, > +0.3 = reading notes.

Usage:
    python helpers/headpose.py <video> --start 0 --end 140            # per-0.1 s strip + down spans
    python helpers/headpose.py <video> --edl <edit>/edl.json           # flag range edges that move
Requires macOS with the Swift toolchain (xcode-select --install). Cached per video in
<edit>/headpose/<stem>.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SWIFT = r'''
import Foundation
import Vision
import AppKit
let dir = CommandLine.arguments[1]
let files = try FileManager.default.contentsOfDirectory(atPath: dir).filter { $0.hasSuffix(".jpg") }.sorted()
for f in files {
    let url = URL(fileURLWithPath: dir).appendingPathComponent(f)
    guard let img = NSImage(contentsOf: url),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNDetectFaceRectanglesRequest()
    req.revision = VNDetectFaceRectanglesRequestRevision3
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    let idx = f.replacingOccurrences(of: ".jpg", with: "")
    if let o = req.results?.first {
        print("\(idx) \(o.pitch?.doubleValue ?? 99) \(o.yaw?.doubleValue ?? 99) \(o.boundingBox.midY)")
    } else {
        print("\(idx) 99 99 0")
    }
}
'''

DOWN = 0.15


def binary() -> Path:
    cache = Path.home() / ".cache" / "video-edit"
    cache.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(SWIFT.encode()).hexdigest()[:10]
    exe = cache / f"headpose-{h}"
    if not exe.exists():
        src = cache / f"headpose-{h}.swift"
        src.write_text(SWIFT)
        r = subprocess.run(["swiftc", "-O", str(src), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"swiftc failed (macOS + Xcode command line tools needed):\n{r.stderr[-800:]}")
    return exe


def measure(video: Path, start: float, end: float, fps: float = 10) -> dict[float, float]:
    """{time: pitch} every 1/fps s; pitch 99 = no face found."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-to", f"{end}", "-i", str(video),
             "-vf", f"fps={fps},scale=360:-2", "-q:v", "4", f"{tmp}/%06d.jpg"], check=True)
        out = subprocess.run([str(binary()), tmp], capture_output=True, text=True).stdout
    res = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 2:
            res[round(start + (int(p[0]) - 1) / fps, 2)] = float(p[1])
    return res


def load_or_measure(video: Path, edit_dir: Path | None, start: float, end: float, fps: float) -> dict[float, float]:
    cache = None
    if edit_dir:
        cache = edit_dir / "headpose" / f"{video.stem}.json"
        if cache.exists():
            d = json.loads(cache.read_text())
            if d["start"] <= start and d["end"] >= end and d["fps"] == fps:
                return {float(k): v for k, v in d["pitch"].items()}
    lo, hi = (0.0, probe_duration(video)) if cache else (start, end)
    pitch = measure(video, lo, hi, fps)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"start": lo, "end": hi, "fps": fps, "pitch": pitch}))
    return pitch


def probe_duration(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def at(pitch: dict[float, float], t: float, fps: float) -> float:
    return pitch.get(round(round(t * fps) / fps, 2), 99)


def char(v: float) -> str:
    return "X" if v >= 9 else "D" if v > 0.3 else "d" if v > DOWN else "."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path, nargs="?")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--fps", type=float, default=10)
    ap.add_argument("--edl", type=Path, help="check the head at every range edge of this EDL")
    ap.add_argument("--edge", type=float, default=0.3, help="seconds checked at each range edge")
    args = ap.parse_args()

    if args.edl:
        edl = json.loads(args.edl.read_text())
        edit_dir = args.edl.parent
        flagged = 0
        cache: dict[str, dict] = {}
        for i, r in enumerate(edl["ranges"]):
            src = Path(edl["sources"][r["source"]])
            if r["source"] not in cache:
                cache[r["source"]] = load_or_measure(src, edit_dir, 0, probe_duration(src), args.fps)
            p = cache[r["source"]]
            a, b = float(r["start"]), float(r["end"])
            n = int(args.edge * args.fps)
            head = [at(p, a + k / args.fps, args.fps) for k in range(n + 1)]
            tail = [at(p, b - k / args.fps, args.fps) for k in range(n + 1)][::-1]
            bad_in = max(v for v in head if v < 9) > DOWN if any(v < 9 for v in head) else False
            bad_out = max(v for v in tail if v < 9) > DOWN if any(v < 9 for v in tail) else False
            mark = ("  <- head moving at START" if bad_in else "") + ("  <- head moving at END" if bad_out else "")
            flagged += bool(mark)
            print(f"[{i:2d}] {r['source']} {a:8.2f}-{b:8.2f}  in {''.join(map(char, head))}  "
                  f"out {''.join(map(char, tail))}{mark}")
        print(f"# {flagged} range(s) flagged. Trim the edge (if no words are lost) or cover it with a "
              f"full-screen graphic / b-roll. Legend: . camera  d looking down  D reading notes  X no face")
        return

    if not args.video:
        ap.error("video or --edl required")
    end = args.end if args.end is not None else probe_duration(args.video)
    p = measure(args.video, args.start, end, args.fps)
    ts = sorted(p)
    for k in range(0, len(ts), 100):
        chunk = ts[k:k + 100]
        print(f"{chunk[0]:8.1f}  " + "".join(char(p[t]) for t in chunk))
    spans, cur = [], None
    for t in ts:
        down = DOWN < p[t] < 9
        if down and cur is None:
            cur = t
        elif not down and cur is not None:
            spans.append((cur, t))
            cur = None
    if cur is not None:
        spans.append((cur, ts[-1]))
    print("# looking down:", ", ".join(f"{a:.1f}-{b:.1f}" for a, b in spans) or "none")


if __name__ == "__main__":
    main()
