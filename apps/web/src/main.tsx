import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AppShell } from "./components/AppShell";
import { Home } from "./routes/Home";
import { NewProject } from "./routes/NewProject";
import { NotFound } from "./routes/NotFound";
import { Pipeline } from "./routes/Pipeline";
import { ProjectView } from "./routes/ProjectView";
import { Status } from "./routes/Status";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Home /> },
      { path: "new", element: <NewProject /> },
      { path: "p/:token", element: <ProjectView /> },
      { path: "pipeline", element: <Pipeline /> },
      { path: "status", element: <Status /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
