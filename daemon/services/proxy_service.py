"""The front door: one port for the whole Hub.

Every app used to publish a host port of its own — 7860 for FaceFusion, 7862
for Qwen-Image, 12156 for TRELLIS — which is fine on a LAN and useless through
a Cloudflare Tunnel, where each of those ports is another hostname and another
DNS record to keep in step with the catalog.

So the apps stop publishing ports entirely. They join one shared Docker
network, keep whatever port they like *inside* their container, and a Caddy
container in front of them maps

    :9000/run/{slug}/...  ->  spark-ai-hub-{slug}:{ui.port}

addressing them by container name. Nothing is published, nothing can collide,
and the tunnel needs exactly one origin: this port.

The daemon itself moves to `settings.port` (9010) and Caddy reverse-proxies
everything that is not an app back to it, so the Hub stays reachable at :9000
exactly as before. If Caddy is dead or misconfigured the daemon is still
directly reachable on 9010 — that is the recovery path, deliberately kept.
"""
from __future__ import annotations

import asyncio
import secrets
import shutil
from pathlib import Path

from daemon.config import settings

NETWORK = "spark-ai-hub-net"
PROXY_CONTAINER = "spark-ai-hub-proxy"
PROXY_IMAGE = "caddy:2-alpine"

# Where the generated config lands. Bind-mounted into the Caddy container.
CADDYFILE = settings.data_dir / "Caddyfile"

# The app prefix. `/run/{slug}/` is stripped before the request is forwarded,
# so the container sees the same paths it would serve at the root. Not `/app/`:
# that is the Hub's own SPA route for a recipe detail page, and proxying over
# it would mean reloading a detail page served you the app instead.
APP_PREFIX = "/run"


def app_url(slug: str) -> str:
    """The Hub-relative URL an app is served at. Root-relative on purpose:
    it works through the tunnel, over Tailscale and on the LAN without the
    daemon having to guess its own public hostname."""
    return f"{APP_PREFIX}/{slug}/"


async def _run(*args: str, check: bool = False) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode(errors="replace").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {text}")
    return proc.returncode or 0, text


async def ensure_network() -> None:
    """Create the shared app network if it isn't there.

    Recipes declare it as `external: true`, so `docker compose down` never
    deletes it out from under the other apps.
    """
    code, _ = await _run("docker", "network", "inspect", NETWORK)
    if code == 0:
        return
    await _run("docker", "network", "create", NETWORK)


PROBE_HEADER = "X-Sah-Probe"
_PROBE_TOKEN_FILE = settings.data_dir / "proxy-probe-token"


def probe_token() -> str:
    """Shared secret that lets the daemon's health check reach an app.

    The apps sit behind `forward_auth`, so an anonymous probe is answered by a
    302 to the sign-in page -- which looks like success to any "did I get a
    non-error status" check and would mark a stopped app ready. Rather than
    weaken that test, the probe carries a token Caddy recognises and routes
    straight through, so a health check sees the app's real answer or a 502.
    """
    if _PROBE_TOKEN_FILE.is_file():
        token = _PROBE_TOKEN_FILE.read_text().strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    _PROBE_TOKEN_FILE.write_text(token)
    _PROBE_TOKEN_FILE.chmod(0o600)
    return token


def _container_name(slug: str) -> str:
    """The name Caddy dials, read off the recipe's own compose file.

    Not derived from the slug: four recipes name their container for the app
    rather than the recipe (facefusion-spark -> spark-ai-hub-facefusion,
    trellis2-spark -> spark-ai-hub-trellis2, and so on), and guessing would
    route those to a host that does not exist.
    """
    from daemon.services.registry_service import get_recipe_dir

    recipe_dir = get_recipe_dir(slug)
    if recipe_dir:
        compose = recipe_dir / "docker-compose.yml"
        if compose.is_file():
            for line in compose.read_text().split("\n"):
                if line.startswith("    container_name:"):
                    return line.split(":", 1)[1].strip()
    return f"spark-ai-hub-{slug}"


def _proxied_recipes() -> list[tuple[str, str, int, bool]]:
    """(slug, container name, container port, strip prefix?) for /run/."""
    # Imported here: registry_service imports config, and this module is
    # imported from main's lifespan before load_recipes() has run.
    from daemon.services.registry_service import get_recipes

    out = []
    for slug, recipe in sorted(get_recipes().items()):
        ui = recipe.ui
        if ui and ui.proxy and ui.type == "web":
            out.append((slug, _container_name(slug), ui.port, ui.strip_prefix))
    return out


def render_caddyfile() -> str:
    """Build the whole front-door config from the recipe registry."""
    host = "host.docker.internal"
    daemon = f"{host}:{settings.port}"

    lines = [
        "# Generated by Spark AI Hub — edits here are overwritten on restart.",
        "{",
        "\tadmin 0.0.0.0:2019",
        "\tauto_https off",
        "}",
        "",
        f":{settings.public_port} {{",
    ]

    token = probe_token()

    for slug, container, port, strip in _proxied_recipes():
        upstream = f"{container}:{port}"
        lines.append(f"\t# {slug}")
        # The daemon's own health probe, before the guarded route so it wins.
        lines += [
            f"\t@probe-{slug} {{",
            f"\t\tpath {APP_PREFIX}/{slug}/*",
            f'\t\theader {PROBE_HEADER} "{token}"',
            "\t}",
            f"\thandle @probe-{slug} {{",
        ]
        if strip:
            lines.append(f"\t\turi strip_prefix {APP_PREFIX}/{slug}")
        lines += [
            f"\t\treverse_proxy {upstream}",
            "\t}",
        ]
        # handle_path only matches the trailing-slash form, so send the bare
        # /run/{slug} there first or the first click 404s.
        lines.append(f"\tredir {APP_PREFIX}/{slug} {APP_PREFIX}/{slug}/ 308")
        # handle_path strips the prefix; handle leaves it on for the apps that
        # want to see it.
        directive = "handle_path" if strip else "handle"
        lines.append(f"\t{directive} {APP_PREFIX}/{slug}/* {{")
        if settings.auth_enabled:
            # The apps have no accounts of their own. ComfyUI or a Gradio file
            # box on a public tunnel is an open GPU, so the Hub's own session
            # gates them: /api/auth/me answers 200 signed-in, 401 otherwise.
            lines += [
                f"\t\tforward_auth {daemon} {{",
                "\t\t\turi /api/auth/me",
                "\t\t\t@denied status 401 403",
                "\t\t\thandle_response @denied {",
                "\t\t\t\tredir * / 302",
                "\t\t\t}",
                "\t\t}",
            ]
        lines.append(f"\t\treverse_proxy {upstream}")
        lines.append("\t}")
        lines.append("")

    lines += [
        "\t# Everything else is the Hub itself.",
        "\thandle {",
        f"\t\treverse_proxy {daemon}",
        "\t}",
        "}",
        "",
    ]
    return "\n".join(lines)


def write_caddyfile() -> bool:
    """Write the config. Returns True if it changed."""
    text = render_caddyfile()
    if CADDYFILE.is_file() and CADDYFILE.read_text() == text:
        return False
    CADDYFILE.parent.mkdir(parents=True, exist_ok=True)
    CADDYFILE.write_text(text)
    return True


async def _container_state() -> str:
    """"running" / "exited" / "" (absent)."""
    code, out = await _run(
        "docker", "inspect", "-f", "{{.State.Status}}", PROXY_CONTAINER
    )
    return out if code == 0 else ""


async def reload_proxy() -> None:
    """Hot-reload Caddy in place. No dropped connections, no restart."""
    await _run(
        "docker", "exec", PROXY_CONTAINER,
        "caddy", "reload", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile",
    )


async def ensure_proxy() -> None:
    """Bring the front door up, or reload it if it is already up.

    Called at daemon startup and whenever the registry changes. Failure is
    logged, never raised: a broken proxy must not stop the daemon from
    booting, or there would be nothing left to fix it from.
    """
    if not settings.proxy_enabled:
        return
    if not shutil.which("docker"):
        print("[proxy] docker not found — apps will not be reachable under /run/")
        return

    try:
        await ensure_network()
        changed = write_caddyfile()
        state = await _container_state()

        if state == "running":
            if changed:
                await reload_proxy()
                print("[proxy] config changed, reloaded")
            return

        if state:  # exists but stopped — config may be stale, recreate
            await _run("docker", "rm", "-f", PROXY_CONTAINER)

        code, out = await _run(
            "docker", "run", "-d",
            "--name", PROXY_CONTAINER,
            "--restart", "unless-stopped",
            "--network", NETWORK,
            # host-gateway is how the container reaches the daemon on the host.
            "--add-host", "host.docker.internal:host-gateway",
            "-p", f"{settings.public_port}:{settings.public_port}",
            "-v", f"{CADDYFILE}:/etc/caddy/Caddyfile:ro",
            PROXY_IMAGE,
        )
        if code != 0:
            print(f"[proxy] failed to start: {out}")
        else:
            print(f"[proxy] listening on :{settings.public_port}")
    except Exception as exc:  # noqa: BLE001 - see docstring
        print(f"[proxy] {exc}")


async def health_url(slug: str, path: str) -> str:
    """Where to poll to decide an app is up.

    Deliberately through the proxy rather than at the container: it proves the
    path the user is about to click actually serves, not merely that something
    is listening inside the container.
    """
    return f"http://127.0.0.1:{settings.public_port}{APP_PREFIX}/{slug}{path}"
