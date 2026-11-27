# Pipeline configuration

This page lists the `hpcperfstats.ini` sections and keys that size and tune the web and pipeline services, with each key's code default.
It is for operators tuning a site and developers adding a key.

The defaults come from `INI_OPTION_REGISTRY` in `hpcperfstats/dbload/lib/conf_parser.py`, the canonical list of wired options.
`hpcperfstats/tests/test_hpcperfstats_ini_example.py` keeps `hpcperfstats.ini.example` aligned with it, and the drift guard checks every default on this page.
[Pipeline concurrency](../explanation/pipeline-concurrency.md) explains why the defaults are what they are.

## File layout

`hpcperfstats.ini` is baked into the image at `/home/hpcperfstats/hpcperfstats.ini` (`HPCPERFSTATS_INI`); production never bind-mounts it.
A change to it is a redeploy: rebuild the image and recreate the containers.

In `hpcperfstats.ini.example`, each setting has a one-line `#` comment directly above it.
Keys whose value is the `conf_parser` code default appear commented (`# key = default`); active lines are required keys or overrides.
`scripts/rewrite_hpcperfstats_ini.sh` brings an older site `hpcperfstats.ini` onto the current example layout ([Upgrade the stack](../how-to/operate/upgrade-the-stack.md#rewrite-the-site-ini)).

The sections:

| Section      | Holds                                                                          |
| ------------ | ------------------------------------------------------------------------------ |
| `[DEFAULT]`  | Install identity, PostgreSQL connection, `total_cores`                         |
| `[PORTAL]`   | Gunicorn and Django web tuning                                                 |
| `[PIPELINE]` | `sync_timedb`, archive and seal, `update_metrics`, `listend`, accounting paths |
| `[RMQ]`      | RabbitMQ host and queue                                                        |
| `[CACHE]`    | Valkey location                                                                |
| `[SYSLOG]`   | Cluster syslog allowlist and listeners                                         |
| `[OAUTH2]`   | OAuth2 login integration                                                       |
| `[XALT]`     | XALT integration                                                               |

## [DEFAULT]

Required keys have no default; the site must set them ([Install HPCPerfStats](../tutorials/install-hpcperfstats.md#step-6-configure-hpcperfstatsini)).

| Key                         | Default                         | Meaning                                                           |
| --------------------------- | ------------------------------- | ----------------------------------------------------------------- |
| `machine`                   | None                            | Cluster name                                                      |
| `host_name_ext`             | None                            | FQDN suffix of the cluster nodes                                  |
| `server`                    | None                            | FQDN of the container host; nginx `server_name` list              |
| `restricted_queue_keywords` | None                            | Queue-name substrings whose jobs are skipped at accounting ingest |
| `staff_email_domain`        | None                            | Email domain whose users are staff                                |
| `timezone`                  | None                            | Cluster local timezone                                            |
| `secret_key`                | None                            | Django secret key                                                 |
| `total_cores`               | `40`                            | CPU budget for application parallelism                            |
| `data_dir`                  | `/hpcperfstats/`                | Root of the data volume                                           |
| `debug`                     | `no`                            | Django debug mode                                                 |
| `engine_name`               | `django.db.backends.postgresql` | Django database engine                                            |
| `dbname`                    | `hpcperfstats`                  | Database name                                                     |
| `username`                  | `hpcperfstats`                  | Database role                                                     |
| `password`                  | `hpcperfstats`                  | Database password                                                 |
| `host`                      | `/run/postgresql`               | Database host; the default is the `db` Unix socket                |
| `port`                      | `5432`                          | Database port                                                     |

`conf_parser.get_effective_cores()` computes `effective_cores = min(ini total_cores, os.cpu_count())` from `[DEFAULT] total_cores`, with `os.cpu_count()` floored at 1.
Pool and worker sizes are absolute INI keys and do not scale with it; `sync_timedb` and `update_metrics` log `effective_cores=` at startup next to their pool sizes.

## [PORTAL]

`[PORTAL]` holds Gunicorn and Django web settings only.

| Key                                         | Default  | Effect                                                                                                              |
| ------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------- |
| `gunicorn_workers`                          | `32`     | Gunicorn workers (floor 1); a non-empty `WEB_CONCURRENCY` env value replaces it (`services-conf/django_startup.sh`) |
| `api_small_executor_max_workers`            | None     | `ThreadPoolExecutor` size per worker in `api.py`; falls back to `parallel_db_prefetch_max` when unset               |
| `parallel_db_prefetch_max`                  | `4`      | Parallel ORM prefetch per request                                                                                   |
| `db_conn_max_age`                           | `90`     | Django `CONN_MAX_AGE` in seconds                                                                                    |
| `db_statement_timeout_ms`                   | `120000` | Session `statement_timeout`                                                                                         |
| `db_idle_in_transaction_session_timeout_ms` | `300000` | Session `idle_in_transaction_session_timeout`                                                                       |
| `cors_origin_scheme`                        | Empty    | CORS origin scheme                                                                                                  |
| `separate_test_login`                       | `no`     | Development-only separate test login                                                                                |

`conf_parser.get_db_conn_max_age()` reads `CONN_MAX_AGE`, and `conf_parser.build_postgres_connection_options()` builds the Django `OPTIONS` with both timeouts.
`hpcperfstats/site/hpcperfstats_site/settings.py` applies `CONN_MAX_AGE` and the PostgreSQL `OPTIONS`.
The `db` service sets the same two server parameters, so non-Django clients inherit them.

## [PIPELINE]

`[PIPELINE]` holds ingest, archive and metrics settings: the paths plus the optional `sync_*`, `metrics_*`, `archive_*` and `listend_db_ingest_*` keys.

### Paths

| Key                 | Default                      | Holds                      |
| ------------------- | ---------------------------- | -------------------------- |
| `acct_path`         | `%(data_dir)s/accounting`    | Daily accounting files     |
| `archive_dir`       | `%(data_dir)s/archive`       | Raw monitor archive        |
| `daily_archive_dir` | `%(data_dir)s/daily_archive` | Daily tars and sealed tars |

### Archive zstd

`sync_timedb` seals each day's `YYYY-MM-DD.tar` to `YYYY-MM-DD.tar.zst` with zstd inside `pipeline`.

| Key                             | Default | Effect                                                                                                     |
| ------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------- |
| `archive_zstd_threads`          | `4`     | Seal, restore and archive decompress: `4` -> zstd `-T4`; `0` -> `-T0` (all logical cores per zstd process) |
| `ingest_zstd_threads`           | `4`     | Ingest and populate sealed streams: `4` -> `-T4`; `0` -> `-T0`                                             |
| `archive_seal_parallel_workers` | `2`     | Max daily tar seals at once (process-wide seal slots); set `1` on a saturated shared volume                |
| `archive_zstd_level`            | `9`     | Compression level                                                                                          |
| `sync_pipeline_drop_page_cache` | `yes`   | Linux-only `posix_fadvise` on pipeline file I/O (ingest streaming, archive zstd, tar paths)                |

`archive_zstd_threads` and `archive_zstd_level` are also the `--threads` and `--level` defaults of `scripts/seal_open_daily_tars_to_zst.sh` and `scripts/migrate_daily_archive_gz_to_zst.sh`.
A `day_close` with no free seal slot yields `reason=seal_slot_busy`, and its slot does other work.
Env `SYNC_ARCHIVE_SEAL_WORKERS` overrides the per-call seal worker count inside `seal_dirty_daily_archives`, as `SYNC_ARCHIVE_VALIDATION_WORKERS` does for validation fanout; the process-wide seal slot cap (`try_claim_seal_slot`) reads only `archive_seal_parallel_workers`.

### Day close and archive slots

| Key                                          | Default | Effect                                                                                        |
| -------------------------------------------- | ------- | --------------------------------------------------------------------------------------------- |
| `sync_day_close_max_inflight`                | `8`     | Parallel `day_close` worker threads                                                           |
| `sync_day_close_min_age_hours`               | `32`    | Min hours after calendar day end before `day_close` may complete                              |
| `sync_day_close_raw_paths_per_batch`         | `1000`  | Incremental raw deletes per `day_close` batch                                                 |
| `sync_ingest_hot_days`                       | `3`     | Hot-band calendar-day window for ingest ZSET scoring (min 1)                                  |
| `archive_keep_uncompressed_tar`              | `no`    | `no` drops a prior-day `.tar` at seal when raw is gone; `yes` keeps it until tar-drop         |
| `archive_today_uncompressed_tar_grace_hours` | `24`    | Hours to keep the calendar-today `.tar` after local midnight when keep is `no`                |
| `sync_archive_pool_processes`                | `4`     | Archive thread pool workers and concurrent daily-tar append slots (one day per slot)          |
| `sync_timedb_tar_append_batch_size`          | `256`   | Max raw paths per append batch (one clone-append job per daily tar)                           |
| `sync_archive_validation_max_workers`        | `2`     | Parallel raw-remove validation workers                                                        |
| `sync_archive_require_db_ingest`             | `yes`   | Raw is appended or removed only after its first and last timestamp seconds are in `host_data` |

`sync_ingest_hot_days` scores a day hot when `age < N`: today plus the prior N-1 days when N>=2, today only when N=1.
On a reflink filesystem the append clones member data (`FICLONERANGE`) and writes only headers, so batch size does not bound append time.

### Ingest pools

| Key                                      | Default | Effect                                                                                                                                           |
| ---------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sync_ingest_pool_processes`             | `64`    | Parse pool width (threads on 3.14t), archive metadata discovery threads and Codon worker process count; in-flight ingest leases equal it         |
| `sync_ingest_write_pool_processes`       | `0`     | Max concurrent ingest DB sessions and write threads; `0` = 1/2 parse (`32` when parse is `64`)                                                   |
| `sync_ingest_parse_queue_max`            | `0`     | Parse -> write queue slots; `0` = 2x `sync_ingest_pool_processes`                                                                                |
| `sync_ingest_queue_max_size`             | `50000` | Catch-up files discover admits before it skips the rest until the next whole-archive walk; handoffs, readmits and hot files are admitted past it |
| `sync_ingest_rescan_mtime_days`          | `1`     | Incremental discover window (days)                                                                                                               |
| `sync_ingest_full_rescan_interval_hours` | `6`     | Whole-archive walk interval (also at boot, and sooner once the queue drains to half the cap)                                                     |

`get_sync_ingest_pool_processes()` and `get_sync_archive_pool_processes()` read the pool widths.
Codon workers are capped at `MAX_CODON_SLOTS` = 128, with no separate worker-count knob.
Do not raise `sync_ingest_pool_processes` above 64 without the checks in [Pipeline concurrency](../explanation/pipeline-concurrency.md#ingest-parse-width).

### Ingest parsing

| Key                                         | Default     | Effect                                                                                                                           |
| ------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `sync_ingest_max_file_read_bytes`           | `536870912` | Stream-parse segments above 512 MiB instead of `readlines()`                                                                     |
| `sync_ingest_parse_flush_max_mib`           | `512`       | Soft max MiB per streaming parse -> write flush (sample-boundary overage OK); the sole operator batch-size knob for flush sizing |
| `sync_codon_parse_flush_max_mib`            | `1`         | Codon parse window (MiB of input)                                                                                                |
| `sync_codon_worker_recycle_rss_mib`         | `1536`      | Replace a Codon worker between files once its RSS passes this                                                                    |
| `sync_ingest_stream_duplicate_scan_bytes`   | `8388608`   | Route the duplicate scan through the streaming path above this size (even below `sync_ingest_max_file_read_bytes`)               |
| `sync_ingest_db_complete_tail_window_lines` | `500`       | Tail timestamp lines probed before a full duplicate scan on large head-present files                                             |

The default write path is COPY -> staging -> `ON CONFLICT` (`sync_timedb_host_data_insert`, `sync_timedb_proc_data_insert`); `HPCPERFSTATS_SYNC_HOST_DATA_COPY=0` and `HPCPERFSTATS_SYNC_PROC_DATA_COPY=0` opt out.
Host COPY rows are re-merged to `_ingest_write_frame_slice_rows()` (10k) per write; proc rows are never merged (`ON CONFLICT DO UPDATE`).

### Ingest memory

| Key                                           | Default | Effect                                                                                                   |
| --------------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------- |
| `sync_process_tree_rss_limit_cgroup_pct`      | `40`    | Tree RSS roof as a percent of runtime `memory.max`; `0` disables defer and the raw-byte budget           |
| `sync_process_tree_rss_exit_cgroup_pct`       | `0`     | Optional hard exit percent (`0` = off)                                                                   |
| `sync_cgroup_admit_headroom_mib`              | `4096`  | Multi-file ingest admit and append fill need this many MiB free (`0` = off)                              |
| `sync_cgroup_admit_max_file_cache_cgroup_pct` | `60`    | Block admit while active file cache exceeds this percent of `memory.max` (`0` = off)                     |
| `sync_ingest_malloc_trim_after_file`          | `no`    | `gc.collect()` + `malloc_trim(0)` after each ingest pool task; `yes` also clears daily archive member L1 |
| `sync_ingest_stuck_inflight_recycle_s`        | `3600`  | Recycle the ingest pool when the oldest in-flight submit is older (`0` = off)                            |
| `sync_timedb_mem_telemetry`                   | `no`    | Every gated admit line carries `sync_timedb_mem_telemetry=yes`                                           |

The effective roof in MiB is `(max x pct // 100) // (1024x1024)`: 52428 MiB at the default 40 on a 128 GiB cgroup.
The in-flight raw admit budget is `effective_roof_bytes / PEAK_CGROUP_PER_RAW_FILE_BYTE`, with `PEAK_CGROUP_PER_RAW_FILE_BYTE` = 1.0, so the budget equals the roof, with no separate budget INI.
The headroom gate computes `memory.max - (memory.current - inactive_file)`, and the file-cache gate compares cgroup `memory.stat` `file - inactive_file` with `memory.max x pct`.

Starting points:

- Large sites use the example defaults: `sync_ingest_pool_processes=64`, `sync_ingest_malloc_trim_after_file=no`, `sync_process_tree_rss_limit_cgroup_pct=40`.
- Set `sync_ingest_malloc_trim_after_file` to `yes` where the tree RSS roof is small (Horizon / hpcperfstats04), and lower `sync_ingest_pool_processes` to 32 if ingest admit defers there.
- On 8-16 GiB dev compose hosts, set `sync_cgroup_admit_max_file_cache_cgroup_pct=0` with `sync_process_tree_rss_limit_cgroup_pct=0` (slot-only admit), and lower `sync_ingest_pool_processes` if RAM-bound.

### sync_timedb stall and archive CLI

| Key                                              | Default | Effect                                                                                      |
| ------------------------------------------------ | ------- | ------------------------------------------------------------------------------------------- |
| `sync_ingest_stall_idle_s`                       | `1800`  | Soft-requeue when parse or write shows no progress for this many seconds (`0` disables)     |
| `sync_ingest_per_file_timeout_max_s`             | `86400` | Job-store lease EX / external ceiling (24h); not an ingest wall soft-kill                   |
| `sync_pool_poll_timeout_s`                       | `5`     | Poll interval between progress checks in the `sync_timedb_archive.py` sliding window        |
| `sync_timedb_archive_max_concurrent_sealed_days` | `1`     | Sealed days in flight in `sync_timedb_archive.py` (capped at `sync_archive_pool_processes`) |
| `sync_pool_stall_defer_log_interval_s`           | `60`    | Min wall seconds between repeated `WARN: pool imap stall deferred` lines (`0` = every poll) |

### Archive members store

| Key                                            | Default | Effect                                                                                         |
| ---------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------- |
| `sync_archive_members_cache_enabled`           | `yes`   | Per-process L1 cache on the ingest duplicate-check path                                        |
| `sync_archive_members_cache_max_entries`       | `64`    | Max cached days per ingest or archive worker process                                           |
| `sync_archive_members_populate_max_seconds`    | `0`     | Optional absolute populate and wait cap in seconds (`0` = off; idle and heartbeat limits only) |
| `sync_archive_members_populate_pool_processes` | `4`     | Dedicated `populate-pool` threads for sealed and tar store streaming                           |
| `sync_archive_members_wait_poll_seconds`       | `0.25`  | Poll interval while waiting on a populate                                                      |

The store persists complete member maps under `{archive_dir}/.sync_timedb_archive_members/`; the other `sync_archive_members_populate_*` keys are commented in `hpcperfstats.ini.example`.

### Telemetry

| Key                     | Default | Effect                                                                                                |
| ----------------------- | ------- | ----------------------------------------------------------------------------------------------------- |
| `sync_ingest_telemetry` | `no`    | `ingest file` outcome lines add write and parse phase tokens plus `orm_rows` and `overlap_headroom_s` |

Enable `[PIPELINE] sync_ingest_telemetry = yes` in the site INI on the same code redeploy that needs it; never bake it on in `hpcperfstats.ini.example` and never use pipeline env for it.

### listend

| Key                                | Default | Effect                                                            |
| ---------------------------------- | ------- | ----------------------------------------------------------------- |
| `listend_amqp_consumer_count`      | `16`    | Competing AMQP consume threads (floor 1, no upper clamp)          |
| `listend_amqp_prefetch`            | `32`    | Per-consumer `basic_qos` in drop mode                             |
| `listend_db_ingest_enabled`        | `no`    | Optional live DB dual-write                                       |
| `listend_db_ingest_pool_processes` | `32`    | Live DB ingest threads when enabled                               |
| `listend_db_ingest_batch_samples`  | `100`   | Flush after this many samples                                     |
| `listend_db_ingest_flush_max_rows` | `2000`  | Flush after this many pending rows                                |
| `listend_db_ingest_flush_hold_s`   | `5`     | Flush after this hold age                                         |
| `listend_db_ingest_backpressure`   | `drop`  | `drop` (archive + ack, shed live DB via `queue_drops`) or `pause` |

### update_metrics pools

| Key                                   | Default | Effect                                                                           |
| ------------------------------------- | ------- | -------------------------------------------------------------------------------- |
| `metrics_pool_processes`              | `32`    | In-process metrics threads                                                       |
| `metrics_prewarm_processes`           | `auto`  | Bokeh prewarm worker processes; `auto` = one per metrics thread, `0` = in-thread |
| `metrics_prewarm_max_tasks_per_child` | `100`   | Recycle a prewarm worker after this many tasks                                   |
| `metrics_gc_threshold`                | `0`     | Passed to `gc.set_threshold` at scheduler start (`0` = interpreter default)      |
| `metrics_run_stall_timeout_s`         | `900`   | Scheduler soft-fails pending JIDs after this long without progress               |
| `metrics_scheduler_pass_interval_s`   | `60`    | Fresh-day relist interval                                                        |

### Metrics memory admission

| Key                                 | Default | Effect                                                                                                                                                                                   |
| ----------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `metrics_memory_budget_pct`         | `40`    | Estimated memory of all in-flight jobs stays under this percent of the pipeline cgroup `memory.max` (or host `MemTotal` when unlimited); one job always runs alone; `0` = count cap only |
| `metrics_memory_budget_head_wait_s` | `600`   | A job too large for the free budget may be passed by smaller jobs this long; then its lane waits for it                                                                                  |
| `metrics_admit_headroom_mib`        | `16384` | No new job starts while another runs when headroom is below this (`0` = off)                                                                                                             |

Keep `metrics_memory_budget_pct` plus the `sync_timedb` roof percent well under 100; at the defaults they total 80.

### Metrics backfill

| Key                                    | Default | Effect                                                                                      |
| -------------------------------------- | ------- | ------------------------------------------------------------------------------------------- |
| `metrics_backfill_enabled`             | `yes`   | `metrics_backfill_enabled = no` turns the lane off (dirty catch-up of processed jobs stays) |
| `metrics_backfill_min_slots`           | `0`     | Lane floor; `0` = `metrics_pool_processes // 5`, at least 1                                 |
| `metrics_backfill_fresh_reserve_slots` | `2`     | Threads never given to the lane                                                             |
| `metrics_backfill_max_jobs_per_hour`   | `0`     | Lane rate ceiling (`0` = unlimited)                                                         |

The lane's slot cap is `max(metrics_backfill_min_slots, metrics_pool_processes - metrics_backfill_fresh_reserve_slots - fresh jobs active)`.
`update_metrics.py --backfill-status` prints the remaining counts.

### Metrics readiness

| Key                                         | Default | Effect                                                                      |
| ------------------------------------------- | ------- | --------------------------------------------------------------------------- |
| `metrics_readiness_require_window_coverage` | `yes`   | Defer a job until in-window `host_data` samples exist near both Slurm edges |
| `metrics_readiness_start_margin_seconds`    | `600`   | Margin after Slurm `start_time`                                             |
| `metrics_readiness_end_margin_seconds`      | `600`   | Margin before Slurm `end_time`                                              |
| `metrics_deferred_not_ready_retry_s`        | `10`    | Retry interval for deferred jobs                                            |
| `metrics_deferred_not_ready_quarantine_s`   | `300`   | Quarantine interval after the retries                                       |

Set `metrics_readiness_require_window_coverage = no` only for recovery or backfill emergencies.

### Metrics statement timeouts

| Key                                    | Default  | Effect                                                                                                                 |
| -------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------- |
| `metrics_worker_statement_timeout_ms`  | `120000` | Session timeout during `compute_metrics` (`0` disables); non-zero arms `host__in` chunk-on-timeout splitting           |
| `metrics_persist_statement_timeout_ms` | `120000` | Parent persist timeout                                                                                                 |
| `metrics_copy_statement_timeout_ms`    | `600000` | Each columnar `host_data` COPY (8 hosts x \<=15 min) in its own transaction (`0` disables; not applied under gunicorn) |

Do not raise the global `db_statement_timeout_ms` for long `host_data` compute; use `[PIPELINE] metrics_worker_statement_timeout_ms` instead.

### Ingest coverage

| Key                                | Default | Effect                                                         |
| ---------------------------------- | ------- | -------------------------------------------------------------- |
| `ingest_coverage_sweep_enabled`    | `yes`   | `no` makes the `ingest_coverage_sweeper` program exit at start |
| `ingest_coverage_sweep_interval_s` | `3600`  | Sweep interval                                                 |
| `ingest_coverage_window_minutes`   | `60`    | Window per host, ending at its newest sample                   |
| `ingest_coverage_query_timeout_ms` | `60000` | Per-host query timeout                                         |

## [RMQ]

| Key          | Default       | Meaning                               |
| ------------ | ------------- | ------------------------------------- |
| `rmq_server` | `rabbitmq`    | Broker hostname (the Compose service) |
| `rmq_queue`  | `%(machine)s` | Ingest queue name                     |

Change `[RMQ]` hostnames only when the RabbitMQ layout differs from Compose.

## [CACHE]

| Key               | Default                               | Meaning                                    |
| ----------------- | ------------------------------------- | ------------------------------------------ |
| `valkey_location` | `unix:///run/valkey/valkey.sock?db=1` | Valkey URL for the web and `listend` cache |

Django `[CACHE] valkey_location` serves the web and `listend` cache only; `sync_timedb` keeps its member maps in process.

## [SYSLOG]

| Key          | Default | Meaning                                                           |
| ------------ | ------- | ----------------------------------------------------------------- |
| `allow_from` | Empty   | IPv4 CIDRs (comma- or line-separated) that may send remote syslog |
| `listen_tcp` | `yes`   | TCP listener on                                                   |
| `listen_udp` | `yes`   | UDP listener on                                                   |

An example `allow_from` is `10.0.0.0/8, 192.168.50.0/24`.
If `allow_from` is blank or `[SYSLOG]` is omitted, all IPv4 sources are accepted.
A change needs a new `render_syslog_ng_generated` run and a syslog-ng restart ([Cluster syslog](compose-services.md#cluster-syslog)).

## Environment overrides

These variables override INI or code defaults inside the containers; production sets the INI keys instead.

| Variable                                                    | Overrides                                                           |
| ----------------------------------------------------------- | ------------------------------------------------------------------- |
| `WEB_CONCURRENCY`                                           | `[PORTAL] gunicorn_workers`                                         |
| `DJANGO_CONN_MAX_AGE`                                       | `[PORTAL] db_conn_max_age`                                          |
| `DJANGO_DB_STATEMENT_TIMEOUT_MS`                            | `[PORTAL] db_statement_timeout_ms` (`0` disables)                   |
| `DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`                  | `[PORTAL] db_idle_in_transaction_session_timeout_ms` (`0` disables) |
| `HPCPERFSTATS_METRICS_WORKER_STATEMENT_TIMEOUT_MS`          | `[PIPELINE] metrics_worker_statement_timeout_ms`                    |
| `SYNC_ARCHIVE_SEAL_WORKERS`                                 | Per-call seal workers in `seal_dirty_daily_archives`                |
| `SYNC_ARCHIVE_VALIDATION_WORKERS`                           | Validation fanout                                                   |
| `HPCPERFSTATS_SYNC_HOST_DATA_COPY`                          | `0` opts out of the `host_data` COPY write path                     |
| `HPCPERFSTATS_SYNC_PROC_DATA_COPY`                          | `0` opts out of the `proc_data` COPY write path                     |
| `MKL_NUM_THREADS`, `OMP_NUM_THREADS`, `NUMEXPR_NUM_THREADS` | Inner BLAS threads for `update_metrics` (default 1)                 |

## PostgreSQL connection budget

`estimate_django_pg_slot_peak()` in `hpcperfstats/dbload/lib/pg_slot_budget.py` estimates peak connections:

```text
gunicorn_workers x max(api_small_executor_max_workers, parallel_db_prefetch_max)
+ listend_db_ingest_pool_processes + metrics_pool_processes
+ metrics_prewarm_processes + public_ef_pool(4)
+ sync_ingest_pool_processes + sync_day_close_max_inflight
+ sync_archive_pool_processes + reserve(32)
```

With INI defaults: `32 x 4 + 32 + 32 + 32 + 4 + 64 + 8 + 4 + 32 = 336`, under `max_connections=500` and the 400 WARN threshold.
The estimate counts ingest at parse width (`sync_ingest_pool_processes`); concurrent ingest DB sessions are bounded by `sync_ingest_write_pool_processes`, so the ingest term is an upper bound.
Startup logs a WARN at 80% of 500 (`log_pg_slot_budget_if_needed`); that check is log-only.

## Related

- [Pipeline concurrency](../explanation/pipeline-concurrency.md)
- [Compose services](compose-services.md)
- [Install HPCPerfStats](../tutorials/install-hpcperfstats.md)
- [`hpcperfstats.ini.example`](../../hpcperfstats.ini.example)
- [`conf_parser.py`](../../hpcperfstats/dbload/lib/conf_parser.py)
