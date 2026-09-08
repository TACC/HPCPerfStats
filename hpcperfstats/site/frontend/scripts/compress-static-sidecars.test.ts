import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { afterEach, describe, expect, it } from "vitest";
import {
  SIDECAR_MIN_BYTES,
  compressStaticSidecars,
  shouldWriteStaticSidecars,
} from "./compress-static-sidecars.mjs";
import { runCopyNextExport } from "./copy-next-export.mjs";

describe("compress-static-sidecars", () => {
  let tmpRoot = "";

  afterEach(() => {
    if (tmpRoot) {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
      tmpRoot = "";
    }
  });

  it("compresses JS/CSS and skips SPA HTML, maps, binaries, and tiny files", () => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "sidecars-"));
    const root = path.join(tmpRoot, "frontend");
    const jsRel = path.join("_next", "static", "chunks", "app.js");
    const cssRel = path.join("_next", "static", "css", "app.css");
    const jsBody = `${"export const x = 1;\n".repeat(40)}// pad\n`;
    const cssBody = `${"body { color: red; }\n".repeat(40)}`;
    expect(jsBody.length).toBeGreaterThan(SIDECAR_MIN_BYTES);
    fs.mkdirSync(path.join(root, path.dirname(jsRel)), { recursive: true });
    fs.mkdirSync(path.join(root, path.dirname(cssRel)), { recursive: true });
    fs.mkdirSync(path.join(root, "machine"), { recursive: true });
    fs.mkdirSync(path.join(root, "pub"), { recursive: true });
    fs.writeFileSync(path.join(root, jsRel), jsBody);
    fs.writeFileSync(path.join(root, cssRel), cssBody);
    fs.writeFileSync(path.join(root, "machine", "index.html"), `<html>${"x".repeat(300)}</html>`);
    fs.writeFileSync(path.join(root, "pub", "index.html"), `<html>${"y".repeat(300)}</html>`);
    fs.writeFileSync(path.join(root, jsRel + ".map"), `${"{}".repeat(80)}`);
    fs.writeFileSync(path.join(root, "tiny.js"), "x=1");
    fs.writeFileSync(path.join(root, "icon.png"), Buffer.alloc(512, 1));

    const { written } = compressStaticSidecars(root);
    expect(written).toBe(2);

    const jsBr = fs.readFileSync(path.join(root, `${jsRel}.br`));
    const jsGz = fs.readFileSync(path.join(root, `${jsRel}.gz`));
    expect(zlib.brotliDecompressSync(jsBr).toString("utf8")).toBe(jsBody);
    expect(zlib.gunzipSync(jsGz).toString("utf8")).toBe(jsBody);
    expect(fs.existsSync(path.join(root, `${cssRel}.br`))).toBe(true);
    expect(fs.existsSync(path.join(root, `${cssRel}.gz`))).toBe(true);
    expect(fs.existsSync(path.join(root, "machine", "index.html.br"))).toBe(false);
    expect(fs.existsSync(path.join(root, "pub", "index.html.gz"))).toBe(false);
    expect(fs.existsSync(path.join(root, `${jsRel}.map.br`))).toBe(false);
    expect(fs.existsSync(path.join(root, "tiny.js.br"))).toBe(false);
    expect(fs.existsSync(path.join(root, "icon.png.br"))).toBe(false);
    expect(fs.readFileSync(path.join(root, jsRel), "utf8")).toBe(jsBody);
  });

  it("shouldWriteStaticSidecars rejects maps, html shells, and sidecars", () => {
    expect(shouldWriteStaticSidecars("_next/app.js", 512)).toBe(true);
    expect(shouldWriteStaticSidecars("machine/index.html", 512)).toBe(false);
    expect(shouldWriteStaticSidecars("pub/jobs/index.html", 512)).toBe(false);
    expect(shouldWriteStaticSidecars("_next/app.js.map", 512)).toBe(false);
    expect(shouldWriteStaticSidecars("_next/app.js.br", 512)).toBe(false);
    expect(shouldWriteStaticSidecars("tiny.js", 10)).toBe(false);
    expect(shouldWriteStaticSidecars("font.woff2", 512)).toBe(false);
  });

  it("runCopyNextExport writes sidecars after CSP inject", () => {
    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "copy-sidecars-"));
    const outDir = path.join(tmpRoot, "out");
    const targetDir = path.join(tmpRoot, "static");
    const jsBody = `${"export const n = 2;\n".repeat(40)}`;
    fs.mkdirSync(path.join(outDir, "machine"), { recursive: true });
    fs.mkdirSync(path.join(outDir, "_next", "static"), { recursive: true });
    fs.writeFileSync(path.join(outDir, "machine", "index.html"), "<html><body></body></html>");
    fs.writeFileSync(path.join(outDir, "_next", "static", "chunk.js"), jsBody);

    runCopyNextExport({
      out: outDir,
      target: targetDir,
      productionStatic: true,
      edgeNginxDir: path.join(tmpRoot, "edge_nginx"),
    });

    expect(fs.existsSync(path.join(targetDir, "_next", "static", "chunk.js.br"))).toBe(true);
    expect(fs.existsSync(path.join(targetDir, "_next", "static", "chunk.js.gz"))).toBe(true);
    expect(fs.existsSync(path.join(targetDir, "machine", "index.html.br"))).toBe(false);
  });
});
