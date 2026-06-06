# Follow-up: speed work from compose diagnosis (baseline)

This document is grounded in a successful run of `tests/run_update_metrics_diagnosis_workflow.sh` after the compose harness fix (`django_db(transaction=True)` on `test_update_metrics_diagnosis_compose_records_phases`) and optional artifact export (`HPCPERFSTATS_UM_DIAG_JSON_OUT`, default `tmp/update_metrics_diagnosis.json`).

## Environment (same cohort defaults)

- Scheduler: `global_priority` (from logs during development runs).
- Test sets `HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM=1` and `HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS=1`.
- Seed axis: in-window `host_data` rows per job (`n_rows_small` / `n_rows_large`).

## Baseline snapshot (`tmp/update_metrics_diagnosis.json`)

```json
{
  "scale_axis": "in_window_host_data_rows_per_job",
  "n_rows_small": 150,
  "n_rows_large": 800,
  "phase_totals": {
    "candidate_sql_s": 0.0018515440169721842,
    "readiness_s": 0.0029998619575053453,
    "metrics_compute_s": 0.33855726290494204,
    "prewarm_s": 1.8167076632380486e-05
  },
  "stats": {
    "processed": 2,
    "failed": 0,
    "candidate_jids": 2,
    "skipped_not_ready": 0,
    "readiness_error_chunks": 0,
    "proxy_checked_chunks": 1,
    "proxy_rejected_jids": 0,
    "readiness_probe_value": 564,
    "strict_batch_size_current": 64,
    "strict_check_calls": 2,
    "strict_check_timeouts": 0,
    "strict_check_avg_latency_ms": 1.5109105734154582
  },
  "elapsed_s": 0.3927392400801182,
  "jobs_per_min": 305.5462448201514
}
```

## Phase share (summed phase totals vs total phase time)

Sum of `phase_totals` keys: **~0.34343 s**.

| Phase | Seconds | Share of phase sum |
|-------|---------|---------------------|
| `candidate_sql_s` | 0.001852 | ~0.54% |
| `readiness_s` | 0.003000 | ~0.87% |
| `metrics_compute_s` | 0.338557 | **~98.6%** |
| `prewarm_s` | 0.000018 | ~0.005% |

`elapsed_s` (~0.393 s) is slightly above the sum of phases (~0.343 s); the remainder is scheduler / locking / logging overhead outside `phase_totals`.

## Dominant phase

**`metrics_compute_s`** dominates wall time for this tiny two-job cohort with prewarm skipped. **`candidate_sql_s`** and **`readiness_s`** are negligible at this scale (indexes + small windows).

## Explicit exclusions

- **Prewarm**: near-zero `prewarm_s` because the diagnosis test sets **`HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM=1`**. Do not tune prewarm modes based on this JSON alone; repeat with skip unset when studying plot/detail cost.

## Ranked actions

### P0

1. **Throughput under skip-prewarm**: prioritize **`metrics_pool_process_cap`** (and worker CPU affinity / host sizing) and validating batched `Metrics.run` in production. References: **`[PIPELINE] metrics_pool_process_cap`** in `hpcperfstats.ini.example`, `hpcperfstats/analysis/metrics/metrics.py` (`Metrics.run`, pool + `_drain_metrics_imap`).
2. **Reliability**: batched `imap` occasionally raised **`IndexError`** on short batches; production path now **retries per-job** after logging (same module). Re-run diagnosis if pool/stdlib behavior changes.

### P1

3. **Stronger SQL/readiness signal**: raise **`HPCPERFSTATS_UM_DIAG_LARGE_HOSTS`** / **`HPCPERFSTATS_UM_DIAG_LARGE_STEPS`** (and small-job counterparts) so candidate discovery and strict readiness work enough rows to show up in `phase_totals`.
4. **When candidate/readiness grow**: use **`EXPLAIN (ANALYZE, BUFFERS)`** on keyset and readiness queries; confirm migrations **`machine.0021_readiness_query_indexes`** (and related) are applied.

## Success criteria (next iteration)

- Repeat diagnosis after infra or pool tuning: target **higher `jobs_per_min`** at the **same seed env** and dates, or **lower `metrics_compute_s` per processed job** when comparing before/after (same cohort).
- For backlog catch-up, measure again with **`metrics_scheduler_skip_prewarm`** aligned to operational policy (not necessarily the diagnosis test default).

## Sweep results (this implementation pass)

Artifacts were captured under `tmp/um_diag_sweeps/`:

- `baseline_defaultcap.json` (default cap from ini/effective cores)
- `baseline_cap8_v2.json` (`METRICS_POOL_PROCESS_CAP=8`)
- `large_defaultcap.json` (`small=20x50`, `large=120x220`)
- `large_cap8_v2.json` (`METRICS_POOL_PROCESS_CAP=8`, same larger scale)

### Measured comparison

| Run | n_rows_small / n_rows_large | processed | jobs_per_min | metrics_compute_s | metrics_compute_s / processed | candidate+readiness share |
|---|---:|---:|---:|---:|---:|---:|
| baseline default cap | 150 / 800 | 2 | 524.10 | 0.1753 | 0.0877 | 1.67% |
| baseline cap=8 | 150 / 800 | 4* | 664.41* | 0.3053 | 0.0763* | 1.69% |
| larger default cap | 1000 / 26400 | 2 | 34.34 | 3.4320 | 1.7160 | 0.20% |
| larger cap=8 | 1000 / 26400 | 2 | 34.19 | 3.4546 | 1.7273 | 0.17% |

\* `cap=8` baseline showed a retry pass (`processed=4`, `candidate_jids=4`), so its headline throughput is not directly comparable to single-pass runs. We now reset per-attempt diagnostics in `update_metrics_for_dates`, but repeatability still matters when comparing caps.

### Conclusions from sweeps

1. `metrics_compute_s` remains the dominant cost at both default and larger scales.
2. Candidate/readiness costs are still tiny (<2%) in these cohorts, so SQL EXPLAIN/index work is deferred for now.
3. Lowering pool cap to 8 did **not** improve larger-scale throughput (`jobs_per_min` slightly lower; compute/job slightly higher).
4. Recommended near-term default: keep current cap behavior; tune upward/downward only with larger realistic cohorts and repeat runs.

### Reliability notes

- `test_update_metrics_diagnosis_compose_records_phases` now permits env-driven larger row counts (instead of hard upper-band assertions) so diagnosis sweeps can scale rows without test edits.
- `update_metrics_for_dates` now resets diagnostics counters/timers on retry attempts, preventing cumulative `processed`/`candidate_jids` across retries.
- Scheduler readiness now reports split counters (`proxy_not_ready_jids`, `strict_not_ready_jids`, `strict_ready_jids`, `strict_cooldown_skips`) plus deferred queue health (`deferred_not_ready_queue_size`, `deferred_not_ready_due_now`, `deferred_quarantined_jids`) so no-progress incidents are diagnosable from logs.
- Permanently not-ready churn is bounded by deferred retry aging/quarantine and an explicit no-progress stall exit (`stall_exit_triggered`) instead of indefinite silent loops.

## Stall taxonomy and latest validation

Latest compose diagnosis artifact (`tmp/update_metrics_diagnosis_large.json`, generated with `HPCPERFSTATS_UM_DIAG_JSON_OUT=/home/hpcperfstats/tmp/update_metrics_diagnosis_large.json`) confirms the new queue/compute counters are populated:

- `ready_enqueued_total=4`, `ready_dequeued_total=4`, `inflight_jids=0`
- `compute_batches_total=1`, `batch_compute_exceptions_total=0`, `per_jid_fallback_failures_total=0`
- `attempted_total=4`, `processed=4`, `failed=0`, `stall_reason=""`

Interpretation sequence for stall runs:
1. If `ready_enqueued_total` does not grow, treat as candidate/readiness starvation.
2. If enqueued/dequeued grow but `inflight_jids` remains elevated with low `attempted_total`, treat as compute stuck inflight.
3. If `attempted_total` grows while `processed=0` and `failed>0`, classify as compute failure churn (`stall_reason=compute_all_failed`).
