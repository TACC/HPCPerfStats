# Architecture-agnostic analysis (metrics and plots)

This note summarizes how CPU/GPU vendors are handled in `hpcperfstats/analysis` and how to extend support.

## CPU (AMD, Intel, LIKWID)

- **`utils.utils`**: Logical `pmc` is chosen with `PMC_TYPENAME_PRIORITY` (AMD and 8/4-counter Intel before `cpu_counter_metrics`). IMC is the first entry in `INTEL_IMC_STATS_TYPES` present in the job schema. CHA uses `CHA_TYPENAME_PRIORITY`.
- **Summary plot**: Intel core metrics (`flops64b`, `flops32b`, `instr`, `mcycles`, `acycles`) try `intel_8pmc3`, then `intel_4pmc3`, then `cpu_counter_metrics`. Intel DRAM `mbw` tries every IMC type in `INTEL_IMC_STATS_TYPES` (same idea as roofline).
- **Roofline**: Intel FLOPS paths use `INTEL_CORE_PMC_TYPES_ORDERED` (includes `cpu_counter_metrics`). AMD needs `amd64_pmc` FLOPS plus `amd64_df` MBW channels (family 17h/19h when the monitor exposes them).
- **Heatmap CPI**: Candidate list includes Intel PMC types, `amd64_pmc`, and `cpu_counter_metrics`.

## GPU (NVIDIA, AMD)

- **`avg_gpuutil`**: Prefers `nvidia_gpu.utilization`, else `amd_gpu.gpu_util`. Catalog placeholder type is `gpu` (not vendor-specific).
- **DevPlot**: Uses `value` (not `arc`) for type-detail when `mem`, `nvidia_gpu`, or `amd_gpu` is in the type list.

## Vector / width metrics (`vecpercent_*`, `avg_vector_width_*`)

These need **per-event** FP counters (Intel FP_ARITH and/or legacy SSE names). Aggregate AMD `FLOPS` does not decompose by vector width; see class docstrings in `metrics.py`.

## New CPU types (e.g. NVIDIA Grace / Neoverse)

When the monitor adds new `host_data.type` values:

1. Add a nominal APERF/MPERF reference frequency to `_PMC_FREQ_BY_TYPENAME` in `gen/utils.py` if the type should act as PMC.
2. Append the typename to `PMC_TYPENAME_PRIORITY` in the desired precedence order.
3. Add summary/roofline/heatmap specs or event lists if the counter semantics match an existing path.
4. Add unit tests with mocked `get_aggregate_df` / job schemas.
