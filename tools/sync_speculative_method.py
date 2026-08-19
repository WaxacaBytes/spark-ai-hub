#!/usr/bin/env python3
"""
Ground-truth the `speculative_method` field on every LLM recipe by parsing its
docker-compose.yml command — not tags, not the slug, not the name. Those have
all been wrong (a slug named "dspark" whose active --speculative-algorithm is
actually EAGLE; drafters running with no matching tag at all).

Real YAML parsing means commented-out alternative configs never leak in: a
line like `# - --speculative-algorithm` is not part of the parsed `command`
list at all, so it can't be confused with what's actually active.
"""

import glob
import json
import os
import re

import yaml

RECIPES_DIR = "registry/recipes"

METHOD_NORMALIZE = {
    "qwen3_5_mtp": "mtp",
}


def detect_speculative_method(compose_path):
    with open(compose_path) as f:
        data = yaml.safe_load(f)
    services = (data or {}).get("services", {})
    if not services:
        return None
    svc = next(iter(services.values()))
    cmd = [str(x) for x in (svc.get("command") or [])]

    for i, tok in enumerate(cmd):
        if tok == "--speculative-config" and i + 1 < len(cmd):
            try:
                cfg = json.loads(cmd[i + 1])
            except Exception:
                return None
            method = cfg.get("method")
            if method:
                return METHOD_NORMALIZE.get(method, method)
            # No explicit method, but a draft model path names the drafter
            # (e.g. Laguna's poolside/Laguna-S-2.1-DFlash-NVFP4).
            model = cfg.get("model") or ""
            if "dflash" in model.lower():
                return "dflash"
            return None
        if tok == "--speculative-algorithm" and i + 1 < len(cmd):
            return cmd[i + 1].lower()
        if tok == "--spec-type" and i + 1 < len(cmd):
            # llama.cpp's own flag, e.g. "draft-mtp" for self-speculative
            # decoding off the model's own next-token head.
            val = cmd[i + 1].lower()
            return "mtp" if "mtp" in val else val
    return None


def update_recipe_yaml(slug, method):
    p = os.path.join(RECIPES_DIR, slug, "recipe.yaml")
    with open(p) as f:
        content = f.read()

    content = re.sub(r'^speculative_method:\s*"[^"]*"\n', "", content, flags=re.M)

    value = method or ""
    insertion = f'speculative_method: "{value}"\n'

    new_content, n = re.subn(
        r"^quantization:\s*\"[^\"]*\"\n",
        lambda m: m.group(0) + insertion,
        content, count=1, flags=re.M,
    )
    if n == 0:
        return False  # not an LLM recipe (no quantization field) -> nothing to do

    if new_content != content:
        with open(p, "w") as f:
            f.write(new_content)
        return True
    return False


def main():
    changed = []
    for compose_path in sorted(glob.glob(f"{RECIPES_DIR}/*/docker-compose.yml")):
        slug = os.path.basename(os.path.dirname(compose_path))
        recipe_yaml = os.path.join(RECIPES_DIR, slug, "recipe.yaml")
        if not os.path.isfile(recipe_yaml):
            continue
        try:
            method = detect_speculative_method(compose_path)
        except Exception as e:
            print(f"{slug:45} PARSE_ERROR: {e}")
            continue
        if update_recipe_yaml(slug, method):
            changed.append((slug, method))
            print(f"{slug:45} -> speculative_method={method!r}")

    print(f"\n{len(changed)} recipe.yaml files updated")


if __name__ == "__main__":
    main()
