# MiniMax-H3 prompt guide: text, first frame, last frame (FL2VA models)

Condensed from MiniMax's VIDEO_PROMPT_WRITING_GUIDE_base_en.md
(https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/docs). H3 was trained on prompts in this
exact shape; it makes the picture, the speech and the sound from it. Write it in English; keep
dialogue, lyrics and visible text in their own language.

## 1. First line: only when you pass pictures

- `image` only (first frame):
  `For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`
- `image` + `last_image`:
  `How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.`
- `last_image` only:
  `How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.`

N is the last shot's number; S.SS is the clip length in seconds with two decimals (5.00 for
`seconds: 5`). Leave one blank line after it. Text-only clips start directly with section 2.

## 2. Three fields, in this order

```
integrated_multimodal_description: [Shot 1] <style>, <framing> ... [Shot 2] At 00:03.500, the camera cuts to ...

overall_soundscape: <1-4 sentences of ambience, action sounds, breathing, laughter>

non_diegetic_music: <1-3 sentences of score only the audience hears, or N/A>
```

- **integrated_multimodal_description** carries everything seen and heard, in time order. Open
  `[Shot 1]` with a style (`Live-action, cinematic`, `2D-animated`, `3D CG`, `claymation`,
  `watercolor`, `vintage film`) and the opening composition. `[Shot 1]` has no time; later shots
  start with a strictly increasing cut time `At MM:SS.mmm,` and should bring new information
  (for a small change of distance or angle, move the camera instead of cutting).
- With a first frame, anchor `[Shot 1]` on `<Picture 1>` (keep its people, clothes, layout), then
  describe what happens next. With first and last frames, describe the path between them, not two
  still pictures; one shot is best. With a last frame only, start from a plausible earlier state
  and land on the picture at the end.
- **overall_soundscape** never repeats dialogue or singing. `N/A` only for total silence.
- **non_diegetic_music**: instruments, tempo, dynamics; no mood words. `N/A` for none.

## 3. Camera: type + optional amplitude + speed, as a sentence

Types: zoom in/out, push in/pull out, pan left/right, truck left/right, tilt up/down, pedestal
up/down, arc shot, tracking shot, static shot, shake slightly/strongly, POV, roll
clockwise/counterclockwise. Add `with small/large amplitude` and `at slow/fast speed` only when
they matter: `The camera pushes in with small amplitude at slow speed toward the letter.`

## 4. Speech and singing

- Give each voice a stable ID the first time it is heard, with what identifies it (age, gender,
  on screen or not, pitch, timbre, pace, accent): `the young woman with a quiet, breathy voice (S1)`.
  Two at once: `(S1,S2)`. People who never speak get no ID.
- Only the language tag and the exact words go inside `<d>`, starting with a capital letter:
  `(S1) says: <d>[English] I get off at the next station.</d>`
- Voice-over: `(S1) says in an off-screen voiceover: <d>[English] ...</d>` followed by
  `while his lips remain completely closed.`
- A line crossing a cut: `<scenetrans>` at both ends and say it `continues seamlessly across the cut`.
  Cut off by the clip's end: `<cutoff>`.

## 5. Visible text

Quote signs, labels and subtitles exactly: `A red neon sign reading "OPEN" glows above the door.`

## Example (first frame, 5 s)

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, the blonde woman kneeling on the sunny terrace in <Picture 1> keeps her clothes, pose and the dogs around her. The camera pushes in with small amplitude at slow speed as she strokes the ginger dog, which turns its head toward her, and she laughs. The cheerful young woman (S1) says: <d>[English] Who's a good boy?</d>

overall_soundscape: A light breeze moves through the palms as the dogs pant and gravel crunches under their paws, with distant birdsong.

non_diegetic_music: N/A
```
