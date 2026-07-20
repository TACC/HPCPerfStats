import type { MouseEvent } from "react";
import {
  hrefFromPathAndSearch,
  replacePathIfChanged,
  type ReplacePathOptions,
} from "./replace-path-if-changed";

type SoftNavRouter = { replace: (href: string, options?: { scroll?: boolean }) => void };

/**
 * Soft presentation navigation (sort, page, view tab): `router.replace` with
 * `scroll: false` so the SPA keeps the mounted view (no full Link remount).
 */
export function softReplacePresentationParams(
  router: SoftNavRouter,
  pathname: string,
  nextParams: URLSearchParams,
  currentSearchParams: URLSearchParams,
  options: ReplacePathOptions = { scroll: false },
): void {
  replacePathIfChanged(router, pathname, nextParams, pathname, currentSearchParams, options);
}

/** Convert a full same-path href into URLSearchParams for soft replace. */
export function searchParamsFromSamePathHref(href: string, pathname: string): URLSearchParams {
  if (href.startsWith("?")) {
    return new URLSearchParams(href.slice(1));
  }
  if (href.startsWith(pathname)) {
    const q = href.slice(pathname.length);
    if (q.startsWith("?")) return new URLSearchParams(q.slice(1));
    if (q === "" || q === "/") return new URLSearchParams();
  }
  try {
    const url = new URL(href, "http://local.invalid");
    return new URLSearchParams(url.search);
  } catch {
    return new URLSearchParams();
  }
}

export function softPresentationClick(
  event: MouseEvent<HTMLAnchorElement>,
  router: SoftNavRouter,
  pathname: string,
  href: string,
  currentSearchParams: URLSearchParams,
): void {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return;
  }
  event.preventDefault();
  softReplacePresentationParams(
    router,
    pathname,
    searchParamsFromSamePathHref(href, pathname),
    currentSearchParams,
  );
}

export function softPresentationHref(
  pathname: string,
  params: URLSearchParams,
): string {
  return hrefFromPathAndSearch(pathname, params);
}
