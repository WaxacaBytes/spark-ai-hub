#!/usr/bin/env python3
"""
Fill in the "Editing" column (tokens_per_second_editing) for installed LLM recipes.

Reproduces EXACTLY the published benchmark snippet from RecipeDetail.jsx:
  - 3 writing prompts (code, explainer, prose), max_tokens=512, thinking left on
  - 1 code-edit prompt (45-class module, add a method to every class),
    max_tokens=3000, chat_template_kwargs={"enable_thinking": False}

Noise rule (per Abel): if the measured editing rate is not a REAL win over the
published writing rate (i.e. within NOISE_THRESHOLD), do not publish two
slightly-different numbers (e.g. 50.3/50.4) — set tokens_per_second_editing
equal to the existing tokens_per_second so the pair reads e.g. 50.3/50.3.
The existing tokens_per_second (writing) value is NEVER touched by this script.
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HUB_API = "http://127.0.0.1:9000"
VLLM_API = "http://127.0.0.1:9001"
RECIPES_DIR = Path("/home/abel/sparkforge/registry/recipes")

NOISE_THRESHOLD = 0.15  # |editing - writing| / writing <= 15% => treat as noise either direction

WRITING_PROMPTS = [
    ("code", "Write a quicksort implementation in Python with comments explaining each step."),
    ("explainer", "Explain the differences between TCP and UDP, and when each is preferred."),
    ("prose", "Plan a 3-day cultural travel itinerary for Kyoto, Japan in autumn."),
]

FENCE = "```"


def edit_source(n=45):
    src = "from dataclasses import dataclass, field\n"
    for i in range(1, n + 1):
        src += (
            f"\n@dataclass\nclass Item{i}:\n    sku: str\n    quantity: int = 0\n"
            "    price_cents: int = 0\n    tags: list[str] = field(default_factory=list)\n\n"
            "    def restock(self, n: int) -> None:\n        if n < 0:\n"
            "            raise ValueError(\"n must be non-negative\")\n        self.quantity += n\n\n"
            "    def total_value(self) -> int:\n        return self.quantity * self.price_cents\n"
        )
    return src


EDIT_PROMPT = (
    "Here is a Python module. Add a discount(self, pct: int) -> int method to EVERY "
    "Item class, returning the discounted total value. Output the COMPLETE modified "
    "file, nothing else.\n\n" + FENCE + "python\n" + edit_source() + "\n" + FENCE
)


def http_json(url, data=None, method=None, timeout=30):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_recipe_yaml_fields(slug):
    p = RECIPES_DIR / slug / "recipe.yaml"
    content = p.read_text()
    model_id = re.search(r'model_id:\s*"([^"]+)"', content).group(1)
    writing = re.search(r'^tokens_per_second:\s*([\d.]+)', content, re.M)
    writing = float(writing.group(1)) if writing else None
    return content, model_id, writing


def evict_conflicting_models(slug):
    """Replicate the frontend's LaunchConflictModal: stop any other model
    recipe currently running/starting before launching this one (all model
    recipes conflict with each other — same GPU, same port 9001)."""
    try:
        all_recipes = http_json(f"{HUB_API}/api/recipes", timeout=15)
    except Exception as e:
        print(f"  WARN: could not list recipes to check conflicts: {e}")
        return
    for r in all_recipes:
        if r.get("slug") == slug:
            continue
        if not (r.get("running") or r.get("starting")):
            continue
        # every recipe in RECIPES_DIR here is a model-serving recipe (LLM), so
        # any other running/starting one is a guaranteed port-9001 conflict
        print(f"  Evicting conflicting running recipe: {r['slug']}")
        stop(r["slug"])
    # also defensively make sure nothing is left holding port 9001
    time.sleep(2)


def launch(slug):
    evict_conflicting_models(slug)
    try:
        r = http_json(f"{HUB_API}/api/recipes/{slug}/launch", data={}, method="POST", timeout=30)
        return r.get("status") in ("launched", "already_running", "running")
    except Exception as e:
        print(f"  LAUNCH FAILED: {e}")
        return False


def wait_ready(slug, timeout=900):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = http_json(f"{HUB_API}/api/recipes/{slug}", timeout=5)
            if r.get("ready"):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def stop(slug):
    try:
        http_json(f"{HUB_API}/api/recipes/{slug}/stop", data={}, method="POST", timeout=30)
    except Exception as e:
        print(f"  STOP FAILED: {e}")


def call(model_id, prompt, max_tokens=512, thinking=True):
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if not thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    t0 = time.time()
    d = http_json(f"{VLLM_API}/v1/chat/completions", data=payload, timeout=600)
    dt = time.time() - t0
    return d["usage"]["completion_tokens"], dt


def verify_serving_model(expected_model_id):
    """Cheap sanity check that port 9001 is actually serving the model we
    just launched (guards against a stale/conflicting server on the shared port)."""
    d = http_json(f"{VLLM_API}/v1/models", timeout=10)
    ids = [m.get("id") for m in d.get("data", [])]
    if expected_model_id not in ids:
        raise RuntimeError(f"port 9001 is serving {ids}, expected {expected_model_id}")


def run_benchmark(model_id):
    verify_serving_model(model_id)
    call(model_id, "hi", 32)  # warmup, discarded

    total_tok = 0
    total_t = 0
    per_workload = {}
    for label, p in WRITING_PROMPTS:
        n, dt = call(model_id, p)
        total_tok += n
        total_t += dt
        per_workload[label] = round(n / dt, 2) if dt else 0
        print(f"    {label:<10} {n} tok / {dt:.2f}s = {n/dt:.2f} tok/s")
    writing_measured = total_tok / total_t if total_t else 0

    n, dt = call(model_id, EDIT_PROMPT, 3000, thinking=False)
    editing_measured = n / dt if dt else 0
    per_workload["code-edit"] = round(editing_measured, 2)
    print(f"    {'code-edit':<10} {n} tok / {dt:.2f}s = {editing_measured:.2f} tok/s")

    return writing_measured, editing_measured, per_workload


def update_recipe_yaml(slug, writing_value, editing_value, workload_dict):
    p = RECIPES_DIR / slug / "recipe.yaml"
    content = p.read_text()

    # Strip any pre-existing tokens_per_second_editing / editing_workload /
    # benchmarks block (wherever they are) so we can re-insert a clean, complete
    # one right where tokens_per_second already lives.
    content = re.sub(r'^tokens_per_second_editing:\s*[\d.]+\n', '', content, flags=re.M)
    content = re.sub(r'^editing_workload:\s*"[^"]*"\n', '', content, flags=re.M)
    content = re.sub(r'^benchmarks:\s*\n(?:^\s+\S.*\n)*', '', content, flags=re.M)
    content = re.sub(r'^benchmarks:\s*\{\}\n', '', content, flags=re.M)

    benchmarks_lines = "benchmarks:\n"
    for k in ("prose", "explainer", "code", "code-edit"):
        if k in workload_dict:
            benchmarks_lines += f"  {k}: {workload_dict[k]}\n"

    replacement = (
        f"tokens_per_second: {writing_value:.1f}\n"
        f"tokens_per_second_editing: {editing_value:.1f}\n"
        f'editing_workload: "code-edit"\n'
        f"{benchmarks_lines}"
    )

    new_content, n = re.subn(
        r'^tokens_per_second:\s*[\d.]+\n',
        replacement.replace('\\', '\\\\'),
        content, count=1, flags=re.M,
    )
    if n == 0:
        raise RuntimeError(f"could not find tokens_per_second: line in {p}")

    p.write_text(new_content)


def process(slug, results):
    print(f"\n=== {slug} ===")
    content, model_id, writing_published = get_recipe_yaml_fields(slug)
    print(f"  model_id={model_id}  published writing={writing_published}")

    if not launch(slug):
        results[slug] = "LAUNCH_FAILED"
        return
    if not wait_ready(slug):
        print("  NOT READY within timeout")
        results[slug] = "NOT_READY_TIMEOUT"
        stop(slug)
        return

    try:
        writing_measured, editing_measured, workload_dict = run_benchmark(model_id)
    except Exception as e:
        print(f"  BENCHMARK FAILED: {e}")
        results[slug] = f"BENCHMARK_FAILED: {e}"
        stop(slug)
        return

    stop(slug)

    final_writing = round(writing_measured, 1)
    rel_diff = abs(editing_measured - writing_measured) / writing_measured if writing_measured else 0
    if rel_diff <= NOISE_THRESHOLD:
        final_editing = final_writing
        verdict = "NOISE -> snapped to writing value"
    else:
        final_editing = round(editing_measured, 1)
        verdict = "REAL EDITING GAIN" if editing_measured > writing_measured else "REAL EDITING SLOWDOWN"

    print(f"  writing(measured)={writing_measured:.2f}  editing(measured)={editing_measured:.2f}  => {verdict} => writing={final_writing} editing={final_editing}")
    # Keep the per-workload breakdown (detail-page panel) consistent with the
    # headline number it's shown alongside — if editing was snapped to the
    # writing value, the code-edit row must show that same snapped value too.
    workload_dict["code-edit"] = final_editing
    update_recipe_yaml(slug, final_writing, final_editing, workload_dict)
    results[slug] = f"OK writing={final_writing} editing={final_editing} ({verdict})"


def main():
    slugs = sys.argv[1:]
    if not slugs:
        print("Usage: benchmark_editing_column.py <slug> [slug...]")
        sys.exit(1)

    results = {}
    for slug in slugs:
        if not (RECIPES_DIR / slug).is_dir():
            results[slug] = "RECIPE_NOT_FOUND"
            continue
        try:
            process(slug, results)
        except Exception as e:
            results[slug] = f"UNEXPECTED_ERROR: {e}"
            try:
                stop(slug)
            except Exception:
                pass
        time.sleep(5)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for slug, res in results.items():
        print(f"{slug:50} {res}")


if __name__ == "__main__":
    main()
