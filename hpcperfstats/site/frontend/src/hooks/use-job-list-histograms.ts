import { useEffect, useState } from "react";
import { jobsHistogramsBatchRetrieve } from "@/api/generated/jobs/jobs";
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

const BATCH_METRICS_PARAM = JOB_LIST_HISTOGRAM_METRICS.join(",");

function createInitialMetricStatus(
  loading: boolean,
): MetricHistStatusMap {
  return JOB_LIST_HISTOGRAM_METRICS.reduce<MetricHistStatusMap>((acc, metric) => {
    acc[metric] = { loading, error: null };
    return acc;
  }, {});
}

function metricStatusFromBatchError(message: string): MetricHistStatusMap {
  return JOB_LIST_HISTOGRAM_METRICS.reduce<MetricHistStatusMap>((acc, metric) => {
    acc[metric] = { loading: false, error: message };
    return acc;
  }, {});
}

/** Loads metric histogram embeds for the current job list filter params (single batch API). */
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
      try {
        const batchParams = {
          ...listApiParams,
          metrics: BATCH_METRICS_PARAM,
          _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
        };
        const batchData = await jobsHistogramsBatchRetrieve(
          batchParams,
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;

        const rows = Array.isArray(batchData?.histograms) ? batchData.histograms : [];
        const nextStatus = createInitialMetricStatus(false);
        const entries: JobListHistogramEntry[] = [];
        const loadedMetrics = new Set<MetricName>();

        for (const row of rows) {
          const metric = String(row?.metric || "") as MetricName;
          if (!JOB_LIST_HISTOGRAM_METRICS.includes(metric)) continue;
          const entry = normalizeJobListHistogramEntry(
            row as Parameters<typeof normalizeJobListHistogramEntry>[0],
            metric,
          );
          if (!entry) continue;
          entries.push(entry);
          loadedMetrics.add(metric);
          nextStatus[metric] = { loading: false, error: null };
        }

        for (const metric of JOB_LIST_HISTOGRAM_METRICS) {
          if (!loadedMetrics.has(metric)) {
            nextStatus[metric] = {
              loading: false,
              error: `No histogram data for ${metric}.`,
            };
          }
        }

        setMetricHistStatus(nextStatus);
        setHistograms(entries.length ? entries : null);
      } catch (err) {
        if (controller.signal.aborted) return;
        const message =
          err instanceof Error
            ? err.message
            : "Failed to load histograms for this job list.";
        console.warn("Failed to load job list histogram batch:", err);
        setMetricHistStatus(metricStatusFromBatchError(message));
        setHistograms(null);
      }
    };

    void loadHistograms();
    return () => controller.abort();
  }, [listApiParams, reloadKey, enabled]);

  return { histograms, metricHistStatus, setMetricHistStatus };
}
