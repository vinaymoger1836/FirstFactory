import type { ProductTemplate } from "@fl/ts-types";

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8080";

export interface ApiIssue {
  path: string;
  message: string;
  code?: string;
  severity?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly issues: ApiIssue[] = [],
  ) {
    super(message);
  }
}

function errorFrom(status: number, statusText: string, body: unknown): ApiError {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return new ApiError(status, d);
    if (d && typeof d === "object" && "message" in d) {
      const o = d as { message: unknown; issues?: unknown };
      const issues = Array.isArray(o.issues) ? (o.issues as ApiIssue[]) : [];
      return new ApiError(status, String(o.message), issues);
    }
    if (Array.isArray(d)) {
      const issues = (d as { loc?: unknown[]; msg?: string }[]).map((e) => ({
        path: (e.loc ?? []).slice(1).join("/"),
        message: e.msg ?? "invalid",
      }));
      return new ApiError(status, "The request is not valid", issues);
    }
  }
  return new ApiError(status, statusText || `HTTP ${status}`);
}

/** JSON API call. A string body is sent as JSON; FormData as multipart. */
export async function api<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (typeof init.body === "string") headers["Content-Type"] = "application/json";
  if (token) headers["X-Project-Token"] = token;
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
  });
  if (res.status === 204) return undefined as T;
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok && res.status !== 503) throw errorFrom(res.status, res.statusText, body);
  return body as T;
}

export const post = <T>(path: string, body: unknown, token?: string) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }, token);

// ── Types (responses not covered by the generated domain types) ──────────

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
  status: "draft" | "submitted";
  catalog_version: string | null;
  pack_versions: Record<string, string>;
  template: ProductTemplate | null;
  thread_status: string | null;
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

export interface BomMapping {
  part_no: string | null;
  name: string | null;
  qty: string | null;
  uom: string | null;
  material: string | null;
  variant_qty: Record<string, string>;
  confirmed: boolean;
}

export interface BomPreview {
  headers: string[];
  sample: string[][];
  row_count: number;
  proposal: BomMapping;
  mapping_source: string;
  warnings: string[];
}

export interface AssemblyNodeOut {
  id: string;
  name: string;
  children: AssemblyNodeOut[];
  part_no: string | null;
}

export interface AttachmentOut {
  id: string;
  kind: "step" | "stl" | "photo" | "pdf" | "bom_csv" | "text";
  filename: string;
  mime: string;
  size_bytes: number;
  status: string;
  sha256: string;
  bom: BomPreview | null;
  cad: { tree?: AssemblyNodeOut; parts?: unknown[]; extents_m?: number[]; solids?: number } | null;
  has_glb: boolean;
}

export type StageStatus =
  | "pending"
  | "running"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "stale"
  | "failed"
  | "paused";

export interface StageSummary {
  stage: string;
  status: StageStatus;
  version: number | null;
  run_id: string | null;
  placeholder: boolean;
  depends_on: string[];
}

export interface PipelineOut {
  stages: StageSummary[];
  thread_status: string | null;
  stale: string[];
}

export interface QuestionOut {
  id: string;
  stage: string;
  kind: "choice" | "number" | "text" | "file";
  text: string;
  path: string | null;
  unit: string | null;
  options: string[];
  candidates: Record<string, unknown>[];
  answer: string | null;
}

export interface IssueOut {
  code: string;
  message: string;
  path: string;
  severity: "error" | "warning";
}

export interface AssumptionOut {
  id: string;
  path: string;
  value: unknown;
  unit: string | null;
  source: string;
  confidence: number;
  ref: string | null;
  note: string | null;
}

export interface RunOut {
  id: string;
  stage: string;
  version: number;
  status: StageStatus;
  inputs_hash: string;
  upstream: Record<string, number>;
  params: Record<string, unknown>;
  artifact: Record<string, unknown> | null;
  summary: string;
  assumptions: AssumptionOut[];
  questions: QuestionOut[];
  issues: IssueOut[];
  notices: string[];
  placeholder: boolean;
  tokens_used: number;
  token_budget: number;
  created_at: string;
  updated_at: string | null;
}

export interface StageOut {
  stage: string;
  depends_on: string[];
  latest: RunOut | null;
  versions: { version: number; status: StageStatus; placeholder: boolean; created_at: string }[];
}

export interface UsageRow {
  stage: string | null;
  role: string;
  model: string;
  status: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}
