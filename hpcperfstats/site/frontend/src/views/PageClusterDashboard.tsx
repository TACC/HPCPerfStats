"use client";

import { useId, useMemo, useState } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LazyExpansionHistogram from "@/components/LazyExpansionHistogram";
import LoadingMessage from "../components/LoadingMessage";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePubDashboard } from "../hooks/use-pub-dashboard";
import type {
  PubDashboardBundle,
  PubDashboardExpansionFactorSection,
  PubDashboardHistogramBlock,
  PubDashboardHistogramMap,
} from "../types/view-models";
import { useDocumentTitle } from "../utils/useDocumentTitle";

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

function asExpansionFactorSection(value: unknown): PubDashboardExpansionFactorSection & {
  monthly_period_keys?: string[];
  yearly_period_keys?: string[];
} {
  if (!isRecord(value)) return {};
  return {
    monthly_daily_histograms: asHistogramMap(value.monthly_daily_histograms),
    yearly_weekly_histograms: asHistogramMap(value.yearly_weekly_histograms),
    monthly_period_keys: Array.isArray(value.monthly_period_keys)
      ? value.monthly_period_keys.filter((k): k is string => typeof k === "string")
      : undefined,
    yearly_period_keys: Array.isArray(value.yearly_period_keys)
      ? value.yearly_period_keys.filter((k): k is string => typeof k === "string")
      : undefined,
  };
}

function periodKeysForGrouping(
  efSection: ReturnType<typeof asExpansionFactorSection>,
  grouping: "yearly" | "monthly",
): string[] {
  if (grouping === "yearly") {
    if (efSection.yearly_period_keys?.length) {
      return [...efSection.yearly_period_keys].sort().reverse();
    }
    return Object.keys(efSection.yearly_weekly_histograms ?? {}).sort().reverse();
  }
  if (efSection.monthly_period_keys?.length) {
    return [...efSection.monthly_period_keys].sort().reverse();
  }
  return Object.keys(efSection.monthly_daily_histograms ?? {}).sort().reverse();
}

function inlineBlockForPeriod(
  efSection: ReturnType<typeof asExpansionFactorSection>,
  grouping: "yearly" | "monthly",
  periodKey: string,
): PubDashboardHistogramBlock | null {
  const map =
    grouping === "yearly"
      ? efSection.yearly_weekly_histograms
      : efSection.monthly_daily_histograms;
  const block = map?.[periodKey];
  return block ?? null;
}

function SectionExpansionFactor({ bundle }: SectionExpansionFactorProps) {
  const sections = isRecord(bundle.sections) ? bundle.sections : {};
  const efSection = asExpansionFactorSection(sections.expansion_factor);
  const panelIntroId = useId();
  const [activeGrouping, setActiveGrouping] = useState<"yearly" | "monthly">("yearly");

  const yearlyKeys = useMemo(
    () => periodKeysForGrouping(efSection, "yearly"),
    [efSection],
  );
  const monthlyKeys = useMemo(
    () => periodKeysForGrouping(efSection, "monthly"),
    [efSection],
  );

  const renderHistList = (
    keys: string[],
    grouping: "yearly" | "monthly",
    axisHint: string,
    histogramCaption: string,
  ) => {
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
        {keys.map((periodKey) => (
          <LazyExpansionHistogram
            key={`${grouping}-${periodKey}`}
            grouping={grouping}
            periodKey={periodKey}
            histogramCaption={histogramCaption}
            initialBlock={inlineBlockForPeriod(efSection, grouping, periodKey)}
          />
        ))}
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
          <Button
            type="button"
            variant="link"
            className="mb-0 h-auto p-0 text-xl font-semibold underline"
            onClick={() => setActiveGrouping("monthly")}
          >
            Monthly
          </Button>
        </div>
        {activeGrouping === "yearly"
          ? renderHistList(
              yearlyKeys,
              "yearly",
              "Each chart lists calendar years; buckets are ISO weeks inside that year.",
              "Histogram of weekly mean expansion factor.",
            )
          : null}
      </div>

      <hr className="my-5 border-2 opacity-50" />

      <div id="pub-dashboard-monthly">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 border-b pb-2">
          <Button
            type="button"
            variant="link"
            className="mb-0 h-auto p-0 text-xl font-semibold underline"
            onClick={() => setActiveGrouping("yearly")}
          >
            Yearly
          </Button>
          <h3 className="mb-0 text-xl font-semibold">Monthly</h3>
        </div>
        {activeGrouping === "monthly"
          ? renderHistList(
              monthlyKeys,
              "monthly",
              "Each chart lists completed-job calendar months.",
              "Histogram of daily mean expansion factor.",
            )
          : null}
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
  const { initialLoading, refetchBusy, bundle, error: loadError } = usePubDashboard();
  const error = loadError || null;
  const typedBundle =
    bundle && typeof bundle === "object" ? (bundle as PubDashboardBundle) : null;

  return (
    <div className="container mx-auto px-4 py-4">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Dashboard</h1>
      </header>

      {error ? <BannerErrorMessage message={error} /> : null}

      {initialLoading ? (
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
        <div className={refetchBusy ? "opacity-55" : undefined} aria-busy={refetchBusy || undefined}>
          {refetchBusy ? (
            <p className="mb-2 text-sm text-muted-foreground" role="status" aria-live="polite">
              Updating dashboard…
            </p>
          ) : null}
          <ClusterDashboardTabs bundle={typedBundle} />
        </div>
      )}
    </div>
  );
}
