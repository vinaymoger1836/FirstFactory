import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { PageTitle } from "../components/AppShell";
import { api, type ProjectCreated } from "../lib/api";

export function NewProject() {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ProjectCreated | null>(null);
  const [copied, setCopied] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const p = await api<ProjectCreated>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: name.trim() || "Untitled project" }),
      });
      setCreated(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the project.");
    } finally {
      setBusy(false);
    }
  }

  const fullLink = created ? `${window.location.origin}${created.link}` : "";

  return (
    <>
      <PageTitle>New project</PageTitle>
      {!created ? (
        <form className="card" style={{ maxWidth: "32rem" }} onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="project-name">Project name</label>
            <input
              id="project-name"
              className="input"
              value={name}
              maxLength={200}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Power bank line"
              aria-describedby="project-name-hint"
            />
            <span id="project-name-hint" className="muted">
              No sign-up. You get a private link; anyone with it can open the project.
            </span>
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy} aria-busy={busy}>
            {busy ? "Creating…" : "Create project"}
          </button>
          {error && (
            <p className="notice" data-tone="bad" role="alert" style={{ marginTop: "var(--space-4)" }}>
              {error}
            </p>
          )}
        </form>
      ) : (
        <section className="card" aria-labelledby="created-heading" style={{ maxWidth: "40rem" }}>
          <h2 id="created-heading">Project created</h2>
          <p>Keep this link safe. It is shown once and is the only way back to the project.</p>
          <p>
            <code>{fullLink}</code>
          </p>
          <p style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn"
              onClick={() => {
                void navigator.clipboard.writeText(fullLink).then(() => setCopied(true));
              }}
            >
              {copied ? "Copied" : "Copy link"}
            </button>
            <Link className="btn btn-primary" to={created.link}>
              Open project
            </Link>
          </p>
          <p className="muted" role="status">
            Expires {new Date(created.expires_at).toLocaleDateString()} after 30 days without activity.
          </p>
        </section>
      )}
    </>
  );
}
