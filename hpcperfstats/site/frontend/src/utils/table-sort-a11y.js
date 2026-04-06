/**
 * Shared aria-sort and sort-indicator strings for table column headers.
 *
 * @param {string} column - Header column id
 * @param {string} sortColumn - Currently sorted column id
 * @param {"asc"|"desc"} sortDirection
 */
export function tableSortAriaSort(column, sortColumn, sortDirection) {
  if (sortColumn !== column) return undefined;
  return sortDirection === "asc" ? "ascending" : "descending";
}

/**
 * @param {object} [options]
 * @param {boolean} [options.leadingSpace=true] — JobMonitor uses a space before ▲/▼; AdminMonitor uses label + compact arrow.
 */
export function tableSortColumnArrow(column, sortColumn, sortDirection, options = {}) {
  const { leadingSpace = true } = options;
  if (sortColumn !== column) return "";
  const sym = sortDirection === "asc" ? "▲" : "▼";
  return leadingSpace ? ` ${sym}` : sym;
}
