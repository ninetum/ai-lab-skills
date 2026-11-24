# Put every edit decision in a plan file, and render it blindly

An automatic editor tends to bury its choices inside the render: where to cut,
when to zoom, which word to stress. When one of those choices is wrong, the only
fix is to tweak a heuristic and re-render everything.

`talking-head-edit` splits the work. `plan.py` writes every decision to
`plan.json` (kept segments, subtitle lines, camera moves with a reason each,
SFX cues). `render.py` plays the file back and decides nothing.

The split matters because the best edits come from meaning, not from rules. On
the first test clip, the speaker said "มีอะไรบางอย่างอยู่ข้างหลัง" ("there's
something behind me") and pointed over their shoulder. No keyword rule catches
that. The agent reads the transcript and adds one move to the plan, a drift
toward the background. Keeping the plan editable makes the rules a starting
point that the agent finishes.

Two related choices follow from the same goal of keeping the skill a folder you
copy:

- **Sound effects come from the user's own pack, with a synthesized floor.**
  `sfx_kit.py` sorts any pack into roles and records each sound's peak, so a
  cue names the moment the sound hits. Without a pack, three stdlib generators
  (pop, whoosh, ding) keep the skill working with nothing installed. The skill
  never ships a sample library. Third-party SFX licences vary, so the pack
  stays the user's.
- **Camera moves use ffmpeg `zoompan` driven by one nested expression** of the
  input time, rather than per-frame image processing in Python. That keeps the
  scripts stdlib-only. The cost is that zoompan crops at whole-pixel positions,
  which shimmers on a slow zoom, so the frame is upscaled 2x first. On the
  9.0 s test clip the full render took 12.2 s on 4 CPU cores.
