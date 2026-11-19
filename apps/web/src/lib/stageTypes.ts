import type { ProvLike } from "./prov";

/** What the S3, S4 and vendor panels read from the artifacts (a subset of the domain models). */

export interface TimeStd {
  cycle_s: ProvLike;
  setup_s?: ProvLike | null;
  method: "user" | "catalog_formula" | "mtm_table" | "llm_estimate" | "measured";
  formula?: string | null;
}

export interface RoutingOp {
  op_no: string;
  op_key?: string | null;
  item: string;
  process: { code: string };
  description: string;
  workcenter_class: string;
  task_type: string;
  consumes: string[];
  produces: string;
  time: TimeStd;
  variant_times: Record<string, TimeStd>;
  sample_pct: number;
  quality_gate?: string | null;
  batch?: { batch_size: number; cycle_s: ProvLike; availability: number } | null;
  origin?: string;
  note?: string | null;
}

export interface RoutingFindingT {
  id: string;
  kind: string;
  text: string;
  op_key?: string | null;
  part_no?: string | null;
  blocking: boolean;
  status: "open" | "answered" | "info";
}

export interface RoutingArtifact {
  variants: string[];
  operations: RoutingOp[];
  precedence: { from_op: string; to_op: string }[];
  proposer?: string;
  findings?: RoutingFindingT[];
  totals?: { variant: string; cycle_s: number; manual_s: number; machine_s: number; test_s: number }[];
}

export interface AqlPlanT {
  lot_size: number;
  inspection_level: string;
  aql: number;
  code_letter: string;
  sample_size: number;
  accept: number;
  reject: number;
  full_inspection?: boolean;
  table_row?: string;
}

export interface CheckRecordT {
  id: string;
  check_type: string;
  name: string;
  layer: "iqc" | "ipqc" | "eol" | "oqc" | "lab";
  applies_to: string;
  characteristic: string;
  limit: string;
  method: string;
  instrument_class: string;
  frequency: string;
  reaction: string;
  sample: string;
  certifications: string[];
}

export interface QualityArtifact {
  iqc: {
    part_no: string;
    name?: string | null;
    commodity_class?: string | null;
    check_types: string[];
    inline_checks?: string[];
    plan: AqlPlanT;
    lot_days?: number | null;
    inspector_h_per_day: ProvLike;
  }[];
  inline: { check_type: string; op_no: string; coverage_pct: number }[];
  eol: { check_type: string; op_no: string; coverage_pct: number }[];
  patrol: { check_type: string; per_shift: number }[];
  oqc: { check_type: string; plan?: AqlPlanT | null; frequency?: string | null; inspector_h_per_day: ProvLike }[];
  lab: { name: string; check_type?: string | null; qty: number; frequency: string }[];
  rework: { op_no: string; fpy: number; units_per_day: number; load_s_per_day: ProvLike }[];
  compliance: { pack_key: string; title: string; supported_by_checks: string[]; note: string }[];
  test_policy?: string | null;
  capacity_sample_pct?: number | null;
  inspector_h_per_day?: ProvLike | null;
  quarantine_area_m2?: ProvLike | null;
  checks?: CheckRecordT[];
  basis?: {
    annual_volume?: ProvLike | null;
    working_days_per_year?: ProvLike | null;
    daily_by_variant: Record<string, number>;
    iqc_lot_days?: ProvLike | null;
    inspection_level: string;
  } | null;
  policy_impact?: { policy: string; label: string; coverage_pct: number; rack_hours_per_day: number; channels: number }[];
  notes?: string[];
}

export interface VendorEvidenceT {
  url: string;
  fetched_at: string;
  snippet: string;
  source_kind: "maker" | "directory" | "other";
}

export interface VendorT {
  name: string;
  city?: string | null;
  state?: string | null;
  products: string[];
  certifications: string[];
  moq?: string | null;
  indicative_price?: string | null;
  lead_time?: string | null;
  contact_url?: string | null;
  evidence: VendorEvidenceT[];
  score: number;
  domain: string;
  score_parts?: Record<string, number>;
}

export interface VendorClassT {
  vendor_class: string;
  name?: string;
  vendors: VendorT[];
  notes: string;
  queries_run: string[];
  gaps: string[];
  cached_at?: string | null;
  pages_read?: number;
  pages_dropped?: number;
}

export interface VendorArtifact {
  classes: VendorClassT[];
  region: string;
  disclaimer: string;
  generated_at?: string;
  notes?: string[];
  skipped_classes?: string[];
}

export function routingBlockers(a: RoutingArtifact | null | undefined): number {
  return (a?.findings ?? []).filter((f) => f.blocking && f.status === "open").length;
}
