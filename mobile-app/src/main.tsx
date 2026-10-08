import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import "./styles.css";
import { router } from "./app/router.tsx";
import { registerServiceWorker } from "./pwa.ts";

registerServiceWorker();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
