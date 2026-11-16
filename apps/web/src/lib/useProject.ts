import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PipelineOut, type ProjectOut } from "./api";

export interface Activity {
  stage: string;
  step: string;
  pct: number;
  source?: string | undefined;
}

export function useProject(token: string) {
  const [project, setProject] = useState<ProjectOut | null>(null);
  const [pipeline, setPipeline] = useState<PipelineOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(() => {
    api<PipelineOut>("/projects/current/pipeline", {}, token)
      .then(setPipeline)
      .catch(() => undefined);
  }, [token]);

  const refreshSoon = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(refresh, 250);
  }, [refresh]);

  useEffect(() => {
    let live = true;
    api<ProjectOut>("/projects/current", {}, token)
      .then((p) => {
        if (!live) return;
        setProject(p);
        refresh();
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : "Not found"));
    return () => {
      live = false;
      window.clearTimeout(timer.current);
    };
  }, [token, refresh]);

  return { project, pipeline, error, refresh, refreshSoon };
}
