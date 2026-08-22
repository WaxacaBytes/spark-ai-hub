from __future__ import annotations
import asyncio
import json
import time
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import AsyncGenerator

_ANSI_RE = re.compile(r'\x1b\[[^A-Za-z]*[A-Za-z]')

import yaml
import aiohttp

from daemon.config import settings
from daemon.db import get_db
from daemon.services import proxy_service
from daemon.models.container import ContainerInfo
from daemon.services.registry_service import get_recipe_dir, get_recipe


# In-memory readiness cache: slug -> True
_ready_cache: dict[str, bool] = {}
# Track active health check tasks: slug -> Task
_health_tasks: dict[str, asyncio.Task] = {}
# Track in-flight actions to prevent wrong states during transitions
# slug -> "launching" | "stopping" | "installing"
_pending_actions: dict[str, str] = {}


def mark_ready(slug: str):
    _ready_cache[slug] = True
    _pending_actions.pop(slug, None)


def clear_ready(slug: str):
    _ready_cache.pop(slug, None)
    if slug in _health_tasks:
        _health_tasks[slug].cancel()
        _health_tasks.pop(slug)


def is_ready(slug: str) -> bool:
    return _ready_cache.get(slug, False)


def set_pending(slug: str, action: str):
    _pending_actions[slug] = action


def clear_pending(slug: str):
    _pending_actions.pop(slug, None)


def get_pending(slug: str) -> str | None:
    return _pending_actions.get(slug)


async def start_health_check(slug: str):
    """Start a background health check task if one isn't already running."""
    if is_ready(slug):
        return
    if slug in _health_tasks and not _health_tasks[slug].done():
        return

    recipe = get_recipe(slug)
    if not recipe:
        return

    ui_port = recipe.ui.port if recipe.ui else 8080
    ui_path = recipe.ui.path if recipe.ui else "/"
    health_path = recipe.ui.health_path if recipe.ui and recipe.ui.health_path else ui_path
    proxied = bool(recipe.ui and recipe.ui.proxy)
    probe_headers = (
        {proxy_service.PROBE_HEADER: proxy_service.probe_token()} if proxied else {}
    )

    async def _check():
        if proxied:
            # Poll through the front door, not the container. A proxied app
            # publishes no host port, and this also proves the /run/{slug}/
            # path the user is about to click actually serves -- not merely
            # that something inside the container is listening.
            url = f"http://127.0.0.1:{settings.public_port}/run/{slug}{health_path}"
        else:
            url = f"http://127.0.0.1:{ui_port}{health_path}"
        # Up to 5 minutes of polling
        async with aiohttp.ClientSession() as session:
            for _ in range(300):
                await asyncio.sleep(1)
                try:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=3),
                        headers=probe_headers,
                        # The probe route bypasses forward_auth, so a stopped
                        # app answers 502 rather than a sign-in redirect that
                        # would otherwise read as success.
                        allow_redirects=not proxied,
                    ) as resp:
                        if 200 <= resp.status < 400:
                            mark_ready(slug)
                            print(f"[health] {slug} is ready at {url} ({resp.status})")
                            return
                except Exception:
                    pass
        print(f"[health] {slug} health check timed out")

    _health_tasks[slug] = asyncio.create_task(_check())



# ── Docker listing cache ────────────────────────────────────────────────────
# /api/recipes asks "does this recipe have leftovers?" for every catalog entry
# it has not installed, and each answer used to shell out to docker three or
# four times -- around 250 `docker` processes per poll, every 5 seconds, per
# open tab. The queries are global lists (all volumes, all images, images in
# use); only the membership test is per recipe. So run each list at most once
# every few seconds and match in Python.
_DOCKER_LIST_TTL = 3.0
_docker_lists: dict[str, tuple[float, list[str]]] = {}


async def _docker_lines(key: str, args: list[str]) -> list[str]:
    cached = _docker_lists.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _DOCKER_LIST_TTL:
        return cached[1]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    lines = stdout.decode(errors="replace").strip().splitlines()
    _docker_lists[key] = (now, lines)
    return lines


def invalidate_docker_lists():
    """Drop the cache after anything that adds or removes images/volumes."""
    _docker_lists.clear()


def _compose_project(slug: str) -> str:
    return f"spark-ai-hub-{slug}"


def _compose_cmd(slug: str, recipe_dir: Path) -> list[str]:
    return [
        "docker", "compose",
        "-p", _compose_project(slug),
        "-f", str(recipe_dir / "docker-compose.yml"),
    ]


def _runtime_env_file(recipe_dir: Path) -> Path:
    return recipe_dir / ".env"


def _runtime_env_template_file(recipe_dir: Path) -> Path:
    return recipe_dir / ".env.example"


def _render_runtime_env(template_text: str) -> str:
    shared_secrets: dict[str, str] = {
        "minio_password": secrets.token_urlsafe(24),
    }
    generated_values = {
        "USER_AUTH_SECRET": secrets.token_urlsafe(48),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "OPENSEARCH_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "MINIO_ROOT_PASSWORD": shared_secrets["minio_password"],
        "S3_AWS_SECRET_ACCESS_KEY": shared_secrets["minio_password"],
    }

    rendered_lines: list[str] = []
    for line in template_text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            rendered_lines.append(line)
            continue

        key, value = line.split("=", 1)
        if key in generated_values and not value.strip():
            value = generated_values[key]
        rendered_lines.append(f"{key}={value}")

    return "\n".join(rendered_lines) + "\n"


def ensure_runtime_env(recipe_dir: Path) -> tuple[Path | None, bool]:
    env_file = _runtime_env_file(recipe_dir)
    if env_file.is_file():
        return env_file, False

    template_file = _runtime_env_template_file(recipe_dir)
    if not template_file.is_file():
        return None, False

    env_file.write_text(_render_runtime_env(template_file.read_text()))
    env_file.chmod(0o600)
    return env_file, True


async def _stream_proc(cmd: list[str], cwd: str, env: dict | None = None) -> AsyncGenerator[tuple[str, int | None], None]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env,
    )
    buf = b""
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        # Normalize CRLF then split on both \n and \r so tqdm progress lines
        # (which use \r without \n) are yielded immediately.
        buf = buf.replace(b"\r\n", b"\n")
        parts = buf.replace(b"\r", b"\n").split(b"\n")
        buf = parts[-1]  # keep incomplete trailing fragment
        for part in parts[:-1]:
            text = _ANSI_RE.sub('', part.decode(errors="replace")).strip()
            if text:
                yield text, None
    if buf:
        text = _ANSI_RE.sub('', buf.decode(errors="replace")).strip()
        if text:
            yield text, None
    await proc.wait()
    yield "", proc.returncode


async def _prune_build_cache() -> str:
    """Drop BuildKit's copy of whatever a build downloaded.

    Weights are baked into the image, so once a build finishes the builder's
    cache holds a second copy of the same bytes — outside the image, where
    `docker rmi` cannot reach it. Left alone it doubles the disk cost of every
    build-type install, and an uninstalled recipe strands its weights there
    forever, invisible to the Hub. That is exactly the "cache outlives the
    thing it belongs to" failure the manifest forbids, so the Hub trades a
    re-download on reinstall for the promise that removing an app removes
    every byte it consumed. Records still in use by a concurrent build are
    in-use and survive the prune.
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "builder", "prune", "-a", "-f",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode(errors="replace").strip()
    for line in reversed(text.splitlines()):
        if line.lower().startswith("total:"):
            return line.strip()
    return "reclaimed 0B"


async def _prune_orphaned_images() -> str:
    """Remove every locally-cached Docker image the Hub no longer needs.

    A build pulls base/intermediate images (the vLLM/llama.cpp/CUDA runtime a
    recipe is built FROM) as a side effect, and Docker keeps them tagged in
    the local image store indefinitely once pulled -- nothing else in this
    file ever cleans them up. Left alone they are exactly the "cache outlives
    the thing it belongs to" failure _prune_build_cache() exists to prevent,
    just one layer up: an uninstalled recipe strands its base image forever,
    and every fresh install grows the store instead of costing a clean
    re-download. An image is kept only if it is the exact `image:` of a
    currently-installed recipe, or is referenced by any existing container
    (running or stopped) -- the latter protects anything outside the Hub's
    own management (e.g. a manually-run app) without needing to know its
    name. Everything else, including plain dangling layers, is removed.
    Individual images still held by a concurrent build fail to remove and are
    silently skipped, not treated as an error.
    """
    keep: set[str] = set()
    for slug in await get_installed_slugs():
        keep.update(_parse_compose_images(slug))

    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "-a", "--format", "{{.Image}}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    keep.update(
        line.strip() for line in out.decode(errors="replace").splitlines() if line.strip()
    )

    proc = await asyncio.create_subprocess_exec(
        "docker", "images", "--format", "{{.Repository}}:{{.Tag}}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    all_images = [
        line.strip() for line in out.decode(errors="replace").splitlines() if line.strip()
    ]

    removed = 0
    for image in all_images:
        if "<none>" in image or image in keep:
            continue
        proc = await asyncio.create_subprocess_exec(
            "docker", "rmi", "-f", image,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        rc = await proc.wait()
        if rc == 0:
            removed += 1

    # Plain dangling (untagged) layers are always safe to sweep too.
    proc = await asyncio.create_subprocess_exec(
        "docker", "image", "prune", "-f",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode(errors="replace").strip()
    dangling = "reclaimed 0B"
    for line in reversed(text.splitlines()):
        if line.lower().startswith("total reclaimed space:"):
            dangling = line.strip()
            break
    return f"removed {removed} orphaned image(s); dangling layers: {dangling}"


async def install_recipe(slug: str) -> AsyncGenerator[str, None]:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        yield f"[error] Recipe directory not found for {slug}"
        return

    compose_file = recipe_dir / "docker-compose.yml"
    if not compose_file.is_file():
        yield f"[error] docker-compose.yml not found in {recipe_dir}"
        return

    runtime_env_file, created_env = ensure_runtime_env(recipe_dir)
    if _runtime_env_template_file(recipe_dir).is_file() and runtime_env_file is None:
        yield f"[error] Failed to prepare runtime env for {slug}"
        return
    if created_env and runtime_env_file:
        yield f"[spark-ai-hub] Generated runtime config at {runtime_env_file}"

    recipe = get_recipe(slug)
    build_recipe = bool(recipe and recipe.docker and recipe.docker.build)

    yield f"[spark-ai-hub] Starting install for {slug}..."

    # Install acquires the app artifact and nothing else: weights are baked
    # into the image at build time, so there is no separate download phase.
    # A build does leave a second copy in BuildKit's cache, which is pruned
    # below once the image holds them.
    if build_recipe:
        cmd = _compose_cmd(slug, recipe_dir) + ["build"]
    else:
        cmd = _compose_cmd(slug, recipe_dir) + ["pull"]
    yield f"[spark-ai-hub] Running: {' '.join(cmd)}"
    rc = None
    # _launch_env() carries the auto-detected HF token, which the build needs
    # to pull gated checkpoints.
    async for text, code in _stream_proc(cmd, str(recipe_dir), env=_launch_env()):
        if text:
            yield text
        if code is not None:
            rc = code
    if rc != 0:
        yield f"[spark-ai-hub] Install failed with exit code {rc}"
        return

    if build_recipe:
        yield "[spark-ai-hub] Pruning build cache (the image now holds the weights)..."
        yield f"[spark-ai-hub] Build cache {await _prune_build_cache()}"

    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO installed_recipes (slug, status, compose_project) VALUES (?, 'installed', ?)",
            (slug, _compose_project(slug)),
        )
        await db.commit()
    finally:
        await db.close()

    # Every install ends by removing whatever base/intermediate images the
    # local store accumulated that no installed recipe needs anymore (MANIFEST
    # Principle 2) -- runs for pull-type recipes too, since a bumped `image:`
    # tag orphans the old pull the same way a rebuilt base image would.
    yield f"[spark-ai-hub] Pruning orphaned images... {await _prune_orphaned_images()}"
    yield f"[spark-ai-hub] {slug} installed successfully!"


async def update_recipe(slug: str) -> AsyncGenerator[str, None]:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        yield f"[error] Recipe directory not found for {slug}"
        return

    compose_file = recipe_dir / "docker-compose.yml"
    if not compose_file.is_file():
        yield f"[error] docker-compose.yml not found in {recipe_dir}"
        return

    runtime_env_file, created_env = ensure_runtime_env(recipe_dir)
    if _runtime_env_template_file(recipe_dir).is_file() and runtime_env_file is None:
        yield f"[error] Failed to prepare runtime env for {slug}"
        return
    if created_env and runtime_env_file:
        yield f"[spark-ai-hub] Generated runtime config at {runtime_env_file}"

    recipe = get_recipe(slug)
    build_recipe = bool(recipe and recipe.docker and recipe.docker.build)

    if build_recipe:
        yield f"[spark-ai-hub] Rebuilding local image for {slug}..."
        up_cmd = _compose_cmd(slug, recipe_dir) + ["up", "-d", "--build"]
        yield f"[spark-ai-hub] Running: {' '.join(up_cmd)}"

        proc = await asyncio.create_subprocess_exec(
            *up_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(recipe_dir),
        )

        async for line in proc.stdout:
            text = line.decode(errors="replace").rstrip()
            if '\r' in text:
                text = text.rsplit('\r', 1)[-1]
            if text:
                yield text

        await proc.wait()

        if proc.returncode == 0:
            yield "[spark-ai-hub] Pruning build cache (the image now holds the weights)..."
            yield f"[spark-ai-hub] Build cache {await _prune_build_cache()}"
            # The rebuild may have left the previous version's image (and its
            # base images) orphaned -- clean up per MANIFEST Principle 2.
            yield f"[spark-ai-hub] Pruning orphaned images... {await _prune_orphaned_images()}"
            yield f"[spark-ai-hub] {slug} rebuilt successfully!"
        else:
            yield f"[spark-ai-hub] Rebuild failed with exit code {proc.returncode}"
        return

    # Phase 1: Pull latest images
    yield f"[spark-ai-hub] Pulling latest images for {slug}..."
    pull_cmd = _compose_cmd(slug, recipe_dir) + ["pull"]
    yield f"[spark-ai-hub] Running: {' '.join(pull_cmd)}"

    proc = await asyncio.create_subprocess_exec(
        *pull_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
    )

    async for line in proc.stdout:
        text = line.decode(errors="replace").rstrip()
        if '\r' in text:
            text = text.rsplit('\r', 1)[-1]
        if text:
            yield text

    await proc.wait()

    if proc.returncode != 0:
        yield f"[spark-ai-hub] Pull failed with exit code {proc.returncode}"
        return

    # Phase 2: Recreate containers with new images
    yield f"[spark-ai-hub] Recreating containers for {slug}..."
    up_cmd = _compose_cmd(slug, recipe_dir) + ["up", "-d"]
    yield f"[spark-ai-hub] Running: {' '.join(up_cmd)}"

    proc = await asyncio.create_subprocess_exec(
        *up_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
    )

    async for line in proc.stdout:
        text = line.decode(errors="replace").rstrip()
        if '\r' in text:
            text = text.rsplit('\r', 1)[-1]
        if text:
            yield text

    await proc.wait()

    if proc.returncode == 0:
        yield f"[spark-ai-hub] Pruning orphaned images... {await _prune_orphaned_images()}"
        yield f"[spark-ai-hub] {slug} updated successfully!"
    else:
        yield f"[spark-ai-hub] Update failed with exit code {proc.returncode}"


def _launch_env() -> dict:
    """Environment for container launches, with auto-detected HF token."""
    from daemon.services import hf_token
    env = {**os.environ}
    if not env.get("HF_TOKEN"):
        token = hf_token.read_token()
        if token:
            env["HF_TOKEN"] = token
    return env


async def launch_recipe(slug: str) -> str:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        return f"Recipe directory not found for {slug}"

    runtime_env_file, _ = ensure_runtime_env(recipe_dir)
    if _runtime_env_template_file(recipe_dir).is_file() and runtime_env_file is None:
        return f"Failed to prepare runtime env for {slug}"

    # The shared app network has to exist before compose attaches to it: the
    # recipes declare it `external: true` precisely so that tearing one app
    # down cannot delete the network the others are still on.
    await proxy_service.ensure_network()

    cmd = _compose_cmd(slug, recipe_dir) + ["up", "-d"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
        env=_launch_env(),
    )
    output = await proc.stdout.read()
    await proc.wait()

    if proc.returncode == 0:
        db = await get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO installed_recipes (slug, status, compose_project) VALUES (?, 'installed', ?)",
                (slug, _compose_project(slug)),
            )
            await db.commit()
        finally:
            await db.close()
        return "launched"
    return output.decode(errors="replace")


async def stop_recipe(slug: str) -> str:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        return f"Recipe directory not found for {slug}"

    cmd = _compose_cmd(slug, recipe_dir) + ["down"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
    )
    await proc.wait()
    return "stopped" if proc.returncode == 0 else "failed"


async def remove_recipe(slug: str) -> str:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        return f"Recipe directory not found for {slug}"

    # Figure out which images are exclusive to this recipe. We must NOT use
    # `docker compose down --rmi all` blindly: several recipes share a runtime
    # base image (e.g. the llama.cpp `full-cuda13-*` image is reused across GGUF
    # recipes), and removing it would break the offline-launch guarantee of the
    # other still-installed recipes. Only remove images no other installed
    # recipe references.
    own_images = set(_parse_compose_images(slug))
    shared_images: set[str] = set()
    for other_slug in await get_installed_slugs():
        if other_slug == slug:
            continue
        shared_images.update(_parse_compose_images(other_slug))
    removable_images = own_images - shared_images

    cmd = _compose_cmd(slug, recipe_dir) + ["down", "--volumes"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
    )
    await proc.wait()

    if proc.returncode == 0:
        for image in removable_images:
            rmi_proc = await asyncio.create_subprocess_exec(
                "docker", "rmi", "-f", image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await rmi_proc.communicate()
        for volume in await _find_project_volumes(slug):
            vol_proc = await asyncio.create_subprocess_exec(
                "docker", "volume", "rm", "-f", volume,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await vol_proc.communicate()
        # The image is gone, but a build-type recipe also left its weights in
        # BuildKit's cache, which `docker rmi` never touches. Drop it so the
        # uninstall really does free every byte the recipe consumed.
        await _prune_build_cache()
        db = await get_db()
        try:
            await db.execute("DELETE FROM installed_recipes WHERE slug = ?", (slug,))
            await db.commit()
        finally:
            await db.close()
        # removable_images only catches this recipe's own final image; its
        # base/intermediate images (still tagged, now unreferenced) are a
        # separate leak that a plain `docker rmi` never reaches. Sweep those
        # too so uninstall really does free every byte (MANIFEST Principle 2).
        await _prune_orphaned_images()
        return "removed"
    return "failed"


async def get_running_containers() -> list[ContainerInfo]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "--filter", "label=com.docker.compose.project",
            "--format", '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
    except Exception:
        return []

    containers = []
    for line in stdout.decode().strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        status = parts[1] if len(parts) > 1 else ""
        image = parts[2] if len(parts) > 2 else ""
        ports_str = parts[3] if len(parts) > 3 else ""
        containers.append(ContainerInfo(
            name=name,
            status=status,
            image=image,
            ports=_parse_ports(ports_str),
        ))
    return containers


def _parse_ports(ports_str: str) -> dict[str, int | None]:
    ports = {}
    if not ports_str:
        return ports
    for mapping in ports_str.split(", "):
        if "->" in mapping:
            host_part, container_part = mapping.split("->")
            host_port = host_part.rsplit(":", 1)[-1]
            container_port = container_part.split("/")[0]
            ports[container_port] = int(host_port)
    return ports


async def get_project_for_slug(slug: str) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT compose_project FROM installed_recipes WHERE slug = ?", (slug,)
        )
        row = await cursor.fetchone()
        return row["compose_project"] if row else None
    finally:
        await db.close()


async def is_recipe_running(slug: str) -> bool:
    project = _compose_project(slug)
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-q",
            "--filter", f"label=com.docker.compose.project={project}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return len(stdout.decode().strip()) > 0
    except Exception:
        return False


async def get_container_name(slug: str) -> str | None:
    project = _compose_project(slug)
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "-p", project, "ps",
            "--format", "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        names = stdout.decode().strip().splitlines()
        return names[0] if names else None
    except Exception:
        return None


async def get_installed_slugs() -> set[str]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT slug FROM installed_recipes")
        rows = await cursor.fetchall()
        return {row["slug"] for row in rows}
    finally:
        await db.close()


def _parse_compose_images(slug: str) -> list[str]:
    """Parse docker-compose.yml to extract image names."""
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        return []
    compose_file = recipe_dir / "docker-compose.yml"
    if not compose_file.is_file():
        return []

    with open(compose_file) as f:
        data = yaml.safe_load(f)

    images = []
    for svc in (data.get("services") or {}).values():
        img = svc.get("image")
        if img:
            images.append(img)
    return images


async def _find_project_volumes(slug: str) -> list[str]:
    """Find Docker volumes belonging to any spark-ai-hub compose project for this slug."""
    # Try both the current project name and common historical variants
    project = _compose_project(slug)
    # Also check without the trailing slug suffix parts (e.g. spark-ai-hub-hunyuan3d vs spark-ai-hub-hunyuan3d-spark)
    prefixes = {project + "_"}
    base = slug.rsplit("-", 1)[0] if "-" in slug else slug
    # ...but only if `base` is not itself a real, separate recipe. Otherwise a
    # slug like `vllm-qwen36-27b-nvfp4-dflash` would match — and delete — the
    # cache volume of its sibling recipe `vllm-qwen36-27b-nvfp4`.
    if base != slug and get_recipe_dir(base) is None:
        prefixes.add(f"spark-ai-hub-{base}_")

    all_volumes = await _docker_lines("volumes", ["docker", "volume", "ls", "-q"])

    matched = []
    for v in all_volumes:
        if any(v.startswith(p) for p in prefixes):
            matched.append(v)
    return matched


async def _find_project_images(slug: str) -> list[str]:
    """Find Docker images that were used by a spark-ai-hub compose project for this slug.

    Only matches images that are not currently used by any running container,
    to avoid removing images used by non-Spark AI Hub containers.
    """
    compose_images = _parse_compose_images(slug)
    if not compose_images:
        return []

    # Get images currently in use by running containers
    in_use = set(await _docker_lines("ps_images", ["docker", "ps", "--format", "{{.Image}}"]))
    # Every image on the box, as repo:tag -- the same form `docker images -q
    # <img>` used to be asked about one at a time.
    present = set(await _docker_lines(
        "images", ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]
    ))

    matched = []
    for img in compose_images:
        # docker implies :latest when a reference carries no tag.
        ref = img if ":" in img.rsplit("/", 1)[-1] else f"{img}:latest"
        if ref in present and img not in in_use:
            matched.append(img)

    return matched


async def has_recipe_leftovers(slug: str) -> bool:
    """Check if any Docker images or volumes from a recipe still exist."""
    volumes = await _find_project_volumes(slug)
    if volumes:
        return True

    images = await _find_project_images(slug)
    if images:
        return True

    return False


async def purge_recipe(slug: str) -> str:
    """Remove all leftover Docker images and volumes for a recipe."""
    errors = []
    invalidate_docker_lists()

    volumes = await _find_project_volumes(slug)
    for vol in volumes:
        proc = await asyncio.create_subprocess_exec(
            "docker", "volume", "rm", "-f", vol,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            if err and "No such volume" not in err:
                errors.append(err)

    images = await _find_project_images(slug)
    for img in images:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rmi", "-f", img,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            if err and "No such image" not in err:
                errors.append(err)

    # Purge is the "make sure nothing was left" button -- also sweep any
    # base/intermediate images this recipe orphaned (MANIFEST Principle 2).
    await _prune_orphaned_images()
    return "purged" if not errors else f"partial: {'; '.join(errors)}"
