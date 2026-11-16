"""Record and replay of LLM responses (cassettes) for the end-to-end check (CLAUDE.md rule 7).

A cassette is one JSON file per request, named by a hash of everything that determines
the answer: role, messages (images replaced by their SHA-256), output schema and
temperature. The model name is not part of the key, so replay keeps working if a role is
pointed at another model; the recorded model is stored for the report.

Nothing secret is written: no headers, no keys, no image bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["CassetteStore", "request_key", "sanitize_messages"]


def _image_digest(url: str) -> str:
    if url.startswith("data:") and "," in url:
        raw = base64.b64decode(url.split(",", 1)[1])
        return "sha256:" + hashlib.sha256(raw).hexdigest()
    return "url-sha256:" + hashlib.sha256(url.encode()).hexdigest()


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages with image payloads replaced by digests (stable, small, no bytes stored)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts: list[Any] = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    url = p.get("image_url", {}).get("url", "")
                    parts.append({"type": "image", "digest": _image_digest(url)})
                else:
                    parts.append(p)
            out.append({**m, "content": parts})
        else:
            out.append(dict(m))
    return out


def request_key(
    role: str,
    messages: list[dict[str, Any]],
    schema_name: str,
    schema: dict[str, Any],
    temperature: float,
) -> str:
    payload = {
        "role": role,
        "messages": sanitize_messages(messages),
        "schema_name": schema_name,
        "schema": schema,
        "temperature": temperature,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CassetteStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        if not p.is_file():
            return None
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data

    def _marker(self, role: str) -> Path:
        return self.dir / f"_unavailable_{role}"

    def mark_unavailable(self, role: str) -> None:
        """Recording found no key for ``role``: replay must skip it the same way."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self._marker(role).write_text("no key configured during recording", encoding="utf-8")

    def is_unavailable(self, role: str) -> bool:
        return self._marker(role).is_file()

    def put(
        self,
        key: str,
        *,
        role: str,
        model: str,
        schema_name: str,
        messages: list[dict[str, Any]],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "role": role,
            "model": model,
            "schema": schema_name,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": {"messages": sanitize_messages(messages)},
            "response": {"content": content},
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self._path(key))
