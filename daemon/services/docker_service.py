from __future__ import annotations
import asyncio
import subprocess
from pathlib import Path
from typing import AsyncGenerator

import yaml
import aiohttp

from daemon.config import settings
from daemon.db import get_db
from daemon.models.container import ContainerInfo
from daemon.services.registry_service import get_recipe_dir, get_recipe


# In-memory readiness cache: slug -> True
_ready_cache: dict[str, bool] = {}
# Track active health check tasks: slug -> Task
_health_tasks: dict[str, asyncio.Task] = {}


def mark_ready(slug: str):
    _ready_cache[slug] = True


def clear_ready(slug: str):
    _ready_cache.pop(slug, None)
    if slug in _health_tasks:
        _health_tasks[slug].cancel()
        _health_tasks.pop(slug)


def is_ready(slug: str) -> bool:
    return _ready_cache.get(slug, False)


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

    async def _check():
        url = f"http://0.0.0.0:{ui_port}{ui_path}"
        # Up to 5 minutes of polling
        async with aiohttp.ClientSession() as session:
            for _ in range(300):
                await asyncio.sleep(1)
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)):
                        mark_ready(slug)
                        print(f"[health] {slug} is ready at {url}")
                        return
                except Exception:
                    pass
        print(f"[health] {slug} health check timed out")

    _health_tasks[slug] = asyncio.create_task(_check())


def _compose_project(slug: str) -> str:
    return f"sparkdeck-{slug}"


def _compose_cmd(slug: str, recipe_dir: Path) -> list[str]:
    return [
        "docker", "compose",
        "-p", _compose_project(slug),
        "-f", str(recipe_dir / "docker-compose.yml"),
    ]


async def install_recipe(slug: str) -> AsyncGenerator[str, None]:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        yield f"[error] Recipe directory not found for {slug}"
        return

    compose_file = recipe_dir / "docker-compose.yml"
    if not compose_file.is_file():
        yield f"[error] docker-compose.yml not found in {recipe_dir}"
        return

    yield f"[sparkdeck] Starting install for {slug}..."
    cmd = _compose_cmd(slug, recipe_dir) + ["up", "-d", "--build"]
    yield f"[sparkdeck] Running: {' '.join(cmd)}"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
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
        db = await get_db()
        try:
            await db.execute(
                "INSERT OR REPLACE INTO installed_recipes (slug, status, compose_project) VALUES (?, 'installed', ?)",
                (slug, _compose_project(slug)),
            )
            await db.commit()
        finally:
            await db.close()
        yield f"[sparkdeck] {slug} installed successfully!"
    else:
        yield f"[sparkdeck] Install failed with exit code {proc.returncode}"


async def launch_recipe(slug: str) -> str:
    recipe_dir = get_recipe_dir(slug)
    if not recipe_dir:
        return f"Recipe directory not found for {slug}"

    cmd = _compose_cmd(slug, recipe_dir) + ["up", "-d"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
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

    cmd = _compose_cmd(slug, recipe_dir) + ["down", "--rmi", "all", "--volumes"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
    )
    await proc.wait()

    if proc.returncode == 0:
        db = await get_db()
        try:
            await db.execute("DELETE FROM installed_recipes WHERE slug = ?", (slug,))
            await db.commit()
        finally:
            await db.close()
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
    """Find Docker volumes belonging to any sparkdeck compose project for this slug."""
    # Try both the current project name and common historical variants
    project = _compose_project(slug)
    # Also check without the trailing slug suffix parts (e.g. sparkdeck-hunyuan3d vs sparkdeck-hunyuan3d-spark)
    prefixes = {project + "_"}
    base = slug.rsplit("-", 1)[0] if "-" in slug else slug
    if base != slug:
        prefixes.add(f"sparkdeck-{base}_")

    proc = await asyncio.create_subprocess_exec(
        "docker", "volume", "ls", "-q",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    all_volumes = stdout.decode().strip().splitlines()

    matched = []
    for v in all_volumes:
        if any(v.startswith(p) for p in prefixes):
            matched.append(v)
    return matched


async def _find_project_images(slug: str) -> list[str]:
    """Find Docker images that were used by a sparkdeck compose project for this slug.

    Only matches images that are not currently used by any running container,
    to avoid removing images used by non-SparkDeck containers.
    """
    compose_images = _parse_compose_images(slug)
    if not compose_images:
        return []

    # Get images currently in use by running containers
    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "--format", "{{.Image}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    in_use = set(stdout.decode().strip().splitlines())

    matched = []
    for img in compose_images:
        # Check exact image:tag exists
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "-q", img,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if stdout.decode().strip() and img not in in_use:
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

    return "purged" if not errors else f"partial: {'; '.join(errors)}"
