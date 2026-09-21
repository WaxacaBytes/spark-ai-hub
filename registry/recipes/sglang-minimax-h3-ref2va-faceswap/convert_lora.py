#!/usr/bin/env python3
"""Rewrite a ComfyUI-keyed MiniMax-H3 LoRA into the minimax-h3-diffusers layout
SGLang reads.

SGLang's LoRA loader matches module paths on the diffusers graph
(``transformer_blocks.N.attn.to_q``); ComfyUI-trained adapters name the fused
graph instead (``diffusion_model.blocks.N.attn.qkv_proj``). SGLang's format
adapter classifies the latter as STANDARD and passes it through untouched, so
it loads and matches nothing -- a silent no-op, not an error. Hence this.

Everything here is a rename, a slice or a row swap. No values are recomputed,
so the conversion is exact and works on the raw bf16 bytes: tensors are carried
as uint16 and never interpreted as floats.

Three structural changes, each verified rather than assumed:

  qkv_proj -> to_q / to_k / to_v
      Two layouts exist in the wild. lightx2v's converted adapters concatenate
      three rank-r A blocks and store a BLOCK-DIAGONAL B, so each projection
      takes its own diagonal block. Adapters trained directly in ComfyUI (the
      ai-toolkit ones) share a single A across q/k/v with a dense B, so A is
      reused and only B's rows are split. Which one is in front of us is
      decided by testing the off-diagonal blocks for zeros, not by guessing.

  mlp.fc1 -> ff.net.0.proj
      A SwiGLU projection whose output is two stacked halves. lightx2v's own
      metadata states the mapping: "Diffusers [value;gate] -> ComfyUI
      [gate;value]". Going back therefore swaps the halves of B's rows.

  mlp.fc2 -> ff.net.2, attn.out_proj -> attn.to_out.0, and the block prefixes.
"""
import json, re, struct, sys
from pathlib import Path
import numpy as np

QKV_SPLIT = 3


def read(path):
    raw = Path(path).read_bytes()
    n = struct.unpack("<Q", raw[:8])[0]
    head = json.loads(raw[8:8 + n])
    meta = head.pop("__metadata__", None)
    base = 8 + n
    out = {}
    for k, v in head.items():
        s, e = v["data_offsets"]
        buf = raw[base + s: base + e]
        # bf16/f16 are carried as uint16: slicing never needs their values.
        width = {"BF16": 2, "F16": 2, "F32": 4}[v["dtype"]]
        dt = np.uint16 if width == 2 else np.uint32
        out[k] = (np.frombuffer(buf, dtype=dt).reshape(v["shape"] or [1]), v["dtype"])
    return out, meta


def write(path, tensors, meta):
    head, blobs, off = {}, [], 0
    for k in sorted(tensors):
        arr, dtype = tensors[k]
        b = arr.tobytes()
        head[k] = {"dtype": dtype, "shape": list(arr.shape),
                   "data_offsets": [off, off + len(b)]}
        blobs.append(b); off += len(b)
    if meta:
        head["__metadata__"] = meta
    hb = json.dumps(head).encode()
    pad = (-len(hb)) % 8
    hb += b" " * pad
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(hb))); f.write(hb)
        for b in blobs: f.write(b)


def rename(key):
    """ComfyUI module path -> diffusers module path (without the leaf)."""
    k = key
    if k.startswith("diffusion_model.token_refiner.blocks."):
        k = k.replace("diffusion_model.token_refiner.blocks.",
                      "token_refiner.refiner_blocks.", 1)
    elif k.startswith("diffusion_model.blocks."):
        k = k.replace("diffusion_model.blocks.", "transformer_blocks.", 1)
    else:
        return None
    k = k.replace(".attn.out_proj.", ".attn.to_out.0.")
    k = k.replace(".mlp.fc1.", ".ff.net.0.proj.")
    k = k.replace(".mlp.fc2.", ".ff.net.2.")
    return k


def leaf(k):
    return (k.replace(".lora_A.weight", ".lora_A.default.weight")
             .replace(".lora_B.weight", ".lora_B.default.weight"))


def is_block_diagonal(b):
    """B is [3*out, 3*r] with the off-diagonal blocks all zero?"""
    rows, cols = b.shape
    if rows % QKV_SPLIT or cols % QKV_SPLIT:
        return False
    o, r = rows // QKV_SPLIT, cols // QKV_SPLIT
    for i in range(QKV_SPLIT):
        for j in range(QKV_SPLIT):
            if i == j:
                continue
            # uint16 view: any non-zero bit pattern except -0.0 (0x8000)
            blk = b[i * o:(i + 1) * o, j * r:(j + 1) * r]
            if np.any((blk != 0) & (blk != 0x8000)):
                return False
    return True


def convert(src, dst):
    tensors, _ = read(src)
    out, stats = {}, {"qkv_blockdiag": 0, "qkv_shared_a": 0, "swiglu": 0,
                      "copied": 0, "alpha_dropped": 0}
    for key, (arr, dtype) in tensors.items():
        if key.endswith(".alpha"):
            stats["alpha_dropped"] += 1
            continue
        new = rename(key)
        if new is None:
            sys.exit(f"unmapped key: {key}")
        if ".attn.qkv_proj." in key:
            continue  # handled as a trio below
        if new.endswith(".ff.net.0.proj.lora_B.weight"):
            half = arr.shape[0] // 2
            arr = np.concatenate([arr[half:], arr[:half]], axis=0)
            stats["swiglu"] += 1
        else:
            stats["copied"] += 1
        out[leaf(new)] = (arr, dtype)

    # qkv_proj -> to_q / to_k / to_v
    for key in [k for k in tensors if k.endswith(".attn.qkv_proj.lora_A.weight")]:
        bkey = key.replace(".lora_A.", ".lora_B.")
        a, adt = tensors[key]
        b, bdt = tensors[bkey]
        stem = rename(key).replace(".attn.qkv_proj.lora_A.weight", ".attn.")
        out_dim = b.shape[0] // QKV_SPLIT
        if a.shape[0] == b.shape[1]:
            if a.shape[0] % QKV_SPLIT == 0 and is_block_diagonal(b):
                r = a.shape[0] // QKV_SPLIT
                for i, p in enumerate(("to_q", "to_k", "to_v")):
                    out[leaf(f"{stem}{p}.lora_A.weight")] = (a[i * r:(i + 1) * r], adt)
                    out[leaf(f"{stem}{p}.lora_B.weight")] = (
                        b[i * out_dim:(i + 1) * out_dim, i * r:(i + 1) * r], bdt)
                stats["qkv_blockdiag"] += 1
            else:
                for i, p in enumerate(("to_q", "to_k", "to_v")):
                    out[leaf(f"{stem}{p}.lora_A.weight")] = (a, adt)
                    out[leaf(f"{stem}{p}.lora_B.weight")] = (
                        b[i * out_dim:(i + 1) * out_dim], bdt)
                stats["qkv_shared_a"] += 1
        else:
            sys.exit(f"unrecognised qkv layout: A{list(a.shape)} B{list(b.shape)}")

    write(dst, out, {"key_format": "minimax-h3-diffusers",
                     "converted_from": Path(src).name})
    return out, stats


if __name__ == "__main__":
    o, s = convert(sys.argv[1], sys.argv[2])
    print(f"wrote {len(o)} tensors -> {sys.argv[2]}")
    print("  ", s)
