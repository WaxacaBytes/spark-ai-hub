from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # The daemon's own bind port. Caddy sits in front on `public_port` and
    # proxies everything back here, so 9010 is an implementation detail --
    # except as the recovery door when the proxy itself is broken.
    port: int = 9010
    host: str = "0.0.0.0"

    # The one port the outside world sees: the Hub UI, its API, /v1, and every
    # app under /run/{slug}/. A Cloudflare Tunnel needs this origin and no
    # other. Set proxy_enabled=false to go back to the daemon serving :9000
    # itself, with apps unreachable (they no longer publish host ports).
    public_port: int = 9000
    proxy_enabled: bool = True
    base_dir: Path = Path(__file__).resolve().parent.parent
    registry_path: Path = Path(__file__).resolve().parent.parent / "registry" / "recipes"
    data_dir: Path = Path(__file__).resolve().parent.parent / "data"
    db_path: Path = Path(__file__).resolve().parent.parent / "data" / "spark-ai-hub.db"

    # Upstream OpenAI-compatible LLM endpoint (vLLM heavy slot)
    upstream_openai_url: str = "http://localhost:9001/v1"

    # ── Authentication ──
    # Set SPARK_AI_HUB_AUTH_ENABLED=false only for a Hub on a trusted LAN
    # that you are certain is not reachable from the internet.
    auth_enabled: bool = True
    session_cookie_name: str = "spark_ai_hub_session"
    session_ttl_days: int = 30
    # Terminating TLS elsewhere (Cloudflare Tunnel, Caddy, nginx) means the
    # daemon itself only ever sees plain HTTP. The Secure cookie flag is
    # therefore decided per request from X-Forwarded-Proto, so the same Hub
    # serves a hardened cookie through the tunnel and a working one over
    # http://<lan-ip>:9000. Set this to force Secure on in every case.
    force_secure_cookie: bool = False

    model_config = {"env_prefix": "SPARK_AI_HUB_"}


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
