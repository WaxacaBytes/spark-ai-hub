from pathlib import Path
import yaml
from daemon.config import settings
from daemon.models.recipe import Recipe


_recipes: dict[str, Recipe] = {}

# The flags each engine takes its context window from. vLLM and SGLang spell it
# `--max-model-len`, llama.cpp `-c`/`--ctx-size`.
_CONTEXT_FLAGS = {"--max-model-len", "--max-seq-len", "--max-total-tokens", "--ctx-size", "-c"}


def _context_from_compose(recipe_dir: Path) -> int | None:
    """The context window a recipe actually serves, read off its compose command.

    Only two recipes state it in yaml, but every model recipe passes it to the
    engine — and the compose file is the value that is really in force, so it
    is the one worth showing on the card. Both `--flag value` (two list items)
    and `--flag=value` spellings are read.
    """
    compose = recipe_dir / "docker-compose.yml"
    if not compose.is_file():
        return None
    try:
        with open(compose) as f:
            data = yaml.safe_load(f)
    except Exception:
        return None

    def scan(args):
        args = [str(a) for a in args]
        for i, arg in enumerate(args):
            flag, _, inline = arg.partition("=")
            if flag not in _CONTEXT_FLAGS:
                continue
            value = inline or (args[i + 1] if i + 1 < len(args) else "")
            if value.isdigit():
                return int(value)
        return None

    for service in (data or {}).get("services", {}).values():
        command = (service or {}).get("command")
        if isinstance(command, list) and (found := scan(command)):
            return found
    return None


def load_recipes() -> dict[str, Recipe]:
    global _recipes
    _recipes = {}
    registry = settings.registry_path
    if not registry.is_dir():
        return _recipes
    for recipe_dir in sorted(registry.iterdir()):
        yaml_path = recipe_dir / "recipe.yaml"
        if not yaml_path.is_file():
            continue
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            recipe = Recipe(**data)
            if recipe.context_tokens is None:
                recipe.context_tokens = (
                    data.get("context_length") or _context_from_compose(recipe_dir)
                )
            _recipes[recipe.slug] = recipe
        except Exception as e:
            print(f"[registry] Failed to load {yaml_path}: {e}")
    print(f"[registry] Loaded {len(_recipes)} recipes")
    return _recipes


def get_recipes() -> dict[str, Recipe]:
    return _recipes


def get_recipe(slug: str) -> Recipe | None:
    return _recipes.get(slug)


def get_recipe_dir(slug: str) -> Path | None:
    d = settings.registry_path / slug
    return d if d.is_dir() else None
