import { useEffect, useState } from "react";
import { fetchPubExpansionPeriod } from "@/api/fetch-mutator";
import type { PubDashboardHistogramBlock } from "@/types/view-models";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function asHistogramBlock(value: unknown): PubDashboardHistogramBlock | null {
  if (!isRecord(value)) return null;
  return value as PubDashboardHistogramBlock;
}

/** Lazy-load one pub expansion-factor histogram block (used by LazyExpansionHistogram). */
export function usePubExpansionPeriod(
  grouping: "yearly" | "monthly",
  periodKey: string,
  enabled: boolean,
  initialBlock: PubDashboardHistogramBlock | null = null,
) {
  const [block, setBlock] = useState<PubDashboardHistogramBlock | null>(initialBlock);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled || block || initialBlock) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void fetchPubExpansionPeriod<{ block?: unknown }>(grouping, periodKey)
      .then((payload) => {
        if (cancelled) return;
        setBlock(asHistogramBlock(payload?.block));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Unable to load histogram.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, block, initialBlock, grouping, periodKey]);

  return { block, loadError, loading };
}
