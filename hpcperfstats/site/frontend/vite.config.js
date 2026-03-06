import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: ".",
  base: "/static/frontend/",
  build: {
    outDir: "../hpcperfstats_site/static/frontend",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: "index.html",
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/login": "http://127.0.0.1:8000",
      "/login_prompt": "http://127.0.0.1:8000",
      "/logout": "http://127.0.0.1:8000",
      "/oauth_callback": "http://127.0.0.1:8000",
      "/machine": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
    },
  },
});
