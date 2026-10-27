"""Link LoRAs: an agent passes a Civitai or Hugging Face page as `lora`, and
the Hub fetches the adapter and loads it like an installed LoRA recipe.

A link LoRA is personal: it belongs to the account that added it, shows in
that account's My files as kind "lora" (name, base, source, trigger words),
and stays until it is deleted there (or with delete_file). It is never turned
into an installed LoRA by the Hub; that happens only by writing a recipe.

Adding one (add):
  * the page is read for its versions and the base model each was trained on,
    and the version whose base is installed on this Spark is taken -- Civitai
    names the base per version (`baseModel`), Hugging Face in the card's
    `base_model`, matched against the repos each recipe's weights come from;
  * only a .safetensors file is downloaded (never a pickle), at most
    MAX_BYTES, with the Hub's own Civitai key or Hugging Face token when the
    page needs one -- agents never see either;
  * its layer names are rewritten for the base family (lora_keys.convert) and
    it is stored under data/loras/ with a media row naming its owner.
It refuses with the reason when no version fits an installed model, the file
is not a LoRA, or a key is missing; nothing is kept then. Whether the layers
really fit is only known once a base loads it: check_coverage reads SGLang's
own count and deletes an adapter that matched almost nothing.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import struct
import urllib.parse
from pathlib import Path

import aiohttp

from daemon.config import settings
from daemon.db import get_db
from daemon.models.recipe import Recipe, RecipeImageDefaults, RecipeLora, RecipeVideoDefaults, weights_of
from daemon.services import hf_token, lora_keys
from daemon.services.docker_service import get_installed_slugs
from daemon.services.registry_service import get_recipes

LORA_DIR = settings.data_dir / "loras"
NAME_RE = re.compile(r"^[0-9a-f]{32}\.safetensors$")
PUBLIC_PREFIX = "/loras"
MAX_BYTES = 2 * 1024 ** 3
SLUG_PREFIX = "link-"
CIVITAI_KEY_PATH = settings.data_dir / "civitai-key"

# Civitai's name for a base model -> the family of the Hub's bases it fits.
CIVITAI_FAMILIES = {
    "Qwen 2.1": "qwen-image", "MOSS Image": "moss-image", "MiniMax H3": "minimax-h3",
    "ZImageTurbo": "z-image-turbo", "ZImageBase": "z-image",
    "Flux.2 Klein 9B": "flux2-klein-9b", "Flux.2 Klein 4B": "flux2-klein-4b",
}


class LinkLoraError(Exception):
    """A link LoRA that cannot be added, in words for the agent."""


# ------------------------------------------------------------------ keys

def civitai_key() -> str:
    return CIVITAI_KEY_PATH.read_text().strip() if CIVITAI_KEY_PATH.is_file() else ""


def write_civitai_key(key: str) -> None:
    CIVITAI_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CIVITAI_KEY_PATH.write_text(key.strip())
    CIVITAI_KEY_PATH.chmod(0o600)


# ------------------------------------------------------------------ bases

def family(recipe: Recipe) -> str | None:
    """Which LoRA family a base recipe belongs to: the slug says the model,
    and only SGLang Diffusion servers switch adapters with set_lora."""
    if (recipe.engine or "").lower() != "sglang" or recipe.lora:
        return None
    slug = recipe.slug
    for key in ("qwen-image-21", "moss-image", "minimax-h3", "z-image-turbo", "z-image",
                "flux2-klein-9b", "flux2-klein-4b"):
        if key in slug:
            return {"qwen-image-21": "qwen-image"}.get(key, key)
    return None


async def _bases_for(fam: str | None, repos: list[str] | None = None) -> list[Recipe]:
    """Installed bases of a family, or whose weights come from one of `repos`."""
    have = await get_installed_slugs()
    out = []
    for slug, recipe in get_recipes().items():
        if slug not in have or not (f := family(recipe)):
            continue
        if (fam and f == fam) or (repos and set(repos) & set(weights_of(recipe))):
            out.append(recipe)
    return out


def _variant_hint(text: str) -> str | None:
    """MiniMax-H3 ships two weight sets; an adapter fits only its own."""
    t = text.lower()
    if "ref2va" in t or "ref2v" in t or "reference" in t:
        return "Ref2VA"
    if "fl2va" in t or "t2v" in t or "first frame" in t or "text-to-video" in t:
        return "FL2VA"
    return None


# ------------------------------------------------------------------ sources

def _source_kind(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.endswith("civitai.com"):
        return "civitai"
    if host in ("huggingface.co", "hf.co"):
        return "hf"
    raise LinkLoraError("LoRA links must be Civitai (civitai.com/models/...) or Hugging Face "
                        "(huggingface.co/<org>/<repo>) pages.")


async def _civitai(session: aiohttp.ClientSession, url: str, model: str | None) -> dict:
    parts = urllib.parse.urlsplit(url)
    m = re.search(r"/models/(\d+)", parts.path)
    if not m:
        raise LinkLoraError("A Civitai link must point at a model page: civitai.com/models/<id>.")
    want_version = urllib.parse.parse_qs(parts.query).get("modelVersionId", [None])[0]
    async with session.get(f"https://civitai.com/api/v1/models/{m[1]}") as r:
        if r.status != 200:
            raise LinkLoraError(f"Civitai answered HTTP {r.status} for model {m[1]}.")
        info = await r.json()
    if info.get("type") != "LORA":
        raise LinkLoraError(f"Civitai model {m[1]} is a {info.get('type')}, not a LoRA.")
    tried = []
    for version in info.get("modelVersions") or []:
        if want_version and str(version["id"]) != want_version:
            continue
        fam = CIVITAI_FAMILIES.get(version.get("baseModel"))
        tried.append(version.get("baseModel"))
        bases = await _bases_for(fam) if fam else []
        if model:
            bases = [b for b in bases if b.slug == model]
        if not bases:
            continue
        files = [f for f in version.get("files") or []
                 if f["name"].endswith(".safetensors") and f.get("type") in (None, "Model")]
        if not files:
            raise LinkLoraError(f"Civitai version {version['id']} has no .safetensors file.")
        file = files[0]
        return {"family": fam, "bases": bases, "download": file["downloadUrl"],
                "bytes": int(file.get("sizeKB", 0) * 1024), "file": file["name"],
                "title": f"{info.get('name')} ({version.get('name')})",
                "trigger": ", ".join(w.strip().strip(",") for w in version.get("trainedWords") or [] if w.strip()),
                "source": f"https://civitai.com/models/{m[1]}?modelVersionId={version['id']}",
                "hint": " ".join([info.get("name", ""), version.get("name", ""), " ".join(info.get("tags") or [])])}
    seen = ", ".join(sorted(set(filter(None, tried)))) or "none"
    raise LinkLoraError(f"No version of this LoRA fits a model installed here{f' ({model})' if model else ''}: "
                        f"it is made for {seen}. LoRAs load into Qwen-Image-2.1, MOSS-Image, MiniMax-H3, "
                        "Z-Image and FLUX.2 Klein models on this Spark.")


async def _hf(session: aiohttp.ClientSession, url: str, model: str | None) -> dict:
    path = [p for p in urllib.parse.urlsplit(url).path.split("/") if p]
    if len(path) < 2:
        raise LinkLoraError("A Hugging Face link must name a repo: huggingface.co/<org>/<repo>.")
    repo, rest = f"{path[0]}/{path[1]}", path[2:]
    revision, file = "main", None
    if len(rest) >= 3 and rest[0] in ("blob", "resolve", "tree"):
        revision, file = rest[1], "/".join(rest[2:]) or None
    headers = {"Authorization": f"Bearer {t}"} if (t := hf_token.read_token()) else {}
    async with session.get(f"https://huggingface.co/api/models/{repo}/revision/{revision}", headers=headers) as r:
        if r.status != 200:
            raise LinkLoraError(f"Hugging Face answered HTTP {r.status} for {repo}"
                                f"{' (a gated repo: accept its terms with the Hub token account)' if r.status in (401, 403) else ''}.")
        info = await r.json()
    if not file:
        files = [s["rfilename"] for s in info.get("siblings") or [] if s["rfilename"].endswith(".safetensors")]
        if len(files) != 1:
            raise LinkLoraError(f"{repo} holds {len(files)} .safetensors files; link the one to use "
                                f"(huggingface.co/{repo}/blob/main/<file>). Files: {', '.join(files[:12])}.")
        file = files[0]
    if not file.endswith(".safetensors"):
        raise LinkLoraError("Only .safetensors LoRA files are loaded.")
    card = info.get("cardData") or {}
    base_models = card.get("base_model") or []
    base_models = [base_models] if isinstance(base_models, str) else list(base_models)
    bases = await _bases_for(None, base_models)
    if model:
        bases = [b for b in bases if b.slug == model]
    if not bases:
        raise LinkLoraError(f"{repo} is made for {', '.join(base_models) or 'an unnamed base model'}, which no "
                            f"model installed here{f' ({model})' if model else ''} runs.")
    trigger = card.get("instance_prompt") or ""
    return {"family": family(bases[0]), "bases": bases, "file": Path(file).name, "bytes": 0,
            "download": f"https://huggingface.co/{repo}/resolve/{info.get('sha') or revision}/{file}",
            "title": f"{repo.split('/')[1]}", "trigger": trigger if isinstance(trigger, str) else "",
            "source": f"https://huggingface.co/{repo}/blob/{info.get('sha') or revision}/{file}",
            "hint": " ".join([repo, file, " ".join(card.get("tags") or [])]), "hf": True}


async def _download(session: aiohttp.ClientSession, found: dict, dest: Path) -> None:
    headers = {}
    if found.get("hf"):
        if t := hf_token.read_token():
            headers["Authorization"] = f"Bearer {t}"
    elif key := civitai_key():
        headers["Authorization"] = f"Bearer {key}"
    if found["bytes"] > MAX_BYTES:
        raise LinkLoraError(f"{found['file']} is {found['bytes'] / 2**30:.1f} GB; LoRAs over "
                            f"{MAX_BYTES / 2**30:.0f} GB are not loaded.")
    async with session.get(found["download"], headers=headers) as r:
        if r.status in (401, 403):
            raise LinkLoraError("Civitai refused this download. The Hub's admin can check or replace the "
                                "Civitai API key in Hub settings." if not found.get("hf") else
                                "Hugging Face refused the download (a gated repo: accept its terms with the "
                                "account of the Hub's token).")
        if r.status != 200:
            raise LinkLoraError(f"Downloading {found['file']} failed (HTTP {r.status}).")
        size = 0
        with open(dest, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise LinkLoraError(f"{found['file']} is larger than {MAX_BYTES / 2**30:.0f} GB.")
                f.write(chunk)


# ------------------------------------------------------------------ store

def _recipe(row) -> Recipe:
    """A link LoRA in the shape lora_service reads an installed one."""
    bases = [b for b in row["bases"].split(",") if b]
    notes = f"Trigger words: {row['prompt']}." if row["prompt"] else "No trigger words given by its page."
    notes += f" Added from {row['source']}."
    return Recipe(name=row["title"] or row["name"], slug=SLUG_PREFIX + row["name"][:12],
                  description=(f"Link LoRA from {row['source']}."
                               + (f" Trigger words: {row['prompt']}." if row["prompt"] else "")),
                  tags=["lora", "link-lora"],
                  lora=RecipeLora(bases=bases, file="", host_file=str(LORA_DIR / row["name"])),
                  image_defaults=RecipeImageDefaults(notes=notes, source=row["source"]),
                  video_defaults=RecipeVideoDefaults(notes=notes, source=row["source"]))


async def mine(user: dict | None) -> dict[str, Recipe]:
    """The link LoRAs `user` added, by slug."""
    if not user:
        return {}
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT name, title, source, bases, prompt FROM media WHERE user_id = ? AND name LIKE '%.safetensors'",
            (user["id"],))).fetchall()
    finally:
        await db.close()
    return {r.slug: r for r in (_recipe(row) for row in rows if (LORA_DIR / row["name"]).is_file())}


async def add(url: str, user: dict | None, model: str | None = None) -> Recipe:
    """The link LoRA at `url` for `user`, fetched and converted on first use."""
    if not user:
        raise LinkLoraError("Link LoRAs need a Hub account.")
    kind = _source_kind(url)
    timeout = aiohttp.ClientTimeout(total=1800, sock_connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        found = await (_civitai if kind == "civitai" else _hf)(session, url, model)
        # The same page and version again: the copy already here.
        for recipe in (await mine(user)).values():
            if recipe.image_defaults and recipe.image_defaults.source == found["source"]:
                return recipe
        bases = found["bases"]
        if found["family"] == "minimax-h3" and (variant := _variant_hint(found["hint"])):
            bases = [b for b in bases if (b.variant or "") == variant] or bases
        LORA_DIR.mkdir(parents=True, exist_ok=True)
        name = secrets.token_hex(16) + ".safetensors"
        raw = LORA_DIR / (name + ".download")
        try:
            await _download(session, found, raw)
            try:
                await asyncio.to_thread(lora_keys.convert, raw, LORA_DIR / name, found["family"] or "")
            except ValueError as exc:
                raise LinkLoraError(f"{found['file']} is not a LoRA this Hub can load: {exc}") from None
        finally:
            raw.unlink(missing_ok=True)
    db = await get_db()
    try:
        await db.execute("INSERT OR REPLACE INTO media (name, user_id, model, prompt, title, source, bases) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (name, user["id"], bases[0].slug, found["trigger"], found["title"], found["source"],
                          ",".join(b.slug for b in bases)))
        await db.commit()
    finally:
        await db.close()
    return (await mine(user))[SLUG_PREFIX + name[:12]]


def modules_in(path: Path) -> int:
    """How many layers the adapter file touches, from its header alone."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        head = json.loads(f.read(n))
    return sum(1 for k in head if k.endswith(".lora_A.weight"))


async def remove(lora: Recipe) -> None:
    """Delete a link LoRA that turned out not to fit (its file and row)."""
    path = Path(lora.lora.host_file)
    path.unlink(missing_ok=True)
    db = await get_db()
    try:
        await db.execute("DELETE FROM media WHERE name = ?", (path.name,))
        await db.commit()
    finally:
        await db.close()
