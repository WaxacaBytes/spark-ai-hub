# Qwen3.8-Flash-Next Flare on DGX Spark

This recipe uses the published ARM64 Flare runtime, pinned by digest, and Local
Inference Lab's prepared GB10 package, pinned to commit
`cd5b82260c6922d629b0d97d66d16bc4658ed2c2`. All weights and the embedding table
are baked into the installed image. There are no model/cache volumes, host model
caches, runtime downloads, or preparation steps for the user.

## Serving configuration

- Four active request slots, up to five MTP drafts, reported 262,144-token context.
- The 26.8 GiB embedding table is read from the image on disk; RAM mode with the
  original settings exceeded available memory. Reserve: `--memory-limit -4G`.
- Five drafts is the prepared package's maximum; requesting six was rejected.
- `seccomp-io-uring.json` is the Moby default policy downloaded 2026-10-22 with
  exactly one added allow rule for `io_uring_setup`, `io_uring_enter`, and
  `io_uring_register`, which disk-table reads require. Seccomp stays enabled.
- Every setting is visible in `docker-compose.yml`. There are no published host
  ports; the Hub's front door routes requests to the container.

## Fresh comparison — 2026-10-22

All three installed builds were measured consecutively through the Hub's public
`/v1` endpoint using the unchanged `tools/benchmark_editing_column.py:run_benchmark`
function, which follows the benchmark snippet on the recipe page. Only one model
ran at a time; unrelated apps were left alone. Launch/stop used the Hub APIs that
back its buttons. Flare was restored after the comparison.

Each pass discarded a 32-token warmup, then ran three writing prompts capped at
512 tokens with thinking on and a 3,000-token code edit with thinking off. Both
passes are saved; the table and catalog use the repeat pass consistently, without
selecting whichever pass was faster. Units are tokens/second, including request
elapsed time. Concurrent tests used identical 512-token prose requests.

| Build | Writing | Editing | 4 requests combined | 5 requests combined |
| --- | ---: | ---: | ---: | ---: |
| Flare NVFP4 | 37.4 | 67.4 | 94.6 | 73.3 |
| TensorFold NVFP4 | 49.8 | 103.0 | 150.9 | 177.5 |
| TensorFold INT4 | 63.1 | 117.1 | 108.1 | 156.3 |

The first-pass writing/editing figures were Flare NVFP4: 38.3 / 54.5; TensorFold NVFP4: 41.9 / 95.5; TensorFold INT4: 53.8 / 105.3.
These are short benchmark comparisons, not a quality ranking or a promise of
identical performance on other prompts. TensorFold INT4 uses a different quantized
checkpoint. Both TensorFold builds were faster on the writing/editing workload;
TensorFold NVFP4 delivered the highest combined rate at five requests. Flare's
fifth request queues behind its four active slots.

Raw results, both passes, concurrency samples, and served-model metadata:
`data/benchmarks/flare-tensorfold-comparison-20261022.json` at the Hub root.
Older standalone Flare measurements remain in that directory for reference.

## Functional validation

Passed through the Hub:

- Streaming Chat Completions with text chunks, final usage and `[DONE]`.
- Function tool call with typed arguments, followed by a tool-result round trip.
- Single-image recognition and ordered two-image frame interpretation.
- Reasoning disabled, low effort, and high effort mapped to Qwen's supported level.
- Responses and Anthropic Messages requests.

**Direct video-file input is unsupported:** a `video_url` request returned HTTP
400, `Unexpected item type in content.` The recipe advertises vision, tools and
thinking, and does not advertise video. Clients can provide extracted frames as
ordered images. Full-length 262K prompts and prolonged mixed multimodal load
have not been stress-tested; the recipe remains experimental.

Raw capability responses: `data/benchmarks/flare-capabilities-20261022.json`.

## Offline launch and Hub integration

The earlier Launch-button test temporarily used `network_mode: none`. Docker
confirmed that mode; DNS lookup returned exit 2 and no addresses. Hub logs showed
successful signature verification, weight loading, disk-table preparation, cache
allocation and server startup with 262,144 tokens/request. The test used eight
slots, before the later four-slot tuning; normal Hub networking was restored.
Preserved evidence: `data/benchmarks/flare-offline-20261022.json`.

Flare is grouped with the other Qwen3.8-Flash-Next builds. The model page shows its
publisher, pinned weight revision, benchmark snippet and current speeds. The
Agents page was checked at widths 390, 1440 and 2560 with zero horizontal overflow.
The backend was restarted after the changes. Live `/api/recipes` verification
confirmed all three updated speed pairs, Flare's publisher/revision and corrected
capabilities; `/v1/models` listed only Flare. Flare was ready with zero container
restarts and no OOM event. The model page also passed the same three width checks.

## Sources and license

- [Flare runtime](https://github.com/NVIDIA/flare-local-inference)
- [Container requirements](https://github.com/NVIDIA/flare-local-inference/blob/main/docs/container.md)
- [Prepared GB10 package](https://huggingface.co/local-inference-lab/Qwen3.8-Flash-Next-FLARE/tree/gb10)

This project is based on Qwen3.8-Flash-Next-FLARE by Local Inference Lab, Inc., a
non-profit organization, available at
https://huggingface.co/local-inference-lab/Qwen3.8-Flash-Next-FLARE.
Qwen3.8-Flash-Next-FLARE is licensed under the Local Inference Lab License,
Version 1.0 (Apache 2.0 terms plus no reupload). Installation downloads the
publisher's original package; this repository does not redistribute it. The
Spark's owner accepted this license and publisher key on 2026-10-22.
