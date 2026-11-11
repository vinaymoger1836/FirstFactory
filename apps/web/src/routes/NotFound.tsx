import { Link } from "react-router";
import { PageTitle } from "../components/AppShell";

export function NotFound() {
  return (
    <>
      <PageTitle>Page not found</PageTitle>
      <p>
        <Link to="/">Go to the home page</Link>
      </p>
    </>
  );
}
