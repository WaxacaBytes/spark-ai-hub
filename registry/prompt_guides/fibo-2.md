# Fibo 2.0 prompt guide: a VGL caption (JSON) gives the most control

Condensed from Bria's `vgl/README.md` in briaai/Fibo-2.0-dev ("Writing VGL captions for Fibo 2.0").
Fibo 2.0 was trained on structured JSON captions in Bria's Visual GenAI Language (VGL). A plain
sentence also works, but a full caption follows counts, positions, light and text much more
closely. Send the caption as the `prompt` string: one JSON object, no code fences, no other text.

## 1. Text-to-image: the caption describes the image

Keys, in this order (the pipeline drops empty ones, so leave out what you don't need):

| Key | What it holds |
|---|---|
| `short_description` | A summary of the whole image, 200 words at most. **Required.** |
| `objects` | Up to 5 prominent subjects, one entry each (below). |
| `background_setting` | The environment and every background element not in `objects`. **Required.** |
| `lighting` | `{"conditions", "direction", "shadows"}`: e.g. "golden hour", "side-lit from left", "long, soft shadows". **Required.** |
| `aesthetics` | `{"composition", "color_scheme", "mood_atmosphere"}`: e.g. "rule of thirds", "warm complementary colors", "serene". **Required.** |
| `photographic_characteristics` | `{"depth_of_field", "focus", "camera_angle", "lens_focal_length"}`: e.g. "shallow", "sharp focus on subject", "eye-level", "portrait lens (e.g., 50mm-85mm)". |
| `style_medium` | "photograph", "oil painting", "3D render", "digital illustration", ... |
| `text_render` | Every piece of text in the image, one entry per line or label (below). |
| `context` | What kind of image this is: "a product shot for an e-commerce listing". **Required.** |
| `artistic_style` | 3 words at most: "realistic", "impressionistic, vibrant, textured". |

**`objects` entries:** `description` (100 words at most), `location` ("center", "top-left",
"bottom-right foreground"), `relationship` (to the other objects), and optionally `relative_size`
("large within frame"), `shape_and_color`, `texture`, `appearance_details`, `orientation`,
`number_of_objects` (an integer). People also take `pose`, `expression`, `clothing`, `action`,
`gender`, `skin_tone_and_texture`.

**`text_render` entries:** `text` (the exact words, never a placeholder), `location`, `size`,
`color`, `font` ("bold sans-serif", "handwritten"), optional `appearance_details`. No text in the
image: leave `text_render` out. Don't add text the user didn't ask for, except text that belongs to
an object (STOP on a stop sign).

## 2. Edits: the caption describes the result, plus `edit_instruction`

Give the edit as the same caption of the **edited** image with one more key, `edit_instruction`:
one imperative command starting with a verb (Add, Change, Make, Move, Remove, Render, Turn, Zoom).
A plain instruction alone also works as the prompt for an edit.

| You want | `edit_instruction` |
|---|---|
| a cartoon version | `Turn the image into the cartoon style.` |
| a hat on the person | `Add a wide-brimmed felt hat to the subject.` |
| no book in their hands | `Remove the book from the subject's hands.` |
| a wider shot | `Change the perspective to a wider shot.` |
| new text on a sign | `Change the text "Happy Anniversary" to "Hello".` |

- **Several pictures (up to 4):** name them `<image_1>`, `<image_2>`, ... in the order you pass them
  ("Dress the person in <image_1> in the dress from <image_2>.").
- **Describe the result, not the change:** "his hands are empty", not "the book was removed".
  Outside `edit_instruction`, avoid "changed", "added", "removed", "replaced", "now".
- **Keep everything the edit doesn't touch:** copy the source's subjects, pose, composition, camera
  and style into the caption as they are.

## 3. Rules for every caption

- **Describe, don't instruct.** Every field but `edit_instruction` describes what is in the image:
  "a sad woman", not "make the woman sad".
- **A person's left and right are their own,** even when their left arm is on the right of the frame.
- **Keep fields consistent:** a snowy forest in `background_setting` means the light and mood agree.
- **Each detail once, in its own field.** Background things go in `background_setting`.
- **Default to a photograph:** `style_medium` "photograph", `artistic_style` "realistic", unless
  another medium is wanted.
- **Frame people closely** when a person is the subject: "medium shot" or "close-up", `relative_size`
  "large within frame", a standard or portrait lens.
- **Size:** about one megapixel works best (1024x1024, 1424x800 for 16:9, 1248x832 for 3:2).

## 4. Example

```
{"short_description":"A red city bicycle with a wicker basket leans against a white brick wall on a quiet street on a sunny morning. A small wooden sign above it reads \"OPEN\".","objects":[{"description":"A red city bicycle with a step-through frame, black tyres and a wicker basket on the handlebars.","location":"center","relationship":"Leaning against the wall, below the sign.","relative_size":"large within frame","shape_and_color":"Classic upright bicycle frame, glossy cherry red.","texture":"glossy painted metal and woven wicker","orientation":"side-on, facing left"}],"background_setting":"A white-painted brick wall with a green wooden door on the right and a cobblestone pavement below.","lighting":{"conditions":"bright morning sun","direction":"side-lit from the right","shadows":"crisp shadow of the bicycle cast on the wall to the left"},"aesthetics":{"composition":"centered, eye-level, with the bicycle filling the lower two thirds","color_scheme":"red and green accents on white","mood_atmosphere":"calm, cheerful"},"photographic_characteristics":{"depth_of_field":"deep","focus":"sharp focus on the bicycle","camera_angle":"eye-level","lens_focal_length":"standard lens (e.g., 35mm-50mm)"},"style_medium":"photograph","text_render":[{"text":"OPEN","location":"top center, on a sign above the bicycle","size":"medium","color":"white","font":"hand-painted serif","appearance_details":"painted on a small dark wooden board"}],"context":"A lifestyle street photograph for a café's social media post.","artistic_style":"realistic"}
```
