#!/usr/bin/env python3
"""
One-off: move cover art out of the build script and into the recipes.

Materialises every source picture into registry/covers/ and writes a `cover:`
block into each registry/recipes/*/recipe.yaml. After this runs, which image
a recipe uses is data that ships with the recipe — which is what lets a
third-party recipe bring its own — and tools/build_covers.py only renders
what the registry declares.

Run once:  python3 tools/migrate_covers.py
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_covers import fetch_commons, lab_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "frontend" / "public"
RECIPES = ROOT / "registry" / "recipes"
DEST = ROOT / "registry" / "covers"

MAX_W = 2400


def commons(name, file, caption, fit="cover", grade=True):
    return dict(name=name, commons=file, caption=caption, fit=fit, grade=grade)


def local(name, path, caption, fit="cover", grade=False):
    return dict(name=name, local=path, caption=caption, fit=fit, grade=grade)


# ── one picture per (lab, exact parameter size) ───────────────────────
G = "Mountain View (CA, USA), Charleston Road, Google-{} -- 2022 -- {}.jpg"

MODEL_COVERS: dict[tuple[str, float], dict] = {
    # Alibaba / Qwen — the capybara gets busier as the models get bigger.
    ("alibaba", 0.8): local("qwen-08b", "banners/qwen-beach.png",
        "The Qwen capybara relaxing on a beach. Shared by Qwen models of 0.8B parameters.", "contain"),
    ("alibaba", 2): local("qwen-2b", "banners/qwen-basketball.png",
        "The Qwen capybara playing basketball. Shared by Qwen models of 2B parameters.", "contain"),
    ("alibaba", 4): local("qwen-4b", "banners/qwen-hoodie.png",
        "The Qwen capybara at a laptop in a Qwen hoodie. Shared by Qwen models of 4B parameters.", "contain"),
    ("alibaba", 9): local("qwen-9b", "banners/qwen-driving.png",
        "The Qwen capybara at the wheel. Shared by Qwen models of 9B parameters.", "contain"),
    ("alibaba", 27): local("qwen-27b", "banners/qwen-coder.png",
        "The Qwen capybara writing SQL. Shared by Qwen models of 27B parameters.", "contain"),
    ("alibaba", 35): local("qwen-35b", "banners/qwen-gym.png",
        "The Qwen capybara bench-pressing. Shared by Qwen models of 35B parameters.", "contain"),
    ("alibaba", 122): local("qwen-122b", "banners/qwen-astronaut.png",
        "The Qwen capybara suited up as an astronaut. Shared by Qwen models of 122B parameters.", "contain"),

    # Google — the places Gemma is built.
    ("google", 2.3): commons("google-googleplex", "Google Campus, Mountain View, CA.jpg",
        "The Googleplex in Mountain View, California — Google's main campus."),
    ("google", 4.5): commons("google-council-bluffs", "Google Data Center, Council Bluffs Iowa (49062863796).jpg",
        "Google's data centre in Council Bluffs, Iowa, one of the sites its models are trained in."),
    ("google", 12): commons("google-deepmind-london", "Google-Deep Mind headquarters in London, 6 Pancras Square.jpg",
        "Google DeepMind's headquarters at 6 Pancras Square, London."),
    ("google", 25.2): commons("google-sign", G.format("Schild", 2896),
        "The Google sign on Charleston Road, Mountain View, at the edge of the Googleplex."),
    ("google", 26): commons("google-bikes", G.format("Fahrräder", 2901),
        "Google's campus bicycles on Charleston Road, Mountain View."),
    ("google", 31): commons("google-campus-green", G.format("Erholungsfläche", 2909),
        "A recreation area on Google's Mountain View campus."),

    # Everyone else.
    ("nvidia", 30): commons("nvidia-hq", "2788-2888 San Tomas Expwy.jpg",
        "NVIDIA's headquarters on San Tomas Expressway in Santa Clara, California."),
    ("nvidia", 120): commons("nvidia-gpu-cluster", "CSIRO ScienceImage 11313 The CSIRO GPU cluster at the data centre.jpg",
        "A GPU cluster in a data centre — the hardware NVIDIA's largest models are trained on."),
    ("microsoft", 5.6): commons("microsoft-redmond", "Microsoft Redmond Campus redevelopment aerial view, Sept. 2021.jpg",
        "Microsoft's Redmond campus in Washington, photographed mid-redevelopment in 2021."),
    ("microsoft", 14): commons("microsoft-building-92", "Building92microsoft.jpg",
        "Building 92 on Microsoft's Redmond campus, home of the visitor centre."),
    ("openai", 21): commons("openai-pioneer", "Pioneer Building, San Francisco (2019) -1.jpg",
        "The Pioneer Building in San Francisco's Mission District, OpenAI's former headquarters."),
    ("openai", 117): commons("openai-1515-third", "1515 Third Street.jpg",
        "1515 Third Street in Mission Bay, San Francisco — OpenAI's headquarters."),
    ("poolside", 33): commons("poolside-station-f", "Parvis Alan Turing Station F Paris.jpg",
        "Parvis Alan Turing outside Station F in Paris, the startup campus in the city poolside builds from."),
    ("poolside", 117.6): commons("poolside-la-defense", "From Louvre to La Défense, Paris 2012.jpg",
        "Paris looking west toward La Défense, standing in for poolside's Paris engineering base."),
    ("inclusion", 7.9): commons("inclusion-pudong", "There's the Pudong skyline again (36273761462).jpg",
        "Shanghai's Pudong skyline. inclusionAI is Ant Group's research lab."),
    ("inclusion", 124): commons("inclusion-hangzhou", "View of the night time Hangzhou skyline from the West Lake.JPG",
        "Hangzhou at night seen from the West Lake — Ant Group, and its inclusionAI lab, are headquartered here."),
    ("meta", 29.6): commons("meta-hq", "Meta HQ 2023.png",
        "Meta's headquarters in Menlo Park, California."),
    ("bytedance", 36): commons("bytedance-hq", "ByteDance 1733 Commercial Space (20240731145554).jpg",
        "ByteDance's 1733 Commercial Space building in Beijing."),
    ("tencent", 295): commons("tencent-towers", "Tencent Seafront Towers, the Tencent HQ in Shenzhen (2026) - img 01.jpg",
        "Tencent Seafront Towers in Shenzhen, Tencent's headquarters and the home of the Hunyuan team."),
    ("zai", 30): commons("zai-shanghai-bund", "Shanghai bund – Panorama (Greg Zaal via Poly Haven).jpg",
        "The Bund waterfront in Shanghai at night. Z.AI has no freely licensed imagery of its own, so its cover uses the city it works in."),
    ("deepseek", 284): commons("deepseek-cern", "Cern datacenter.jpg",
        "CERN's data centre in Geneva. No free-licensed photograph of DeepSeek's own offices exists, so its cover borrows machine-room photography."),
    ("minimax", 172): commons("minimax-cray", "Some cabinets of the Cray XE6 Blue Waters supercomputer.JPG",
        "Cabinets of the Cray XE6 \"Blue Waters\" supercomputer, standing in for MiniMax."),
    ("thinking-machines", 276): commons("thinking-machines-fugaku", "FugakuSupercomputerSC19.jpg",
        "The Fugaku supercomputer at RIKEN in Kobe, Japan. Thinking Machines has no freely licensed imagery of its own, so its cover borrows machine-room photography."),
}

# ── one picture per application recipe ────────────────────────────────
APP_COVERS: dict[str, dict] = {
    "comfyui-spark": local("app-comfyui", "banners/comfyui-spark.jpg",
        "A ComfyUI node graph — the app's own interface.", "contain"),
    "facefusion-spark": local("app-facefusion", "banners/facefusion-spark.png",
        "FaceFusion's own project artwork."),
    "hunyuan3d-spark": local("app-hunyuan3d", "banners/hunyuan3d-spark.png",
        "A gallery of meshes generated by Hunyuan3D."),
    "trellis2-spark": local("app-trellis2", "banners/trellis2-spark.png",
        "Sample 3D assets generated by TRELLIS.2."),
    "anythingllm": local("app-anythingllm", "banners/anythingllm.png",
        "AnythingLLM's own project artwork.", "contain"),
    "flowise": local("app-flowise", "banners/flowise.png",
        "A Flowise flow canvas — the app's own interface.", "contain"),
    "langflow": local("app-langflow", "banners/langflow.png",
        "Langflow's own project artwork.", "contain"),
    "localai": local("app-localai", "banners/localai.png",
        "LocalAI's own project artwork.", "contain"),
    "ollama-openwebui": local("app-openwebui", "banners/ollama-openwebui.png",
        "The Open WebUI chat interface, which this recipe pairs with Ollama.", "contain"),
    "acestep": local("app-acestep", "banners/apps/acestep.jpg",
        "ACE-Step's application map, from the project's own README.", "contain"),
    "chatterbox-turbo": local("app-chatterbox", "banners/apps/chatterbox.jpg",
        "Resemble AI's Chatterbox Multilingual key art, from the project's README.", "contain"),
    "deep-live-cam": local("app-deep-live-cam", "banners/apps/deep-live-cam.jpg",
        "A frame from Deep-Live-Cam's own demo reel."),
    "firered-image-edit": local("app-firered", "banners/apps/firered.jpg",
        "A portrait-editing showcase from FireRed Image Edit's model card."),
    "foundation-1": local("app-foundation1", "banners/apps/foundation1.jpg",
        "Foundation-1's banner from its Hugging Face model card.", "contain"),
    "lance": local("app-lance", "banners/apps/lance.jpg",
        "ByteDance's Lance logo art. The model card's video previews are only 288px wide, too small to use as a cover.", "contain"),
    "minicpm-o": local("app-minicpm-o", "banners/apps/minicpm-o.jpg",
        "A video-understanding demo from MiniCPM-o's README.", "contain"),
    "onyx": local("app-onyx", "banners/apps/onyx.jpg",
        "A frame from Onyx's own product demo.", "contain"),
    "pixal3d-spark": local("app-pixal3d", "banners/apps/pixal3d.jpg",
        "Pixal3D's teaser image, from TencentARC's repository."),
    "qwen-image-2512": local("app-qwen-image", "banners/apps/qwen-image.jpg",
        "Qwen-Image 2512's showcase grid, from Alibaba's model card.", "contain"),
    "qwen-image-perspective": local("app-qwen-angles", "banners/apps/qwen-angles.jpg",
        "A frame from the Multiple-Angles LoRA's animation grid, showing one subject re-shot from many camera angles.", "contain"),
    "spatialedit": local("app-spatialedit", "banners/apps/spatialedit.jpg",
        "A camera-control edit from SpatialEdit's repository."),
    "voicebox": local("app-voicebox", "banners/apps/voicebox.jpg",
        "Voicebox's own site artwork.", "contain"),
}


def materialise(spec: dict) -> dict:
    """Copy/normalise the source into registry/covers/ and return yaml fields."""
    DEST.mkdir(parents=True, exist_ok=True)
    out = DEST / f"{spec['name']}.jpg"
    fields = {"image": out.name, "caption": spec["caption"], "fit": spec["fit"],
              "grade": spec["grade"]}

    if not out.exists():
        if spec.get("commons"):
            path, meta = fetch_commons(spec["commons"])
            fields["credit"] = " · ".join(x for x in
                (meta["file"], meta["artist"], meta["license"]) if x)
            fields["source"] = meta["page"]
        else:
            path = PUBLIC / spec["local"]
        im = Image.open(path).convert("RGB")
        if im.width > MAX_W:
            im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
        im.save(out, "JPEG", quality=92, optimize=True)
    elif spec.get("commons"):
        _, meta = fetch_commons(spec["commons"])
        fields["credit"] = " · ".join(x for x in
            (meta["file"], meta["artist"], meta["license"]) if x)
        fields["source"] = meta["page"]
    return fields


def yaml_block(f: dict) -> str:
    def q(s):
        return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'
    lines = ["cover:", f"  image: {f['image']}", f"  caption: {q(f['caption'])}"]
    if f["fit"] != "cover":
        lines.append(f"  fit: {f['fit']}")
    if not f["grade"]:
        lines.append("  grade: false")
    if f.get("credit"):
        lines.append(f"  credit: {q(f['credit'])}")
    if f.get("source"):
        lines.append(f"  source: {q(f['source'])}")
    return "\n".join(lines) + "\n"


def main() -> None:
    import yaml
    cache: dict[str, dict] = {}
    written = unmatched = 0

    for path in sorted(RECIPES.glob("*/recipe.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        slug = data.get("slug", path.parent.name)
        is_model = slug.startswith(("vllm-", "llamacpp-", "atlas-"))

        if is_model:
            spec = MODEL_COVERS.get((lab_key(data), data.get("params_b")))
        else:
            spec = APP_COVERS.get(slug)

        if spec is None:
            print(f"  ! no cover for {slug}")
            unmatched += 1
            continue

        if spec["name"] not in cache:
            cache[spec["name"]] = materialise(spec)
        fields = cache[spec["name"]]

        text = path.read_text()
        text = re.sub(r"\ncover:\n(?:  .*\n)*", "\n", text)   # replace if re-run
        text = text.rstrip("\n") + "\n\n" + yaml_block(fields)
        path.write_text(text)
        written += 1

    print(f"\n{written} recipes given a cover, {unmatched} unmatched, "
          f"{len(cache)} images in {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
