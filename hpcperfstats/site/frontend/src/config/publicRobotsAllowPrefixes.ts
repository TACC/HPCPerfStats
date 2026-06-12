/**
 * Canonical crawlable `/pub/` URL path prefixes for robots.txt (nginx static).
 *
 * When adding a user-facing React route under the public basename, append the
 * browser path here (no hostname) and extend web E2E / Vitest robots checks.
 */
export const PUBLIC_ROBOTS_ALLOW_PREFIXES = Object.freeze([
  "/pub/",
  "/pub/cluster-dashboard",
]);
