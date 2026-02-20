from fastapi import APIRouter, HTTPException

from daemon.models.recipe import Recipe
from daemon.services.registry_service import get_recipes, get_recipe
from daemon.services.docker_service import get_installed_slugs, is_recipe_running, is_ready

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


@router.get("", response_model=list[Recipe])
async def list_recipes(category: str | None = None, search: str | None = None):
    recipes = list(get_recipes().values())
    installed = await get_installed_slugs()

    result = []
    for r in recipes:
        r.installed = r.slug in installed
        if r.installed:
            r.running = await is_recipe_running(r.slug)
            r.ready = is_ready(r.slug) if r.running else False
        if category and category != "all" and r.category != category:
            continue
        if search:
            q = search.lower()
            if q not in r.name.lower() and not any(q in t for t in r.tags):
                continue
        result.append(r)
    return result


@router.get("/{slug}", response_model=Recipe)
async def get_recipe_detail(slug: str):
    recipe = get_recipe(slug)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    installed = await get_installed_slugs()
    recipe.installed = slug in installed
    if recipe.installed:
        recipe.running = await is_recipe_running(slug)
        recipe.ready = is_ready(slug) if recipe.running else False
    return recipe
