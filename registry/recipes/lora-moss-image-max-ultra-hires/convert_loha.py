"""Expand the publisher's mixed-rank LoHa into linear LoRA factors.

For matrices U1, V1, U2, V2, the LoHa delta is
    (U1 @ V1) * (U2 @ V2) * alpha / rank.
The Hadamard product is the sum over every (i,j) of
    outer(U1[:,i] * U2[:,j], V1[i,:] * V2[j,:]).
SGLang's MOSS pipeline loads linear LoRAs, so this algebraic rewrite
lets it run the original LoHa without a custom runtime or host weight cache.
Only the final BF16 storage rounds the result, as for an ordinary BF16 LoRA.
"""
import json
import mmap
import struct
import sys
from pathlib import Path

import numpy as np


def _float(raw, base, spec):
    start, end = spec["data_offsets"]
    shape = spec["shape"] or [1]
    kind = spec["dtype"]
    if kind == "BF16":
        bits = np.frombuffer(raw, np.uint16, count=(end - start) // 2, offset=base + start)
        return (bits.astype(np.uint32) << 16).view(np.float32).reshape(shape)
    if kind == "F16":
        return np.frombuffer(raw, np.float16, count=(end - start) // 2, offset=base + start).astype(np.float32).reshape(shape)
    if kind == "F32":
        return np.frombuffer(raw, np.float32, count=(end - start) // 4, offset=base + start).copy().reshape(shape)
    raise ValueError(f"Unsupported tensor dtype {kind}")


def _bf16(values):
    bits = np.ascontiguousarray(values, dtype=np.float32).view(np.uint32)
    return ((bits + 0x7FFF + ((bits >> 16) & 1)) >> 16).astype(np.uint16)


def convert(source, destination):
    with open(source, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        n = struct.unpack_from("<Q", mm)[0]
        specs = json.loads(mm[8:8 + n])
        specs.pop("__metadata__", None)
        base = 8 + n
        modules = sorted({key[:-len(".hada_w1_a")] for key in specs if key.endswith(".hada_w1_a")})
        if len(modules) != 120:
            raise ValueError(f"Expected 120 MOSS LoHa layers, got {len(modules)}")
        header = {}
        offset = 0
        layers = []
        for name in modules:
            prefix = name + "."
            required = {prefix + suffix for suffix in ("hada_w1_a", "hada_w1_b", "hada_w2_a", "hada_w2_b", "alpha")}
            if not required <= specs.keys():
                raise ValueError(f"Incomplete LoHa layer {name}")
            out_dim, rank = specs[prefix + "hada_w1_a"]["shape"]
            rank_b, in_dim = specs[prefix + "hada_w1_b"]["shape"]
            if rank != rank_b or rank not in (16, 32, 64):
                raise ValueError(f"Unexpected rank in {name}: {rank}, {rank_b}")
            if specs[prefix + "hada_w2_a"]["shape"] != [out_dim, rank] or specs[prefix + "hada_w2_b"]["shape"] != [rank, in_dim]:
                raise ValueError(f"Unequal LoHa factor shapes in {name}")
            model_name = name.removeprefix("diffusion_model.")
            # ComfyUI's MOSS transformer has one qkv projection. SGLang's
            # corresponding MOSS module separates q, k and v.
            targets = [model_name.replace(".proj.qkv", f".proj.{p}") for p in ("q", "k", "v")] \
                if model_name.endswith(".proj.qkv") else [model_name]
            if len(targets) == 3 and out_dim % 3:
                raise ValueError(f"Fused qkv is not divisible by three: {name}")
            out_part = out_dim // len(targets)
            for index, target_name in enumerate(targets):
                layers.append((name, index, rank, out_part))
                for suffix, shape in (("lora_A.weight", [rank * rank, in_dim]),
                                      ("lora_B.weight", [out_part, rank * rank])):
                    key = target_name + "." + suffix
                    size = 2 * int(np.prod(shape))
                    header[key] = {"dtype": "BF16", "shape": shape, "data_offsets": [offset, offset + size]}
                    offset += size
                header[target_name + ".alpha"] = {"dtype": "F32", "shape": [],
                                               "data_offsets": [offset, offset + 4]}
                offset += 4
        encoded = json.dumps(header, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 8)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "wb") as out:
            out.write(struct.pack("<Q", len(encoded)))
            out.write(encoded)
            for name, index, rank, out_part in layers:
                p = name + "."
                a1 = _float(mm, base, specs[p + "hada_w1_a"])
                b1 = _float(mm, base, specs[p + "hada_w1_b"])
                a2 = _float(mm, base, specs[p + "hada_w2_a"])
                b2 = _float(mm, base, specs[p + "hada_w2_b"])
                alpha = float(_float(mm, base, specs[p + "alpha"]).reshape(-1)[0])
                if not np.isfinite(alpha) or alpha <= 0:
                    raise ValueError(f"Invalid alpha in {name}: {alpha}")
                down = np.einsum("ix,jx->ijx", b1, b2).reshape(rank * rank, -1)
                up = np.einsum("oi,oj->oij", a1, a2).reshape(-1, rank * rank) * (alpha / rank)
                up = up[index * out_part:(index + 1) * out_part]
                out.write(_bf16(down).tobytes())
                out.write(_bf16(up).tobytes())
                out.write(struct.pack("<f", float(rank * rank)))
        print(f"Converted {len(modules)} LoHa layers to {len(layers)} linear LoRA layers ({offset} tensor bytes)")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
