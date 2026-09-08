import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

/** Text extensions nginx may serve with brotli_static / gzip_static. */
export const SIDECAR_COMPRESS_EXTENSIONS = new Set([
  ".js",
  ".mjs",
  ".css",
  ".svg",
  ".json",
  ".txt",
  ".xml",
]);

/** Already-encoded or binary assets; do not write .br/.gz siblings. */
export const SIDECAR_SKIP_EXTENSIONS = new Set([
  ".br",
  ".gz",
  ".zst",
  ".woff",
  ".woff2",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".avif",
  ".gif",
  ".wasm",
  ".map",
]);

/** Match nginx gzip_min_length / brotli_min_length / zstd_min_length. */
export const SIDECAR_MIN_BYTES = 256;

/**
 * @param {string} relPosix posix path relative to the frontend static root
 * @param {number} sizeBytes uncompressed file size
 * @returns {boolean}
 */
export function shouldWriteStaticSidecars(relPosix, sizeBytes) {
  if (sizeBytes < SIDECAR_MIN_BYTES) {
    return false;
  }
  const normalized = relPosix.replaceAll("\\", "/");
  if (normalized.startsWith("machine/") && normalized.endsWith(".html")) {
    return false;
  }
  if (normalized.startsWith("pub/") && normalized.endsWith(".html")) {
    return false;
  }
  const ext = path.extname(normalized).toLowerCase();
  if (SIDECAR_SKIP_EXTENSIONS.has(ext)) {
    return false;
  }
  return SIDECAR_COMPRESS_EXTENSIONS.has(ext);
}

/**
 * Write Brotli-11 and Gzip-9 sidecars beside compressible text assets.
 * Keeps the uncompressed original (required by nginx *_static).
 *
 * @param {string} rootDir frontend static tree (copy-next-export target)
 * @returns {{ written: number, skipped: number }}
 */
export function compressStaticSidecars(rootDir) {
  if (!fs.existsSync(rootDir)) {
    return { written: 0, skipped: 0 };
  }
  let written = 0;
  let skipped = 0;
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
      if (!entry.isFile()) {
        continue;
      }
      const rel = path.relative(rootDir, full).split(path.sep).join("/");
      const stat = fs.statSync(full);
      if (!shouldWriteStaticSidecars(rel, stat.size)) {
        skipped += 1;
        continue;
      }
      const raw = fs.readFileSync(full);
      const brPath = `${full}.br`;
      const gzPath = `${full}.gz`;
      fs.writeFileSync(
        brPath,
        zlib.brotliCompressSync(raw, {
          params: {
            [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
          },
        }),
      );
      fs.writeFileSync(gzPath, zlib.gzipSync(raw, { level: 9 }));
      written += 1;
    }
  }
  return { written, skipped };
}
