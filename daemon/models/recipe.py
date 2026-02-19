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


class RecipeDocker(BaseModel):
    build: bool = False
    build_time_minutes: int = 5
    gpu: bool = True


class Recipe(BaseModel):
    name: str
    slug: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    upstream: str = ""
    fork: str = ""
    category: str = "llm"
    tags: list[str] = []
    icon: str = ""
    requirements: RecipeRequirements = RecipeRequirements()
    ui: RecipeUI = RecipeUI()
    docker: RecipeDocker = RecipeDocker()
    status: str = "experimental"
    depends_on: list[str] = []

    # runtime state (not from yaml)
    installed: bool = False
    running: bool = False
