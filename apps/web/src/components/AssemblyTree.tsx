import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { AssemblyNodeOut } from "../lib/api";

interface Flat {
  node: AssemblyNodeOut;
  level: number;
  parent: string | null;
}

/** WAI-ARIA tree view of a CAD assembly: arrow keys move, Right/Left expand/collapse. */
export function AssemblyTree({ root, label }: { root: AssemblyNodeOut; label: string }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([root.id]));
  const [active, setActive] = useState(root.id);
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  const visible = useMemo(() => {
    const out: Flat[] = [];
    const walk = (n: AssemblyNodeOut, level: number, parent: string | null) => {
      out.push({ node: n, level, parent });
      if (expanded.has(n.id)) n.children.forEach((c) => walk(c, level + 1, n.id));
    };
    walk(root, 1, null);
    return out;
  }, [root, expanded]);

  function focus(id: string) {
    setActive(id);
    refs.current[id]?.focus();
  }

  function onKey(e: KeyboardEvent, f: Flat) {
    const i = visible.findIndex((v) => v.node.id === f.node.id);
    const hasKids = f.node.children.length > 0;
    const open = expanded.has(f.node.id);
    const toggle = (on: boolean) =>
      setExpanded((s) => {
        const n = new Set(s);
        if (on) n.add(f.node.id);
        else n.delete(f.node.id);
        return n;
      });
    switch (e.key) {
      case "ArrowDown": {
        const nxt = visible[i + 1];
        if (nxt) focus(nxt.node.id);
        break;
      }
      case "ArrowUp": {
        const prev = visible[i - 1];
        if (prev) focus(prev.node.id);
        break;
      }
      case "ArrowRight":
        if (hasKids && !open) toggle(true);
        else if (hasKids && f.node.children[0]) focus(f.node.children[0].id);
        break;
      case "ArrowLeft":
        if (hasKids && open) toggle(false);
        else if (f.parent) focus(f.parent);
        break;
      case "Home":
        focus(root.id);
        break;
      case "End": {
        const last = visible[visible.length - 1];
        if (last) focus(last.node.id);
        break;
      }
      case "Enter":
      case " ":
        if (hasKids) toggle(!open);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  return (
    <div role="tree" aria-label={label} className="tree">
      {visible.map((f) => {
        const hasKids = f.node.children.length > 0;
        return (
          <div
            key={f.node.id}
            ref={(el) => {
              refs.current[f.node.id] = el;
            }}
            role="treeitem"
            aria-level={f.level}
            aria-expanded={hasKids ? expanded.has(f.node.id) : undefined}
            aria-selected={active === f.node.id}
            tabIndex={active === f.node.id ? 0 : -1}
            className="tree__item"
            style={{ paddingLeft: `${(f.level - 1) * 1.25 + 0.5}rem` }}
            onKeyDown={(e) => onKey(e, f)}
            onClick={() => {
              setActive(f.node.id);
              if (hasKids)
                setExpanded((s) => {
                  const n = new Set(s);
                  if (n.has(f.node.id)) n.delete(f.node.id);
                  else n.add(f.node.id);
                  return n;
                });
            }}
          >
            <span className="tree__twisty" aria-hidden="true">
              {hasKids ? (expanded.has(f.node.id) ? "▾" : "▸") : "•"}
            </span>
            {f.node.name}
            {f.node.part_no && <span className="muted small"> · {f.node.part_no}</span>}
          </div>
        );
      })}
    </div>
  );
}
