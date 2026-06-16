import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC_ROOT = join(import.meta.dirname, "..");
const LEGACY_HREF_PATTERN =
  /`\/(date|year|username|account|queue|host|job)\/[^`]*`/g;

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (entry === "generated" || entry === "generated-zod" || entry === "__tests__") {
        continue;
      }
      collectSourceFiles(full, out);
      continue;
    }
    if (!/\.(tsx?)$/.test(entry) || /\.test\.(tsx?)$/.test(entry)) {
      continue;
    }
    out.push(full);
  }
  return out;
}

describe("machine href drift guard", () => {
  it("forbids bare legacy browse paths in views and utils", () => {
    const offenders: string[] = [];
    for (const file of collectSourceFiles(join(SRC_ROOT, "views"))) {
      const text = readFileSync(file, "utf8");
      const matches = text.match(LEGACY_HREF_PATTERN);
      if (matches?.length) {
        offenders.push(`${file}: ${matches.join(", ")}`);
      }
    }
    for (const file of collectSourceFiles(join(SRC_ROOT, "utils"))) {
      if (file.endsWith("job-list-breadcrumbs.ts")) {
        continue;
      }
      const text = readFileSync(file, "utf8");
      const matches = text.match(LEGACY_HREF_PATTERN);
      if (matches?.length) {
        offenders.push(`${file}: ${matches.join(", ")}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
