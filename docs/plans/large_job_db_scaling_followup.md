# Large-job DB and pipeline scaling (follow-up from stress reports)

**Row sweep (80k → 2.56M, large-job threshold):** see [stress_row_sweep_scaling_plan.md](stress_row_sweep_scaling_plan.md). The stress test calls **`update_metrics(timezone.localtime(job.end_time), ...)`**; **`Metrics.job_arc` / `job_value_mean`** honor **`jid_table`** **`time__in`** when large-job sampling is active (default **1.5M** `host_data` rows per job window).

This note is grounded in **`stress_report_*.json`** from `tests/stress_host_data/` after retooling the suite to run the real **`update_metrics(..., rerun=True)`** path (readiness probes, pooled metrics, plot prewarm). Two representative compose runs on 2026-04-06 (Apple Silicon host, Docker):

| Report artifact | Scale (nominal) | `update_metrics_rerun_true` (s) | `seed_bulk_insert_probes` (s) | `latest_sample_time_by_host` (s) |
|-----------------|-----------------|----------------------------------|--------------------------------|-------------------------------------|
| `artifacts/stress/stress_report_20260406T180559Z.json` | 1 host, 2000 steps, 1 s cadence, 80,000 main rows + 1 probe row | 3.28 | 0.62 | 0.0007 |
| `artifacts/stress/stress_report_20260406T180631Z.json` | 8 hosts, 30 steps, 30 s cadence, 9,600 main rows + 8 probes | 1.05 | 0.07 | 0.0006 |

Both runs completed full **metrics** catalog persistence (**39** `metrics_data` rows) and **3** `job_plot_artifact` rows (remaining catalog entries / layouts skipped or capped as today).

## Reference density **R_ref** (sample) vs stress **R**

From the same reports, **`r_ref_from_sample`** on `HPCPerfStatsdDataSample` gives **`r_median` = `r_max` = 91** rows per (host, time) after `compute_deltas_and_arc` (collapsed shape). Order-of-magnitude full Cartesian (if every host had every bucket) is documented in metadata as **`using_r_max` = 4,717,440,000** rows for **6,000 × 8,640 × 91** (written as a plain integer in JSON).

The synthetic stress seed uses **40** `(type, event)` pairs per timestep, so **nominal R = 40** for extrapolations that must match the stress insert pattern, while **R_ref = 91** remains the monitor-sample anchor for real-world row pressure.

## Prioritized follow-ups (evidence-linked)

1. **Redis aggregate cache keys (`CacheKeyWarning`)**  
   Pytest emitted **`CacheKeyWarning`** for keys longer than 250 characters (`agg_df:stress_um_pipeline:intel_8pmc3:...`). Same wall clock as **`update_metrics`** in these runs, so this is a production risk for Memcached-class backends. **Action:** hash or truncate stable key material (keep collision safety), and add a regression test that asserts no warning for the stress jid path.

2. **`update_metrics` vs seed cost**  
   At 80k rows, **`update_metrics_rerun_true` (~3.3 s)** dominated **`seed_bulk_insert_probes` (~0.6 s)**. **Action:** profile inside `Metrics().run` / `jid_table` / DB queries for the next decade-scale row count; watch for `list(qs)` hot paths and repeated aggregate work.

3. **Readiness: `_latest_sample_time_by_host` batching**  
   Even with **8** accounting hosts, the timed phase stayed sub-millisecond. **Action:** re-evaluate when **N hosts → thousands**: `HOST_LAST_TIME_LOOKUP_BATCH` and LATERAL probes should be stress-tested at **O(n_hosts)** with realistic latency targets.

4. **PostgreSQL `pg_stat_user_tables.n_live_tup` for `host_data`**  
   Snapshots showed **0** live tuples for **`host_data`** while **`metrics_data`** showed **39** after the run—consistent with **TimescaleDB** / child chunk statistics not rolling up to the parent name the query used. **Action:** for hypertables, either query **chunk** stats or **`hypertable_detailed_size`**-style introspection so future reports do not misread “empty” parent stats.

5. **Job chunk iterator (`CHUNK_SIZE = 500`)**  
   Not hot in single-job stress; **Action:** on sites with many jobs per day, validate keyset pagination cost and memory against **hundreds of thousands** of `job_data` rows ending the same day.

6. **Plot artifact cardinality**  
   Only **3** persisted rows vs many `JOB_PLOT_KINDS` × layouts—expected if some kinds skip or hit size caps. **Action:** if product requires full prewarm, align `JOB_PLOT_REDIS_MAX_BYTES` / gzip persistence policy with operator SLOs and re-measure **`update_metrics`** duration.

## Re-running the measurements

```bash
cd HPCPerfStats
HPCPERFSTATS_STRESS_HOST_DATA_ROWS=80000 tests/run_stress_host_data_workflow.sh --skip-build
HPCPERFSTATS_STRESS_USE_TIME_SCALE=1 HPCPERFSTATS_STRESS_N_HOSTS=8 \
  HPCPERFSTATS_STRESS_DURATION_SEC=900 HPCPERFSTATS_STRESS_INTERVAL_SEC=30 \
  tests/run_stress_host_data_workflow.sh --skip-build
```

Reports land in **`artifacts/stress/`** (override with **`HPCPERFSTATS_STRESS_REPORT_DIR`**). Set **`HPCPERFSTATS_STRESS_EXPLAIN=1`** for an **`EXPLAIN (FORMAT JSON)`** attachment on the representative host/time/jid count query.
