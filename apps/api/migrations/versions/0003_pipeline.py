"""pipeline: stage runs, attachments, LLM call log, change sets, threads, events

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16

docs/02 §2-§7, docs/04 §2-§3 (P2). Artifacts live in ``stage_run.artifact`` (JSONB, validated
by Pydantic on write); LangGraph keeps only IDs in its own checkpoint tables, which
``fl-agents setup`` creates.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

STAGES = (
    "'intake','bom','routing','quality','capacity','inventory','workorders_shifts',"
    "'utilities','layout','simulation','finance','reports','vendors'"
)
STATUSES = (
    "'pending','running','awaiting_approval','approved','rejected','stale','failed','paused'"
)


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE project
            ADD COLUMN catalog_version  text,
            ADD COLUMN pack_versions    jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN template         jsonb,
            ADD COLUMN template_version integer NOT NULL DEFAULT 1,
            ADD COLUMN status           text NOT NULL DEFAULT 'draft'
                                        CHECK (status IN ('draft', 'submitted')),
            ADD COLUMN stage_budgets    jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN attachment_seq   integer NOT NULL DEFAULT 0
        """
    )
    op.execute(
        f"""
        CREATE TABLE stage_run (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project (id) ON DELETE CASCADE,
            stage         text NOT NULL CHECK (stage IN ({STAGES})),
            version       integer NOT NULL CHECK (version >= 1),
            status        text NOT NULL CHECK (status IN ({STATUSES})),
            inputs_hash   char(64) NOT NULL,
            upstream      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            params        jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            artifact      jsonb,
            summary       text NOT NULL DEFAULT '',
            assumptions   jsonb NOT NULL DEFAULT '[]'::jsonb,
            questions     jsonb NOT NULL DEFAULT '[]'::jsonb,
            issues        jsonb NOT NULL DEFAULT '[]'::jsonb,
            notices       jsonb NOT NULL DEFAULT '[]'::jsonb,
            placeholder   boolean NOT NULL DEFAULT false,
            tokens_used   integer NOT NULL DEFAULT 0,
            token_budget  integer NOT NULL DEFAULT 0,
            work          jsonb NOT NULL DEFAULT '{{}}'::jsonb,   -- scratch while running
            thread_id     text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (project_id, stage, version)
        )
        """
    )
    op.execute("CREATE INDEX ix_stage_run_project_stage ON stage_run (project_id, stage)")
    op.execute(
        """
        CREATE TABLE attachment (
            project_id    uuid NOT NULL REFERENCES project (id) ON DELETE CASCADE,
            id            text NOT NULL,                 -- att_01, att_02 ... (stable per project)
            kind          text NOT NULL
                          CHECK (kind IN ('step', 'stl', 'photo', 'pdf', 'bom_csv', 'text')),
            filename      text NOT NULL,
            mime          text NOT NULL,
            size_bytes    bigint NOT NULL,
            sha256        char(64) NOT NULL,
            path          text NOT NULL,                 -- relative to the uploads volume
            status        text NOT NULL DEFAULT 'uploaded'
                          CHECK (status IN ('uploaded', 'processing', 'done', 'failed', 'skipped')),
            meta          jsonb NOT NULL DEFAULT '{}'::jsonb,
            derived_path  text,                          -- relative to the derived volume (GLB)
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE llm_call (
            id                bigserial PRIMARY KEY,
            project_id        uuid REFERENCES project (id) ON DELETE CASCADE,
            stage_run_id      uuid REFERENCES stage_run (id) ON DELETE CASCADE,
            role              text NOT NULL,
            model             text NOT NULL,
            fallback_from     text,
            prompt_tokens     integer NOT NULL DEFAULT 0,
            completion_tokens integer NOT NULL DEFAULT 0,
            latency_ms        integer NOT NULL DEFAULT 0,
            status            text NOT NULL CHECK (status IN
                                  ('ok', 'rate_limited', 'error', 'replay', 'record')),
            cassette_key      char(64),
            error             text,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_llm_call_run ON llm_call (stage_run_id)")
    op.execute("CREATE INDEX ix_llm_call_created ON llm_call (created_at)")
    op.execute(
        f"""
        CREATE TABLE change_set (
            project_id     uuid NOT NULL REFERENCES project (id) ON DELETE CASCADE,
            id             text NOT NULL,
            stage          text NOT NULL CHECK (stage IN ({STAGES})),
            base_version   integer NOT NULL,
            result_version integer,
            ops            jsonb NOT NULL,
            diff           jsonb NOT NULL DEFAULT '[]'::jsonb,
            reason         text NOT NULL,
            origin         text NOT NULL CHECK (origin IN ('user', 'simulation', 'agent')),
            status         text NOT NULL DEFAULT 'proposed'
                           CHECK (status IN ('proposed', 'applied', 'rejected')),
            created_at     timestamptz NOT NULL DEFAULT now(),
            applied_at     timestamptz,
            PRIMARY KEY (project_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE pipeline_thread (
            thread_id     text PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project (id) ON DELETE CASCADE,
            status        text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'superseded', 'done')),
            started_from  text,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_pipeline_thread_project ON pipeline_thread (project_id)")
    op.execute(
        """
        CREATE TABLE stage_event (
            id          bigserial PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project (id) ON DELETE CASCADE,
            stage       text,
            run_id      uuid,
            kind        text NOT NULL,
            payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_stage_event_project ON stage_event (project_id, id)")
    # Wake SSE listeners; the payload is only the project id, events are read from the table.
    op.execute(
        """
        CREATE FUNCTION stage_event_notify() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('fl_events', NEW.project_id::text);
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER stage_event_notify AFTER INSERT ON stage_event
        FOR EACH ROW EXECUTE FUNCTION stage_event_notify()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER stage_event_notify ON stage_event")
    op.execute("DROP FUNCTION stage_event_notify()")
    for table in (
        "stage_event",
        "pipeline_thread",
        "change_set",
        "llm_call",
        "attachment",
        "stage_run",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute(
        """
        ALTER TABLE project
            DROP COLUMN catalog_version, DROP COLUMN pack_versions, DROP COLUMN template,
            DROP COLUMN template_version, DROP COLUMN status, DROP COLUMN stage_budgets,
            DROP COLUMN attachment_seq
        """
    )
