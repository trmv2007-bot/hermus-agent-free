import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The bundle is emitted straight into the directory the gateway already serves
// assets from, so the Python process needs no new mount point and no second
// static root. `emptyOutDir` is deliberate: a stale chunk referenced by a new
// index.html would 404 in the browser and read as an app bug.
export default defineConfig({
  base: "/static/workspace/",
  plugins: [react()],
  build: {
    outDir: "../gateway/static/workspace",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        // Stable, extension-only names keep the gateway's asset route list
        // short: the surface files are hashed by the SPA runtime, not the server.
        entryFileNames: "assets/workspace.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/stream": "http://127.0.0.1:8000",
      "/missions": "http://127.0.0.1:8000",
      "/events": "http://127.0.0.1:8000",
      "/ws": { ws: true, target: "ws://127.0.0.1:8000" },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
