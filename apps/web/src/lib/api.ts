export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8080";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok && res.status !== 503) {
    const detail =
      body && typeof body === "object" && "detail" in body ? String(body.detail) : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export interface ProjectCreated {
  id: string;
  name: string;
  token: string;
  link: string;
  expires_at: string;
}

export interface ProjectOut {
  id: string;
  name: string;
  created_at: string;
  expires_at: string;
}

export interface ReadyCheck {
  ok: boolean;
  error?: string;
  age_s?: number;
  arch?: string;
  [k: string]: unknown;
}

export interface Ready {
  status: "ok" | "degraded";
  checks: Record<string, ReadyCheck>;
}
