import { tableSortAriaSort, tableSortColumnArrow } from "../utils/table-sort-a11y";

export default function SortableTableHeader({
  column,
  sortKey,
  sortDir,
  onSort,
  buttonClassName = "btn btn-link btn-sm p-0",
  children,
}) {
  return (
    <th scope="col" aria-sort={tableSortAriaSort(column, sortKey, sortDir)}>
      <button
        type="button"
        className={buttonClassName}
        onClick={() => onSort(column)}
      >
        {children}
        {tableSortColumnArrow(column, sortKey, sortDir, { leadingSpace: false })}
      </button>
    </th>
  );
}
