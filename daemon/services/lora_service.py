"""LoRAs: add-ons the Hub loads into a running base model when a render asks.

A LoRA recipe (Recipe.lora) builds an image holding nothing but the adapter
file, so it installs and uninstalls like any recipe while no copy of the 45 GB
model it plugs into comes with it, and nothing of it ever runs. A render that
names it goes to one of its `bases`; before the job is sent, the Hub makes that
base's adapter match:

  * the file is copied out of the LoRA's image into the base's container (once
    per container: a recreated container starts without it), and
  * SGLang Diffusion's own endpoints switch it: POST /v1/set_lora turns an
    adapter on, POST /v1/unmerge_lora_weights turns it off again, and
    GET /v1/list_loras says which one is on -- asked every time rather than
    remembered here, so a restarted base is never mistaken for a loaded one.

The adapter is server-wide, not per request, and the server keeps its own
queue: switching while it still holds a job would change that job. So a switch
waits for nothing and changes nothing mid-queue -- a render that needs a
different adapter than the one rendering is refused with that reason, and the
agent sends it once the base is free.

Adapters are applied dynamically (beside the weights on every step, about 20%
per step on H3): our bases are FP8/INT8, and a BF16 adapter cannot be merged
into quantized weights. The first adapter a base loads also makes SGLang keep a
copy of the layers it wraps, for undoing it, until that base restarts.

Several LoRAs can be asked for together (a Turbo plus an effect). SGLang runs
only one dynamic adapter per transformer, so the Hub stacks them into one: on a
layer they add up, W + s1*B1@A1 + s2*B2@A2, which is exactly one adapter of
the summed rank, A = [A1; A2] and B = [s1*B1, s2*B2]. Each one's scale
(strength x alpha / rank) is folded into its B and its rank padded with zeros
to its largest, so the stack runs at scale 1. It is built inside the base's
container, beside the files it is made of, the first time that combination
is asked for. LoRAs stack only when they name their layers alike, which is
why each LoRA recipe ships its file in its model family's names.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from daemon.models.recipe import Recipe, RecipeLora
from daemon.services import proxy_service
from daemon.services.docker_service import (
    _parse_compose_images, get_container_name, get_installed_slugs,
)
from daemon.services.registry_service import get_recipes

# Where adapters land inside a base's container.
CONTAINER_DIR = "/tmp/loras"

_locks: dict[str, asyncio.Lock] = {}


class LoraError(Exception):
    """A LoRA request that cannot be served, in words for the agent."""


@dataclass(frozen=True)
class Stack:
    """Several LoRAs that run together as one adapter. Shaped like the LoRA
    recipe it stands in for wherever a render reads one."""
    parts: tuple[Recipe, ...]

    @property
    def slug(self) -> str:
        return "+".join(p.slug for p in self.parts)

    @property
    def name(self) -> str:
        return " + ".join(p.name for p in self.parts)

    @property
    def tags(self) -> list[str]:
        return sorted({t for p in self.parts for t in p.tags})

    @property
    def lora(self) -> RecipeLora:
        bases = [b for b in self.parts[0].lora.bases if all(b in p.lora.bases for p in self.parts)]
        special = next((p.lora for p in self.parts if p.lora.pipeline), None)
        return RecipeLora(bases=bases, file="", pipeline=special and special.pipeline,
                          text_embeddings=special and special.text_embeddings)


def parts(lora) -> tuple[Recipe, ...]:
    return lora.parts if isinstance(lora, Stack) else (lora,) if lora else ()


async def resolve(slugs: list[str], user: dict | None = None, model: str | None = None) -> Recipe | Stack | None:
    """The installed LoRA, or stack of them, that `slugs` name. `user`'s own
    link LoRAs count too, and a Civitai or Hugging Face URL is added as one
    first (for `model`, when the caller named it)."""
    from daemon.services import link_lora_service
    slugs = list(dict.fromkeys(s.strip() for s in slugs if s and s.strip()))
    if not slugs:
        return None
    have = {**await installed(), **await link_lora_service.mine(user)}
    for i, s in enumerate(slugs):
        if s.startswith(("http://", "https://")):
            try:
                link = await link_lora_service.add(s, user, model)
            except link_lora_service.LinkLoraError as exc:
                raise LoraError(str(exc)) from None
            have[link.slug] = link
            slugs[i] = link.slug
    missing = [s for s in slugs if s not in have]
    if missing:
        names = ", ".join(sorted(have)) or "none installed"
        raise LoraError(f"{', '.join(repr(s) for s in missing)} {'is not an installed LoRA' if len(missing) == 1 else 'are not installed LoRAs'}. "
                        f"Installed LoRAs: {names}. A Civitai or Hugging Face LoRA page's URL also works.")
    if len(slugs) == 1:
        return have[slugs[0]]
    stack = Stack(tuple(have[s] for s in slugs))
    if not stack.lora.bases:
        raise LoraError(f"{' and '.join(slugs)} have no base model in common, so they cannot run together. "
                        + "; ".join(f"{p.slug} loads into {', '.join(p.lora.bases)}" for p in stack.parts))
    special = [p.slug for p in stack.parts if p.lora.pipeline]
    if len(special) > 1:
        raise LoraError(f"{' and '.join(special)} each need their own pipeline; use them one at a time.")
    return stack


def lock(slug: str) -> asyncio.Lock:
    """Held from checking a base's adapter until its job is queued, so two
    renders cannot switch it under each other."""
    return _locks.setdefault(slug, asyncio.Lock())


def catalog() -> dict[str, Recipe]:
    return {slug: r for slug, r in get_recipes().items() if r.lora}


async def installed() -> dict[str, Recipe]:
    have = await get_installed_slugs()
    return {slug: r for slug, r in catalog().items() if slug in have}


async def for_base(slug: str, user: dict | None = None) -> list[Recipe]:
    """The installed LoRAs that load into `slug`, and `user`'s link LoRAs."""
    from daemon.services import link_lora_service
    found = {**await installed(), **await link_lora_service.mine(user)}
    return [r for r in found.values() if slug in r.lora.bases]


def takes_loras(slug: str) -> bool:
    """Is `slug` a base any catalog LoRA loads into? Only those are asked
    which adapter they hold."""
    return any(slug in r.lora.bases for r in catalog().values())


def merged_defaults(defaults, lora, field: str = "video_defaults"):
    """The base's settings with the LoRA's own on top: an adapter often wants
    its own step count, shape or shift, and `extra_params` keys it names.
    `field` is the recipe's video_defaults or image_defaults. In a stack a
    Turbo's settings go on last: the steps and shifts it was distilled for
    are the ones that work, whatever an effect LoRA would pick for the full
    model."""
    for part in sorted(parts(lora), key=lambda p: "turbo" in p.tags):
        own = getattr(part, field, None)
        if own is None:
            continue
        if defaults is None:
            defaults = own
            continue
        update = {k: v for k, v in own.model_dump(exclude_unset=True).items()
                  if k != "extra_params" and v not in (None, "")}
        if getattr(own, "extra_params", None):
            update["extra_params"] = {**(defaults.extra_params or {}), **own.extra_params}
        defaults = defaults.model_copy(update=update)
    return defaults


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace").strip()


async def _copy_in(container: str, lora: Recipe, source: str | None = None,
                   suffix: str = "") -> str:
    """The path inside `container` of the LoRA image's file `source` (the
    adapter itself by default), copying it there if missing.

    Copied out of the LoRA's image through a container that is created and
    removed again without ever being started -- `docker cp` reads from
    containers, not images.
    """
    source = source or lora.lora.file
    dest = f"{CONTAINER_DIR}/{lora.slug}{suffix}.safetensors"
    if (await _run("docker", "exec", container, "test", "-f", dest))[0] == 0:
        return dest
    if lora.lora.host_file:
        await _run("docker", "exec", container, "mkdir", "-p", CONTAINER_DIR)
        rc, out = await _run("docker", "cp", lora.lora.host_file, f"{container}:{dest}")
        if rc != 0:
            raise LoraError(f"Copying {lora.name} into {container} failed: {out[-300:]}")
        return dest
    images = _parse_compose_images(lora.slug)
    if not images:
        raise LoraError(f"{lora.slug} names no image in its docker-compose.yml.")
    holder = f"spark-ai-hub-lora-copy-{secrets.token_hex(4)}"
    with tempfile.TemporaryDirectory() as tmp:
        local = str(Path(tmp) / "adapter.safetensors")
        rc, out = await _run("docker", "create", "--name", holder, images[0], "/none")
        if rc != 0:
            raise LoraError(f"{lora.slug}'s image is missing; install it again. ({out[-300:]})")
        try:
            rc, out = await _run("docker", "cp", f"{holder}:{source}", local)
        finally:
            await _run("docker", "rm", "-f", holder)
        if rc != 0:
            raise LoraError(f"{lora.slug}'s image has no {source}. ({out[-300:]})")
        await _run("docker", "exec", container, "mkdir", "-p", CONTAINER_DIR)
        rc, out = await _run("docker", "cp", local, f"{container}:{dest}")
        if rc != 0:
            raise LoraError(f"Copying {lora.slug} into {container} failed: {out[-300:]}")
    return dest


# Run with the base container's own Python (every engine image has torch and
# safetensors): out.safetensors, then (path, strength, alpha or "") per LoRA.
# Prints the stack's rank. Layer names are kept as each file has them; the
# files must share them, or the stack would not add up on one layer.
_STACK = r"""
import re, sys, torch
from safetensors import safe_open
from safetensors.torch import save_file
out, args = sys.argv[1], sys.argv[2:]
parts, metadata, dtype = [], {}, torch.bfloat16
for i in range(0, len(args), 3):
    path, strength, alpha = args[i], float(args[i + 1]), args[i + 2]
    f = safe_open(path, "pt")
    meta = f.metadata() or {}
    metadata.update({k: v for k, v in meta.items() if k not in ("alpha", "rank") and k not in metadata})
    pairs = {}
    for key in f.keys():
        m = re.match(r"^(.*)\.(lora_A|lora_B|lora_down|lora_up)(?:\.default)?\.weight$", key) or re.match(r"^(.*)\.(alpha)$", key)
        if not m:
            sys.exit(f"{path}: {key} is not a LoRA weight")
        t = f.get_tensor(key)
        if t.dtype == torch.float32:
            dtype = torch.float32
        # ComfyUI and diffusers files prefix the same layer differently
        # (diffusion_model. / transformer.); the server reads it either way,
        # so the parts match on the bare name.
        name = re.sub(r"^(?:diffusion_model|transformer|base_model\.model)\.", "", m[1])
        pairs.setdefault(name, {})[{"lora_down": "A", "lora_up": "B"}.get(m[2], m[2][-1])] = t
    layers = {}
    for name, t in pairs.items():
        a, b = t["A"].float(), t["B"].float()
        rank = a.shape[0]
        scale = float(alpha) if alpha else t["a"].item() if "a" in t else float(meta.get("alpha") or rank)
        layers[name] = (a, b * (strength * scale / rank))
    parts.append((path, layers, max(a.shape[0] for a, _ in layers.values())))
for (p1, l1, _), (p2, l2, _) in zip(parts, parts[1:]):
    if not set(l1) & set(l2):
        sys.exit(f"{p1} and {p2} name their layers differently, so they cannot be stacked")
out_t = {}
for name in dict.fromkeys(n for _, layers, _ in parts for n in layers):
    a_in = b_out = None
    for _, layers, _ in parts:
        if name in layers:
            a_in, b_out = layers[name][0].shape[1], layers[name][1].shape[0]
    a_rows, b_cols = [], []
    for _, layers, r in parts:
        a, b = layers.get(name, (torch.zeros(0, a_in), torch.zeros(b_out, 0)))
        a_rows.append(torch.cat([a, torch.zeros(r - a.shape[0], a_in)]))
        b_cols.append(torch.cat([b, torch.zeros(b_out, r - b.shape[1])], dim=1))
    out_t[f"{name}.lora_A.weight"] = torch.cat(a_rows).to(dtype).contiguous()
    out_t[f"{name}.lora_B.weight"] = torch.cat(b_cols, dim=1).to(dtype).contiguous()
save_file(out_t, out + ".part", metadata=metadata)
import os
os.replace(out + ".part", out)
print(sum(r for _, _, r in parts))
"""


async def _stack(container: str, stack: Stack) -> tuple[str, int]:
    """The stacked adapter's path inside `container` and its rank, built
    there from its parts on first use."""
    dest = f"{CONTAINER_DIR}/{stack.slug}.safetensors"
    rank_file = dest + ".rank"
    rc, out = await _run("docker", "exec", container, "cat", rank_file)
    if rc == 0 and out.strip().isdigit():
        return dest, int(out)
    args = []
    for part in stack.parts:
        args += [await _copy_in(container, part), str(part.lora.strength),
                 str(part.lora.alpha or "")]
    rc, out = await _run("docker", "exec", container, "python3", "-c", _STACK, dest, *args)
    if rc != 0 or not out.strip().splitlines()[-1].isdigit():
        raise LoraError(f"Stacking {stack.name} failed: {out[-400:]}")
    rank = int(out.strip().splitlines()[-1])
    await _run("docker", "exec", container, "sh", "-c", f"echo {rank} > '{rank_file}'")
    return dest, rank


async def _call(session: aiohttp.ClientSession, method: str, slug: str, path: str,
                body: dict | None = None) -> tuple[int, dict | str]:
    async with session.request(method, f"{proxy_service.internal_url(slug)}{path}", json=body,
                               headers=proxy_service.probe_headers()) as r:
        try:
            return r.status, await r.json()
        except (aiohttp.ContentTypeError, ValueError):
            return r.status, await r.text()


async def active(slug: str, session: aiohttp.ClientSession) -> str | None:
    """The nickname of the adapter `slug` has switched on, or None. Recipes
    load adapters under their own slug, so this is a LoRA recipe's slug."""
    status, body = await _call(session, "GET", slug, "/v1/list_loras")
    if status != 200 or not isinstance(body, dict):
        return None
    on = (body.get("active") or {}).get("transformer") or []
    return on[0].get("nickname") if on else None


async def use(slug: str, lora: Recipe | Stack | None, busy) -> None:
    """Make base `slug` run with `lora` (or a stack) on, or with none. Call under lock(slug),
    and send the job before letting go. `busy()` counts the jobs the base
    still holds; it is asked only when the adapter has to change."""
    if lora is None and not takes_loras(slug):
        return
    timeout = aiohttp.ClientTimeout(total=900, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        current = await active(slug, session)
        wanted = lora.slug if lora else None
        if current == wanted:
            return
        if busy := await busy():
            names = {None: "no LoRA"}
            raise LoraError(
                f"{slug} is rendering {busy} job{'s' * (busy > 1)} with "
                f"{names.get(current, current)} right now, and this render needs "
                f"{names.get(wanted, wanted)}; switching now would change the queued render. "
                "Collect that render with get_video first, then send this one again.")
        if lora is None:
            status, body = await _call(session, "POST", slug, "/v1/unmerge_lora_weights",
                                       {"target": "all"})
        else:
            container = await get_container_name(slug)
            if not container:
                raise LoraError(f"{slug} is not running.")
            if isinstance(lora, Stack):
                path, rank = await _stack(container, lora)
                request = {"lora_nickname": lora.slug, "lora_path": path, "strength": 1.0,
                           "lora_alpha": rank, "merge_mode": "dynamic"}
            else:
                request = {
                    "lora_nickname": lora.slug, "lora_path": await _copy_in(container, lora),
                    "strength": lora.lora.strength, "lora_alpha": lora.lora.alpha,
                    "merge_mode": "dynamic",
                }
            # Only engines that read them get these (SGLang never sees them).
            special = next((p for p in parts(lora) if p.lora.pipeline), None)
            if special:
                request["pipeline"] = special.lora.pipeline
                if special.lora.text_embeddings:
                    request["text_embeddings_path"] = await _copy_in(
                        container, special, special.lora.text_embeddings, suffix=".text-embeddings")
            since = str(int(time.time()) - 1)
            status, body = await _call(session, "POST", slug, "/v1/set_lora", request)
            if status == 200 and not isinstance(lora, Stack) and lora.lora.host_file:
                await _check_coverage(session, slug, container, lora, since)
        if status != 200:
            raise LoraError(f"{slug} refused to switch to {wanted or 'no LoRA'} "
                            f"(HTTP {status}): {str(body)[:400]}")


_COVERS = re.compile(r"covers (\d+)/(\d+) LoRA layers")


async def _check_coverage(session: aiohttp.ClientSession, slug: str, container: str,
                          lora: Recipe, since: str) -> None:
    """A link LoRA SGLang matched to too few of its layers fits another model
    (or a layout the Hub cannot rewrite): switch it off, delete it, say so.
    SGLang reports the count in its log when it loads an adapter."""
    from daemon.services import link_lora_service
    rc, log = await _run("docker", "logs", "--since", since, container)
    line = next((l for l in log.splitlines() if f"{lora.slug}.safetensors" in l and _COVERS.search(l)), None)
    if line is None:
        return                       # an engine that does not report it
    matched = int(_COVERS.search(line)[1])
    wanted = link_lora_service.modules_in(Path(lora.lora.host_file))
    if wanted and matched >= 0.9 * wanted:
        return
    await _call(session, "POST", slug, "/v1/unmerge_lora_weights", {"target": "all"})
    await _run("docker", "exec", container, "rm", "-f", f"{CONTAINER_DIR}/{lora.slug}.safetensors")
    await link_lora_service.remove(lora)
    raise LoraError(f"{lora.name} matched only {matched} of its {wanted} layers in {slug}, so it was made "
                    "for another model or saved in a layout the Hub cannot read. It was switched off and "
                    "deleted.")


async def forget(name: str) -> None:
    """A deleted link LoRA: switch it off in any running base that holds it."""
    slug = "link-" + name[:12]
    for base in get_recipes().values():
        container = await get_container_name(base.slug)
        if not container or (await _run("docker", "exec", container, "test", "-f",
                                        f"{CONTAINER_DIR}/{slug}.safetensors"))[0] != 0:
            continue
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            if await active(base.slug, session) == slug:
                await _call(session, "POST", base.slug, "/v1/unmerge_lora_weights", {"target": "all"})
        await _run("docker", "exec", container, "rm", "-f", f"{CONTAINER_DIR}/{slug}.safetensors")
