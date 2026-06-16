import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const FRONTEND_SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Recurring lockup: `[searchParams]` object identity in hook/view memo/effect deps. */
const UNSTABLE_SEARCH_PARAMS_DEP = /\[[^\]]*\bsearchParams\b(?![^\]]*\.toString\(\))[^\]]*\]/;

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (entry === "node_modules" || entry === "generated") continue;
      collectSourceFiles(full, out);
      continue;
    }
    if (/\.(tsx?|jsx?)$/.test(entry) && !/\.test\.(tsx?|jsx?)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

describe("unstable searchParams dependency guard", () => {
  it("views and hooks do not list raw searchParams in memo/effect deps", () => {
    const roots = ["views", "hooks"].map((segment) => join(FRONTEND_SRC, segment));
    const violations: string[] = [];

    for (const root of roots) {
      for (const file of collectSourceFiles(root)) {
        const text = readFileSync(file, "utf8");
        const lines = text.split("\n");
        for (let i = 0; i < lines.length; i += 1) {
          const line = lines[i];
          if (!line.includes("searchParams")) continue;
          if (!/useMemo\(|useEffect\(|useCallback\(/.test(line)) continue;
          if (!UNSTABLE_SEARCH_PARAMS_DEP.test(line)) continue;
          violations.push(`${relative(FRONTEND_SRC, file)}:${i + 1}: ${line.trim()}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
