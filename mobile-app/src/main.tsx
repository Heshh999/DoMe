import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import "./styles.css";
import { router } from "./app/router.tsx";
import { registerServiceWorker } from "./pwa.ts";

async function boot(): Promise<void> {
  // Development only: the design preview (/preview → the real app on a fake account, PC and butler; see
  // src/dev/preview.ts). `import.meta.env.DEV` is `false` in `vite build`, so this branch and the module
  // it loads are dropped from the production bundle (scripts/check-bundle.ts verifies it on every build).
  if (import.meta.env.DEV) await (await import("./dev/preview.ts")).startPreviewIfAsked();

  registerServiceWorker();

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
}

void boot();
