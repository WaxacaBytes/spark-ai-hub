from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 9000
    host: str = "0.0.0.0"
    base_dir: Path = Path(__file__).resolve().parent.parent
    registry_path: Path = Path(__file__).resolve().parent.parent / "registry" / "recipes"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    db_path: Path = Path(__file__).resolve().parent.parent / "data" / "spark-ai-hub.db"

    # Upstream OpenAI-compatible LLM endpoint (vLLM heavy slot)
    upstream_openai_url: str = "http://localhost:9001/v1"

    model_config = {"env_prefix": "SPARK_AI_HUB_"}


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
