import { Fragment, useEffect, useMemo, useState } from "react";
import type { RunOut } from "../../lib/api";
import { fmtValue } from "../../lib/prov";
import type { CheckRecordT, QualityArtifact } from "../../lib/stageTypes";
import { ProvBadge } from "../ProvBadge";
import { Tabs } from "../ui/Tabs";
import "../ui/ui.css";
import "./quality.css";

type Answers = Record<string, string>;

const LAYER_LABEL: Record<string, string> = {
  iqc: "Incoming (IQC)",
  ipqc: "In process (IPQC)",
  eol: "End of line",
  oqc: "Outgoing (OQC)",
  lab: "Lab",
};
const LEVELS = ["S-1", "S-2", "S-3", "S-4", "I", "II", "III"];
const AQLS = [0.065, 0.1, 0.15, 0.25, 0.4, 0.65, 1.0, 1.5, 2.5, 4.0, 6.5, 10];
const POLICY_LABEL: Record<string, string> = {
  functional_100_capacity_sampled: "Test every unit briefly, sample the capacity test",
  capacity_100: "Run the full capacity test on every unit",
};

const num = (x: number, d = 2) => x.toLocaleString("en-IN", { maximumFractionDigits: d });

export function QualityPanel({
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
  const raw = run.artifact as unknown as Partial<QualityArtifact>;
  const q = useMemo(
    () =>
      ({
        ...raw,
        iqc: raw.iqc ?? [],
        inline: raw.inline ?? [],
        eol: raw.eol ?? [],
        patrol: raw.patrol ?? [],
        oqc: raw.oqc ?? [],
        lab: raw.lab ?? [],
        rework: raw.rework ?? [],
        compliance: raw.compliance ?? [],
        checks: raw.checks ?? [],
        policy_impact: raw.policy_impact ?? [],
        notes: raw.notes ?? [],
      }) as QualityArtifact & Required<Pick<QualityArtifact, "checks" | "policy_impact" | "notes">>,
    [raw],
  );
  const [pending, setPending] = useState<Answers>({});
  useEffect(() => setPending({}), [run.version]);
  const set = (k: string, v: string | null) =>
    setPending((p) => {
      const next = Object.fromEntries(Object.entries(p).filter(([key]) => key !== k));
      if (v !== null && v !== "") next[k] = v;
      return next;
    });
  const n = Object.keys(pending).length;
  const hours = q.inspector_h_per_day?.value as number | undefined;
  const quarantine = q.quarantine_area_m2?.value as number | undefined;

  return (
    <section className="card quality" aria-label="Quality plan">
      <ul className="ui-stats" aria-label="Quality plan at a glance">
        <li className="ui-stat">
          <span className="ui-stat__v">{q.checks.length}</span>
          <span className="ui-stat__l">checks, each with limit, method and reaction</span>
        </li>
        <li className="ui-stat">
          <span className="ui-stat__v">{q.iqc.length}</span>
          <span className="ui-stat__l">incoming items with an ISO 2859-1 plan</span>
        </li>
        {hours !== undefined && (
          <li className="ui-stat">
            <span className="ui-stat__v">{num(hours)} h</span>
            <span className="ui-stat__l">inspection work per day</span>
          </li>
        )}
        {quarantine !== undefined && (
          <li className="ui-stat">
            <span className="ui-stat__v">{num(quarantine, 0)} m²</span>
            <span className="ui-stat__l">quarantine area (estimate)</span>
          </li>
        )}
      </ul>

      {q.notes.length > 0 && (
        <div className="ui-note" role="note">
          <ul className="quality__notes">
            {q.notes.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      <Tabs
        label="Quality plan views"
        tabs={[
          { key: "checks", label: "All checks" },
          { key: "iqc", label: "Incoming sampling" },
          ...(q.policy_impact.length ? [{ key: "policy", label: "Test policy" }] : []),
          { key: "line", label: "Line, outgoing and lab" },
          { key: "compliance", label: "Compliance" },
        ]}
      >
        {(tab) =>
          tab === "checks" ? (
            <Checks checks={q.checks} />
          ) : tab === "iqc" ? (
            <Incoming q={q} canReview={canReview} pending={pending} set={set} />
          ) : tab === "policy" ? (
            <Policy q={q} canReview={canReview} pending={pending} set={set} />
          ) : tab === "line" ? (
            <LineChecks q={q} />
          ) : (
            <Compliance q={q} />
          )
        }
      </Tabs>

      {canReview && (
        <div className="ui-apply" role="region" aria-label="Apply your changes">
          <span role="status" aria-live="polite">
            {n ? `${n} change${n > 1 ? "s" : ""} ready to apply` : "Change a sampling level, an AQL or the test policy to build a new version."}
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

function Checks({ checks }: { checks: CheckRecordT[] }) {
  const [layer, setLayer] = useState<string>("all");
  const [open, setOpen] = useState<string | null>(null);
  const layers = Array.from(new Set(checks.map((c) => c.layer)));
  const shown = checks.filter((c) => layer === "all" || c.layer === layer);
  return (
    <>
      <div className="ui-filter" role="group" aria-label="Filter checks by layer">
        {["all", ...layers].map((l) => (
          <button key={l} type="button" aria-pressed={layer === l} onClick={() => setLayer(l)}>
            {l === "all" ? `All (${checks.length})` : `${LAYER_LABEL[l] ?? l} (${checks.filter((c) => c.layer === l).length})`}
          </button>
        ))}
      </div>
      <div className="table-scroll">
        <table className="table quality__table">
          <caption className="visually-hidden">Checks with sample, frequency and a details row for each</caption>
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col">Layer</th>
              <th scope="col">Applies to</th>
              <th scope="col">Sample</th>
              <th scope="col">Frequency</th>
              <th scope="col">
                <span className="visually-hidden">Details</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => {
              const isOpen = open === c.id;
              return (
                <Fragment key={c.id}>
                  <tr>
                    <th scope="row">{c.name}</th>
                    <td>{LAYER_LABEL[c.layer] ?? c.layer}</td>
                    <td>
                      <code>{c.applies_to}</code>
                    </td>
                    <td>{c.sample}</td>
                    <td>{c.frequency}</td>
                    <td>
                      <button type="button" className="btn btn-small btn-quiet" aria-expanded={isOpen} aria-controls={`ck-${c.id}`} onClick={() => setOpen(isOpen ? null : c.id)}>
                        {isOpen ? "Hide" : "Details"}
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="ui-row-detail" id={`ck-${c.id}`}>
                      <td colSpan={6}>
                        <dl className="ui-dl">
                          <dt>Characteristic</dt>
                          <dd>{c.characteristic}</dd>
                          <dt>Limit</dt>
                          <dd>{c.limit}</dd>
                          <dt>Method</dt>
                          <dd>{c.method}</dd>
                          <dt>Instrument</dt>
                          <dd>{c.instrument_class}</dd>
                          <dt>Reaction on failure</dt>
                          <dd>{c.reaction}</dd>
                          {c.certifications.length > 0 && (
                            <>
                              <dt>Supports</dt>
                              <dd>{c.certifications.map((k) => k.split(".").slice(1).join(" · ").replace(/_/g, " ")).join("; ")}</dd>
                            </>
                          )}
                        </dl>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="ui-muted small">Limits are written against your product specification: numeric limits belong to the product, so set them in your inspection instructions.</p>
    </>
  );
}

function Incoming({
  q,
  canReview,
  pending,
  set,
}: {
  q: QualityArtifact;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const b = q.basis;
  return (
    <>
      {b && (
        <div className="quality__basis">
          <p className="ui-muted small">
            Sized for {b.annual_volume ? fmtValue(b.annual_volume.value as number) : "?"} units a year over{" "}
            {b.working_days_per_year ? fmtValue(b.working_days_per_year.value as number) : "?"} working days. A lot is {b.iqc_lot_days ? fmtValue(b.iqc_lot_days.value as number) : "?"} days of use
            (catalog default until the order policy exists).
          </p>
          {canReview && (
            <div className="quality__controls">
              <label className="inline-field">
                <span>Inspection level</span>
                <select className="input" value={pending["level:iqc"] ?? b.inspection_level} onChange={(e) => set("level:iqc", e.target.value === b.inspection_level ? null : e.target.value)}>
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline-field">
                <span>Days of use per lot</span>
                <input
                  className="input ui-input-s"
                  type="number"
                  min={1}
                  step="any"
                  value={pending["lotdays:iqc"] ?? String(b.iqc_lot_days?.value ?? 7)}
                  onChange={(e) => set("lotdays:iqc", Number(e.target.value) === Number(b.iqc_lot_days?.value) ? null : e.target.value)}
                />
              </label>
            </div>
          )}
        </div>
      )}
      <div className="table-scroll">
        <table className="table quality__table">
          <caption className="visually-hidden">Incoming inspection sampling plans from ISO 2859-1</caption>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col" className="num">
                Lot
              </th>
              <th scope="col">Code</th>
              <th scope="col" className="num">
                Sample
              </th>
              <th scope="col" className="num">
                Accept / reject
              </th>
              <th scope="col">AQL %</th>
              <th scope="col">Checks</th>
              <th scope="col" className="num">
                Hours a day
              </th>
            </tr>
          </thead>
          <tbody>
            {q.iqc.map((x) => {
              const key = `aql:${x.part_no}`;
              return (
                <tr key={x.part_no}>
                  <th scope="row">
                    {x.name ?? x.part_no}
                    <div className="ui-muted small">
                      <code>{x.part_no}</code>
                    </div>
                  </th>
                  <td className="num">{x.plan.lot_size.toLocaleString("en-IN")}</td>
                  <td>{x.plan.code_letter}</td>
                  <td className="num">{x.plan.full_inspection ? "all" : x.plan.sample_size}</td>
                  <td className="num">
                    {x.plan.accept} / {x.plan.reject}
                  </td>
                  <td>
                    {canReview ? (
                      <select className="input" aria-label={`AQL for ${x.part_no}`} value={pending[key] ?? String(x.plan.aql)} onChange={(e) => set(key, Number(e.target.value) === x.plan.aql ? null : e.target.value)}>
                        {AQLS.map((a) => (
                          <option key={a} value={String(a)}>
                            {a}
                          </option>
                        ))}
                      </select>
                    ) : (
                      x.plan.aql
                    )}
                  </td>
                  <td>
                    {x.check_types.map((c) => c.replace(/_/g, " ")).join(", ")}
                    {(x.inline_checks ?? []).length > 0 && <div className="ui-muted small">On the line: {(x.inline_checks ?? []).join(", ").replace(/_/g, " ")}</div>}
                  </td>
                  <td className="num">
                    {num(x.inspector_h_per_day.value as number, 3)} <ProvBadge p={x.inspector_h_per_day} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {q.iqc[0]?.plan.table_row && <p className="ui-muted small">Example of the table lookup: {q.iqc[0].plan.table_row}.</p>}
    </>
  );
}

function Policy({
  q,
  canReview,
  pending,
  set,
}: {
  q: QualityArtifact;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const current = pending["policy:test"] ?? q.test_policy ?? "";
  return (
    <>
      <p>
        The power bank capacity test takes hours, so how many units you test changes the number of test channels a lot. Both options are calculated from the catalog; pick one.
      </p>
      <fieldset className="quality__policy" disabled={!canReview}>
        <legend className="visually-hidden">Test policy</legend>
        {q.policy_impact?.map((p) => (
          <div key={p.policy} className="quality__opt" data-on={current === p.policy}>
            <input
              id={`policy-${p.policy}`}
              type="radio"
              name="policy"
              checked={current === p.policy}
              onChange={() => set("policy:test", p.policy === q.test_policy ? null : p.policy)}
            />
            <span>
              <label htmlFor={`policy-${p.policy}`}>
                <strong>{POLICY_LABEL[p.policy] ?? p.label}</strong>
              </label>
              <span className="ui-muted small"> {p.label}</span>
              <span className="quality__opt-nums">
                <span>
                  <b>{p.channels}</b> test channels
                </span>
                <span>
                  <b>{num(p.rack_hours_per_day, 1)}</b> rack-hours a day
                </span>
              </span>
            </span>
          </div>
        ))}
      </fieldset>
    </>
  );
}

function LineChecks({ q }: { q: QualityArtifact }) {
  return (
    <>
      <h3 className="ui-sub">Checks that run as routing operations</h3>
      <div className="table-scroll">
        <table className="table">
          <caption className="visually-hidden">Inline and end-of-line checks bound to operations</caption>
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col">Operation</th>
              <th scope="col">Where</th>
              <th scope="col" className="num">
                Share of units
              </th>
            </tr>
          </thead>
          <tbody>
            {[...q.inline.map((x) => ({ ...x, where: "In process" })), ...q.eol.map((x) => ({ ...x, where: "End of line" }))].map((x) => (
              <tr key={`${x.where}-${x.check_type}-${x.op_no}`}>
                <th scope="row">{x.check_type.replace(/_/g, " ")}</th>
                <td>
                  <code>{x.op_no}</code>
                </td>
                <td>{x.where}</td>
                <td className="num">{x.coverage_pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {q.patrol.length > 0 && (
        <>
          <h3 className="ui-sub">Patrol</h3>
          <ul className="ui-list">
            {q.patrol.map((p) => (
              <li key={p.check_type}>
                {p.check_type.replace(/_/g, " ")}: {p.per_shift} per shift
              </li>
            ))}
          </ul>
        </>
      )}
      {(q.oqc.length > 0 || q.lab.length > 0) && (
        <>
          <h3 className="ui-sub">Outgoing and lab</h3>
          <ul className="ui-list">
            {q.oqc.map((o) => (
              <li key={`oqc-${o.check_type}`}>
                Outgoing: {o.check_type.replace(/_/g, " ")}
                {o.plan ? ` — sample ${o.plan.sample_size}, accept ${o.plan.accept} / reject ${o.plan.reject} (AQL ${o.plan.aql})` : ` — ${o.frequency ?? ""}`}
              </li>
            ))}
            {q.lab.map((l) => (
              <li key={`lab-${l.name}`}>
                Lab: {l.name} — {l.frequency}
              </li>
            ))}
          </ul>
        </>
      )}
      {q.rework.length > 0 && (
        <>
          <h3 className="ui-sub">Rework from first-pass yield</h3>
          <div className="table-scroll">
            <table className="table">
              <caption className="visually-hidden">Rework load per operation</caption>
              <thead>
                <tr>
                  <th scope="col">Operation</th>
                  <th scope="col" className="num">
                    First-pass yield
                  </th>
                  <th scope="col" className="num">
                    Units a day
                  </th>
                  <th scope="col" className="num">
                    Rework minutes a day
                  </th>
                </tr>
              </thead>
              <tbody>
                {q.rework.map((r) => (
                  <tr key={r.op_no}>
                    <th scope="row">
                      <code>{r.op_no}</code>
                    </th>
                    <td className="num">{(r.fpy * 100).toFixed(1)}%</td>
                    <td className="num">{num(r.units_per_day, 0)}</td>
                    <td className="num">{num((r.load_s_per_day.value as number) / 60, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function Compliance({ q }: { q: QualityArtifact }) {
  if (!q.compliance.length) return <p>No compliance items are linked for this product type.</p>;
  return (
    <>
      <p className="ui-muted">These come from the dated data pack. They are informational: verify with a certification body.</p>
      <ul className="ui-list">
        {q.compliance.map((c) => (
          <li key={c.pack_key}>
            <strong>{c.title}</strong>
            <div className="ui-muted small">
              <code>{c.pack_key}</code>
            </div>
            <div>{c.supported_by_checks.length ? `Supported by: ${c.supported_by_checks.map((x) => x.replace(/_/g, " ")).join(", ")}` : "No check in the plan supports this yet."}</div>
          </li>
        ))}
      </ul>
    </>
  );
}
