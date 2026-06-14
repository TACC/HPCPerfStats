import { useEffect, useState } from "react";
import { jobsHistogramsRetrieve } from "@/api/generated/jobs/jobs";
import { HISTOGRAM_EMBED_VERSION } from "@/api-paths";
import type { JobListHistogramEntry, MetricHistStatusMap } from "@/types/view-models";
import { normalizeJobListHistogramEntry } from "@/utils/normalize-job-list-histogram-entry";

export type MetricName = "runtime" | "nhosts" | "queue_wait";

/** Stable default metric list — do not pass inline arrays from views (refetch loop). */
export const JOB_LIST_HISTOGRAM_METRICS: readonly MetricName[] = [
  "runtime",
  "nhosts",
  "queue_wait",
];

function createInitialMetricStatus(
  loading: boolean,
): MetricHistStatusMap {
  return JOB_LIST_HISTOGRAM_METRICS.reduce<MetricHistStatusMap>((acc, metric) => {
    acc[metric] = { loading, error: null };
    return acc;
  }, {});
}

type MetricLoadResult = {
  metric: MetricName;
  entry: JobListHistogramEntry | null;
  error: string | null;
};

async function loadHistogramMetric(
  metric: MetricName,
  params: Record<string, string>,
  signal: AbortSignal,
): Promise<MetricLoadResult> {
  try {
    const histParams = {
      ...params,
      group: "metric",
      metric,
      _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
    };
    const metricData = await jobsHistogramsRetrieve(histParams, undefined, signal);
    if (!metricData) {
      return { metric, entry: null, error: null };
    }
    return {
      metric,
      entry: normalizeJobListHistogramEntry(
        metricData as Parameters<typeof normalizeJobListHistogramEntry>[0],
        metric,
      ),
      error: null,
    };
  } catch (err) {
    const message =
      err instanceof Error
        ? err.message
        : `Failed to load ${metric} histogram for this job list.`;
    console.warn(`Failed to load job list histogram for metric '${metric}':`, err);
    return { metric, entry: null, error: message };
  }
}

function metricStatusFromResults(results: MetricLoadResult[]): MetricHistStatusMap {
  return results.reduce<MetricHistStatusMap>((acc, { metric, error }) => {
    acc[metric] = { loading: false, error };
    return acc;
  }, createInitialMetricStatus(false));
}

/** Loads metric histogram embeds for the current job list filter params. */
export function useJobListHistograms(
  listApiParams: Record<string, string>,
  reloadKey = 0,
  enabled = true,
) {
  const [histograms, setHistograms] = useState<JobListHistogramEntry[] | null>(null);
  const [metricHistStatus, setMetricHistStatus] = useState<MetricHistStatusMap>(() =>
    createInitialMetricStatus(false),
  );

  useEffect(() => {
    if (!enabled) {
      setHistograms(null);
      setMetricHistStatus(createInitialMetricStatus(false));
      return;
    }

    const controller = new AbortController();
    setHistograms(null);
    setMetricHistStatus(createInitialMetricStatus(true));

    const loadHistograms = async () => {
      const results = await Promise.all(
        JOB_LIST_HISTOGRAM_METRICS.map((metric) =>
          loadHistogramMetric(metric, listApiParams, controller.signal),
        ),
      );
      if (controller.signal.aborted) return;
      setMetricHistStatus(metricStatusFromResults(results));
      setHistograms(
        results
          .map((result) => result.entry)
          .filter((entry): entry is JobListHistogramEntry => entry != null),
      );
    };

    void loadHistograms();
    return () => controller.abort();
  }, [listApiParams, reloadKey, enabled]);

  return { histograms, metricHistStatus, setMetricHistStatus };
}
