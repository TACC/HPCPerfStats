/** Build canonical href for Next client navigation (no trailing `?` when empty). */
export function hrefFromPathAndSearch(pathname: string, searchParams: URLSearchParams): string {
  const qs = searchParams.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

export type ReplacePathOptions = {
  /** Next App Router `router.replace` scroll option (default true). */
  scroll?: boolean;
};

/** Skip duplicate `router.replace` when target matches current location. */
export function replacePathIfChanged(
  router: { replace: (href: string, options?: { scroll?: boolean }) => void },
  targetPathname: string,
  targetParams: URLSearchParams,
  currentPathname: string,
  currentSearchParams: URLSearchParams,
  options?: ReplacePathOptions,
): void {
  const nextHref = hrefFromPathAndSearch(targetPathname, targetParams);
  const currentHref = hrefFromPathAndSearch(currentPathname, currentSearchParams);
  if (nextHref === currentHref) return;
  if (options?.scroll === false) {
    router.replace(nextHref, { scroll: false });
    return;
  }
  router.replace(nextHref);
}
