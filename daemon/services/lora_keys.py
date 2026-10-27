"""Rewrite a LoRA file someone trained elsewhere into the layer names the Hub's
SGLang bases read, without torch: tensors are carried as raw numpy arrays.

What arrives from Civitai or Hugging Face comes in several layouts, and SGLang
loads a layout it does not recognise as a silent no-op (it matches nothing).
The rewrites here are the ones the installed LoRA recipes needed, each exact:

  * names: PEFT's `.default`, kohya's lora_down / lora_up, the
    `base_model.model.` wrapper -> plain `.lora_A` / `.lora_B`;
  * alpha: a per-layer `.alpha` is folded into that layer's B (scale
    alpha / rank), so the file runs at strength 1 with no alpha of its own;
  * Qwen-Image: ai-toolkit's fused `img_mlp.gate_up` = [gate_layer; proj] is
    split in two (both halves keep A, B is cut in half);
  * MOSS-Image: a fused `proj.qkv` is split into q, k, v the same way, and
    ComfyUI's `diffusion_model.` prefix is dropped;
  * MiniMax-H3: native / ComfyUI names (blocks.N.attn.qkv_proj, mlp.fc1, ...)
    -> the diffusers names the H3 LoRA recipes ship in, as their
    convert_lora.py does (registry/recipes/lora-minimax-h3-charswap).

Whether the result really fits is checked after it loads, against the layer
count SGLang reports (link_lora_service.check_coverage).
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import numpy as np

_WIDTH = {"BF16": (2, np.uint16), "F16": (2, np.uint16), "F32": (4, np.uint32)}


def read(path: Path) -> tuple[dict, dict]:
    """{name: (array, dtype)} and the metadata of a .safetensors file. Raises
    ValueError for anything that is not a LoRA in a float format."""
    raw = Path(path).read_bytes()
    if len(raw) < 8:
        raise ValueError("not a safetensors file")
    n = struct.unpack("<Q", raw[:8])[0]
    if n > len(raw) - 8 or n > 100 << 20:
        raise ValueError("not a safetensors file")
    head = json.loads(raw[8:8 + n])
    meta = head.pop("__metadata__", None) or {}
    out = {}
    for k, v in head.items():
        if v["dtype"] not in _WIDTH:
            raise ValueError(f"{k} is {v['dtype']}; a LoRA's tensors are BF16, F16 or F32")
        s, e = v["data_offsets"]
        out[k] = (np.frombuffer(raw[8 + n + s:8 + n + e], dtype=_WIDTH[v["dtype"]][1]).reshape(v["shape"] or [1]),
                  v["dtype"])
    return out, meta


def write(path: Path, tensors: dict, meta: dict) -> None:
    head, blobs, off = {}, [], 0
    for k in sorted(tensors):
        arr, dtype = tensors[k]
        b = np.ascontiguousarray(arr).tobytes()
        head[k] = {"dtype": dtype, "shape": list(arr.shape), "data_offsets": [off, off + len(b)]}
        blobs.append(b)
        off += len(b)
    if meta:
        head["__metadata__"] = {k: str(v) for k, v in meta.items()}
    hb = json.dumps(head).encode()
    hb += b" " * (-len(hb) % 8)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(hb)) + hb)
        for b in blobs:
            f.write(b)


def _f32(arr: np.ndarray, dtype: str) -> np.ndarray:
    if dtype == "BF16":
        return (arr.astype(np.uint32) << 16).view(np.float32)
    if dtype == "F16":
        return arr.view(np.float16).astype(np.float32)
    return arr.view(np.float32)


def _bf16(values: np.ndarray) -> np.ndarray:
    """float32 -> bf16 bits, rounded to nearest even."""
    u = values.astype(np.float32).view(np.uint32)
    return ((u + 0x7FFF + ((u >> 16) & 1)) >> 16).astype(np.uint16)


def modules(tensors: dict) -> set[str]:
    """The layers a LoRA touches: one per lora_A."""
    return {k[: -len(".lora_A.weight")] for k in tensors if k.endswith(".lora_A.weight")}


def _plain(tensors: dict) -> dict:
    """Common names, alpha folded into B, everything BF16."""
    pairs: dict[str, dict] = {}
    for key, value in tensors.items():
        k = key.removeprefix("base_model.model.")
        m = (re.match(r"^(.*)\.(lora_A|lora_B|lora_down|lora_up)(?:\.default)?\.weight$", k)
             or re.match(r"^(.*)\.(alpha)$", k))
        if not m:
            raise ValueError(f"{key} is not a LoRA weight (expected lora_A/lora_B or lora_down/lora_up)")
        part = {"lora_down": "A", "lora_up": "B", "lora_A": "A", "lora_B": "B", "alpha": "alpha"}[m[2]]
        pairs.setdefault(m[1], {})[part] = value
    out = {}
    for name, p in pairs.items():
        if "A" not in p or "B" not in p:
            raise ValueError(f"{name} has no matching lora_A / lora_B pair")
        (a, adt), (b, bdt) = p["A"], p["B"]
        a, b = _f32(a, adt), _f32(b, bdt)
        if a.ndim != 2 or b.ndim != 2:
            raise ValueError(f"{name} is a {a.ndim}-D LoRA layer; only linear layers are supported")
        if "alpha" in p:
            alpha = float(_f32(*p["alpha"]).reshape(-1)[0])
            b = b * (alpha / a.shape[0])
        out[f"{name}.lora_A.weight"] = (_bf16(a), "BF16")
        out[f"{name}.lora_B.weight"] = (_bf16(b), "BF16")
    return out


def _split(out: dict, fused: str, parts: list[str]) -> dict:
    """A fused layer -> its parts: A shared, B cut into equal row blocks."""
    result = {}
    for key, value in out.items():
        if f".{fused}." not in key:
            result[key] = value
            continue
        names = [key.replace(f".{fused}.", f".{p}.") for p in parts]
        arr, dtype = value
        if ".lora_A." in key:
            for name in names:
                result[name] = (arr.copy(), dtype)
        else:
            for name, chunk in zip(names, np.split(arr, len(parts), axis=0)):
                result[name] = (np.ascontiguousarray(chunk), dtype)
    return result


def _h3(out: dict) -> dict:
    """MiniMax-H3 native names -> diffusers names (convert_lora.py's mapping)."""
    result = {}
    for key, (arr, dtype) in out.items():
        k = key.removeprefix("diffusion_model.").removeprefix("model.diffusion_model.")
        if k.startswith(("transformer_blocks.", "token_refiner.refiner_blocks.")):
            result[k] = (arr, dtype)          # already diffusers names
            continue
        k = k.replace("token_refiner.blocks.", "token_refiner.refiner_blocks.", 1) if k.startswith("token_refiner.blocks.") \
            else k.replace("blocks.", "transformer_blocks.", 1)
        k = (k.replace(".attn.out_proj.", ".attn.to_out.0.").replace(".mlp.fc2.", ".ff.net.2."))
        if ".mlp.fc1." in k:
            k = k.replace(".mlp.fc1.", ".ff.net.0.proj.")
            if ".lora_B." in k:               # SwiGLU halves: [gate; value] -> [value; gate]
                half = arr.shape[0] // 2
                arr = np.concatenate([arr[half:], arr[:half]], axis=0)
        result[k] = (arr, dtype)
    # A fused qkv comes two ways: one A shared by q/k/v with a dense B (trained
    # in ComfyUI), or three A blocks stacked over a block-diagonal B
    # (lightx2v's exports), where each projection takes its own block.
    for a_key in [k for k in result if k.endswith(".attn.qkv_proj.lora_A.weight")]:
        b_key = a_key.replace(".lora_A.", ".lora_B.")
        (a, adt), (b, bdt) = result[a_key], result[b_key]
        r, o = a.shape[0] // 3, b.shape[0] // 3
        if a.shape[0] % 3 == 0 and all(
                not np.any((b[i * o:(i + 1) * o, j * r:(j + 1) * r] & 0x7FFF) != 0)
                for i in range(3) for j in range(3) if i != j):
            del result[a_key], result[b_key]
            stem = a_key[: -len("qkv_proj.lora_A.weight")]
            for i, p in enumerate(("to_q", "to_k", "to_v")):
                result[f"{stem}{p}.lora_A.weight"] = (np.ascontiguousarray(a[i * r:(i + 1) * r]), adt)
                result[f"{stem}{p}.lora_B.weight"] = (np.ascontiguousarray(b[i * o:(i + 1) * o, i * r:(i + 1) * r]), bdt)
    return _split(result, "attn.qkv_proj", ["attn.to_q", "attn.to_k", "attn.to_v"])


def convert(src: Path, dst: Path, family: str) -> int:
    """Rewrite `src` for a base of `family` (qwen-image, moss-image,
    minimax-h3, or anything else: names and alpha only) into `dst`.
    Returns how many layers the adapter touches."""
    tensors, meta = read(src)
    out = _plain(tensors)
    if family == "qwen-image":
        out = _split(out, "img_mlp.gate_up", ["img_mlp.gate_layer", "img_mlp.proj"])
    elif family == "moss-image":
        out = {k.removeprefix("diffusion_model."): v for k, v in out.items()}
        out = _split(out, "proj.qkv", ["proj.q", "proj.k", "proj.v"])
    elif family == "minimax-h3":
        out = _h3(out)
    write(dst, out, {"converted_for": family, **{k: v for k, v in meta.items() if len(str(v)) < 2000}})
    return len(modules(out))
