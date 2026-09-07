#!/usr/bin/env python3
"""Select the reduced draft vocabulary from pre-computed corpus counts.

The selection, the special/added-token handling, the coverage report and the
output format are copied from MiaAI Lab's files/build_draft_vocab.py main();
only the counting was moved out (count_corpus.py) so it could be parallelised.
"""

import pickle, sys
from collections import Counter
from transformers import AutoTokenizer
import os

# Where the corpora, counts and vocabularies live. Set DRAFT_VOCAB_WORK to
# point these scripts at a different working directory.
WORK = os.environ.get("DRAFT_VOCAB_WORK", "/home/abel/draft-vocab-work")

SIZE = 65536
TOK_DIR = WORK + "/tok"
OUT = sys.argv[1]
CORPORA = sys.argv[2:]

tok = AutoTokenizer.from_pretrained(TOK_DIR, trust_remote_code=True)
vocab_size = len(tok)

counts: Counter = Counter()
docs = 0
for path in CORPORA:
    with open(path, "rb") as f:
        d = pickle.load(f)
    counts.update(d["counts"])
    docs += d["chunks"]
    print(f"  + {d['path']} x{d['repeat']}: {sum(d['counts'].values()):,} occurrences, "
          f"{len(d['counts']):,} distinct")

total = sum(counts.values())
if not total:
    sys.exit("ERROR: corpus produced no tokens")

special = set(tok.all_special_ids or [])
added = getattr(tok, "added_tokens_encoder", {}) or {}
special |= {int(i) for i in added.values()}
special = {i for i in special if 0 <= i < vocab_size}

ranked = [tid for tid, _ in counts.most_common()]
keep: list[int] = sorted(special)
seen = set(keep)
for tid in ranked:
    if len(keep) >= SIZE:
        break
    if tid not in seen:
        keep.append(tid)
        seen.add(tid)
keep = sorted(seen)

covered = sum(counts[t] for t in seen if t in counts)
print(f"corpus:      {docs} documents, {total:,} token occurrences, {len(counts):,} distinct ids")
print(f"vocabulary:  {vocab_size:,} -> {len(keep):,} ({100.0*len(keep)/vocab_size:.1f}%), "
      f"{len(special)} special/added kept unconditionally")
print(f"coverage:    {100.0*covered/total:.4f}% of corpus occurrences")
miss = total - covered
print(f"             {miss:,} occurrences ({100.0*miss/total:.4f}%) fall outside; "
      f"those become rejected drafts, never wrong output")
print("per-corpus coverage of the selected vocabulary:")
for path in CORPORA:
    d = pickle.load(open(path, "rb"))
    c = d["counts"]; t = sum(c.values())
    cov = sum(v for k, v in c.items() if k in seen)
    print(f"  {d['path']:<22} {100.0*cov/t:6.2f}%")

for cut in (8192, 16384, 32768, 65536, 131072):
    sub = set(sorted(special)) | set(ranked[:max(0, cut - len(special))])
    cov = sum(counts[t] for t in sub if t in counts)
    print(f"  size {cut:>7,}: coverage {100.0*cov/total:7.4f}%")

with open(OUT, "w") as h:
    h.write("".join(f"{t}\n" for t in keep))
print(f"wrote {len(keep):,} ids -> {OUT}")
