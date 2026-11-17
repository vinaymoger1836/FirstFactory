import type { Bom, BomFinding, BomLine, MakeBuyDecision } from "@fl/ts-types";
import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router";
import { api, API_BASE, type AttachmentOut, type RunOut } from "../../lib/api";
import { rowsOf, type BomRow } from "../../lib/bom";
import { fmtValue, type ProvLike } from "../../lib/prov";
import { STEP_LABEL } from "../../lib/stages";
import { ProvBadge } from "../ProvBadge";
import "./bom.css";

const GlbViewer = lazy(() => import("../GlbViewer"));

type Answers = Record<string, string>;
type TabKey = "agent" | "review" | "mbom" | "export";

const ORIGIN_LABEL: Record<string, string> = {
  user_bom: "Your BoM",
  cad: "CAD",
  skeleton: "Skeleton",
  reasoner: "AI",
  engine: "Engine",
};
const REVIEW_LABEL: Record<string, string> = {
  given: "Given",
  suggested: "Suggested",
  accepted: "Accepted",
  rejected: "Rejected",
};
const CASE_LABEL: Record<string, string> = {
  A: "Case A: no BoM file. Drafted from CAD and the product type's skeleton.",
  B: "Case B: your complete BoM file. Your lines are never rewritten.",
  C: "Case C: your BoM file with probable gaps. Missing parts are suggestions.",
};
const FLOW = ["load_inputs", "plan", "tool_loop", "draft", "validate", "compute", "summarise", "approval"];

const inr = (x: number | null | undefined) =>
  x === null || x === undefined ? "—" : `₹${x.toLocaleString("en-IN", { maximumFractionDigits: x < 10 ? 2 : 0 })}`;
const grams = (g: number | null | undefined) =>
  g === null || g === undefined ? "—" : g >= 1000 ? `${(g / 1000).toLocaleString("en-IN", { maximumFractionDigits: 2 })} kg` : `${Math.round(g).toLocaleString("en-IN")} g`;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

export function BomPanel({
  run,
  token,
  files,
  canReview,
  busy,
  step,
  onAnswer,
}: {
  run: RunOut;
  token: string;
  files: AttachmentOut[];
  canReview: boolean;
  busy: boolean;
  step: string | null;
  onAnswer: (answers: Answers) => void;
}) {
  // Older versions may lack fields added later (coverage, totals …): fill safe defaults.
  const raw = run.artifact as unknown as Partial<Bom>;
  const bom = useMemo(
    () =>
      ({
    ...raw,
    coverage: raw.coverage ?? [],
    findings: raw.findings ?? [],
    totals: raw.totals ?? [],
    make_buy_decisions: raw.make_buy_decisions ?? [],
    ebom: raw.ebom ?? [],
    mbom: raw.mbom ?? [],
    items: raw.items ?? [],
    variants: raw.variants ?? [],
    proposer: raw.proposer ?? "",
  }) as Bom,
    [raw],
  );
  const [tab, setTab] = useState<TabKey>("review");
  const [pending, setPending] = useState<Answers>({});
  const [focus, setFocus] = useState<string | null>(null);
  const rows = useMemo(() => rowsOf(bom), [bom]);
  const cad = files.find((f) => f.has_glb);
  // GLB node names carry the CAD solid name; map each to the BoM line that lists it.
  const solidLine = useMemo(() => {
    const m = new Map<string, BomRow>();
    rows.forEach((r) => (r.item?.cad_refs ?? []).forEach((sd) => m.set(sd, r)));
    return m;
  }, [rows]);
  const colorFor = useCallback(
    (solid: string) => {
      const r = solidLine.get(solid);
      if (focus && r && r.id === focus) return cssVar("--c-accent");
      if (!r) return cssVar("--c-danger");
      const st = pending[`line:${r.id}`] ?? r.line.review;
      return st === "suggested" ? cssVar("--c-warning") : st === "rejected" ? cssVar("--c-text-muted") : cssVar("--c-success");
    },
    [solidLine, focus, pending],
  );
  function pick(solid: string | null) {
    const r = solid ? solidLine.get(solid) : undefined;
    setFocus(r ? r.id : null);
    if (!r) return;
    // The line lives on the Review tab; show it there.
    setTab("review");
    requestAnimationFrame(() =>
      requestAnimationFrame(() =>
        document.querySelector(`[data-row="${CSS.escape(r.id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }),
      ),
    );
  }
  const tabs: { key: TabKey; label: string }[] = [
    { key: "agent", label: "Agent run" },
    { key: "review", label: "Review" },
    { key: "mbom", label: "MBOM & make/buy" },
    { key: "export", label: "Export" },
  ];
  const uid = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  useEffect(() => setPending({}), [run.version]);

  function onTabKey(e: KeyboardEvent, i: number) {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = tabs[(i + d + tabs.length) % tabs.length];
    if (next) {
      setTab(next.key);
      refs.current[next.key]?.focus();
    }
  }

  const set = useCallback((k: string, v: string | null) => {
    setPending((p) => {
      const n = Object.fromEntries(Object.entries(p).filter(([key]) => key !== k));
      if (v !== null) n[k] = v;
      return n;
    });
  }, []);
  const nPending = Object.keys(pending).length;

  return (
    <section className="card bom" aria-label="Bill of materials">
      <div className="bom__head">
        <p className="bom__case">{CASE_LABEL[bom.case ?? ""] ?? ""}</p>
        <span className="badge" data-tone={bom.proposer.startsWith("AI") ? "run" : undefined}>
          Proposer: {bom.proposer || "rules engine"}
        </span>
      </div>
      <div role="tablist" aria-label="BoM views" className="tabs">
        {tabs.map((t, i) => (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[t.key] = el;
            }}
            role="tab"
            type="button"
            id={`${uid}-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`${uid}-panel-${t.key}`}
            tabIndex={tab === t.key ? 0 : -1}
            className="tabs__tab"
            onClick={() => setTab(t.key)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="bom__body" data-split={cad && tab !== "agent" ? "true" : undefined}>
        {cad && tab !== "agent" && (
          <div className="bom__viewer">
            <Suspense fallback={<p role="status">Loading 3D viewer…</p>}>
              <GlbViewer
                token={token}
                attachmentId={cad.id}
                label={cad.filename}
                colorFor={colorFor}
                onPick={pick}
                caption={
                  <span className="legend">
                    <i data-k="ok" /> given or accepted <i data-k="warn" /> suggested <i data-k="bad" /> not in the BoM{" "}
                    <i data-k="sel" /> selected
                  </span>
                }
              />
            </Suspense>
          </div>
        )}
        <div role="tabpanel" id={`${uid}-panel-${tab}`} aria-labelledby={`${uid}-tab-${tab}`} className="tabs__panel" key={tab}>
          {tab === "agent" && <AgentRun run={run} token={token} step={step} />}
          {tab === "review" && (
            <Review bom={bom} token={token} canReview={canReview} pending={pending} set={set} focus={focus} setFocus={setFocus} />
          )}
          {tab === "mbom" && <MbomMakeBuy bom={bom} canReview={canReview} pending={pending} set={set} />}
          {tab === "export" && <Export bom={bom} run={run} token={token} />}
        </div>
      </div>
      {canReview && (
        <div className="bom__apply" role="region" aria-label="Pending review decisions">
          <span role="status" aria-live="polite">
            {nPending ? `${nPending} decision${nPending > 1 ? "s" : ""} not applied yet.` : "No pending decisions."}
          </span>
          <button type="button" className="btn btn-small btn-quiet" disabled={!nPending || busy} onClick={() => setPending({})}>
            Discard
          </button>
          <button
            type="button"
            className="btn btn-primary btn-small"
            disabled={!nPending || busy}
            aria-busy={busy}
            onClick={() => onAnswer(pending)}
          >
            Apply review ({nPending})
          </button>
        </div>
      )}
    </section>
  );
}

// ── Agent run ────────────────────────────────────────────────────────────

interface AuditRow {
  id: number;
  kind: string;
  at: string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: string;
  status?: string;
  version?: number;
  message?: string;
  action?: string;
}

function AgentRun({ run, token, step }: { run: RunOut; token: string; step: string | null }) {
  const [log, setLog] = useState<AuditRow[]>([]);
  useEffect(() => {
    api<AuditRow[]>("/projects/current/stages/bom/audit", {}, token)
      .then(setLog)
      .catch(() => setLog([]));
  }, [token, run.version, run.status]);
  const tools = log.filter((e) => e.kind === "tool_call");
  const at = step ? FLOW.indexOf(step) : run.status === "running" ? 1 : FLOW.length;
  return (
    <div className="agent">
      <p className="muted small">
        The proposer chooses lines from the catalog and your files; engines compute every quantity, mass and cost;
        nothing reaches the BoM until you approve it.
      </p>
      <ol className="flow" aria-label="Agent steps">
        {FLOW.map((s, i) => (
          <li key={s} className="flow__step" data-state={i < at ? "done" : i === at ? "current" : "todo"} aria-current={i === at ? "step" : undefined}>
            {STEP_LABEL[s] ?? s}
          </li>
        ))}
      </ol>
      <dl className="kv agent__stats">
        <div>
          <dt>Tool calls</dt>
          <dd>{tools.length}</dd>
        </div>
        <div>
          <dt>Model tokens</dt>
          <dd>
            {run.tokens_used.toLocaleString()} of {run.token_budget.toLocaleString()}
          </dd>
        </div>
      </dl>
      <h3 className="small-head">Tool calls</h3>
      {tools.length === 0 ? (
        <p className="muted small">No tool calls in this run (the rules engine, or the model answered directly).</p>
      ) : (
        <ol className="toollog">
          {tools.map((t) => (
            <li key={t.id}>
              <code>{t.tool}</code> <span className="muted small">{JSON.stringify(t.args ?? {}).slice(0, 120)}</span>
              <div className="small toollog__res">→ {t.result}</div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// ── Review ───────────────────────────────────────────────────────────────

function Review({
  bom,
  token,
  canReview,
  pending,
  set,
  focus,
  setFocus,
}: {
  bom: Bom;
  token: string;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
  focus: string | null;
  setFocus: (id: string | null) => void;
}) {
  const rows = useMemo(() => rowsOf(bom), [bom]);
  const [origin, setOrigin] = useState("all");
  const [review, setReview] = useState("all");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [materials, setMaterials] = useState<{ code: string; name: string }[]>([]);
  const uid = useId();

  useEffect(() => {
    api<{ code: string; name: string }[]>("/projects/current/catalog/materials", {}, token)
      .then(setMaterials)
      .catch(() => setMaterials([]));
  }, [token]);

  const decided = (r: BomRow) => pending[`line:${r.id}`] ?? (r.line.review === "suggested" ? null : r.line.review);
  const shown = rows
    .filter((r) => origin === "all" || r.line.origin === origin)
    .filter((r) => review === "all" || r.line.review === review)
    .sort((a, b) => a.line.qty.confidence - b.line.qty.confidence || a.id.localeCompare(b.id));
  const suggested = rows.filter((r) => r.line.review === "suggested");

  function bulk(v: "accepted" | "rejected", ids: string[]) {
    ids.forEach((id) => set(`line:${id}`, v));
    setSel(new Set());
  }

  return (
    <div className="review-bom">
      <Coverage bom={bom} canReview={canReview} pending={pending} set={set} />
      <Findings findings={bom.findings} canReview={canReview} pending={pending} set={set} />

      <div>
        <div>
          <div className="bom-tools" role="group" aria-label="Filter and bulk actions">
            <label>
              <span className="small">Origin</span>
              <select className="input" value={origin} onChange={(e) => setOrigin(e.target.value)}>
                <option value="all">All</option>
                {Object.entries(ORIGIN_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className="small">Status</span>
              <select className="input" value={review} onChange={(e) => setReview(e.target.value)}>
                <option value="all">All</option>
                {Object.entries(REVIEW_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            {canReview && (
              <>
                <button type="button" className="btn btn-small" disabled={!sel.size} onClick={() => bulk("accepted", [...sel])}>
                  Accept selected ({sel.size})
                </button>
                <button type="button" className="btn btn-small" disabled={!sel.size} onClick={() => bulk("rejected", [...sel])}>
                  Reject selected
                </button>
                <button
                  type="button"
                  className="btn btn-small"
                  disabled={!suggested.length}
                  onClick={() => bulk("accepted", suggested.map((r) => r.id))}
                >
                  Accept all suggested ({suggested.length})
                </button>
              </>
            )}
          </div>
          <div className="table-scroll">
            <table className="table bom-table">
              <caption className="visually-hidden">EBOM lines. Low confidence first.</caption>
              <thead>
                <tr>
                  {canReview && (
                    <th scope="col">
                      <span className="visually-hidden">Select</span>
                    </th>
                  )}
                  <th scope="col">Part</th>
                  <th scope="col">Class</th>
                  {bom.variants.map((v) => (
                    <th key={v} scope="col" className="num">
                      Qty {v}
                    </th>
                  ))}
                  <th scope="col">Material</th>
                  <th scope="col" className="num">
                    Mass
                  </th>
                  <th scope="col" className="num">
                    Unit cost
                  </th>
                  <th scope="col">Origin</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const st = decided(r) ?? "suggested";
                  const item = r.item;
                  const calcOpen = open.has(r.id);
                  return (
                    <RowFragment key={r.id}>
                      <tr data-row={r.id} data-review={st} data-focus={focus === r.id || undefined}>
                        {canReview && (
                          <td>
                            {r.line.review === "suggested" && (
                              <input
                                type="checkbox"
                                aria-label={`Select ${r.line.child}`}
                                checked={sel.has(r.id)}
                                onChange={(e) =>
                                  setSel((s) => {
                                    const n = new Set(s);
                                    if (e.target.checked) n.add(r.id);
                                    else n.delete(r.id);
                                    return n;
                                  })
                                }
                              />
                            )}
                          </td>
                        )}
                        <td>
                          <button type="button" className="linklike" onClick={() => setFocus(focus === r.id ? null : r.id)} aria-pressed={focus === r.id}>
                            <code>{r.line.child}</code>
                          </button>
                          <div>{item?.name}</div>
                          {r.line.reason && (
                            <div className="small muted bom-reason" title={r.line.reason}>
                              {r.line.reason}
                            </div>
                          )}
                        </td>
                        <td className="small">{r.line.commodity_class ?? "—"}</td>
                        {bom.variants.map((v) => (
                          <td key={v} className="num">
                            {canReview && r.line.review !== "rejected" && Object.keys(r.qty).length > 0 && new Set(Object.values(r.qty)).size === 1 && v === bom.variants[0] ? (
                              <input
                                className="input input--qty"
                                type="number"
                                min={0}
                                step="any"
                                aria-label={`Quantity of ${r.line.child} (all variants)`}
                                value={pending[`qty:${r.id}`] ?? String(r.qty[v] ?? "")}
                                onChange={(e) => set(`qty:${r.id}`, e.target.value === "" || e.target.value === String(r.qty[v]) ? null : e.target.value)}
                              />
                            ) : r.qty[v] !== undefined ? (
                              <>
                                {fmtValue(r.qty[v])} <span className="muted small">{r.line.uom !== "unit" ? r.line.uom : ""}</span>
                              </>
                            ) : (
                              "—"
                            )}
                            {v === bom.variants[0] && <ProvBadge p={r.line.qty as unknown as ProvLike} />}
                          </td>
                        ))}
                        <td>
                          {canReview && item && materials.length > 0 ? (
                            <select
                              className="input input--mat"
                              aria-label={`Material of ${r.line.child}`}
                              value={pending[`mat:${item.part_no}`] ?? item.material?.value ?? ""}
                              onChange={(e) => set(`mat:${item.part_no}`, e.target.value === (item.material?.value ?? "") ? null : e.target.value)}
                            >
                              <option value="">—</option>
                              {materials.map((m) => (
                                <option key={m.code} value={m.code}>
                                  {m.name}
                                </option>
                              ))}
                            </select>
                          ) : (
                            item?.material?.value ?? "—"
                          )}
                        </td>
                        <td className="num">{grams(item?.mass_g?.value)}</td>
                        <td className="num">
                          {inr(item?.unit_cost_inr?.base)}
                          {item && item.calc.length > 0 && (
                            <button
                              type="button"
                              className="calc-toggle"
                              aria-expanded={calcOpen}
                              aria-label={`${calcOpen ? "Hide" : "Show"} calculation for ${r.line.child}`}
                              onClick={() =>
                                setOpen((s) => {
                                  const n = new Set(s);
                                  if (n.has(r.id)) n.delete(r.id);
                                  else n.add(r.id);
                                  return n;
                                })
                              }
                            >
                              ▾
                            </button>
                          )}
                        </td>
                        <td>
                          <span className="origin" data-origin={r.line.origin}>
                            {ORIGIN_LABEL[r.line.origin] ?? r.line.origin}
                          </span>
                        </td>
                        <td>
                          {r.line.review === "suggested" && canReview ? (
                            <div className="segmented segmented--small" role="radiogroup" aria-label={`Decision for ${r.line.child}`}>
                              {(["accepted", "rejected"] as const).map((d) => (
                                <label key={d} className="segmented__opt">
                                  <input
                                    type="radio"
                                    name={`${uid}-${r.id}`}
                                    checked={pending[`line:${r.id}`] === d}
                                    onChange={() => set(`line:${r.id}`, d)}
                                  />
                                  <span>{d === "accepted" ? "Accept" : "Reject"}</span>
                                </label>
                              ))}
                            </div>
                          ) : (
                            <span className="tag" data-review={r.line.review}>
                              {REVIEW_LABEL[r.line.review]}
                            </span>
                          )}
                        </td>
                      </tr>
                      {calcOpen && item && (
                        <tr className="calc-row">
                          <td colSpan={7 + bom.variants.length}>
                            <ol className="calc">
                              {item.calc.map((c, i) => (
                                <li key={i}>
                                  <b>{c.label}</b>
                                  {c.formula && <> · <code>{c.formula}</code></>}
                                  {c.value !== null && (
                                    <>
                                      {" "}
                                      = {fmtValue(c.value)} {c.unit ?? ""}
                                    </>
                                  )}{" "}
                                  <span className="muted small">({c.source})</span>
                                </li>
                              ))}
                            </ol>
                          </td>
                        </tr>
                      )}
                    </RowFragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="small muted">
            Mass and cost are never typed by the model: CAD volume × catalog density, catalog prices. All catalog numbers are
            estimates.
          </p>
        </div>
      </div>
    </div>
  );
}

function RowFragment({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

function Coverage({
  bom,
  canReview,
  pending,
  set,
}: {
  bom: Bom;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  if (!bom.coverage.length) return null;
  return (
    <div className="coverage">
      <h3 className="small-head">What the product type expects</h3>
      <ul className="chips">
        {bom.coverage.map((c) => {
          const excluded = (pending[`exclude:${c.commodity_class}`] ?? (c.state === "excluded" ? "yes" : "no")) === "yes";
          return (
            <li key={c.commodity_class} className="chip" data-state={excluded ? "excluded" : c.state}>
              {c.mbom_only && canReview ? (
                <label>
                  <input
                    type="checkbox"
                    checked={!excluded}
                    onChange={(e) => set(`exclude:${c.commodity_class}`, e.target.checked ? (c.state === "excluded" ? "no" : null) : "yes")}
                  />{" "}
                  {c.label}
                </label>
              ) : (
                c.label
              )}
              <span className="visually-hidden">: {excluded ? "excluded" : c.state}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Findings({
  findings,
  canReview,
  pending,
  set,
}: {
  findings: BomFinding[];
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const uid = useId();
  const list = findings.filter((f) => f.status !== "info" || f.options.length);
  if (!list.length && !findings.length) return null;
  return (
    <div className="findings">
      <h3 className="small-head">Findings ({findings.filter((f) => f.status === "open").length} open)</h3>
      <ul>
        {findings.map((f) => (
          <li key={f.id} className="finding" data-kind={f.kind} data-status={f.status}>
            <span>{f.text}</span>
            {f.kind === "missing_class" ? (
              <span className="muted small"> Decide on its line in the table.</span>
            ) : canReview && f.options.length > 0 && f.status !== "answered" ? (
              <span className="segmented segmented--small" role="radiogroup" aria-label={f.text}>
                {f.options.map((o) => (
                  <label key={o} className="segmented__opt">
                    <input type="radio" name={`${uid}-${f.id}`} checked={pending[`finding:${f.id}`] === o} onChange={() => set(`finding:${f.id}`, o)} />
                    <span>{o}</span>
                  </label>
                ))}
              </span>
            ) : canReview && f.kind === "line_quality" && f.status === "open" ? (
              <input
                className="input input--qty"
                type="number"
                min={0}
                step="any"
                aria-label={`Quantity for: ${f.text}`}
                value={pending[`finding:${f.id}`] ?? ""}
                onChange={(e) => set(`finding:${f.id}`, e.target.value || null)}
              />
            ) : f.answer ? (
              <span className="tag">{f.answer}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── MBOM & make/buy ──────────────────────────────────────────────────────

function MbomMakeBuy({
  bom,
  canReview,
  pending,
  set,
}: {
  bom: Bom;
  canReview: boolean;
  pending: Answers;
  set: (k: string, v: string | null) => void;
}) {
  const [variant, setVariant] = useState(bom.variants[0] ?? "");
  const items = new Map(bom.items.map((i) => [i.part_no, i]));
  const lines = bom.mbom.filter((l) => l.variant === null || l.variant === variant);
  const groups = new Map<string, BomLine[]>();
  lines.forEach((l) => {
    if (items.get(l.child)?.kind === "assembly") return;
    const k = l.subassembly ?? "final assembly";
    groups.set(k, [...(groups.get(k) ?? []), l]);
  });
  const total = bom.totals.find((t) => t.variant === variant);
  const uid = useId();

  function confirmAll() {
    bom.make_buy_decisions.forEach((d) => {
      if (!d.confirmed) set(`mbconfirm:${d.part_no}`, "yes");
    });
  }

  return (
    <div className="mbom">
      <div className="bom-tools">
        <label>
          <span className="small">Variant</span>
          <select className="input" value={variant} onChange={(e) => setVariant(e.target.value)}>
            {bom.variants.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        {total && (
          <p className="mbom__total">
            Total {variant}: <b>{inr(total.cost_inr?.base)}</b>{" "}
            <span className="muted small">
              (range {inr(total.cost_inr?.low)}–{inr(total.cost_inr?.high)}; {total.lines_without_cost} line(s) without a
              price) · mass {grams(total.mass_g)}
            </span>
          </p>
        )}
      </div>
      <div className="table-scroll">
        <table className="table">
          <caption className="visually-hidden">MBOM for {variant}, grouped by sub-assembly</caption>
          <thead>
            <tr>
              <th scope="col">Part</th>
              <th scope="col" className="num">
                Qty
              </th>
              <th scope="col" className="num">
                Cost
              </th>
              <th scope="col" className="num">
                Mass
              </th>
            </tr>
          </thead>
          {[...groups.entries()].map(([g, ls]) => {
            const sub = ls.reduce((a, l) => a + (l.ext_cost_inr?.base ?? 0), 0);
            const mass = ls.reduce((a, l) => a + (l.ext_mass_g ?? 0), 0);
            return (
              <tbody key={g}>
                <tr className="group-row">
                  <th scope="rowgroup" colSpan={2}>
                    {g}
                  </th>
                  <td className="num">{inr(sub)}</td>
                  <td className="num">{grams(mass || null)}</td>
                </tr>
                {ls.map((l) => (
                  <tr key={l.id ?? l.child}>
                    <td>
                      <code>{l.child}</code> {items.get(l.child)?.name}
                    </td>
                    <td className="num">
                      {fmtValue(l.qty.value)} {l.uom !== "unit" ? l.uom : ""}
                    </td>
                    <td className="num">{inr(l.ext_cost_inr?.base)}</td>
                    <td className="num">{grams(l.ext_mass_g)}</td>
                  </tr>
                ))}
              </tbody>
            );
          })}
        </table>
      </div>

      <div className="makebuy">
        <div className="makebuy__head">
          <h3 className="small-head">Make or buy</h3>
          {canReview && (
            <button type="button" className="btn btn-small" onClick={confirmAll} disabled={bom.make_buy_decisions.every((d) => d.confirmed)}>
              Confirm all recommendations
            </button>
          )}
        </div>
        <p className="small muted">The first decision with a large cost effect (docs/03 §4.3). Previews are catalog estimates for one station per process.</p>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Decision</th>
                <th scope="col">If made in-house</th>
                <th scope="col">Confirmed</th>
              </tr>
            </thead>
            <tbody>
              {bom.make_buy_decisions.map((d: MakeBuyDecision) => {
                const current = pending[`mb:${d.part_no}`] ?? d.decision.value;
                const confirmed = d.confirmed || pending[`mbconfirm:${d.part_no}`] === "yes";
                const p = d.preview;
                return (
                  <tr key={d.part_no}>
                    <td>
                      <code>{d.part_no}</code> {items.get(d.part_no)?.name}
                      <div className="small muted">
                        Recommended: {d.recommendation} · {d.rationale}
                      </div>
                    </td>
                    <td>
                      {canReview ? (
                        <span className="segmented segmented--small" role="radiogroup" aria-label={`Make or buy ${d.part_no}`}>
                          {(["make", "buy"] as const).map((o) => (
                            <label key={o} className="segmented__opt">
                              <input
                                type="radio"
                                name={`${uid}-${d.part_no}`}
                                checked={current === o}
                                onChange={() => set(`mb:${d.part_no}`, o === d.decision.value ? null : o)}
                              />
                              <span>{o === "make" ? "Make" : "Buy"}</span>
                            </label>
                          ))}
                        </span>
                      ) : (
                        current
                      )}
                    </td>
                    <td className="small">
                      {p?.capex_inr ? (
                        <>
                          Capex {inr(p.capex_inr.low)}–{inr(p.capex_inr.high)} · {p.area_m2?.base ?? "—"} m² ·{" "}
                          {p.operators_per_shift?.base ?? "—"} operator(s)/shift
                          <div className="muted">{p.processes.join(", ")}</div>
                        </>
                      ) : (
                        <span className="muted">{p?.note ?? "—"}</span>
                      )}
                    </td>
                    <td>
                      {canReview ? (
                        <input
                          type="checkbox"
                          aria-label={`Confirm ${current} for ${d.part_no}`}
                          checked={confirmed}
                          disabled={d.confirmed}
                          onChange={(e) => set(`mbconfirm:${d.part_no}`, e.target.checked ? "yes" : null)}
                        />
                      ) : confirmed ? (
                        "Yes"
                      ) : (
                        "No"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Export ───────────────────────────────────────────────────────────────

function Export({ bom, run, token }: { bom: Bom; run: RunOut; token: string }) {
  const [msg, setMsg] = useState("");
  const [log, setLog] = useState<AuditRow[]>([]);
  useEffect(() => {
    api<AuditRow[]>("/projects/current/stages/bom/audit", {}, token)
      .then((l) => setLog(l.filter((e) => e.kind !== "tool_call")))
      .catch(() => setLog([]));
  }, [token, run.version]);

  function save(blob: Blob, name: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function workbook() {
    const r = await fetch(`${API_BASE}/projects/current/stages/bom/runs/${run.version}/workbook`, { headers: { "X-Project-Token": token } });
    if (!r.ok) {
      setMsg("The workbook could not be built.");
      return;
    }
    save(await r.blob(), `bom-v${run.version}.xlsx`);
    setMsg("Workbook downloaded.");
  }

  function csv() {
    const head = ["part_no", "name", "class", ...bom.variants.map((v) => `qty_${v}`), "uom", "origin", "review"];
    const items = new Map(bom.items.map((i) => [i.part_no, i]));
    const esc = (s: unknown) => `"${String(s ?? "").replace(/"/g, '""')}"`;
    const lines = rowsOf(bom).map((r) =>
      [r.line.child, items.get(r.line.child)?.name, r.line.commodity_class, ...bom.variants.map((v) => r.qty[v] ?? ""), r.line.uom, r.line.origin, r.line.review]
        .map(esc)
        .join(","),
    );
    void navigator.clipboard.writeText([head.join(","), ...lines].join("\n")).then(() => setMsg("EBOM copied as CSV."));
  }

  return (
    <div className="export">
      <div className="grid">
        <div className="card">
          <h3 className="small-head">Download</h3>
          <div className="row-gap">
            <button type="button" className="btn btn-primary btn-small" onClick={() => void workbook()}>
              Excel workbook (.xlsx)
            </button>
            <button type="button" className="btn btn-small" onClick={() => save(new Blob([JSON.stringify(bom, null, 2)], { type: "application/json" }), `bom-v${run.version}.json`)}>
              BoM JSON
            </button>
            <button type="button" className="btn btn-small" onClick={csv}>
              Copy EBOM as CSV
            </button>
          </div>
          <p role="status" className="small muted">
            {msg}
          </p>
          <p className="small muted">Workbook sheets: EBOM, MBOM, Make-Buy, Variants, Open items.</p>
        </div>
        <div className="card">
          <h3 className="small-head">Next: S3 routing</h3>
          <p className="small">
            Routing reads the approved MBOM: every make line and assembly becomes operations from the catalog. Routing is
            built in the next part of this phase; until then it is a placeholder that approves itself.
          </p>
          <Link className="btn btn-small" to="../stage/routing" relative="path">
            Open routing
          </Link>
        </div>
      </div>
      <h3 className="small-head">Audit log</h3>
      <ol className="audit">
        {log.map((e) => (
          <li key={e.id}>
            <time dateTime={e.at}>{new Date(e.at).toLocaleString()}</time> · {e.kind}
            {e.version ? ` v${e.version}` : ""}
            {e.status ? ` → ${e.status}` : ""}
            {e.action ? ` (${e.action})` : ""}
            {e.message ? `: ${e.message}` : ""}
          </li>
        ))}
      </ol>
    </div>
  );
}
