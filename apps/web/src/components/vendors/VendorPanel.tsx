import { useMemo, useState } from "react";
import type { RunOut } from "../../lib/api";
import type { VendorArtifact, VendorClassT, VendorT } from "../../lib/stageTypes";
import "../ui/ui.css";
import "./vendors.css";

const KIND_LABEL: Record<string, string> = {
  maker: "Maker's own site",
  directory: "Listing site",
  other: "Other site",
};

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "");
const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

export function VendorPanel({ run }: { run: RunOut }) {
  const raw = run.artifact as unknown as Partial<VendorArtifact>;
  const classes = useMemo(() => raw.classes ?? [], [raw]);
  const [q, setQ] = useState("");
  const total = classes.reduce((n, c) => n + c.vendors.length, 0);
  const needle = q.trim().toLowerCase();
  const shown = classes
    .map((c) => ({
      ...c,
      vendors: needle
        ? c.vendors.filter((v) => [v.name, v.city, v.state, ...v.products, ...v.certifications].join(" ").toLowerCase().includes(needle))
        : c.vendors,
    }))
    .filter((c) => !needle || c.vendors.length > 0);

  return (
    <section className="card vendors" aria-label="Vendor leads">
      <div className="ui-note" role="note">
        <span>
          <strong>Leads, not endorsements.</strong> {raw.disclaimer ?? "Verify before purchase."} Every vendor below links to the public page it was found on, and the quoted text
          is checked against that page. If nothing was found, a class shows a gap instead.
        </span>
      </div>
      <ul className="ui-stats" aria-label="Vendor research at a glance">
        <li className="ui-stat">
          <span className="ui-stat__v">{total}</span>
          <span className="ui-stat__l">vendor leads with sources</span>
        </li>
        <li className="ui-stat">
          <span className="ui-stat__v">{classes.length}</span>
          <span className="ui-stat__l">bought classes searched</span>
        </li>
        <li className="ui-stat">
          <span className="ui-stat__v">{classes.filter((c) => c.cached_at).length}</span>
          <span className="ui-stat__l">from the shared 30-day cache</span>
        </li>
      </ul>
      {(raw.notes ?? []).length > 0 && (
        <ul className="ui-list">
          {(raw.notes ?? []).map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {(raw.skipped_classes ?? []).length > 0 && (
        <p className="ui-muted small">Not searched (limit per run): {(raw.skipped_classes ?? []).join(", ").replace(/_/g, " ")}.</p>
      )}
      <label className="inline-field vendors__search">
        <span>Filter vendors</span>
        <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, city, product or certification" />
      </label>
      {shown.length === 0 && <p>No vendor matches this filter.</p>}
      {shown.map((c) => (
        <ClassCard key={c.vendor_class} c={c} />
      ))}
    </section>
  );
}

function ClassCard({ c }: { c: VendorClassT }) {
  return (
    <section className="vendors__class" aria-labelledby={`vc-${c.vendor_class}`}>
      <h3 id={`vc-${c.vendor_class}`} className="vendors__title">
        {c.name || c.vendor_class.replace(/_/g, " ")}
        <span className="ui-chip">{c.vendors.length} lead{c.vendors.length === 1 ? "" : "s"}</span>
        {c.cached_at && (
          <span className="ui-chip" data-tone="run">
            Cached {when(c.cached_at)}
          </span>
        )}
      </h3>
      {c.vendors.length === 0 && <p className="ui-muted">No vendor with evidence yet.</p>}
      <ul className="vendors__list">
        {c.vendors.map((v) => (
          <VendorRow key={`${v.name}-${v.domain}`} v={v} />
        ))}
      </ul>
      {c.gaps.length > 0 && (
        <details className="vendors__gaps">
          <summary>
            {c.gaps.length} gap{c.gaps.length === 1 ? "" : "s"} in this search
          </summary>
          <ul>
            {c.gaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </details>
      )}
      <p className="ui-muted small">
        {c.queries_run.length > 0 && <>Searched: “{c.queries_run[0]}”. </>}
        {c.pages_read ? `${c.pages_read} page(s) read` : ""}
        {c.pages_dropped ? `, ${c.pages_dropped} gave no vendor with evidence` : ""}.
      </p>
    </section>
  );
}

function VendorRow({ v }: { v: VendorT }) {
  const place = [v.city, v.state].filter(Boolean).join(", ");
  return (
    <li className="vendors__item">
      <div className="vendors__head">
        <strong>{v.name}</strong>
        <span className="vendors__score" role="img" aria-label={`Score ${Math.round(v.score)} out of 100`} title={v.score_parts ? Object.entries(v.score_parts).map(([k, x]) => `${k} ${x}`).join(", ") : undefined}>
          <span className="vendors__bar" aria-hidden="true">
            <span style={{ transform: `scaleX(${Math.min(1, v.score / 100)})` }} />
          </span>
          {Math.round(v.score)}
        </span>
      </div>
      <dl className="ui-dl vendors__facts">
        {place && (
          <>
            <dt>Where</dt>
            <dd>{place}</dd>
          </>
        )}
        {v.products.length > 0 && (
          <>
            <dt>Makes or sells</dt>
            <dd>{v.products.join("; ")}</dd>
          </>
        )}
        {v.certifications.length > 0 && (
          <>
            <dt>Certifications claimed</dt>
            <dd>{v.certifications.join("; ")}</dd>
          </>
        )}
        {v.moq && (
          <>
            <dt>Minimum order</dt>
            <dd>{v.moq}</dd>
          </>
        )}
        {v.indicative_price && (
          <>
            <dt>Indicative price</dt>
            <dd>{v.indicative_price}</dd>
          </>
        )}
        {v.lead_time && (
          <>
            <dt>Lead time</dt>
            <dd>{v.lead_time}</dd>
          </>
        )}
      </dl>
      <details className="vendors__evidence">
        <summary>
          Source{v.evidence.length === 1 ? "" : "s"} ({v.evidence.length})
        </summary>
        <ul>
          {v.evidence.map((e) => (
            <li key={e.url}>
              <a href={e.url} target="_blank" rel="noopener noreferrer">
                {host(e.url)}
                <span className="visually-hidden"> (opens in a new tab)</span>
              </a>{" "}
              <span className="ui-chip">{KIND_LABEL[e.source_kind] ?? e.source_kind}</span> <span className="ui-muted small">read {when(e.fetched_at)}</span>
              <blockquote>{e.snippet}</blockquote>
            </li>
          ))}
        </ul>
        {v.contact_url && (
          <p className="small">
            Contact page:{" "}
            <a href={v.contact_url} target="_blank" rel="noopener noreferrer">
              {host(v.contact_url)}
              <span className="visually-hidden"> (opens in a new tab)</span>
            </a>
          </p>
        )}
      </details>
    </li>
  );
}
