#!/usr/bin/env python3
"""Turn a talking-head clip plus its word timings into an edit plan.

The plan is one JSON file holding every editorial decision, so it can be read,
argued with and hand-edited before a single frame renders:

  segments  which source ranges survive (dead air between them is cut)
  lines     subtitle lines, each a list of timed words on the OUTPUT clock
  moves     camera moves: zoom level and focus point, with a ramp time
  sfx       sound-effect cues on the output clock; `t` is where the sound
            HITS (render.py aligns each sound's peak to it)

Defaults, all overridable by editing the JSON:

  * silences of --min-silence or longer are cut, keeping --pad either side
  * every cut, and every ~3 s of talk, alternates the framing between 1.0x and
    1.12x (a "punch-in") so jump cuts read as intentional
  * saying a zoom word ("ซูมอิน", "zoom in") pushes in; a zoom-out word
    ("ซูมเอาท์", "zoom out") pulls back -- the speaker directs the camera
  * emphasis words ("ป๊อป", "เด้ง", "ว้าว", plus --emphasis) get a quick
    bounce, a ding, and the display font in the subtitles
  * every subtitle line lands with a soft pop

Usage:
  python3 plan.py VIDEO words.json --out plan.json
                  [--aspect source|9:16|4:5|1:1] [--focus 0.5,0.4]
                  [--min-silence 0.45] [--silence-db -32] [--pad 0.12]
                  [--emphasis คำ,word] [--no-cuts] [--no-punch] [--no-sfx]
                  [--music THEME|FILE|none]  (default: theme "main" if the
                  private library ~/.cache/talking-head-edit/music has it)

Prints `PLAN: <abs-path>` on the last line. Exit 1 on bad input.
Requires: ffmpeg on PATH. Python stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ZOOM_IN = ["ซูมอิน", "ซูมเข้า", "zoomin", "ดูใกล้ๆ", "ดูชัดๆ"]
ZOOM_OUT = ["ซูมเอาท์", "ซูมเอ้าท์", "ซูมออก", "zoomout"]
EMPHASIS = ["ป๊อป", "เด้ง", "ว้าว", "สุดยอด", "เจ๋ง", "ปัง", "ที่สุด", "ฟรี",
            "wow", "boom", "amazing"]

PUNCH = 1.12          # alternate framing on cuts
ZOOM_IN_Z = 1.5       # keyword push-in
BOUNCE = 0.12         # emphasis bounce, added to current zoom
PUNCH_EVERY = 3.0     # seconds of continuous talk between punches
AUTO_OUT = 2.0        # a push-in with no zoom-out word returns after this
MAX_LINE_WORDS = 7
LINE_TARGET = 11      # visible characters (Thai marks don't count)
MAX_LINE_CHARS = 16


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def visible_len(s: str) -> int:
    return sum(unicodedata.category(c) not in ("Mn", "Mc") for c in s)


def silences(video: str, db: float, min_dur: float) -> list[tuple[float, float]]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vn", "-af",
         f"silencedetect=n={db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True)
    out, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, 1e9))
    return out


def duration(video: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def keep_segments(dur: float, sil, pad: float) -> list[list[float]]:
    segs, cur = [], 0.0
    for s, e in sil:
        cut_s, cut_e = (s + pad if s > 0 else 0.0), min(e - pad, dur)
        if cut_e - cut_s < 0.15:
            continue
        if cut_s > cur:
            segs.append([round(cur, 3), round(cut_s, 3)])
        cur = cut_e
    if dur - cur > 0.15:
        segs.append([round(cur, 3), round(dur, 3)])
    return segs


class Clock:
    """Maps source time to output time across the kept segments."""

    def __init__(self, segs):
        self.segs, self.offs, acc = segs, [], 0.0
        for s, e in segs:
            self.offs.append(acc)
            acc += e - s
        self.total = acc

    def span(self, s: float, e: float):
        """Output span of a source span, inside the segment it overlaps most."""
        best, bi = 0.0, -1
        for i, (a, b) in enumerate(self.segs):
            ov = min(e, b) - max(s, a)
            if ov > best:
                best, bi = ov, i
        if bi < 0:
            return None
        a, b = self.segs[bi]
        o = self.offs[bi]
        return (bi, round(max(s, a) - a + o, 3), round(min(e, b) - a + o, 3))


def break_lines(ws: list[dict]) -> list[list[dict]]:
    """Split a run of words into subtitle lines by minimum total cost.

    Greedy filling splits Thai loanwords that the segmenter cut into pieces
    ("ซับ|ไต|เติล"). The cost here prefers lines near LINE_TARGET characters,
    rewards breaking at a pause (a gap, or a word stretched over a pause), and
    penalises breaking next to a 1-2 character fragment.
    """
    n = len(ws)
    lens = [visible_len(w["w"]) for w in ws]

    def cost(i: int, j: int) -> float:
        L = sum(lens[i:j])
        c = (L - LINE_TARGET) ** 2 + (300 if L > MAX_LINE_CHARS else 0)
        if j < n:
            left, right = ws[j - 1], ws[j]
            c -= 40 * min(max(right["s"] - left["e"], 0), 0.4) / 0.4
            if left["e"] - left["s"] > 0.6:
                c -= 20
            if lens[j - 1] <= 2 or lens[j] <= 2:
                c += 25
        return c

    best = [0.0] + [float("inf")] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - MAX_LINE_WORDS), j):
            c = best[i] + cost(i, j)
            if c < best[j]:
                best[j], back[j] = c, i
    out, j = [], n
    while j > 0:
        out.append(ws[back[j]:j])
        j = back[j]
    return out[::-1]


MUSIC_LIB = Path.home() / ".cache" / "talking-head-edit" / "music"


def pick_music(choice: str) -> dict | None:
    """Background bed: a library theme or a file. None when there is nothing."""
    if choice == "none":
        return None
    f, start = Path(choice).expanduser(), 0.0
    if not f.is_file():
        lib = MUSIC_LIB / "library.json"
        tracks = json.loads(lib.read_text(encoding="utf-8")) if lib.exists() else []
        hit = next((t for t in tracks if t["theme"] == choice), None)
        if not hit:
            if choice != "main":
                print(f"music theme '{choice}' not in library; no music", file=sys.stderr)
            return None
        f, start = MUSIC_LIB / hit["file"], hit["start"]
    return {"file": str(f.resolve()), "start": start, "gain_db": -6,
            "duck": True, "fade_in": 0.4, "fade_out": 1.5}


def find_hits(words, keywords) -> dict[int, str]:
    """Word index -> keyword, matching across word boundaries."""
    text, owner = "", []
    for i, w in enumerate(words):
        t = norm(w["w"])
        text += t
        owner += [i] * len(t)
    hits = {}
    for kw in keywords:
        for m in re.finditer(re.escape(norm(kw)), text):
            hits.setdefault(owner[m.start()], kw)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("words")
    ap.add_argument("--out", required=True)
    ap.add_argument("--aspect", default="source",
                    choices=["source", "9:16", "4:5", "1:1"])
    ap.add_argument("--focus", default="0.5,0.4",
                    help="face position as x,y fractions of the frame")
    ap.add_argument("--min-silence", type=float, default=0.45)
    ap.add_argument("--silence-db", type=float, default=-32)
    ap.add_argument("--pad", type=float, default=0.12)
    ap.add_argument("--emphasis", default="", help="extra words, comma-separated")
    ap.add_argument("--no-cuts", action="store_true")
    ap.add_argument("--no-punch", action="store_true")
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--music", default="main",
                    help="library theme (main/commerce/travel/...), an audio file, or none")
    args = ap.parse_args()

    video = str(Path(args.video).resolve())
    if not Path(video).is_file():
        print(f"no such file: {video}", file=sys.stderr)
        return 1
    words = json.loads(Path(args.words).read_text(encoding="utf-8"))["words"]
    fx, fy = (float(v) for v in args.focus.split(","))
    dur = duration(video)

    if args.no_cuts:
        segs = [[0.0, round(dur, 3)]]
    else:
        segs = keep_segments(dur, silences(video, args.silence_db,
                                           args.min_silence), args.pad)
    clock = Clock(segs)

    # words on the output clock
    ow = []
    for w in words:
        sp = clock.span(w["s"], w["e"])
        if sp and sp[2] > sp[1]:
            ow.append({"w": w["w"], "seg": sp[0], "s": sp[1], "e": sp[2]})

    emph_list = EMPHASIS + [x for x in args.emphasis.split(",") if x.strip()]
    zin = find_hits(ow, ZOOM_IN + ["zoom in"])
    zout = find_hits(ow, ZOOM_OUT + ["zoom out"])
    emph = find_hits(ow, emph_list)
    for i in emph:
        ow[i]["emph"] = True

    # subtitle lines: hard break at every cut, best-cost breaks inside a run
    lines, run_ = [], []
    for w in ow:
        if run_ and w["seg"] != run_[-1]["seg"]:
            lines += break_lines(run_)
            run_ = []
        run_.append(w)
    if run_:
        lines += break_lines(run_)
    line_out = []
    for k, ln in enumerate(lines):
        end = lines[k + 1][0]["s"] if k + 1 < len(lines) else min(
            ln[-1]["e"] + 0.5, clock.total)
        line_out.append({"start": ln[0]["s"], "end": round(end, 3),
                         "words": [{k2: v for k2, v in w.items() if k2 != "seg"}
                                   for w in ln]})

    # camera moves, walked in time order with a small state machine
    events = []
    last_punch, last_seg = 0.0, 0
    for ln in lines:
        t, seg = ln[0]["s"], ln[0]["seg"]
        if not args.no_punch and (seg != last_seg or t - last_punch >= PUNCH_EVERY):
            events.append((t, "punch", None))
            last_punch = t
        last_seg = seg
    for i in zin:
        events.append((ow[i]["s"], "zin", i))
    for i in zout:
        events.append((ow[i]["s"], "zout", i))
    for i in emph:
        if i not in zin and i not in zout:
            events.append((ow[i]["s"], "emph", i))
    events.sort(key=lambda e: e[0])

    moves = [{"t": 0.0, "z": 1.0, "fx": fx, "fy": fy, "ramp": 0, "why": "start"}]
    sfx = []
    base, zoomed, auto_out = 1.0, False, None

    def move(t, z, ramp, why):
        moves.append({"t": round(max(t, 0), 3), "z": round(z, 3), "fx": fx,
                      "fy": fy, "ramp": ramp, "why": why})

    for t, kind, i in events:
        if zoomed and auto_out is not None and t >= auto_out:
            move(auto_out, base, 0.5, "auto zoom-out")
            sfx.append({"t": round(auto_out + 0.25, 3), "kind": "whoosh", "gain": 0.35})
            zoomed = False
        if kind == "punch" and not zoomed:
            base = PUNCH if base == 1.0 else 1.0
            move(t, base, 0, "punch-in" if t > 0 else "start")
        elif kind == "zin" and not zoomed:
            move(t, ZOOM_IN_Z, 0.35, f"said '{ow[i]['w']}'")
            sfx.append({"t": round(t + 0.17, 3), "kind": "whoosh", "gain": 0.6})
            zoomed, auto_out = True, t + AUTO_OUT
        elif kind == "zout" and zoomed:
            move(t, base, 0.5, f"said '{ow[i]['w']}'")
            sfx.append({"t": round(t + 0.25, 3), "kind": "whoosh", "gain": 0.4})
            zoomed = False
        elif kind == "emph":
            z = moves[-1]["z"] if zoomed else base
            move(t, z + BOUNCE, 0.08, f"emphasis '{ow[i]['w']}'")
            move(t + 0.1, z, 0.18, "bounce back")
            sfx.append({"t": round(t, 3), "kind": "ding", "gain": 0.3})
    if zoomed:
        move(min(auto_out, clock.total - 0.5), base, 0.5, "auto zoom-out")

    for ln in line_out:
        if all(abs(c["t"] - ln["start"]) > 0.15 for c in sfx):
            sfx.append({"t": ln["start"], "kind": "pop", "gain": 0.35})
    if args.no_sfx:
        sfx = []
    sfx.sort(key=lambda c: c["t"])
    moves.sort(key=lambda m: m["t"])

    plan = {
        "source": video, "aspect": args.aspect, "focus": [fx, fy],
        "segments": segs, "duration": round(clock.total, 3),
        "style": {"font": "Mitr", "font_emph": "Pattaya",
                  "active": "#FFE500", "base": "#FFFFFF",
                  "y": 0.72 if args.aspect == "9:16" else 0.86, "size": 0.1},
        "lines": line_out, "moves": moves, "sfx": sfx,
        "music": pick_music(args.music),
    }
    out = Path(args.out).resolve()
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    cut = sum(e - s for s, e in segs)
    print(f"{len(segs)} segments, {dur:.2f}s -> {cut:.2f}s; {len(line_out)} lines, "
          f"{len(moves)} moves, {len(sfx)} sfx", file=sys.stderr)
    for m in moves:
        print(f"  move {m['t']:6.2f}s z={m['z']:.2f} ramp={m['ramp']}  {m['why']}",
              file=sys.stderr)
    print(f"PLAN: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
