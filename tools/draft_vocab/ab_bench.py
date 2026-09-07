#!/usr/bin/env python3
"""A/B measurement for the MTP draft-vocab change.

Reuses tools/benchmark_editing_column.py's prompts and call() verbatim so the
numbers are comparable with the published recipe.yaml figures, but writes JSON
to a file instead of mutating recipe.yaml. Repeats each workload N times.
"""

import json, statistics, sys, time
sys.path.insert(0, "/home/abel/sparkforge/tools")
import benchmark_editing_column as B
import os

# Where the corpora, counts and vocabularies live. Set DRAFT_VOCAB_WORK to
# point these scripts at a different working directory.
WORK = os.environ.get("DRAFT_VOCAB_WORK", "/home/abel/draft-vocab-work")

MODEL = "Mia-AiLab/Qwen3.8-Flash-Next-NVFP4"


def main():
    tag = sys.argv[1]
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    B.verify_serving_model(MODEL)
    B.call(MODEL, "hi", 32)  # warmup, discarded

    rows = []
    for r in range(repeats):
        print(f"--- repeat {r+1}/{repeats} ---", flush=True)
        rep = {}
        wtok = wt = 0
        for label, p in B.WRITING_PROMPTS:
            n, dt = B.call(MODEL, p)
            wtok += n; wt += dt
            rep[label] = n / dt
            print(f"    {label:<10} {n} tok / {dt:.2f}s = {n/dt:.2f} tok/s", flush=True)
        rep["writing"] = wtok / wt
        n, dt = B.call(MODEL, B.EDIT_PROMPT, 3000, thinking=False)
        if n < 50:
            raise RuntimeError(f"degenerate code-edit response: {n} tokens")
        rep["code-edit"] = n / dt
        print(f"    {'code-edit':<10} {n} tok / {dt:.2f}s = {n/dt:.2f} tok/s", flush=True)
        print(f"    writing(aggregate) = {rep['writing']:.2f} tok/s", flush=True)
        rows.append(rep)

    summary = {k: round(statistics.mean(r[k] for r in rows), 2)
               for k in ("prose", "explainer", "code", "writing", "code-edit")}
    out = {"tag": tag, "repeats": repeats, "rows": rows, "mean": summary,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = fWORK + "/bench-{tag}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print("\nMEAN:", json.dumps(summary), flush=True)
    print("wrote", path)


if __name__ == "__main__":
    main()
