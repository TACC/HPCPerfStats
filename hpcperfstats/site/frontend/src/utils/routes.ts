/** Machine SPA base path (replaces React Router basename `/machine`). */
const MACHINE_BASE = "/machine";

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
