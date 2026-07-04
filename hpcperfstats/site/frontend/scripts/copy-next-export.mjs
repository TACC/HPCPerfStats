import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Next static export dirs omitted from production deploy (Playwright / test-only routes). */
export const PRODUCTION_EXCLUDED_EXPORT_DIRS = ["bokeh-playwright-smoke"];

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const outDir = path.join(frontendRoot, "out");
const targetDir = path.resolve(frontendRoot, "../hpcperfstats_site/static/frontend");

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
} = {}) {
  if (!fs.existsSync(out)) {
    throw new Error("copy-next-export: run `next build` first — out/ not found");
  }
  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true });
  }
  const skipped = productionStatic ? [...PRODUCTION_EXCLUDED_EXPORT_DIRS] : [];
  copyRecursive(out, target, { productionStatic });
  return { out, target, mode: productionStatic ? "production" : "full", skipped };
}

const isDirectRun =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (isDirectRun) {
  try {
    const productionStatic = isProductionStaticCopy();
    const { out, target, mode, skipped } = runCopyNextExport({ productionStatic });
    for (const entry of skipped) {
      if (fs.existsSync(path.join(out, entry))) {
        console.log(`copy-next-export: skipping production-excluded dir ${entry}/`);
      }
    }
    console.log(`copy-next-export (${mode}): copied ${out} → ${target}`);
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  }
}
