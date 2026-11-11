import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router";
import { IconClose, IconFlow, IconHome, IconMenu, IconPlus, IconPulse } from "./Icons";

const ITEMS = [
  { to: "/", label: "Home", Icon: IconHome, end: true },
  { to: "/new", label: "New project", Icon: IconPlus, end: false },
  { to: "/pipeline", label: "Pipeline", Icon: IconFlow, end: false },
  { to: "/status", label: "System status", Icon: IconPulse, end: false },
] as const;

/**
 * Main menu: a plain list of links in a labelled <nav>. NavLink sets aria-current="page".
 * On small screens it collapses behind a disclosure button (aria-expanded/aria-controls);
 * Escape closes it and returns focus to the button.
 */
export function MainNav() {
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const location = useLocation();

  useEffect(() => setOpen(false), [location.pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        ref={toggleRef}
        type="button"
        className="btn menu-toggle"
        aria-expanded={open}
        aria-controls="main-menu"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? <IconClose className="main-nav__icon" /> : <IconMenu className="main-nav__icon" />}
        Menu
      </button>
      <nav id="main-menu" className="main-nav" aria-label="Main" data-open={open}>
        <ul className="main-nav__list">
          {ITEMS.map(({ to, label, Icon, end }) => (
            <li key={to}>
              <NavLink to={to} end={end} className="main-nav__link">
                <Icon className="main-nav__icon" />
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </>
  );
}
