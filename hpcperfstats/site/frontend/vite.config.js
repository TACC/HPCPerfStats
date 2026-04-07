import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Rolldown warns on direct `eval`; indirect `(0, eval)(...)` keeps semantics but satisfies the checker.
 * - Bokeh CustomJS: dynamic `import()` workaround (microsoft/TypeScript#43329).
 * - SlickGrid (Bokeh bundle): debug helper `this.eval`.
 */
function bokehDependencyIndirectEvalPlugin() {
  const customJsDynamicImport = /await eval\(`import\("\$\{url\}"\)`\)/;
  return {
    name: "bokeh-dependency-indirect-eval",
    enforce: "pre",
    transform(code, id) {
      if (!id.includes("node_modules") || !id.includes("@bokeh/")) {
        return null;
      }
      let next = code;
      let changed = false;
      if (id.includes("bokehjs") && id.includes("customjs.js")) {
        if (customJsDynamicImport.test(next)) {
          next = next.replace(
            customJsDynamicImport,
            'await (0, eval)(`import("${url}")`)',
          );
          changed = true;
        }
      }
      if (id.includes("slickgrid") && id.includes("slick.grid.js")) {
        if (next.includes("return eval(expr);")) {
          next = next.replace("return eval(expr);", "return (0, eval)(expr);");
          changed = true;
        }
      }
      if (!changed) {
        return null;
      }
      return { code: next, map: null };
    },
  };
}

export default defineConfig({
  plugins: [bokehDependencyIndirectEvalPlugin(), react()],
  root: ".",
  base: "/static/frontend/",
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.js"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{js,jsx}"],
      exclude: [
        "src/**/*.test.{js,jsx}",
        "src/**/setupTests.js",
        "src/main.jsx",
        "src/utils/generate-variable-metadata-monitor-events.py",
      ],
    },
  },
  build: {
    outDir: "../hpcperfstats_site/static/frontend",
    emptyOutDir: true,
    manifest: true,
    sourcemap: true,
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
