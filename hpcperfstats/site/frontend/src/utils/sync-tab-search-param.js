/**
 * @param {URLSearchParams} searchParams
 * @param {string} key
 * @param {string|null|undefined} tabValue — omit or null to delete param
 * @returns {URLSearchParams}
 */
export function searchParamsWithTab(searchParams, key, tabValue) {
  const next = new URLSearchParams(searchParams);
  if (tabValue == null || tabValue === "") {
    next.delete(key);
  } else {
    next.set(key, tabValue);
  }
  return next;
}

/**
 * @param {URLSearchParams} searchParams
 * @param {string} key
 * @param {string} defaultTab
 * @returns {string}
 */
export function readTabFromSearchParams(searchParams, key, defaultTab) {
  const raw = searchParams.get(key);
  return raw && String(raw).trim() ? raw : defaultTab;
}
