import { useId, useState } from "react";

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

/** Source and confidence of a value, with details on demand (ADR-016 provenance badges). */
export function ProvBadge({ p }: { p: ProvLike }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const low = p.confidence < 0.6;
  const pct = Math.round(p.confidence * 100);
  return (
    <span className="prov">
      <button
        type="button"
        className="prov__badge"
        data-source={p.source}
        data-low={low}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
      >
        <span>{SOURCE_LABEL[p.source] ?? p.source}</span>
        <span className="prov__meter" aria-hidden="true">
          <span style={{ transform: `scaleX(${p.confidence})` }} />
        </span>
        <span className="visually-hidden">
          , confidence {pct}%{low ? ", low: please check" : ""}. Show details
        </span>
      </button>
      <span id={id} className="prov__detail" hidden={!open} role="note">
        From {SOURCE_LABEL[p.source] ?? p.source}, confidence {pct}%{low ? " (low, please check)" : ""}.
        {p.ref && <> Evidence: <code>{p.ref}</code>.</>}
        {p.note && <> {p.note}</>}
      </span>
    </span>
  );
}
