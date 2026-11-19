"""HTML to plain text for evidence checking (standard library only).

Scripts, styles and hidden parts are dropped; link targets are kept as ``(https://...)`` after
the link text so a contact URL a page really contains can be checked against the text.
"""

from __future__ import annotations

import contextlib
import re
from html.parser import HTMLParser

__all__ = ["MAX_TEXT_CHARS", "html_to_text"]

MAX_TEXT_CHARS = 60_000
_SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}
_BLOCK = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "td",
    "th",
    "table",
    "ul",
    "ol",
}
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n\s*\n+")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        if tag == "title":
            self._title = True
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a" and not self._skip:
            href = dict(attrs).get("href") or ""
            if href.startswith(("http://", "https://")):
                self.parts.append(f" ({href}) ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._title = False
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title += data
        if self._skip:
            return
        self.parts.append(data)


def decode(body: bytes, content_type_charset: str | None = None) -> str:
    for enc in (content_type_charset, "utf-8"):
        if enc:
            try:
                return body.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
    return body.decode("latin-1", errors="replace")


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text) of a page, whitespace tidied and capped."""
    p = _Text()
    with contextlib.suppress(Exception):  # malformed markup: use what was parsed so far
        p.feed(html)
        p.close()
    text = "".join(p.parts)
    text = "\n".join(_WS.sub(" ", ln).strip() for ln in text.split("\n"))
    text = _NL.sub("\n\n", text).strip()
    return p.title.strip(), text[:MAX_TEXT_CHARS]
