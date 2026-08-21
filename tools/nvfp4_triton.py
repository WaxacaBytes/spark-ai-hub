"""NVFP4 activation quantization for the DGX Spark.

torchao's fast NVFP4 path calls out to Meta's MSLK Triton kernel, which has no
aarch64 distribution (the PyPI `mslk` is an empty 0.0.0 placeholder). GB10 has
native FP4 tensor cores and `torch._scaled_mm` drives them fine, so the only
missing piece is the quantizer. This is it: one Triton kernel producing exactly
what torchao's pure-torch `nvfp4_quantize` produces, ~50x faster.
"""

import torch
import triton
import triton.language as tl

F4_E2M1_MAX = 6.0
F8E4M3_MAX = 448.0
E4M3_EPS = 2.0 ** -9

# Triton kernels may only close over constexpr globals.
_F4_MAX = tl.constexpr(6.0)
_F8_MAX = tl.constexpr(448.0)
_EPS = tl.constexpr(2.0 ** -9)


@triton.jit
def _nvfp4_quantize_kernel(
    x_ptr, scale_ptr, out_ptr, pts_ptr,
    M, N, n_groups,
    BLOCK_G: tl.constexpr,          # scale groups (of 16) handled per program
):
    row = tl.program_id(0)
    gblk = tl.program_id(1)

    g = gblk * BLOCK_G + tl.arange(0, BLOCK_G)          # group index within the row
    lane = tl.arange(0, 16)
    cols = g[:, None] * 16 + lane[None, :]
    mask = (cols < N) & (g[:, None] < n_groups)

    x = tl.load(x_ptr + row * N + cols, mask=mask, other=0.0).to(tl.float32)

    pts = tl.load(pts_ptr).to(tl.float32)
    max_abs = tl.max(tl.abs(x), axis=1)
    block_scale = max_abs / _F4_MAX
    scaled = block_scale / pts
    scaled = tl.minimum(tl.maximum(scaled, _EPS), _F8_MAX)
    s_fp8 = scaled.to(tl.float8e4nv)
    s_f32 = s_fp8.to(tl.float32)

    recip = (1.0 / pts) / s_f32
    v = x * recip[:, None]
    v = tl.minimum(tl.maximum(v, -_F4_MAX), _F4_MAX)

    # float32 -> e2m1 nibble, round-to-nearest-even on the ties at the midpoints.
    a = tl.abs(v)
    sign = tl.where(v < 0, 8, 0).to(tl.uint8)
    mag = tl.where(a <= 0.25, 0,
          tl.where(a < 0.75, 1,
          tl.where(a <= 1.25, 2,
          tl.where(a < 1.75, 3,
          tl.where(a <= 2.5, 4,
          tl.where(a < 3.5, 5,
          tl.where(a <= 5.0, 6, 7))))))).to(tl.uint8)
    nib = sign | mag

    # Pack two nibbles per byte, low nibble first (matches torchao's pack_uint4).
    lo = tl.reshape(nib, (BLOCK_G, 8, 2))
    packed = tl.sum(tl.where(tl.arange(0, 2)[None, None, :] == 0,
                             lo.to(tl.int32), lo.to(tl.int32) * 16), axis=2).to(tl.uint8)

    out_cols = g[:, None] * 8 + tl.arange(0, 8)[None, :]
    tl.store(out_ptr + row * (N // 2) + out_cols,
             packed, mask=(g[:, None] < n_groups))
    tl.store(scale_ptr + row * n_groups + g, s_fp8, mask=g < n_groups)


def nvfp4_quantize_triton(x: torch.Tensor, per_tensor_scale: torch.Tensor):
    """Returns (blockwise_scales_fp8 [M, N//16], packed_data_uint8 [M, N//2])."""
    assert x.is_contiguous() and x.shape[-1] % 16 == 0
    orig = x.shape
    x2 = x.reshape(-1, orig[-1])
    M, N = x2.shape
    n_groups = N // 16
    scale = torch.empty(M, n_groups, dtype=torch.float8_e4m3fn, device=x.device)
    out = torch.empty(M, N // 2, dtype=torch.uint8, device=x.device)
    BLOCK_G = 16
    grid = (M, triton.cdiv(n_groups, BLOCK_G))
    _nvfp4_quantize_kernel[grid](
        x2, scale, out, per_tensor_scale.reshape(()), M, N, n_groups, BLOCK_G=BLOCK_G,
        num_warps=4,
    )
    return scale, out
