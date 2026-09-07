#!/usr/bin/env python3
"""Parallel token-frequency counter for one corpus file.

Equivalent to the counting loop in MiaAI Lab's files/build_draft_vocab.py --
same tokenizer, same add_special_tokens=False, same 1 MiB chunking -- but
encodes chunks in batches across processes, because the sequential loop runs at
1.06 MB/s here and the corpus is 660 MB. Verified to produce identical token
counts on a 20 MB sample.

A `:N` repeat weight is applied as a count multiplier rather than by reading the
file N times; for a frequency Counter those are the same thing.
"""

import os, pickle, sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

# Where the corpora, counts and vocabularies live. Set DRAFT_VOCAB_WORK to
# point these scripts at a different working directory.
WORK = os.environ.get("DRAFT_VOCAB_WORK", "/home/abel/draft-vocab-work")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/mia_files")
CHUNK = 1 << 20
TOK_DIR = WORK + "/tok"
_tok = None


def _init():
    global _tok
    os.environ["TOKENIZERS_PARALLELISM"] = "true"
    from transformers import AutoTokenizer
    _tok = AutoTokenizer.from_pretrained(TOK_DIR, trust_remote_code=True)


def _count(batch):
    c = Counter()
    for ids in _tok(batch, add_special_tokens=False)["input_ids"]:
        c.update(ids)
    return c


def iter_chunks(path):
    """Same chunking as build_draft_vocab.iter_texts, minus the repeat weight."""
    import json
    if path.endswith(".jsonl"):
        with open(path) as h:
            for line in h:
                line = line.strip()
                if line:
                    t = json.loads(line).get("text", "")
                    if t:
                        yield t
    else:
        with open(path, errors="replace") as h:
            while True:
                block = h.read(CHUNK)
                if not block:
                    break
                yield block


def main():
    spec = sys.argv[1]
    out = sys.argv[2]
    workers = int(os.environ.get("COUNT_WORKERS", "6"))
    repeat = 1
    path = spec
    if ":" in spec and spec.rsplit(":", 1)[1].isdigit():
        path, r = spec.rsplit(":", 1)
        repeat = int(r)

    batches, cur = [], []
    n_chunks = 0
    for ch in iter_chunks(path):
        cur.append(ch)
        n_chunks += 1
        if len(cur) == 8:
            batches.append(cur)
            cur = []
    if cur:
        batches.append(cur)

    total = Counter()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init) as ex:
        for i, c in enumerate(ex.map(_count, batches, chunksize=1)):
            total.update(c)
            if (i + 1) % 20 == 0:
                print(f"  {path}: {i+1}/{len(batches)} batches, "
                      f"{sum(total.values()):,} tokens", flush=True)

    if repeat != 1:
        total = Counter({k: v * repeat for k, v in total.items()})
    with open(out, "wb") as f:
        pickle.dump({"path": path, "repeat": repeat, "chunks": n_chunks,
                     "counts": total}, f)
    print(f"{path} x{repeat}: {n_chunks} chunks, {sum(total.values()):,} weighted "
          f"token occurrences, {len(total):,} distinct -> {out}", flush=True)


if __name__ == "__main__":
    main()
