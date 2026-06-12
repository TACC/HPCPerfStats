import type { BreadcrumbItem } from "@/components/PageBreadcrumbs";

export function buildJobListBreadcrumbs(
  routeParams: Record<string, string | string[] | undefined>,
  terminalLabel = "Jobs",
): BreadcrumbItem[] {
  const items: BreadcrumbItem[] = [{ label: "Browse", to: "/" }];
  const year = routeParams.year;
  const date = routeParams.date;
  const username = routeParams.username;
  const account = routeParams.account;
  const queue = routeParams.queue;
  const host = routeParams.host;

  if (year && typeof year === "string") {
    items.push({ label: `Year ${year}`, to: `/year/${year}` });
  } else if (date && typeof date === "string") {
    items.push({ label: `Date ${date}`, to: `/date/${date}` });
  } else if (username && typeof username === "string") {
    items.push({
      label: `User ${username}`,
      to: `/username/${encodeURIComponent(username)}`,
    });
  } else if (account && typeof account === "string") {
    items.push({
      label: `Project ${account}`,
      to: `/account/${encodeURIComponent(account)}`,
    });
  } else if (queue && typeof queue === "string") {
    items.push({
      label: `Queue ${queue}`,
      to: `/queue/${encodeURIComponent(queue)}`,
    });
  } else if (host && typeof host === "string") {
    items.push({
      label: `Host ${host}`,
      to: `/host/${encodeURIComponent(host)}`,
    });
  }
  items.push({ label: terminalLabel });
  return items;
}
