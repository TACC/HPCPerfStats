import { useEffect, useRef, useState } from "react";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation";
import LoadingMessage from "../components/LoadingMessage";
import { fetchPubExpansionPeriod } from "@/api/fetch-mutator";
import type { PubDashboardHistogramBlock } from "../types/view-models";
import { formatDecimalStandard } from "../utils/formatDecimal";

type LazyExpansionHistogramProps = {
  grouping: "yearly" | "monthly";
  periodKey: string;
  histogramCaption: string;
  /** Preloaded block (tests / legacy full bundle). */
  initialBlock?: PubDashboardHistogramBlock | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function asHistogramBlock(value: unknown): PubDashboardHistogramBlock | null {
  if (!isRecord(value)) return null;
  return value as PubDashboardHistogramBlock;
}

export default function LazyExpansionHistogram({
  grouping,
  periodKey,
  histogramCaption,
  initialBlock = null,
}: LazyExpansionHistogramProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [shouldLoad, setShouldLoad] = useState(Boolean(initialBlock));
  const [block, setBlock] = useState<PubDashboardHistogramBlock | null>(initialBlock);
  const [loadError, setLoadError] = useState<string | null>(null);
  const safeDomId = String(periodKey).replace(/[^a-zA-Z0-9_-]+/g, "-");

  useEffect(() => {
    if (initialBlock || shouldLoad) return;
    const el = containerRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setShouldLoad(true);
          observer.disconnect();
        }
      },
      { rootMargin: "120px 0px", threshold: 0.01 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [initialBlock, shouldLoad]);

  useEffect(() => {
    if (!shouldLoad || block || initialBlock) return;
    let cancelled = false;
    void fetchPubExpansionPeriod<{ block?: unknown }>(grouping, periodKey)
      .then((payload) => {
        if (cancelled) return;
        setBlock(asHistogramBlock(payload?.block));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Unable to load histogram.");
      });
    return () => {
      cancelled = true;
    };
  }, [shouldLoad, block, initialBlock, grouping, periodKey]);

  const edges = Array.isArray(block?.histogram_bin_edges) ? block!.histogram_bin_edges! : [];
  const counts = Array.isArray(block?.histogram_counts) ? block!.histogram_counts! : [];
  const maxCount = counts.length
    ? Math.max(1, ...counts.map((c: unknown) => Number(c) || 0))
    : 1;

  return (
    <div ref={containerRef} className="mb-4 rounded-lg border bg-muted/40 p-3">
      <div className="mb-2 font-semibold">{periodKey}</div>
      <div className="mb-2 text-sm text-muted-foreground">
        definition: {block?.expansion_factor_definition || "—"}
      </div>
      {!block && !loadError ? (
        <LoadingMessage message={`Loading ${periodKey}…`} />
      ) : null}
      {loadError ? <p className="text-sm text-destructive">{loadError}</p> : null}
      {block?.bokeh_histogram_json_item ? (
        <div className="mb-3">
          <BokehPlotWithLimitation
            item={block.bokeh_histogram_json_item}
            id={`pub-expansion-factor-${grouping}-${safeDomId}`}
            plotName={`Expansion factor histogram for ${periodKey}`}
            embedAriaLabel={`Expansion factor histogram for ${periodKey}`}
            embedMinHeightPx={320}
          />
        </div>
      ) : null}
      {block && !block.bokeh_histogram_json_item ? (
        <div className="flex flex-col gap-1">
          {counts.map((cntRaw: unknown, idx: number) => {
            const lo = edges[idx];
            const hi = idx + 1 < edges.length ? edges[idx + 1] : null;
            const cnt = Number(cntRaw) || 0;
            const widthPct = (cnt / maxCount) * 100;
            const labelRight =
              hi !== null
                ? `[${formatDecimalStandard(lo)}, ${formatDecimalStandard(hi)})`
                : `≥ ${formatDecimalStandard(lo)}`;
            return (
              <div key={`${periodKey}-${idx}`} className="flex items-center gap-2">
                <div className="w-56 shrink-0 text-sm">{labelRight}</div>
                <div className="min-w-0 flex-1">
                  <div
                    className="h-[1.1rem] overflow-hidden rounded-full bg-muted"
                    role="progressbar"
                    aria-valuenow={cnt}
                    aria-valuemin={0}
                    aria-valuemax={maxCount}
                    aria-label={`${labelRight}: ${cnt} jobs`}
                  >
                    <div
                      className="h-full rounded-full bg-primary/70"
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                </div>
                <div className="w-12 shrink-0 text-end text-sm">{cnt}</div>
              </div>
            );
          })}
        </div>
      ) : null}
      {!block?.bokeh_histogram_json_item && block ? (
        <p className="mb-0 text-sm text-muted-foreground">{histogramCaption}</p>
      ) : null}
    </div>
  );
}
