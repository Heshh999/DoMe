import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";
import { PROTOCOL_VERSION, registry } from "@dome/protocol";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <p>DoMe {PROTOCOL_VERSION} {registry.actions().size}</p>
  </StrictMode>,
);
