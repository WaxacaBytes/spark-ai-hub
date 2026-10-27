# FastH3 Omni NVFP4 on one DGX Spark

The recipe uses the pinned FastVideo `fa2d1324` code and the pinned
`FastH3-Omni-NVFP4-QAD` checkpoint. The model weights are baked into its image.
`h3_sequential_load` releases the Qwen3-VL conditioner before loading the DiT
and VAEs. `MAX_JOBS=2` bounds FlashInfer's first-use FP4 JIT compilation on the
Spark's shared CPU/GPU memory. Unbounded compilation exhausted unified memory
on the first render. See the [FlashInfer DGX Spark issue](https://github.com/flashinfer-ai/flashinfer/issues/3634)
and [FastVideo's memory policy](https://github.com/hao-ai-lab/FastVideo/blob/fa2d1324c60050cd65209c6aaeac470e7fb412f5/docs/inference/fasth3_omniref.md).

## Spark verification (2026-10-22 local time)

Both renders were sent through the Hub's `generate_video`/`get_video` MCP tools,
with one image reference, 832×480, 124 frames at 24 fps, eight denoising
passes, seed 7. Prompt: a woman at a flower market smelling peonies and
speaking to camera. The generated MP4s had H.264 video, AAC audio, and 5.167 s
duration. They were byte-identical at the same seed.

| Request | Hub time | Output |
| --- | ---: | --- |
| First, including FP4 JIT and sequential model load | 783 s | `data/videos/749f2a3ba5a746bcb41b200192ceac6e.mp4` |
| Repeat in the same running container | 142 s | `data/videos/920d6ca2bbd32aff37dc3dea1624168d.mp4` |

The host memory guard recorded a minimum `MemAvailable` of 44.2 GiB across
both requests; it never intervened. The repeat request reloaded the conditioner
and completed, confirming the sequential path works after the first clip.

For a practical comparison, `sglang-minimax-h3-ref2va-int8-turbo` rendered the
same image, prompt, seed and requested 5 s length through the Hub in 683 s.
Its recipe produced 1344×768 at nine server steps; FastH3 produced 832×480 at
eight steps. The Ref2VA clip was 5.175 s with H.264 and AAC. FastH3's warm
request was 4.8× faster under these two recipes' default output settings, but
the resolutions differ, so this is not a same-pixel throughput comparison.
The comparison output is `data/videos/ccbc69ec95aa70525f4e355c66c44578.mp4`.

For offline-launch validation, the same image was started with Docker
`--network none`, `HF_HUB_OFFLINE=1`, and `TRANSFORMERS_OFFLINE=1`. Its local
`/health` endpoint returned HTTP 200 while external DNS resolution failed. The
temporary validation container was removed after the check.
