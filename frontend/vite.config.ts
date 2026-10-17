import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In ontwikkeling proxyt Vite de backend, zodat cookies first-party blijven
// (zelfde situatie als in productie achter nginx/NPM).
const backend = process.env.VAULTX_BACKEND_URL ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": backend,
  "/auth": backend,
  "/health": backend,
  "/identity": backend,
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy,
  },
  // `vite preview` (o.a. de e2e-tests) met dezelfde Content-Security-Policy als nginx in
  // productie, zodat een bibliotheek die inline stijlen of eval nodig heeft daar al faalt.
  preview: {
    proxy,
    headers: {
      "Content-Security-Policy":
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
