"""Minimal OpenAI-compatible server for DeepSeek DSpark speculative decoding.

DSpark has no vLLM/SGLang serving path yet (the only vLLM PR is DeepSeek-V4-only).
DeepSpec ships its drafter + a *benchmark* loop in plain HuggingFace transformers.
This server reuses DeepSpec's own Gemma4 DSpark evaluator primitives
(`build_models` + `generate_one_sample`, which drives `generate_decoding_sample`)
behind an OpenAI `/v1/chat/completions` + `/v1/models` API so the Spark AI Hub can
launch it like any other LLM recipe.

Single request at a time (DeepSpec's loop is bsz=1 by design). transformers
backend, sdpa attention -> modest throughput vs vLLM, but it genuinely runs DSpark.
"""
import os
import sys
import time
import uuid
import argparse
import threading
from types import SimpleNamespace

import torch
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from deepspec.eval.dspark import Gemma4DSparkEvaluator
from deepspec.eval.base_evaluator import resolve_stop_token_ids

# Model repos are passed as CLI flags (--model / --hf-repo-draft) so the Spark AI
# Hub installer's existing prefetch parser pulls both target and drafter into the
# huggingface-cache volume at install time (offline-first). Env vars are fallbacks.
_p = argparse.ArgumentParser()
_p.add_argument("--model", default=os.environ.get("DSPARK_TARGET", "google/gemma-4-12B-it"))
_p.add_argument("--hf-repo-draft", dest="draft",
                default=os.environ.get("DSPARK_DRAFT", "deepseek-ai/dspark_gemma4_12b_block7"))
_p.add_argument("--port", type=int, default=8000)
_cli, _ = _p.parse_known_args(sys.argv[1:])

TARGET = _cli.model
DRAFT = _cli.draft
SERVE_PORT = _cli.port
DEFAULT_MAX_TOKENS = int(os.environ.get("DSPARK_DEFAULT_MAX_TOKENS", "2048"))
# Drafters were trained in non-thinking mode; thinking mode lowers acceptance.
ENABLE_THINKING = os.environ.get("DSPARK_ENABLE_THINKING", "0") == "1"

print(f"[dspark] target={TARGET} draft={DRAFT}", flush=True)

args = SimpleNamespace(
    target_name_or_path=TARGET,
    draft_name_or_path=DRAFT,
    max_new_tokens=DEFAULT_MAX_TOKENS,
    temperature=1.0,
    confidence_threshold=0.0,
    tensorboard_dir=None,
    step=None,
    seed=980406,
    tasks=[],
)

# Construct the DeepSpec evaluator without its distributed init / dataset machinery.
ev = Gemma4DSparkEvaluator.__new__(Gemma4DSparkEvaluator)
ev.args = args
ev.device = torch.device("cuda:0")
ev.global_rank = 0
ev.world_size = 1
ev.tasks = []
ev.metrics_rows = []
ev.target_model, ev.draft_model, ev.tokenizer = ev.build_models()
ev.confidence_head_recorder = None

STOP_TOKEN_IDS = resolve_stop_token_ids(ev.target_model, ev.tokenizer)
_lock = threading.Lock()

app = FastAPI(title="DSpark Gemma 4 12B")


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": TARGET, "object": "model", "owned_by": "deepspec-dspark"}],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
def chat_completions(body: dict):
    messages = body.get("messages")
    if not messages:
        return JSONResponse(status_code=400, content={"error": "messages required"})
    max_tokens = int(body.get("max_tokens") or DEFAULT_MAX_TOKENS)
    temperature = float(body.get("temperature", 1.0))

    # transformers 5.x returns a BatchEncoding; pull out input_ids explicitly.
    enc = ev.tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        enable_thinking=ENABLE_THINKING,
        return_dict=True,
    )
    prompt_ids = enc["input_ids"].to(ev.device)
    prompt_len = prompt_ids.shape[1]

    with _lock:
        args.max_new_tokens = max_tokens
        # generate_decoding_sample divides logits by temperature; keep it positive.
        args.temperature = max(temperature, 1e-2)
        t0 = time.time()
        with torch.inference_mode():
            result = ev.generate_one_sample(
                input_ids=prompt_ids, stop_token_ids=STOP_TOKEN_IDS
            )
        dt = time.time() - t0

    gen_ids = result.output_ids[0, result.num_input_tokens:]
    text = ev.tokenizer.decode(gen_ids, skip_special_tokens=True)
    n_out = int(result.num_output_tokens)
    accepted = sum(result.acceptance_lengths)
    verifies = max(int(result.verify_count), 1)
    mean_accept = accepted / verifies if accepted else 0.0

    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": TARGET,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_len,
            "completion_tokens": n_out,
            "total_tokens": prompt_len + n_out,
        },
        "dspark": {
            "mean_accept_length": round(mean_accept, 3),
            "verify_count": int(result.verify_count),
            "tokens_per_second": round(n_out / dt, 2) if dt > 0 else None,
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=SERVE_PORT)
