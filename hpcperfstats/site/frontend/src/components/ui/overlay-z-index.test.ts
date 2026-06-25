import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const uiDir = join(dirname(fileURLToPath(import.meta.url)));

function readUiSource(filename: string): string {
  return readFileSync(join(uiDir, filename), "utf8");
}

describe("overlay z-index contract", () => {
  it.each([
    ["popover.tsx", "popover"],
    ["dropdown-menu.tsx", "dropdown-menu"],
    ["select.tsx", "select"],
  ])("keeps portaled %s above modal backdrop (z-[1050])", (filename) => {
    const source = readUiSource(filename);
    expect(source).toMatch(/z-\[1050\]/);
    expect(source).not.toMatch(/\bz-50\b/);
  });

  it.each([["dialog.tsx", "dialog"]])(
    "keeps modal %s on project backdrop scale",
    (filename) => {
    const source = readUiSource(filename);
    expect(source).toMatch(/z-\[var\(--z-modal-backdrop\)\]/);
    expect(source).toMatch(/z-\[calc\(var\(--z-modal-backdrop\)\+1\)\]/);
    expect(source).not.toMatch(/\bz-50\b/);
  });
});
