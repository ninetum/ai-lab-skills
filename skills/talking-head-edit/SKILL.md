---
name: talking-head-edit
description: >-
  Edit a talking-head / vlog / to-camera clip into a ready-to-post short in one
  pass. It cuts dead air with jump cuts, adds punch-in zooms and zooms when the
  speaker says "zoom in/out", burns in karaoke subtitles that light up and pop
  one word at a time (Thai or English), and lays sound effects (pop, whoosh,
  ding) on every beat. Use whenever the user hands over a video of someone
  talking and wants it edited, cut, subtitled or made punchy for TikTok, Reels
  or Shorts: "edit this clip", "add word-by-word subtitles", "karaoke captions",
  "make it a short", "ตัดต่อคลิปนี้", "ตัดต่อให้หน่อย", "ใส่ซับ", "ใส่ซับเด้งๆ",
  "ซับเปลี่ยนสีทีละคำ", "ตัดช่วงเงียบ", "ใส่ซาวด์เอฟเฟกต์", "ทำคลิปลง TikTok".
  Apply the full default style without asking. Do NOT use for watching,
  summarizing or searching a video (use video-understand), for multi-camera or
  b-roll timelines, music videos, or generating video from nothing.
---

# Talking-Head Edit

Turn a raw to-camera clip into a short that holds attention: the silences go,
the camera moves on the beats, every spoken word lights up on screen, and the
moves land with a sound. Three scripts do the mechanics; you make the editorial
calls in between.

- `scripts/transcribe.py`: word-timed transcript (faster-whisper; Thai words
  rebuilt with PyThaiNLP).
- `scripts/plan.py`: writes `plan.json`, which holds every decision (kept
  segments, subtitle lines, camera moves, SFX cues). You read and edit it.
- `scripts/render.py`: renders the plan exactly. It makes no choices of its own.

**Apply the house style without asking.** The user set it up so they never have
to repeat it. Jump cuts, punch-ins, keyword zooms, karaoke subtitles (Mitr Bold;
emphasis words in Pattaya; active word yellow), and SFX are all on by default.
Ask only when the request truly forks: a 9:16 crop is a choice, so use it when
they say TikTok/Reels/Shorts/เต็มจอ and keep the source aspect otherwise.

## Resolve `SKILL_DIR` first

Set `SKILL_DIR` to the absolute path of the directory holding THIS SKILL.md.
Scripts are its `scripts/` sibling. Use one work dir per clip.

## Step 1: transcribe

```bash
python3 "$SKILL_DIR/scripts/transcribe.py" "<video>" --out <work>/words.json --lang th
```

Prints the words to stderr and `WORDS: <path>` last. Read the words. Whisper
mishears names and slang; fix the `w` text in `words.json` by hand and leave the
times alone. Pass `--lang` whenever you know it, because detection on a few
seconds of audio guesses wrong.

Thai needs `pythainlp`. Without it, Whisper's Thai word timestamps arrive one
character at a time, and the fallback groups them into short clusters. That
output is readable, but the highlight then jumps in fragments instead of words.

## Step 2: find the face

```bash
ffmpeg -v error -ss 1 -i "<video>" -frames:v 1 -vf scale=360:-1 <work>/face.jpg
```

Look at it and estimate where the face is as fractions of the frame (`x,y`, with
0,0 top-left). Zooms push toward this point. Getting it wrong zooms into a wall.

## Step 3: plan

```bash
python3 "$SKILL_DIR/scripts/plan.py" "<video>" <work>/words.json --out <work>/plan.json \
  --focus 0.5,0.42 [--aspect 9:16] [--emphasis คำ1,คำ2]
```

It prints the kept duration and one line per camera move with the reason. The
defaults:

| What | Default | Change with |
| --- | --- | --- |
| Silence cut | gaps >= 0.45 s at -32 dB, 0.12 s pad kept each side | `--min-silence`, `--silence-db`, `--pad`, `--no-cuts` |
| Punch-in | framing alternates 1.0x / 1.12x at each cut and every ~3 s | `--no-punch` |
| Keyword zoom | "ซูมอิน/ซูมเข้า/zoom in" pushes to 1.5x; "ซูมเอาท์/ซูมออก/zoom out" returns; auto-return after 2 s | edit `moves` |
| Emphasis | ป๊อป เด้ง ว้าว สุดยอด เจ๋ง ปัง ที่สุด ฟรี wow boom amazing: bounce + ding + display font | `--emphasis` |
| SFX | pop when a subtitle line lands, whoosh on zooms, ding on emphasis | `--no-sfx`, edit `sfx` |

**Then do the editing a script can't.** Read the transcript for what the speaker
*points at* and add moves for it in `plan.json`. A move is
`{"t": seconds_on_output_clock, "z": zoom, "fx": 0-1, "fy": 0-1, "ramp": seconds, "why": "..."}`.
`ramp: 0` is a hard cut. Moves apply in time order and hold until the next one.
Examples:

- "มีอะไรอยู่ข้างหลัง" / "look behind me": ease to z 1.4 with `fx,fy` on the
  background over 1 s, then cut back to the face on the next line.
- A product held up: push toward it while it is named.
- Add an emphasis word the defaults missed: set `"emph": true` on that word in
  `lines` and add a `ding` cue at its `s`.

All times in the plan are on the OUTPUT clock, after cuts. `segments` are the
only source-clock times.

## Step 4: render

```bash
python3 "$SKILL_DIR/scripts/render.py" <work>/plan.json --out <work>/final.mp4 --workdir <work>/render
```

Width defaults to 1080, and height follows the aspect. On first run it downloads
Mitr Bold and Pattaya (Google Fonts, OFL) to `~/.cache/talking-head-edit/fonts`.
If that download fails, it warns and falls back to an installed Thai font.

## Step 5: check before you hand it over

```bash
ffmpeg -v error -i <work>/final.mp4 -vf "fps=1,scale=270:-1,tile=4x2" -frames:v 1 <work>/check.jpg
```

Look at it. Confirm that the subtitles sit clear of the face and are not cut at
the frame edge, that zooms land on the face rather than a wall, and that no line
is a lone fragment. Then send the video to the user and say what was cut
(seconds before and after) and which moves fired on which words.

## Traps

- **Thai word timing from Whisper is inflated across pauses.** A word followed
  by a pause can claim the whole pause ("มี" spanning 1.0 s of silence). Cuts
  therefore come from `silencedetect`, never from word gaps, and `plan.py` snaps
  each word into the kept segment it overlaps most.
- **faster-whisper's own decoder breaks on PyAV version drift** (seen: `open()
  got an unexpected keyword argument 'metadata_errors'`). `transcribe.py`
  decodes with ffmpeg instead. Do not "fix" this by passing the file path.
- **zoompan rounds its crop to whole pixels**, so a slow zoom straight off a
  phone frame shimmers. `render.py` upscales 2x before zoompan. Keep that step.
- **Model download in sandboxes.** The first run fetches the Whisper model from
  huggingface.co, and its files come off `us.aws.cdn.hf.co`. A network
  allowlist needs both hosts.
- **`--min-silence` is a style dial.** Raise it to 0.7 for calm explainers.
  Below 0.3 it starts eating breaths mid-sentence.

## Measured

On a 9.0 s, 480x360 (portrait, rotated) H.264 phone clip of Thai speech, on a
4-core cloud container with no GPU: `transcribe.py` with large-v3 took 27.2 s
(model already cached), `plan.py` 0.2 s, and `render.py` 12.2 s for a 7.3 s
1080x1440 output.
