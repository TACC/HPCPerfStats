import { useEffect, useRef, useState } from "react";
import { jobsHistogramsBatchRetrieve } from "@/api/generated/jobs/jobs";
import type { JobListHistogramBatchResponse } from "@/api/generated/models/jobListHistogramBatchResponse";
import { ApiError } from "@/api/api-error";
import { orvalResponseData } from "@/api/orval-response";
import { HISTOGRAM_EMBED_VERSION } from "@/api-paths";
import type { JobListHistogramEntry, MetricHistStatusMap } from "@/types/view-models";
import { normalizeJobListHistogramEntry } from "@/utils/normalize-job-list-histogram-entry";

export type MetricName = "runtime" | "nhosts" | "queue_wait";

export type JobListHistogramSampleMeta = {
  nj: number | null;
  histogramNj: number | null;
  histogramSampled: boolean;
};

/** Stable default metric list — do not pass inline arrays from views (refetch loop). */
export const JOB_LIST_HISTOGRAM_METRICS: readonly MetricName[] = [
  "runtime",
  "nhosts",
  "queue_wait",
];

const BATCH_METRICS_PARAM = JOB_LIST_HISTOGRAM_METRICS.join(",");

const NO_JOBS_MATCHED_MESSAGE = "No jobs matched this query.";

/** Debounce filter-driven histogram batch refetch so rapid chip toggles do not pile up. */
export const JOB_LIST_HISTOGRAM_DEBOUNCE_MS = 450;

const NO_JOBS_META: JobListHistogramSampleMeta = {
  nj: null,
  histogramNj: null,
  histogramSampled: false,
};

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

function batchErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail =
      typeof err.body.detail === "string" && err.body.detail.trim()
        ? err.body.detail.trim()
        : "";
    if (detail) return detail;
    return err.message;
  }
  if (err instanceof Error && err.message.trim()) return err.message;
  return "Failed to load histograms for this job list.";
}

/** Stable serialized key for effect dependencies (avoids object-identity refetch loops). */
function serializeJobListApiParams(params: Record<string, string>): string {
  const keys = Object.keys(params).sort();
  return keys.map((key) => `${key}=${params[key]}`).join("&");
}

/** Loads metric histogram embeds for the current job list filter params (single batch API). */
export function useJobListHistograms(
  listApiParams: Record<string, string>,
  reloadKey = 0,
  enabled = true,
  jobsFetching = false,
) {
  const paramsKey = serializeJobListApiParams(listApiParams);
  const listApiParamsRef = useRef(listApiParams);
  listApiParamsRef.current = listApiParams;

  const [histograms, setHistograms] = useState<JobListHistogramEntry[] | null>(null);
  const [metricHistStatus, setMetricHistStatus] = useState<MetricHistStatusMap>(() =>
    createInitialMetricStatus(false),
  );
  const [batchError, setBatchError] = useState<string | null>(null);
  const [sampleMeta, setSampleMeta] = useState<JobListHistogramSampleMeta>(NO_JOBS_META);
  const [histogramsUpdating, setHistogramsUpdating] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setHistograms(null);
      setMetricHistStatus(createInitialMetricStatus(false));
      setBatchError(null);
      setSampleMeta(NO_JOBS_META);
      setHistogramsUpdating(false);
      return;
    }

    // Keep previous embeds visible while filter-identity refetch runs (sort/page
    // must not reach this effect — callers pass stripPresentationParams keys).
    setMetricHistStatus((prev) => {
      const next = createInitialMetricStatus(true);
      for (const metric of JOB_LIST_HISTOGRAM_METRICS) {
        if (prev[metric]?.error) next[metric] = { loading: true, error: null };
      }
      return next;
    });
    setBatchError(null);
    setHistogramsUpdating(true);

    const controller = new AbortController();
    let debounceTimer: ReturnType<typeof setTimeout> | null = null;

    const loadHistograms = async () => {
      setHistogramsUpdating(true);
      try {
        const batchParams = {
          ...listApiParamsRef.current,
          metrics: BATCH_METRICS_PARAM,
          _histogram_embed_v: HISTOGRAM_EMBED_VERSION,
        };
        const batchEnvelope = await jobsHistogramsBatchRetrieve(batchParams, {
          signal: controller.signal,
        });
        const batchData = orvalResponseData<JobListHistogramBatchResponse>(batchEnvelope);
        if (controller.signal.aborted) return;

        const rows = Array.isArray(batchData?.histograms) ? batchData.histograms : [];
        const nextStatus = createInitialMetricStatus(false);
        const entries: JobListHistogramEntry[] = [];
        const loadedMetrics = new Set<MetricName>();

        setSampleMeta({
          nj: typeof batchData?.nj === "number" ? batchData.nj : null,
          histogramNj:
            typeof batchData?.histogram_nj === "number" ? batchData.histogram_nj : null,
          histogramSampled: batchData?.histogram_sampled === true,
        });

        if (batchData?.nj === 0 && rows.length === 0) {
          setMetricHistStatus(metricStatusFromBatchError(NO_JOBS_MATCHED_MESSAGE));
          setBatchError(NO_JOBS_MATCHED_MESSAGE);
          setHistograms(null);
          setHistogramsUpdating(false);
          return;
        }

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
          const hasPlot =
            entry.plot_item_thumb != null || entry.plot_item_full != null;
          const unavailableReason = entry.plot_unavailable_reason?.trim() || null;
          if (hasPlot) {
            nextStatus[metric] = { loading: false, error: null };
          } else if (unavailableReason) {
            nextStatus[metric] = { loading: false, error: unavailableReason };
          } else {
            nextStatus[metric] = { loading: false, error: null };
          }
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
        setBatchError(null);
        setHistogramsUpdating(false);
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = batchErrorMessage(err);
        console.warn("Failed to load job list histogram batch:", err);
        setMetricHistStatus(metricStatusFromBatchError(message));
        setBatchError(message);
        // Keep prior histograms on transient failure so enlarge/Close stay mounted.
        setSampleMeta(NO_JOBS_META);
        setHistogramsUpdating(false);
      }
    };

    debounceTimer = setTimeout(() => {
      if (jobsFetching) return;
      void loadHistograms();
    }, JOB_LIST_HISTOGRAM_DEBOUNCE_MS);

    return () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      controller.abort();
    };
  }, [paramsKey, reloadKey, enabled, jobsFetching]);

  return {
    histograms,
    metricHistStatus,
    batchError,
    sampleMeta,
    histogramsUpdating,
    setMetricHistStatus,
  };
}
