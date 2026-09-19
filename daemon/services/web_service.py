"""Web search and page fetching for the MCP tools, run from the Spark.

Search goes to the SearXNG recipe through the front door's probe route, the
same way the image tools reach their models, so no search engine key and no
search middleman is involved: SearXNG asks the engines its compose file
allows, with no account, cookie or key.

Fetch goes straight from the Spark to the page. It is HTTPS only, sends no
cookies and no Referer, and refuses anything that resolves to a private
address, since any signed-in user can make the daemon fetch a URL.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
import urllib.parse

import aiohttp
from bs4 import BeautifulSoup
from markdownify import markdownify

from daemon.config import settings
from daemon.services import proxy_service

SEARXNG = "searxng"
MAX_RESULTS = 20
DEFAULT_RESULTS = 8
MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 2**20
MAX_CHARS = 80_000
TIMEOUT = aiohttp.ClientTimeout(total=30)

# A common desktop browser, so pages answer as they would to a person, and the
# Spark looks like one of many rather than a distinctive client.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# Page furniture that is never the content an agent asked for.
_DROP_TAGS = ["script", "style", "noscript", "svg", "nav", "header", "footer",
              "aside", "form", "iframe", "button"]


class WebError(Exception):
    """A failure the agent should read, not a crash."""


def is_public(host: str, port: int) -> bool:
    """True when every address `host` resolves to is on the public internet.

    Anything the daemon fetches for a caller goes through this first, so a URL
    can never reach the daemon's own port, the LAN or a cloud metadata address.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


async def read_capped(response: aiohttp.ClientResponse, limit: int) -> bytes:
    """The body, stopping once it passes `limit` bytes (so callers can tell).

    Not `response.content.read(limit)`: that returns whatever chunk has
    arrived, which for a page or an image is usually only the first few KB.
    """
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        body += chunk
        if len(body) > limit:
            break
    return bytes(body)


async def search(query: str, max_results: int | None = None) -> str:
    count = min(max(max_results or DEFAULT_RESULTS, 1), MAX_RESULTS)
    url = f"http://127.0.0.1:{settings.public_port}{proxy_service.APP_PREFIX}/{SEARXNG}/search"
    headers = {proxy_service.PROBE_HEADER: proxy_service.probe_token()}
    # POST keeps the query out of every URL, and so out of any log line.
    form = {"q": query, "format": "json"}
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, data=form, headers=headers) as r:
                if r.status != 200:
                    raise WebError(
                        "Web search is unavailable: SearXNG is not running on the Spark "
                        f"(HTTP {r.status}). Install or start SearXNG from the Spark AI Hub.")
                data = await r.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise WebError(f"Web search is unavailable: {exc}") from None
    return format_results(data, count)


def format_results(data: dict, count: int) -> str:
    """SearXNG's JSON answer as the numbered list the model reads."""
    lines = []
    for box in data.get("infoboxes") or []:
        text = (box.get("content") or "").strip()
        link = next((u.get("url") for u in box.get("urls") or [] if u.get("url")), "")
        if text:
            lines.append(f"{box.get('infobox', 'Summary')}: {text}" + (f" ({link})" if link else ""))
            lines.append("")
    for i, item in enumerate((data.get("results") or [])[:count], 1):
        lines.append(f"{i}. {item.get('title') or item.get('url')}")
        lines.append(f"   {item.get('url')}")
        if snippet := (item.get("content") or "").strip():
            lines.append(f"   {snippet}")
        if date := item.get("publishedDate"):
            lines.append(f"   Published: {str(date)[:10]}")
    if not lines:
        failed = ", ".join(f"{name} ({why})" for name, why in data.get("unresponsive_engines") or [])
        return "No results." + (f" Engines that did not answer: {failed}. Try again in a few "
                                "minutes, or rephrase." if failed else " Try a different query.")
    lines.append("")
    lines.append("Read a result in full with web_fetch. Cite the URLs you use.")
    return "\n".join(lines)


def _check_url(url: str) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise WebError(f"Only https:// URLs can be fetched (got {url!r}). Plain http is refused "
                       "so nothing travels unencrypted.")
    return parts


async def fetch(url: str) -> str:
    """The page at `url` as markdown (HTML) or text, following safe redirects."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
               "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"}
    # DummyCookieJar: no cookie is ever stored or sent, not even one a
    # redirect sets along the way.
    # Python's stock TLS context, not aiohttp's own: Wikimedia answers aiohttp's
    # handshake with a 403 and serves this one.
    connector = aiohttp.TCPConnector(ssl=ssl.create_default_context())
    async with aiohttp.ClientSession(timeout=TIMEOUT, cookie_jar=aiohttp.DummyCookieJar(),
                                     headers=headers, connector=connector) as session:
        for _ in range(MAX_REDIRECTS + 1):
            parts = _check_url(url)
            if not await asyncio.to_thread(is_public, parts.hostname, parts.port or 443):
                raise WebError(f"{parts.hostname} is not a public address; only public sites "
                               "can be fetched.")
            try:
                async with session.get(url, allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308) and "Location" in r.headers:
                        url = urllib.parse.urljoin(url, r.headers["Location"])
                        continue
                    kind = (r.content_type or "").lower()
                    body = await read_capped(r, MAX_PAGE_BYTES)
                    status, charset = r.status, r.charset or "utf-8"
            except aiohttp.ClientError as exc:
                raise WebError(f"Fetching {url} failed: {exc}") from None
            break
        else:
            raise WebError(f"Too many redirects fetching {url}.")

    too_big = len(body) > MAX_PAGE_BYTES
    try:
        text = body[:MAX_PAGE_BYTES].decode(charset, errors="replace")
    except LookupError:  # a charset Python has never heard of
        text = body[:MAX_PAGE_BYTES].decode("utf-8", errors="replace")
    if "html" in kind:
        text = _html_to_markdown(text, url)
    elif not (kind.startswith("text/") or "json" in kind or "xml" in kind):
        raise WebError(f"{url} is {kind or 'an unknown type'}, not a web page or text; "
                       "web_fetch reads HTML and text only.")

    cut = too_big or len(text) > MAX_CHARS
    out = f"Fetched {url} (HTTP {status})\n\n{text[:MAX_CHARS]}"
    if cut:
        out += "\n\n(Content truncated. Fetch a more specific page for the rest.)"
    return out


def _html_to_markdown(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    # Absolute links, so the agent can web_fetch one as it stands.
    for a in soup.find_all("a", href=True):
        a["href"] = urllib.parse.urljoin(base_url, a["href"])
    root = soup.find("main") or soup.find("article") or soup.body or soup
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = markdownify(str(root), heading_style="ATX", strip=["img"])
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"# {title}\n\n{text}" if title and not text.startswith("# ") else text
