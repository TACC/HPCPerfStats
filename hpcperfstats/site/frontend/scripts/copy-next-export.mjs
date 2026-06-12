import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const outDir = path.join(frontendRoot, "out");
const targetDir = path.resolve(frontendRoot, "../hpcperfstats_site/static/frontend");

function copyRecursive(src, dest) {
  if (!fs.existsSync(src)) {
    console.error(`copy-next-export: source missing: ${src}`);
    process.exit(1);
  }
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      copyRecursive(path.join(src, entry), path.join(dest, entry));
    }
    return;
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

if (!fs.existsSync(outDir)) {
  console.error("copy-next-export: run `next build` first — out/ not found");
  process.exit(1);
}

if (fs.existsSync(targetDir)) {
  fs.rmSync(targetDir, { recursive: true, force: true });
}
copyRecursive(outDir, targetDir);
console.log(`copy-next-export: copied ${outDir} → ${targetDir}`);
