import { useId, useState } from "react";
import { SOURCE_LABEL, type ProvLike } from "../lib/prov";

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
