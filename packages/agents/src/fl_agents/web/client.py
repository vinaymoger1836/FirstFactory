"""Public web access for vendor research: search, fetch, record and replay (docs/09 §2).

* ``search`` calls the Tavily API (key in the environment, never in code or fixtures). Queries
  are built by the caller from catalog data only; nothing about the user's project is sent.
* ``fetch`` goes through the SSRF guard, honours ``robots.txt`` and waits politely per host.
* ``web_mode``: ``live`` talks to the network; ``record`` and ``record_missing`` use a recorded
  search or page when there is one and otherwise go to the network and write it to the fixture
  directory (delete a file to record it again); ``replay`` reads only those fixtures
  (end-to-end check: no network, no quota). A missing fixture is a ``FixtureMissError``, not
  a silent empty answer.

Fixtures hold public page text and search result snippets only: no keys, no headers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from fl_agents.settings import Settings
from fl_agents.web.ssrf import BlockedUrlError, FetchFailedError, fetch_public
from fl_agents.web.text import decode, html_to_text

__all__ = [
    "FixtureMissError",
    "Page",
    "RobotsDisallowedError",
    "SearchResult",
    "SearchUnavailableError",
    "WebClient",
    "WebError",
]

log = logging.getLogger(__name__)
TAVILY_URL = "https://api.tavily.com/search"


class WebError(Exception):
    """Base class: the caller records a gap and goes on."""


class SearchUnavailableError(WebError):
    """No search key, or the search service refused or failed."""


class RobotsDisallowedError(WebError):
    """robots.txt does not allow this page for our user agent."""


class FixtureMissError(WebError):
    """Replay mode has no recorded answer for this request."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    fetched_at: str


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WebClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sem = asyncio.Semaphore(3)

    @property
    def mode(self) -> str:
        return self.s.web_mode

    @property
    def can_search(self) -> bool:
        """Live and record need a key; replay needs recorded results."""
        return self.mode == "replay" or bool(self.s.search_key())

    def _fixture(self, kind: str, key: str) -> Path:
        return self.s.web_fixture_dir / kind / f"{key}.json"

    def _write(self, kind: str, key: str, data: dict[str, Any]) -> None:
        p = self._fixture(kind, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(p)

    # ── search ────────────────────────────────────────────────────────

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        key = _key(query.strip().lower())
        p = self._fixture("search", key)
        if self.mode == "replay" or (self.mode != "live" and p.is_file()):
            if not p.is_file():
                raise FixtureMissError(f"no recorded search for '{query}'")
            data = json.loads(p.read_text(encoding="utf-8"))
            return [SearchResult(**r) for r in data["results"]][:max_results]
        api_key = self.s.search_key()
        if not api_key:
            raise SearchUnavailableError("no TAVILY_API_KEY is set, so no web search can run")
        try:
            async with httpx.AsyncClient(timeout=self.s.web_timeout_s * 2) as c:
                r = await c.post(
                    TAVILY_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                )
        except httpx.HTTPError as e:
            raise SearchUnavailableError(f"search failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise SearchUnavailableError(f"search returned HTTP {r.status_code}")
        raw = r.json().get("results", [])
        results = [
            SearchResult(
                title=str(x.get("title", ""))[:200],
                url=str(x.get("url", "")),
                snippet=str(x.get("content", ""))[:500],
            )
            for x in raw
            if str(x.get("url", "")).startswith(("http://", "https://"))
        ]
        if self.mode != "live":
            self._write(
                "search",
                key,
                {
                    "query": query,
                    "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "results": [x.__dict__ for x in results],
                },
            )
        return results[:max_results]

    # ── fetch ─────────────────────────────────────────────────────────

    async def _polite(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, 0.0) + self.s.web_min_delay_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            await self._polite(parts.netloc)
            try:
                got = await fetch_public(
                    f"{origin}/robots.txt",
                    user_agent=self.s.web_user_agent,
                    timeout_s=self.s.web_timeout_s,
                    max_bytes=100_000,
                )
                assert rp is not None
                rp.parse(decode(got.body).splitlines())
            except FetchFailedError as e:
                # 4xx (no robots file) allows everything; a server error or timeout does not.
                missing = str(e).startswith("HTTP 4")
                rp = RobotFileParser() if missing else None
                if rp is not None:
                    rp.parse([])
            except BlockedUrlError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is not None and rp.can_fetch(self.s.web_user_agent, url)

    async def fetch(self, url: str) -> Page:
        key = _key(url)
        p = self._fixture("pages", key)
        if self.mode == "replay" or (self.mode != "live" and p.is_file()):
            if not p.is_file():
                raise FixtureMissError(f"no recorded page for {url}")
            d = json.loads(p.read_text(encoding="utf-8"))
            return Page(d["url"], d.get("title", ""), d["text"], d["fetched_at"])
        async with self._sem:
            try:
                if not await self._allowed(url):
                    raise RobotsDisallowedError(f"robots.txt does not allow {urlsplit(url).netloc}")
                await self._polite(urlsplit(url).netloc)
                got = await fetch_public(
                    url,
                    user_agent=self.s.web_user_agent,
                    timeout_s=self.s.web_timeout_s,
                    max_bytes=self.s.web_max_bytes,
                )
            except BlockedUrlError as e:
                raise WebError(f"blocked: {e}") from e
            except FetchFailedError as e:
                raise WebError(f"not fetched: {e}") from e
        title, text = html_to_text(decode(got.body))
        page = Page(got.url, title, text, datetime.now(UTC).isoformat(timespec="seconds"))
        if self.mode != "live":
            self._write(
                "pages",
                key,
                {
                    "url": page.url,
                    "requested": url,
                    "title": page.title,
                    "text": page.text,
                    "fetched_at": page.fetched_at,
                },
            )
        return page
