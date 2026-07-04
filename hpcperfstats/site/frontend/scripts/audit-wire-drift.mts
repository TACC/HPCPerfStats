/**
 * One-off audit: compare realistic Django wire payloads vs Orval Zod schemas
 * registered in response-schema-registry.ts. Run: npx tsx scripts/audit-wire-drift.mts
 */
import { resolveResponseSchema } from "../src/api/response-schema-registry";
import { WIRE_AUDIT_CASES } from "../test/wire-audit/wire-audit-cases";

function summarize(data: unknown): string {
  if (data === null || data === undefined) return String(data);
  if (typeof data !== "object") return JSON.stringify(data);
  const keys = Object.keys(data as object);
  if (keys.length === 0) return "(empty object — all wire fields stripped)";
  if (keys.length <= 6) return `{ ${keys.join(", ")} }`;
  return `{ ${keys.slice(0, 6).join(", ")}, … +${keys.length - 6} }`;
}

function routeLabel(method: string, path: string, label?: string): string {
  const base = `${method} ${path}`;
  return label ? `${base} (${label})` : base;
}

console.log("OpenAPI wire drift audit (Zod safeParse vs realistic Django payloads)\n");
console.log("| Route | Zod | Parsed output | Wire keys lost |");
console.log("|-------|-----|---------------|----------------|");

let fail = 0;
let silent = 0;
let ok = 0;

for (const { method, path, wire, label } of WIRE_AUDIT_CASES) {
  const route = routeLabel(method, path, label);
  const schema = resolveResponseSchema(method, path);
  if (!schema) {
    fail += 1;
    console.log(`| ${route} | **FAIL** | — | no registry schema |`);
    continue;
  }
  const wireKeys =
    wire && typeof wire === "object" ? Object.keys(wire as object) : [];
  const result = schema.safeParse(wire);
  if (!result.success) {
    fail += 1;
    const issues = result.error.issues.map((i) => i.path.join(".") || i.message).slice(0, 3);
    console.log(`| ${route} | **FAIL** | — | ${issues.join("; ")} |`);
    continue;
  }
  const parsedKeys =
    result.data && typeof result.data === "object"
      ? Object.keys(result.data as object)
      : [];
  const lost = wireKeys.filter((k) => !parsedKeys.includes(k));
  if (lost.length > 0) {
    silent += 1;
    console.log(
      `| ${route} | pass (strip) | ${summarize(result.data)} | ${lost.slice(0, 8).join(", ")}${lost.length > 8 ? "…" : ""} |`,
    );
  } else {
    ok += 1;
    console.log(`| ${route} | OK | ${summarize(result.data)} | — |`);
  }
}

console.log(`\nSummary: ${ok} aligned, ${silent} silent strip, ${fail} hard fail (total ${WIRE_AUDIT_CASES.length})`);
