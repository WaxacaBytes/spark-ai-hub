"""HuggingFace token storage.

Source of truth is the Hub's data dir so the token has a clean lifecycle
(uninstall removes it, backups are scoped to the Hub directory). On read we
fall back to the standard `~/.cache/huggingface/token` path if the Hub-local
file is missing — this lets users who already ran `huggingface-cli login`
reuse their existing token without re-entering it.
"""

from pathlib import Path

import httpx

from daemon.config import settings


HUB_TOKEN_PATH = settings.data_dir / "hf-token"
SYSTEM_TOKEN_PATH = Path.home() / ".cache" / "huggingface" / "token"


def read_token() -> str:
    """Return the active HF token, or empty string if none is configured."""
    for path in (HUB_TOKEN_PATH, SYSTEM_TOKEN_PATH):
        if path.is_file():
            value = path.read_text().strip()
            if value:
                return value
    return ""


def has_token() -> bool:
    return read_token() != ""


def write_token(token: str) -> None:
    HUB_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    HUB_TOKEN_PATH.write_text(token.strip())
    HUB_TOKEN_PATH.chmod(0o600)


def clear_token() -> None:
    if HUB_TOKEN_PATH.is_file():
        HUB_TOKEN_PATH.unlink()


async def check_repo_access(repo_ids: list[str]) -> list[dict]:
    """Whether the stored token may actually download each repo.

    A token is necessary but not sufficient for a gated repo: HuggingFace also
    requires the account behind it to have accepted that repo's agreement on
    the web, and there is no API to accept one — it is a real agreement, so a
    person has to read it. Until they do, every file 403s. The build only finds
    that out in the weights stage, an hour in, so this asks up front.

    Access is probed by resolving `config.json` rather than by reading the
    `gated` field on the model info, because model info comes back 200 for a
    gated repo the caller cannot download — the gate only shows on the files.
    A repo that fails for any other reason (offline, renamed, rate-limited) is
    reported accessible: this is a pre-flight courtesy, and it must never be
    the thing that stops an install that would have worked.
    """
    token = read_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    results = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for repo_id in repo_ids:
            entry = {
                "repo_id": repo_id,
                "url": f"https://huggingface.co/{repo_id}",
                "accessible": True,
            }
            try:
                r = await client.get(
                    f"https://huggingface.co/{repo_id}/resolve/main/config.json",
                    headers=headers,
                )
                if r.status_code in (401, 403):
                    entry["accessible"] = False
            except httpx.HTTPError:
                pass
            results.append(entry)
    return results
