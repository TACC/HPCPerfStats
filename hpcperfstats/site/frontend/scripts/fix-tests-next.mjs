#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(__dirname, "../src");

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, files);
    else if (/\.test\.tsx?$/.test(entry.name)) files.push(full);
  }
  return files;
}

for (const file of walk(srcRoot)) {
  let content = fs.readFileSync(file, "utf8");
  if (!content.includes("MemoryRouter") && !content.includes("react-router-dom")) continue;

  if (!content.includes('vi.mock("next/navigation"')) {
    content = `import { vi } from "vitest";\n\nvi.mock("next/navigation", () => ({\n  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),\n  usePathname: () => "/machine/",\n  useSearchParams: () => new URLSearchParams(),\n  useParams: () => ({}),\n  redirect: vi.fn(),\n}));\n\n${content}`;
  }

  content = content.replace(/import\s+\{\s*MemoryRouter[^}]*\}\s+from\s+['"]react-router-dom['"];?\n?/g, "");
  content = content.replace(/<MemoryRouter[^>]*>/g, "<>");
  content = content.replace(/<\/MemoryRouter>/g, "</>");
  content = content.replace(/import\s+[^;]+from\s+['"]react-router-dom['"];?\n?/g, "");

  fs.writeFileSync(file, content, "utf8");
  console.log(`fixed ${path.relative(srcRoot, file)}`);
}
