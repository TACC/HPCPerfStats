import { useCallback, useEffect, useRef, useState } from "react";
import { jobsPlotsRetrieve } from "@/api/generated/jobs/jobs";
import type { JobPlotBatchResponse, JobPlotsState } from "@/types/view-models";
import {
  JOB_PLOT_CONFIGS,
  createEmptyJobPlotsState,
  jobPlotStatesEqual,
  mergeProgressiveJobPlotsState,
  plotsStateFromBatchResponse,
} from "@/utils/job-detail-plots";
import { scheduleJobPlotsRetry } from "@/utils/job-plots-polling";

/** Progressive job plots polling via Orval `jobsPlotsRetrieve`. */
export function useJobPlotsQuery(pk: string, enabled: boolean) {
  const [plots, setPlots] = useState<JobPlotsState | null>(null);
  const [plotsLoading, setPlotsLoading] = useState(true);
  const [plotsFetchFailed, setPlotsFetchFailed] = useState(false);
  const plotsFetchGenRef = useRef(0);

  const fetchAllJobPlotsWithPolling = useCallback(
    async (cancelledCheck: () => boolean): Promise<void> => {
      let keepLoading = false;
      try {
        const plotResponse = (await jobsPlotsRetrieve(pk, {
          progressive: "1",
        })) as unknown as JobPlotBatchResponse;
        if (cancelledCheck()) return;

        if (plotResponse?.status === "loading") {
          keepLoading = true;
          scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        if (plotResponse?.status === "partial" && plotResponse?.progressive) {
          keepLoading = true;
          setPlotsFetchFailed(false);
          setPlots((prev) => {
            const merged = mergeProgressiveJobPlotsState(prev, plotResponse);
            return jobPlotStatesEqual(prev, merged) ? prev : merged;
          });
          scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        if (
          plotResponse &&
          typeof plotResponse === "object" &&
          Object.hasOwn(plotResponse, "mplot_item")
        ) {
          setPlotsFetchFailed(false);
          setPlots((prev) => {
            const next = plotsStateFromBatchResponse(plotResponse);
            return jobPlotStatesEqual(prev, next) ? prev : next;
          });
        } else {
          setPlots(createEmptyJobPlotsState(false));
        }
      } catch {
        if (cancelledCheck()) return;
        setPlotsFetchFailed(true);
        setPlots(createEmptyJobPlotsState(false));
      } finally {
        if (cancelledCheck() || keepLoading) return;
        setPlotsLoading(false);
      }
    },
    [pk],
  );

  const retryJobPlots = useCallback(() => {
    setPlotsFetchFailed(false);
    setPlotsLoading(true);
    setPlots(createEmptyJobPlotsState(true));
    plotsFetchGenRef.current += 1;
    const gen = plotsFetchGenRef.current;
    void fetchAllJobPlotsWithPolling(() => plotsFetchGenRef.current !== gen);
  }, [fetchAllJobPlotsWithPolling]);

  useEffect(() => {
    if (!pk || !enabled) return;

    let cancelled = false;
    const cancelledCheck = (): boolean => cancelled;

    setPlots(null);
    setPlotsLoading(true);
    setPlotsFetchFailed(false);
    setPlots(createEmptyJobPlotsState(true));
    void fetchAllJobPlotsWithPolling(cancelledCheck);

    return () => {
      cancelled = true;
    };
  }, [pk, enabled, fetchAllJobPlotsWithPolling]);

  useEffect(() => {
    if (!plots) return;
    const anyPlotReady = JOB_PLOT_CONFIGS.some(
      (config) => plots?.[config.key] && plots[config.key].loading === false,
    );
    if (anyPlotReady) setPlotsLoading(false);
  }, [plots]);

  return {
    plots,
    plotsLoading,
    plotsFetchFailed,
    retryJobPlots,
  };
}
