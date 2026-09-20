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

# What an image or video model makes, from the same hand-written tags that tell
# the Hub's MCP tools which recipes can answer which call. Shown on the detail
# page so nobody has to read tags to learn a model generates but cannot edit.
MEDIA_CAPABILITIES = {
    "text-to-image": "image-generation",
    "image-edit": "image-editing",
    # Several input images plus the text instruction in one request (combine a
    # person from one photo with a background from another, and so on).
    "multi-image": "multi-image-input",
    "text-to-video": "text-to-video",
    "image-to-video": "image-to-video",
    "video-edit": "video-editing",
    "text-to-music": "music-generation",
}


class RecipeRequirements(BaseModel):
    min_memory_gb: int = 8
    recommended_memory_gb: int | None = None
    disk_gb: int = 10
    cuda_compute: str = "12.1"


class RecipeUI(BaseModel):
    type: str = "web"
    # The port *inside* the container. Proxied apps publish nothing to the
    # host, so this never has to be unique across the catalog -- two apps both
    # serving 7860 internally is fine, they are told apart by container name.
    port: int = 8080
    path: str = "/"
    health_path: str | None = None
    # True: served at /run/{slug}/ through the Hub's front door, so the whole
    # catalog lives behind one port. False: the recipe still publishes its own
    # host port and is reached directly.
    proxy: bool = False
    # True (the default): the /run/{slug} prefix is stripped before the request
    # reaches the container, so the app serves the same paths it would at the
    # root -- which is what Gradio's GRADIO_ROOT_PATH expects. False: the app
    # is handed the full path and deals with the prefix itself. Needed by apps
    # built on `FastAPI(root_path=...)`, where Starlette strips the prefix
    # itself and a route only matches if the prefix is still on the request.
    strip_prefix: bool = True


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


class RecipeImageDefaults(BaseModel):
    """Generation settings the image model's own documentation recommends.

    Applied by the Hub's image tools whenever the agent leaves them unset, and
    reported by `list_image_models`, so an agent never has to guess that a
    step-distilled model wants 4 steps while a full diffusion model wants 50.
    Copy the numbers from the model card and cite it in `source`.
    """
    steps: int | None = None
    guidance_scale: float | None = None
    true_cfg_scale: float | None = None
    # A named sampler preset, for models that derive steps and guidance from it
    # and refuse them set directly (Ideogram 4). When set, steps are not sent.
    preset: str | None = None
    notes: str = ""
    source: str = ""


class RecipeVideoDefaults(BaseModel):
    """Generation settings a video model's documentation recommends."""
    steps: int | None = None
    guidance_scale: float | None = None
    fps: int | None = None
    seconds: int | None = None
    # Frame-count models (Wan VACE) take num_frames, which must be 4k+1; when
    # set, a requested length in seconds is converted to frames instead.
    num_frames: int | None = None
    size: str | None = None      # "WIDTHxHEIGHT", landscape; flipped for 9:16
    flow_shift: float | None = None
    # Model-specific request options, sent as the server's extra_params JSON.
    # A "duration" key there takes the requested length instead of `seconds`
    # (MiniMax-H3 reads its clip length from extra_params.duration).
    extra_params: dict | None = None
    # Also send the chosen aspect_ratio ("16:9"/"9:16") as its own field, for
    # servers that require it alongside width/height (MiniMax-H3 t2va).
    send_aspect_ratio: bool = False
    notes: str = ""
    source: str = ""


class RecipeAudioDefaults(BaseModel):
    """Generation settings a music model's documentation recommends."""
    seconds: int | None = None
    notes: str = ""
    source: str = ""


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
    image_defaults: RecipeImageDefaults | None = None  # image models only
    video_defaults: RecipeVideoDefaults | None = None  # video models only
    audio_defaults: RecipeAudioDefaults | None = None  # music models only
    source: str = "community"  # spark-ai-hub | official | community
    status: str = "experimental"
    release_date: str = ""  # YYYY-MM or YYYY-MM-DD, model/tool original release date used for catalog ordering
    # model metadata (LLM recipes) — powers the catalog badges and sort controls
    engine: str = ""                      # serving engine: vLLM | llama.cpp | Atlas
    params_b: float | None = None         # total parameters, in billions
    active_params_b: float | None = None  # active parameters per token, MoE only
    arch: str = ""                        # "dense" | "moe"
    quantization: str = ""                # BF16 | FP8 | NVFP4 | INT4 | MXFP4 | Q8_0 | IQ2_M | ...
    # What actually drafts at serve time, read off the docker-compose command
    # itself (--speculative-config / --speculative-algorithm) rather than the
    # slug or tags — those have been wrong often enough (a slug named "dspark"
    # whose active config is EAGLE; drafters with no matching tag at all) that
    # only the literal runtime flag can be trusted. Empty if no drafter runs.
    speculative_method: str = ""          # mtp | dflash | dspark | eagle | ...
    # Published Artificial Analysis Intelligence Index score for the base
    # model (artificialanalysis.ai) — one capability score per model group,
    # shared across every quant/drafter build of it. None where no
    # independent score is published (community finetunes, or a model too
    # new to have been evaluated).
    artificial_analysis_index: float | None = None
    weights_gb: float | None = None       # actual weight download size on disk, in GB
    depends_on: list[str] = []
    requires_hf_token: bool = False
    # HuggingFace repos behind a terms gate, which a token alone does not open:
    # the account holding it has to have accepted that repo's agreement, and
    # until it has, every download 403s. Nothing about the token says so, so
    # declaring the repos here is what lets the Hub check access before a build
    # spends an hour to fail on the weights stage.
    gated_repos: list[str] = []
    runtime_env_path: str = ""
    tokens_per_second: float | None = None
    # Two sustained rates, not a number and a spike. Throughput tracks how much
    # of the output is copied from the prompt rather than invented, because that
    # is what sets a speculative drafter's acceptance: `tokens_per_second` is
    # writing new text (the three mixed prompts), `tokens_per_second_editing` is
    # the same server reproducing a document with a small change applied.
    # Measured across four edit workloads on Qwen3.8-27B NVFP4 DSpark, the kind
    # of text barely matters — prose 57.4, markdown 56.3, repetitive code 58.1 —
    # so this is an editing figure, not a code figure. What does matter is how
    # much new material the edit introduces: an edit that writes a fresh
    # docstring per function fell to 48.5.
    tokens_per_second_editing: float | None = None
    editing_workload: str = ""            # which edit was measured, e.g. "code-edit"
    # Per-workload detail behind those two numbers, keyed by the labels the
    # benchmark snippet prints: code, explainer, prose, code-edit. Shown on the
    # detail page so a range can be checked rather than taken on faith.
    benchmarks: dict[str, float] = {}
    context_tokens: int | None = None     # served context window; read off the
                                          # compose command when not declared
    # vLLM and SGLang claim a fixed share of the whole memory pool at boot and
    # refuse to start without it free. That share in GiB, read off the compose
    # command by the registry loader; 0 for engines that claim no fixed share.
    reserved_gb: float = 0.0

    # runtime state (not from yaml)
    installed: bool = False
    running: bool = False
    ready: bool = False
    starting: bool = False
    installing: bool = False
    has_leftovers: bool = False
    # Why the last launch died, in words. Set when a container exits before it
    # ever answers its health check; cleared on the next launch.
    error: str | None = None

    @property
    def memory_gb(self) -> float:
        """What the app holds once it is up, in GiB."""
        return max(float(self.requirements.min_memory_gb), self.reserved_gb)

    @property
    def is_llm(self) -> bool:
        """An OpenAI-compatible model server the Hub's /v1 routes to."""
        return self.category == "llm" and bool(self.ui and self.ui.type == "api-only")

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

    @computed_field
    @property
    def media_capabilities(self) -> list[str]:
        """What an image/video model makes; [] for everything else."""
        return [cap for tag, cap in MEDIA_CAPABILITIES.items() if tag in self.tags]

    @computed_field
    @property
    def app_url(self) -> str:
        """Where to send the browser when the user clicks Open.

        Root-relative for proxied apps, so the same string works through a
        Cloudflare Tunnel, over Tailscale and on the LAN without the daemon
        having to know its own public hostname. Empty for everything else --
        the model servers have no UI of their own, and the frontend keeps its
        existing host:port fallback for any recipe that still publishes a port.
        """
        if self.ui and self.ui.proxy and self.ui.type == "web":
            return f"/run/{self.slug}/"
        # A proxied API server publishes no host port, so the host:port
        # fallback would be a dead link; point at its API path instead.
        if self.ui and self.ui.proxy and self.ui.type == "api-only":
            return f"/run/{self.slug}{self.ui.path}"
        return ""
