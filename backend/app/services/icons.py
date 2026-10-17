"""Websitepictogrammen voor de Bitwarden-clients (GET /icons/{domein}/icon.png).

De apps tonen naast elk login-item het pictogram van de website. VaultX haalt
dat zelf op bij de website (zoals Vaultwarden), zodat geen externe dienst ziet
welke sites in je kluis staan.

Bescherming tegen misbruik als proxy naar het interne netwerk (SSRF):
- alleen domeinnamen, geen IP-adressen of poorten;
- elk adres wordt opgelost en gecontroleerd vlak voor de verbinding (ook na een
  redirect), en de verbinding gaat naar precies dat adres: DNS-rebinding helpt niet;
- private, loopback- en link-local adressen enkel voor domeinen die VaultX zelf
  kent uit de NPM-koppelingen of de catalogus (je eigen apps achter NPM);
- kleine limieten op grootte, redirects en tijd; alleen echte afbeeldingen (geen SVG).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
import ssl
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

log = logging.getLogger(__name__)

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
MAX_PAGE_BYTES = 512 * 1024
MAX_ICON_BYTES = 256 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 5.0
CACHE_ENTRIES = 1000
CACHE_SECONDS = 7 * 24 * 3600
MISS_SECONDS = 24 * 3600
USER_AGENT = "Mozilla/5.0 (compatible; VaultX icon fetcher)"


@dataclass(slots=True, frozen=True)
class Icon:
    data: bytes
    content_type: str


def normalize_domain(raw: str) -> str | None:
    domain = raw.strip().lower().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return domain if DOMAIN_RE.match(domain) else None


def sniff_image(data: bytes) -> str | None:
    """Inhoudstype op basis van de eerste bytes; SVG en HTML vallen bewust af."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class _IconLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        href = a.get("href", "").strip()
        if not href or not ({"icon", "apple-touch-icon"} & set(rel)):
            return
        if href.lower().endswith(".svg") or "svg" in a.get("type", ""):
            return
        # Liefst iets van rond 32-64 pixels, daarna groter, daarna de rest.
        size = 0
        m = re.match(r"(\d+)x\d+", a.get("sizes", ""))
        if m:
            size = int(m.group(1))
        score = 100 - abs(48 - size) if size else (40 if "icon" in rel else 30)
        self.links.append((score, href))


def icon_candidates(html: str, page_url: str) -> list[str]:
    parser = _IconLinks()
    # Kapotte HTML: dan blijven enkel de links tot daar, plus /favicon.ico.
    with contextlib.suppress(Exception):
        parser.feed(html)
    urls = [urljoin(page_url, href) for _, href in sorted(parser.links, key=lambda x: -x[0])]
    urls.append(urljoin(page_url, "/favicon.ico"))
    seen: list[str] = []
    for u in urls:
        if urlsplit(u).scheme in ("http", "https") and u not in seen:
            seen.append(u)
    return seen[:4]


class BlockedAddress(Exception):
    pass


def is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Lost de host zelf op, controleert het adres en verbindt met precies dat adres."""

    def __init__(self, allow_private: Callable[[str], bool]) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._allow_private = allow_private

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - interface van httpcore
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        ips = await resolve(host, port)
        if not ips:
            raise BlockedAddress(host)
        if not all(is_public(ip) for ip in ips) and not self._allow_private(host):
            raise BlockedAddress(host)
        return await self._inner.connect_tcp(ips[0], port, timeout, local_address, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, allow_private: Callable[[str], bool]) -> None:
        super().__init__()
        # TLS controleert nog steeds de hostnaam (SNI = host uit de URL), enkel de
        # TCP-verbinding gaat naar het gecontroleerde adres.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=10,
            network_backend=_PinnedBackend(allow_private),
        )


IconFetcher = Callable[[str, Callable[[str], bool]], Awaitable[Icon | None]]


async def _get_limited(client: httpx.AsyncClient, url: str, limit: int) -> tuple[httpx.Response, bytes, str]:
    """GET met eigen redirects (elke stap wordt opnieuw gecontroleerd) en een maximale grootte."""
    for _ in range(MAX_REDIRECTS + 1):
        async with client.stream("GET", url) as resp:
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                if urlsplit(url).scheme not in ("http", "https"):
                    raise httpx.InvalidURL(url)
                continue
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    break
            return resp, body[:limit], url
    raise httpx.TooManyRedirects(url)


async def fetch_icon(domain: str, allow_private: Callable[[str], bool]) -> Icon | None:
    async with httpx.AsyncClient(
        transport=_PinnedTransport(allow_private),
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,image/*;q=0.8"},
    ) as client:
        page_html, page_url = "", f"https://{domain}/"
        for start in (f"https://{domain}/", f"http://{domain}/"):
            try:
                resp, body, final = await _get_limited(client, start, MAX_PAGE_BYTES)
            except (httpx.HTTPError, BlockedAddress, OSError):
                continue
            page_url = final
            if "html" in resp.headers.get("content-type", ""):
                page_html = body.decode(resp.encoding or "utf-8", errors="replace")
            break
        else:
            return None
        for url in icon_candidates(page_html, page_url):
            try:
                resp, body, _ = await _get_limited(client, url, MAX_ICON_BYTES + 1)
            except (httpx.HTTPError, BlockedAddress, OSError):
                continue
            if resp.status_code != 200 or len(body) > MAX_ICON_BYTES:
                continue
            content_type = sniff_image(body)
            if content_type:
                return Icon(body, content_type)
    return None


class IconCache:
    """Kleine LRU per proces, ook voor domeinen zonder pictogram (minder vaak opnieuw proberen)."""

    def __init__(self, fetcher: IconFetcher = fetch_icon, max_entries: int = CACHE_ENTRIES) -> None:
        self._fetcher = fetcher
        self._max = max_entries
        self._entries: OrderedDict[str, tuple[float, Icon | None]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[Icon | None]] = {}
        self._limit = asyncio.Semaphore(4)

    async def get(self, domain: str, allow_private: Callable[[str], bool]) -> Icon | None:
        now = time.monotonic()
        hit = self._entries.get(domain)
        if hit is not None and hit[0] > now:
            self._entries.move_to_end(domain)
            return hit[1]
        if domain in self._inflight:
            return await self._inflight[domain]
        future: asyncio.Future[Icon | None] = asyncio.get_running_loop().create_future()
        self._inflight[domain] = future
        icon: Icon | None = None
        try:
            async with self._limit:
                icon = await self._fetcher(domain, allow_private)
        except Exception:  # noqa: BLE001 - een pictogram is nooit belangrijk genoeg voor een 500
            log.debug("Pictogram voor %s ophalen faalde", domain, exc_info=True)
        finally:
            del self._inflight[domain]
            future.set_result(icon)
        self._entries[domain] = (now + (CACHE_SECONDS if icon else MISS_SECONDS), icon)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
        return icon
