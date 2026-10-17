import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In ontwikkeling proxyt Vite de backend, zodat cookies first-party blijven
// (zelfde situatie als in productie achter nginx/NPM).
const backend = process.env.VAULTX_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/auth": backend,
      "/health": backend,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
