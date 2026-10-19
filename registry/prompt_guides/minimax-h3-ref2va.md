# MiniMax-H3 prompt guide: references (Ref2VA models)

Condensed from MiniMax's VIDEO_PROMPT_WRITING_GUIDE_ref_en.md
(https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/docs). H3's reference models were trained
on prompts with these six sections, in this order; write them in English (dialogue, lyrics and
visible text stay in their own language). Camera wording, speaker IDs and `<d>` dialogue are as
in the FL2VA guide (get_prompt_guide on an FL2VA H3 model).

## Numbering

The Hub numbers what you pass in the order given: `video` is <Video 1>, then `image` and
`images` are <Picture 1>, <Picture 2>, ..., then `audio` is <Audio 1>. Videos and audio are
numbered separately. A reference video's own soundtrack is not an <Audio N> unless you say so.

## 1. subject_definitions

One line per thing that will be reused, labelled `<Subject N>` (a person, animal, object, place,
outfit, style or motion), saying where it comes from and what to keep:
`<Subject 1> is the young woman in <Picture 1>, with long dark hair, a blue cardigan and a thin silver necklace.`
Give a picture its own line only when it is a concrete frame (first, key or last frame, or a
storyboard): `<Picture 2> is the first frame of [Shot 1], ...`. `<Video N>` is for a whole video
being edited or continued, or whose camera, cuts or rhythm are followed. `<Audio N>` says its role:
`<Audio 1> is the voice-timbre reference for <Subject 1> (S1).`

## 2. summary

One short paragraph starting with the task types in brackets, joined with ` + `:
`keyframe completion` (a picture is a concrete frame), `reference generation` (references guide
a character, place, style, motion), `video editing` (a source video is changed; then begin with
`The target video is an edited version of <Video 1>.`), `video continuation`, `audio reuse`
(the same signal is used), `audio reference` (only its timbre, style or rhythm).
Example: `[video editing + audio reuse] The target video is an edited version of <Video 1>, ...`

## 3. retention_analysis

One line per label, with a fixed marker:
`<Subject 1> (appears in [Shot 1]): fully_preserved - ...` (also `partially_preserved`,
`attribute_transfer`, `weak_reference`); for audio `fully_copy`, `partially_copy`, `reference`,
`weak_reference`.

## 4. detailed_description

The main body, 350-500 words for new videos (edits as long as the source needs). One or two
sentences of overall style first, then `[Shot 1] ...`, `[Shot 2] At 00:04.000, the shot cuts to ...`.
Per shot: composition, how each subject looks and where it is, place and light, actions,
camera, sound, and where each reference takes effect (`the shot begins from <Picture 1>`).
A referenced person who speaks keeps both labels: `<Subject 1> (S1) says: <d>[English] ...</d>`.

## 5. overall_soundscape and 6. non_diegetic_music

As in the FL2VA guide: ambience and action sounds in 1-4 sentences; score only the audience
hears in 1-3 sentences. `N/A` asks for silence / no music (without both sections H3 tends to
invent a soundtrack).

## Example (face swap on a clip)

```
subject_definitions: <Video 1> is the source video for the target video edit. <Subject 1> is the woman whose face comes from <Picture 1>: short curly black hair, brown eyes, olive skin.
summary: [video editing + audio reuse] The target video is an edited version of <Video 1> in which the kneeling woman has the face of <Subject 1>.
retention_analysis: <Video 1> (whole video): partially_preserved - motion, framing, dogs and terrace are kept; only her face changes. <Subject 1> (appears in [Shot 1]): attribute_transfer - her face replaces the original one.
detailed_description: Live-action, sunny daylight. [Shot 1] The shot follows <Video 1>: on a gravel terrace the woman, now with <Subject 1>'s face, kneels and strokes a long-haired ginger dog that turns to her; she laughs as a black dog trots past behind them.
overall_soundscape: Dogs panting, soft laughter, birdsong and a light breeze, as in <Video 1>.
non_diegetic_music: N/A
```
