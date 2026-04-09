import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

describe("open-sans self-host entry", () => {
  it("pulls Spacelab-aligned weights and latin + latin-ext subsets from fontsource", () => {
    const text = readFileSync(join(__dirname, "open-sans.css"), "utf8");
    expect(text).toContain("@fontsource/open-sans/latin-400.css");
    expect(text).toContain("@fontsource/open-sans/latin-700-italic.css");
    expect(text).toContain("@fontsource/open-sans/latin-ext-400.css");
    expect(text).toContain("@fontsource/open-sans/latin-ext-700-italic.css");
  });
});
