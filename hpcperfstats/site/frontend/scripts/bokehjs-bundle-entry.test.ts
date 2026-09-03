import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(__dirname, "..");

/** Bare paths emitted by ``@bokeh/bokehjs@3.10+`` package ``main`` entry. */
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

  it("re-exports every bare package-main target via package subpaths", () => {
    const bundlePath = path.join(frontendRoot, "src/bokehjs-bundle.ts");
    const bundle = fs.readFileSync(bundlePath, "utf8");
    for (const bare of REQUIRED_BARE_IMPORTS) {
      expect(bundle).toContain(
        `export * from "@bokeh/bokehjs/build/js/lib/${bare}"`,
      );
      const abs = path.join(
        path.dirname(require.resolve("@bokeh/bokehjs/package.json")),
        "build/js/lib",
        `${bare}.js`,
      );
      expect(fs.existsSync(abs), `missing ${abs}`).toBe(true);
    }
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
    expect(init).toContain('import("./bokehjs-bundle")');
    expect(init).not.toContain('import("@bokeh/bokehjs")');
    expect(smoke).toContain('import("@/bokehjs-bundle")');
    expect(smoke).not.toContain('import("@bokeh/bokehjs")');
  });
});
