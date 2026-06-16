import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC_ROOT = join(import.meta.dirname, "..");

/** Allowed pointer-events-none in views (decorative pagination spans, documented). */
const POINTER_EVENTS_ALLOWLIST = new Set([
  join(SRC_ROOT, "views/JobList.tsx"),
]);

function collectViewFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) {
      if (entry === "__tests__") continue;
      collectViewFiles(full, out);
      continue;
    }
    if (entry.endsWith(".tsx") && !entry.endsWith(".test.tsx")) {
      out.push(full);
    }
  }
  return out;
}

describe("interactive-ready drift guard", () => {
  it("forbids pointer-events-none on view surfaces outside allowlist", () => {
    const offenders: string[] = [];
    for (const file of collectViewFiles(join(SRC_ROOT, "views"))) {
      if (POINTER_EVENTS_ALLOWLIST.has(file)) continue;
      const text = readFileSync(file, "utf8");
      if (text.includes("pointer-events-none")) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("requires JobList progressive tableBusy contract test", () => {
    const jobListTest = readFileSync(
      join(SRC_ROOT, "views/__tests__/JobList.test.tsx"),
      "utf8",
    );
    expect(jobListTest).toMatch(/tableBusy/);
    expect(jobListTest).toMatch(/pointer-events-none/);
  });

  it("requires useFocusTrap test for focus-outside panel", () => {
    const trapTest = readFileSync(
      join(SRC_ROOT, "hooks/useFocusTrap.test.ts"),
      "utf8",
    );
    expect(trapTest).toMatch(/outside|does not preventDefault Tab when focus is outside/i);
  });
});
