import { useId, useState } from "react";
import { getAt, humanize, pathKey, type Path, type Schema, type SchemaTools } from "../../lib/schema";
import { defaultFor, ENUM_LABEL } from "../../lib/schemaDefaults";

export interface FieldCtx {
  tools: SchemaTools;
  value: unknown;
  onChange: (path: Path, v: unknown) => void;
  errors: Record<string, string>;
  labels?: Record<string, string>;
  recommended?: Set<string>;
  hidden?: Set<string>;
  nullLabel?: Record<string, string>;
}

/** Path with array indexes replaced by "*", for label/hint lookups. */
function patternKey(path: Path): string {
  return path.map((p) => (typeof p === "number" ? "*" : p)).join(".");
}

function domId(path: Path): string {
  return `f-${pathKey(path).replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function Help({ schema, id }: { schema: Schema; id: string }) {
  const [open, setOpen] = useState(false);
  const desc = typeof schema.description === "string" ? schema.description : null;
  const ex = Array.isArray(schema.examples) ? schema.examples[0] : undefined;
  if (!desc) return null;
  return (
    <>
      <button
        type="button"
        className="help-toggle"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
      >
        <span aria-hidden="true">?</span>
        <span className="visually-hidden">{open ? "Hide help" : "Show help"}</span>
      </button>
      <span id={id} className="help-text" hidden={!open}>
        {desc}
        {ex !== undefined && (
          <>
            {" "}
            <span className="muted">Example: {Array.isArray(ex) ? ex.join(" × ") : String(ex)}</span>
          </>
        )}
      </span>
    </>
  );
}

export function SchemaField({ ctx, path }: { ctx: FieldCtx; path: Path }) {
  const { schema, nullable, required } = ctx.tools.at(path);
  const pk = patternKey(path);
  if (ctx.hidden?.has(pk)) return null;
  const key = path[path.length - 1];
  const label = ctx.labels?.[pk] ?? (typeof key === "string" ? humanize(key) : `Item ${Number(key) + 1}`);
  const value = getAt(ctx.value, path);
  const id = domId(path);
  const error = ctx.errors[pathKey(path)];
  const recommended = ctx.recommended?.has(pk);
  const type = schema.type;

  // Nullable object: "Add …" / "Remove".
  if (type === "object" && nullable) {
    if (value === null || value === undefined) {
      return (
        <div className="field field--optional">
          <span className="field__label">{label}</span>
          {recommended && <span className="hint">Recommended. We will suggest this if you skip it.</span>}
          <div>
            <button type="button" className="btn btn-small" onClick={() => ctx.onChange(path, defaultFor(ctx.tools, path))}>
              Add {label.toLowerCase()}
            </button>
          </div>
        </div>
      );
    }
    return (
      <ObjectFields ctx={ctx} path={path} schema={schema} label={label}>
        <button type="button" className="btn btn-small btn-quiet" onClick={() => ctx.onChange(path, null)}>
          Remove {label.toLowerCase()}
        </button>
      </ObjectFields>
    );
  }
  if (type === "object") return <ObjectFields ctx={ctx} path={path} schema={schema} label={label} />;

  if (type === "array" && Array.isArray(schema.prefixItems)) {
    const items = schema.prefixItems as Schema[];
    const arr = Array.isArray(value) ? (value as unknown[]) : [];
    return (
      <fieldset className="field tuple" aria-describedby={error ? `${id}-err` : undefined}>
        <legend className="field__label">
          {label} <Help schema={schema} id={`${id}-help`} />
        </legend>
        <div className="tuple__row">
          {items.map((_, i) => (
            <input
              key={i}
              className="input"
              type="number"
              inputMode="decimal"
              aria-label={`${label}, value ${i + 1}`}
              value={typeof arr[i] === "number" ? String(arr[i]) : ""}
              onChange={(e) => {
                const copy = [...arr];
                copy[i] = e.target.value === "" ? null : Number(e.target.value);
                ctx.onChange(path, copy);
              }}
            />
          ))}
        </div>
        {error && <p id={`${id}-err`} className="field__error">{error}</p>}
      </fieldset>
    );
  }

  if (type === "array") {
    const arr = Array.isArray(value) ? (value as unknown[]) : [];
    const min = typeof schema.minItems === "number" ? schema.minItems : 0;
    const max = typeof schema.maxItems === "number" ? schema.maxItems : 50;
    const itemLabel = ctx.labels?.[`${pk}.*`] ?? "item";
    return (
      <fieldset className="field array">
        <legend className="field__label">
          {label} <Help schema={schema} id={`${id}-help`} />
        </legend>
        <ol className="array__list">
          {arr.map((_, i) => (
            <li key={i} className="array__item">
              <div className="array__head">
                <span className="array__title">
                  {humanize(itemLabel)} {i + 1}
                </span>
                {arr.length > min && (
                  <button
                    type="button"
                    className="btn btn-small btn-quiet"
                    onClick={() => ctx.onChange(path, arr.filter((__, j) => j !== i))}
                  >
                    Remove<span className="visually-hidden"> {itemLabel} {i + 1}</span>
                  </button>
                )}
              </div>
              <SchemaField ctx={ctx} path={[...path, i]} />
            </li>
          ))}
        </ol>
        {arr.length < max && (
          <button
            type="button"
            className="btn btn-small"
            onClick={() => ctx.onChange(path, [...arr, defaultFor(ctx.tools, [...path, arr.length])])}
          >
            Add {itemLabel}
          </button>
        )}
        {error && <p className="field__error">{error}</p>}
      </fieldset>
    );
  }

  const describedBy = [error ? `${id}-err` : "", recommended ? `${id}-rec` : ""].filter(Boolean).join(" ") || undefined;
  const head = (
    <span className="field__head">
      <label htmlFor={id} className="field__label">
        {label}
        {required && !nullable && schema.default === undefined && <span className="req" aria-hidden="true"> *</span>}
      </label>
      <Help schema={schema} id={`${id}-help`} />
    </span>
  );
  const foot = (
    <>
      {recommended && (value === null || value === undefined || value === "") && (
        <span id={`${id}-rec`} className="hint">
          Recommended. We will suggest this if you leave it blank.
        </span>
      )}
      {error && (
        <p id={`${id}-err`} className="field__error">
          {error}
        </p>
      )}
    </>
  );

  const en = (schema.enum as string[] | undefined) ?? (typeof schema.const === "string" ? [schema.const] : undefined);
  if (en) {
    const opts = nullable ? [null, ...en] : en;
    if (opts.length <= 4) {
      return (
        <fieldset className="field" aria-describedby={describedBy}>
          <legend className="field__label">
            {label} <Help schema={schema} id={`${id}-help`} />
          </legend>
          <div className="segmented" role="radiogroup" aria-label={label}>
            {opts.map((o) => (
              <label key={String(o)} className="segmented__opt">
                <input
                  type="radio"
                  name={id}
                  checked={value === o || (o === null && (value === undefined || value === null))}
                  onChange={() => ctx.onChange(path, o)}
                />
                <span>{o === null ? (ctx.nullLabel?.[pk] ?? "Not sure") : (ENUM_LABEL[o] ?? humanize(o))}</span>
              </label>
            ))}
          </div>
          {foot}
        </fieldset>
      );
    }
    return (
      <div className="field">
        {head}
        <select
          id={id}
          className="input"
          value={typeof value === "string" ? value : ""}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => ctx.onChange(path, e.target.value === "" ? null : e.target.value)}
        >
          {nullable && <option value="">{ctx.nullLabel?.[pk] ?? "Not sure"}</option>}
          {en.map((o) => (
            <option key={o} value={o}>
              {ENUM_LABEL[o] ?? humanize(o)}
            </option>
          ))}
        </select>
        {foot}
      </div>
    );
  }

  if (type === "boolean") {
    return (
      <div className="field field--check">
        <label>
          <input type="checkbox" checked={value === true} onChange={(e) => ctx.onChange(path, e.target.checked)} /> {label}
        </label>
      </div>
    );
  }

  if (type === "number" || type === "integer") {
    return (
      <div className="field">
        {head}
        <input
          id={id}
          className="input"
          type="number"
          inputMode={type === "integer" ? "numeric" : "decimal"}
          step={type === "integer" ? 1 : "any"}
          value={typeof value === "number" ? String(value) : ""}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => ctx.onChange(path, e.target.value === "" ? null : Number(e.target.value))}
        />
        {foot}
      </div>
    );
  }

  const long = typeof schema.maxLength === "number" && schema.maxLength > 400;
  return (
    <div className="field">
      {head}
      {long ? (
        <textarea
          id={id}
          className="input textarea"
          rows={4}
          value={typeof value === "string" ? value : ""}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => ctx.onChange(path, e.target.value)}
        />
      ) : (
        <input
          id={id}
          className="input"
          value={typeof value === "string" ? value : ""}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => ctx.onChange(path, nullable && e.target.value === "" ? null : e.target.value)}
        />
      )}
      {foot}
    </div>
  );
}

function ObjectFields({
  ctx,
  path,
  schema,
  label,
  children,
}: {
  ctx: FieldCtx;
  path: Path;
  schema: Schema;
  label: string;
  children?: React.ReactNode;
}) {
  const props = Object.keys((schema.properties ?? {}) as Record<string, unknown>);
  const legendId = useId();
  const body = (
    <div className="object__grid">
      {props.map((p) => (
        <SchemaField key={p} ctx={ctx} path={[...path, p]} />
      ))}
    </div>
  );
  if (path.length === 0 || typeof path[path.length - 1] === "number") return body;
  return (
    <fieldset className="object" aria-labelledby={legendId}>
      <legend id={legendId} className="object__legend">
        {label}
      </legend>
      {body}
      {children}
    </fieldset>
  );
}
