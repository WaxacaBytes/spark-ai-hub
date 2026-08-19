#!/usr/bin/env python3
"""
Write the Artificial Analysis Intelligence Index score into every recipe.yaml
in a model group. Values below were fetched from artificialanalysis.ai
directly (or cross-checked against it) on 2026-08-18 — not estimated, not
inferred from a slug. `None` means no published score exists for that model
(either a community finetune with no independent evaluation, or a genuinely
unpublished model).
"""

import glob
import os
import re

RECIPES_DIR = "registry/recipes"

# model_key -> AA Intelligence Index score, or None if not published
AA_INDEX = {
    "anythingllm": None,
    "deepseek-v4-flash": 52,
    "diffusiongemma-26b-a4b-it": None,
    "gemma4-12b": 22,
    "gemma4-26b-a4b": 26,
    "gemma4-31b": 39,
    "gemma4-e2b": 10,
    "gemma4-e4b": 12,
    "glm47-flash": 23,
    "gpt-oss-120b": 24,
    "gpt-oss-20b": 15,
    "hy3": 41,
    "inkling-small": 41,
    "laguna-s-21": None,
    "laguna-xs-21": None,
    "ling3-flash": 38,
    "ling3-tiny": 25,
    "mimo-v25": 38,
    "minimax-m27": 39,
    "muse-glimmer-30b": 35,
    "nemotron-cascade2-30b-a3b": 18,
    "nemotron3-elastic-30b-a3b": None,
    "nemotron3-nano": 15,
    "nemotron3-nano-omni-30b-a3b": 15,
    "nemotron3-puzzle-75b-a9b": None,
    "nemotron3-super-120b": 26,
    "nemotron35-lightning-30b-a3b": 24,
    "ollama-openwebui": None,
    "onyx": None,
    "phi4-multimodal": 5,
    "phi4-reasoning": None,
    "qwen35-08b": 5,
    "qwen35-122b-a10b": 33,
    "qwen35-27b": 35,
    "qwen35-2b": 7,
    "qwen35-35b-a3b": 30,
    "qwen35-4b": 20,
    "qwen35-9b": 22,
    "qwen36-27b": 38,
    "qwen36-27b-aeon-ultimate": None,
    "qwen36-35b-a3b": 32,
    "qwen36-35b-a3b-heretic": None,
    "qwen38-27b": 52,
    "qwen38-27b-aeon-ultimate": None,
    "seed-oss-36b": 19,
}

ENGINE_PREFIX = re.compile(r"^(vllm|sglang|llamacpp|atlas)-")
BUILD_TOKENS = {
    "bf16", "fp8", "nvfp4", "int4", "mxfp4", "awq", "gptq",
    "q8", "q4", "iq2m", "iq1m", "q3ks", "dflash", "dspark", "mtp",
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
