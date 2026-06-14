import { useEffect, useState } from "react";
import { jobsHistogramsRetrieve } from "@/api/generated/jobs/jobs";
import { HISTOGRAM_EMBED_VERSION } from "@/api-paths";
import type { JobListHistogramEntry, MetricHistStatusMap } from "@/types/view-models";
import { normalizeJobListHistogramEntry } from "@/utils/normalize-job-list-histogram-entry";

export type MetricName = "runtime" | "nhosts" | "queue_wait";

const DEFAULT_METRICS: MetricName[] = ["runtime", "nhosts", "queue_wait"];

function createInitialMetricStatus(metricNames: MetricName[]): MetricHistStatusMap {
  return metricNames.reduce<MetricHistStatusMap>((acc, metric) => {
    acc[metric] = { loading: false, error: null };
    return acc;
  }, {});
}

async function loadHistogramForMetric({
  metric,
  params,
  setMetricHistStatus,
  signal,
}: {
  metric: MetricName;
  params: Record<string, string>;
  setMetricHistStatus: React.Dispatch<React.SetStateAction<MetricHistStatusMap>>;
  signal: AbortSignal;
}): Promise<JobListHistogramEntry | null> {
  setMetricHistStatus((prev) => ({
    ...prev,
    [metric]: { loading: true, error: null },
  }));
  try {
    const histParams = {
      ...params,
      group: "metric",
      metric,
      _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
    };
    const metricData = await jobsHistogramsRetrieve(histParams, undefined, signal);
    if (!metricData) return null;
    setMetricHistStatus((prev) => ({
      ...prev,
      [metric]: { loading: false, error: null },
    }));
    return normalizeJobListHistogramEntry(
      metricData as Parameters<typeof normalizeJobListHistogramEntry>[0],
      metric,
    );
  } catch (err) {
    const message =
      err instanceof Error
        ? err.message
        : `Failed to load ${metric} histogram for this job list.`;
    console.warn(`Failed to load job list histogram for metric '${metric}':`, err);
    setMetricHistStatus((prev) => ({
      ...prev,
      [metric]: {
        loading: false,
        error: message,
      },
    }));
    return null;
  }
}

/** Loads metric histogram embeds for the current job list filter params. */
export function useJobListHistograms(
  listApiParams: Record<string, string>,
  reloadKey = 0,
  metricNames: MetricName[] = DEFAULT_METRICS,
) {
  const [histograms, setHistograms] = useState<JobListHistogramEntry[] | null>(null);
  const [metricHistStatus, setMetricHistStatus] = useState<MetricHistStatusMap>(() =>
    createInitialMetricStatus(metricNames),
  );

  useEffect(() => {
    const controller = new AbortController();
    setHistograms(null);
    setMetricHistStatus(createInitialMetricStatus(metricNames));

    const loadHistograms = async () => {
      const metricPromises = metricNames.map((metric) =>
        loadHistogramForMetric({
          metric,
          params: listApiParams,
          setMetricHistStatus,
          signal: controller.signal,
        }),
      );
      const metricResults = await Promise.all(metricPromises);
      if (controller.signal.aborted) return;
      setHistograms(
        metricResults.filter((entry): entry is JobListHistogramEntry => entry != null),
      );
    };

    void loadHistograms();
    return () => controller.abort();
  }, [listApiParams, reloadKey, metricNames]);

  return { histograms, metricHistStatus, setMetricHistStatus };
}
