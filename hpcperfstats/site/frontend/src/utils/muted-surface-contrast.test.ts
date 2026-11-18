import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC_ROOT = join(import.meta.dirname, "..");

// --muted-foreground on solid --muted is ~4.3:1 in the light theme, under the
// WCAG AA 4.5:1 minimum for normal text. jsdom cannot compute contrast, so
// jest-axe misses it; Playwright axe in test_a11y_axe_browser.py caught it on
// the job-list histogram "Unavailable" placeholders.
const SOLID_MUTED_BG = /(?:^|\s)bg-muted(?=\s|$)/;
const MUTED_TEXT = /(?:^|\s)text-muted-foreground(?=\s|$)/;
const CLASS_STRING = /"([^"\n]*)"|`([^`]*)`/g;

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry !== "__tests__" && entry !== "generated") collectSourceFiles(full, out);
    } else if (entry.endsWith(".tsx") && !entry.endsWith(".test.tsx")) {
      out.push(full);
    }
  }
  return out;
}

describe("muted surface contrast", () => {
  it("never pairs text-muted-foreground with a solid bg-muted surface", () => {
    const offenders: string[] = [];
    for (const file of collectSourceFiles(SRC_ROOT)) {
      for (const m of readFileSync(file, "utf-8").matchAll(CLASS_STRING)) {
        const cls = m[1] ?? m[2] ?? "";
        if (SOLID_MUTED_BG.test(cls) && MUTED_TEXT.test(cls)) {
          offenders.push(`${file.slice(SRC_ROOT.length + 1)}: ${cls}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("detects the low-contrast pair (guard self-check)", () => {
    const cls = "rounded bg-muted p-3 text-muted-foreground";
    expect(SOLID_MUTED_BG.test(cls) && MUTED_TEXT.test(cls)).toBe(true);
    expect(SOLID_MUTED_BG.test("hover:bg-muted text-muted-foreground")).toBe(false);
    expect(SOLID_MUTED_BG.test("bg-muted/50 text-muted-foreground")).toBe(false);
  });
});
