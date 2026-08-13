import type { BokehJsonItem } from "./bokeh";

export type FreshnessBucket = "ok" | "gt_10min" | "gt_hour" | "gt_day" | "gt_week";

export type AdminMonitorHostRow = {
  host: string;
  last_time?: string | null;
  age_bucket?: FreshnessBucket | string;
};

export type AdminMonitorSectionResponse = Record<string, unknown>;

export type AdminMonitorXaltStats = {
  total_jids?: number;
  jids_with_xalt_data?: number;
  jids_missing_xalt_data?: number;
  jids?: string[];
  error?: string;
  missing_jids?: string[];
  missing_jids_truncated?: boolean;
  missing_jids_limit?: number;
  found_jids?: string[];
  found_jids_truncated?: boolean;
  found_jids_limit?: number;
};

export type AdminMonitorTelemetryZeroEvent = {
  type: string;
  event: string;
  row_count: number;
};

export type AdminMonitorTelemetryHealth = {
  window_hours?: number;
  computed_at?: string;
  timed_out?: boolean;
  error?: string | null;
  all_zero_events?: AdminMonitorTelemetryZeroEvent[];
  missing_core_types?: string[];
  truncated?: boolean;
  ok_summary?: {
    nonzero_type_event_pairs?: number;
    scanned_note?: string;
    hosts_sampled?: number;
  };
};

export type JobMetricCell = {
  value?: string | number | null;
  no_data_reason?: string | null;
};

export type JobPlotStateEntry = {
  loading: boolean;
  plotItem: BokehJsonItem | null;
  unavailableReason: string | null;
  /** GPU roofline measured-axis mode from ``grplot_bw_axis``. */
  bwAxis?: "memory_bw" | "pcie_nvlink" | null;
};

export type JobPlotsState = Record<string, JobPlotStateEntry>;

export type JobPlotBatchResponse = Record<string, unknown> & {
  status?: string;
  progressive?: boolean;
  retry_after_seconds?: number;
  loading_plots?: string[];
};

export type JobListHistogramEntry = {
  title?: string;
  plot_item_thumb?: BokehJsonItem | null;
  plot_item_full?: BokehJsonItem | null;
  plot_unavailable_reason?: string | null;
};

export type MetricHistStatusEntry = {
  loading: boolean;
  error: string | null;
};

export type MetricHistStatusMap = Record<string, MetricHistStatusEntry>;

export type JobMonitorRow = Record<string, unknown>;

export type HostDetailData = Record<string, unknown>;

export type TypeDetailData = Record<string, unknown> & {
  type_name?: string;
  values?: unknown[];
};

export type PubDashboardHistogramBlock = {
  expansion_factor_definition?: string;
  histogram_bin_edges?: unknown[];
  histogram_counts?: unknown[];
  bokeh_histogram_json_item?: BokehJsonItem | null;
};

export type PubDashboardHistogramMap = Record<string, PubDashboardHistogramBlock>;

export type PubDashboardExpansionFactorSection = {
  monthly_daily_histograms?: PubDashboardHistogramMap;
  yearly_weekly_histograms?: PubDashboardHistogramMap;
};

export type PubDashboardBundle = Record<string, unknown> & {
  status?: string;
  detail?: string;
  retry_hint?: string;
  sections?: {
    expansion_factor?: PubDashboardExpansionFactorSection;
  };
};
