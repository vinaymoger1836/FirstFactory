import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { BrandMark } from "./Icons";
import { MainNav } from "./MainNav";
import { ThemeSwitch } from "./ThemeSwitch";

export function AppShell() {
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const prevPath = useRef(location.pathname);
  const [announcement, setAnnouncement] = useState("");

  // On client-side navigation, move focus to the new page and announce its title.
  // (Compares paths rather than a "first render" flag, which StrictMode's double effects defeat.)
  useEffect(() => {
    if (prevPath.current === location.pathname) return;
    prevPath.current = location.pathname;
    const id = requestAnimationFrame(() => {
      const h1 = mainRef.current?.querySelector("h1");
      (h1 instanceof HTMLElement ? h1 : mainRef.current)?.focus();
      setAnnouncement(document.title);
    });
    return () => cancelAnimationFrame(id);
  }, [location.pathname]);

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="site-header">
        <div className="site-header__inner">
          <Link to="/" className="brand" aria-label="Factory Planner, home">
            <BrandMark className="brand__mark" />
            <span>Factory Planner</span>
          </Link>
          <MainNav />
          <ThemeSwitch />
        </div>
      </header>
      <main id="main" ref={mainRef} className="site-main" tabIndex={-1}>
        <div className="page" key={location.pathname}>
          <Outlet />
        </div>
      </main>
      <footer className="site-footer">
        Planning estimates only, not engineering certification, legal or financial advice.
      </footer>
      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
    </>
  );
}

/** Sets document.title and gives the page h1 a programmatic focus target. */
export function PageTitle({ children }: { children: string }) {
  useEffect(() => {
    document.title = `${children} · Factory Planner`;
  }, [children]);
  return <h1 tabIndex={-1}>{children}</h1>;
}
