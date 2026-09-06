"""Compute the Hub's own reachable addresses at runtime.

Nothing here is hardcoded to a specific machine — every value is derived
from the box the daemon is running on. Drop Spark AI Hub on any DGX Spark
and it advertises *that* box's stable names, so `sah` clients and the
"Connect a device" UI panel always show a working address even after the
LAN IP changes (DHCP) or the project is shared to someone else's hardware.

Address preference, most stable first:
  0. The address the caller actually used — read off the request itself.
     Nothing the daemon can compute beats it: behind a Cloudflare Tunnel,
     an ngrok/Tailscale Funnel or someone else's nginx, the Hub is reached
     at a hostname the box has never heard of and cannot guess. The client
     is holding the one true answer, so ask the request instead of guessing.
  1. mDNS  — `<hostname>.local` (avahi auto-advertises it; survives DHCP,
             works fully offline on the LAN, no DNS server needed)
  2. Tailscale MagicDNS — reachable on-LAN *and* remotely, if tailscale is up
  3. LAN IPv4 — always works right now but changes when the box moves
"""
from __future__ import annotations

import json
import re
import socket
import subprocess


# Agents that `sah` can wire to the served model. Mirrors the sah CLI's
# integration list — the modal renders these so users know what's supported.
SUPPORTED_AGENTS = [
    {"name": "OpenCode", "kind": "CLI", "command": "sah opencode"},
    {"name": "Codex", "kind": "CLI", "command": "sah codex"},
    {"name": "Claude Code", "kind": "CLI", "command": "sah claude"},
    {"name": "Qwen Code", "kind": "CLI", "command": "sah qwen"},
    {"name": "Hermes", "kind": "CLI", "command": "sah hermes"},
    {"name": "OpenClaw", "kind": "CLI", "command": "sah openclaw"},
    {"name": "Pi", "kind": "CLI", "command": "sah pi"},
    {"name": "Claude Desktop", "kind": "Desktop", "command": "sah claude-desktop --install"},
    {"name": "Hermes Desktop", "kind": "Desktop", "command": "sah hermes-desktop"},
    {"name": "Other OpenAI/Anthropic apps", "kind": "Any", "command": "sah env"},
]


def _short_hostname() -> str:
    name = socket.gethostname().split(".")[0].strip()
    return name or "spark-ai-hub"


def _primary_lan_ip() -> str | None:
    """Source IP the kernel would use to reach the outside world.

    UDP `connect` picks a route and source address without sending any
    packets, so this works offline as long as a default route exists.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(_short_hostname())
        except OSError:
            return None
    finally:
        s.close()
    if ip.startswith("127."):
        return None
    return ip


def _tailscale_info() -> dict | None:
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    self_ = data.get("Self") or {}
    dns = (self_.get("DNSName") or "").rstrip(".")
    ips = self_.get("TailscaleIPs") or []
    if not dns and not ips:
        return None
    return {"dns": dns, "ips": ips, "online": bool(self_.get("Online"))}


# ── The address the caller actually used ────────────────────────────────────
#
# Everything below this line is a guess about how the outside world reaches
# this box. The request itself is not a guess: whatever host the browser or
# `curl` put in the URL bar is, by construction, an address that works from
# where that client is standing. Behind a tunnel it is the *only* one that
# does — the box has no way to learn its own public hostname.

# A host we are willing to paste into a shell command the user will run.
# Host headers are client-controlled, and `commands.install` ends in `| sh`,
# so anything that is not plainly a hostname/IP (plus optional port) is
# dropped rather than escaped. Nobody can hurt anyone but themselves with a
# forged Host here, but a copy-pasteable command is no place to find out.
_SAFE_ORIGIN = re.compile(r"^https?://(?:[A-Za-z0-9._\-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?$")


def _forwarded(header: str) -> dict[str, str]:
    """Parse the first element of an RFC 7239 `Forwarded:` header."""
    out: dict[str, str] = {}
    for part in header.split(",")[0].split(";"):
        key, _, val = part.strip().partition("=")
        if key and val:
            out[key.strip().lower()] = val.strip().strip('"')
    return out


def request_scheme(request) -> str:
    """http or https as the *client* saw it.

    Caddy and cloudflared both terminate TLS and speak plain HTTP to the
    daemon, so `request.url.scheme` is always "http" behind them and only the
    forwarded header knows the truth.
    """
    fwd = _forwarded(request.headers.get("forwarded", ""))
    proto = fwd.get("proto") or request.headers.get("x-forwarded-proto", "")
    proto = proto.split(",")[0].strip().lower()
    if proto in ("http", "https"):
        return proto
    return request.url.scheme or "http"


def request_origin(request) -> str | None:
    """`scheme://host[:port]` exactly as the client addressed this Hub.

    Returns None when no usable host is on the request (a raw socket probe,
    HTTP/1.0 with no Host) or when the host looks forged, so callers keep
    their locally-derived fallback instead of trusting garbage.
    """
    fwd = _forwarded(request.headers.get("forwarded", ""))
    host = (
        fwd.get("host")
        or request.headers.get("x-forwarded-host", "").split(",")[0]
        or request.headers.get("host", "")
    ).strip()
    if not host:
        return None
    origin = f"{request_scheme(request)}://{host}"
    return origin if _SAFE_ORIGIN.match(origin) else None


def compute_connect_info(
    port: int, api_key: str | None = None, origin: str | None = None
) -> dict:
    """Return the Hub's reachable addresses plus copy-paste client commands.

    `api_key` is the viewer's own key. It is baked into the install one-liner
    so connecting a new device stays a single paste: the key is per person,
    so it cannot be baked into the script the Hub serves publicly.

    `origin` is where the caller reached us (see `request_origin`). If it is
    not one of the addresses this box can see on itself, the Hub is being
    fronted by something the box knows nothing about — a tunnel, a reverse
    proxy, a custom domain — and that address is the only one the client can
    be told to use, so it leads the list.
    """
    hostname = _short_hostname()
    candidates: list[dict] = []

    def add(host: str | None, kind: str, note: str, recommended: bool = False):
        if not host:
            return
        url = f"http://{host}:{port}"
        if any(c["url"] == url for c in candidates):
            return
        candidates.append({
            "host": host,
            "url": url,
            "kind": kind,
            "note": note,
            "recommended": recommended,
        })

    # 1. mDNS — the stable, offline, zero-config LAN name.
    add(f"{hostname}.local", "mdns",
        "Stable on your LAN — survives IP changes, works offline.",
        recommended=True)

    # 2. Tailscale MagicDNS — stable on-LAN and remotely.
    ts = _tailscale_info()
    if ts and ts.get("dns"):
        add(ts["dns"], "tailscale",
            "Works on your LAN and remotely (requires Tailscale on the client).")

    # 3. Current LAN IP — works now, but changes when the box moves.
    add(_primary_lan_ip(), "ip",
        "Reachable now, but this address changes when the server moves networks.")

    # 0. The address in the caller's URL bar. Already in the list when they
    #    came in over one of the names above; otherwise something is fronting
    #    the Hub and this is the only address that reaches it from out there.
    origin = (origin or "").rstrip("/") or None
    external = bool(origin) and not any(c["url"] == origin for c in candidates)
    if external:
        for c in candidates:
            c["recommended"] = False
        candidates.insert(0, {
            "host": origin.split("://", 1)[1],
            "url": origin,
            "kind": "origin",
            "note": "The address you are using right now — works wherever this "
                    "page loaded from.",
            "recommended": True,
        })

    # What to hand out for "point a client at this Hub". The origin wins when
    # it is external; otherwise the stable LAN name does, because a client
    # that saves the mDNS name survives the DHCP lease the IP does not.
    primary = candidates[0]["url"] if candidates else f"http://localhost:{port}"
    # ...but the installer has to be *fetched* before it can save anything, so
    # its curl must use the address the user is provably able to reach: the
    # one they are reading this on. install.sh persists the whole candidate
    # list the Hub injects, so the stable names are still what sah falls back
    # to later.
    install_from = origin or primary

    return {
        "hostname": hostname,
        "port": port,
        "origin": origin,
        "external_origin": external,
        "primary": primary,
        "candidates": candidates,
        "agents": SUPPORTED_AGENTS,
        "commands": {
            "install": (
                f"curl -fsSL {install_from}/sah/install.sh | sh -s -- --key {api_key}"
                if api_key else
                f"curl -fsSL {install_from}/sah/install.sh | sh"
            ),
            "set_hub": f"sah set-hub {primary}",
            "set_key": f"sah set-key {api_key}" if api_key else None,
        },
    }
