import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { AppShell } from "./components/AppShell";
import { Home } from "./routes/Home";
import { NotFound } from "./routes/NotFound";
import { Pipeline } from "./routes/Pipeline";
import { ProjectView } from "./routes/ProjectView";
import { StageView } from "./routes/StageView";
import { Status } from "./routes/Status";
import { Wizard } from "./routes/Wizard";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/shell.css";
import "./styles/components.css";

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Home /> },
      { path: "new", element: <Wizard /> },
      { path: "p/:token", element: <ProjectView /> },
      { path: "p/:token/stage/:stage", element: <StageView /> },
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
