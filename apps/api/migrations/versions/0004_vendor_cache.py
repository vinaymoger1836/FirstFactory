"""vendor_cache: shared vendor research results

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19

docs/09 §2.2, docs/04 §2: results of vendor research are shared across projects, keyed by
commodity class and region, and expire after the catalog policy's TTL (30 days). Queries
carry only the class name and region, so a cached row holds nothing about any project.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE vendor_cache (
            cache_key    text PRIMARY KEY,
            vendor_class text NOT NULL,
            region       text NOT NULL,
            payload      jsonb NOT NULL,
            fetched_at   timestamptz NOT NULL,
            expires_at   timestamptz NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_vendor_cache_expires ON vendor_cache (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE vendor_cache")
