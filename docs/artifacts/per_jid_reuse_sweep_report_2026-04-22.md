# Per-JID Reuse Sweep Report (2026-04-22)

## Scope
- Implemented follow-up work for per-jid unified compute and additional DB-reuse optimizations.
- Captured compose-backed runtime telemetry with `HPCPERFSTATS_METRICS_TELEMETRY=1`.

## Implemented Wins

1. Per-jid stage telemetry and query-pressure proxies
- File: `hpcperfstats/analysis/metrics/update_metrics.py`
- Added opt-in telemetry with stage timing summaries in final scheduler log:
  - first-jid latency,
  - p50/p95 metrics stage latency,
  - p50/p95 prewarm stage latency,
  - query/reuse proxy counters from detail/plot paths.

2. Plot prewarm memo telemetry and batched row lookup accounting
- File: `hpcperfstats/site/machine/job_plot_artifacts.py`
- `_JtMemoProxy` now tracks host-time and aggregate cache hit/miss signals.
- Batched existing-row lookup is counted once per shared context.

3. Detail prewarm metric-reuse accounting
- File: `hpcperfstats/site/machine/job_detail_artifacts.py`
- Tracks whether FSIO/GPU detail payloads were sourced from existing metrics rows or fallback DB aggregation paths.

4. Reuse coverage tests
- Files:
  - `hpcperfstats/site/machine/tests/test_update_metrics.py`
  - `hpcperfstats/site/machine/tests/test_job_plot_artifacts.py`
- Added tests for shared context flow and memo telemetry behavior.

5. Duplicate-path exception register
- File: `docs/artifacts/per_jid_duplicate_query_exceptions.md`
- Added required registry entries with rationale and re-evaluation dates.

6. Type-detail prewarm skip for fresh artifacts
- File: `hpcperfstats/site/machine/job_detail_artifacts.py`
- Added fingerprint-scoped precheck to skip rebuilding already-fresh `type_detail` artifacts.
- This reduces repeated per-type DB/plot work for unchanged jobs.

7. Plot aggregate bundle normalization and shared prefetch
- File: `hpcperfstats/site/machine/job_plot_artifacts.py`
- Added normalized aggregate cache keys (event-order-insensitive) and shared aggregate bundle prefetch for common roofline probes.

## Compose-backed telemetry runs

Command used:
- `HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER=0 HPCPERFSTATS_METRICS_TELEMETRY=1 python -m hpcperfstats.analysis.metrics.update_metrics 2026-04-13 2026-04-13`

Representative scheduler summary (seeded single-jid run):
- `processed=1 failed=0`
- `telemetry_first_jid_s=0.366`
- `telemetry_metrics_p50_s=0.092`
- `telemetry_metrics_p95_s=0.092`
- `telemetry_prewarm_p50_s=0.219`
- `telemetry_prewarm_p95_s=0.219`
- `telemetry_plot_row_lookup_queries=1`
- `telemetry_plot_row_lookup_hits=0`
- `telemetry_plot_jt_host_time_hits=2`
- `telemetry_plot_jt_aggregate_hits=4`
- `telemetry_plot_jt_aggregate_misses=38`
- `telemetry_detail_fsio_metrics_reused=0`
- `telemetry_detail_gpu_metrics_reused=1`
- `telemetry_detail_fsio_fallback_queries=1`
- `telemetry_detail_gpu_fallback_queries=0`

## Telemetry delta (baseline -> latest)

Baseline used: prior telemetry-guided seed run (`2026-04-13` single-jid), then replay after latest reuse changes.

| Metric | Baseline | Latest | Delta |
|---|---:|---:|---:|
| telemetry_first_jid_s | 0.366 | 0.402 | +0.036 |
| telemetry_metrics_p50_s | 0.092 | 0.102 | +0.010 |
| telemetry_prewarm_p50_s | 0.219 | 0.245 | +0.026 |
| telemetry_plot_jt_aggregate_hits | 4 | 8 | +4 |
| telemetry_plot_jt_aggregate_misses | 38 | 38 | 0 |
| telemetry_detail_gpu_metrics_reused | 1 | 1 | 0 |
| telemetry_detail_fsio_fallback_queries | 1 | 0 | -1 |

Interpretation:
- **Measurable DB-query proxy reduction achieved**: `telemetry_detail_fsio_fallback_queries` dropped from `1` to `0`.
- Plot aggregation reuse improved cache-hit behavior (`aggregate_hits` up), though misses remained flat for this seed scenario.

## Deferred opportunities
- Directly sharing metrics-worker in-memory arrays with artifact prewarm across process boundaries.
- Expanding aggregate-bundle consolidation across summary/heatmap probes while preserving fallback/no-data behavior contracts.
- Replacing per-type detail provider rebuilds with canonical cached series reuse beyond current fingerprint-skip optimization.

## Residual risk
- Telemetry counters are proxy indicators, not direct SQL statement counts.
- Some targeted tests were skipped in this environment due test-selection/marker constraints; compose runtime validation succeeded for telemetry capture.
