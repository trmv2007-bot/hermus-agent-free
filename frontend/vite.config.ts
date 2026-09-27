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
    // During `npm run dev` the SPA is served by Vite, not the gateway, so every
    // gateway route the panels read has to be proxied or it 404s in the browser
    // and looks like a dead panel. Keep this list in step with `api` in
    // src/api/client.ts — a route missing here fails as a fetch error, which is
    // indistinguishable from the endpoint being broken.
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/stream": "http://127.0.0.1:8000",
      "/missions": "http://127.0.0.1:8000",
      "/events": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
      "/queue": "http://127.0.0.1:8000",
      "/keys": "http://127.0.0.1:8000",
      "/engine": "http://127.0.0.1:8000",
      "/computer": "http://127.0.0.1:8000",
      "/sandbox": "http://127.0.0.1:8000",
      "/memory2": "http://127.0.0.1:8000",
      "/workspace": "http://127.0.0.1:8000",
      "/agent": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
      "/livez": "http://127.0.0.1:8000",
      "/readyz": "http://127.0.0.1:8000",
      // The diagnostics surface embeds this same document, so in dev it has to
      // come from the gateway too rather than 404 inside the iframe.
      "/control": "http://127.0.0.1:8000",
      "/static/control.css": "http://127.0.0.1:8000",
      "/static/hood.css": "http://127.0.0.1:8000",
      "/static/jarvis-hud.css": "http://127.0.0.1:8000",
      "/static/control-client.js": "http://127.0.0.1:8000",
      "/static/control-room.js": "http://127.0.0.1:8000",
      "/static/console.js": "http://127.0.0.1:8000",
      "/static/hood.js": "http://127.0.0.1:8000",
      "/static/jarvis-hud.js": "http://127.0.0.1:8000",
      "/static/gods-eye.js": "http://127.0.0.1:8000",
      "/favicon.ico": "http://127.0.0.1:8000",
      "/ws": { ws: true, target: "ws://127.0.0.1:8000" },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
