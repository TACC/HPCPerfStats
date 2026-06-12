import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const targetDir = path.resolve(__dirname, "../../hpcperfstats_site/static/frontend");

/** Keep in sync with src/config/publicRobotsAllowPrefixes.ts */
const PUBLIC_ROBOTS_ALLOW_PREFIXES = ["/pub/", "/pub/cluster-dashboard"];

function formatRobotsTxtBody(allowPrefixes) {
  const lines = ["User-agent: *"];
  for (const prefix of allowPrefixes) {
    lines.push(`Allow: ${prefix}`);
  }
  lines.push("Disallow: /");
  return lines.join("\n");
}

if (!fs.existsSync(targetDir)) {
  console.error("generate-robots-txt: static frontend dir missing — run build first");
  process.exit(1);
}

const body = formatRobotsTxtBody(PUBLIC_ROBOTS_ALLOW_PREFIXES);
fs.writeFileSync(path.join(targetDir, "robots.txt"), body, "utf8");
console.log(`generate-robots-txt: wrote robots.txt to ${targetDir}`);
