"""Public web access for vendor research (docs/09 §2): SSRF guard, search, fetch, replay."""

from fl_agents.web.client import (
    FixtureMissError,
    Page,
    RobotsDisallowedError,
    SearchResult,
    SearchUnavailableError,
    WebClient,
    WebError,
)
from fl_agents.web.ssrf import BlockedUrlError, FetchFailedError, check_public_url, fetch_public

__all__ = [
    "BlockedUrlError",
    "FetchFailedError",
    "FixtureMissError",
    "Page",
    "RobotsDisallowedError",
    "SearchResult",
    "SearchUnavailableError",
    "WebClient",
    "WebError",
    "check_public_url",
    "fetch_public",
]
