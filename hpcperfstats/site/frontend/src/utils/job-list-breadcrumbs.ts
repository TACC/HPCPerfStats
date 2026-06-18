import type { BreadcrumbItem } from "@/components/PageBreadcrumbs";
import type { JobListSelectionContext } from "./job-list-selection-context";

export function buildJobListBreadcrumbs(
  selection: JobListSelectionContext,
  terminalLabel = "Jobs",
): BreadcrumbItem[] {
  const items: BreadcrumbItem[] = [{ label: "Browse", to: "/machine/" }];

  if (selection.year) {
    items.push({ label: `Year ${selection.year}`, to: `/year/${selection.year}` });
  } else if (selection.date) {
    items.push({ label: `Date ${selection.date}`, to: `/date/${selection.date}` });
  } else if (selection.username) {
    items.push({
      label: `User ${selection.username}`,
      to: `/username/${encodeURIComponent(selection.username)}`,
    });
  } else if (selection.account) {
    items.push({
      label: `Project ${selection.account}`,
      to: `/account/${encodeURIComponent(selection.account)}`,
    });
  } else if (selection.queue) {
    items.push({
      label: `Queue ${selection.queue}`,
      to: `/queue/${encodeURIComponent(selection.queue)}`,
    });
  } else if (selection.host) {
    items.push({
      label: `Host ${selection.host}`,
      to: `/host/${encodeURIComponent(selection.host)}`,
    });
  }
  items.push({ label: terminalLabel });
  return items;
}
