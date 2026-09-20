from pathlib import Path
import yaml
from daemon.config import settings
from daemon.models.recipe import Recipe


_recipes: dict[str, Recipe] = {}

# The flags each engine takes its context window from. vLLM and SGLang spell it
# `--max-model-len`, llama.cpp `-c`/`--ctx-size`.
_CONTEXT_FLAGS = {"--max-model-len", "--max-seq-len", "--max-total-tokens", "--ctx-size", "-c"}

# The share of the whole memory pool vLLM (and the engines built on it) and
# SGLang claim at boot.
_RESERVE_FLAGS = {"--gpu-memory-utilization", "--mem-fraction-static"}


def _compose_commands(recipe_dir: Path) -> list[list[str]]:
    """Every service's command, as a list of strings."""
    compose = recipe_dir / "docker-compose.yml"
    if not compose.is_file():
        return []
    try:
        with open(compose) as f:
            data = yaml.safe_load(f)
    except Exception:
        return []
    return [
        [str(a) for a in command]
        for service in (data or {}).get("services", {}).values()
        if isinstance(command := (service or {}).get("command"), list)
    ]


def _flag_value(recipe_dir: Path, flags: set[str]) -> str | None:
    """The value a compose command passes to any of `flags`.

    Both `--flag value` (two list items) and `--flag=value` spellings are read,
    and a `${VAR:-default}` value resolves to its default.
    """
    for args in _compose_commands(recipe_dir):
        for i, arg in enumerate(args):
            flag, _, inline = arg.partition("=")
            if flag not in flags:
                continue
            value = inline or (args[i + 1] if i + 1 < len(args) else "")
            if value.startswith("${") and ":-" in value:
                value = value.split(":-", 1)[1].rstrip("}")
            return value
    return None


def _context_from_compose(recipe_dir: Path) -> int | None:
    """The context window a recipe actually serves, read off its compose command.

    Only two recipes state it in yaml, but every model recipe passes it to the
    engine — and the compose file is the value that is really in force, so it
    is the one worth showing on the card.
    """
    value = _flag_value(recipe_dir, _CONTEXT_FLAGS) or ""
    return int(value) if value.isdigit() else None


def _total_memory_gb() -> float:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 2**20
    return 0.0


def _reserved_from_compose(recipe_dir: Path) -> float:
    """The GiB an engine claims at boot: its reserved fraction of the pool."""
    try:
        fraction = float(_flag_value(recipe_dir, _RESERVE_FLAGS) or 0)
    except ValueError:
        fraction = 0.0
    return round(fraction * _total_memory_gb(), 1)


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
            recipe.reserved_gb = _reserved_from_compose(recipe_dir)
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
