#!/usr/bin/env python3
"""
One-off: fill in `credit`/`source` on the covers that were added from local
files, and repoint DeepSeek at imagery that is actually connected to it.

Commons-sourced covers already carry their attribution (the migration wrote
it). The Qwen capybaras and the application artwork were copied out of local
banners, so their provenance was never recorded — this adds it.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "registry" / "recipes"

CAPY = ("cfahlgren1/flux-qwen-capybara — a FLUX.1-dev LoRA recreating the "
        "Qwen capybara · FLUX.1-dev non-commercial licence")
CAPY_URL = "https://huggingface.co/cfahlgren1/flux-qwen-capybara"

# stem -> (credit, source)
PATCH: dict[str, tuple[str, str]] = {
    f"qwen-{s}": (CAPY, CAPY_URL)
    for s in ("08b", "2b", "4b", "9b", "27b", "35b", "122b")
}

PATCH.update({
    # Pulled from each project's own README / model card during this work.
    "app-acestep": ("ACE-Step project artwork",
        "https://raw.githubusercontent.com/ACE-Step/ACE-Step-1.5/main/assets/application_map.png"),
    "app-chatterbox": ("Resemble AI project artwork",
        "https://raw.githubusercontent.com/resemble-ai/chatterbox/master/Chatterbox-Multilingual.png"),
    "app-deep-live-cam": ("Deep-Live-Cam demo reel",
        "https://raw.githubusercontent.com/hacksider/Deep-Live-Cam/main/media/demo.gif"),
    "app-minicpm-o": ("MiniCPM-o project artwork",
        "https://raw.githubusercontent.com/OpenBMB/MiniCPM-o/main/assets/minicpmv4.6/video_play.png"),
    "app-pixal3d": ("Pixal3D teaser, TencentARC",
        "https://raw.githubusercontent.com/TencentARC/Pixal3D/master/assets/teaser.png"),
    "app-spatialedit": ("SpatialEdit project artwork",
        "https://raw.githubusercontent.com/EasonXiao-888/SpatialEdit/main/assets/application/camera/output.png"),
    "app-onyx": ("Onyx product demo",
        "https://github.com/onyx-dot-app/onyx/releases/download/v3.0.0/Onyx.gif"),
    "app-firered": ("FireRed Image Edit showcase",
        "https://github.com/FireRedTeam/FireRed-Image-Edit/raw/main/assets/showcase_portrait.jpg"),
    "app-foundation1": ("Foundation-1 model-card banner, RoyalCities",
        "https://huggingface.co/RoyalCities/Foundation-1/resolve/main/Charts/banner.PNG"),
    "app-lance": ("Lance logo art, ByteDance Research",
        "https://huggingface.co/bytedance-research/Lance/resolve/main/assets/logo/lance-logo.webp"),
    "app-qwen-image": ("Qwen-Image 2512 model-card artwork, Alibaba",
        "https://qianwen-res.oss-accelerate-overseas.aliyuncs.com/Qwen-Image/image2512/image2512big.png"),
    "app-qwen-angles": ("Multiple-Angles LoRA animation grid, fal",
        "https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA/resolve/main/all_animations_combined.gif"),
    "app-voicebox": ("Voicebox site artwork",
        "https://voicebox.sh/og.webp"),

    # Already in the repo before this work; the exact file each came from was
    # never recorded, so credit the project rather than invent a URL.
    "app-comfyui": ("ComfyUI project artwork (from the Hub's original banner set)",
        "https://github.com/comfyanonymous/ComfyUI"),
    "app-facefusion": ("FaceFusion project artwork (from the Hub's original banner set)",
        "https://github.com/facefusion/facefusion"),
    "app-hunyuan3d": ("Hunyuan3D project artwork (from the Hub's original banner set)",
        "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1"),
    "app-trellis2": ("TRELLIS project artwork (from the Hub's original banner set)",
        "https://github.com/microsoft/TRELLIS"),
    "app-anythingllm": ("AnythingLLM project artwork (from the Hub's original banner set)",
        "https://github.com/Mintplex-Labs/anything-llm"),
    "app-flowise": ("Flowise project artwork (from the Hub's original banner set)",
        "https://github.com/FlowiseAI/Flowise"),
    "app-langflow": ("Langflow project artwork (from the Hub's original banner set)",
        "https://github.com/langflow-ai/langflow"),
    "app-localai": ("LocalAI project artwork (from the Hub's original banner set)",
        "https://github.com/mudler/LocalAI"),
    "app-openwebui": ("Open WebUI project artwork (from the Hub's original banner set)",
        "https://github.com/open-webui/open-webui"),
})

# DeepSeek's cover was CERN's data centre — nothing to do with them. Hangzhou
# is where DeepSeek and its parent High-Flyer are based.
REPOINT = {
    "deepseek-cern.jpg": dict(
        image="deepseek-hangzhou.jpg",
        caption=("Qianjiang New City in Hangzhou at night, the city DeepSeek and its "
                 "parent High-Flyer are based in. No freely licensed photograph of "
                 "DeepSeek's founder or offices exists."),
        credit="Light show in Qianjiang New City 05.png · Y Chen · CC BY-SA 4.0",
        source="https://commons.wikimedia.org/wiki/File%3ALight%20show%20in%20Qianjiang%20New%20City%2005.png",
    ),
}


def q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(cover: dict) -> str:
    lines = ["cover:", f"  image: {cover['image']}", f"  caption: {q(cover['caption'])}"]
    if cover.get("fit", "cover") != "cover":
        lines.append(f"  fit: {cover['fit']}")
    if cover.get("backdrop_fit"):
        lines.append(f"  backdrop_fit: {cover['backdrop_fit']}")
    if cover.get("grade", True) is False:
        lines.append("  grade: false")
    if cover.get("credit"):
        lines.append(f"  credit: {q(cover['credit'])}")
    if cover.get("source"):
        lines.append(f"  source: {q(cover['source'])}")
    return "\n".join(lines) + "\n"


def main() -> None:
    patched = repointed = 0
    for path in sorted(RECIPES.glob("*/recipe.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        cover = dict(data.get("cover") or {})
        if not cover.get("image"):
            continue
        before = dict(cover)

        if cover["image"] in REPOINT:
            cover.update(REPOINT[cover["image"]])
            repointed += 1

        stem = Path(cover["image"]).stem
        if stem in PATCH and not cover.get("credit"):
            cover["credit"], cover["source"] = PATCH[stem]
            patched += 1

        if cover == before:
            continue
        text = re.sub(r"\ncover:\n(?:  .*\n)*", "\n", path.read_text())
        path.write_text(text.rstrip("\n") + "\n\n" + render(cover))

    print(f"{patched} covers given credit/source, {repointed} repointed")


if __name__ == "__main__":
    main()
