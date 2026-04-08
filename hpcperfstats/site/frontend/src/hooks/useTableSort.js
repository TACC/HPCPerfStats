import { useCallback, useState } from "react";

export function useTableSort(initialColumn, initialDirection = "asc", nextColumnDirection = "asc") {
  const [sort, setSort] = useState({
    column: initialColumn,
    direction: initialDirection,
  });

  const onSort = useCallback(
    (column) => {
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
