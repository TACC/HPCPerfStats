/**
 * Copy stock UMD ``bokeh.min.js`` into ``public/vendor/`` so the SPA can load
 * it via ``<script>`` without Turbopack rewriting the file (which breaks
 * ``register_models`` → ``could not resolve type 'Grid'``).
 *
 * Keep version lockstep with ``@bokeh/bokehjs`` in package.json.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const require = createRequire(import.meta.url);

const pkgRoot = path.dirname(require.resolve("@bokeh/bokehjs/package.json"));
const src = path.join(pkgRoot, "build/js/bokeh.min.js");
const destDir = path.join(frontendRoot, "public/vendor");
const dest = path.join(destDir, "bokeh.min.js");

if (!fs.existsSync(src)) {
  console.error(`sync-bokeh-vendor: missing ${src}`);
  process.exit(1);
}

fs.mkdirSync(destDir, { recursive: true });
fs.copyFileSync(src, dest);
console.log(`sync-bokeh-vendor: copied ${path.relative(frontendRoot, dest)} from @bokeh/bokehjs`);
