/** Provenance helpers (ADR-016): shapes, labels, value formatting. */
export interface ProvLike {
  value: unknown;
  unit?: string | null;
  source: string;
  confidence: number;
  ref?: string | null;
  note?: string | null;
}

export const SOURCE_LABEL: Record<string, string> = {
  user: "You",
  cad: "CAD",
  datasheet: "Datasheet",
  bom_file: "BoM file",
  vision: "Photo",
  llm: "AI",
  library: "Catalog",
  web: "Web",
  engine: "Calculated",
};

export function isProv(x: unknown): x is ProvLike {
  return !!x && typeof x === "object" && "value" in x && "source" in x && "confidence" in x;
}

export function fmtValue(v: unknown, unit?: string | null): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map((x) => fmtValue(x)).join(" × ") + (unit ? ` ${unit}` : "");
  if (typeof v === "number") {
    const s = Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toLocaleString("en-IN", { maximumFractionDigits: 3 });
    return unit && unit !== "unit" ? `${s} ${unit}` : s;
  }
  return String(v) + (unit && unit !== "unit" ? ` ${unit}` : "");
}
