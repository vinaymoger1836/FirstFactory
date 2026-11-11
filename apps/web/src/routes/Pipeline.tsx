import { useEffect, useRef, useState } from "react";
import { PageTitle } from "../components/AppShell";
import { API_BASE } from "../lib/api";
import "./pipeline.css";

interface Progress {
  step: string;
  index: number;
  total: number;
  pct: number;
}

const STAGES = [
  "Intake", "BoM", "Routing", "Quality", "Capacity", "Inventory", "Work orders & shifts",
  "Utilities", "Layout", "Simulation", "Finance", "Reports", "Vendors",
];

export function Pipeline() {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [state, setState] = useState<"idle" | "running" | "done" | "error">("idle");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => () => esRef.current?.close(), []);

  function start() {
    esRef.current?.close();
    setProgress(null);
    setState("running");
    const es = new EventSource(`${API_BASE}/events/demo?interval_ms=450`);
    esRef.current = es;
    es.addEventListener("progress", (e) => setProgress(JSON.parse((e as MessageEvent<string>).data) as Progress));
    es.addEventListener("done", () => {
      setState("done");
      es.close();
    });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) return;
      setState("error");
      es.close();
    };
  }

  const pct = progress?.pct ?? 0;
  return (
    <>
      <PageTitle>Pipeline</PageTitle>
      <p className="muted">
        Thirteen stages, each proposed by an agent, computed by engines and approved by you.
      </p>
      <ol className="stage-list">
        {STAGES.map((s, i) => (
          <li key={s} className="stage-chip">
            <span className="stage-chip__n" aria-hidden>
              {i < 12 ? `S${i + 1}` : "P1"}
            </span>
            {s}
          </li>
        ))}
      </ol>

      <section className="card" aria-labelledby="sse-heading" style={{ marginTop: "var(--space-6)" }}>
        <h2 id="sse-heading">Live progress demo</h2>
        <p className="muted">Streams the steps of one stage run over server-sent events.</p>
        <button type="button" className="btn btn-primary" onClick={start} disabled={state === "running"}>
          {state === "running" ? "Running…" : state === "idle" ? "Run demo stage" : "Run again"}
        </button>
        <div
          className="progress"
          role="progressbar"
          aria-label="Stage progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          aria-valuetext={progress ? `${progress.step}, ${pct}%` : "Not started"}
        >
          <div className="progress__bar" style={{ transform: `scaleX(${pct / 100})` }} />
        </div>
        <p role="status" aria-live="polite">
          {state === "running" && progress && `Step ${progress.index} of ${progress.total}: ${progress.step}`}
          {state === "done" && "Done: awaiting approval."}
          {state === "error" && "Connection lost. Is the API running?"}
        </p>
      </section>
    </>
  );
}
