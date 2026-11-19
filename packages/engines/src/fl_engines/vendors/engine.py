"""Vendor research engine (docs/09 §2.2, ADR-028): evidence check, score, dedupe.

The model only *reads* a page and proposes records; this module decides what survives:

* **evidence-required**: a record needs a quote that appears in the fetched page text and the
  vendor name near that quote; every optional field (city, MOQ, price, ...) is kept only if
  its text appears in the page. Records without evidence are dropped; missing fields stay
  empty (rule 4: never invent the world);
* **score** 0-100 from the catalog's vendor policy (relevance, certifications, evidence
  quality, region);
* **dedupe** by registrable domain (own sites) or by name (listing sites), keeping all
  evidence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import urlparse

from pydantic import HttpUrl, TypeAdapter, ValidationError

from fl_domain.artifacts.vendors import Vendor, VendorEvidence
from fl_domain.library.catalog import VendorPolicy
from fl_engines.bom.rules import tokens

__all__ = [
    "Candidate",
    "Verified",
    "dedupe",
    "norm",
    "registrable_domain",
    "score_vendor",
    "to_vendor",
    "verify_candidate",
]

MIN_QUOTE_CHARS = 20
WINDOW = 400  # characters around the quote in which the vendor's name must appear
NAME_SIMILAR = 0.9
_PUNCT = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)
_WS = re.compile(r"\s+")
_URL = TypeAdapter(HttpUrl)


def norm(text: str) -> str:
    """Casefolded, whitespace-collapsed text with typographic quotes and dashes flattened."""
    t = unicodedata.normalize("NFKC", text).translate(_PUNCT).casefold()
    return _WS.sub(" ", t).strip()


@dataclass
class Candidate:
    """What the model proposed for one vendor on one page (untrusted)."""

    name: str
    quote: str
    city: str | None = None
    state: str | None = None
    products: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    moq: str | None = None
    indicative_price: str | None = None
    lead_time: str | None = None
    contact_url: str | None = None


@dataclass
class Verified:
    """A candidate that survived the evidence check, with the page it came from."""

    name: str
    city: str | None
    state: str | None
    products: list[str]
    certifications: list[str]
    moq: str | None
    indicative_price: str | None
    lead_time: str | None
    contact_url: str | None
    evidence: list[VendorEvidence]
    domain: str


def registrable_domain(url_or_host: str) -> str:
    host = (urlparse(url_or_host).hostname or url_or_host).lower().removeprefix("www.")
    labels = host.split(".")
    if (
        len(labels) >= 3
        and len(labels[-1]) == 2
        and labels[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"}
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _in(value: str | None, page: str) -> str | None:
    """``value`` if its normalised text occurs in the page, else None."""
    if value and (v := norm(value)) and v in page:
        return value.strip()
    return None


def _kind(domain: str, name: str, policy: VendorPolicy) -> str:
    if domain in policy.directory_domains:
        return "directory"
    first = next((w for w in re.findall(r"[a-z0-9]+", norm(name)) if len(w) >= 3), "")
    return "maker" if first and first in domain.split(".")[0] else "other"


def verify_candidate(
    c: Candidate, page_text: str, url: str, fetched_at: str, policy: VendorPolicy
) -> Verified | None:
    """Keep the candidate only if the page really says what the quote claims."""
    page = norm(page_text)
    quote = norm(c.quote)
    name = norm(c.name)
    if len(quote) < MIN_QUOTE_CHARS or not name:
        return None
    at = page.find(quote)
    if at < 0:
        return None
    window = page[max(0, at - WINDOW) : at + len(quote) + WINDOW]
    if name not in window:
        return None
    contact: str | None = None
    if c.contact_url and _in(c.contact_url, page):
        try:
            contact = str(_URL.validate_python(c.contact_url.strip()))
        except ValidationError:
            contact = None
    domain = registrable_domain(contact or url)
    page_domain = registrable_domain(url)
    return Verified(
        name=c.name.strip(),
        city=_in(c.city, page),
        state=_in(c.state, page),
        products=[p.strip() for p in c.products if _in(p, page)][:8],
        certifications=[x.strip() for x in c.certifications if _in(x, page)][:6],
        moq=_in(c.moq, page),
        indicative_price=_in(c.indicative_price, page),
        lead_time=_in(c.lead_time, page),
        contact_url=contact,
        evidence=[
            VendorEvidence(
                url=_URL.validate_python(url),
                fetched_at=fetched_at,
                snippet=c.quote.strip()[:600],
                source_kind=_kind(page_domain, c.name, policy),
            )
        ],
        domain=domain,
    )


def score_vendor(
    v: Verified,
    policy: VendorPolicy,
    class_phrases: list[str],
    state: str | None,
) -> tuple[float, dict[str, float]]:
    """0-100 from the policy weights. ``state`` is the site's state code (e.g. KA)."""
    w = policy.weights
    text_tokens = tokens(" ".join([*v.products, *(e.snippet for e in v.evidence)]))
    rel = 0.0
    for phrase in class_phrases:
        pt = tokens(phrase)
        if pt:
            rel = max(rel, len(pt & text_tokens) / len(pt))
    certs = min(1.0, len(v.certifications) / 2)
    kinds = {e.source_kind for e in v.evidence}
    kind = "maker" if "maker" in kinds else "other" if "other" in kinds else "directory"
    f = policy.directory_factor
    evid = 1.0 if kind == "maker" else f if kind == "directory" else (1 + f) / 2
    names = {norm(n) for n in policy.region_state_names.get(state or "", [])}
    here = {norm(x) for x in (v.state, v.city) if x}
    region = 1.0 if names & here else 0.0
    parts = {
        "relevance": round(w.get("relevance", 0) * rel, 1),
        "certifications": round(w.get("certifications", 0) * certs, 1),
        "evidence": round(w.get("evidence", 0) * evid, 1),
        "region": round(w.get("region", 0) * region, 1),
    }
    return round(min(100.0, sum(parts.values())), 1), parts


def dedupe(
    items: list[tuple[Verified, float, dict[str, float]]],
) -> list[tuple[Verified, float, dict[str, float]]]:
    """One record per vendor: same own-site domain, or a very similar name; evidence merged."""
    out: list[tuple[Verified, float, dict[str, float]]] = []
    for v, s, parts in sorted(items, key=lambda t: -t[1]):
        own = {e.source_kind for e in v.evidence} != {"directory"}
        hit = next(
            (
                o
                for o, _s, _p in out
                if (
                    o.domain == v.domain
                    and own
                    and {e.source_kind for e in o.evidence} != {"directory"}
                )
                or SequenceMatcher(None, norm(o.name), norm(v.name)).ratio() >= NAME_SIMILAR
            ),
            None,
        )
        if hit is None:
            out.append((v, s, parts))
            continue
        seen = {str(e.url) for e in hit.evidence}
        hit.evidence += [e for e in v.evidence if str(e.url) not in seen]
    return out


def to_vendor(v: Verified, score: float, parts: dict[str, float]) -> Vendor:
    """The domain record; every vendor carries all the evidence found for it."""
    ev = v.evidence
    return Vendor(
        name=v.name,
        city=v.city,
        state=v.state,
        products=v.products,
        certifications=v.certifications,
        moq=v.moq,
        indicative_price=v.indicative_price,
        lead_time=v.lead_time,
        contact_url=_URL.validate_python(v.contact_url) if v.contact_url else None,
        evidence=ev,
        score=score,
        domain=v.domain,
        score_parts=parts,
    )
