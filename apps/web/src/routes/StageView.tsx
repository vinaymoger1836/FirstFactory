import { lazy, Suspense, useCallback, useEffect, useId, useState } from "react";
import { Link, useParams } from "react-router";
import { PageTitle } from "../components/AppShell";
import { AssemblyTree } from "../components/AssemblyTree";
import { NoteDialog } from "../components/NoteDialog";
import { BomPanel } from "../components/bom/BomPanel";
import { ProvBadge } from "../components/ProvBadge";
import { blockersOf } from "../lib/bom";
import { fmtValue, isProv, type ProvLike } from "../lib/prov";
import {
  api,
  ApiError,
  post,
  type AssemblyNodeOut,
  type AttachmentOut,
  type QuestionOut,
  type RunOut,
  type StageOut,
} from "../lib/api";
import { useProjectEvents, type StreamEvent } from "../lib/sse";
import { STAGE_BY_KEY, STATUS_LABEL, STATUS_TONE, STEP_LABEL } from "../lib/stages";
import "./project.css";

const GlbViewer = lazy(() => import("../components/GlbViewer"));

type Edits = Record<string, number | string>;

export function StageView() {
  const { token = "", stage = "" } = useParams();
  const info = STAGE_BY_KEY[stage];
  const [data, setData] = useState<StageOut | null>(null);
  const [run, setRun] = useState<RunOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<{ step: string; pct: number } | null>(null);
  const [message, setMessage] = useState<{ text: string; tone: "ok" | "bad" | "warn" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Edits>({});
  const [dialog, setDialog] = useState<"rerun" | "reject" | null>(null);
  const [files, setFiles] = useState<AttachmentOut[]>([]);

  const load = useCallback(
    (version?: number) => {
      api<StageOut>(`/projects/current/stages/${stage}`, {}, token)
        .then((d) => {
          setData(d);
          if (version && d.latest && version !== d.latest.version) {
            return api<RunOut>(`/projects/current/stages/${stage}/runs/${version}`, {}, token).then(setRun);
          }
          setRun(d.latest);
          return undefined;
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Not found"));
    },
    [stage, token],
  );

  useEffect(() => {
    load();
    if (stage === "intake" || stage === "bom")
      api<AttachmentOut[]>("/projects/current/attachments", {}, token)
        .then(setFiles)
        .catch(() => undefined);
  }, [load, stage, token]);

  const onEvent = useCallback(
    (e: StreamEvent) => {
      if (e.data.stage !== stage) return;
      if (e.event === "progress") setStep({ step: String(e.data.step), pct: Number(e.data.pct) || 0 });
      if (["status", "version", "paused", "skipped"].includes(e.event)) {
        load();
        if (e.event === "status" && e.data.status !== "running") setStep(null);
      }
    },
    [load, stage],
  );
  useProjectEvents(token, onEvent);

  if (!info)
    return (
      <>
        <PageTitle>Unknown stage</PageTitle>
        <p>
          <Link to={`/p/${token}`}>Back to the pipeline</Link>
        </p>
      </>
    );
  if (error)
    return (
      <>
        <PageTitle>Stage not found</PageTitle>
        <p className="notice" data-tone="bad" role="alert">
          {error}
        </p>
      </>
    );

  const latest = data?.latest ?? null;
  const isLatest = !!run && !!latest && run.version === latest.version;
  const status = run?.status ?? "pending";
  const openQuestions = (run?.questions ?? []).filter((q) => !q.answer);
  const canEdit =
    stage !== "bom" && isLatest && !!run && !run.placeholder && ["awaiting_approval", "approved"].includes(status);
  const blockers = isLatest ? blockersOf(run) : 0;

  async function act(path: string, body: unknown, ok: string) {
    setBusy(true);
    setMessage(null);
    try {
      await post(`/projects/current/stages/${stage}/${path}`, body, token);
      setMessage({ text: ok, tone: "ok" });
    } catch (e) {
      setMessage({ text: e instanceof ApiError ? e.message : "The action failed.", tone: "bad" });
    } finally {
      setBusy(false);
    }
  }

  async function saveEdits(approve: boolean) {
    if (!run) return;
    setBusy(true);
    setMessage(null);
    try {
      const body = {
        version: run.version,
        approve,
        edits: Object.entries(edits).map(([path, value]) => ({ path, value })),
      };
      const res = await api<RunOut>(`/projects/current/stages/${stage}/edit`, { method: "POST", body: JSON.stringify(body) }, token);
      setEditing(false);
      setEdits({});
      setMessage({
        text: `Saved as version ${res.version}${approve ? " and approved. Later stages built on the old version are now out of date." : "."}`,
        tone: "ok",
      });
      load();
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.message}${e.issues.length ? `: ${e.issues.map((i) => i.message).join("; ")}` : ""}` : "Could not save.";
      setMessage({ text: msg, tone: "bad" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <nav aria-label="Breadcrumb" className="crumbs">
        <Link to={`/p/${token}`}>Pipeline</Link> <span aria-hidden="true">/</span> {info.code} {info.label}
      </nav>
      <PageTitle>{`${info.code} ${info.label}`}</PageTitle>
      <div className="stage-head">
        <span className="badge" data-tone={STATUS_TONE[status]}>
          {STATUS_LABEL[status]}
        </span>
        {run?.placeholder && <span className="badge">Placeholder</span>}
        {data && data.versions.length > 0 && (
          <label className="version-pick">
            <span>Version</span>
            <select
              className="input"
              value={run?.version ?? ""}
              onChange={(e) => {
                setEditing(false);
                load(Number(e.target.value));
              }}
            >
              {data.versions.map((v) => (
                <option key={v.version} value={v.version}>
                  v{v.version} · {STATUS_LABEL[v.status]}
                </option>
              ))}
            </select>
          </label>
        )}
        {run && run.token_budget > 0 && (
          <span className="muted small">
            Model tokens: {run.tokens_used.toLocaleString()} of {run.token_budget.toLocaleString()}
          </span>
        )}
      </div>
      <p className="muted">{info.blurb}</p>

      <div aria-live="polite" role="status">
        {message && (
          <p className="notice" data-tone={message.tone}>
            {message.text}
          </p>
        )}
      </div>

      {!run && <p className="notice">This stage has not run yet. It starts when the stages it depends on are approved.</p>}

      {run && (status === "running" || step) && (
        <div className="card activity">
          <div className="activity__text">
            <span className="pulse" aria-hidden="true" />
            {step ? (STEP_LABEL[step.step] ?? step.step) : "Running"}…
          </div>
          <div className="progress" role="progressbar" aria-label="Stage progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={step?.pct ?? 5}>
            <div className="progress__bar" style={{ transform: `scaleX(${(step?.pct ?? 5) / 100})` }} />
          </div>
        </div>
      )}

      {run && status === "paused" && isLatest && <PausedBanner run={run} busy={busy} onResume={(extra) => void act("resume", { extra_budget: extra }, "Continuing the stage.")} />}

      {run && status === "failed" && (
        <div className="callout" data-tone="bad" role="alert">
          <div>
            <strong>This stage failed.</strong>
            <ul>
              {run.issues.filter((i) => i.severity === "error").map((i, k) => (
                <li key={k}>
                  {i.path && <code>{i.path}</code>} {i.message}
                </li>
              ))}
            </ul>
          </div>
          {isLatest && (
            <button type="button" className="btn btn-small" onClick={() => setDialog("rerun")} disabled={busy}>
              Re-run
            </button>
          )}
        </div>
      )}

      {run && isLatest && (status === "awaiting_approval" || canEdit) && (
        <div className="action-bar" role="toolbar" aria-label="Stage actions">
          {status === "awaiting_approval" && !editing && (
            <>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || blockers > 0}
                aria-describedby={blockers > 0 ? "approve-why" : undefined}
                onClick={() => void act("approve", { version: run.version }, "Approved. Later stages will start now.")}
              >
                Approve v{run.version}
              </button>
              {blockers > 0 && (
                <span id="approve-why" className="small muted" role="status">
                  {blockers} item{blockers > 1 ? "s" : ""} still need a decision
                </span>
              )}
            </>
          )}
          {canEdit && !editing && (
            <button type="button" className="btn" onClick={() => setEditing(true)} disabled={busy}>
              Edit values
            </button>
          )}
          {editing && (
            <>
              <button type="button" className="btn btn-primary" disabled={busy || !Object.keys(edits).length} onClick={() => void saveEdits(true)}>
                Save and approve
              </button>
              <button type="button" className="btn" disabled={busy || !Object.keys(edits).length} onClick={() => void saveEdits(false)}>
                Save as new version
              </button>
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => {
                  setEditing(false);
                  setEdits({});
                }}
              >
                Cancel
              </button>
            </>
          )}
          {status === "awaiting_approval" && !editing && (
            <>
              <button type="button" className="btn" onClick={() => setDialog("rerun")} disabled={busy}>
                Re-run with note
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => setDialog("reject")} disabled={busy}>
                Reject
              </button>
            </>
          )}
        </div>
      )}
      {editing && (
        <p className="notice">
          Edit any value below. Your values are saved with source “You” and are never overwritten by a re-run.
        </p>
      )}

      {run && run.summary && (
        <section className="card summary" aria-labelledby="sum-h">
          <h2 id="sum-h">Summary</h2>
          {run.summary.split("\n").map((l, i) => (
            <p key={i}>{l}</p>
          ))}
        </section>
      )}

      {run && isLatest && status === "awaiting_approval" && openQuestions.length > 0 && (
        <Questions questions={openQuestions} busy={busy} onSubmit={(answers) => void act("answer", { answers }, "Answers sent. The stage is updating.")} />
      )}

      {run?.notices && run.notices.length > 0 && (
        <section aria-labelledby="not-h" className="card">
          <h2 id="not-h">Notices</h2>
          <ul>
            {run.notices.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </section>
      )}

      {run && run.artifact && stage === "bom" && (
        <BomPanel
          run={run}
          token={token}
          files={files}
          canReview={isLatest && status === "awaiting_approval"}
          busy={busy}
          step={step?.step ?? null}
          onAnswer={(answers) => void act("answer", { answers }, "Review applied. A new version is being built.")}
        />
      )}
      {run && run.artifact && stage === "intake" && (
        <IntakeArtifact
          artifact={run.artifact}
          editing={editing}
          edits={edits}
          setEdit={(p, v) => setEdits((e) => ({ ...e, [p]: v }))}
          token={token}
          files={files}
        />
      )}
      {run && !run.artifact && run.placeholder && (
        <p className="notice">Nothing to show: this stage is built in a later phase. Its version still tracks what it depends on.</p>
      )}

      {run && run.assumptions.length > 0 && (
        <section aria-labelledby="as-h" className="card">
          <h2 id="as-h">Assumptions to check ({run.assumptions.length})</h2>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Where</th>
                  <th scope="col">Value</th>
                  <th scope="col">Source</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {run.assumptions.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <code>{a.path}</code>
                    </td>
                    <td>{fmtValue(a.value, a.unit)}</td>
                    <td>
                      <ProvBadge p={a as ProvLike} />
                    </td>
                    <td>{a.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {run && run.issues.some((i) => i.severity === "warning") && (
        <section aria-labelledby="wa-h" className="card">
          <h2 id="wa-h">Warnings</h2>
          <ul>
            {run.issues
              .filter((i) => i.severity === "warning")
              .map((i, k) => (
                <li key={k}>
                  {i.message} {i.path && <code>{i.path}</code>}
                </li>
              ))}
          </ul>
        </section>
      )}

      <NoteDialog
        open={dialog === "rerun"}
        title="Re-run this stage"
        intro="The agent runs the stage again. Tell it what to do differently."
        confirm="Re-run"
        onClose={() => setDialog(null)}
        onConfirm={(note) => {
          setDialog(null);
          void act("rerun", { note }, "Re-running the stage.");
        }}
      />
      <NoteDialog
        open={dialog === "reject"}
        title="Reject this version"
        intro="Say what is wrong. The agent runs again with your note."
        confirm="Reject and re-run"
        required
        danger
        onClose={() => setDialog(null)}
        onConfirm={(note) => {
          setDialog(null);
          void act("reject", { note }, "Rejected. The agent is running again with your note.");
        }}
      />
    </>
  );
}

function PausedBanner({ run, busy, onResume }: { run: RunOut; busy: boolean; onResume: (extra: number) => void }) {
  const [extra, setExtra] = useState(run.token_budget || 10000);
  const id = useId();
  return (
    <div className="callout" data-tone="warn" role="region" aria-labelledby={`${id}-h`}>
      <div>
        <strong id={`${id}-h`}>Paused.</strong> This stage used {run.tokens_used.toLocaleString()} of its{" "}
        {run.token_budget.toLocaleString()} model tokens, or the model provider's free quota ran out. Everything done so far
        is kept.
        <label className="inline-field">
          <span>Extra tokens</span>
          <input className="input input--narrow" type="number" min={0} step={1000} value={extra} onChange={(e) => setExtra(Number(e.target.value))} />
        </label>
      </div>
      <button type="button" className="btn btn-primary btn-small" disabled={busy} onClick={() => onResume(extra)}>
        Continue
      </button>
    </div>
  );
}

function Questions({
  questions,
  busy,
  onSubmit,
}: {
  questions: QuestionOut[];
  busy: boolean;
  onSubmit: (answers: Record<string, string | number>) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string | number>>({});
  const uid = useId();
  return (
    <section className="card questions" aria-labelledby={`${uid}-h`}>
      <h2 id={`${uid}-h`}>Questions ({questions.length})</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(answers);
        }}
      >
        {questions.map((q) => (
          <fieldset key={q.id} className="question">
            <legend>{q.text}</legend>
            {q.kind === "choice" ? (
              <div className="question__opts">
                {q.options.map((o) => (
                  <label key={o} className="question__opt">
                    <input type="radio" name={`${uid}-${q.id}`} checked={answers[q.id] === o} onChange={() => setAnswers((a) => ({ ...a, [q.id]: o }))} />{" "}
                    {o}
                  </label>
                ))}
              </div>
            ) : (
              <label className="inline-field">
                <span className="visually-hidden">Answer</span>
                <input
                  className="input"
                  type={q.kind === "number" ? "number" : "text"}
                  value={answers[q.id] ?? ""}
                  onChange={(e) =>
                    setAnswers((a) => ({ ...a, [q.id]: q.kind === "number" && e.target.value ? Number(e.target.value) : e.target.value }))
                  }
                />
                {q.unit && <span className="muted">{q.unit}</span>}
              </label>
            )}
          </fieldset>
        ))}
        <button type="submit" className="btn btn-primary" disabled={busy || !Object.keys(answers).length}>
          Send answers
        </button>
      </form>
    </section>
  );
}

// ── S1 artifact ─────────────────────────────────────────────────────────

interface Variant {
  code: string;
  name: string;
  mix_pct: ProvLike;
  params: Record<string, ProvLike>;
}
interface Part {
  part_no: string;
  name: string;
  qty: ProvLike;
  bbox_mm?: ProvLike | null;
  material?: ProvLike | null;
}

function EditableValue({
  path,
  p,
  editing,
  edits,
  setEdit,
  label,
}: {
  path: string;
  p: ProvLike;
  editing: boolean;
  edits: Edits;
  setEdit: (path: string, v: number | string) => void;
  label: string;
}) {
  const numeric = typeof p.value === "number";
  if (editing && (numeric || typeof p.value === "string")) {
    const v = edits[path] ?? (p.value as number | string);
    return (
      <input
        className="input input--narrow"
        aria-label={label}
        type={numeric ? "number" : "text"}
        step="any"
        value={v}
        onChange={(e) => setEdit(path, numeric ? Number(e.target.value) : e.target.value)}
      />
    );
  }
  return (
    <span className="val">
      <span className="val__v">{fmtValue(p.value, p.unit)}</span> <ProvBadge p={p} />
    </span>
  );
}

function IntakeArtifact({
  artifact,
  editing,
  edits,
  setEdit,
  token,
  files,
}: {
  artifact: Record<string, unknown>;
  editing: boolean;
  edits: Edits;
  setEdit: (path: string, v: number | string) => void;
  token: string;
  files: AttachmentOut[];
}) {
  const product = artifact.product as Record<string, unknown>;
  const variants = (product.variants ?? []) as Variant[];
  const demand = (product.demand ?? {}) as Record<string, unknown>;
  const structure = (artifact.structure ?? { parts: [], assemblies: [] }) as { parts: Part[]; assemblies: AssemblyNodeOut[] };
  const params = Array.from(new Set(variants.flatMap((v) => Object.keys(v.params))));
  const archetype = product.archetype as ProvLike;
  const ext = (artifact.extensions ?? {}) as Record<string, Record<string, unknown>>;
  const cadFiles = files.filter((f) => f.has_glb);

  return (
    <>
      <section className="card" aria-labelledby="pr-h">
        <h2 id="pr-h">Product</h2>
        <dl className="kv">
          <div>
            <dt>Name</dt>
            <dd>{String(product.name)}</dd>
          </div>
          <div>
            <dt>Product type</dt>
            <dd>
              <span className="val">
                {String(archetype.value).replace(/_/g, " ")} <ProvBadge p={archetype} />
              </span>
            </dd>
          </div>
          <div>
            <dt>Annual volume</dt>
            <dd>
              {isProv(demand.annual_volume) ? (
                <EditableValue
                  path="/product/demand/annual_volume"
                  p={demand.annual_volume}
                  editing={editing}
                  edits={edits}
                  setEdit={setEdit}
                  label="Annual volume"
                />
              ) : (
                "Not given"
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="card" aria-labelledby="vp-h">
        <h2 id="vp-h">Variant parameters</h2>
        <p className="muted small">Used by the routing and capacity formulas. Low-confidence values are marked; please check them.</p>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Parameter</th>
                {variants.map((v) => (
                  <th key={v.code} scope="col">
                    {v.code} <span className="muted">({fmtValue(v.mix_pct.value, "%")})</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {params.map((name) => (
                <tr key={name}>
                  <th scope="row">{name.replace(/_/g, " ")}</th>
                  {variants.map((v, i) => {
                    const p = v.params[name];
                    return (
                      <td key={v.code}>
                        {p ? (
                          <EditableValue
                            path={`/product/variants/${i}/params/${name}`}
                            p={p}
                            editing={editing}
                            edits={edits}
                            setEdit={setEdit}
                            label={`${name.replace(/_/g, " ")} for ${v.code}`}
                          />
                        ) : (
                          "—"
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {(structure.parts.length > 0 || structure.assemblies.length > 0) && (
        <section className="card" aria-labelledby="st-h">
          <h2 id="st-h">Parts found in your files ({structure.parts.length})</h2>
          <div className="structure">
            {structure.assemblies.map((a) => (
              <div key={a.id} className="structure__tree">
                <h3 className="small-head">Assembly tree</h3>
                <AssemblyTree root={a} label={`Assembly ${a.name}`} />
              </div>
            ))}
            <div className="table-scroll structure__parts">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Part</th>
                    <th scope="col">Name</th>
                    <th scope="col">Qty</th>
                    <th scope="col">Size (mm)</th>
                  </tr>
                </thead>
                <tbody>
                  {structure.parts.map((p) => (
                    <tr key={p.part_no}>
                      <td>
                        <code>{p.part_no}</code>
                      </td>
                      <td>{p.name}</td>
                      <td>
                        <span className="val">
                          {fmtValue(p.qty.value)} <ProvBadge p={p.qty} />
                        </span>
                      </td>
                      <td>{p.bbox_mm ? fmtValue(p.bbox_mm.value) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {cadFiles.length > 0 && (
            <Suspense fallback={<p role="status">Loading 3D viewer…</p>}>
              {cadFiles.map((f) => (
                <GlbViewer key={f.id} token={token} attachmentId={f.id} label={f.filename} />
              ))}
            </Suspense>
          )}
        </section>
      )}

      {Object.keys(ext).length > 0 && (
        <section className="card" aria-labelledby="ex-h">
          <h2 id="ex-h">More from your files</h2>
          {Object.entries(ext).map(([ns, vals]) => (
            <div key={ns}>
              <h3 className="small-head">{ns.replace(/_/g, " ")}</h3>
              <dl className="kv">
                {Object.entries(vals).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k.replace(/_/g, " ")}</dt>
                    <dd>{isProv(v) ? <span className="val">{fmtValue(v.value, v.unit)} <ProvBadge p={v} /></span> : String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
