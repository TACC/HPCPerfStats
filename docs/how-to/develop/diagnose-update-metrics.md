# Diagnose update_metrics

This page shows how to time, check and troubleshoot the `update_metrics` scheduler from the checkout.
It is for developers and operators; use it when metrics fall behind, a pass stalls, or a change to the columnar metrics path needs a parity check.

The scheduler states, listing phases and census format are in the [update_metrics state machine](../../reference/update-metrics-state-machine.md); the INI keys are in [Pipeline configuration](../../reference/pipeline-configuration.md#update_metrics-pools).

## Prerequisites

- The checkout and rootless Podman set up as in [Run tests](run-tests.md#prerequisites).
- For parity checks: the golden corpus (outside git) and the dev container runner `tests/run_dev_container_pytest.sh`.

## Run the diagnosis workflow

The workflow seeds two jobs that end on the same local calendar day and times each phase:

- small: in-window `host_data` scale 100-300 rows (default 10x15 = 150);
- large: 300-5000 rows (default 25x32 = 800).

It runs `update_metrics_for_dates` and asserts `LAST_UPDATE_METRICS_DIAGNOSTICS` phase totals with `HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS=1`.

```bash
tests/run_update_metrics_diagnosis_workflow.sh
```

- Options: `--skip-build`, `--keep-env`; extra pytest arguments after `--`.
- Tunables the workflow forwards: `HPCPERFSTATS_UM_DIAG_SMALL_HOSTS`, `HPCPERFSTATS_UM_DIAG_SMALL_STEPS`, `HPCPERFSTATS_UM_DIAG_LARGE_HOSTS`, `HPCPERFSTATS_UM_DIAG_LARGE_STEPS`.
- `tests/run_update_metrics_diagnosis_inner.sh` sets `HPCPERFSTATS_UM_DIAG_JSON_OUT` (default `test_runs/diagnosis/update_metrics_diagnosis.json` on the bind-mounted repo), so a copy of the JSON report lands next to the tree, not only in pytest's temp directory.
  Export `HPCPERFSTATS_UM_DIAG_JSON_OUT` before the workflow to change the path.

Read the report's counters against [Tune throughput](#tune-throughput) and [Troubleshooting](#troubleshooting) below.

## Check columnar parity

The metrics, Job Detail and plot path is pandas-free: one COPY BINARY fetch per job -> `JobFrame` -> NumPy reference reductions (`lib/columnar/segments.py`) or the baked Codon kernels (`hpcperfstats/analysis/metrics/codon/`, pyext `hpcperfstats_codon_metrics`).

- Unit kernels + bridge (host): `pytest -q hpcperfstats/analysis/metrics/tests/test_columnar_segments.py .../test_columnar_kernels.py .../test_columnar_bridge.py .../test_jobframe.py .../test_copy_decode.py .../test_copy_fetch.py`
- No pandas on the path (host): `pytest -q hpcperfstats/tests/test_update_metrics_import_graph.py`
- Monitor value sentinels (reasons, transient/static folds, gap fill, audience split, Admin Monitor findings) (host): `pytest -q hpcperfstats/analysis/metrics/tests/test_metrics_sentinel_reasons.py .../test_sentinel_classify_series.py .../test_metrics_sentinel_aggregation.py .../test_fill_interior_gaps.py hpcperfstats/site/lib/machine/tests/test_metrics.py hpcperfstats/site/lib/machine/tests/test_job_detail_openapi_wire_contract.py hpcperfstats/site/lib/machine/tests/test_admin_monitor_telemetry_health.py`
- Bake contract (Dockerfile / supervisord / runner) (host): `pytest -q hpcperfstats/tests/test_codon_metrics_bake_contract.py`
- Golden parity (series, metric rows, detail, Bokeh JSON) (host, corpus mounted): `HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1 pytest -q hpcperfstats/analysis/metrics/tests/test_columnar_golden.py hpcperfstats/analysis/metrics/tests/test_bokeh_from_arrays.py`
- Codon vs NumPy, GIL off, threads, golden frames (dev container, 3.14t): `HPCPERFSTATS_CODON_METRICS_REQUIRED=1 tests/run_dev_container_pytest.sh --ft hpcperfstats/analysis/metrics/tests/test_codon_kernels.py -q`
- Thread scaling (>= 0.6x linear at 8 threads per kernel) (container run, host check): `tests/run_dev_container_pytest.sh --ft --exec '$PY' scripts/metrics_codon_scaling_report.py run` then `scripts/metrics_codon_scaling_report.py --check`

Tolerances: base `rtol=1e-9`, `atol=0`, NaN-equal, ints and strings exact.
The looser per-metric allowances (pandas-path float4 SQL sums `1e-5`, the conv-less GPU link/mem-bw cache scale, PostgreSQL float4 text rounding, Bokeh JSON floats at `1e-5`) are written by the golden test to `test_runs/columnar_golden_deviation.json`.

### Use the golden corpus

The corpus lives outside git at `HPCPERFSTATS_METRICS_GOLDEN_DIR`.
When the variable is unset, the tests and `scripts/metrics_golden_corpus.py` fall back to the dev host path in `DEFAULT_CORPUS_DIR` of that script, and `tests/run_dev_container_pytest.sh` mounts whichever directory exists.
It holds `manifest.json` (`pandas_path_commit`), `edge_jobs.json`, `jobs/<jid>.golden.json.gz` (pandas-path metric rows, detail payloads, plot JSON, pre-Bokeh series) and `jobs/<jid>.frame.npz` (the JobFrame).

Golden tests skip with a reason when the corpus is absent and fail only with `HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1`.
Rebuild the corpus with `scripts/metrics_golden_corpus.py seed-exemplars / seed-job-data / rewrite-host-suffix / seed-edge-jobs / tag-edge-jobs / capture / verify --min-jobs 27` inside `tests/run_dev_container_pytest.sh --exec`.

### Reseed the dev database

DB-backed metrics checks read the corpus jobs from the hpcperfstats-dev database, not from the fixtures.
If that database was reset or emptied (`job_data` count 0), rebuild every corpus job from the raw exemplars and check that each manifest job has its `job_data` and `host_data` rows.
The exemplars are 7.3 GB at `METRICS_CORPUS_RAW` (default `/data/user/$USER/metrics-corpus/raw`); mount them read-write, because ingest writes `*.fnctl.lock` sidecars beside each file.

```bash
RAW="${METRICS_CORPUS_RAW:-/data/user/$USER/metrics-corpus/raw}"
X='$PY scripts/metrics_golden_corpus.py'
X="$X reseed-dev-db --raw-root /corpus/raw"
HPCPERFSTATS_DEV_BASE_IMAGE=<IMAGE_ID> \
  tests/run_dev_container_pytest.sh --ft \
  --mount "$RAW:/corpus/raw:rw" \
  --exec "$X"
```

`<IMAGE_ID>` is the dev base image to run.
When a manifest job cannot be served, `reseed-dev-db` exits 1 and prints `missing=` followed by each `<jid>:job_data` / `<jid>:host_data` gap.
It applies no migrations, so run `manage.py migrate` first when the dev schema is behind.

### Test an unbaked Codon metrics build

Before a bake is in the image, build the metrics pyext in a `codon-build` container with the host Codon mounted at `/opt/codon` (`CODON_SRC=.../analysis/metrics/codon CODON_OUT=<dir> build_codon_metrics_ext.sh`).
Point `HPCPERFSTATS_CODON_METRICS_BUILD_DIR=<dir>` at the output; the runner mounts it at `/opt/hpcperfstats/codon_metrics`.
In the image, the scheduler refuses to start on `/opt/python3.14t` without `hpcperfstats_codon_metrics.so` (`codon_metrics status=disabled ...` then `SystemExit`).

## Tune throughput

Set absolute pool sizes against Django `max_connections`: `[PIPELINE] sync_ingest_pool_processes`, `metrics_pool_processes` (metrics plus plot and detail prewarm on the same pool), `listend_db_ingest_pool_processes` and `[PORTAL] gunicorn_workers`.

- Ready-queue depth is `metrics_scheduler_ready_queue_target` alone (default 100).
- With `metrics_idle_slot_supplement_enabled=yes`, the compute session uses a pool-sized sliding feeder and fills idle slots from the ready queue under `estimated_sample_count` caps: `metrics_supplement_sample_soft_max` / `metrics_supplement_sample_hard_max`, defaults 10000 / 80000.
  It stops pulling supplements when no original-batch work remains in flight.
- `metrics_compute_batch_max_window_s` / `metrics_compute_batch_max_single_job_s` limit how many long-window jobs share one primary compute batch.
- `metrics_compute_watchdog_s` and `metrics_compute_total_watchdog_s` treat metrics plus prewarm as one batch wall-time unit.
- `metrics_deferred_not_ready_*` keys tune readiness backoff.

Environment overrides for diagnosis:

- `HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`, `HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS` and `HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL` (seconds; `0` disables the cached window `COUNT(*)` for large-job gating).
- `HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S` / `HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS`: plot aggregate hostxtime chunks and the materialised-row budget for the design capacity of `5000x48x60` host-samples (INI `metrics_plot_aggregate_time_slice_s` / `metrics_plot_aggregate_max_host_time_points`).
- `HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST=1` restores `unnest(host_list)` live distinct SQL, for emergency rollback only (see the `get_live_distinct_use_legacy_hostlist()` docstring).
- `HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER` defaults on; `0`/`no`/`false` makes the `update_metrics.py` entrypoint run one pass over the date range and exit, with no continuous scheduler and no post-pass sleep.

## Recalculate one job

`update_metrics.py --jid <JID>` (also `--jid=<JID>`) invalidates the Valkey and DB plot and detail artifacts and the per-jid derived caches for that job, recomputes metrics plus detail and plot artifacts, and exits 0 or 1.
It skips the date-range scheduler and the post-run sleep; do not combine `--jid` with positional dates.
`<JID>` is the job ID.

## Verify the backfill lane

Behind the fresh window, a slot-capped backfill lane recomputes dirty old jobs first, then every job whose `job_data.metrics_version` predates `METRICS_CONTRACT_VERSION`, newest first with a persisted cursor (`metrics_scheduler_state`), skip-instead-of-defer readiness and a quiescence partial-final rule (state-machine doc section 4.5).
The operator CLI is `update_metrics.py --backfill-status` (census) and `update_metrics.py --backfill-finalize-partials [--before YYYY-MM-DD]` (compute still-skipped old jobs now).

To check the lane against real PostgreSQL in the dev container (it seeds old jobs and drives the listing, cursor, skips, census and the widened dirty stamp):

```bash
T=hpcperfstats/site/lib/machine/tests
tests/run_dev_container_pytest.sh \
  "$T/test_update_metrics_backfill_compose.py" -q
```

The host unit tests for the lane are in [Test layout](../../reference/test-layout.md#update_metrics).

## Troubleshooting

Scheduler logs and the diagnosis JSON carry queue and compute counters: `ready_enqueued_total`, `ready_dequeued_total`, `inflight_jids`, `attempted_total`, `compute_batches_total`, `batch_compute_exceptions_total` and `per_jid_fallback_failures_total`, plus readiness and deferred counters (`proxy_not_ready_jids`, `strict_not_ready_jids`, `strict_ready_jids`, `strict_cooldown_skips`, `deferred_not_ready_queue_size`, `deferred_not_ready_due_now`, `deferred_quarantined_jids`).
The once-a-minute `metrics census` line prints them grouped (`queue=`, `readiness=`, `deferred=`, `batch=`, `fail=`; state-machine doc section 7), with the mid-batch heartbeat as `batch=phase:,done:c/n,age_s:,idle_s:`.

- `inflight_jids ~ ready_dequeued_total` with `compute_batches_total=0` / `processed_total=0` for a long window: this can be a slow first compute batch (metrics or `pipeline_required` prewarm under DB load), not a dead pool.
  Confirm `[thread:metrics-pool]` titles under `update_metrics.py [main]` and watch the census heartbeat: rising `done` and small `idle_s` mean slow, not hung.
  Then wait for `compute batch N ... compute_batch_elapsed_s=... next_batch_cap=...`.
  `metrics_compute_watchdog_s` downshifts `next_batch_cap` from the metrics+prewarm `batch_wall_s`; the default `metrics_compute_total_watchdog_s=0` is not needed for that downshift.
- The pool idles with small jobs in the ready queue while a large original-batch jid stays in metrics or prewarm: check `metrics_idle_slot_supplement_enabled` and the sample caps.
  Smaller ready-queue jobs should appear as extra in-flight work under the caps; when only supplement work remains, the scheduler stops pulling new ready-queue jobs.
- `MetricsPrewarmStallError` at partial `completed=k/n`: the scheduler keeps partial successes, soft-fails the rest, detaches the current executor, and lazily creates a fresh titled pool before the next batch; results are never wiped to `[]`.
- The process exits with code 1 and supervisord restarts it: a stall exit fired (`stall_exit_triggered=1`); read `stall_reason` in the stall summary.
  Without a stall, the supervised default (no CLI dates) runs passes back to back in one process (`_run_continuous_scheduler`; idle `metrics_scheduler_pass_interval_s`, default 60 s) and refreshes `/pub/` EF artifacts on a background `_PublicEfRefresher` thread every `metrics_public_ef_interval_s` (default 3600 s).
  Explicit-date runs keep the single blocking pass followed by the 600 s sleep.

### Stall reasons

The scheduler emits `stall_exit_triggered=1` only after heartbeats show no advancing `completed_jids` and workers are dead or missing, for a long window with zero progress:

- `no_ready_candidates`: Starvation at the readiness or candidate stage: listing not finished, and no ready enqueue, mid-batch or inflight progress for `STALL_EXIT_AFTER_SECONDS`
- `listing_query_failed`: An unexpected exception escaped listing or `_fill_ready_queue` (statement-timeout recovery catches `OperationalError` inside fill; this is the producer safety net); partial `local_ready` is flushed before exit
- `compute_stuck_inflight`: The queue drained but work remains inflight
- `compute_all_failed`: Compute keeps attempting but every job fails

`no_ready_candidates` must not fire while `ready_queue` is non-empty, `inflight_jids > 0`, or mid-batch `compute_batch_completed_jids` / `ready_enqueued_total` still advances; a long first metrics+prewarm batch with rising heartbeat `completed_jids` is a slow batch.
When listing is finished and only deferred not-ready jids remain, the pass ends normally once the consumer is idle, and those jids retry next pass.
`DOCUMENTED_SCHEDULER_STALL_REASONS` in `hpcperfstats/analysis/metrics/update_metrics.py` holds these strings, and `test_update_metrics_stall_reason_doc_drift_guard` locks them.

## Related

- [update_metrics state machine](../../reference/update-metrics-state-machine.md)
- [Pipeline configuration](../../reference/pipeline-configuration.md)
- [Pipeline concurrency](../../explanation/pipeline-concurrency.md#update_metrics-memory-admission)
- [Run tests](run-tests.md)
- [Test layout](../../reference/test-layout.md)
