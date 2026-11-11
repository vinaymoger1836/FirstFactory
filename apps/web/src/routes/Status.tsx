import { useCallback, useEffect, useState } from "react";
import { PageTitle } from "../components/AppShell";
import { api, type Ready } from "../lib/api";

export function Status() {
  const [ready, setReady] = useState<Ready | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<Ready>("/health/ready")
      .then(setReady)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "API unreachable"));
  }, []);

  useEffect(load, [load]);

  return (
    <>
      <PageTitle>System status</PageTitle>
      <p>
        <button type="button" className="btn" onClick={load}>
          Refresh
        </button>
      </p>
      {error && (
        <p className="notice" data-tone="bad" role="alert">
          API unreachable: {error}
        </p>
      )}
      {ready && (
        <>
          <p role="status">
            Overall:{" "}
            <span className="badge" data-tone={ready.status === "ok" ? "ok" : "bad"}>
              {ready.status === "ok" ? "All systems ready" : "Degraded"}
            </span>
          </p>
          <ul className="grid" style={{ listStyle: "none", padding: 0 }}>
            {Object.entries(ready.checks).map(([name, c]) => (
              <li key={name} className="card">
                <h2 style={{ fontSize: "var(--text-lg)" }}>{name}</h2>
                <span className="badge" data-tone={c.ok ? "ok" : "bad"}>
                  {c.ok ? "Ready" : "Not ready"}
                </span>
                <p className="muted" style={{ margin: "var(--space-3) 0 0", fontSize: "var(--text-sm)" }}>
                  {c.error ?? [c.arch, c.age_s !== undefined ? `heartbeat ${c.age_s}s ago` : null]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
