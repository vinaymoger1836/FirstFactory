import type { ProductTemplate } from "@fl/ts-types";
import { useId, useRef, useState, type DragEvent } from "react";
import { api, ApiError, type AttachmentOut, type BomMapping } from "../../lib/api";

type Attachment = ProductTemplate["attachments"][number];
type Pasted = NonNullable<ProductTemplate["parts_paste"]>[number];

const ACCEPT = ".step,.stp,.stl,.jpg,.jpeg,.png,.webp,.pdf,.csv,.xlsx";
const KIND_LABEL: Record<string, string> = {
  step: "3D CAD (STEP)",
  stl: "3D mesh (STL)",
  photo: "Photo",
  pdf: "PDF / datasheet",
  bom_csv: "BoM file",
  text: "Text",
};
const HINTS: { value: ProductTemplate["bom_hint"]; title: string; text: string }[] = [
  { value: "have_full", title: "Yes, I will upload it", text: "A CSV or Excel file with all parts." },
  { value: "have_partial", title: "I have part of it", text: "Upload what you have; we suggest what is missing." },
  { value: "none", title: "No, draft one for me", text: "We start from your description, CAD and photos." },
];
const MAP_FIELDS: { key: keyof Omit<BomMapping, "variant_qty" | "confirmed">; label: string }[] = [
  { key: "part_no", label: "Part number" },
  { key: "name", label: "Part name" },
  { key: "qty", label: "Quantity per product" },
  { key: "uom", label: "Unit" },
  { key: "material", label: "Material" },
];

function fmtSize(n: number): string {
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} kB`;
}

export function FilesStep({
  template,
  setTemplate,
  files,
  setFiles,
  ensureProject,
}: {
  template: ProductTemplate;
  setTemplate: (t: ProductTemplate) => void;
  files: AttachmentOut[];
  setFiles: (f: AttachmentOut[]) => void;
  ensureProject: () => Promise<string>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [paste, setPaste] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const uid = useId();

  async function upload(list: FileList | File[]) {
    setError(null);
    const token = await ensureProject();
    let atts = [...template.attachments];
    let current = [...files];
    for (const file of Array.from(list)) {
      setBusy(`Uploading ${file.name}…`);
      try {
        const fd = new FormData();
        fd.append("file", file);
        const a = await api<AttachmentOut>("/projects/current/attachments", { method: "POST", body: fd }, token);
        current = [...current, a];
        const entry: Attachment = {
          id: a.id,
          kind: a.kind,
          file_ref: a.filename,
          note: null,
          bom_columns: a.bom ? { ...a.bom.proposal, confirmed: false } : null,
        };
        atts = [...atts, entry];
      } catch (e) {
        setError(e instanceof ApiError ? e.message : `${file.name} could not be uploaded.`);
      }
    }
    setFiles(current);
    setTemplate({ ...template, attachments: atts });
    setBusy(null);
  }

  async function remove(id: string) {
    const token = await ensureProject();
    try {
      await api(`/projects/current/attachments/${id}`, { method: "DELETE" }, token);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove the file.");
      return;
    }
    setFiles(files.filter((f) => f.id !== id));
    setTemplate({ ...template, attachments: template.attachments.filter((a) => a.id !== id) });
  }

  function setMapping(id: string, m: BomMapping) {
    setTemplate({
      ...template,
      attachments: template.attachments.map((a) => (a.id === id ? { ...a, bom_columns: m } : a)),
    });
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files.length) void upload(e.dataTransfer.files);
  }

  const parts: Pasted[] = template.parts_paste ?? [];
  const setParts = (p: Pasted[]) => setTemplate({ ...template, parts_paste: p.length ? p : null });

  function addPasted() {
    const rows = paste
      .split(/\r?\n/)
      .map((l) => l.split(/\t|,|;/).map((c) => c.trim()))
      .filter((c) => c[0]);
    const added: Pasted[] = rows.map((c) => {
      const q = Number(c[1]);
      return { name: c[0] ?? "", qty: c[1] && q > 0 ? q : null, note: c[2] ? c[2] : null };
    });
    setParts([...parts, ...added].slice(0, 500));
    setPaste("");
  }

  return (
    <div className="files-step">
      <fieldset className="field">
        <legend className="field__label">Do you have a bill of materials (BoM)?</legend>
        <div className="choice-cards" role="radiogroup" aria-label="Do you have a bill of materials?">
          {HINTS.map((h) => (
            <label key={h.value} className="choice-card">
              <input
                type="radio"
                name={`${uid}-bom`}
                checked={template.bom_hint === h.value}
                onChange={() => setTemplate({ ...template, bom_hint: h.value })}
              />
              <span className="choice-card__title">{h.title}</span>
              <span className="choice-card__text">{h.text}</span>
            </label>
          ))}
        </div>
        <span className="hint">This is only a hint. The BoM stage checks whatever you upload.</span>
      </fieldset>

      <section aria-labelledby={`${uid}-files`} className="field">
        <h3 id={`${uid}-files`} className="field__label">
          Files
        </h3>
        {/* Drag and drop is a mouse shortcut; the "choose files" button is the keyboard path. */}
        {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
        <div
          className="dropzone"
          data-drag={drag}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
        >
          <p>
            Drop files here, or{" "}
            <button type="button" className="btn btn-small" onClick={() => inputRef.current?.click()}>
              choose files
            </button>
          </p>
          <p className="muted small">
            STEP/STP or STL (up to 50 MB), photos JPG/PNG/WEBP (10 MB), PDF datasheets, BoM as CSV or XLSX.
            At most 20 files. 3D files are read in a sandbox; only extracted text goes to the AI models.
          </p>
          <input
            ref={inputRef}
            id={`${uid}-input`}
            className="visually-hidden"
            type="file"
            multiple
            accept={ACCEPT}
            aria-label="Choose files to upload"
            onChange={(e) => {
              if (e.target.files?.length) void upload(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
        <p role="status" aria-live="polite" className="muted small">
          {busy ?? ""}
        </p>
        {error && (
          <p role="alert" className="notice" data-tone="bad">
            {error}
          </p>
        )}
        {files.length > 0 && (
          <ul className="file-list">
            {files.map((f) => {
              const att = template.attachments.find((a) => a.id === f.id);
              return (
                <li key={f.id} className="file-list__item">
                  <div className="file-list__row">
                    <span className="badge">{KIND_LABEL[f.kind] ?? f.kind}</span>
                    <span className="file-list__name">{f.filename}</span>
                    <span className="muted small">{fmtSize(f.size_bytes)}</span>
                    <button type="button" className="btn btn-small btn-quiet" onClick={() => void remove(f.id)}>
                      Remove<span className="visually-hidden"> {f.filename}</span>
                    </button>
                  </div>
                  {f.bom && att?.bom_columns && (
                    <BomMapper
                      file={f}
                      mapping={att.bom_columns as BomMapping}
                      variants={template.product.variants.map((v) => v.code)}
                      onChange={(m) => setMapping(f.id, m)}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-labelledby={`${uid}-parts`} className="field">
        <h3 id={`${uid}-parts`} className="field__label">
          Parts list (optional)
        </h3>
        <p className="muted small">For a short list without a file. Quantity is per product.</p>
        {parts.length > 0 && (
          <table className="table parts-table">
            <caption className="visually-hidden">Parts you typed or pasted</caption>
            <thead>
              <tr>
                <th scope="col">Part name</th>
                <th scope="col">Qty</th>
                <th scope="col">Note</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {parts.map((p, i) => (
                <tr key={i}>
                  <td>
                    <input
                      className="input"
                      aria-label={`Part ${i + 1} name`}
                      value={p.name}
                      onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                    />
                  </td>
                  <td>
                    <input
                      className="input input--narrow"
                      type="number"
                      min={0}
                      step="any"
                      aria-label={`Part ${i + 1} quantity`}
                      value={p.qty ?? ""}
                      onChange={(e) =>
                        setParts(parts.map((x, j) => (j === i ? { ...x, qty: e.target.value ? Number(e.target.value) : null } : x)))
                      }
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      aria-label={`Part ${i + 1} note`}
                      value={p.note ?? ""}
                      onChange={(e) =>
                        setParts(parts.map((x, j) => (j === i ? { ...x, note: e.target.value || null } : x)))
                      }
                    />
                  </td>
                  <td>
                    <button type="button" className="btn btn-small btn-quiet" onClick={() => setParts(parts.filter((_, j) => j !== i))}>
                      Remove<span className="visually-hidden"> part {i + 1}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="parts-actions">
          <button type="button" className="btn btn-small" onClick={() => setParts([...parts, { name: "", qty: null, note: null }])}>
            Add a part
          </button>
        </div>
        <label htmlFor={`${uid}-paste`} className="field__label small-label">
          Or paste rows from a spreadsheet (name, quantity, note)
        </label>
        <textarea
          id={`${uid}-paste`}
          className="input textarea"
          rows={3}
          value={paste}
          placeholder={"21700 cell\t2\nScrew M2 x 6\t4"}
          onChange={(e) => setPaste(e.target.value)}
        />
        <div className="parts-actions">
          <button type="button" className="btn btn-small" disabled={!paste.trim()} onClick={addPasted}>
            Add pasted rows
          </button>
        </div>
      </section>
    </div>
  );
}

function BomMapper({
  file,
  mapping,
  variants,
  onChange,
}: {
  file: AttachmentOut;
  mapping: BomMapping;
  variants: string[];
  onChange: (m: BomMapping) => void;
}) {
  const uid = useId();
  const bom = file.bom;
  if (!bom) return null;
  const noQty = !mapping.qty && Object.keys(mapping.variant_qty).length === 0;
  const select = (value: string | null, label: string, set: (v: string | null) => void) => (
    <select className="input" aria-label={label} value={value ?? ""} onChange={(e) => set(e.target.value || null)}>
      <option value="">— not in the file —</option>
      {bom.headers.map((h) => (
        <option key={h} value={h}>
          {h}
        </option>
      ))}
    </select>
  );
  return (
    <div className="bom-map" aria-labelledby={`${uid}-t`} role="group">
      <p id={`${uid}-t`} className="bom-map__title">
        Check the columns of {file.filename} ({bom.row_count} rows; proposed by {bom.mapping_source.replace("+", " and ")})
      </p>
      <div className="bom-map__grid">
        {MAP_FIELDS.map((f) => (
          <label key={f.key} className="bom-map__row">
            <span>{f.label}</span>
            {select(mapping[f.key], f.label, (v) => onChange({ ...mapping, [f.key]: v, confirmed: false }))}
          </label>
        ))}
        {variants.map((v) => (
          <label key={v} className="bom-map__row">
            <span>Quantity for {v}</span>
            {select(mapping.variant_qty[v] ?? null, `Quantity column for ${v}`, (col) => {
              const vq = Object.fromEntries(Object.entries(mapping.variant_qty).filter(([k]) => k !== v));
              if (col) vq[v] = col;
              onChange({ ...mapping, variant_qty: vq, confirmed: false });
            })}
          </label>
        ))}
      </div>
      {noQty && (
        <p className="notice" data-tone="warn" role="status">
          No quantity column is mapped. The BoM stage will ask for each line's quantity.
        </p>
      )}
      {bom.sample.length > 0 && (
        <div className="table-scroll">
          <table className="table small">
            <caption className="muted">First rows of the file</caption>
            <thead>
              <tr>
                {bom.headers.map((h) => (
                  <th key={h} scope="col">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bom.sample.slice(0, 3).map((r, i) => (
                <tr key={i}>
                  {bom.headers.map((h, j) => (
                    <td key={h}>{r[j] ?? ""}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <label className="field--check bom-map__confirm">
        <input type="checkbox" checked={mapping.confirmed} onChange={(e) => onChange({ ...mapping, confirmed: e.target.checked })} />{" "}
        The column mapping is right
      </label>
    </div>
  );
}
