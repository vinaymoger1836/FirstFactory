import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { PageTitle } from "../components/AppShell";
import { PipelineGraph } from "../components/PipelineGraph";
import { ApiError, post } from "../lib/api";
import { useProject, type Activity } from "../lib/useProject";
import { useProjectEvents, type StreamEvent } from "../lib/sse";
import { STAGES, STATUS_LABEL, STATUS_TONE, STEP_LABEL } from "../lib/stages";
import "./project.css";

export function ProjectView() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const { project, pipeline, error, refreshSoon } = useProject(token);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const onEvent = useCallback(
    (e: StreamEvent) => {
      const d = e.data;
      if (e.event === "progress") {
        setActivity({ stage: String(d.stage), step: String(d.step), pct: Number(d.pct) || 0 });
      } else if (e.event === "progress_detail") {
        setActivity((a) => (a ? { ...a, source: String(d.source) } : a));
      } else if (["status", "version", "skipped", "pipeline", "paused"].includes(e.event)) {
        refreshSoon();
        if (e.event === "status" && d.status === "awaiting_approval") {
          const label = STAGES.find((s) => s.key === d.stage)?.label ?? String(d.stage);
          setMessage(`${label} is ready for your review.`);
          setActivity(null);
        }
        if (e.event === "pipeline" && d.status === "done") setActivity(null);
      }
    },
    [refreshSoon],
  );
  useProjectEvents(project ? token : null, onEvent);

  const open = useCallback((key: string) => void navigate(`/p/${token}/stage/${key}`), [navigate, token]);

  async function rerunStale() {
    setBusy(true);
    try {
      await post("/projects/current/rerun-stale", {}, token);
      setMessage("Re-running the stages that are out of date.");
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Could not start the re-run.");
    } finally {
      setBusy(false);
    }
  }

  if (error)
    return (
      <>
        <PageTitle>Project not found</PageTitle>
        <p className="notice" data-tone="bad" role="alert">
          This link is not valid or the project has expired.
        </p>
      </>
    );
  if (!project)
    return (
      <p role="status" aria-live="polite">
        Loading project…
      </p>
    );

  const stages = pipeline?.stages ?? [];
  const stale = pipeline?.stale ?? [];
  const waiting = stages.filter((s) => s.status === "awaiting_approval" || s.status === "paused");

  return (
    <>
      <PageTitle>{project.name}</PageTitle>
      <p className="muted">
        Catalog {project.catalog_version ?? "?"} · Karnataka pack {project.pack_versions.KA ?? "?"} · link expires{" "}
        {new Date(project.expires_at).toLocaleDateString()}
      </p>
      <p className="visually-hidden" role="status" aria-live="polite">
        {message}
      </p>

      {project.status === "draft" && (
        <p className="notice" data-tone="warn">
          This project has not been submitted yet. Finish the <Link to="/new">wizard</Link> to start the pipeline.
        </p>
      )}

      {waiting.map((s) => {
        const info = STAGES.find((x) => x.key === s.stage);
        return (
          <div key={s.stage} className="callout" data-tone={s.status === "paused" ? "warn" : "run"}>
            <span>
              <strong>{info?.label}</strong>{" "}
              {s.status === "paused" ? "is paused (token budget or model quota)." : "is waiting for your review."}
            </span>
            <Link className="btn btn-primary btn-small" to={`/p/${token}/stage/${s.stage}`}>
              {s.status === "paused" ? "Open and continue" : "Review"}
              <span className="visually-hidden"> {info?.label}</span>
            </Link>
          </div>
        );
      })}

      {stale.length > 0 && (
        <div className="callout" data-tone="stale">
          <span>
            {stale.length} stage{stale.length > 1 ? "s are" : " is"} out of date after an upstream change.
          </span>
          <button type="button" className="btn btn-small" onClick={() => void rerunStale()} disabled={busy} aria-busy={busy}>
            Re-run stale stages
          </button>
        </div>
      )}

      {activity && (
        <div className="activity card" aria-live="polite">
          <div className="activity__text">
            <span className="pulse" aria-hidden="true" />
            {STAGES.find((s) => s.key === activity.stage)?.label}: {STEP_LABEL[activity.step] ?? activity.step}
            {activity.source ? ` (${activity.source})` : ""}
          </div>
          <div
            className="progress"
            role="progressbar"
            aria-label="Current stage progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={activity.pct}
          >
            <div className="progress__bar" style={{ transform: `scaleX(${activity.pct / 100})` }} />
          </div>
        </div>
      )}

      <section aria-labelledby="pl-h" className="pipeline-section">
        <h2 id="pl-h">Pipeline</h2>
        <PipelineGraph
          label="Pipeline graph. Each stage is a button; the same stages are listed below."
          stages={stages.map((s) => ({
            key: s.stage,
            status: s.status,
            version: s.version,
            placeholder: s.placeholder,
            step: activity?.stage === s.stage ? activity.step : undefined,
          }))}
          onOpen={open}
        />
        <ol className="stage-rows">
          {STAGES.map((info) => {
            const s = stages.find((x) => x.stage === info.key);
            const status = s?.status ?? "pending";
            return (
              <li key={info.key}>
                <Link to={`/p/${token}/stage/${info.key}`} className="stage-row" data-tone={STATUS_TONE[status]}>
                  <span className="stage-row__code">{info.code}</span>
                  <span className="stage-row__main">
                    <span className="stage-row__label">{info.label}</span>
                    <span className="muted small">{info.blurb}</span>
                  </span>
                  <span className="badge" data-tone={STATUS_TONE[status]}>
                    {STATUS_LABEL[status]}
                    {s?.version ? ` · v${s.version}` : ""}
                  </span>
                  {s?.placeholder && <span className="badge">Placeholder</span>}
                </Link>
              </li>
            );
          })}
        </ol>
      </section>
    </>
  );
}
