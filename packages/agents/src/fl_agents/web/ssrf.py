"""SSRF guard for fetching public pages (CLAUDE.md rule 9, docs/11 §3).

Only http(s) on ordinary ports, no credentials in the URL, and only **public** IP addresses:
the host is resolved here, every address must be global, and the connection goes to that
checked address (the original host name is kept for the Host header and the TLS name check,
so a second DNS answer cannot redirect the request). Redirects are followed by hand, at most
three, and every hop goes through the same checks. Size and time are capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

__all__ = [
    "BlockedUrlError",
    "FetchFailedError",
    "Fetched",
    "check_public_url",
    "fetch_public",
]

ALLOWED_PORTS = {80, 443, 8080, 8443}
ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml", "application/xml", "text/xml")
MAX_REDIRECTS = 3


class BlockedUrlError(Exception):
    """The URL failed the SSRF guard."""


class FetchFailedError(Exception):
    """The page could not be fetched (status, type, size, network)."""


@dataclass(frozen=True)
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes
    truncated: bool


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global


async def check_public_url(url: str) -> tuple[str, str, int, str, list[str]]:
    """(scheme, host, port, path+query, public addresses) or raise ``BlockedUrlError``."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise BlockedUrlError("only http and https URLs are allowed")
    if parts.username or parts.password:
        raise BlockedUrlError("credentials in the URL are not allowed")
    host = parts.hostname
    if not host:
        raise BlockedUrlError("no host in the URL")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise BlockedUrlError(f"port {port} is not allowed")
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError as e:
            raise BlockedUrlError(f"cannot resolve {host}: {e}") from e
        addrs = sorted({str(i[4][0]) for i in infos})
    if not addrs:
        raise BlockedUrlError(f"{host} has no address")
    bad = [a for a in addrs if not _public(a)]
    if bad:
        raise BlockedUrlError(f"{host} resolves to a non-public address ({bad[0]})")
    return parts.scheme, host, port, target, addrs


async def fetch_public(url: str, *, user_agent: str, timeout_s: float, max_bytes: int) -> Fetched:
    """GET a public page with the guard applied to every redirect hop."""
    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_s, verify=True) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, target, addrs = await check_public_url(current)
            ip = addrs[0]
            netloc = f"[{ip}]" if ":" in ip else ip
            default = 443 if scheme == "https" else 80
            netloc += "" if port == default else f":{port}"
            headers = {
                "Host": host if port == default else f"{host}:{port}",
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                "Accept-Encoding": "gzip",
            }
            try:
                async with client.stream(
                    "GET",
                    f"{scheme}://{netloc}{target}",
                    headers=headers,
                    extensions={"sni_hostname": host},
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            raise FetchFailedError("redirect without a location")
                        current = urljoin(current, loc)
                        continue
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if resp.status_code != 200:
                        raise FetchFailedError(f"HTTP {resp.status_code}")
                    if ctype and not ctype.startswith(ALLOWED_TYPES):
                        raise FetchFailedError(f"content type {ctype} is not a web page")
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        chunks.append(chunk)
                        if size > max_bytes:
                            truncated = True
                            break
                    return Fetched(
                        current, 200, ctype or "text/html", b"".join(chunks)[:max_bytes], truncated
                    )
            except httpx.HTTPError as e:
                raise FetchFailedError(f"{type(e).__name__}: {e}") from e
    raise FetchFailedError("too many redirects")
