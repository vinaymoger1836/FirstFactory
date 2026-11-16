import type { Bom, BomLine, Item } from "@fl/ts-types";
import type { RunOut } from "./api";

/** BoM view helpers (EBOM lines grouped per line id across variants). */
export interface BomRow {
  id: string;
  line: BomLine;
  item: Item | undefined;
  qty: Record<string, number>;
  cost: Record<string, number | null>;
}

function baseId(l: BomLine): string {
  const id = l.id ?? `${l.parent}>${l.child}`;
  return l.variant && id.endsWith(`-${l.variant}`) ? id.slice(0, -(l.variant.length + 1)) : id;
}

export function rowsOf(bom: Bom): BomRow[] {
  const items = new Map(bom.items.map((i) => [i.part_no, i]));
  const rows = new Map<string, BomRow>();
  for (const l of bom.ebom) {
    const id = baseId(l);
    const r = rows.get(id) ?? { id, line: l, item: items.get(l.child), qty: {}, cost: {} };
    for (const v of l.variant ? [l.variant] : bom.variants) {
      r.qty[v] = l.qty.value;
      r.cost[v] = l.ext_cost_inr ? l.ext_cost_inr.base : null;
    }
    rows.set(id, r);
  }
  return [...rows.values()];
}

export function bomBlockers(bom: Bom): number {
  const suggested = rowsOf(bom).filter((r) => r.line.review === "suggested").length;
  const findings = (bom.findings ?? []).filter((f) => f.status === "open" && f.kind !== "note").length;
  const mb = (bom.make_buy_decisions ?? []).filter((d) => !d.confirmed).length;
  return suggested + findings + mb;
}

export function blockersOf(run: RunOut | null): number {
  if (!run || run.stage !== "bom" || !run.artifact) return 0;
  return bomBlockers(run.artifact as unknown as Bom);
}
