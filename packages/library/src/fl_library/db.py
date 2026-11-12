"""Versioned load of catalog and packs into Postgres, and hybrid catalog search.

Tables are created by the Alembic migration ``0002_library`` (apps/api). Loading is one
transaction under an advisory lock, so concurrent loaders cannot interleave:

- a version already loaded with the same content hash is skipped (idempotent deploys);
- the same version with different content is refused: publish a new version instead
  (artifacts and pinned projects must stay reproducible, ADR-009).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from fl_domain.base import Issue
from fl_domain.library.catalog import Catalog, CatalogEntry
from fl_domain.library.pack import Pack
from fl_library.embed import DIM, EMBEDDING_MODEL, embed, to_pgvector

__all__ = ["LoadResult", "content_hash", "load_library", "psycopg_url", "search_catalog"]

_LOCK_KEY = "fl_library_load"


@dataclass
class LoadResult:
    kind: str
    scope: str
    version: str
    status: str  # "loaded" | "unchanged"
    rows: int
    verify_count: int = 0
    notes: list[str] = field(default_factory=list)


def psycopg_url(url: str) -> str:
    """Accept the API's SQLAlchemy URL (postgresql+asyncpg://...) as well."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def content_hash(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _search_text(e: CatalogEntry) -> str:
    parts = [e.code.replace("_", " "), e.name, *e.aliases, e.description, e.kind.replace("_", " ")]
    return " ".join(p for p in parts if p)


def _existing(cur: psycopg.Cursor[Any], kind: str, scope: str, version: str) -> str | None:
    cur.execute(
        "SELECT content_hash FROM library_version WHERE kind=%s AND scope=%s AND version=%s",
        (kind, scope, version),
    )
    row = cur.fetchone()
    return str(row[0]) if row else None


def _new_version(
    cur: psycopg.Cursor[Any],
    kind: str,
    scope: str,
    version: str,
    digest: str,
    count: int,
    verify_count: int,
    warnings: list[Issue],
) -> int:
    cur.execute(
        """INSERT INTO library_version
             (kind, scope, version, content_hash, entry_count, verify_count, issues,
              embedding_model, embedding_dim)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            kind,
            scope,
            version,
            digest,
            count,
            verify_count,
            Jsonb([w.model_dump() for w in warnings]),
            EMBEDDING_MODEL if kind == "catalog" else None,
            DIM if kind == "catalog" else None,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _load_catalog(cur: psycopg.Cursor[Any], cat: Catalog, warnings: list[Issue]) -> LoadResult:
    version = cat.manifest.version
    digest = content_hash(cat.model_dump(mode="json"))
    prior = _existing(cur, "catalog", "catalog", version)
    if prior == digest:
        return LoadResult("catalog", "catalog", version, "unchanged", len(cat.entries))
    if prior is not None:
        raise ValueError(
            f"catalog {version} is already loaded with different content; "
            "bump the version in library/catalog/catalog.yaml"
        )
    vid = _new_version(cur, "catalog", "catalog", version, digest, len(cat.entries), 0, warnings)
    with cur.copy(
        "COPY catalog_entry (version_id, code, kind, name, data, search_text, embedding) FROM STDIN"
    ) as copy:
        for e in cat.entries:
            text = _search_text(e)
            copy.write_row(
                (
                    vid,
                    e.code,
                    e.kind,
                    e.name,
                    json.dumps(e.model_dump(mode="json"), ensure_ascii=False),
                    text,
                    to_pgvector(embed(text)),
                )
            )
    return LoadResult("catalog", "catalog", version, "loaded", len(cat.entries))


def _load_pack(cur: psycopg.Cursor[Any], pack: Pack, warnings: list[Issue]) -> LoadResult:
    m = pack.manifest
    digest = content_hash(pack.model_dump(mode="json"))
    verify_count = sum(1 for v in pack.values if v.verify)
    prior = _existing(cur, "pack", m.state, m.version)
    if prior == digest:
        return LoadResult("pack", m.state, m.version, "unchanged", len(pack.values), verify_count)
    if prior is not None:
        raise ValueError(
            f"pack {m.state} {m.version} is already loaded with different content; "
            f"bump the version in library/packs/{m.state}/pack.yaml"
        )
    vid = _new_version(
        cur, "pack", m.state, m.version, digest, len(pack.values), verify_count, warnings
    )
    cur.executemany(
        """INSERT INTO pack_value
             (version_id, key, grp, title, value, unit, effective_from, effective_to, superseded,
              applies_to, source_kind, source_url, source_title, retrieved_at, confidence,
              verify, note)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        [
            (
                vid,
                v.key,
                v.group,
                v.title,
                Jsonb(v.value),
                v.unit,
                v.effective_from,
                v.effective_to,
                v.superseded,
                v.applies_to,
                v.source_kind,
                v.source_url,
                v.source_title,
                v.retrieved_at,
                v.confidence,
                v.verify,
                v.note,
            )
            for v in pack.values
        ],
    )
    return LoadResult("pack", m.state, m.version, "loaded", len(pack.values), verify_count)


def load_library(
    url: str, catalog: Catalog, packs: list[Pack], warnings: list[Issue]
) -> list[LoadResult]:
    """Load (or confirm) the catalog and packs in one transaction."""
    with (
        psycopg.connect(psycopg_url(url), application_name="fl-library") as conn,
        conn.transaction(),
        conn.cursor() as cur,
    ):
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_LOCK_KEY,))
        results = [_load_catalog(cur, catalog, warnings)]
        results += [_load_pack(cur, p, warnings) for p in packs]
    return results


_SEARCH_SQL = """
WITH v AS (
  SELECT id FROM library_version
  WHERE kind = 'catalog' AND scope = 'catalog'
    AND (%(version)s::text IS NULL OR version = %(version)s)
  ORDER BY string_to_array(version, '.')::int[] DESC LIMIT 1
),
q AS (SELECT %(vec)s::vector AS vec, plainto_tsquery('english', %(text)s) AS tsq),
ann AS (   -- approximate nearest neighbours through the HNSW index
  SELECT e.id FROM catalog_entry e, v, q
  WHERE e.version_id = v.id AND (%(kind)s::text IS NULL OR e.kind = %(kind)s)
  ORDER BY e.embedding <=> q.vec LIMIT 50
),
kw AS (    -- full-text matches through the GIN index
  SELECT e.id FROM catalog_entry e, v, q
  WHERE e.version_id = v.id AND e.tsv @@ q.tsq AND (%(kind)s::text IS NULL OR e.kind = %(kind)s)
  LIMIT 50
)
SELECT e.code, e.kind, e.name,
       1 - (e.embedding <=> q.vec) AS vector_score,
       ts_rank(e.tsv, q.tsq) AS keyword_score,
       0.7 * (1 - (e.embedding <=> q.vec)) + 0.3 * least(ts_rank(e.tsv, q.tsq) * 10, 1) AS score
FROM catalog_entry e, q
WHERE e.id IN (SELECT id FROM ann UNION SELECT id FROM kw)
ORDER BY score DESC, e.code
LIMIT %(limit)s
"""


def search_catalog(
    url: str, text: str, kind: str | None = None, version: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Hybrid search: vector similarity (pgvector) + keyword rank (tsvector)."""
    params = {
        "vec": to_pgvector(embed(text)),
        "text": text,
        "kind": kind,
        "version": version,
        "limit": limit,
    }
    with psycopg.connect(psycopg_url(url), application_name="fl-library") as conn:
        rows = conn.execute(_SEARCH_SQL, params).fetchall()
    keys = ("code", "kind", "name", "vector_score", "keyword_score", "score")
    return [dict(zip(keys, r, strict=True)) for r in rows]
