/** Drift guard: no legacy JavaScript source under src/ after Next.js + TS migration. */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const srcRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)));

function listJsFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...listJsFiles(full));
      continue;
    }
    if (entry.name.endsWith(".js") || entry.name.endsWith(".jsx")) {
      found.push(full);
    }
  }
  return found;
}

describe("typescript migration", () => {
  it("has no .js or .jsx files under src/", () => {
    const legacy = listJsFiles(srcRoot).map((p) => path.relative(srcRoot, p));
    expect(legacy).toEqual([]);
  });
});
