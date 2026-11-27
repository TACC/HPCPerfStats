# Run sync_timedb benchmarks

This page shows how to run the `sync_timedb` ingest throughput benchmarks and soaks in rootless Podman.
It is for developers measuring ingest changes; use it when a parse, write or concurrency change needs a timed A/B before it lands.

Benchmark results are screening evidence, not production INI changes.
Past campaign results are in the [throughput campaign record](../../history/sync-timedb-throughput-campaign.md).

## Prerequisites

- The checkout and rootless Podman set up as in [Run tests](run-tests.md#prerequisites).
  The study runs the free-threaded `/opt/python3.14t/bin/python` in the container.
- A derived corpus under `test_runs/sync_timedb_bench/` ([Build a derived corpus](#build-a-derived-corpus)).
- `zstd` on `PATH` for the host modules that touch archives.

## Run the unit harness

The host-safe harness checks accounting, winner selection and schema contracts, with no compose:

```bash
../.venv/bin/python3 -m pytest tests/sync_timedb_benchmark -q \
  --tb=short
```

Host modules for the ingest throughput path (no DB needed):

- `test_codon_file_process_pool.py`: the Codon worker-process pool, with a real `codon_process_worker` subprocess against the importable fake `codon_worker_fake_mod.py` (COPY-frame IPC, large-frame reads, abandon and respawn, recycle by RSS, host-row coalescing, 1 MiB window cap, `codon_worker_mem` lines).
- `test_sync_timedb_host_copy_vectorized.py` / `test_sync_timedb_proc_copy_vectorized.py`: vectorized COPY encoders byte-identical to the per-cell path.
- `test_sync_timedb_wait_telemetry.py`, `test_sync_timedb_ingest_rss_pressure.py`, and the soak analyzers `test_analyze_ingest_stacks.py` / `test_analyze_ingest_wait_breakdown.py`.

The long compose study runs with no mode flag:

```bash
tests/run_sync_timedb_benchmark_workflow.sh
```

## Build a derived corpus

`scripts/derive_sync_timedb_benchmark_corpus.py` rewrites identities into a new tree and never mutates the exemplars:

```bash
../.venv/bin/python3 \
  scripts/derive_sync_timedb_benchmark_corpus.py \
  --source-dir /path/to/exemplars \
  --output-dir test_runs/sync_timedb_bench/corpus_smoke \
  --host-suffix .cluster_name.domain.edu \
  --max-files 6
# Steady tier example (N smallest across exemplar trees):
../.venv/bin/python3 \
  scripts/derive_sync_timedb_benchmark_corpus.py \
  --source-dir /path/to/hpcperfstats02-exemplars \
  --source-dir /path/to/hpcperfstats04-exemplars \
  --source-dir /path/to/hpcperfstats01-exemplars \
  --output-dir test_runs/sync_timedb_bench/corpus_steady \
  --host-suffix .cluster_name.domain.edu \
  --max-files 24
```

Replace the `/path/to/...` directories with your exemplar trees and `.cluster_name.domain.edu` with your site's host suffix.

## Run a benchmark mode

Each mode is a flag of `tests/run_sync_timedb_benchmark_workflow.sh` and writes JSON artifacts to `test_runs/sync_timedb_bench/`.
The timed window holds no `update_metrics`; Timescale runs through compose `db` and `valkey`.
Every mode except `--screening` clears ambient `HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS` / `_REPLICATES` from an earlier screening shell unless `HPCPERFSTATS_SYNC_TIMEDB_KNEE_ALLOW_SCREEN_ENV=1`.

- `--screening` (`screening_*.json`): Ingest-width screening on `corpus_smoke`; defaults 1..96 widths, 2 replicates; the winner is a hint
- `--knee` (`knee_*.json`): Knee confirmation on `corpus_steady`; default widths `48,64,80,96`, 5 replicates, longer per-replicate timeout
- `--e2` (`e2_closed_book_*.json`): Closed-book mid-size timing on the steady corpus; residual \<=5% recorded in the artifact
- `--knobs` (`knobs_*.json`): Supporting-knob sweeps at fixed width 48 (sequential day-close, archive, populate, bulk-create; >=3 replicates)
- `--e6` (`e6_arm_baseline_*.json`, then `e6_parse_feed_ab_*.json`): `parse_feed` paired A/B at width 48 (>=5 replicates); `retain` true/false per the lower-CI gate
- `--e7` (`e7_arm_baseline_*.json`, then `e7_proc_build_ab_*.json`): Residual `proc_merge` / `build_df` A/B at width 48 (skip-redundant-dedupe and columnar `take_proc`); same retain gate as E6
- `--e8` (`e8_delta_collapse_ab_*.json`): `delta_s` / `collapse_s` hold-seconds A/B; retain is decided on the per-hold seconds, not files/s
- `--host-insert` (`host_data_insert_ab_*.json`): Write-only `host_data` `bulk_create` vs COPY A/B (at least 100,000 rows)
- `--proc-insert` (`proc_data_insert_ab_*.json`): Write-only `proc_data` `bulk_create` vs COPY A/B
- `--contention` (`contention_<wave>_arm_baseline_*.json`, then `contention_<wave>_ab_*.json`): FT contention wave A/B at width 48 (one wave patch at a time, >=5 replicates); `retain` plus `gate` = `throughput`|`no_regression`
- `--loaded48` (`loaded48_arm_baseline_*.json`): Continuous-refill soak ([Run the loaded soak](#run-the-loaded-soak))

Optional variables:

- `HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS=1,2,4,8` and `HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=2` override the screening CSV defaults; `HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=5` sets the A/B replicate count.
- Fixed widths per mode: `HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH=48`, `HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH=48`, `HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH=48`, `HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH=48`, `HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH=48`.
- `HPCPERFSTATS_HOST_INSERT_ROWS` (default 100000) and `HPCPERFSTATS_HOST_INSERT_REPLICATES` (default 5); `HPCPERFSTATS_PROC_INSERT_ROWS` / `_REPLICATES` take the same defaults.

### Run a paired A/B

The E6, E7 and contention modes run a baseline arm, then a candidate arm after you apply the product patch:

```bash
HPCPERFSTATS_E6_ARM=baseline \
  tests/run_sync_timedb_benchmark_workflow.sh --e6
# Apply feed_line candidate patch, then:
HPCPERFSTATS_E6_ARM=candidate \
  tests/run_sync_timedb_benchmark_workflow.sh --e6
```

`--e7` takes `HPCPERFSTATS_E7_ARM` the same way, with the residual merge/build_df candidate patch.
`--contention` takes `HPCPERFSTATS_CONTENTION_ARM` plus `HPCPERFSTATS_CONTENTION_WAVE` set to a wave id: `caches`, `park_resume`, `manifest_io`, `members_shard`, `claim_heap`, `tar_ex`, `thread_id`, `pool_split`, `log_drain`, `discover` or `telem_tls`:

```bash
HPCPERFSTATS_CONTENTION_WAVE=caches \
  HPCPERFSTATS_CONTENTION_ARM=baseline \
  tests/run_sync_timedb_benchmark_workflow.sh --contention
```

## Run the loaded soak

`--loaded48` is a continuous-refill soak at ingest width 48 over a mixed small, medium and large corpus (size terciles), with an occupancy oracle after warm-up.
The default wall is 6h; use `0.1` for a smoke:

```bash
HPCPERFSTATS_LOADED48_HOURS=0.1 \
  tests/run_sync_timedb_benchmark_workflow.sh --loaded48
HPCPERFSTATS_LOADED48_HOURS=6 \
  tests/run_sync_timedb_benchmark_workflow.sh --loaded48
```

The artifact records `occupancy_ok`, files/s, `complete_files`, `orm_rows` and `measure_s`; the headline files/s uses `max(complete_files, orm_rows)`.
Each run keeps the soak child's output as `test_runs/sync_timedb_bench/loaded48_child_<utc>_<pid>.log` (recorded as `child_log` in the artifact).
Check a soak about 5 minutes after launch (files/min, quarantines, `parse_file failed`, OOM) instead of waiting for it to end.

Test-only soak overrides (production uses the INI keys named):

- `HPCPERFSTATS_SYNC_INGEST_TELEMETRY=1`: Parse-stage and write-phase tokens on `ingest file` lines (INI `sync_ingest_telemetry`)
- `HPCPERFSTATS_LOADED48_STACKDUMP_S=30`: All-thread Python stack dump every 30 s to `<child_log>.stacks` (off by default)
- `HPCPERFSTATS_LOADED48_GC_MODE=trace`: `gc_probe` line every 30 s (GC count and summed pause seconds; on free-threaded CPython a collection parks every other thread); `=off` also disables automatic GC for an A/B (memory grows, test hosts only)
- `HPCPERFSTATS_LOADED48_MALLOC_TRIM=yes|no`: INI `sync_ingest_malloc_trim_after_file`: `no` skips the per-file and mid-chunk `gc.collect()` + `malloc_trim`, `yes` forces them on
- `HPCPERFSTATS_LOADED48_MEM_TELEMETRY=yes`: INI `sync_timedb_mem_telemetry`: `sync_timedb_mem_telemetry:` lines with `inflight_raw_mib`, `budget_mib`, `peak_factor`, tree RSS and cgroup `anon_mib`/`file_mib`, the evidence for `PEAK_CGROUP_PER_RAW_FILE_BYTE`
- `HPCPERFSTATS_TEST_PIPELINE_MEM=128g`: Compose test overlay `mem_limit` (default `0` = unlimited); a cgroup `memory.max` turns on the `sync_process_tree_rss_*` roof and the in-flight raw-byte budget, otherwise off (`budget_mib=0`)
- `HPCPERFSTATS_LOADED48_RSS_LIMIT_PCT=30`: INI `sync_process_tree_rss_limit_cgroup_pct`; with the cap above, the RSS-pressure gate trips in a short soak (`queue_orchestrator rss_hold` / `rss_release` lines, `skip_rss_pressure` in `fill_block=`)
- `HPCPERFSTATS_LOADED48_BIG_CLONE_CAP=N`: Limits how many times a corpus template of at least 256 MiB is cloned (default unlimited); planting stops when every template is exhausted
- `HPCPERFSTATS_LOADED48_WRITE_POOL=N`: INI `sync_ingest_write_pool_processes` (1-4096; default a quarter of the ingest width), for write-bottleneck runs
- `HPCPERFSTATS_LOADED48_PARSE_QUEUE=N`: INI `sync_ingest_parse_queue_max` (1-4096)
- `HPCPERFSTATS_LOADED48_PRECREATE_CHUNKS=<epoch_min>:<epoch_max>`: Pre-creates the daily `host_data` chunks; a fresh DB otherwise serializes every first insert of a day under a hypertable-wide `ShareUpdateExclusiveLock`

Use `HPCPERFSTATS_LOADED48_BIG_CLONE_CAP` with a corpus that holds a multi-GB real file (`HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS=test_runs/sync_timedb_bench/<corpus>`), so a 14 GB file is not copied hundreds of times.
To run with a freshly baked Codon extension instead of the image one, set `HPCPERFSTATS_CODON_PYEXT_DIR` to a directory holding the whole `/opt/hpcperfstats/codon_parse` tree and confirm the child log shows `codon_parse status=enabled`.

### Codon worker processes

The Codon worker count equals the ingest width (`HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH`).
With 1 MiB windows, N=32 peaks at about 26 GiB combined worker RSS and about 52 GiB cgroup; with 16 MiB windows, N=24 hit the 100g cap and OOM-killed a worker.
With ingest telemetry on, workers log `codon_worker_mem` lines (RSS split, Boehm heap/free/unmapped, GC count, frame sizes).
Compare Codon and CPython by DB `tup_inserted` over time, not by completed-file MiB: dense files expand to millions of rows and finish in bursts.
Planted queue clones rename their own `$hostname` per clone (verbatim clones share keys and block each other's `ON CONFLICT`) and publish atomically via `.plant_tmp`.

## Analyze a soak log

With `HPCPERFSTATS_SYNC_INGEST_TELEMETRY=1`, run the production analyzer on the child log (Python 3.11+, for `datetime.UTC`):

```bash
HPCPERFSTATS_SYNC_INGEST_TELEMETRY=1 \
  HPCPERFSTATS_LOADED48_HOURS=0.5 \
  tests/run_sync_timedb_benchmark_workflow.sh --loaded48 \
  --skip-build
../.venv/bin/python3 scripts/measure_pipeline_ingest_rate.py \
  --log-file \
  test_runs/sync_timedb_bench/loaded48_child_<utc>_<pid>.log
```

`<utc>` and `<pid>` are the timestamp and process ID in the log name the run prints.

Wait attribution comes from the same telemetry:

- `ingest file` lines carry `thr_cpu_s`/`thr_runq_s`/`thr_sleep_s` (schedstat split of the ingest thread's wall), per-stage `*_cpu_s`, `wq_put_s`/`wq_resid_s`/`wq_join_s`/`wq_n` (write-queue handoff), `w_exec_s`/`w_cpu_s`/`w_runq_s` (writer thread), `slot_wait_s`, `copy_encode_s` (Python COPY encoding) and `codon_primary`/`emit_start` (parse path).
- The 60 s census adds `psi_*10`, `sys_runnable`, per-role `cpu_*`/`runq_*` cores, `pygc_*`, `boehm_*`, `wq_depth` and `w_busy`.
- `tests/sync_timedb_benchmark/sample_phase2.sh OUT_PREFIX [interval]` samples container CPU and Postgres wait events beside a soak.
- `scripts/analyze_ingest_wait_breakdown.py --log-file CHILD.log` and `scripts/analyze_ingest_stacks.py --stacks CHILD.log.stacks` (needs `HPCPERFSTATS_LOADED48_STACKDUMP_S=30`) turn the artifacts into tier tables.

## Sweep ingest widths

The width sweep reuses the continuous-refill harness and only reports: serial 48 -> 64 -> 96, each a 0.1h smoke plus a 3h soak, against this run's 3h at 48.
It must survive an SSH disconnect, so launch it detached:

```bash
TS=$(date +%Y%m%dT%H%M%S)
LOG="test_runs/loaded-width-sweep-$TS.log"
PIDF="test_runs/loaded-width-sweep.pid"
nohup setsid bash tests/run_loaded_width_sweep_ab.sh \
  >"$LOG" 2>&1 < /dev/null &
echo $! >"$PIDF"
```

Artifacts: `test_runs/sync_timedb_bench/width_sweep_ab_*.json` (`gates` per candidate width).

## Print pool sizes

`tests/pipeline_e2e/cpuset_budget_bench.py` prints, as one dict, the absolute INI pool sizes that `conf_parser` resolves (`sync_ingest_pool_processes`, `metrics_pool_processes`, `gunicorn_workers`, `listend_db_ingest_pool_processes`), for operator comparison; it takes no arguments:

```bash
../.venv/bin/python3 tests/pipeline_e2e/cpuset_budget_bench.py
```

The listend archive append benchmark is `../.venv/bin/python3 scripts/bench_listend_archive_append.py` (ns/msg and MB/s per payload shape; scratch dir `test_runs/listend_dedupe/bench`).

## Troubleshooting

- The next run fails on an existing volume after a killed soak: the soak left a `hpcperfstats-test_*` volume; `podman volume rm` it.
- The day-close soak: `tests/run_sync_timedb_day_close_soak_workflow.sh` is in [Test layout](../../reference/test-layout.md#sync_timedb).

## Related

- [Throughput campaign record](../../history/sync-timedb-throughput-campaign.md)
- [Pipeline concurrency](../../explanation/pipeline-concurrency.md#ingest-memory)
- [Pipeline configuration](../../reference/pipeline-configuration.md#ingest-pools)
- [Run tests](run-tests.md)
