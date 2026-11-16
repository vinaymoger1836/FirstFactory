import type { StageStatus } from "./api";

export interface StageInfo {
  key: string;
  code: string;
  label: string;
  blurb: string;
  deps: string[];
}

// docs/02 §1 (the same map as fl_domain.common.STAGE_DEPS).
export const STAGES: StageInfo[] = [
  { key: "intake", code: "S1", label: "Intake", blurb: "Reads your template and files into one product spec.", deps: [] },
  { key: "bom", code: "S2", label: "Bill of materials", blurb: "Parts list, sub-assemblies, make or buy.", deps: ["intake"] },
  { key: "routing", code: "S3", label: "Routing", blurb: "Operations, sequence and time standards.", deps: ["bom"] },
  { key: "quality", code: "S4", label: "Quality plan", blurb: "Checks, sampling and test stations.", deps: ["routing"] },
  { key: "capacity", code: "S5", label: "Capacity", blurb: "Takt, stations and three line strategies.", deps: ["routing", "quality"] },
  { key: "inventory", code: "S6", label: "Inventory", blurb: "Stock policy, storage and material flow.", deps: ["bom", "capacity"] },
  { key: "workorders_shifts", code: "S7", label: "Work orders & shifts", blurb: "Work-order scheme and rosters.", deps: ["routing", "capacity"] },
  { key: "utilities", code: "S8", label: "Utilities", blurb: "Power, air, HVAC, water and fire.", deps: ["capacity", "inventory", "workorders_shifts"] },
  { key: "layout", code: "S9", label: "Layout", blurb: "2D block layout and 3D factory.", deps: ["quality", "capacity", "inventory", "workorders_shifts", "utilities"] },
  { key: "simulation", code: "S10", label: "Simulation", blurb: "Proves the line can hit the volume.", deps: ["capacity", "workorders_shifts", "layout"] },
  { key: "finance", code: "S11", label: "Finance", blurb: "Capex, opex, P&L, NPV and IRR.", deps: ["bom", "capacity", "inventory", "workorders_shifts", "utilities", "layout", "vendors"] },
  { key: "reports", code: "S12", label: "Reports", blurb: "Report pack, exports and the factory package.", deps: ["intake", "bom", "routing", "quality", "capacity", "inventory", "workorders_shifts", "utilities", "layout", "simulation", "finance", "vendors"] },
  { key: "vendors", code: "P1", label: "Vendors", blurb: "Supplier research with sources (runs beside S3).", deps: ["bom"] },
];

export const STAGE_BY_KEY: Record<string, StageInfo> = Object.fromEntries(
  STAGES.map((s) => [s.key, s]),
);

export const STATUS_LABEL: Record<StageStatus, string> = {
  pending: "Not started",
  running: "Running",
  awaiting_approval: "Waiting for you",
  approved: "Approved",
  rejected: "Rejected",
  stale: "Out of date",
  failed: "Failed",
  paused: "Paused",
};

export type Tone = "ok" | "warn" | "bad" | "stale" | "run" | "idle";

export const STATUS_TONE: Record<StageStatus, Tone> = {
  pending: "idle",
  running: "run",
  awaiting_approval: "warn",
  approved: "ok",
  rejected: "bad",
  stale: "stale",
  failed: "bad",
  paused: "warn",
};

/** Dependency depth of each stage (column in the pipeline graph). */
export function stageDepth(): Record<string, number> {
  const depth: Record<string, number> = {};
  const visit = (k: string): number => {
    if (depth[k] !== undefined) return depth[k];
    const info = STAGE_BY_KEY[k];
    const d = info && info.deps.length ? Math.max(...info.deps.map(visit)) + 1 : 0;
    depth[k] = d;
    return d;
  };
  STAGES.forEach((s) => visit(s.key));
  return depth;
}

export const STEP_LABEL: Record<string, string> = {
  load_inputs: "Loading inputs",
  plan: "Planning",
  tool_loop: "Reading sources",
  draft: "Drafting",
  validate: "Checking",
  compute: "Computing",
  summarise: "Summarising",
  approval: "Ready for review",
};
