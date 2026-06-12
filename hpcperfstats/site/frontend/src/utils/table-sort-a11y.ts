export type SortDirection = "asc" | "desc";

/**
 * Shared aria-sort and sort-indicator strings for table column headers.
 */
export function tableSortAriaSort(
  column: string,
  sortColumn: string,
  sortDirection: SortDirection,
): "ascending" | "descending" | undefined {
  if (sortColumn !== column) return undefined;
  return sortDirection === "asc" ? "ascending" : "descending";
}

export type TableSortColumnArrowOptions = {
  /** JobMonitor uses a space before ▲/▼; AdminMonitor uses label + compact arrow. */
  leadingSpace?: boolean;
};

export function tableSortColumnArrow(
  column: string,
  sortColumn: string,
  sortDirection: SortDirection,
  options: TableSortColumnArrowOptions = {},
): string {
  const { leadingSpace = true } = options;
  if (sortColumn !== column) return "";
  const sym = sortDirection === "asc" ? "▲" : "▼";
  return leadingSpace ? ` ${sym}` : sym;
}

export function tableSortButtonAriaLabel(
  label: string,
  column: string,
  sortColumn: string,
  sortDirection: SortDirection,
): string {
  const base = `Sort by ${label}`;
  if (sortColumn !== column) return base;
  return `${base}, ${sortDirection === "asc" ? "ascending" : "descending"}`;
}
