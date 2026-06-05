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

/**
 * @param {string} label - Visible column label
 * @param {string} column
 * @param {string} sortColumn
 * @param {"asc"|"desc"} sortDirection
 */
export function tableSortButtonAriaLabel(label, column, sortColumn, sortDirection) {
  const base = `Sort by ${label}`;
  if (sortColumn !== column) return base;
  return `${base}, ${sortDirection === "asc" ? "ascending" : "descending"}`;
}
