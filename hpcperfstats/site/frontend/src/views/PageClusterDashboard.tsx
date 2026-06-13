import { useId, useState } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehPlotWithLimitation from "../components/BokehPlotWithLimitation";
import LoadingMessage from "../components/LoadingMessage";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePubDashboardBundle } from "../pub-dashboard-bundle-context";
import type {
  PubDashboardBundle,
  PubDashboardExpansionFactorSection,
  PubDashboardHistogramBlock,
  PubDashboardHistogramMap,
} from "../types/view-models";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { formatDecimalStandard } from "../utils/formatDecimal";

const CLUSTER_DASH_TAB_EXPANSION = "expansion-factors";

type SectionExpansionFactorProps = {
  bundle: PubDashboardBundle;
};

type ClusterDashboardTabsProps = {
  bundle: PubDashboardBundle;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function asHistogramMap(value: unknown): PubDashboardHistogramMap {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, rawBlock]) => [
      key,
      (isRecord(rawBlock) ? rawBlock : {}) as PubDashboardHistogramBlock,
    ]),
  );
}

function asExpansionFactorSection(value: unknown): PubDashboardExpansionFactorSection {
  if (!isRecord(value)) return {};
  return {
    monthly_daily_histograms: asHistogramMap(value.monthly_daily_histograms),
    yearly_weekly_histograms: asHistogramMap(value.yearly_weekly_histograms),
  };
}

function SectionExpansionFactor({ bundle }: SectionExpansionFactorProps) {
  const sections = isRecord(bundle.sections) ? bundle.sections : {};
  const efSection = asExpansionFactorSection(sections.expansion_factor);
  const monthly = efSection.monthly_daily_histograms ?? {};
  const yearly = efSection.yearly_weekly_histograms ?? {};
  const panelIntroId = useId();

  const renderHist = (
    payloadMap: PubDashboardHistogramMap,
    axisHint: string,
    groupingKey: string,
    histogramCaption: string,
  ) => {
    const keys = Object.keys(payloadMap).sort().reverse();
    if (!keys.length) {
      return (
        <p className="mb-0 text-muted-foreground">
          No histogram data for this grouping yet.
        </p>
      );
    }
    return (
      <div className="mb-4">
        <p className="mb-2 text-sm text-muted-foreground">{histogramCaption}</p>
        <p className="mb-3 text-sm text-muted-foreground">{axisHint}</p>
        {keys.map((k) => {
          const block = payloadMap[k];
          const safeDomId = String(k).replace(/[^a-zA-Z0-9_-]+/g, "-");
          const edges = Array.isArray(block.histogram_bin_edges)
            ? block.histogram_bin_edges
            : [];
          const counts = Array.isArray(block.histogram_counts) ? block.histogram_counts : [];
          const maxCount = counts.length
            ? Math.max(1, ...counts.map((c: unknown) => Number(c) || 0))
            : 1;
          return (
            <div key={k} className="mb-4 rounded-lg border bg-muted/40 p-3">
              <div className="mb-2 font-semibold">{k}</div>
              <div className="mb-2 text-sm text-muted-foreground">
                definition: {block.expansion_factor_definition || "—"}
              </div>
              {block.bokeh_histogram_json_item ? (
                <div className="mb-3">
                  <BokehPlotWithLimitation
                    item={block.bokeh_histogram_json_item}
                    id={`pub-expansion-factor-${groupingKey}-${safeDomId}`}
                    plotName={`Expansion factor histogram for ${k}`}
                    embedAriaLabel={`Expansion factor histogram for ${k}`}
                    embedMinHeightPx={320}
                  />
                </div>
              ) : null}
              {!block.bokeh_histogram_json_item ? (
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
                      <div key={`${k}-${idx}`} className="flex items-center gap-2">
                        <div className="w-56 shrink-0 text-sm">
                          {labelRight}
                        </div>
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
                        <div className="w-12 shrink-0 text-end text-sm">
                          {cnt}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <section aria-labelledby={panelIntroId}>
      <p id={panelIntroId} className="text-muted-foreground">
        Scheduler-centric expansion factor aggregates precomputed offline from accounting timestamps.
        Yearly views summarize distributions of{" "}
        <strong>weekly mean expansion factor</strong> values (ISO weeks). Monthly views summarize
        distributions of <strong>daily mean expansion factor</strong> values.
      </p>

      <div id="pub-dashboard-yearly" className="pt-2">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 border-b pb-2">
          <h3 className="mb-0 text-xl font-semibold">Yearly</h3>
          <a href="#pub-dashboard-monthly" className="mb-0 text-xl font-semibold underline">
            Monthly
          </a>
        </div>
        {renderHist(
          yearly,
          "Each chart lists calendar years; buckets are ISO weeks inside that year.",
          "year",
          "Histogram of weekly mean expansion factor.",
        )}
      </div>

      <hr className="my-5 border-2 opacity-50" />

      <div id="pub-dashboard-monthly">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 border-b pb-2">
          <a href="#pub-dashboard-yearly" className="mb-0 text-xl font-semibold underline">
            Yearly
          </a>
          <h3 className="mb-0 text-xl font-semibold">Monthly</h3>
        </div>
        {renderHist(
          monthly,
          "Each chart lists completed-job calendar months.",
          "month",
          "Histogram of daily mean expansion factor.",
        )}
      </div>
    </section>
  );
}

function ClusterDashboardTabs({ bundle }: ClusterDashboardTabsProps) {
  const [activeTab, setActiveTab] = useState(CLUSTER_DASH_TAB_EXPANSION);

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab}>
      <TabsList variant="line" className="mb-0 w-full justify-start rounded-none border-b bg-transparent p-0">
        <TabsTrigger value={CLUSTER_DASH_TAB_EXPANSION} className="rounded-none">
          Expansion factors
        </TabsTrigger>
      </TabsList>
      <TabsContent
        value={CLUSTER_DASH_TAB_EXPANSION}
        className="rounded-b-lg border border-t-0 bg-background p-3 md:p-4"
      >
        <SectionExpansionFactor bundle={bundle} />
      </TabsContent>
    </Tabs>
  );
}

export default function PageClusterDashboard() {
  useDocumentTitle("Cluster dashboard — public");
  const { loading, bundle, error: loadError } = usePubDashboardBundle();
  const error = loadError || null;
  const typedBundle =
    bundle && typeof bundle === "object" ? (bundle as PubDashboardBundle) : null;

  return (
    <div className="container mx-auto px-4 py-4">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Dashboard</h1>
      </header>

      {error ? <BannerErrorMessage message={error} /> : null}

      {loading ? (
        <LoadingMessage message="Loading cluster dashboard…" />
      ) : typedBundle == null ? null : typedBundle.status !== "ready" ? (
        <Alert role="status" className="border-sky-200 bg-sky-50 text-sky-950 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-100">
          <AlertTitle>Dashboard warming</AlertTitle>
          <AlertDescription className="text-sm">
            {typedBundle.detail || "metrics_not_ready"} —{" "}
            {typedBundle.retry_hint || "check_back_later"}
          </AlertDescription>
        </Alert>
      ) : (
        <ClusterDashboardTabs bundle={typedBundle} />
      )}
    </div>
  );
}
