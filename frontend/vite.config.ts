import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Offline-first: emit a relative base so the built bundle loads from disk
// inside the pywebview shell (Phase 10), not from a server root.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
  },
});
