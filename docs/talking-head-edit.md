## What it does

`talking-head-edit` takes a raw clip of someone talking to camera and returns a
short that is ready to post. Dead air is cut out, the framing punches in at
every cut, the camera zooms on the words that call for it, every spoken word
lights up and pops in the subtitles, and each beat lands with a sound effect.

The speaker directs the edit. Saying "ซูมอิน" or "zoom in" on camera pushes the
camera in on that word, and saying "ซูมเอาท์" pulls it back. Words like
"ป๊อป", "เด้ง" and "ว้าว" bounce the frame, ring a ding, and switch to the
display font. The style is fixed and applied without questions, so the same
request never has to be explained twice.

## When to reach for it

Type `/talking-head-edit`, or the agent reaches for it when you hand over a
to-camera clip and ask for it to be edited, subtitled or made into a short.

| Your situation | Where to go |
| --- | --- |
| "ตัดต่อคลิปนี้", "ใส่ซับเด้งๆ", "add word-by-word captions" | This skill |
| A vlog or explainer with long pauses to tighten | This skill, which cuts silences and hides the jumps with punch-ins |
| "What happens in this video?" / summarize / find a moment | [`video-understand`](../skills/video-understand/SKILL.md) |
| Many clips, b-roll, music, or a multi-camera timeline | Not this skill: it edits one continuous take |

## Prerequisites

| Need | Why |
| --- | --- |
| `ffmpeg` with libass | Cutting, zooming, subtitles, mixing |
| `pip install faster-whisper` | Word-timed transcript. The first run downloads the model from huggingface.co |
| `pip install pythainlp` | Thai only. It rebuilds real words from Whisper's character-level Thai timing |

The fonts (Mitr Bold and Pattaya, both from Google Fonts under the OFL) download
on first use. Without network access, an installed Thai font is used instead.

## The plan file

Every decision goes into one `plan.json` before anything renders:

- **segments**: the source ranges that survive the silence cut
- **lines**: subtitle lines, each with word timings
- **moves**: zoom level, focus point and ramp time, each with the reason it fired
- **sfx**: pop, whoosh and ding cues

The agent reads the plan and adds what a rule cannot see. When the speaker
points over their shoulder and says "there's something behind me", the agent
adds a move that drifts the camera to the background. The renderer then plays
the plan back exactly and makes no choices of its own.

## Your SFX pack, by role

Point `sfx_kit.py` at your own sound-effect pack, a folder or a .zip. It sorts
the pack into **roles**: pop, whoosh, ding, cash, click, camera, punch, fail,
victory, drama, riser, glitch and typing. Each pick is trimmed, levelled, and
marked with where its peak sits, so a whoosh hits on the zoom rather than a
beat late. The agent places the role that fits what is being said: a ka-ching
on a price, a shutter when a product is shown, a riser into a reveal.

Without a pack, pop, whoosh and ding are synthesized in code (a pitch-dropping
sine, swept band-passed noise, a decaying bell), so the skill still works with
nothing installed.

## Your own music, kept private

`music_lib.py` registers your own background tracks by **theme** (main,
travel, ...) in a library outside the repo. The agent picks a theme to match
the clip. Product and sales clips get no music at all: voice and sound effects
carry them. The bed starts where the track's energy kicks in, sits about
14 dB under the voice, ducks while anyone talks, and fades out at the end. The
tracks never enter the repo, which is public.

Trending and commercial songs are deliberately left out of the render. Add the
sound in the TikTok or Reels app when posting: it avoids copyright takedowns,
and the platforms push clips that use their trending sounds.

## Common questions

**Can it make a 9:16 TikTok version from a 4:3 or landscape clip?**
Yes. Pass `--aspect 9:16` and it crops around the face. Without that flag it
keeps the source aspect.

**Can it put the trending TikTok song on it?**
No. Songs mixed into the file get muted or taken down, and business accounts
are limited to TikTok's Commercial Music Library. Add the trending sound in
the app when you post. Your own tracks, from the private music library, are
mixed in.

**Why are some Thai words highlighted in pieces?**
PyThaiNLP splits some loanwords into smaller parts ("ซับ ไต เติล"). Line breaks
avoid splitting those parts across lines, but the highlight still steps through
each part.

## It's working if

- The output is shorter than the input by about the length of its pauses
- The word being spoken is yellow and pops; the words before and after it are white
- The frame visibly pushes in when the speaker says a zoom word
- You hear a soft pop as each subtitle line appears, and a whoosh on each zoom
- The voice plays at about the same loudness as other clips in the feed (around -14 LUFS)

## Where it fits

[`video-understand`](../skills/video-understand/SKILL.md) watches a video and
tells you what is in it. This skill changes the video. It fits single-take
talking-head footage, which is most of what gets posted as shorts.

Measured on a 9.0 s, 480x360 portrait H.264 phone clip of Thai speech, on a
4-core container with no GPU: transcription with large-v3 took 27.2 s (model
cached), planning 0.2 s, and rendering 12.2 s for a 7.3 s 1080x1440 output. Building an SFX kit from a
395 MB, 636-file pack took 11 s.
