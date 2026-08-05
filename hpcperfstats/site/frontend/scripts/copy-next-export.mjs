import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Next static export dirs omitted from production deploy (Playwright / test-only routes). */
export const PRODUCTION_EXCLUDED_EXPORT_DIRS = ["bokeh-playwright-smoke"];

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const outDir = path.join(frontendRoot, "out");
const targetDir = path.resolve(frontendRoot, "../hpcperfstats_site/static/frontend");

const INLINE_SCRIPT_RE = /<script\b(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)<\/script>/gi;
const INLINE_STYLE_RE = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
const STYLE_ATTR_RE = /\sstyle\s*=\s*(["'])([\s\S]*?)\1/gi;

/**
 * @param {string} content
 * @returns {string}
 */
export function sha256CspHash(content) {
  const digest = crypto.createHash("sha256").update(content, "utf8").digest("base64");
  return `'sha256-${digest}'`;
}

/**
 * @param {string} html
 * @returns {{ scriptHashes: string[], styleHashes: string[], styleAttrHashes: string[] }}
 */
export function extractInlineCspHashesFromHtml(html) {
  const scriptHashes = new Set();
  const styleHashes = new Set();
  const styleAttrHashes = new Set();

  for (const match of html.matchAll(INLINE_SCRIPT_RE)) {
    scriptHashes.add(sha256CspHash(match[1]));
  }
  for (const match of html.matchAll(INLINE_STYLE_RE)) {
    styleHashes.add(sha256CspHash(match[1]));
  }
  for (const match of html.matchAll(STYLE_ATTR_RE)) {
    styleAttrHashes.add(sha256CspHash(match[2]));
  }

  return {
    scriptHashes: [...scriptHashes].sort(),
    styleHashes: [...styleHashes].sort(),
    styleAttrHashes: [...styleAttrHashes].sort(),
  };
}

/**
 * @param {string} rootDir
 * @returns {string[]}
 */
function listHtmlFiles(rootDir) {
  /** @type {string[]} */
  const files = [];
  if (!fs.existsSync(rootDir)) {
    return files;
  }
  const stack = [rootDir];
  while (stack.length) {
    const current = stack.pop();
    if (!current) {
      continue;
    }
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
        continue;
      }
      if (entry.isFile() && entry.name.endsWith(".html")) {
        files.push(full);
      }
    }
  }
  return files.sort();
}

/**
 * @param {string} rootDir
 * @returns {{ scriptHashes: string[], styleHashes: string[], styleAttrHashes: string[] }}
 */
export function collectInlineCspHashes(rootDir) {
  const scriptHashes = new Set();
  const styleHashes = new Set();
  const styleAttrHashes = new Set();
  for (const file of listHtmlFiles(rootDir)) {
    const html = fs.readFileSync(file, "utf8");
    const extracted = extractInlineCspHashesFromHtml(html);
    for (const hash of extracted.scriptHashes) {
      scriptHashes.add(hash);
    }
    for (const hash of extracted.styleHashes) {
      styleHashes.add(hash);
    }
    for (const hash of extracted.styleAttrHashes) {
      styleAttrHashes.add(hash);
    }
  }
  return {
    scriptHashes: [...scriptHashes].sort(),
    styleHashes: [...styleHashes].sort(),
    styleAttrHashes: [...styleAttrHashes].sort(),
  };
}

/**
 * @param {{
 *   scriptHashes?: string[],
 *   styleHashes?: string[],
 *   styleAttrHashes?: string[],
 *   allowUnsafeEval?: boolean,
 * }} options
 * @returns {string}
 */
export function buildNginxCspInclude({
  scriptHashes = [],
  styleHashes = [],
  styleAttrHashes = [],
  allowUnsafeEval = false,
} = {}) {
  const scriptParts = ["'self'", ...scriptHashes];
  if (allowUnsafeEval) {
    scriptParts.push("'unsafe-eval'");
  }
  const styleParts = ["'self'", ...styleHashes];
  if (styleAttrHashes.length) {
    styleParts.push("'unsafe-hashes'", ...styleAttrHashes);
  }
  const policy = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "form-action 'self'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `style-src ${styleParts.join(" ")}`,
    `script-src ${scriptParts.join(" ")}`,
    "connect-src 'self'",
    "upgrade-insecure-requests",
    "report-uri /csp-report/",
  ].join("; ");
  return `add_header Content-Security-Policy "${policy}" always;\n`;
}

/**
 * @param {string} htmlRoot Directory containing machine/ and pub/ HTML trees
 * @param {string} outDir Private directory for nginx-csp-*.inc (NOT under static/)
 */
export function writeNginxCspIncludes(htmlRoot, outDir = htmlRoot) {
  const machineHashes = collectInlineCspHashes(path.join(htmlRoot, "machine"));
  const pubHashes = collectInlineCspHashes(path.join(htmlRoot, "pub"));
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(
    path.join(outDir, "nginx-csp-machine.inc"),
    buildNginxCspInclude({ ...machineHashes, allowUnsafeEval: true }),
    "utf8",
  );
  fs.writeFileSync(
    path.join(outDir, "nginx-csp-pub.inc"),
    buildNginxCspInclude({ ...pubHashes, allowUnsafeEval: false }),
    "utf8",
  );
}

/** Private CSP artifact dir beside Django static/ — never under STATIC_URL. */
export function defaultEdgeNginxCspDir(staticFrontendTarget = targetDir) {
  return path.resolve(staticFrontendTarget, "..", "..", "edge_nginx");
}

export function isProductionStaticCopy(argv = process.argv, env = process.env) {
  return env.HPCPERFSTATS_PRODUCTION_STATIC === "1" || argv.includes("--production");
}

export function copyRecursive(src, dest, { productionStatic = false } = {}) {
  if (!fs.existsSync(src)) {
    throw new Error(`copy-next-export: source missing: ${src}`);
  }
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      if (productionStatic && PRODUCTION_EXCLUDED_EXPORT_DIRS.includes(entry)) {
        continue;
      }
      copyRecursive(path.join(src, entry), path.join(dest, entry), { productionStatic });
    }
    return;
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

export function runCopyNextExport({
  out = outDir,
  target = targetDir,
  productionStatic = isProductionStaticCopy(),
  edgeNginxDir = defaultEdgeNginxCspDir(target),
} = {}) {
  if (!fs.existsSync(out)) {
    throw new Error("copy-next-export: run `next build` first — out/ not found");
  }
  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true });
  }
  const skipped = productionStatic ? [...PRODUCTION_EXCLUDED_EXPORT_DIRS] : [];
  copyRecursive(out, target, { productionStatic });
  // Never publish nginx config into the public static/frontend tree.
  for (const name of ["nginx-csp-machine.inc", "nginx-csp-pub.inc"]) {
    const leaked = path.join(target, name);
    if (fs.existsSync(leaked)) {
      fs.rmSync(leaked, { force: true });
    }
  }
  writeNginxCspIncludes(target, edgeNginxDir);
  return {
    out,
    target,
    edgeNginxDir,
    mode: productionStatic ? "production" : "full",
    skipped,
  };
}

const isDirectRun =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (isDirectRun) {
  try {
    const productionStatic = isProductionStaticCopy();
    const { out, target, edgeNginxDir, mode, skipped } = runCopyNextExport({
      productionStatic,
    });
    for (const entry of skipped) {
      if (fs.existsSync(path.join(out, entry))) {
        console.log(`copy-next-export: skipping production-excluded dir ${entry}/`);
      }
    }
    console.log(`copy-next-export (${mode}): copied ${out} → ${target}`);
    console.log(
      `copy-next-export: wrote edge CSP includes under ${edgeNginxDir} (not under static/)`,
    );
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  }
}
