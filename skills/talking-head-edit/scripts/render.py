#!/usr/bin/env python3
"""Render an edit plan: jump cuts, camera moves, karaoke subtitles, sound effects.

Everything comes from plan.json (see plan.py); this script makes no editorial
choices of its own. Pipeline:

  1. cut     keep plan.segments, re-timed to a constant 30 fps, 30 ms audio
             fades at every join so cuts don't click
  2. picture crop to the aspect, upscale 2x, then zoompan per frame from the
             plan's moves (smoothstep ramps), then burn the ASS subtitles
  3. sound   each cue plays from the SFX kit built by sfx_kit.py (your own
             pack), aligned so the sound's PEAK lands on the cue time; without
             a kit, pop / whoosh / ding are synthesized in code. Voice is
             levelled to -16 LUFS before the mix and the master to -14 LUFS,
             the level TikTok / Reels / Shorts play at. plan.music, when set,
             is a background bed from the user's private library: it starts at
             the track's energy point, sits about 14 dB under the voice (ducked
             3:1 while anyone talks), and fades out at the end

Subtitles light up word by word: the spoken word turns yellow and pops from
135% to 100% in 140 ms; emphasis words use the display font. Fonts are Google
Fonts (OFL, free for commercial use) fetched once into a cache; without network
it falls back to an installed Thai font.

Usage:
  python3 render.py plan.json --out final.mp4 [--width 1080] [--workdir DIR]
                    [--sfx-kit ~/.cache/talking-head-edit/sfx/kit.json]

Prints `VIDEO: <abs-path>` on the last line. Exit 1 on bad input, 4 if ffmpeg
fails.
Requires: ffmpeg (with libass) on PATH. Python stdlib only.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

FPS = 30
SR = 48000
FONT_URLS = {
    "Mitr": "https://raw.githubusercontent.com/google/fonts/main/ofl/mitr/Mitr-Bold.ttf",
    "Pattaya": "https://raw.githubusercontent.com/google/fonts/main/ofl/pattaya/Pattaya-Regular.ttf",
}
# fullname libass matches on, per downloaded file
FONT_NAMES = {"Mitr": "Mitr Bold", "Pattaya": "Pattaya Regular"}
CACHE = Path.home() / ".cache" / "talking-head-edit" / "fonts"


# ---------------------------------------------------------------- sound ----

def write_wav(path: Path, samples: list[float]) -> None:
    peak = max(1e-9, max(abs(s) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(s / peak * 0.9 * 32767))
                               for s in samples))


def synth_pop() -> list[float]:
    """Bubble pop: a sine sweeping 1100 -> 260 Hz in 90 ms, fast decay."""
    n, out, ph = int(0.09 * SR), [], 0.0
    for i in range(n):
        t = i / n
        ph += 2 * math.pi * (260 + 840 * (1 - t) ** 3) / SR
        out.append(math.sin(ph) * math.exp(-5 * t) * min(1, i / 48))
    return out


def synth_whoosh() -> list[float]:
    """Air swish: white noise through a band-pass sweeping 400 -> 3500 Hz."""
    rnd = random.Random(7)
    n, out = int(0.45 * SR), []
    low = band = 0.0
    for i in range(n):
        t = i / n
        f = 400 + 3100 * t ** 1.5
        k = 2 * math.sin(math.pi * f / SR)
        x = rnd.uniform(-1, 1)
        low += k * band
        high = x - low - 0.35 * band
        band += k * high
        out.append(band * math.sin(math.pi * t) ** 2)
    return out


def synth_ding() -> list[float]:
    """Bright bell: E6 with two partials, 0.8 s exponential decay."""
    n, out = int(0.8 * SR), []
    for i in range(n):
        t = i / SR
        env = math.exp(-6 * t) * min(1, i / 96)
        out.append(env * (math.sin(2 * math.pi * 1318.5 * t)
                          + 0.45 * math.sin(2 * math.pi * 2637 * t) * math.exp(-4 * t)
                          + 0.2 * math.sin(2 * math.pi * 3951 * t) * math.exp(-8 * t)))
    return out


SYNTHS = {"pop": synth_pop, "whoosh": synth_whoosh, "ding": synth_ding}
SYNTH_PEAK = {"pop": 0.0, "whoosh": 0.22, "ding": 0.0}  # seconds to the loudest point


# ---------------------------------------------------------------- fonts ----

def ensure_fonts() -> tuple[Path, dict[str, str]]:
    """Fetch the two Google Fonts once; fall back to an installed Thai font."""
    CACHE.mkdir(parents=True, exist_ok=True)
    names = {}
    for fam, url in FONT_URLS.items():
        f = CACHE / url.rsplit("/", 1)[1]
        if not f.exists():
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    f.write_bytes(r.read())
            except Exception as e:  # noqa: BLE001 - any failure means fallback
                print(f"font {fam} unavailable ({e}); using a system Thai font",
                      file=sys.stderr)
        if f.exists():
            names[fam] = FONT_NAMES[fam]
        else:
            r = subprocess.run(["fc-match", "-f", "%{fullname[0]}",
                                ":lang=th:weight=bold"], capture_output=True, text=True)
            names[fam] = r.stdout.strip() or "Sans"
    return CACHE, names


# ------------------------------------------------------------ subtitles ----

def ass_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_time(t: float) -> str:
    t = max(t, 0)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def clean(s: str) -> str:
    return s.replace("{", "").replace("}", "").replace("\\", "")


def build_ass(plan: dict, w: int, h: int, fonts: dict[str, str]) -> str:
    st = plan["style"]
    size = round(w * st["size"])
    y = round(h * st["y"])
    act, base = ass_color(st["active"]), ass_color(st["base"])
    emph_font = fonts["Pattaya"]
    ev = []
    for ln in plan["lines"]:
        ws = ln["words"]
        for i, wd in enumerate(ws):
            s = ln["start"] if i == 0 else wd["s"]
            e = ws[i + 1]["s"] if i + 1 < len(ws) else ln["end"]
            if e <= s:
                continue
            parts = []
            reset = r"\fn" + fonts["Mitr"] + r"\fscx100\fscy100"
            for j, x in enumerate(ws):
                font = r"\fn" + emph_font + r"\fscx115\fscy115" if x.get("emph") else ""
                if j == i:
                    big = 155 if x.get("emph") else 135
                    end = 115 if x.get("emph") else 100
                    parts.append(r"{\c" + act + reset + font + rf"\fscx{big}\fscy{big}"
                                 + rf"\t(0,140,\fscx{end}\fscy{end})}}" + clean(x["w"]))
                else:
                    parts.append(r"{\c" + base + reset + font + "}" + clean(x["w"]))
            intro = r"\fscx70\fscy70\t(0,120,\fscx100\fscy100)" if i == 0 else ""
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Pop,,0,0,0,,"
                      + rf"{{\an2\pos({w // 2},{y}){intro}}}" + "".join(parts))
    outline = max(2, round(size * 0.09))
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{fonts['Mitr']},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,{outline},{max(1, outline // 2)},2,40,40,0,222

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n"


# --------------------------------------------------------------- camera ----

def curve(moves: list[dict], key: str) -> str:
    """ffmpeg expression of `it` that follows the moves with smoothstep ramps."""
    def value_at(t: float, upto: int) -> float:
        v = moves[0][key]
        for m in moves[1:upto]:
            if t < m["t"]:
                break
            r = m["ramp"]
            if r > 0 and t < m["t"] + r:
                u = (t - m["t"]) / r
                return v + (m[key] - v) * u * u * (3 - 2 * u)
            v = m[key]
        return v

    expr = f"{moves[0][key]:.4f}"
    for k, m in enumerate(moves[1:], start=1):
        v1 = m[key]
        r = m["ramp"]
        if r > 0:
            v0 = value_at(m["t"], k)
            u = f"((it-{m['t']:.3f})/{r:.3f})"
            body = (f"if(lt(it,{m['t'] + r:.3f}),"
                    f"{v0:.4f}+({v1 - v0:.4f})*{u}*{u}*(3-2*{u}),{v1:.4f})")
        else:
            body = f"{v1:.4f}"
        expr = f"if(gte(it,{m['t']:.3f}),{body},{expr})"
    return expr


def probe(src: str) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:stream_side_data=rotation", "-of", "json", src],
        capture_output=True, text=True, check=True)
    st = json.loads(r.stdout)["streams"][0]
    w, h = st["width"], st["height"]
    rot = 0
    for sd in st.get("side_data_list", []):
        rot = int(sd.get("rotation", 0) or 0)
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        raise SystemExit(4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("plan")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--sfx-kit", default=str(CACHE.parent / "sfx" / "kit.json"),
                    help="kit.json from sfx_kit.py (default: the cache; synth if absent)")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    src = plan["source"]
    if not Path(src).is_file():
        print(f"source missing: {src}", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("ffmpeg not on PATH", file=sys.stderr)
        return 1
    work = Path(args.workdir or tempfile.mkdtemp(prefix="the-"))
    work.mkdir(parents=True, exist_ok=True)

    # output geometry
    sw, sh = probe(src)
    ar = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0}.get(plan["aspect"], sw / sh)
    W = args.width - args.width % 2
    H = int(round(W / ar / 2) * 2)
    fx, fy = plan["focus"]
    if sw / sh > ar:   # crop width around the face
        cw, ch = int(sh * ar) // 2 * 2, sh
    else:
        cw, ch = sw, int(sw / ar) // 2 * 2
    cx = min(max(fx * sw - cw / 2, 0), sw - cw)
    cy = min(max(fy * sh - ch / 2, 0), sh - ch)
    # focus re-expressed inside the cropped frame
    moves = [dict(m, fx=(m["fx"] * sw - cx) / cw, fy=(m["fy"] * sh - cy) / ch)
             for m in plan["moves"]]

    # 1. jump cuts
    segs = plan["segments"]
    fc, cat = [], ""
    for i, (s, e) in enumerate(segs):
        d = e - s
        fc.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS[v{i}]")
        fade = f",afade=t=in:d=0.03,afade=t=out:st={max(d - 0.03, 0):.3f}:d=0.03" \
            if len(segs) > 1 else ""
        fc.append(f"[0:a]atrim={s}:{e},asetpts=PTS-STARTPTS{fade}[a{i}]")
        cat += f"[v{i}][a{i}]"
    fc.append(f"{cat}concat=n={len(segs)}:v=1:a=1[vc][ac];[vc]fps={FPS}[vf]")
    cut = work / "cut.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", ";".join(fc),
         "-map", "[vf]", "-map", "[ac]", "-c:v", "libx264", "-crf", "12",
         "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", str(cut)])

    # 2. subtitles + fonts
    fontdir, fonts = ensure_fonts()
    ass = work / "subs.ass"
    ass.write_text(build_ass(plan, W, H, fonts), encoding="utf-8")

    # 3. sound effects
    cues = plan.get("sfx", [])
    inputs = ["-i", str(cut)]
    kit_path = Path(args.sfx_kit).expanduser()
    kit = json.loads(kit_path.read_text(encoding="utf-8")) if kit_path.is_file() else {}
    # each cue -> (file, peak); a role's variants rotate so repeats don't sound canned
    by_file: dict[str, list[tuple[dict, float]]] = {}
    seen: dict[str, int] = {}
    synth_files = {}
    for c in cues:
        k = c["kind"]
        if k in kit:
            v = kit[k][seen.get(k, 0) % len(kit[k])]
            seen[k] = seen.get(k, 0) + 1
            f, peak = str(kit_path.parent / v["file"]), v["peak"]
        elif k in SYNTHS:
            if k not in synth_files:
                synth_files[k] = work / f"{k}.wav"
                write_wav(synth_files[k], SYNTHS[k]())
            f, peak = str(synth_files[k]), SYNTH_PEAK[k]
        else:
            print(f"no sound for sfx kind '{k}' (build a kit with sfx_kit.py); skipped",
                  file=sys.stderr)
            continue
        by_file.setdefault(f, []).append((c, peak))
    idx = {}
    for f in by_file:
        idx[f] = inputs.count("-i")
        inputs += ["-i", f]
    print(f"sfx: {'kit ' + str(kit_path) if kit else 'synthesized (no kit)'}",
          file=sys.stderr)

    z = curve(moves, "z")
    x = f"max(0,min(iw-iw/zoom,({curve(moves, 'fx')})*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,({curve(moves, 'fy')})*ih-ih/zoom/2))"
    esc = str(ass).replace("\\", "/").replace(":", r"\:")
    fdir = str(fontdir).replace("\\", "/").replace(":", r"\:")
    graph = [f"[0:v]crop={cw}:{ch}:{int(cx)}:{int(cy)},scale={2 * W}:{2 * H}:flags=lanczos,"
             f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS},"
             f"ass='{esc}':fontsdir='{fdir}',format=yuv420p[v]"]
    # voice to -16 LUFS first so kit SFX (also -16 LUFS) sit `gain` below it
    mus = plan.get("music")
    if mus and not Path(mus["file"]).is_file():
        print(f"music file missing: {mus['file']}; rendering without music",
              file=sys.stderr)
        mus = None
    duck = bool(mus and mus.get("duck", True))
    graph.append("[0:a]loudnorm=I=-16:TP=-1.5:LRA=11"
                 + (",asplit=2[voice][vkey]" if duck else "[voice]"))
    mix = ["[voice]"]
    if mus:
        mi = inputs.count("-i")
        inputs += ["-stream_loop", "-1", "-ss", str(mus.get("start", 0)), "-i", mus["file"]]
        d = plan["duration"]
        fo = mus.get("fade_out", 1.5)
        graph.append(
            f"[{mi}:a]atrim=0:{d:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"loudnorm=I=-16:TP=-2,volume={mus.get('gain_db', -6)}dB,"
            f"afade=t=in:d={mus.get('fade_in', 0.4)},"
            f"afade=t=out:st={max(d - fo, 0):.3f}:d={fo}"
            + ("[mraw];[mraw][vkey]sidechaincompress=threshold=0.05:ratio=3:"
               "attack=20:release=400[mus]" if duck else "[mus]"))
        mix.append("[mus]")
    for fi, (f, mine) in enumerate(by_file.items()):
        k = f"s{fi}_"
        labels = "".join(f"[{k}{n}]" for n in range(len(mine)))
        graph.append(f"[{idx[f]}:a]asplit={len(mine)}{labels}" if len(mine) > 1
                     else f"[{idx[f]}:a]anull{labels}")
        for n, (c, peak) in enumerate(mine):
            start = c["t"] - peak          # cue time is where the sound HITS
            trim = f"atrim=start={-start:.3f},asetpts=PTS-STARTPTS," if start < 0 else ""
            ms = max(0, int(start * 1000))
            graph.append(f"[{k}{n}]{trim}adelay={ms}:all=1,volume={c['gain']}[{k}{n}o]")
            mix.append(f"[{k}{n}o]")
    if len(mix) > 1:
        graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:"
                     "duration=first,loudnorm=I=-14:TP=-1:LRA=11[a]")
    else:
        graph.append("[voice]loudnorm=I=-14:TP=-1:LRA=11[a]")

    out = Path(args.out).resolve()
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
         str(out)])
    print(f"{W}x{H}, {plan['duration']:.2f}s, {len(plan['lines'])} lines, "
          f"{len(plan['moves'])} moves, {len(cues)} sfx; work files in {work}",
          file=sys.stderr)
    print(f"VIDEO: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
