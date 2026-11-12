"""library: versioned catalog and data packs, pgvector + full-text index for catalog search

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12

docs/04 §4 (catalog_*, pack_value) and docs/10. Rows are written by ``fl-library load``.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 256  # fl_library.embed.DIM


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE library_version (
            id               bigserial PRIMARY KEY,
            kind             text NOT NULL CHECK (kind IN ('catalog', 'pack')),
            scope            text NOT NULL,          -- 'catalog' or the pack state, e.g. 'KA'
            version          text NOT NULL CHECK (version ~ '^\\d{4}\\.\\d{2}\\.\\d+$'),
            content_hash     char(64) NOT NULL,
            entry_count      integer NOT NULL,
            verify_count     integer NOT NULL DEFAULT 0,
            issues           jsonb NOT NULL DEFAULT '[]'::jsonb,   -- warnings at load time
            embedding_model  text,
            embedding_dim    integer,
            loaded_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kind, scope, version)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE catalog_entry (
            id           bigserial PRIMARY KEY,
            version_id   bigint NOT NULL REFERENCES library_version(id) ON DELETE CASCADE,
            code         text NOT NULL,
            kind         text NOT NULL,
            name         text NOT NULL,
            data         jsonb NOT NULL,
            search_text  text NOT NULL,
            tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', search_text)) STORED,
            embedding    vector({EMBEDDING_DIM}) NOT NULL,
            UNIQUE (version_id, code)
        )
        """
    )
    op.execute("CREATE INDEX ix_catalog_entry_version_kind ON catalog_entry (version_id, kind)")
    op.execute("CREATE INDEX ix_catalog_entry_tsv ON catalog_entry USING gin (tsv)")
    op.execute(
        "CREATE INDEX ix_catalog_entry_embedding ON catalog_entry "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        """
        CREATE TABLE pack_value (
            id              bigserial PRIMARY KEY,
            version_id      bigint NOT NULL REFERENCES library_version(id) ON DELETE CASCADE,
            key             text NOT NULL,
            grp             text NOT NULL,
            title           text NOT NULL,
            value           jsonb,                 -- JSON null = unknown (verify: true)
            unit            text,
            effective_from  date NOT NULL,
            effective_to    date,
            superseded      boolean NOT NULL DEFAULT false,
            applies_to      text,
            source_kind     text NOT NULL
                            CHECK (source_kind IN ('primary','secondary','derived','estimate','none')),
            source_url      text,
            source_title    text NOT NULL,
            retrieved_at    date NOT NULL,
            confidence      real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            verify          boolean NOT NULL,
            note            text,
            CHECK (effective_to IS NULL OR effective_to >= effective_from)
        )
        """
    )
    op.execute("CREATE INDEX ix_pack_value_version_key ON pack_value (version_id, key)")


def downgrade() -> None:
    op.execute("DROP TABLE pack_value")
    op.execute("DROP TABLE catalog_entry")
    op.execute("DROP TABLE library_version")
