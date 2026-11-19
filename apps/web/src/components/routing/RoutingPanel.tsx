import { useEffect, useMemo, useState } from "react";
import type { RunOut } from "../../lib/api";
import { fmtValue } from "../../lib/prov";
import type { RoutingArtifact, RoutingFindingT, RoutingOp, TimeStd } from "../../lib/stageTypes";
import { ProvBadge } from "../ProvBadge";
import { Tabs } from "../ui/Tabs";
import "../ui/ui.css";
import "./routing.css";

type Answers = Record<string, string>;

const METHOD_LABEL: Record<string, string> = {
  user: "Your value",
  catalog_formula: "Catalog formula",
  mtm_table: "MTM elements",
  llm_estimate: "AI estimate",
  measured: "Measured",
};
const TASK_LABEL: Record<string, string> = {
  manual: "Manual",
  machine_paced: "Machine",
  batch: "Batch",
  long_test: "Long test",
  offline_rack: "Rack test",
  inspection: "Inspection",
};

const secs = (s: number) => (s >= 3600 ? `${(s / 3600).toLocaleString("en-IN", { maximumFractionDigits: 1 })} h` : `${s.toLocaleString("en-IN", { maximumFractionDigits: 1 })} s`);

export function RoutingPanel({
  run,
  canReview,
  busy,
  onAnswer,
}: {
  run: RunOut;
  canReview: boolean;
  busy: boolean;
  onAnswer: (answers: Answers) => void;
}) {
  const raw = run.artifact as unknown as Partial<RoutingArtifact>;
  const r: RoutingArtifact = useMemo(
    () => ({
      variants: raw.variants ?? [],
      operations: raw.operations ?? [],
      precedence: raw.precedence ?? [],
      proposer: raw.proposer ?? "",
      findings: raw.findings ?? [],
      totals: raw.totals ?? [],
    }),
    [raw],
  );
  const [pending, setPending] = useState<Answers>({});
  useEffect(() => setPending({}), [run.version]);
  const open = (r.findings ?? []).filter((f) => f.status !== "info");
  const estimates = r.operations.filter((o) => o.time.method === "llm_estimate" || Object.values(o.variant_times).some((t) => t.method === "llm_estimate"));
  const n = Object.keys(pending).length;

  const set = (k: string, v: string | null) =>
    setPending((p) => {
      const next = Object.fromEntries(Object.entries(p).filter(([key]) => key !== k));
      if (v !== null && v !== "") next[k] = v;
      return next;
    });

  return (
    <section className="card routing" aria-label="Routing">
      <p className="ui-muted routing__src">
        Built from {r.proposer || "the catalog's typical routing"}. Times come from catalog formulas; every number is calculated by the engine.
      </p>
      <ul className="ui-stats" aria-label="Time per unit">
        <li className="ui-stat">
          <span className="ui-stat__v">{r.operations.length}</span>
          <span className="ui-stat__l">operations</span>
        </li>
        {(r.totals ?? []).map((t) => (
          <li key={t.variant} className="ui-stat">
            <span className="ui-stat__v">{secs(t.cycle_s)}</span>
            <span className="ui-stat__l">
              work per {t.variant} · manual {secs(t.manual_s)}, machine {secs(t.machine_s)}, test {secs(t.test_s)}
            </span>
          </li>
        ))}
      </ul>

      {estimates.length > 0 && (
        <div className="ui-note" data-tone="warn" role="note">
          <span>
            <strong>{estimates.length} time(s) are AI estimates</strong> with low confidence: {estimates.map((o) => o.op_no).join(", ")}. Replace them with a measured value
            or a catalog time before you rely on the line design.
          </span>
        </div>
      )}

      <Tabs
        label="Routing views"
        tabs={[
          { key: "ops", label: "Operations" },
          { key: "flow", label: "Flow" },
          { key: "items", label: "Open items", badge: open.length },
        ]}
      >
        {(tab) =>
          tab === "ops" ? (
            <Operations r={r} canReview={canReview} pending={pending} set={set} />
          ) : tab === "flow" ? (
            <Flow r={r} />
          ) : (
            <Findings findings={r.findings ?? []} ops={r.operations} canReview={canReview} pending={pending} set={set} />
          )
        }
      </Tabs>

      {canReview && (
        <div className="ui-apply" role="region" aria-label="Apply your changes">
          <span role="status" aria-live="polite">
            {n ? `${n} change${n > 1 ? "s" : ""} ready to apply` : "Change a time, remove a step or acknowledge an item to build a new version."}
          </span>
          <button type="button" className="btn btn-quiet" disabled={!n || busy} onClick={() => setPending({})}>
            Discard
          </button>
          <button type="button" className="btn btn-primary" disabled={!n || busy} onClick={() => onAnswer(pending)}>
            Apply changes
          </button>
        </div>
      )}
    </section>
  );
}

function TimeCell({
  op,
  variant,
  std,
  canReview,
  pending,
  set,
}: {
  op: RoutingOp;
  variant: string | null;
  std: TimeStd;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const key = `time:${op.op_key}:${variant ?? "*"}`;
  const est = std.method === "llm_estimate";
  return (
    <span className="ui-time">
      {canReview && op.op_key ? (
        <input
          className="input ui-input-s"
          type="number"
          min={0}
          step="any"
          inputMode="decimal"
          aria-label={`${op.op_no} time in seconds${variant ? ` for ${variant}` : ""}`}
          value={pending[key] ?? String(Math.round((std.cycle_s.value as number) * 1000) / 1000)}
          onChange={(e) => set(key, Number(e.target.value) === (std.cycle_s.value as number) ? null : e.target.value)}
        />
      ) : (
        <span className="num">{fmtValue(std.cycle_s.value as number)} s</span>
      )}
      <ProvBadge p={std.cycle_s} />
      {est && (
        <span className="ui-chip" data-tone="warn">
          AI estimate
        </span>
      )}
    </span>
  );
}

function Operations({
  r,
  canReview,
  pending,
  set,
}: {
  r: RoutingArtifact;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const multi = r.variants.length > 1;
  return (
    <div className="table-scroll">
      <table className="table routing__table">
        <caption className="visually-hidden">Operations with time per unit and where each time comes from</caption>
        <thead>
          <tr>
            <th scope="col">Op</th>
            <th scope="col">Operation</th>
            <th scope="col">Works on</th>
            <th scope="col">Workcenter</th>
            <th scope="col">Time per unit</th>
            <th scope="col">How</th>
            {canReview && (
              <th scope="col">
                <span className="visually-hidden">Remove</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {r.operations.map((o) => {
            const dropKey = `drop:${o.op_key}`;
            const dropped = pending[dropKey] === "yes";
            const perVariant = Object.keys(o.variant_times).length > 0;
            return (
              <tr key={o.op_no} data-dropped={dropped || undefined}>
                <th scope="row">
                  <code>{o.op_no}</code>
                </th>
                <td>
                  <strong>{o.description.split(" — ")[0]}</strong>
                  {o.sample_pct < 100 && <span className="ui-chip routing__sample"> {o.sample_pct}% sampled</span>}
                  {o.batch && (
                    <div className="ui-muted small">
                      Batch of {o.batch.batch_size}, {secs(o.batch.cycle_s.value as number)} per batch
                    </div>
                  )}
                  {o.consumes.length > 0 && <div className="ui-muted small">Uses {o.consumes.join(", ")}</div>}
                </td>
                <td>
                  <code>{o.item}</code>
                </td>
                <td>
                  {o.workcenter_class === "manual" ? "Bench" : o.workcenter_class.replace(/_/g, " ")}
                  <div className="ui-muted small">{TASK_LABEL[o.task_type] ?? o.task_type}</div>
                </td>
                <td>
                  {perVariant && multi ? (
                    <ul className="routing__variants">
                      {r.variants.map((v) => (
                        <li key={v}>
                          <span className="ui-muted">{v}</span>
                          <TimeCell op={o} variant={v} std={o.variant_times[v] ?? o.time} canReview={canReview} pending={pending} set={set} />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <TimeCell op={o} variant={null} std={o.time} canReview={canReview} pending={pending} set={set} />
                  )}
                </td>
                <td>
                  <span className="small">{METHOD_LABEL[o.time.method] ?? o.time.method}</span>
                  {o.time.formula && o.time.method === "catalog_formula" && <div className="ui-mono ui-muted routing__formula">{o.time.formula}</div>}
                </td>
                {canReview && (
                  <td>
                    <button
                      type="button"
                      className="btn btn-small btn-quiet"
                      aria-pressed={dropped}
                      onClick={() => set(dropKey, dropped ? null : "yes")}
                    >
                      {dropped ? "Keep" : "Remove"}
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Precedence graph: columns by longest path from a start, edges as lines. Text list below for screen readers. */
function Flow({ r }: { r: RoutingArtifact }) {
  const layout = useMemo(() => {
    const preds = new Map<string, string[]>();
    r.operations.forEach((o) => preds.set(o.op_no, []));
    r.precedence.forEach((p) => preds.get(p.to_op)?.push(p.from_op));
    const level = new Map<string, number>();
    const visit = (k: string, seen: Set<string>): number => {
      if (level.has(k)) return level.get(k) ?? 0;
      if (seen.has(k)) return 0;
      seen.add(k);
      const l = Math.max(-1, ...(preds.get(k) ?? []).map((p) => visit(p, seen))) + 1;
      level.set(k, l);
      return l;
    };
    r.operations.forEach((o) => visit(o.op_no, new Set()));
    const cols = new Map<number, string[]>();
    r.operations.forEach((o) => {
      const l = level.get(o.op_no) ?? 0;
      cols.set(l, [...(cols.get(l) ?? []), o.op_no]);
    });
    const W = 150;
    const H = 46;
    const GX = 36;
    const GY = 14;
    const pos = new Map<string, { x: number; y: number }>();
    let maxY = 0;
    for (const [l, ops] of cols) {
      ops.forEach((id, i) => {
        pos.set(id, { x: l * (W + GX), y: i * (H + GY) });
        maxY = Math.max(maxY, i * (H + GY) + H);
      });
    }
    const width = (Math.max(...cols.keys(), 0) + 1) * (W + GX) - GX;
    return { pos, width, height: maxY, W, H };
  }, [r]);
  const byNo = new Map(r.operations.map((o) => [o.op_no, o]));
  return (
    <>
      <div className="routing__flow" role="img" aria-label={`Precedence graph of ${r.operations.length} operations; a text list follows`}>
        <svg width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`}>
          <defs>
            <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" fill="var(--c-border-strong)" />
            </marker>
          </defs>
          {r.precedence.map((p) => {
            const a = layout.pos.get(p.from_op);
            const b = layout.pos.get(p.to_op);
            if (!a || !b) return null;
            const x1 = a.x + layout.W;
            const y1 = a.y + layout.H / 2;
            const x2 = b.x;
            const y2 = b.y + layout.H / 2;
            const mx = (x1 + x2) / 2;
            return <path key={`${p.from_op}-${p.to_op}`} d={`M${x1} ${y1}C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`} fill="none" stroke="var(--c-border-strong)" strokeWidth="1.5" markerEnd="url(#flow-arrow)" />;
          })}
          {r.operations.map((o) => {
            const p = layout.pos.get(o.op_no);
            if (!p) return null;
            const est = o.time.method === "llm_estimate";
            return (
              <g key={o.op_no} transform={`translate(${p.x} ${p.y})`}>
                <rect width={layout.W} height={layout.H} rx="8" fill="var(--c-surface)" stroke={est ? "var(--c-warning)" : "var(--c-border-strong)"} strokeWidth={est ? 2 : 1} />
                <text x="10" y="19" fontSize="12" fontWeight="600" fill="var(--c-text)">
                  {o.op_no}
                </text>
                <text x="10" y="35" fontSize="11" fill="var(--c-text-muted)">
                  {truncate(o.description.split(" — ")[0] ?? "", 22)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <h3 className="ui-sub">Order as a list</h3>
      <ol className="routing__order">
        {r.operations.map((o) => {
          const before = r.precedence.filter((p) => p.to_op === o.op_no).map((p) => p.from_op);
          return (
            <li key={o.op_no}>
              <strong>{o.op_no}</strong> {byNo.get(o.op_no)?.description.split(" — ")[0]}
              <span className="ui-muted"> {before.length ? `after ${before.join(", ")}` : "can start first"}</span>
            </li>
          );
        })}
      </ol>
    </>
  );
}

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function Findings({
  findings,
  ops,
  canReview,
  pending,
  set,
}: {
  findings: RoutingFindingT[];
  ops: RoutingOp[];
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const opByKey = new Map(ops.map((o) => [o.op_key ?? "", o]));
  const open = findings.filter((f) => f.status !== "info");
  const info = findings.filter((f) => f.status === "info");
  if (!findings.length) return <p>Nothing needs your attention: every step has a time and every part is used.</p>;
  return (
    <>
      {open.length > 0 && (
        <>
          <h3 className="ui-sub">Needs a decision ({open.length})</h3>
          <ul className="ui-list">
            {open.map((f) => (
              <li key={f.id}>
                <span className="ui-chip" data-tone={f.blocking ? "bad" : "warn"}>
                  {f.blocking ? "Blocks approval" : "Check"}
                </span>{" "}
                {f.text}
                {canReview && f.kind === "time_missing" && f.op_key && (
                  <label className="inline-field routing__fix">
                    <span>Seconds per unit</span>
                    <input
                      className="input ui-input-s"
                      type="number"
                      min={0}
                      step="any"
                      value={pending[`time:${f.op_key}:*`] ?? ""}
                      onChange={(e) => set(`time:${f.op_key}:*`, e.target.value)}
                    />
                  </label>
                )}
                {canReview && f.kind === "no_process" && (
                  <button type="button" className="btn btn-small" aria-pressed={pending[`ack:${f.id}`] === "yes"} onClick={() => set(`ack:${f.id}`, pending[`ack:${f.id}`] === "yes" ? null : "yes")}>
                    {pending[`ack:${f.id}`] === "yes" ? "Acknowledged" : "Acknowledge"}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {info.length > 0 && (
        <>
          <h3 className="ui-sub">For your information ({info.length})</h3>
          <ul className="ui-list">
            {info.map((f) => (
              <li key={f.id}>
                <span className="ui-chip">{f.kind.replace(/_/g, " ")}</span> {f.text}
                {f.op_key && opByKey.get(f.op_key) && <span className="ui-muted"> ({opByKey.get(f.op_key)?.op_no})</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
