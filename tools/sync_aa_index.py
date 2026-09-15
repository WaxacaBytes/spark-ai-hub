#!/usr/bin/env python3
"""
Write the Artificial Analysis Intelligence Index score into every recipe.yaml
in a model group. Values below were fetched from artificialanalysis.ai
directly (or cross-checked against it) on 2026-09-07, against **Intelligence
Index v4.2** (released 2026-09-04) — not estimated, not inferred from a slug.

v4.2 is a different index, not a refresh of the same one: it drops the
saturated GPQA Diamond, adds AA-Briefcase and Surge's GDP.pdf, and doubles
held-out private test sets to 40% of the weighting. Scores compress downward
across the board, so a v4.1 number and a v4.2 number are not comparable — the
whole catalog has to move together or the ranking lies.

Where a model publishes several reasoning-effort variants, take the
highest-effort one (xhigh/max): that is the mode these recipes actually serve.
`None` means no published score exists for that model — either a community
finetune with no independent evaluation, or a genuinely unevaluated model.
"""

import glob
import os
import re

RECIPES_DIR = "registry/recipes"

# model_key -> AA Intelligence Index score, or None if not published
AA_INDEX = {
    "anythingllm": None,
    "deepseek-v4-flash": 41,
    "diffusiongemma-26b-a4b-it": 8,
    "gemma4-12b": 16,
    "gemma4-26b-a4b": 19,
    "gemma4-31b": 22,
    "gemma4-e2b": 4,
    "gemma4-e4b": 6,
    "glm47-flash": 17,
    "glm53-flash": 46,
    "gpt-oss-120b": 16,
    "gpt-oss-20b": 9,
    "hy3": 32,
    "inkling-small": 32,
    "laguna-s-21": None,
    "laguna-xs-21": None,
    "ling3-flash": 27,
    "ling3-tiny": 16,
    "mimo-v25": 28,
    "minimax-m27": 30,
    "muse-glimmer-30b": 24,
    "nemotron-cascade2-30b-a3b": 12,
    "nemotron3-elastic-30b-a3b": None,
    "nemotron3-nano": 9,
    "nemotron3-nano-omni-30b-a3b": 9,
    "nemotron3-puzzle-75b-a9b": None,
    "nemotron3-super-120b": 19,
    "nemotron35-lightning-30b-a3b": 16,
    "ollama-openwebui": None,
    "onyx": None,
    "ornith15-35b-a3b": None,
    "ornith15-35b-a3b-uncensored": None,
    "phi4-multimodal": 1,
    "phi4-reasoning": None,
    "qwen35-08b": 1,
    "qwen35-122b-a10b": 25,
    "qwen35-27b": 27,
    "qwen35-2b": 2,
    "qwen35-35b-a3b": 23,
    "qwen35-4b": 14,
    "qwen35-9b": 15,
    "qwen36-27b": 29,
    "qwen36-27b-aeon-ultimate": None,
    "qwen36-35b-a3b": 26,
    "qwen36-35b-a3b-heretic": None,
    "qwen38-27b": 41,
    "qwen38-27b-aeon-ultimate": None,
    "qwen38-flash-next": 46,
    "seed-oss-36b": 12,
}

ENGINE_PREFIX = re.compile(r"^(vllm|sglang|llamacpp|atlas)-")
# Kept in lockstep with BUILD_TOKENS in frontend/src/models.js — the two have
# to strip the same trailing tokens or a build lands in one model group in the
# UI and another one here, and gets the wrong score.
BUILD_TOKENS = {
    "bf16", "fp8", "nvfp4", "int4", "mxfp4", "awq", "gptq",
    "q8", "q4", "iq2m", "iq1m", "q3ks", "q2kxl", "q4km", "exl3",
    "k2", "bf16head",
    "dflash", "dflash2", "dspark", "mtp", "eagle",
    "abliterated", "ple", "mmap",
}


def model_key(slug):
    parts = ENGINE_PREFIX.sub("", slug).split("-")
    while len(parts) > 1 and parts[-1] in BUILD_TOKENS:
        parts.pop()
    return "-".join(parts)


def update_recipe_yaml(slug, score):
    p = os.path.join(RECIPES_DIR, slug, "recipe.yaml")
    with open(p) as f:
        content = f.read()

    content = re.sub(r"^artificial_analysis_index:.*\n", "", content, flags=re.M)

    value = "null" if score is None else str(score)
    insertion = f"artificial_analysis_index: {value}\n"

    new_content, n = re.subn(
        r"^speculative_method:\s*\"[^\"]*\"\n",
        lambda m: m.group(0) + insertion,
        content, count=1, flags=re.M,
    )
    if n == 0:
        return False

    if new_content != content:
        with open(p, "w") as f:
            f.write(new_content)
        return True
    return False


def main():
    changed = 0
    unmatched = set()
    for recipe_dir in sorted(glob.glob(f"{RECIPES_DIR}/*/recipe.yaml")):
        slug = os.path.basename(os.path.dirname(recipe_dir))
        key = model_key(slug)
        if key not in AA_INDEX:
            unmatched.add(key)
            continue
        if update_recipe_yaml(slug, AA_INDEX[key]):
            changed += 1
            print(f"{slug:45} key={key:35} -> {AA_INDEX[key]}")

    print(f"\n{changed} recipe.yaml files updated")
    if unmatched:
        print(f"UNMATCHED KEYS (no entry in AA_INDEX map): {sorted(unmatched)}")


if __name__ == "__main__":
    main()
