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

  it("emits nginx CSP includes with Bokeh style unsafe-inline and hashed scripts", async () => {
    const {
      buildNginxCspInclude,
      collectInlineCspHashes,
      sha256CspHash,
    } = await import("./copy-next-export.mjs");

    tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "copy-next-export-"));
    const outDir = path.join(tmpRoot, "out");
    const targetDir = path.join(tmpRoot, "static");
    const scriptBody = "window.__TEST__=1;";
    const styleBody = "body{color:red}";
    const styleAttr = "display:none";
    fs.mkdirSync(path.join(outDir, "machine"), { recursive: true });
    fs.mkdirSync(path.join(outDir, "pub"), { recursive: true });
    fs.writeFileSync(
      path.join(outDir, "machine", "index.html"),
      `<html><head><style>${styleBody}</style></head>` +
        `<body style="${styleAttr}"><script>${scriptBody}</script></body></html>`,
    );
    fs.writeFileSync(
      path.join(outDir, "pub", "index.html"),
      `<html><body><script>${scriptBody}</script></body></html>`,
    );

    runCopyNextExport({
      out: outDir,
      target: targetDir,
      productionStatic: true,
      edgeNginxDir: path.join(tmpRoot, "edge_nginx"),
    });

    expect(fs.existsSync(path.join(targetDir, "nginx-csp-machine.inc"))).toBe(false);
    expect(fs.existsSync(path.join(targetDir, "nginx-csp-pub.inc"))).toBe(false);

    const machineHtml = fs.readFileSync(
      path.join(targetDir, "machine", "index.html"),
      "utf8",
    );
    expect(machineHtml).toContain('http-equiv="Content-Security-Policy"');
    expect(machineHtml).toContain(sha256CspHash(scriptBody));
    expect(machineHtml).toContain("unsafe-eval");
    expect(machineHtml).toContain("style-src 'self' 'unsafe-inline'");
    expect(machineHtml).not.toContain(sha256CspHash(styleBody));

    const pubHtml = fs.readFileSync(path.join(targetDir, "pub", "index.html"), "utf8");
    expect(pubHtml).toContain("style-src 'self' 'unsafe-inline'");
    expect(pubHtml).toContain("unsafe-eval");

    const machineInc = fs.readFileSync(
      path.join(tmpRoot, "edge_nginx", "nginx-csp-machine.inc"),
      "utf8",
    );
    const pubInc = fs.readFileSync(
      path.join(tmpRoot, "edge_nginx", "nginx-csp-pub.inc"),
      "utf8",
    );
    expect(machineInc).toContain("Content-Security-Policy");
    expect(machineInc).toContain("style-src 'self' 'unsafe-inline'");
    expect(machineInc).toContain("unsafe-eval");
    expect(pubInc).toContain("style-src 'self' 'unsafe-inline'");
    expect(pubInc).toContain("unsafe-eval");

    const scriptHash = sha256CspHash(scriptBody);
    const styleHash = sha256CspHash(styleBody);
    expect(machineInc).toContain(scriptHash);
    expect(machineInc).not.toContain(styleHash);
    expect(machineInc).not.toContain("'unsafe-hashes'");
    expect(pubInc).toContain(scriptHash);

    // script-src must never allow unsafe-inline
    const machineScriptSrc = machineInc.split("script-src ")[1].split(";")[0];
    expect(machineScriptSrc).not.toContain("unsafe-inline");
    const pubScriptSrc = pubInc.split("script-src ")[1].split(";")[0];
    expect(pubScriptSrc).not.toContain("unsafe-inline");
    expect(pubScriptSrc).toContain("unsafe-eval");
    const recomputed = collectInlineCspHashes(path.join(targetDir, "machine"));
    expect(recomputed.scriptHashes).toContain(scriptHash);
    expect(recomputed.styleHashes).toContain(styleHash);

    const rebuilt = buildNginxCspInclude({
      scriptHashes: recomputed.scriptHashes,
      styleHashes: recomputed.styleHashes,
      styleAttrHashes: recomputed.styleAttrHashes,
      allowUnsafeEval: true,
      allowBokehStyleInline: true,
    });
    expect(rebuilt).toBe(machineInc);
  });
});
