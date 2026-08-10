from __future__ import annotations
from pydantic import BaseModel


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
