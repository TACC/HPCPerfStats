import { hrefFromPathAndSearch } from "./replace-path-if-changed";
import { searchParamsWithTab } from "./sync-tab-search-param";

/**
 * Sync `?tab=` (or other presentation tab key) via `history.replaceState` so
 * Next App Router does not remount the lazy view (unlike `router.replace`).
 */
export function replaceTabInHistory(
  pathname: string,
  currentSearchParams: URLSearchParams,
  key: string,
  tabValue: string | null,
): void {
  if (typeof window === "undefined" || !window.history?.replaceState) return;
  const next = searchParamsWithTab(currentSearchParams, key, tabValue);
  const href = hrefFromPathAndSearch(pathname, next);
  const currentHref = hrefFromPathAndSearch(pathname, currentSearchParams);
  if (href === currentHref) return;
  window.history.replaceState(window.history.state, "", href);
}
