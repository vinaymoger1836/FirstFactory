import { Link } from "react-router";
import { PageTitle } from "../components/AppShell";

const OUTPUTS = [
  ["Product & BoM", "Template in, engineering and manufacturing BoM out, with make/buy."],
  ["Line design", "Routing, quality plan, takt and three line strategies side by side."],
  ["Factory layout", "2D block layout and an interactive 3D factory you can edit."],
  ["Proof & cost", "Simulation against target volume, utilities, finance and vendors."],
] as const;

export function Home() {
  return (
    <>
      <PageTitle>Plan your factory</PageTitle>
      <p className="muted" style={{ fontSize: "var(--text-lg)", maxWidth: "42rem" }}>
        Describe your product. Agents propose each part of the factory, engines compute every
        number, and you approve each step.
      </p>
      <p>
        <Link to="/new" className="btn btn-primary">
          Start a project
        </Link>
      </p>
      <h2 style={{ marginTop: "var(--space-7)" }}>What you get</h2>
      <ul className="grid" style={{ listStyle: "none", padding: 0 }}>
        {OUTPUTS.map(([title, text]) => (
          <li key={title} className="card">
            <h3>{title}</h3>
            <p className="muted" style={{ margin: 0 }}>
              {text}
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}
