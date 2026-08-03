"""Minimal no-op shim for Hugging Face's ZeroGPU `spaces` package.

The upstream Space runs on ZeroGPU, where `import spaces` monkey-patches
`torch.cuda.*` and `@spaces.GPU` packs the module-scope resident set to disk
for an on-demand GPU worker. On the DGX Spark we have a real, always-attached
GPU, so none of that machinery is needed (or wanted): the module-scope
`.to("cuda")` calls should move weights straight onto the device, and the
decorated `synthesize()` should run in-process on that same GPU.

This shim provides exactly the surface `app.py` touches:

- `spaces.GPU(...)` / `@spaces.GPU` — a pass-through decorator that ignores the
  ZeroGPU-only kwargs (`duration`, `size`) and returns the function unchanged.

The app's AoTI loader does `from spaces.zero.torch.aoti import ...` inside a
`try/except`; since this shim has no `zero` submodule, that import raises and
the app falls back to eager execution — which is what we want (the precompiled
AoTI artifact is built for ZeroGPU's x86 workers, not aarch64 GB10).
"""


def GPU(*args, **kwargs):
    # Support both bare `@GPU` and called `@GPU(duration=..., size=...)` forms.
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]

    def _decorator(func):
        return func

    return _decorator
