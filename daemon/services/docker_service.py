from __future__ import annotations
import asyncio
import subprocess
from pathlib import Path
from typing import AsyncGenerator

from daemon.config import settings
from daemon.db import get_db
from daemon.models.container import ContainerInfo
from daemon.services.registry_service import get_recipe_dir


# In-memory readiness cache: slug -> True
_ready_cache: dict[str, bool] = {}


def mark_ready(slug: str):
    _ready_cache[slug] = True


def clear_ready(slug: str):
    _ready_cache.pop(slug, None)


def is_ready(slug: str) -> bool:
    return _ready_cache.get(slug, False)


def _compose_project(slug: str) -> str:
    return f"sparkforge-{slug}"


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

    yield f"[sparkforge] Starting install for {slug}..."
    cmd = _compose_cmd(slug, recipe_dir) + ["up", "-d", "--build"]
    yield f"[sparkforge] Running: {' '.join(cmd)}"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(recipe_dir),
    )

    async for line in proc.stdout:
        yield line.decode(errors="replace").rstrip()

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
        yield f"[sparkforge] {slug} installed successfully!"
    else:
        yield f"[sparkforge] Install failed with exit code {proc.returncode}"


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
