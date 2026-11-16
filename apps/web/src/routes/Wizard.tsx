import { SAMPLES, TEMPLATE_SCHEMA, type ProductTemplate } from "@fl/ts-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { PageTitle } from "../components/AppShell";
import { defaultFor, SchemaField, type FieldCtx } from "../components/wizard/SchemaField";
import { FilesStep } from "../components/wizard/FilesStep";
import { api, ApiError, post, type AttachmentOut, type ProjectCreated } from "../lib/api";
import { checkValue, pathKey, SchemaTools, setAt, type Path, type Schema } from "../lib/schema";
import "./wizard.css";

const DRAFT_KEY = "fl-wizard-draft";

interface StepDef {
  key: string;
  title: string;
  intro: string;
  fields: Path[];
}

const STEPS: StepDef[] = [
  {
    key: "product",
    title: "Product",
    intro: "What you want to make. A few plain sentences are enough.",
    fields: [["product", "name"], ["product", "category"], ["product", "archetype"], ["product", "description"]],
  },
  {
    key: "variants",
    title: "Variants & volume",
    intro: "Sizes or models you will make, how the volume splits between them, and your targets.",
    fields: [
      ["product", "variants"],
      ["product", "demand", "annual_volume"],
      ["product", "targets", "unit_cost_inr"],
      ["product", "targets", "quality_level"],
      ["product", "targets", "automation_level"],
    ],
  },
  {
    key: "files",
    title: "Files and parts",
    intro: "CAD, photos, datasheets and your BoM if you have one. All optional; more files mean fewer questions.",
    fields: [],
  },
  {
    key: "site",
    title: "Site",
    intro: "Where the factory will be and how many shifts you plan to run.",
    fields: [["site", "state"], ["site", "district"], ["site", "mode"], ["site", "plot"], ["site", "shed"], ["operating_pattern"]],
  },
  {
    key: "money",
    title: "Money (optional)",
    intro: "Budget and financing. Leave blank and the finance stage uses sensible defaults.",
    fields: [["finance_inputs"]],
  },
  { key: "review", title: "Review", intro: "Check everything, then start the pipeline.", fields: [] },
];

const LABELS: Record<string, string> = {
  "product.name": "Product name",
  "product.category": "Category",
  "product.archetype": "Closest product type",
  "product.description": "Description",
  "product.variants": "Variants",
  "product.variants.*": "variant",
  "product.variants.*.code": "Variant code",
  "product.variants.*.name": "Variant name",
  "product.variants.*.mix_pct": "Share of volume (%)",
  "product.variants.*.selling_price_inr": "Selling price (₹)",
  "product.variants.*.differences": "How it differs",
  "product.demand.annual_volume": "Annual volume (units, all variants)",
  "product.targets.unit_cost_inr": "Target cost per unit (₹)",
  "product.targets.quality_level": "Quality level",
  "product.targets.automation_level": "Automation",
  "site.state": "State",
  "site.district": "District",
  "site.mode": "Building",
  "site.plot": "Plot size",
  "site.shed": "Shed",
  "site.shed.doors.*": "door",
  "site.shed.column_grid_m": "Column grid (m)",
  operating_pattern: "Working pattern",
  finance_inputs: "Money",
  "finance_inputs.debt_equity": "Loan : own money (%)",
  "finance_inputs.land_cost_inr_per_sqm": "Land cost (₹ per m²)",
};
const RECOMMENDED = new Set([
  "product.demand.annual_volume",
  "product.variants.*.selling_price_inr",
  "finance_inputs.budget_inr",
  "site.plot",
]);
const NULL_LABEL = { "product.archetype": "Let intake decide" };

const tools = new SchemaTools(TEMPLATE_SCHEMA as Schema);

function emptyTemplate(): ProductTemplate {
  const t = defaultFor(tools, []) as ProductTemplate;
  t.product.variants = [{ code: "V1", name: "", mix_pct: 100, selling_price_inr: null, differences: "" }];
  return t;
}

function loadDraft(): { template: ProductTemplate; token: string | null } | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    return raw ? (JSON.parse(raw) as { template: ProductTemplate; token: string | null }) : null;
  } catch {
    return null;
  }
}

function saveDraft(template: ProductTemplate, token: string | null) {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ template, token }));
  } catch {
    /* storage unavailable: the draft just isn't kept */
  }
}

function collect(path: Path, value: unknown, out: Record<string, string>, hidden: Set<string>) {
  const pk = path.map((p) => (typeof p === "number" ? "*" : p)).join(".");
  if (hidden.has(pk)) return;
  const { schema, required, nullable } = tools.at(path);
  if (schema.type === "object") {
    if (value === null || value === undefined) {
      if (required && !nullable) out[pathKey(path)] = "This is required.";
      return;
    }
    for (const k of Object.keys((schema.properties ?? {}) as Record<string, unknown>))
      collect([...path, k], (value as Record<string, unknown>)[k], out, hidden);
    return;
  }
  if (schema.type === "array" && !Array.isArray(schema.prefixItems)) {
    const arr = Array.isArray(value) ? value : [];
    if (typeof schema.minItems === "number" && arr.length < schema.minItems)
      out[pathKey(path)] = `Add at least ${schema.minItems}.`;
    arr.forEach((v, i) => collect([...path, i], v, out, hidden));
    return;
  }
  if (schema.type === "array") {
    const arr = Array.isArray(value) ? value : [];
    if (arr.some((v) => typeof v !== "number")) out[pathKey(path)] = "Enter both numbers.";
    return;
  }
  const msg = checkValue(schema, value, required, nullable);
  if (msg) out[pathKey(path)] = msg;
}

function hiddenFor(t: ProductTemplate): Set<string> {
  const h = new Set(["product.demand.ramp_plan"]);
  h.add(t.site.mode === "greenfield" ? "site.shed" : "site.plot");
  return h;
}

function stepErrors(step: StepDef, t: ProductTemplate): Record<string, string> {
  const out: Record<string, string> = {};
  const hidden = hiddenFor(t);
  for (const f of step.fields) {
    let v: unknown = t;
    for (const k of f) v = (v as Record<string | number, unknown> | null)?.[k];
    collect(f, v, out, hidden);
  }
  if (step.key === "variants") {
    const sum = t.product.variants.reduce((a, v) => a + (Number(v.mix_pct) || 0), 0);
    if (Math.abs(sum - 100) > 0.01) out["product.variants"] = `Shares must add up to 100% (now ${sum}%).`;
    const codes = t.product.variants.map((v) => v.code);
    if (new Set(codes).size !== codes.length) out["product.variants"] = "Each variant needs its own code.";
  }
  if (step.key === "site" && t.site.mode === "existing_shed" && !t.site.shed)
    out["site.shed"] = "Add the shed's size for an existing shed.";
  return out;
}

function fieldLabel(key: string): string {
  const pattern = key
    .split(".")
    .map((p) => (/^\d+$/.test(p) ? "*" : p))
    .join(".");
  return LABELS[pattern] ?? key.split(".").pop()?.replace(/_/g, " ") ?? key;
}

export function Wizard() {
  const draft = useMemo(loadDraft, []);
  const [template, setTemplate] = useState<ProductTemplate>(draft?.template ?? emptyTemplate());
  const [token, setToken] = useState<string | null>(draft?.token ?? null);
  const [files, setFiles] = useState<AttachmentOut[]>([]);
  const [step, setStep] = useState(0);
  const [dir, setDir] = useState<"fwd" | "back">("fwd");
  const [visited, setVisited] = useState(1);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverIssues, setServerIssues] = useState<{ path: string; message: string }[] | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ token: string; link: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const creating = useRef<Promise<string> | null>(null);

  useEffect(() => saveDraft(template, token), [template, token]);

  // Files already uploaded to a draft project (e.g. after a reload).
  useEffect(() => {
    if (!token) return;
    api<AttachmentOut[]>("/projects/current/attachments", {}, token)
      .then(setFiles)
      .catch(() => setToken(null));
  }, [token]);

  const current = STEPS[step] as StepDef;

  function onChange(path: Path, v: unknown) {
    setTemplate((t) => setAt(t, path, v));
    if (errors[pathKey(path)]) setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => k !== pathKey(path))));
  }

  async function ensureProject(): Promise<string> {
    if (token) return token;
    if (!creating.current) {
      creating.current = post<ProjectCreated>("/projects", {
        name: template.product.name.trim() || "Untitled project",
      }).then((p) => {
        setToken(p.token);
        return p.token;
      });
    }
    return creating.current;
  }

  function go(to: number) {
    setDir(to > step ? "fwd" : "back");
    setStep(to);
    setVisited((v) => Math.max(v, to + 1));
    setErrors({});
    requestAnimationFrame(() => headingRef.current?.focus());
  }

  function next() {
    const errs = stepErrors(current, template);
    setErrors(errs);
    if (Object.keys(errs).length) {
      requestAnimationFrame(() => summaryRef.current?.focus());
      return;
    }
    go(step + 1);
  }

  useEffect(() => {
    if (current.key !== "review") return;
    setServerIssues(null);
    post<{ ok: boolean; issues: { path: string; message: string }[] }>("/templates/validate", template)
      .then((r) => setServerIssues(r.issues))
      .catch(() => setServerIssues([]));
  }, [current.key, template]);

  async function submit() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const tok = await ensureProject();
      await api("/projects/current/template", { method: "PUT", body: JSON.stringify(template) }, tok);
      await post("/projects/current/submit", {}, tok);
      setCreated({ token: tok, link: `${window.location.origin}/p/${tok}` });
      try {
        sessionStorage.removeItem(DRAFT_KEY);
      } catch {
        /* ignore */
      }
    } catch (e) {
      setSubmitError(
        e instanceof ApiError
          ? `${e.message}${e.issues.length ? `: ${e.issues.map((i) => `${i.path} ${i.message}`).join("; ")}` : ""}`
          : "Could not submit. Is the API running?",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function loadSample(key: string) {
    const s = SAMPLES[key];
    if (!s) return;
    setTemplate({ ...structuredClone(s.template), attachments: template.attachments });
    setErrors({});
  }

  function reset() {
    setTemplate(emptyTemplate());
    setToken(null);
    setFiles([]);
    creating.current = null;
    go(0);
  }

  const ctx: FieldCtx = {
    tools,
    value: template,
    onChange,
    errors,
    labels: LABELS,
    recommended: RECOMMENDED,
    hidden: hiddenFor(template),
    nullLabel: NULL_LABEL,
  };
  const errorList = Object.entries(errors);

  if (created) {
    return (
      <>
        <PageTitle>Project started</PageTitle>
        <div className="card wizard-done">
          <p>
            Intake is reading your product now. This is your private project link. <strong>Save it</strong>: anyone
            with the link can open the project, and it is shown only once.
          </p>
          <div className="link-box">
            <code>{created.link}</code>
            <button
              type="button"
              className="btn btn-small"
              onClick={() => {
                void navigator.clipboard.writeText(created.link).then(() => setCopied(true));
              }}
            >
              {copied ? "Copied" : "Copy link"}
            </button>
          </div>
          <p role="status" className="visually-hidden">
            {copied ? "Link copied to the clipboard" : ""}
          </p>
          <button type="button" className="btn btn-primary" onClick={() => void navigate(`/p/${created.token}`)}>
            Open the pipeline
          </button>
        </div>
      </>
    );
  }

  return (
    <>
      <PageTitle>New project</PageTitle>
      <div className="wizard-top">
        <p className="muted">Fields marked * are required. Everything else helps, and we suggest what you skip.</p>
        <div className="sample-menu" role="group" aria-label="Load a sample">
          <span className="muted small">Load a sample:</span>
          {Object.entries(SAMPLES).map(([k, s]) => (
            <button key={k} type="button" className="btn btn-small" onClick={() => loadSample(k)}>
              {s.label}
            </button>
          ))}
          <button type="button" className="btn btn-small btn-quiet" onClick={reset}>
            Start over
          </button>
        </div>
      </div>

      <nav aria-label="Wizard steps">
        <ol className="stepper">
          {STEPS.map((s, i) => (
            <li key={s.key} className="stepper__item" data-state={i < step ? "done" : i === step ? "current" : "todo"}>
              <button
                type="button"
                className="stepper__btn"
                aria-current={i === step ? "step" : undefined}
                disabled={i >= visited}
                onClick={() => go(i)}
              >
                <span className="stepper__n" aria-hidden="true">
                  {i < step ? "✓" : i + 1}
                </span>
                <span className="stepper__label">
                  {s.title}
                  {i < step && <span className="visually-hidden"> (done)</span>}
                </span>
              </button>
            </li>
          ))}
        </ol>
        <div
          className="stepper__progress"
          role="progressbar"
          aria-label="Wizard progress"
          aria-valuemin={1}
          aria-valuemax={STEPS.length}
          aria-valuenow={step + 1}
          aria-valuetext={`Step ${step + 1} of ${STEPS.length}: ${current.title}`}
        >
          <span style={{ transform: `scaleX(${(step + 1) / STEPS.length})` }} />
        </div>
      </nav>

      <section className="card step-panel" data-dir={dir} key={current.key} aria-labelledby="step-heading">
        <h2 id="step-heading" ref={headingRef} tabIndex={-1}>
          {step + 1}. {current.title}
        </h2>
        <p className="muted">{current.intro}</p>

        {errorList.length > 0 && (
          <div className="error-summary" role="alert" tabIndex={-1} ref={summaryRef}>
            <p>
              <strong>Please fix {errorList.length === 1 ? "this" : `these ${errorList.length}`} before continuing:</strong>
            </p>
            <ul>
              {errorList.map(([k, m]) => (
                <li key={k}>
                  <a href={`#f-${k.replace(/[^A-Za-z0-9_-]/g, "-")}`}>
                    {fieldLabel(k)}: {m}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {current.key === "files" && (
          <FilesStep
            template={template}
            setTemplate={setTemplate}
            files={files}
            setFiles={setFiles}
            ensureProject={ensureProject}
          />
        )}
        {current.key === "variants" && <MixMeter template={template} />}
        {current.fields.map((f) => (
          <SchemaField key={pathKey(f)} ctx={ctx} path={f} />
        ))}
        {current.key === "review" && <Review template={template} files={files} issues={serverIssues} onEdit={go} />}

        {submitError && (
          <p className="notice" data-tone="bad" role="alert">
            {submitError}
          </p>
        )}
        <div className="step-actions">
          {step > 0 && (
            <button type="button" className="btn" onClick={() => go(step - 1)}>
              Back
            </button>
          )}
          {current.key !== "review" ? (
            <button type="button" className="btn btn-primary" onClick={next}>
              Next: {STEPS[step + 1]?.title}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void submit()}
              disabled={submitting || (serverIssues?.length ?? 0) > 0}
              aria-busy={submitting}
            >
              {submitting ? "Starting…" : "Create project and start intake"}
            </button>
          )}
        </div>
      </section>
      <p className="muted small">
        Already have a project? Open its private link. <Link to="/pipeline">How the pipeline works</Link>
      </p>
    </>
  );
}

function MixMeter({ template }: { template: ProductTemplate }) {
  const sum = template.product.variants.reduce((a, v) => a + (Number(v.mix_pct) || 0), 0);
  const ok = Math.abs(sum - 100) <= 0.01;
  return (
    <div className="mix-meter" data-ok={ok}>
      <div className="mix-meter__bar" aria-hidden="true">
        {template.product.variants.map((v, i) => (
          <span key={i} style={{ flexGrow: Math.max(0, Number(v.mix_pct) || 0) }} title={`${v.code}: ${v.mix_pct}%`} />
        ))}
      </div>
      <p role="status" aria-live="polite" className="small">
        Shares add up to {sum}%{ok ? "." : ". They need to add up to 100%."}
      </p>
    </div>
  );
}

function Review({
  template,
  files,
  issues,
  onEdit,
}: {
  template: ProductTemplate;
  files: AttachmentOut[];
  issues: { path: string; message: string }[] | null;
  onEdit: (step: number) => void;
}) {
  const p = template.product;
  const rows: { step: number; title: string; items: [string, string][] }[] = [
    {
      step: 0,
      title: "Product",
      items: [
        ["Name", p.name],
        ["Category", p.category],
        ["Product type", p.archetype ?? "Let intake decide"],
        ["Description", p.description],
      ],
    },
    {
      step: 1,
      title: "Variants & volume",
      items: [
        ...p.variants.map((v): [string, string] => [v.code, `${v.name} · ${v.mix_pct}%${v.differences ? ` · ${v.differences}` : ""}`]),
        ["Annual volume", p.demand.annual_volume ? p.demand.annual_volume.toLocaleString() : "We will suggest one"],
      ],
    },
    {
      step: 2,
      title: "Files and parts",
      items: [
        ["Do you have a BoM?", { have_full: "Yes", have_partial: "Part of it", none: "No" }[template.bom_hint]],
        ["Files", files.length ? files.map((f) => f.filename).join(", ") : "None"],
        ["Parts typed", String(template.parts_paste?.length ?? 0)],
      ],
    },
    {
      step: 3,
      title: "Site",
      items: [
        ["Location", `${template.site.district}, Karnataka`],
        ["Building", template.site.mode === "greenfield" ? "New building" : "Existing shed"],
        ["Shifts", `${template.operating_pattern.shifts_per_day} per day, ${template.operating_pattern.days_per_week} days a week`],
      ],
    },
    {
      step: 4,
      title: "Money",
      items: [["Budget", template.finance_inputs.budget_inr ? `₹${template.finance_inputs.budget_inr.toLocaleString("en-IN")}` : "Not given"]],
    },
  ];
  return (
    <div className="review">
      {issues === null && <p role="status">Checking the template…</p>}
      {issues && issues.length > 0 && (
        <div className="error-summary" role="alert">
          <p>
            <strong>The server found problems:</strong>
          </p>
          <ul>
            {issues.map((i) => (
              <li key={i.path + i.message}>
                {i.path}: {i.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {issues && issues.length === 0 && (
        <p className="notice" data-tone="ok" role="status">
          The template is complete.
        </p>
      )}
      {rows.map((r) => (
        <section key={r.title} className="review__block" aria-labelledby={`rv-${r.step}`}>
          <div className="review__head">
            <h3 id={`rv-${r.step}`}>{r.title}</h3>
            <button type="button" className="btn btn-small btn-quiet" onClick={() => onEdit(r.step)}>
              Edit<span className="visually-hidden"> {r.title}</span>
            </button>
          </div>
          <dl className="review__list">
            {r.items.map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v || "—"}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
