import { useCallback, useState } from "react";
import type { SortDirection } from "../utils/table-sort-a11y";

export type TableSortState = {
  column: string;
  direction: SortDirection;
};

export function useTableSort(
  initialColumn: string,
  initialDirection: SortDirection = "asc",
  nextColumnDirection: SortDirection = "asc",
) {
  const [sort, setSort] = useState<TableSortState>({
    column: initialColumn,
    direction: initialDirection,
  });

  const onSort = useCallback(
    (column: string) => {
      setSort((prev) => {
        if (prev.column === column)
          return {
            column,
            direction: prev.direction === "asc" ? "desc" : "asc",
          };
        return { column, direction: nextColumnDirection };
      });
    },
    [nextColumnDirection],
  );

  return { sort, setSort, onSort };
}
