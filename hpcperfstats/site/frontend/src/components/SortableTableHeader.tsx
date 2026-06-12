import type { ReactNode } from "react";
import type { SortDirection } from "../utils/table-sort-a11y";
import {
  tableSortAriaSort,
  tableSortButtonAriaLabel,
  tableSortColumnArrow,
} from "../utils/table-sort-a11y";

export type SortableTableHeaderProps = {
  column: string;
  sortKey: string;
  sortDir: SortDirection;
  onSort: (column: string) => void;
  buttonClassName?: string;
  children: ReactNode;
};

export default function SortableTableHeader({
  column,
  sortKey,
  sortDir,
  onSort,
  buttonClassName = "btn btn-link btn-sm p-0",
  children,
}: SortableTableHeaderProps) {
  return (
    <th scope="col" aria-sort={tableSortAriaSort(column, sortKey, sortDir)}>
      <button
        type="button"
        className={buttonClassName}
        aria-label={tableSortButtonAriaLabel(
          typeof children === "string" ? children : column,
          column,
          sortKey,
          sortDir,
        )}
        onClick={() => onSort(column)}
      >
        {children}
        {tableSortColumnArrow(column, sortKey, sortDir, { leadingSpace: false })}
      </button>
    </th>
  );
}
