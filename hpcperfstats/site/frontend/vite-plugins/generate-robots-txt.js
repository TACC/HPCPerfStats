import fs from "node:fs";
import path from "node:path";
import { formatRobotsTxtBody } from "../src/utils/robots-txt-format.js";
import { PUBLIC_ROBOTS_ALLOW_PREFIXES } from "../src/config/publicRobotsAllowPrefixes.js";

export function generateRobotsTxtPlugin() {
  let outDir = "";
  return {
    name: "generate-robots-txt",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      if (!outDir || !fs.existsSync(outDir)) {
        return;
      }
      const body = formatRobotsTxtBody(PUBLIC_ROBOTS_ALLOW_PREFIXES);
      fs.writeFileSync(path.join(outDir, "robots.txt"), body, "utf8");
    },
  };
}
