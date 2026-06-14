/** Safe dev-mode check for Next.js static export (import.meta.env may be undefined). */
export function isDevEnvironment(): boolean {
  if (typeof import.meta !== "undefined" && import.meta.env?.DEV) return true;
  if (typeof process !== "undefined" && process.env?.NODE_ENV === "development") return true;
  return false;
}
