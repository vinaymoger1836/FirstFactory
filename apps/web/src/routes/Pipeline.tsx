import { Link } from "react-router";
import { PageTitle } from "../components/AppShell";
import { PipelineGraph } from "../components/PipelineGraph";
import { STAGES } from "../lib/stages";
import "./project.css";

/** "How it works": the 13-stage graph (docs/02 §1) with what each stage does. */
export function Pipeline() {
  return (
    <>
      <PageTitle>How the pipeline works</PageTitle>
      <p className="muted">
        Thirteen stages turn your product into a factory design. Each stage waits for the stages it depends on,
        proposes its part, shows where every number came from, and waits for your approval. Change something upstream
        and the stages after it are marked out of date, so nothing is lost and nothing goes stale silently.
      </p>
      <PipelineGraph
        label="Pipeline stages and how they depend on each other"
        stages={STAGES.map((s) => ({ key: s.key, status: "pending", version: null, placeholder: false }))}
      />
      <ol className="stage-rows">
        {STAGES.map((s) => (
          <li key={s.key} className="stage-row">
            <span className="stage-row__code">{s.code}</span>
            <span className="stage-row__main">
              <span className="stage-row__label">{s.label}</span>
              <span className="muted small">{s.blurb}</span>
              {s.deps.length > 0 && (
                <span className="muted small">
                  Needs: {s.deps.map((d) => STAGES.find((x) => x.key === d)?.label ?? d).join(", ")}
                </span>
              )}
            </span>
          </li>
        ))}
      </ol>
      <p>
        <Link className="btn btn-primary" to="/new">
          Start a project
        </Link>
      </p>
    </>
  );
}
