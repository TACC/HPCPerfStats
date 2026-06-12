#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, "../app");

const machinePages = [
  { route: "machine/page.tsx", component: "Search", importPath: "@/views/Search" },
  { route: "machine/job/[pk]/page.tsx", component: "JobDetail", importPath: "@/views/JobDetail" },
  { route: "machine/job/[jid]/[typeName]/page.tsx", component: "TypeDetail", importPath: "@/views/TypeDetail" },
  { route: "machine/year/[year]/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/date/[date]/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/username/[username]/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/account/[account]/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/queue/[queue]/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/host/[host]/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/host/[host]/plot/page.tsx", component: "HostDetail", importPath: "@/views/HostDetail" },
  { route: "machine/jobs/page.tsx", component: "JobList", importPath: "@/views/JobList" },
  { route: "machine/admin_monitor/page.tsx", component: "AdminMonitor", importPath: "@/views/AdminMonitor" },
  { route: "machine/job_monitor/page.tsx", component: "JobMonitor", importPath: "@/views/JobMonitor" },
  { route: "machine/api-key/page.tsx", component: "PageApiKey", importPath: "@/views/PageApiKey" },
  { route: "machine/not-found.tsx", component: "PageNotFound", importPath: "@/views/PageNotFound", isNotFound: true },
];

const pubPages = [
  {
    route: "pub/page.tsx",
    content: `"use client";\n\nimport { redirect } from "next/navigation";\n\nexport default function PubIndexPage() {\n  redirect("/pub/cluster-dashboard/");\n}\n`,
  },
  {
    route: "pub/cluster-dashboard/page.tsx",
    content: `"use client";\n\nimport PageClusterDashboard from "@/views/PageClusterDashboard";\n\nexport default function ClusterDashboardPage() {\n  return <PageClusterDashboard />;\n}\n`,
  },
];

function writePage(relPath, component, importPath) {
  const full = path.join(appRoot, relPath);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  const body = `"use client";\n\nimport ${component} from "${importPath}";\n\nexport default function Page() {\n  return <${component} />;\n}\n`;
  fs.writeFileSync(full, body, "utf8");
  console.log(`wrote ${relPath}`);
}

for (const page of machinePages) {
  if (page.isNotFound) {
    const full = path.join(appRoot, page.route);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    const body = `"use client";\n\nimport ${page.component} from "${page.importPath}";\n\nexport default function NotFound() {\n  return <${page.component} />;\n}\n`;
    fs.writeFileSync(full, body, "utf8");
    console.log(`wrote ${page.route}`);
  } else {
    writePage(page.route, page.component, page.importPath);
  }
}

for (const page of pubPages) {
  const full = path.join(appRoot, page.route);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, page.content, "utf8");
  console.log(`wrote ${page.route}`);
}
