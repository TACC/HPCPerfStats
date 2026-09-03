import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(__dirname, "..");

/** Bare paths emitted by ``@bokeh/bokehjs@3.10+`` package ``main`` (lib) entry. */
const REQUIRED_BARE_IMPORTS = [
  "main",
  "api/main",
  "models/glyphs/webgl/main",
  "models/widgets/main",
  "models/widgets/tables/main",
  "models/text/mathjax/main",
] as const;

describe("bokehjs-bundle Turbopack entry (Bokeh 3.10+)", () => {
  it("documents that package main still uses bare internal imports", () => {
    const bokehJs = path.join(
      path.dirname(require.resolve("@bokeh/bokehjs/package.json")),
      "build/js/lib/bokeh.js",
    );
    const src = fs.readFileSync(bokehJs, "utf8");
    for (const bare of REQUIRED_BARE_IMPORTS) {
      expect(src).toContain(`from "${bare}"`);
      expect(src).not.toContain(`from "./${bare}"`);
    }
  });

  it("loads unprocessed UMD vendor script (not Turbopack-bundled lib/ESM)", () => {
    const bundlePath = path.join(frontendRoot, "src/bokehjs-bundle.ts");
    const bundle = fs.readFileSync(bundlePath, "utf8");
    // Lib/ESM import paths let Turbopack rewrite the graph and drop registration
    // (regression: could not resolve type Grid / DocumentConfig).
    expect(bundle).not.toContain(
      'export * from "@bokeh/bokehjs/build/js/lib/main"',
    );
    expect(bundle).not.toMatch(
      /import\s+.*['"]@bokeh\/bokehjs\/build\/js\/bokeh\.esm\.min\.js['"]/,
    );
    expect(bundle).not.toMatch(
      /from\s+['"]@bokeh\/bokehjs\/build\/js\/bokeh\.esm\.min\.js['"]/,
    );
    expect(bundle).toContain('BOKEH_VENDOR_SCRIPT_PATH = BOKEH_VENDOR_SCRIPT_CANDIDATES[0]');
    expect(bundle).toContain('"/static/frontend/vendor/bokeh.min.js"');
    expect(bundle).toContain('"/vendor/bokeh.min.js"');
    expect(bundle).toContain("export function loadBokehRuntime");
    expect(bundle).toContain("createElement(\"script\")");

    const syncScript = path.join(frontendRoot, "scripts/sync-bokeh-vendor.mjs");
    expect(fs.existsSync(syncScript)).toBe(true);
    const syncSrc = fs.readFileSync(syncScript, "utf8");
    expect(syncSrc).toContain("build/js/bokeh.min.js");
    expect(syncSrc).toContain("public/vendor");

    const umd = path.join(
      path.dirname(require.resolve("@bokeh/bokehjs/package.json")),
      "build/js/bokeh.min.js",
    );
    expect(fs.existsSync(umd), `missing ${umd}`).toBe(true);
  });

  it("loads Bokeh through bokehjs-bundle from bokehInit and smoke page", () => {
    const init = fs.readFileSync(
      path.join(frontendRoot, "src/bokehInit.ts"),
      "utf8",
    );
    const smoke = fs.readFileSync(
      path.join(frontendRoot, "app/bokeh-playwright-smoke/page.tsx"),
      "utf8",
    );
    const identity = fs.readFileSync(
      path.join(frontendRoot, "scripts/write-site-identity.mjs"),
      "utf8",
    );
    expect(init).toContain('import("./bokehjs-bundle")');
    expect(init).toContain("loadBokehRuntime");
    expect(init).not.toContain('import("@bokeh/bokehjs")');
    expect(smoke).toContain('import("@/bokehjs-bundle")');
    expect(smoke).toContain("loadBokehRuntime");
    expect(smoke).not.toContain('import("@bokeh/bokehjs")');
    expect(identity).toContain('import("./sync-bokeh-vendor.mjs")');
  });
});
