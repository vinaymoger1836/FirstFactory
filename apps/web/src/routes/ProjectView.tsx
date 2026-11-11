import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { PageTitle } from "../components/AppShell";
import { api, type ProjectOut } from "../lib/api";

export function ProjectView() {
  const { token = "" } = useParams();
  const [project, setProject] = useState<ProjectOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api<ProjectOut>("/projects/current", { headers: { "X-Project-Token": token } })
      .then((p) => live && setProject(p))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Not found"));
    return () => {
      live = false;
    };
  }, [token]);

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
  return (
    <>
      <PageTitle>{project.name}</PageTitle>
      <p className="muted">
        Created {new Date(project.created_at).toLocaleString()} · expires{" "}
        {new Date(project.expires_at).toLocaleDateString()}
      </p>
      <p className="notice">The product template wizard arrives in phase 2.</p>
    </>
  );
}
