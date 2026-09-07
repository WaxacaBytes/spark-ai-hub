#!/usr/bin/env python3
"""Generate the model's own output as a corpus for the draft vocabulary.

The drafter has to predict what THIS model emits, so the corpus that matters is
its own output distribution -- including the chain of thought, which is most of
what it emits and is drafted exactly like the visible answer. Both
reasoning_content and content are captured.
"""

import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import os

# Where the corpora, counts and vocabularies live. Set DRAFT_VOCAB_WORK to
# point these scripts at a different working directory.
WORK = os.environ.get("DRAFT_VOCAB_WORK", "/home/abel/draft-vocab-work")

VLLM = "http://127.0.0.1:9001/v1/chat/completions"
MODEL = "Mia-AiLab/Qwen3.8-Flash-Next-NVFP4"
OUT = WORK + "/model_out.jsonl"

TOPICS = [
    "the Kyoto tea ceremony", "submarine fibre-optic cables", "sourdough fermentation",
    "the Bauhaus movement", "coral reef restoration", "the history of the astrolabe",
    "monetary policy and interest rates", "Antarctic field logistics", "birdsong dialects",
    "the Silk Road spice trade", "urban heat islands", "the physics of curveballs",
    "medieval cathedral acoustics", "desalination plants", "the Voyager probes",
    "Japanese joinery", "vaccine cold chains", "the Dutch tulip mania",
    "glacier mass balance", "the invention of the shipping container",
]
CODE_TASKS = [
    "a rate limiter with a token bucket", "an LRU cache with TTL eviction",
    "a CSV to Parquet converter", "a retrying HTTP client with backoff",
    "a binary search tree with deletion", "a WebSocket chat server",
    "a Markdown table formatter", "a topological sort with cycle detection",
    "a thread-safe connection pool", "a diff algorithm on lines",
    "a priority queue scheduler", "a JSON schema validator",
    "a trie for prefix search", "a circular buffer for audio samples",
    "a memoising decorator with a size cap",
]
EXPLAIN = [
    "how TLS certificate chains are verified", "why B-trees suit disk storage",
    "how garbage collection generations work", "what causes TCP head-of-line blocking",
    "how CUDA streams overlap compute and copy", "why floating point addition is not associative",
    "how consistent hashing handles node loss", "what a memory barrier does",
    "how columnar formats speed up scans", "why quantisation lowers model bandwidth",
]
MATH = [
    "A train leaves at 09:14 travelling 82 km/h; another leaves the same station at 10:02 travelling 104 km/h. When does the second catch the first?",
    "Find all integer solutions to 7x + 11y = 100 with x, y >= 0.",
    "A bag has 5 red, 3 blue and 2 green marbles. Three are drawn without replacement. What is the probability exactly two are red?",
    "Compute the area between y = x^2 and y = 3x - 2.",
    "A 12% solution is mixed with a 30% solution to make 6 L of 20%. How much of each?",
    "Prove that the sum of the first n odd numbers is n^2.",
    "A loan of 24,000 at 6.4% annual interest compounded monthly is repaid over 5 years. What is the monthly payment?",
    "How many distinct arrangements of MISSISSIPPI have no two S adjacent?",
]

def prompts():
    out = []
    for t in TOPICS:
        out.append(f"Write four detailed paragraphs about {t}.")
        out.append(f"Explain {t} to a curious 12-year-old, then to a specialist.")
    for c in CODE_TASKS:
        out.append(f"Write {c} in Python, with docstrings and a short test.")
        out.append(f"Implement {c} in TypeScript and explain the trade-offs.")
    for e in EXPLAIN:
        out.append(f"Explain in depth: {e}")
    for m in MATH:
        out.append(m)
    return out

def one(p):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": p}],
        "max_tokens": 900,
        "temperature": 0.8,
        "top_p": 0.95,
    }).encode()
    req = urllib.request.Request(VLLM, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.loads(r.read().decode())
        msg = d["choices"][0]["message"]
        # vLLM 0.25+ renamed reasoning_content -> reasoning; this build
        # emits `reasoning`, and a generation cut off mid-CoT has ONLY
        # that field. Reading just reasoning_content dropped 55 of 88.
        text = ((msg.get("reasoning") or msg.get("reasoning_content") or "")
                + "\n" + (msg.get("content") or ""))
        return text, d["usage"]["completion_tokens"]
    except Exception as e:
        print("  ERR", e, flush=True)
        return "", 0

def main():
    ps = prompts()[:96]
    print(f"{len(ps)} prompts, 4 concurrent", flush=True)
    t0 = time.time()
    done = tot = 0
    from concurrent.futures import as_completed
    with open(OUT, "w") as f, ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(one, p) for p in ps]
        for fut in as_completed(futs):
            text, n = fut.result()
            done += 1
            tot += n
            if text.strip():
                f.write(json.dumps({"text": text}) + "\n")
                f.flush()
            if done % 10 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(ps)}  {tot:,} tokens  {tot/el:.1f} tok/s agg  {el/60:.1f} min",
                      flush=True)
    print(f"done: {done} generations, {tot:,} tokens in {(time.time()-t0)/60:.1f} min")

if __name__ == "__main__":
    main()
