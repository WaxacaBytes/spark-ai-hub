# Ideogram 4 prompt guide: a JSON caption, never plain text

Condensed from Ideogram's docs/prompting.md and its caption-writing system prompt
src/ideogram4/magic_prompt_system_prompts/v1.txt (https://github.com/ideogram-oss/ideogram4).
Ideogram 4 was trained only on structured JSON captions. A plain-text prompt comes back as a
gray picture reading "Image blocked by safety filter" far more often than a JSON one; Ideogram
calls the false-positive rate "high for non-json prompts". Send the caption as the `prompt`
string: one line of minified JSON, no code fences, no other text. Even a JSON caption is blocked
on some seeds; the Hub then returns an error naming the seed instead of the gray picture, and the
same caption may render on another seed.

## 1. Three keys, in this order

```
{"high_level_description":"...","style_description":{...},"compositional_deconstruction":{"background":"...","elements":[...]}}
```

- **high_level_description**: one sentence (two at most, 50 words) that starts with the subject:
  who or what, the medium, the overall composition. No "this image shows".
- **style_description** (optional): keys in this exact order:
  - photo: `aesthetics`, `lighting`, `photo`, `medium` ("photograph"), `color_palette`
  - anything else: `aesthetics`, `lighting`, `medium` ("illustration", "3d_render", "painting",
    "graphic_design", ...), `art_style`, `color_palette`
  Use `photo` (camera and lens: "35mm, f/1.4, eye-level") or `art_style` ("flat vector,
  bold outlines"), never both. `color_palette` is optional: up to 16 uppercase `#RRGGBB` colours;
  include the background colour too.
- **compositional_deconstruction** (required): `background` first, then `elements`.

## 2. Background and elements

- **background**: the setting only: sky, walls, floor, ground, distant scenery, light. The
  ground or floor always lives here. Things a viewer could point at and place are elements,
  not background. For a cutout, write exactly `transparent background`.
- **elements**: one entry per subject, object or piece of text, keys in this order:
  - `{"type":"obj","bbox":[y1,x1,y2,x2],"desc":"...","color_palette":[...]}`
  - `{"type":"text","bbox":[y1,x1,y2,x2],"text":"...","desc":"...","color_palette":[...]}`
  `bbox` and `color_palette` (up to 5 colours) are optional.
- **desc**: 30-60 words in English: what it is, its colours, materials, pose, expression. No
  camera words (focus, bokeh, blur, grain) inside an element.
- **Name what the user named.** A brand, product, public figure, character, artwork or
  landmark goes into its `desc` by full name ("Spider-Man", "Coca-Cola logo", "Eiffel Tower"),
  not as a lookalike description. Ideogram's own example captions name real people and logos.
- **Commit to one value.** No "or", "such as", "various", "maybe", "implied": pick one colour,
  one typeface category, one count.

## 3. Text in the picture

- Every word a viewer could read is its own `text` element, `text` verbatim (keep accents,
  capitals and the user's language; all other fields stay in English).
- `desc` says size, place, typeface category (serif, sans-serif, script, display, monospace),
  weight and colour.
- Use `\n` inside one `text` for stacked lines of one block; separate elements for separate
  blocks. Stack long stylized titles at word breaks ("THE LAST\nCHAPTER").

## 4. Boxes

- `bbox` is `[y1, x1, y2, x2]`, 0-1000 on both axes, top-left origin, measured on the picture
  you ask for: on 16:9 a square object needs a narrower x-span than y-span.
- Give boxes to things whose place matters (people, products, logos, signs). Leave them off
  crowds, skies and scattered things.

## 5. Example

```
{"high_level_description":"A clean, modern business card layout for a tech company.","style_description":{"aesthetics":"minimal, professional, geometric","lighting":"even, diffuse studio lighting","medium":"graphic_design","art_style":"flat vector design, generous whitespace, sans-serif typography","color_palette":["#FFFFFF","#F0F0F0","#333333","#0066FF","#00CC88"]},"compositional_deconstruction":{"background":"A solid off-white card surface with subtle paper texture.","elements":[{"type":"text","text":"ACME TECH","desc":"Bold dark grey sans-serif company name across the upper third of the card."},{"type":"text","text":"hello@acme.tech","desc":"Small blue sans-serif contact email near the bottom of the card."}]}}
```
