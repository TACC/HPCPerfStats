export function searchParamsWithTab(
  searchParams: URLSearchParams,
  key: string,
  tabValue: string | null | undefined,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  if (tabValue == null || tabValue === "") {
    next.delete(key);
  } else {
    next.set(key, tabValue);
  }
  return next;
}

export function readTabFromSearchParams(
  searchParams: URLSearchParams | { get: (key: string) => string | null },
  key: string,
  defaultTab: string,
): string {
  const raw = searchParams.get(key);
  return raw && String(raw).trim() ? raw : defaultTab;
}
