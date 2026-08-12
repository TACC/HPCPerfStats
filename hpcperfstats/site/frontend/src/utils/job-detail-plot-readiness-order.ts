/**
 * Rank and stable-sort Job Detail multi-plot panels by embed readiness.
 *
 * Ready graphs render before loading, which render before unavailable/error.
 * Equal ranks keep catalog order (CPU before GPU).
 */

export type PlotReadinessInput = {
  isLoading: boolean;
  item: unknown | null;
  unavailableReason: string | null | undefined;
};

/** 0 = ready, 1 = loading, 2 = unavailable / missing item when settled. */
export function plotReadinessRank(input: PlotReadinessInput): number {
  if (input.isLoading) return 1;
  if (input.unavailableReason || input.item == null) return 2;
  return 0;
}

/**
 * Stable sort: lower readiness rank first; ties keep original index order.
 */
export function sortByPlotReadiness<T>(
  items: readonly T[],
  getState: (item: T) => PlotReadinessInput,
): T[] {
  return items
    .map((item, index) => ({
      item,
      index,
      rank: plotReadinessRank(getState(item)),
    }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.item);
}
