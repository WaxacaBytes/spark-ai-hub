#!/usr/bin/env python3
"""Chinese long-form decode: the coverage stress case for an English+code vocab."""

import json, statistics, sys, time, urllib.request
import os

# Where the corpora, counts and vocabularies live. Set DRAFT_VOCAB_WORK to
# point these scripts at a different working directory.
WORK = os.environ.get("DRAFT_VOCAB_WORK", "/home/abel/draft-vocab-work")
M = "Mia-AiLab/Qwen3.8-Flash-Next-NVFP4"
PROMPTS = [
    "请用中文详细说明宋代科举制度的运作方式及其社会影响，分四段。",
    "用中文解释什么是量子纠缠，并说明它为什么不能用来超光速通信。",
    "用中文写一篇关于城市热岛效应成因与缓解措施的说明文。",
]
def call(p, mt=600):
    body = {"model": M, "messages": [{"role": "user", "content": p}],
            "max_tokens": mt, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False, "thinking": False}}
    r = urllib.request.Request("http://127.0.0.1:9001/v1/chat/completions",
                               data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.loads(urllib.request.urlopen(r, timeout=900).read().decode())
    return d["usage"]["completion_tokens"] / (time.time() - t0)
tag = sys.argv[1]
call("你好", 16)
rates = []
for p in PROMPTS:
    for _ in range(2):
        v = call(p); rates.append(v); print(f"    {v:.2f} tok/s", flush=True)
mean = statistics.mean(rates)
json.dump({"tag": tag, "rates": rates, "mean": mean},
          open(fWORK + "/zh-{tag}.json", "w"), indent=2)
print(f"MEAN zh {tag}: {mean:.2f} tok/s")
