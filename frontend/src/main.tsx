import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

// Styles: fonts first (local woff2, offline-first), then the palette tokens,
// then the shell. Vite bundles all of it — nothing loads from a CDN.
import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/app.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
