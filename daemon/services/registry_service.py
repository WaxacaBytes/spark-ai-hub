from pathlib import Path
import re
import yaml
from daemon.config import settings
from daemon.models.recipe import Recipe, RecipeWeights


_recipes: dict[str, Recipe] = {}

# The flags each engine takes its context window from. vLLM and SGLang spell it
# `--max-model-len`, llama.cpp `-c`/`--ctx-size`, TensorFold `--context`.
_CONTEXT_FLAGS = {"--max-model-len", "--max-seq-len", "--max-total-tokens", "--ctx-size", "-c", "--context"}

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


# The flag that names the model id a server answers to, per engine: vLLM and
# SGLang, TensorFold, llama.cpp.
_SERVED_NAME_FLAGS = {"--served-model-name", "--name", "--alias"}


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


_REPO = r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*"
# `hf download org/repo --revision sha`, the CLI spelling.
_CLI_DOWNLOAD = re.compile(rf"(?:\bhf|huggingface-cli)\s+download\s+({_REPO})([^\n;&|]*)")


def _weights_from_compose(recipe_dir: Path) -> list[RecipeWeights]:
    """The Hugging Face repos a recipe's build downloads, in download order.

    Builds call huggingface_hub's snapshot_download / hf_hub_download (some
    under an `import ... as get` alias) or the `hf download` CLI, each with
    the repo as its first argument and, when pinned, a revision.
    """
    compose = recipe_dir / "docker-compose.yml"
    if not compose.is_file():
        return []
    text = compose.read_text()
    funcs = {"snapshot_download", "hf_hub_download"} | set(
        re.findall(r"import\s+(?:snapshot_download|hf_hub_download)\s+as\s+(\w+)", text))
    call = re.compile(rf"\b(?:{'|'.join(sorted(funcs))})\(\s*(?:repo_id\s*=\s*)?['\"]({_REPO})['\"]([^)]*)")
    found = [(m.start(), m.group(1), re.search(r"revision\s*=\s*['\"]([^'\"]+)['\"]", m.group(2)))
             for m in call.finditer(text)]
    found += [(m.start(), m.group(1), re.search(r"--revision[ =]+([^\s'\"]+)", m.group(2)))
              for m in _CLI_DOWNLOAD.finditer(text)]
    weights: dict[str, RecipeWeights] = {}
    for _, repo, revision in sorted(found, key=lambda f: f[0]):
        weights.setdefault(repo, RecipeWeights(repo=repo, revision=revision.group(1) if revision else ""))
    return list(weights.values())


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
                # An engine given no context flag serves the model's native
                # window, which the recipe states as its integration's
                # max_context.
                native = (recipe.integration.max_context if recipe.integration else "").strip()
                recipe.context_tokens = (
                    data.get("context_length") or _context_from_compose(recipe_dir)
                    or (int(native) if native.isdigit() else None)
                )
            recipe.reserved_gb = _reserved_from_compose(recipe_dir)
            if not recipe.weights:
                recipe.weights = _weights_from_compose(recipe_dir)
            recipe.served_name = _flag_value(recipe_dir, _SERVED_NAME_FLAGS)
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
