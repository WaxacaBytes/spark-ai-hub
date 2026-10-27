import asyncio
from fastapi import APIRouter, Depends, HTTPException

from daemon.db import get_db
from daemon.models.recipe import Recipe
from daemon.routers import openai_proxy
from daemon.routers.auth import require_admin
from daemon.services import favorites_service, proxy_service
from daemon.services.registry_service import get_recipes, get_recipe, get_recipe_dir, load_recipes
from daemon.services.docker_service import (
    get_installed_slugs,
    is_recipe_running,
    is_ready,
    has_recipe_leftovers,
    start_health_check,
    get_pending,
    get_startup_error,
    up_since,
)

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


def _set_runtime_env_path(recipe: Recipe) -> None:
    recipe_dir = get_recipe_dir(recipe.slug)
    if not recipe_dir:
        return

    env_file = recipe_dir / ".env"
    env_template = recipe_dir / ".env.example"
    if env_file.is_file() or env_template.is_file():
        recipe.runtime_env_path = str(env_file)


@router.get("", response_model=list[Recipe])
async def list_recipes(category: str | None = None, search: str | None = None):
    recipes = list(get_recipes().values())
    installed = await get_installed_slugs()

    result = []
    for r in recipes:
        _set_runtime_env_path(r)
        r.installed = r.slug in installed
        pending = get_pending(r.slug)
        r.error = get_startup_error(r.slug)
        if r.installed:
            r.installing = pending == "installing"
            container_running = await is_recipe_running(r.slug)
            r.ready = is_ready(r.slug) if container_running else False
            # If an action is in-flight, keep showing the right transition state
            if pending == "launching":
                r.starting = True
                r.running = False
            elif pending == "stopping":
                r.running = False
                r.starting = False
            else:
                r.starting = container_running and not r.ready
                r.running = container_running and r.ready
            if r.starting or (container_running and not r.ready):
                asyncio.create_task(start_health_check(r.slug))
        else:
            r.running = False
            r.ready = False
            r.starting = pending == "launching"
            r.installing = pending == "installing"
            if not r.starting and not r.installing:
                r.has_leftovers = await has_recipe_leftovers(r.slug)
        recipe_categories = r.categories if r.categories else [r.category]
        if category and category != "all" and category not in recipe_categories:
            continue
        if search:
            q = search.lower()
            if q not in r.name.lower() and not any(q in t for t in r.tags):
                continue
        result.append(r)
    return result


@router.get("/activity")
async def activity():
    """When each installed recipe was last used, and since when each running
    one has been up, for the Library page. Kept out of the recipe list, which
    every page polls: this one is read only where it is shown.

    Last use is the latest of a /v1 call it answered, a render it made and a
    launch, so an app (which has neither calls nor renders) counts too.
    """
    recipes = get_recipes()
    db = await get_db()
    try:
        launches = await (await db.execute(
            "SELECT slug, launched_at FROM installed_recipes WHERE launched_at IS NOT NULL")).fetchall()
        renders = await (await db.execute(
            "SELECT model, MAX(created_at) FROM media WHERE model != '' GROUP BY model")).fetchall()
        calls = await (await db.execute(
            "SELECT recipe, MAX(ts) FROM usage_events WHERE recipe != '' GROUP BY recipe")).fetchall()
        # Calls from before the recipe was recorded name only the id the
        # server reported. That is a recipe's served name, unless several
        # builds share it -- then it can't be told whose call it was.
        older = await (await db.execute(
            "SELECT model, MAX(ts) FROM usage_events WHERE recipe = '' AND model != '' GROUP BY model")).fetchall()
    finally:
        await db.close()
    by_name: dict[str, list[str]] = {}
    for r in recipes.values():
        if r.served_name:
            by_name.setdefault(r.served_name, []).append(r.slug)
    rows = [*launches, *renders, *calls,
            *((by_name[m][0], t) for m, t in older if len(by_name.get(m, [])) == 1)]
    last_used: dict[str, str] = {}
    for slug, at in rows:
        if slug in recipes and at and at > last_used.get(slug, ""):
            last_used[slug] = at
    return {"last_used": last_used, "up_since": await up_since()}


@router.post("/reload")
async def reload_registry(admin: dict = Depends(require_admin)):
    """Read registry/ again, so a recipe added or edited on disk shows up
    without restarting the daemon (a restart kills renders and builds in
    flight), and give the front door a /run/ route for any new one."""
    count = len(load_recipes())
    openai_proxy.invalidate_upstreams()
    await proxy_service.ensure_proxy()
    return {"recipes": count}


@router.put("/{slug}/favorite")
async def star(slug: str, admin: dict = Depends(require_admin)):
    if not get_recipe(slug):
        raise HTTPException(status_code=404, detail="Recipe not found")
    await favorites_service.set_favorite(slug, admin, True)
    openai_proxy.invalidate_upstreams()
    return {"favorite": favorites_service.star_of(slug)}


@router.delete("/{slug}/favorite")
async def unstar(slug: str, admin: dict = Depends(require_admin)):
    await favorites_service.set_favorite(slug, admin, False)
    openai_proxy.invalidate_upstreams()
    return {"favorite": None}


@router.get("/{slug}", response_model=Recipe)
async def get_recipe_detail(slug: str):
    recipe = get_recipe(slug)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    _set_runtime_env_path(recipe)
    installed = await get_installed_slugs()
    recipe.installed = slug in installed
    pending = get_pending(slug)
    recipe.error = get_startup_error(slug)
    if recipe.installed:
        recipe.installing = pending == "installing"
        container_running = await is_recipe_running(slug)
        recipe.ready = is_ready(slug) if container_running else False
        if pending == "launching":
            recipe.starting = True
            recipe.running = False
        elif pending == "stopping":
            recipe.running = False
            recipe.starting = False
        else:
            recipe.starting = container_running and not recipe.ready
            recipe.running = container_running and recipe.ready
        if recipe.starting or (container_running and not recipe.ready):
            asyncio.create_task(start_health_check(slug))
    else:
        recipe.running = False
        recipe.ready = False
        recipe.starting = pending == "launching"
        recipe.installing = pending == "installing"
        if not recipe.starting and not recipe.installing:
            recipe.has_leftovers = await has_recipe_leftovers(slug)
    return recipe
