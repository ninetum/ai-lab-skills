#!/usr/bin/env python3
"""Word-timed transcript for a talking-head clip -- the clock every edit hangs on.

Karaoke subtitles light up one word at a time, and keyword zooms fire on the
word that names them, so the transcript must carry a start and end per WORD,
not per sentence.

Thai has no spaces, and Whisper's word timestamps on Thai come back one
CHARACTER at a time ("ไหน" arrives as three timed pieces). We rebuild real words
by segmenting the text with PyThaiNLP and giving each word the span of its
characters. Without PyThaiNLP, characters are grouped into short clusters that
never split a vowel or tone mark from its consonant -- readable, but the
highlight jumps in pieces rather than words.

Usage:
  python3 transcribe.py VIDEO --out words.json [--model large-v3] [--lang th]

Output JSON:
  {language, model, duration_s, words:[{w, s, e}]}   (seconds)
  prints `WORDS: <abs-path>` on the last line

Exit codes:
  0  written
  1  bad args / missing input
  2  faster-whisper not installed
  3  no speech found

Requires: ffmpeg on PATH, `pip install faster-whisper` (and `pythainlp` for
Thai). Python stdlib otherwise.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

# Thai leading vowels start a new cluster; marks never start one.
THAI_LEAD = set("เแโใไ")


def is_mark(ch: str) -> bool:
    return unicodedata.category(ch) in ("Mn", "Mc")


def is_thai(text: str) -> bool:
    return any("฀" <= c <= "๿" for c in text)


def load_audio(path: Path):
    """Decode to 16 kHz mono float32 via ffmpeg.

    faster-whisper's own decoder goes through PyAV and breaks whenever the
    installed av and faster-whisper versions disagree (seen: av 16 rejects the
    `metadata_errors` argument). ffmpeg is already required, so use it.
    """
    import numpy as np  # faster-whisper depends on numpy

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "s16le",
         "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, "int16").astype("float32") / 32768.0


def char_times(pieces: list[dict]) -> list[tuple[str, float, float]]:
    """Spread each timed piece evenly over its characters."""
    out = []
    for p in pieces:
        txt = p["w"]
        n = max(len(txt), 1)
        for i, ch in enumerate(txt):
            out.append((ch, p["s"] + (p["e"] - p["s"]) * i / n,
                        p["s"] + (p["e"] - p["s"]) * (i + 1) / n))
    return out


def clusters(text: str, max_len: int = 4) -> list[str]:
    """Fallback Thai grouping: short runs that keep marks on their consonant."""
    out, cur = [], ""
    for ch in text:
        start_new = cur and not is_mark(ch) and (
            ch in THAI_LEAD or sum(not is_mark(c) for c in cur) >= max_len)
        if start_new:
            out.append(cur)
            cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


def thai_words(pieces: list[dict]) -> list[dict]:
    chars = [c for c in char_times(pieces) if not c[0].isspace()]
    text = "".join(c[0] for c in chars)
    try:
        from pythainlp.tokenize import word_tokenize  # type: ignore
        toks = [t for t in word_tokenize(text, engine="newmm") if t.strip()]
    except ImportError:
        print("pythainlp not installed: grouping Thai by character clusters "
              "(pip install pythainlp for real words)", file=sys.stderr)
        toks = clusters(text)
    words, pos = [], 0
    for t in toks:
        seg = chars[pos:pos + len(t)]
        pos += len(t)
        if seg:
            words.append({"w": t, "s": round(seg[0][1], 3),
                          "e": round(seg[-1][2], 3)})
    return words


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="large-v3",
                    help="faster-whisper model (default large-v3; small is "
                         "faster but much weaker on Thai)")
    ap.add_argument("--lang", default=None, help="e.g. th, en (default: detect)")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_file():
        print(f"no such file: {video}", file=sys.stderr)
        return 1
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        print("faster-whisper not installed: pip install faster-whisper",
              file=sys.stderr)
        return 2

    audio = load_audio(video)
    model = WhisperModel(args.model, device="auto", compute_type="int8")
    segs, info = model.transcribe(audio, language=args.lang,
                                  word_timestamps=True, beam_size=5)
    pieces = []
    for s in segs:
        for w in s.words or []:
            pieces.append({"w": w.word, "s": w.start, "e": w.end})
    if not pieces:
        print("no speech found", file=sys.stderr)
        return 3

    if is_thai("".join(p["w"] for p in pieces)):
        words = thai_words(pieces)
    else:
        words = [{"w": p["w"].strip(), "s": round(p["s"], 3),
                  "e": round(p["e"], 3)} for p in pieces if p["w"].strip()]

    out = Path(args.out).resolve()
    out.write_text(json.dumps({
        "language": info.language, "model": args.model,
        "duration_s": round(len(audio) / 16000, 3), "words": words,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(" ".join(w["w"] for w in words), file=sys.stderr)
    print(f"WORDS: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
