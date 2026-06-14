/** Parse `/machine/**` catch-all slug segments for static-export client routing. */

export type MachineRouteView =
  | "search"
  | "jobList"
  | "jobDetail"
  | "typeDetail"
  | "hostDetail"
  | "adminMonitor"
  | "jobMonitor"
  | "pageApiKey"
  | "notFound";

export type MachineFlatRouteParams = {
  year?: string;
  date?: string;
  pk?: string;
  jid?: string;
  typeName?: string;
  username?: string;
  account?: string;
  queue?: string;
  host?: string;
};

export function routeParamString(value: string | string[] | undefined): string {
  if (Array.isArray(value)) return value[0] ?? "";
  return value ?? "";
}

/** Strip `/machine` prefix and return URL path segments. */
export function parseMachinePathname(pathname: string): string[] {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (!normalized.startsWith("/machine")) return [];
  const rest = normalized.slice("/machine".length).replace(/^\//, "");
  if (!rest) return [];
  return rest.split("/").filter(Boolean).map((segment) => decodeURIComponent(segment));
}

export function parseMachineSlug(slug: string[] | undefined): {
  slug: string[];
  flatParams: MachineFlatRouteParams;
  view: MachineRouteView;
} {
  const parts = slug ?? [];
  const flatParams: MachineFlatRouteParams = {};
  let view: MachineRouteView = "notFound";

  if (parts.length === 0) {
    view = "search";
  } else if (parts.length === 1) {
    const [head] = parts;
    if (head === "jobs") view = "jobList";
    else if (head === "admin_monitor") view = "adminMonitor";
    else if (head === "job_monitor") view = "jobMonitor";
    else if (head === "api-key") view = "pageApiKey";
  } else if (parts.length === 2) {
    const [head, value] = parts;
    if (head === "job") {
      view = "jobDetail";
      flatParams.pk = value;
    } else if (head === "year") {
      view = "jobList";
      flatParams.year = value;
    } else if (head === "date") {
      view = "jobList";
      flatParams.date = value;
    } else if (head === "username") {
      view = "jobList";
      flatParams.username = value;
    } else if (head === "account") {
      view = "jobList";
      flatParams.account = value;
    } else if (head === "queue") {
      view = "jobList";
      flatParams.queue = value;
    } else if (head === "host") {
      view = "jobList";
      flatParams.host = value;
    }
  } else if (parts.length === 3 && parts[0] === "job") {
    view = "typeDetail";
    flatParams.jid = parts[1];
    flatParams.typeName = parts[2];
  } else if (parts.length === 3 && parts[0] === "host" && parts[2] === "plot") {
    view = "hostDetail";
    flatParams.host = parts[1];
  }

  return { slug: parts, flatParams, view };
}

export function resolveMachineSlugFromNavigation(
  params: Record<string, string | string[] | undefined>,
  pathname: string,
): string[] {
  const rawSlug = params.slug;
  if (Array.isArray(rawSlug) && rawSlug.length > 0) {
    return rawSlug.map((segment) => decodeURIComponent(String(segment)));
  }
  if (typeof rawSlug === "string" && rawSlug.length > 0) {
    return rawSlug.split("/").filter(Boolean).map((segment) => decodeURIComponent(segment));
  }
  return parseMachinePathname(pathname);
}
