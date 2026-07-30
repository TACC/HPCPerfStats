/**
 * Map job-detail metrics_list rows to source subsections (CPU → GPU → File System → Network → Misc).
 * Grouping uses each row's API `type` (host_data / catalog lineage); unknown types fall through to Misc.
 */

export type JobMetricSourceSectionId =
  | "cpu"
  | "gpu"
  | "filesystem"
  | "network"
  | "misc";

export type JobMetricSourceSectionRow = {
  type?: string | null;
  metric: string;
  units?: string | null;
  value?: string | number | null;
  no_data_reason?: string | null;
};

export type JobMetricSourceSection<T extends JobMetricSourceSectionRow = JobMetricSourceSectionRow> = {
  id: JobMetricSourceSectionId;
  label: string;
  rows: T[];
};

/** Display order for Metrics tab subsections. */
export const JOB_METRIC_SOURCE_SECTION_ORDER: readonly JobMetricSourceSectionId[] = [
  "cpu",
  "gpu",
  "filesystem",
  "network",
  "misc",
] as const;

export const JOB_METRIC_SOURCE_SECTION_LABELS: Record<JobMetricSourceSectionId, string> = {
  cpu: "CPU",
  gpu: "GPU",
  filesystem: "File System",
  network: "Network",
  misc: "Misc",
};

/** Exact catalog / monitor type → section (non-prefix). */
const EXACT_TYPE_SECTION: Record<string, JobMetricSourceSectionId> = {
  host_cpu: "cpu",
  host_cpu_hw: "cpu",
  host_mem: "cpu",
  host_numa: "cpu",
  cpu: "cpu",
  pmc: "cpu",
  imc: "cpu",
  amd_x86_pmc: "cpu",
  intel_8pmc3: "cpu",
  intel_4pmc3: "cpu",
  arm_aarch64_imc: "cpu",
  arm_imc: "cpu",
  intel_x86_rapl: "cpu",
  amd_x86_rapl: "cpu",
  intel_rapl: "cpu",
  amd64_rapl: "cpu",
  intel_skx_cha: "cpu",
  amd64_df: "cpu",
  intel_snb_imc: "cpu",
  intel_ivb_imc: "cpu",
  intel_hsw_imc: "cpu",
  intel_bdw_imc: "cpu",
  intel_skx_imc: "cpu",
  intel_icx_imc: "cpu",
  intel_spr_imc: "cpu",
  nvidia_gpu: "gpu",
  amd_gpu: "gpu",
  intel_gpu: "gpu",
  gpu: "gpu",
  lustre_llite: "filesystem",
  llite: "filesystem",
  nfs: "filesystem",
  host_nfs: "filesystem",
  host_block: "filesystem",
  beegfs_client: "filesystem",
  host_ib: "network",
  host_opa: "network",
  host_net: "network",
  net: "network",
  host_lnet: "network",
  lnet: "network",
  job: "misc",
};

const CPU_TYPE_PREFIXES = [
  "intel_x86_pmc_",
  "intel_x86_uncore_imc_",
  "amd_x86_uncore_df",
  "intel_x86_uncore_cha_",
] as const;

function sectionFromMetricNameFallback(metric: string): JobMetricSourceSectionId | null {
  if (metric.startsWith("detail_gpu_") || metric === "avg_gpuutil") return "gpu";
  if (metric.startsWith("detail_fsio_")) return "filesystem";
  if (
    metric.startsWith("avg_vector_width_") ||
    metric === "avg_vector_width_combined" ||
    metric.startsWith("vecpercent_")
  ) {
    return "cpu";
  }
  if (metric === "job_cpu_gpu_watt_hours" || metric.endsWith("_node_power_est_w")) {
    return "misc";
  }
  return null;
}

/** Resolve a single metrics_list row to a subsection id. */
export function jobMetricSourceSectionId(
  row: Pick<JobMetricSourceSectionRow, "type" | "metric">,
): JobMetricSourceSectionId {
  const rawType = row.type == null ? "" : String(row.type).trim();
  if (rawType) {
    const exact = EXACT_TYPE_SECTION[rawType];
    if (exact) return exact;
    for (const prefix of CPU_TYPE_PREFIXES) {
      if (rawType.startsWith(prefix)) return "cpu";
    }
  }
  const fromMetric = sectionFromMetricNameFallback(row.metric);
  if (fromMetric) return fromMetric;
  if (rawType) return "misc";
  return "misc";
}

/**
 * Group metrics into ordered subsections.
 * Always includes Network (possibly empty). Omits empty GPU / File System / Misc.
 * Always includes CPU when it has rows; omits empty CPU as well (same as GPU/FS/Misc).
 */
export function groupJobMetricsBySourceSection<T extends JobMetricSourceSectionRow>(
  metrics: readonly T[],
): JobMetricSourceSection<T>[] {
  const buckets: Record<JobMetricSourceSectionId, T[]> = {
    cpu: [],
    gpu: [],
    filesystem: [],
    network: [],
    misc: [],
  };
  for (const row of metrics) {
    buckets[jobMetricSourceSectionId(row)].push(row);
  }

  const out: JobMetricSourceSection<T>[] = [];
  for (const id of JOB_METRIC_SOURCE_SECTION_ORDER) {
    const rows = buckets[id];
    if (id === "network" || rows.length > 0) {
      out.push({
        id,
        label: JOB_METRIC_SOURCE_SECTION_LABELS[id],
        rows,
      });
    }
  }
  return out;
}
