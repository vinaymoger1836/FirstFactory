import { SOURCE_LABEL, type ProvLike } from "../lib/prov";
import { Tip } from "./Tip";

/** Source and confidence of a value; details appear on hover or focus (ADR-016 provenance badges). */
export function ProvBadge({ p }: { p: ProvLike }) {
  const low = p.confidence < 0.6;
  const pct = Math.round(p.confidence * 100);
  const source = SOURCE_LABEL[p.source] ?? p.source;
  const detail = (
    <>
      From {source}, confidence {pct}%{low ? " (low, please check)" : ""}.
      {p.ref && (
        <>
          {" "}
          Evidence: <code>{p.ref}</code>.
        </>
      )}
      {p.note && <> {p.note}</>}
    </>
  );
  return (
    <span className="prov">
      <Tip content={detail}>
        {(t) => (
          <button type="button" className="prov__badge" data-source={p.source} data-low={low} {...t}>
            <span>{source}</span>
            <span className="prov__meter" aria-hidden="true">
              <span style={{ transform: `scaleX(${p.confidence})` }} />
            </span>
            <span className="visually-hidden">
              , confidence {pct}%{low ? ", low: please check" : ""}
            </span>
          </button>
        )}
      </Tip>
    </span>
  );
}
