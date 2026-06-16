/** Build canonical href for Next client navigation (no trailing `?` when empty). */
export function hrefFromPathAndSearch(pathname: string, searchParams: URLSearchParams): string {
  const qs = searchParams.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

/** Skip duplicate `router.replace` when target matches current location. */
export function replacePathIfChanged(
  router: { replace: (href: string) => void },
  targetPathname: string,
  targetParams: URLSearchParams,
  currentPathname: string,
  currentSearchParams: URLSearchParams,
): void {
  const nextHref = hrefFromPathAndSearch(targetPathname, targetParams);
  const currentHref = hrefFromPathAndSearch(currentPathname, currentSearchParams);
  if (nextHref === currentHref) return;
  router.replace(nextHref);
}
