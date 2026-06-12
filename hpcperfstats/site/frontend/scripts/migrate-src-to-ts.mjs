#!/usr/bin/env node
// One-shot migration: rename src js/jsx files to ts/tsx; replace react-router with next/navigation.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(__dirname, "../src");

const DELETE_AFTER = new Set([
  "main-machine.jsx",
  "main-pub.jsx",
  "main.jsx",
  "AppMachine.jsx",
  "AppPub.jsx",
  "App.jsx",
  "App.test.jsx",
  "AppPub.test.jsx",
]);

const PUB_PATH_PREFIXES = ["/cluster-dashboard"];

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "api" && full.endsWith("src/api")) {
        const generated = path.join(full, "generated");
        if (fs.existsSync(generated)) continue;
      }
      walk(full, files);
    } else if (/\.(js|jsx)$/.test(entry.name)) {
      files.push(full);
    }
  }
  return files;
}

function stripExtension(specifier) {
  return specifier.replace(/\.(jsx?|tsx?)$/, "");
}

function rewriteImports(content) {
  return content.replace(
    /from\s+(['"])([^'"]+)\1/g,
    (match, quote, specifier) => {
      if (!specifier.startsWith(".") && !specifier.startsWith("@/")) return match;
      const stripped = stripExtension(specifier);
      return stripped === specifier ? match : `from ${quote}${stripped}${quote}`;
    },
  );
}

function prefixMachinePath(pathExpr) {
  const trimmed = pathExpr.trim();
  if (
    trimmed.startsWith("/machine") ||
    trimmed.startsWith("/pub") ||
    trimmed.startsWith("/login") ||
    trimmed.startsWith("http") ||
    trimmed.includes("${")
  ) {
    return trimmed;
  }
  if (trimmed === "/" || trimmed === '"/"' || trimmed === "'/'") {
    return "/machine/";
  }
  if (trimmed.startsWith("`")) {
    if (trimmed.includes("/machine") || trimmed.includes("/pub")) return trimmed;
    if (trimmed.startsWith("`/")) {
      return trimmed.replace(/^`(\/[^`?]*)/, (m, p) => {
        if (PUB_PATH_PREFIXES.some((pp) => p === pp || p.startsWith(`${pp}/`))) {
          return `\`/pub${p === "/cluster-dashboard" ? "/cluster-dashboard" : p}`;
        }
        const suffix = p.endsWith("/") ? p : `${p}/`;
        return `\`/machine${suffix}`;
      });
    }
    return trimmed;
  }
  if ((trimmed.startsWith('"') || trimmed.startsWith("'")) && trimmed.startsWith("'/") || trimmed.startsWith('"/')) {
    const inner = trimmed.slice(1, -1);
    if (PUB_PATH_PREFIXES.some((pp) => inner === pp || inner.startsWith(`${pp}/`))) {
      return `"/pub${inner === "/cluster-dashboard" ? "/cluster-dashboard/" : inner.endsWith("/") ? inner : `${inner}/`}"`;
    }
    const suffix = inner.includes("?") ? inner : inner.endsWith("/") ? inner : `${inner}/`;
    return `"/machine${suffix.startsWith("/") ? suffix : `/${suffix}`}"`;
  }
  return trimmed;
}

function rewriteRouterImports(content, isPubFile) {
  let out = content;

  if (out.includes("react-router-dom")) {
    const needsLink = /\bLink\b/.test(out);
    const needsNavLink = /\bNavLink\b/.test(out);
    const needsNavigate = /\buseNavigate\b/.test(out) || /\bnavigate\(/.test(out);
    const needsParams = /\buseParams\b/.test(out);
    const needsPathname = /\buseLocation\b/.test(out) || /\busePathname\b/.test(out);
    const needsSearchParams = /\buseSearchParams\b/.test(out);
    const needsRedirect = /\bNavigate\b/.test(out);

    const nextImports = [];
    if (needsLink) nextImports.push("Link");
    if (needsNavigate) nextImports.push("useRouter");
    if (needsParams) nextImports.push("useParams");
    if (needsPathname) nextImports.push("usePathname");
    if (needsSearchParams) nextImports.push("useSearchParams");
    if (needsRedirect) nextImports.push("redirect");

    out = out.replace(/import\s+[^;]+from\s+['"]react-router-dom['"];?\n?/g, "");

    if (needsLink) {
      out = `import Link from "next/link";\n${out}`;
    }
    if (needsNavLink) {
      out = `import NavLink from "@/components/NavLink";\n${out}`;
    }
    if (nextImports.length > 0) {
      const filtered = nextImports.filter((n) => n !== "Link");
      if (filtered.length > 0) {
        out = `import { ${filtered.join(", ")} } from "next/navigation";\n${out}`;
      }
    }
  }

  out = out.replace(/\bconst\s+navigate\s*=\s*useNavigate\(\)/g, "const router = useRouter()");
  out = out.replace(/\bnavigate\(/g, "router.push(");
  out = out.replace(/\bconst\s+location\s*=\s*useLocation\(\)/g, "const pathname = usePathname()");
  out = out.replace(/\blocation\.pathname\b/g, "pathname");
  out = out.replace(/\bconst\s+\[searchParams\]\s*=\s*useSearchParams\(\)/g, "const searchParams = useSearchParams()");
  out = out.replace(/<Link\s+([^>]*?)\bto=/g, "<Link $1href=");
  out = out.replace(/<NavLink\s+([^>]*?)\bto=/g, "<NavLink $1to=");

  if (!isPubFile) {
    out = out.replace(/href=\{`(\/(?!machine|pub)[^`]+)`\}/g, (m, p) => {
      const suffix = p.includes("?") ? p : p.endsWith("/") ? p : `${p}/`;
      return `href={\`/machine${suffix}\`}`;
    });
    out = out.replace(/href="(\/(?!machine|pub)[^"]+)"/g, (m, p) => {
      const suffix = p.includes("?") ? p : p.endsWith("/") ? p : `${p}/`;
      return `href="/machine${suffix}"`;
    });
    out = out.replace(/router\.push\(`(\/(?!machine|pub)[^`]+)`\)/g, (m, p) => {
      const suffix = p.includes("?") ? p : p.endsWith("/") ? p : `${p}/`;
      return `router.push(\`/machine${suffix}\`)`;
    });
    out = out.replace(/router\.push\("(\/(?!machine|pub)[^"]+)"\)/g, (m, p) => {
      const suffix = p.includes("?") ? p : p.endsWith("/") ? p : `${p}/`;
      return `router.push("/machine${suffix}")`;
    });
  } else {
    out = out.replace(/href="\/cluster-dashboard/g, 'href="/pub/cluster-dashboard');
    out = out.replace(/href=\{`\/cluster-dashboard/g, "href={`/pub/cluster-dashboard");
    out = out.replace(/router\.push\("\/cluster-dashboard/g, 'router.push("/pub/cluster-dashboard');
  }

  out = out.replace(/from\s+['"]\.\/api\.js['"]/g, 'from "@/api"');
  out = out.replace(/from\s+['"]\.\.\/api\.js['"]/g, 'from "@/api"');
  out = out.replace(/from\s+['"]\.\/api['"]/g, 'from "@/api"');
  out = out.replace(/from\s+['"]\.\.\/api['"]/g, 'from "@/api"');

  return out;
}

function convertFile(filePath) {
  const base = path.basename(filePath);
  if (DELETE_AFTER.has(base)) {
    fs.unlinkSync(filePath);
    console.log(`deleted ${path.relative(srcRoot, filePath)}`);
    return;
  }

  const isPubFile = filePath.includes("LayoutPub") || filePath.includes("PageClusterDashboard");
  let content = fs.readFileSync(filePath, "utf8");
  content = rewriteImports(content);
  content = rewriteRouterImports(content, isPubFile);

  if (base.endsWith(".jsx")) {
    content = content.replace(
      /^(import\s+[^;]+;\s*)+/,
      (block) => block,
    );
  }

  const newPath = filePath.replace(/\.jsx$/, ".tsx").replace(/\.js$/, ".ts");
  fs.writeFileSync(newPath, content, "utf8");
  if (newPath !== filePath) fs.unlinkSync(filePath);
  console.log(`converted ${path.relative(srcRoot, filePath)} → ${path.relative(srcRoot, newPath)}`);
}

const files = walk(srcRoot);
for (const file of files) {
  if (file.includes("/api/generated/")) continue;
  if (file.endsWith("fetch-mutator.ts")) continue;
  convertFile(file);
}

console.log(`migrate-src-to-ts: processed ${files.length} files`);
