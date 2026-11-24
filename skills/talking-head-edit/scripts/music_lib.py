#!/usr/bin/env python3
"""Keep a private library of the user's own background music, by theme.

The library lives OUTSIDE the repo (default ~/.cache/talking-head-edit/music):
the tracks are the user's own, and this repo is public. Never copy them into
the skill folder, a commit, or any upload.

Each track gets a theme ("main", "travel", ...), a one-line `use`
that tells the agent when to pick it, and a `start` offset. Most tracks open
with a 10-20 s intro. A 30 s short that starts at 0:00 never reaches the part
that carries energy, so `start` is the first moment the track reaches its
working level: smoothed RMS hits 80% of its 90th percentile.

Usage:
  python3 music_lib.py add FILE --theme travel --use "trips, riding, nature"
  python3 music_lib.py list
  [--lib DIR]  (default ~/.cache/talking-head-edit/music)

`add` copies the file in as <theme>.<ext> (replacing that theme) and prints the
entry. The library file is <lib>/library.json.
Requires: ffmpeg on PATH. Python stdlib only.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_LIB = Path.home() / ".cache" / "talking-head-edit" / "music"


def energy_start(path: Path, win_s: float = 2.0) -> float:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le",
                          "-ac", "1", "-ar", "8000", "-"], capture_output=True).stdout
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    win = int(8000 * win_s)
    rms = [math.sqrt(sum(x * x for x in a[i:i + win]) / win)
           for i in range(0, max(len(a) - win, 1), win)]
    if not rms:
        return 0.0
    smooth = [sum(rms[max(0, i - 1):i + 2]) / len(rms[max(0, i - 1):i + 2])
              for i in range(len(rms))]
    p90 = sorted(smooth)[int(len(smooth) * 0.9)]
    for i, v in enumerate(smooth):
        if v >= 0.8 * p90:
            return round(i * win_s, 1)
    return 0.0


def load(lib: Path) -> list[dict]:
    f = lib / "library.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["add", "list"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--theme")
    ap.add_argument("--use", default="")
    ap.add_argument("--lib", default=str(DEFAULT_LIB))
    args = ap.parse_args()
    lib = Path(args.lib).expanduser()

    if args.cmd == "list":
        for t in load(lib):
            print(f"{t['theme']:10s} start {t['start']:5.1f}s  {t['file']}  -- {t['use']}")
        return 0

    if not args.file or not args.theme or not Path(args.file).is_file():
        print("add needs an existing FILE and --theme", file=sys.stderr)
        return 1
    lib.mkdir(parents=True, exist_ok=True)
    src = Path(args.file)
    dst = lib / f"{args.theme}{src.suffix.lower()}"
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    dst.chmod(0o600)
    entry = {"theme": args.theme, "file": dst.name, "use": args.use,
             "start": energy_start(dst)}
    tracks = [t for t in load(lib) if t["theme"] != args.theme] + [entry]
    (lib / "library.json").write_text(json.dumps(tracks, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
