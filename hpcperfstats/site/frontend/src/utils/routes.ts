/** Machine SPA base path (replaces React Router basename `/machine`). */
export const MACHINE_BASE = "/machine";

/** Public dashboard base path. */
export const PUB_BASE = "/pub";

export function machineHref(path: string): string {
  if (!path || path === "/") return `${MACHINE_BASE}/`;
  if (path.startsWith(MACHINE_BASE)) {
    if (path.includes("?")) return path;
    return path.endsWith("/") ? path : `${path}/`;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  if (normalized.includes("?")) {
    const [pathname, query] = normalized.split("?");
    const slashPath = pathname.endsWith("/") ? pathname : `${pathname}/`;
    return `${MACHINE_BASE}${slashPath}?${query}`;
  }
  const suffix = normalized.endsWith("/") ? normalized : `${normalized}/`;
  return `${MACHINE_BASE}${suffix}`;
}

export function pubHref(path: string): string {
  if (!path || path === "/") return `${PUB_BASE}/`;
  if (path.startsWith(PUB_BASE)) {
    if (path.includes("?")) return path;
    return path.endsWith("/") ? path : `${path}/`;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  if (normalized.includes("?")) {
    const [pathname, query] = normalized.split("?");
    const slashPath = pathname.endsWith("/") ? pathname : `${pathname}/`;
    return `${PUB_BASE}${slashPath}?${query}`;
  }
  const suffix = normalized.endsWith("/") ? normalized : `${normalized}/`;
  return `${PUB_BASE}${suffix}`;
}
