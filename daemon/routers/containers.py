import asyncio
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from daemon.services.docker_service import (
    install_recipe,
    launch_recipe,
    stop_recipe,
    remove_recipe,
    get_running_containers,
    get_container_name,
    mark_ready,
    clear_ready,
)
from daemon.services.registry_service import get_recipe
from daemon.models.container import ContainerInfo

router = APIRouter(tags=["containers"])

# Track active build tasks: slug -> {"lines": [...], "done": bool}
_builds: dict[str, dict] = {}


@router.post("/api/recipes/{slug}/install")
async def install(slug: str):
    recipe = get_recipe(slug)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    # If already building, return current status
    if slug in _builds and not _builds[slug]["done"]:
        return {"status": "building", "slug": slug}

    _builds[slug] = {"lines": [], "done": False}

    async def _run_build():
        try:
            async for line in install_recipe(slug):
                _builds[slug]["lines"].append(line)
        except Exception as e:
            _builds[slug]["lines"].append(f"[error] {e}")
        finally:
            _builds[slug]["done"] = True

    asyncio.create_task(_run_build())
    return {"status": "building", "slug": slug}


@router.get("/api/recipes/{slug}/build-status")
async def build_status(slug: str):
    build = _builds.get(slug)
    if not build:
        return {"status": "idle", "lines": []}
    return {
        "status": "done" if build["done"] else "building",
        "lines": build["lines"],
    }


@router.post("/api/recipes/{slug}/launch")
async def launch(slug: str):
    recipe = get_recipe(slug)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    clear_ready(slug)
    result = await launch_recipe(slug)
    if result == "launched":
        # Start background health check
        ui_port = recipe.ui.port if recipe.ui else 8080
        ui_path = recipe.ui.path if recipe.ui else "/"
        asyncio.create_task(_background_health_check(slug, ui_port, ui_path))
        return {"status": "launched", "slug": slug}
    raise HTTPException(status_code=500, detail=result)


async def _background_health_check(slug: str, port: int, path: str):
    import aiohttp
    url = f"http://localhost:{port}{path}"
    for _ in range(300):
        await asyncio.sleep(1)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)):
                    mark_ready(slug)
                    return
        except Exception:
            pass


@router.post("/api/recipes/{slug}/stop")
async def stop(slug: str):
    recipe = get_recipe(slug)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    clear_ready(slug)
    result = await stop_recipe(slug)
    return {"status": result, "slug": slug}


@router.delete("/api/recipes/{slug}")
async def remove(slug: str):
    recipe = get_recipe(slug)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    clear_ready(slug)
    result = await remove_recipe(slug)
    return {"status": result, "slug": slug}


@router.get("/api/containers", response_model=list[ContainerInfo])
async def list_containers():
    return await get_running_containers()


@router.websocket("/ws/build/{slug}")
async def build_log_ws(websocket: WebSocket, slug: str):
    await websocket.accept()
    seen = 0
    try:
        # Wait for the build to exist (it may not have started yet)
        for _ in range(50):  # up to 5 seconds
            if slug in _builds:
                break
            await asyncio.sleep(0.1)

        while True:
            build = _builds.get(slug)
            if not build:
                await asyncio.sleep(0.3)
                continue

            lines = build["lines"]
            while seen < len(lines):
                await websocket.send_text(lines[seen])
                seen += 1

            if build["done"]:
                await websocket.send_text("[done]")
                await websocket.close()
                return

            await asyncio.sleep(0.3)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/logs/{slug}")
async def container_log_ws(websocket: WebSocket, slug: str):
    await websocket.accept()
    proc = None
    health_task = None
    try:
        container = await get_container_name(slug)
        if not container:
            await websocket.send_text("[sparkforge] Container not running")
            await websocket.close()
            return

        # Start health check alongside log streaming
        recipe = get_recipe(slug)
        ui_port = recipe.ui.port if recipe and recipe.ui else 8080
        ui_path = recipe.ui.path if recipe and recipe.ui else "/"

        async def _check_ready():
            import aiohttp
            url = f"http://localhost:{ui_port}{ui_path}"
            for _ in range(300):  # up to 5 minutes
                await asyncio.sleep(1)
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                            mark_ready(slug)
                            await websocket.send_text("[sparkforge:ready]")
                            return
                except Exception:
                    pass

        health_task = asyncio.create_task(_check_ready())

        proc = await asyncio.create_subprocess_exec(
            "docker", "logs", "-f", "--tail", "200", container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async for line in proc.stdout:
            text = line.decode(errors="replace").rstrip()
            await websocket.send_text(text)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if health_task and not health_task.done():
            health_task.cancel()
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
