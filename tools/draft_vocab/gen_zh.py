#!/usr/bin/env python3
"""Chinese model-output corpus, for coverage measurement and vocab v2."""

import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# Where the corpora, counts and vocabularies live. Set DRAFT_VOCAB_WORK to
# point these scripts at a different working directory.
WORK = os.environ.get("DRAFT_VOCAB_WORK", "/home/abel/draft-vocab-work")
M = "Mia-AiLab/Qwen3.8-Flash-Next-NVFP4"
TOPICS = ["宋代科举制度", "光合作用", "城市热岛效应", "丝绸之路的香料贸易", "量子纠缠",
          "中医的经络学说", "京都的茶道", "区块链共识算法", "黄河的治理history", "唐诗的格律",
          "青藏高原的形成", "人工智能的伦理问题", "台风的形成机制", "围棋的基本战术",
          "中国古代的造纸术", "全球供应链的脆弱性", "深海热液喷口的生态", "货币政策与通货膨胀"]
PROMPTS = ([f"请用中文详细介绍{t}，分成四段。" for t in TOPICS] +
           [f"用中文向一个高中生解释{t}，然后再向专家解释一遍。" for t in TOPICS])
def one(p):
    body = json.dumps({"model": M, "messages": [{"role": "user", "content": p}],
                       "max_tokens": 700, "temperature": 0.8, "top_p": 0.95}).encode()
    r = urllib.request.Request("http://127.0.0.1:9001/v1/chat/completions", data=body,
                               headers={"Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(r, timeout=900).read().decode())
        m = d["choices"][0]["message"]
        return ((m.get("reasoning") or m.get("reasoning_content") or "") + "\n" +
                (m.get("content") or ""), d["usage"]["completion_tokens"])
    except Exception as e:
        print("  ERR", e, flush=True); return "", 0
t0 = time.time(); done = tot = 0
with open("model_out_zh.jsonl", "w") as f, ThreadPoolExecutor(max_workers=4) as ex:
    for fut in as_completed([ex.submit(one, p) for p in PROMPTS]):
        text, n = fut.result(); done += 1; tot += n
        if text.strip():
            f.write(json.dumps({"text": text}) + "\n"); f.flush()
        if done % 6 == 0:
            print(f"  {done}/{len(PROMPTS)} {tot:,} tok {(time.time()-t0)/60:.1f} min", flush=True)
print(f"done: {done} generations, {tot:,} tokens, {(time.time()-t0)/60:.1f} min")
