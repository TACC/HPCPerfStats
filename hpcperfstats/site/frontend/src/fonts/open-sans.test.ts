import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

describe("open-sans self-host entry", () => {
  it("declares latin woff2 faces only (no latin-ext or woff fallbacks)", () => {
    const text = readFileSync(join(__dirname, "open-sans.css"), "utf8");
    expect(text).toContain("open-sans-latin-400-normal.woff2");
    expect(text).toContain("open-sans-latin-700-italic.woff2");
    expect(text).not.toContain("latin-ext");
    expect(text).not.toContain("format(\"woff\")");
    expect(text).not.toContain("@import");
  });
});
