from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 9000
    host: str = "0.0.0.0"
    base_dir: Path = Path(__file__).resolve().parent.parent
    registry_path: Path = Path(__file__).resolve().parent.parent / "registry" / "recipes"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    db_path: Path = Path(__file__).resolve().parent.parent / "data" / "sparkforge.db"

    model_config = {"env_prefix": "SPARKFORGE_"}


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
