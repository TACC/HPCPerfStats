import { useEffect, useRef, useState } from "react";
import { fetchPubExpansionPeriod } from "@/api/fetch-mutator";
import type { PubDashboardHistogramBlock } from "@/types/view-models";
import { fingerprintBokehJsonItem } from "@/utils/fingerprint-bokeh-json-item";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function asHistogramBlock(value: unknown): PubDashboardHistogramBlock | null {
  if (!isRecord(value)) return null;
  return value as PubDashboardHistogramBlock;
}

function fingerprintPubHistogramBlock(block: PubDashboardHistogramBlock | null): string {
  if (!block) return "";
  return [
    fingerprintBokehJsonItem(block.bokeh_histogram_json_item ?? null),
    JSON.stringify(block.histogram_bin_edges ?? null),
    JSON.stringify(block.histogram_counts ?? null),
    String(block.expansion_factor_definition ?? ""),
  ].join("\u001f");
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
  const fingerprintRef = useRef(fingerprintPubHistogramBlock(initialBlock));
  const initialFingerprint = fingerprintPubHistogramBlock(initialBlock);
  const hasInitialBlock = initialBlock != null;

  useEffect(() => {
    if (!initialBlock) return;
    if (initialFingerprint === fingerprintRef.current) return;
    fingerprintRef.current = initialFingerprint;
    setBlock(initialBlock);
  }, [initialBlock, initialFingerprint]);

  useEffect(() => {
    if (!enabled || block || hasInitialBlock) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void fetchPubExpansionPeriod<{ block?: unknown }>(grouping, periodKey)
      .then((payload) => {
        if (cancelled) return;
        const next = asHistogramBlock(payload?.block);
        fingerprintRef.current = fingerprintPubHistogramBlock(next);
        setBlock(next);
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
  }, [enabled, block, hasInitialBlock, grouping, periodKey]);

  return { block, loadError, loading };
}
