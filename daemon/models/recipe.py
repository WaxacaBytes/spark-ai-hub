from __future__ import annotations
from pydantic import BaseModel, computed_field


# What a served model can actually do, derived from the recipe's hand-written
# tags. This is the single source of truth: the OpenAI proxy reports it to
# clients on /v1/models, and the detail page shows the same list to the user so
# they know what their coding agent is being told.
TAG_CAPABILITIES = {
    "vision": "vision",
    "multimodal": "vision",
    "video": "video",
    "tool-use": "tools",
    "reasoning": "thinking",
}


class RecipeRequirements(BaseModel):
    min_memory_gb: int = 8
    recommended_memory_gb: int | None = None
    disk_gb: int = 10
    cuda_compute: str = "12.1"


class RecipeUI(BaseModel):
    type: str = "web"
    port: int = 8080
    path: str = "/"
    health_path: str | None = None


class RecipeDocker(BaseModel):
    build: bool = False
    gpu: bool = True


class RecipeIntegration(BaseModel):
    api_url: str = ""
    model_id: str = ""
    api_key: str = ""
    max_context: str = ""
    max_output_tokens: str = ""
    curl_example: str = ""


class RecipeBackdrop(BaseModel):
    """A second picture, framed for the wide hero band.

    The card is a tall crop with its title across the foot; the hero is a
    2.56:1 band with the title set into its left third. One picture rarely
    suits both, so a recipe may name a hero of its own here. Omit it and the
    hero is cropped from `RecipeCover.image` as before.
    """
    image: str = ""
    fit: str = ""
    focus_x: float | None = None
    focus_y: float | None = None


class RecipeCover(BaseModel):
    """Cover art, declared by the recipe rather than mapped in code.

    `image` names a file in registry/covers/. Recipes may share one — every
    build of a model at a given parameter size points at the same image, and
    that is how they end up looking alike. A recipe contributed from outside
    ships its own image alongside its yaml, so nothing about it lives here.
    """
    image: str = ""
    caption: str = ""            # what the picture shows and why
    fit: str = "cover"           # "cover" crops to fill, "contain" shows it whole
    backdrop_fit: str = ""       # override `fit` for the wide hero; portrait
                                 # sources usually want "contain" there
    focus_x: float = 0.5         # where to centre a "cover" crop, 0..1 across
    focus_y: float = 0.5         # and down. A wide panorama usually needs this
                                 # or the poster crops away the subject.
    credit: str = ""             # "File · Author · CC BY 4.0", for CC sources
    source: str = ""             # URL the image came from
    grade: bool = True           # apply the cinematic colour grade
    backdrop: RecipeBackdrop | None = None   # optional hero-only picture


class Recipe(BaseModel):
    name: str
    slug: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    website: str = ""
    upstream: str = ""
    fork: str = ""
    category: str = "llm"
    categories: list[str] = []
    tags: list[str] = []
    icon: str = ""
    logo: str = ""
    cover: RecipeCover = RecipeCover()
    requirements: RecipeRequirements = RecipeRequirements()
    ui: RecipeUI = RecipeUI()
    docker: RecipeDocker = RecipeDocker()
    integration: RecipeIntegration | None = None
    source: str = "community"  # spark-ai-hub | official | community
    status: str = "experimental"
    release_date: str = ""  # YYYY-MM or YYYY-MM-DD, model/tool original release date used for catalog ordering
    # model metadata (LLM recipes) — powers the catalog badges and sort controls
    engine: str = ""                      # serving engine: vLLM | llama.cpp | Atlas
    params_b: float | None = None         # total parameters, in billions
    active_params_b: float | None = None  # active parameters per token, MoE only
    arch: str = ""                        # "dense" | "moe"
    quantization: str = ""                # BF16 | FP8 | NVFP4 | INT4 | MXFP4 | Q8_0 | IQ2_M | ...
    weights_gb: float | None = None       # actual weight download size on disk, in GB
    depends_on: list[str] = []
    requires_hf_token: bool = False
    runtime_env_path: str = ""
    tokens_per_second: float | None = None

    # runtime state (not from yaml)
    installed: bool = False
    running: bool = False
    ready: bool = False
    starting: bool = False
    installing: bool = False
    has_leftovers: bool = False

    @computed_field
    @property
    def capabilities(self) -> list[str]:
        """Capabilities reported for this model, or [] if it serves no API."""
        if self.integration is None:
            return []
        found = {"completion"}
        for tag in self.tags:
            if capability := TAG_CAPABILITIES.get(tag):
                found.add(capability)
        return sorted(found)
