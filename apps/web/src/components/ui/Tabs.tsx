import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import "./ui.css";

export interface TabDef {
  key: string;
  label: string;
  badge?: string | number;
}

/** Accessible tabs: roving tabindex, arrow keys, Home/End, panel linked to its tab. */
export function Tabs({
  label,
  tabs,
  initial,
  children,
}: {
  label: string;
  tabs: TabDef[];
  initial?: string;
  children: (active: string) => ReactNode;
}) {
  const [active, setActive] = useState(initial && tabs.some((t) => t.key === initial) ? initial : tabs[0]?.key ?? "");
  const uid = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  function onKey(e: KeyboardEvent, i: number) {
    let to = -1;
    if (e.key === "ArrowRight") to = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") to = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = tabs.length - 1;
    if (to < 0) return;
    e.preventDefault();
    const next = tabs[to];
    if (next) {
      setActive(next.key);
      refs.current[next.key]?.focus();
    }
  }

  return (
    <>
      <div role="tablist" aria-label={label} className="ui-tabs">
        {tabs.map((t, i) => (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[t.key] = el;
            }}
            role="tab"
            type="button"
            id={`${uid}-tab-${t.key}`}
            aria-selected={active === t.key}
            aria-controls={`${uid}-panel-${t.key}`}
            tabIndex={active === t.key ? 0 : -1}
            className="ui-tabs__tab"
            onClick={() => setActive(t.key)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {t.label}
            {t.badge !== undefined && t.badge !== 0 && <span className="ui-tabs__badge">{t.badge}</span>}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${uid}-panel-${active}`} aria-labelledby={`${uid}-tab-${active}`} className="ui-tabs__panel" key={active}>
        {children(active)}
      </div>
    </>
  );
}
