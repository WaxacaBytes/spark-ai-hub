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

Every candidate also carries a `scope` (lan / vpn / remote), and the reply
says whether the *caller* is on this box's own LAN (`client_local`) — the
daemon can see the client's source address, so this is measured, not
guessed. A client that is told it is local can prefer the LAN addresses
over a Tailscale or tunnel hostname that would otherwise send its traffic
out to the internet and back.
"""
from __future__ import annotations

import ipaddress
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


# ── Is the caller on this box's LAN? ────────────────────────────────────────
#
# A `sah` client saves the Hub's addresses once and then keeps using them, so
# it can end up talking to a Tailscale or tunnel hostname long after it has
# moved back onto the same LAN as the Hub — every token round-tripping through
# the internet for a machine two metres away. It cannot work that out on its
# own (its own IP says nothing about where the Hub is), but the daemon can:
# it sees the source address of the request and knows its own interfaces.

_SCOPE_BY_KIND = {"mdns": "lan", "ip": "lan", "tailscale": "vpn", "origin": "remote"}


def _local_networks() -> list[ipaddress.IPv4Network]:
    """Every IPv4 subnet this box has an interface on.

    Read from `ip -o -4 addr`, which reports the prefix length — a client on
    192.168.3.x is only "local" if this box actually holds 192.168.3.0/24, and
    assuming /24 around our own address would be a guess. Loopback is dropped;
    docker/bridge subnets are kept, since a caller from one of those is a
    container on this very host and could not be more local.
    """
    try:
        out = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []
    nets: list[ipaddress.IPv4Network] = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        cidr = parts[parts.index("inet") + 1]
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if isinstance(net, ipaddress.IPv4Network) and not net.is_loopback:
            nets.append(net)
    return nets


def client_ip(request) -> str | None:
    """Caller's address, preferring what a tunnel or reverse proxy says it was.

    Behind Caddy (the Hub's own front door) every request arrives from a docker
    bridge address, which would make every caller in the world look local; the
    forwarded headers carry the address that actually matters.
    """
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        if val := request.headers.get(header):
            candidate = val.split(",")[0].strip()
            if candidate:
                return candidate
    return request.client.host if request.client else None


def is_local_client(ip: str | None) -> bool:
    """True when `ip` sits inside one of this box's own IPv4 subnets."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        # A Tailscale or public IPv6 caller is not on the LAN; a loopback one
        # (::1) is this box itself, which is as local as it gets.
        return addr.is_loopback
    if addr.is_loopback:
        return True
    return any(addr in net for net in _local_networks())


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
    port: int,
    api_key: str | None = None,
    origin: str | None = None,
    caller_ip: str | None = None,
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

    `caller_ip` (see `client_ip`) decides `client_local`: whether the caller is
    on one of this box's own subnets. The displayed order is left alone — the
    address someone is reading the page on is still the safest one to paste —
    but a `sah` client reads `client_local` and `scope` and re-sorts its saved
    candidates so a machine on the LAN stops routing through the internet.
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
            "scope": _SCOPE_BY_KIND.get(kind, "remote"),
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
            "scope": "remote",
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

    # Where the caller is standing. `local_url` is the address a client on this
    # LAN should be using right now — recomputed on every request, so a client
    # that asks again after a DHCP lease change gets the new one for free.
    local = [c for c in candidates if c["scope"] == "lan"]
    return {
        "hostname": hostname,
        "port": port,
        "origin": origin,
        "external_origin": external,
        "primary": primary,
        "candidates": candidates,
        "client_ip": caller_ip,
        "client_local": is_local_client(caller_ip),
        "local_url": next((c["url"] for c in local if c["kind"] == "ip"), None),
        "local_urls": [c["url"] for c in local],
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
