import { useCallback, useEffect, useRef, useState } from "react";
import { jobsPlotsRetrieve } from "@/api/generated/jobs/jobs";
import { orvalResponseData } from "@/api/orval-response";
import type { JobPlotBatchResponse, JobPlotsState } from "@/types/view-models";
import {
  JOB_PLOT_CONFIGS,
  clearJobPlotsLoadingFlags,
  createEmptyJobPlotsState,
  jobPlotStatesEqual,
  mergeProgressiveJobPlotsState,
  plotsStateFromBatchResponse,
} from "@/utils/job-detail-plots";
import {
  JOB_PLOTS_MAX_PROGRESSIVE_ATTEMPTS,
  scheduleJobPlotsRetry,
} from "@/utils/job-plots-polling";

/** Progressive job plots polling via Orval `jobsPlotsRetrieve`. */
export function useJobPlotsQuery(pk: string, enabled: boolean) {
  const [plots, setPlots] = useState<JobPlotsState | null>(null);
  const [plotsLoading, setPlotsLoading] = useState(true);
  const [plotsFetchFailed, setPlotsFetchFailed] = useState(false);
  const plotsFetchGenRef = useRef(0);
  const plotsRetryCancelRef = useRef<(() => void) | null>(null);
  const progressiveAttemptsRef = useRef(0);
  const prevPkRef = useRef<string>("");

  const failClosedProgressive = useCallback(() => {
    plotsRetryCancelRef.current?.();
    plotsRetryCancelRef.current = null;
    setPlotsFetchFailed(true);
    setPlotsLoading(false);
    setPlots((prev) => clearJobPlotsLoadingFlags(prev));
  }, []);

  const fetchAllJobPlotsWithPolling = useCallback(
    async (cancelledCheck: () => boolean): Promise<void> => {
      let keepLoading = false;
      try {
        progressiveAttemptsRef.current += 1;
        if (progressiveAttemptsRef.current > JOB_PLOTS_MAX_PROGRESSIVE_ATTEMPTS) {
          failClosedProgressive();
          return;
        }

        const plotEnvelope = await jobsPlotsRetrieve(pk, {
          progressive: "1",
        });
        const plotResponse = orvalResponseData(plotEnvelope) as JobPlotBatchResponse | undefined;
        if (cancelledCheck()) return;

        if (plotResponse?.status === "loading") {
          keepLoading = true;
          plotsRetryCancelRef.current?.();
          plotsRetryCancelRef.current = scheduleJobPlotsRetry(
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
          plotsRetryCancelRef.current?.();
          plotsRetryCancelRef.current = scheduleJobPlotsRetry(
            () => fetchAllJobPlotsWithPolling(cancelledCheck),
            plotResponse.retry_after_seconds,
            cancelledCheck,
          );
          return;
        }

        progressiveAttemptsRef.current = 0;
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
        if (!cancelledCheck() && !keepLoading) {
          setPlotsLoading(false);
        }
      }
    },
    [pk, failClosedProgressive],
  );

  const retryJobPlots = useCallback(() => {
    setPlotsFetchFailed(false);
    setPlotsLoading(true);
    setPlots(createEmptyJobPlotsState(true));
    progressiveAttemptsRef.current = 0;
    plotsFetchGenRef.current += 1;
    const gen = plotsFetchGenRef.current;
    void fetchAllJobPlotsWithPolling(() => plotsFetchGenRef.current !== gen);
  }, [fetchAllJobPlotsWithPolling]);

  useEffect(() => {
    if (!pk) {
      setPlots(null);
      setPlotsLoading(false);
      setPlotsFetchFailed(false);
      prevPkRef.current = "";
      progressiveAttemptsRef.current = 0;
      plotsRetryCancelRef.current?.();
      plotsRetryCancelRef.current = null;
      return;
    }

    if (!enabled) {
      plotsRetryCancelRef.current?.();
      plotsRetryCancelRef.current = null;
      return;
    }

    const pkChanged = prevPkRef.current !== pk;
    prevPkRef.current = pk;

    let cancelled = false;
    const cancelledCheck = (): boolean => cancelled;

    if (pkChanged) {
      setPlots(createEmptyJobPlotsState(true));
      setPlotsLoading(true);
      setPlotsFetchFailed(false);
      progressiveAttemptsRef.current = 0;
    }

    void fetchAllJobPlotsWithPolling(cancelledCheck);

    return () => {
      cancelled = true;
      plotsRetryCancelRef.current?.();
      plotsRetryCancelRef.current = null;
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
