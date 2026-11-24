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

The sound effects are synthesized in code (a pitch-dropping sine for pop,
swept band-passed noise for whoosh, a decaying bell for ding). There is no
sample pack to license and nothing to download.

## Common questions

**Can it make a 9:16 TikTok version from a 4:3 or landscape clip?**
Yes. Pass `--aspect 9:16` and it crops around the face. Without that flag it
keeps the source aspect.

**Why are some Thai words highlighted in pieces?**
PyThaiNLP splits some loanwords into smaller parts ("ซับ ไต เติล"). Line breaks
avoid splitting those parts across lines, but the highlight still steps through
each part.

## It's working if

- The output is shorter than the input by about the length of its pauses
- The word being spoken is yellow and pops; the words before and after it are white
- The frame visibly pushes in when the speaker says a zoom word
- You hear a soft pop as each subtitle line appears, and a whoosh on each zoom

## Where it fits

[`video-understand`](../skills/video-understand/SKILL.md) watches a video and
tells you what is in it. This skill changes the video. It fits single-take
talking-head footage, which is most of what gets posted as shorts.

Measured on a 9.0 s, 480x360 portrait H.264 phone clip of Thai speech, on a
4-core container with no GPU: transcription with large-v3 took 27.2 s (model
cached), planning 0.2 s, and rendering 12.2 s for a 7.3 s 1080x1440 output.
