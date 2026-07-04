import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  PRODUCTION_EXCLUDED_EXPORT_DIRS,
  copyRecursive,
  runCopyNextExport,
} from "./copy-next-export.mjs";

describe("copy-next-export production mode", () => {
  let tmpRoot = "";

  afterEach(() => {
    if (tmpRoot) {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
      tmpRoot = "";
    }
  });

  it("lists bokeh-playwright-smoke as production-excluded", () => {
    expect(PRODUCTION_EXCLUDED_EXPORT_DIRS).toContain("bokeh-playwright-smoke");
  });

  it("runCopyNextExport omits excluded dirs when productionStatic is true", () => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "copy-next-export-"));
    const outDir = path.join(tmpRoot, "out");
    const targetDir = path.join(tmpRoot, "static");
    fs.mkdirSync(path.join(outDir, "machine"), { recursive: true });
    fs.writeFileSync(path.join(outDir, "machine", "index.html"), "<html></html>");
    fs.mkdirSync(path.join(outDir, "bokeh-playwright-smoke"), { recursive: true });
    fs.writeFileSync(
      path.join(outDir, "bokeh-playwright-smoke", "index.html"),
      "<html>smoke</html>",
    );

    const { mode } = runCopyNextExport({ out: outDir, target: targetDir, productionStatic: true });
    expect(mode).toBe("production");
    expect(fs.existsSync(path.join(targetDir, "machine", "index.html"))).toBe(true);
    expect(fs.existsSync(path.join(targetDir, "bokeh-playwright-smoke"))).toBe(false);
  });

  it("runCopyNextExport includes smoke dir in full mode", () => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "copy-next-export-"));
    const outDir = path.join(tmpRoot, "out");
    const targetDir = path.join(tmpRoot, "static");
    fs.mkdirSync(path.join(outDir, "bokeh-playwright-smoke"), { recursive: true });
    fs.writeFileSync(
      path.join(outDir, "bokeh-playwright-smoke", "index.html"),
      "<html>smoke</html>",
    );

    runCopyNextExport({ out: outDir, target: targetDir, productionStatic: false });
    expect(fs.existsSync(path.join(targetDir, "bokeh-playwright-smoke", "index.html"))).toBe(
      true,
    );
  });

  it("copyRecursive skips only top-level excluded directory names", () => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "copy-next-export-"));
    const src = path.join(tmpRoot, "src");
    const dest = path.join(tmpRoot, "dest");
    fs.mkdirSync(path.join(src, "machine"), { recursive: true });
    fs.mkdirSync(path.join(src, "bokeh-playwright-smoke"), { recursive: true });
    fs.writeFileSync(path.join(src, "machine", "index.html"), "ok");

    copyRecursive(src, dest, { productionStatic: true });
    expect(fs.existsSync(path.join(dest, "machine", "index.html"))).toBe(true);
    expect(fs.existsSync(path.join(dest, "bokeh-playwright-smoke"))).toBe(false);
  });
});
