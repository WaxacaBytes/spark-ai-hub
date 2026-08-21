"""Compute the Hub's own reachable addresses at runtime.

Nothing here is hardcoded to a specific machine — every value is derived
from the box the daemon is running on. Drop Spark AI Hub on any DGX Spark
and it advertises *that* box's stable names, so `sah` clients and the
"Connect a device" UI panel always show a working address even after the
LAN IP changes (DHCP) or the project is shared to someone else's hardware.

Address preference, most stable first:
  1. mDNS  — `<hostname>.local` (avahi auto-advertises it; survives DHCP,
             works fully offline on the LAN, no DNS server needed)
  2. Tailscale MagicDNS — reachable on-LAN *and* remotely, if tailscale is up
  3. LAN IPv4 — always works right now but changes when the box moves
"""
from __future__ import annotations

import json
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


def compute_connect_info(port: int, api_key: str | None = None) -> dict:
    """Return the Hub's reachable addresses plus copy-paste client commands.

    `api_key` is the viewer's own key. It is baked into the install one-liner
    so connecting a new device stays a single paste: the key is per person,
    so it cannot be baked into the script the Hub serves publicly.
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

    primary = candidates[0]["url"] if candidates else f"http://localhost:{port}"

    return {
        "hostname": hostname,
        "port": port,
        "primary": primary,
        "candidates": candidates,
        "agents": SUPPORTED_AGENTS,
        "commands": {
            "install": (
                f"curl -fsSL {primary}/sah/install.sh | sh -s -- --key {api_key}"
                if api_key else
                f"curl -fsSL {primary}/sah/install.sh | sh"
            ),
            "set_hub": f"sah set-hub {primary}",
            "set_key": f"sah set-key {api_key}" if api_key else None,
        },
    }
