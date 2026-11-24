#!/usr/bin/env python3
"""Build a ready-to-mix SFX kit from your own sound-effect pack.

A pack downloaded off the internet is hundreds of files with names like
"SWOOSH #2.mp3", "Sound Effect 7.wav" and a 5-minute music track mixed in. The
renderer needs something else: a few short, level-matched clips per ROLE (pop,
whoosh, ding, cash, ...) and, for each one, where its loudest moment sits. A
whoosh is useless unless its peak lands on the zoom, not its first sample.

This script picks files by filename keyword per role, skips anything too long,
trims leading silence, cuts to a role-specific length with a fade, normalises
loudness (EBU R128, -16 LUFS), and records the peak time. It writes
`<out>/kit.json` plus one WAV per pick. render.py uses the kit automatically
when it exists, and falls back to its built-in synthesized pop/whoosh/ding
when it doesn't.

Usage:
  python3 sfx_kit.py SOURCE --out ~/.cache/talking-head-edit/sfx [--per-role 3]

SOURCE is a folder or a .zip. Nothing is extracted except the chosen files.
Prints a table of picks and `KIT: <abs-path to kit.json>` last.
Exit 1 if SOURCE is missing, 3 if no role found a single file.
Requires: ffmpeg on PATH. Python stdlib only.
"""
from __future__ import annotations

import argparse
import array
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

AUDIO = (".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".ogg")

# role -> (filename patterns in preference order, max source seconds, kept seconds)
ROLES = {
    "pop":      ([r"\bpop\b", r"blop", r"bubble"], 1.5, 0.4),
    "whoosh":   ([r"^swoosh( #\d)?\.", r"whoosh sound effect hd", r"quick swing",
                  r"\bwhip\b", r"s?w(h)?oo?sh"], 1.6, 1.0),
    "ding":     ([r"\bding\b", r"\bting\b", r"\bbell\b", r"chime"], 3.0, 1.5),
    "cash":     ([r"cashier|ka-?ching|ching", r"money"], 3.0, 1.5),
    "click":    ([r"^mouse click\.", r"mouse click", r"\bclick\b"], 1.0, 0.4),
    "camera":   ([r"camera shutter", r"shutter", r"camera flash"], 2.0, 1.0),
    "punch":    ([r"\bpunch\b", r"\bkick\b"], 1.2, 0.8),
    "fail":     ([r"wrong.?buzzer", r"\bfail\b", r"error"], 5.5, 2.0),
    "victory":  ([r"victory", r"\b(success|win|winner)\b"], 6.0, 3.0),
    "drama":    ([r"dun dun", r"drama"], 6.0, 3.5),
    "riser":    ([r"sharp riser", r"riser", r"buildup"], 5.5, 2.5),
    "glitch":   ([r"glitch"], 1.5, 1.0),
    "typing":   ([r"typing|typewriter|keyboard"], 20.0, 2.0),
}


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def peak_time(wav: Path) -> float:
    """Second at which the 20 ms window with the most energy starts."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le",
                          "-ac", "1", "-ar", "8000", "-"], capture_output=True).stdout
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    win, best, at = 160, -1, 0
    for i in range(0, max(len(a) - win, 1), win // 2):
        e = sum(x * x for x in a[i:i + win])
        if e > best:
            best, at = e, i
    return round(at / 8000, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-role", type=int, default=3)
    args = ap.parse_args()

    src = Path(args.source).expanduser()
    if not src.exists():
        print(f"no such source: {src}", file=sys.stderr)
        return 1
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="sfxkit-"))

    zf = None
    if src.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(src)
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
    else:
        names = [str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()]
    names = sorted(n for n in names if n.lower().endswith(AUDIO)
                   and "__MACOSX" not in n and not Path(n).name.startswith("._"))

    def fetch(name: str) -> Path:
        if zf is None:
            return src / name
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"unsafe path in zip: {name}")
        dst = tmp / f"in{abs(hash(name))}{Path(name).suffix}"
        with zf.open(name) as r, open(dst, "wb") as w:
            shutil.copyfileobj(r, w)
        return dst

    kit, used = {}, set()
    for role, (pats, max_src, keep) in ROLES.items():
        picks = []
        for pat in pats:
            for n in names:
                if len(picks) >= args.per_role:
                    break
                base = Path(n).name.lower()
                if n in used or not re.search(pat, base):
                    continue
                f = fetch(n)
                d = duration(f)
                if not 0.05 < d <= max_src:
                    continue
                dst = out / f"{role}_{len(picks) + 1}.wav"
                r = subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-i", str(f), "-af",
                     "silenceremove=start_periods=1:start_threshold=-45dB,"
                     f"atrim=0:{keep},afade=t=out:st={max(keep - 0.15, 0)}:d=0.15,"
                     "loudnorm=I=-16:TP=-1.5", "-ar", "48000", "-ac", "2", str(dst)],
                    capture_output=True)
                if r.returncode != 0 or not dst.exists():
                    continue
                used.add(n)
                picks.append({"file": dst.name, "from": n, "peak": peak_time(dst),
                              "dur": round(duration(dst), 3)})
        if picks:
            kit[role] = picks
    shutil.rmtree(tmp, ignore_errors=True)
    if not kit:
        print("no role matched any file", file=sys.stderr)
        return 3

    (out / "kit.json").write_text(json.dumps(kit, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    for role, picks in kit.items():
        for p in picks:
            print(f"{role:8s} peak {p['peak']:.2f}s  {p['from']}", file=sys.stderr)
    missing = [r for r in ROLES if r not in kit]
    if missing:
        print(f"no match for: {', '.join(missing)} (synth fallback covers pop/whoosh/ding)",
              file=sys.stderr)
    print(f"KIT: {out / 'kit.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
