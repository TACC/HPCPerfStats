# Pipeline concurrency

This page explains how many threads, processes and connections each HPCPerfStats service runs, how they share memory, disk and PostgreSQL, and why the defaults are set where they are.
It is for operators sizing a site and developers changing pool, memory or archive code.

Every service on the host competes for three budgets: PostgreSQL `max_connections`, the pipeline cgroup `memory.max`, and the disk and CPU that `web`, `db` and `pipeline` share.
Pool sizes are absolute INI keys, so each site sets them against those budgets; the keys and defaults are in [Pipeline configuration](../reference/pipeline-configuration.md).

## Services and parallelism

| Service and program | Parallelism                                                                                                     | Database impact                                                                                      |
| ------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `web` Gunicorn      | `[PORTAL] gunicorn_workers` workers; `api.py` adds a `ThreadPoolExecutor` per worker                            | Each worker may hold a persistent connection (`CONN_MAX_AGE`); the executor adds concurrent ORM work |
| `update_metrics.py` | `metrics_pool_processes` titled in-process threads, plus `metrics_prewarm_processes` forkserver prewarm workers | One `COPY (FORMAT BINARY)` reader per job chunk, plus one connection per prewarm worker process      |
| `sync_timedb.py`    | Ingest, append, populate and `day_close` thread pools under one orchestrator                                    | Ingest write sessions, `day_close` and append slots                                                  |
| `listend.py`        | N AMQP consume threads plus 2 x N host-affine archive threads; optional live DB threads                         | Low on the archive path; DB slots when live ingest is on                                             |
| `db`                | `max_connections=500`, parallel workers capped                                                                  | Hard slot ceiling; per-query memory bounded by GUCs                                                  |

### update_metrics

Each metrics thread runs one job's metrics through the baked Codon kernels (`/opt/hpcperfstats/codon_metrics`, `Py_MOD_GIL_NOT_USED`, single-threaded kernels, `OMP_NUM_THREADS=1`).
The Bokeh detail and plot prewarm then runs in forkserver worker processes titled `[worker:prewarm-pool]`, recycled every `metrics_prewarm_max_tasks_per_child` tasks.
Operators see `[thread:metrics-pool]` and `[thread:public-ef-pool]` threads under one `update_metrics.py [main]` process, plus the prewarm processes.

`update_metrics` calls `configure_blas_thread_env()` before numpy, so inner BLAS and VML threads default to 1 and outer parallelism is `metrics_pool_processes`.
Operator env (`MKL_NUM_THREADS`, `OMP_NUM_THREADS`, `NUMEXPR_NUM_THREADS` and the other `setdefault` keys) still wins.
Raising inner BLAS threads to the core count while the metrics pool is large oversubscribes the CPU.

### sync_timedb

The production coordinator is the in-process job-store queue orchestrator `run_sync_timedb_queue_orchestrator`.
It holds an exclusive `archive_dir` flock and runs ingest, append and `day_close` jobs on sliding-window thread pools.
Ingest uses `get_sync_ingest_pool_processes()` threads and archive append uses `get_sync_archive_pool_processes()`; concurrent daily-tar append slots track that pool size, one day per slot.
The cold path (seal -> raw removal -> tar-drop) runs on `day_close` threads, at most `sync_day_close_max_inflight`.

The orchestrator keeps at most `sync_ingest_pool_processes` ingest leases in flight and refills on completion, with reserved hot and catch-up slots.
`ingest file path=` lines reflect completion order across parallel workers, so they may permute among simultaneous dispatches.
Scheduling order is the job-store ingest ZSET score bands: hot newest-first and catch-up oldest-first.

### listend

`listend` runs `listend_amqp_consumer_count` competing AMQP consume threads, each with its own pika `BlockingConnection`, on the original ingest queue.
Host-affine archive threads (2 x consumer count, 32 by default; not an INI key) do the durable `current` append and rotate, and ack on the originating connection threadsafe.
They peek only the first line or `$` header for host and timestamp, reorder per host by timestamp when N>1, keep raw AMQP bytes, ack before the live-DB submit, and hold a sticky flock with a kept `current` fd.
Drop-mode `listend_amqp_prefetch` is per consumer, so process-wide unacked messages are 32xN; pause keeps prefetch 1 per consumer.
Client and broker use `frame_max=131072`.

The optional live DB dual-write runs on host-affine threads that share ordinary `queue.Queue` objects (no `/dev/shm`, no spawn).
Its queue budget is 8 GiB total, hard-capped at 8, so INI values above 8 are clipped; the budget is parse debt plus held archive fds.
A flush is bounded by sample count, pending rows or hold age, so a never-empty queue still writes Timescale.
With backpressure `drop`, `listend` archives and acks and sheds the live DB copy; with `pause`, a high watermark of 0.95 (or any full worker) stops consuming without archiving until the queue falls to 0.50, and the overflow stays on the broker.
Size live ingest with `sync_ingest_pool_processes` against Postgres `max_connections`.
When live ingest is on, archive-backed catch-up stays the completeness repair for live gaps, and archive or delete still needs a `sync_timedb` file-complete mark.

## Archive zstd on a shared host

Daily monitor archives are sealed inside `pipeline`, and `web`, `db` and `pipeline` share one CPU scheduler and often one physical disk (`postgres_data` vs `/data/hpcperfstats_data/site_data`).
Archive zstd runs at normal CPU and I/O priority (no `nice`/`ionice` wrapper), and space comes back only when a seal finishes.

Compression speed scales with `archive_zstd_threads`, so pick `archive_zstd_level` at the seal's thread count.
A seal runs at the slower of two rates: the disk's per-seal read rate and zstd's compression speed at `-T<archive_zstd_threads>`.
Choose the highest level whose speed at that thread count stays above the read rate one seal gets on the daily archive volume (total read rate divided by `archive_seal_parallel_workers`); above it seals get slower, below it they only cost ratio.

The default level 9 is the knee of a measurement on hpcperfstats01 (2026-11-04), compressing 512 MiB of a daily tar at `-T4` (MB/s in, ratio):

| Level | MB/s | Ratio |
| ----- | ---- | ----- |
| 3     | 757  | x4.4  |
| 5     | 414  | x4.6  |
| 7     | 251  | x4.9  |
| 9     | 257  | x9.1  |
| 11    | 150  | x9.6  |
| 13    | 41   | x9.7  |
| 15    | 24   | x10.1 |
| 17    | 16   | x18.3 |
| 19    | 10   | x19.0 |

The archive volume was 100% busy at about 105 MB/s read, and one seal there got about 24-40 MB/s.
Level 9 runs as fast as 7 at twice the ratio and far above the disk; 13 and up are slower than the disk at `-T4`, and 17-19 halve the file size but run 2-4x slower than the disk.
Before changing it, benchmark a representative daily tar on the pipeline host at the seal's thread count: `zstd -b3 -e19 -T4 -S -- ./YYYY-MM-DD.tar`.

Tuning follows from the same model:

- Seals too slow and the disk not saturated: raise `archive_seal_parallel_workers`.
- Disk saturated: more parallel seals only split the bandwidth; set `1` on a saturated shared volume, which frees each tar's space soonest for the same total time.
- Web, API or Postgres latency spikes during a seal: lower `archive_seal_parallel_workers` or `archive_zstd_threads`.

To inspect an archive by hand, use `zstd -d -o YYYY-MM-DD.tar YYYY-MM-DD.tar.zst`.
`sync_timedb` also reads and restores daily `YYYY-MM-DD.tar.gz` archives through `zstd --format=gzip`; inspect one with `zstd -d --format=gzip`.

### Page cache

Large daily archives and parallel ingest raw reads fill the Linux page cache.
With `sync_pipeline_drop_page_cache=yes`, `zstd_cli.py` issues `POSIX_FADV_SEQUENTIAL` before large scans and `POSIX_FADV_DONTNEED` after one-shot paths and streaming read batches; post-ingest raw `POSIX_FADV_DONTNEED` follows the same key.
It is a hint only (not `O_DIRECT`), a no-op on macOS dev hosts, and not a substitute for lowering ingest pool width.
On a 192g pipeline cgroup at 60% file-cache admit, the effective cap is about 117965 MiB; tune `sync_cgroup_admit_max_file_cache_cgroup_pct` only as an admit tripwire.

## Day close

Ingest is always on under `run_sync_timedb_queue_orchestrator`.
Cold-path archive work is job-store `day_close` jobs drained by a `ThreadPoolExecutor` of at most `sync_day_close_max_inflight` threads.

```mermaid
flowchart LR
  accTitle: day_close stages
  accDescr: day_close passes the age gate, dedupes the open tar, seals it, removes raw in batches, then drops the tar.
  A[age gate] --> D[dedupe open tar]
  D --> S[seal_dirty_daily_archives]
  S --> R[DayRawRemovalCoordinator.apply_batch_delete]
  R --> T[tar-drop when sealed and no closed raw remains]
```

A day closes only after it is old enough, sealed, and stripped of raw files, so raw stays the source of truth until every later stage succeeds.

Progress and `day_close` eligibility come from the in-process job store plus reconstruct from disk and Timescale; the checkpoint `.sync_timedb_state.json` is not the reconstruct source of truth.
`day_close` jobs refill continuously up to `sync_day_close_max_inflight`.
Append slots (`sync_archive_pool_processes`) and `day_close` slots are fixed caps of one calendar day or daily tar per slot; overflow days drain when a slot finalizes.
One calendar day is always single-writer; on backlog sites with many open daily tars, keep `sync_archive_pool_processes` at 4+.
Smaller append batches finish gate and tvf sooner; pair `sync_timedb_tar_append_batch_size` with the site ingest pool as needed.

For a multi-week backlog or disk pressure, raise `sync_day_close_max_inflight` and keep `sync_day_close_raw_paths_per_batch` high enough for the daily file count; 15k files a day may need several internal batches in one `day_close` pass.
On very large trees (about 100k+ closed paths), `sync_day_close_max_inflight` sets both pipeline occupancy and parallel workers.
Ingest never waits for `day_close`.

With the default `archive_keep_uncompressed_tar=no`, a prior-day `.tar` is dropped at seal once raw is gone, and calendar-today keeps its `.tar` for `archive_today_uncompressed_tar_grace_hours` (24h after local midnight).
Set global `archive_keep_uncompressed_tar=yes` during heavy same-day append if late raw appends need the uncompressed `.tar`.

### Duplicate tar members

`_run_day_close_job` runs a `dedupe` stage on the open `.tar` before seal (`dedupe_tar_keep_largest_file_per_member`, which keeps the largest file per member name).
It runs only when the open daily `.tar` exists (with no open tar the job takes the `no_tar_handoff` stage instead) and `skip_merge_remaining_raw` is false.
It then runs only after `reconcile_merge` and the second `pre_seal_verify` / `wait_on_ingest` checks return without yielding.

`skip_merge_remaining_raw` is set, so `reconcile_merge` and `dedupe` are skipped, in two cases:

- The coordinator phase is `done` and a sealed `.tar.zst` / `.tar.gz` sibling exists (`reason=phase_done_sealed`).
- Raw remains or a handoff is wanted, but no append or hot ingest holds the tar (`reason=append_idle`).

Log lines: `queue_orchestrator day_close stage_enter day=... stage=dedupe`, `queue_orchestrator day_close stage_exit day=... stage=dedupe result=ok|yielded|fail`, and on error `queue_orchestrator day_close dedupe fail day=... err=...`.
Populate still records a members-store `dedupe_hint` for days with duplicate members; no production path reads it.

### Seal and append locks

`atomic_seal_tar_to_zst` holds an exclusive `file_write_lock` on the daily `.tar` for the whole compress-and-replace window.
Hot-path append uses the same lock with a 60s default timeout, so a large-day seal during ingest can surface an append `TimeoutError` (fail-closed).
The mitigations: use `archive_keep_uncompressed_tar=yes` (or rely on today's grace window) during heavy same-day append, isolate the `pipeline` CPU budget from `web` and `db` where possible, and rely on `day_close` yielding to the hot path when ingest or populate needs the tar (`sync_timedb_day_close_cooperation.py`).

Parallel raw-remove validation takes read locks, which is why `sync_archive_validation_max_workers=2`; raise it only when append and read-lock contention is acceptable.

## Ingest memory

Ingest workers are threads inside the `sync_timedb.py` process (`SyncTimedbThreadPool`, role `ingest-pool`), so a multi-GiB anon spike while parsing a giant raw segment counts against that process.
On the free-threaded image, file parses run in Codon worker processes (`python -m hpcperfstats.dbload.lib.codon_process_worker`), one per concurrent file, at most `MAX_CODON_SLOTS` = 128.
A kernel or cgroup OOM kill of one fails that file's parse with `codon worker EOF rc=-9`, and the slot starts a fresh worker on its next checkout.

Defences against OOM, outermost first:

| Mitigation                                                       | Role                                                                                                                                                            |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline` `mem_limit` / `memswap_limit`                         | Cgroup cap before host global OOM on swapless hosts; 128 GiB fits a 192 GiB RAM host ([Memory cap](../reference/compose-services.md#memory-cap))                |
| `PR_SET_PDEATHSIG` (SIGKILL) on `update_metrics` prewarm workers | Prewarm workers exit when `update_metrics.py` dies, so supervisord restarts a clean tree (`apply_pool_worker_process_title`); Codon parse workers do not set it |
| Tree RSS roof                                                    | `sync_process_tree_rss_limit_cgroup_pct` defers ingest admit above the roof                                                                                     |
| In-flight raw-byte budget                                        | Caps the raw bytes in flight at once                                                                                                                            |
| cgroup headroom and file-cache gates                             | Stop multi-file admit near the cgroup wall                                                                                                                      |
| Streaming parse                                                  | `sync_ingest_max_file_read_bytes` streams large segments instead of `readlines()`                                                                               |

### Ingest parse width

`sync_ingest_pool_processes` is also the Codon worker count, one worker per ingest thread, each with its own Codon runtime and Boehm GC; threads sharing one runtime would serialize on Boehm's allocation lock.
Do not raise it above 64 (toward 96 or 128) without watching census `job_store_wait_s` / `job_store_hold_s` and confirming `[thread:job-store-persist]` owns sidecar I/O.
Parse width 128 on a deep ZSET can futex-stall claim and ACK when wait tokens dominate ([`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`](../OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md)).

On the dev host (w64, 100g cgroup, 1 MiB windows), 32 workers peaked at 29.0 GiB combined worker RSS and 56.9 GiB cgroup, and 64 workers at 35.5 GiB and 64.7 GiB, with no OOM; both were DB-bound at about 155-160k `tup_inserted`/s.
Worker RSS counts toward the tree RSS roof, so on Horizon (51.2 GiB roof) lower the width to 32 if ingest admit defers.

Ingest write sessions are a separate, smaller pool: the connection budget uses `sync_ingest_write_pool_processes`, not the parse width.
The bounded parse -> write queue refuses new parse when full, and an in-flight parse blocks on `put`; the census reports `parse_put_block_s` / `_max_s` / `_n`.

### Codon worker heap

Codon worker heap follows output rows, not input bytes.
The densest corpus files peaked at 5.7-7.7 GiB per worker with 16 MiB windows and 0.9-1.1 GiB with 1 MiB, which is why `sync_codon_parse_flush_max_mib` defaults to 1.
Small windows would otherwise create tiny transactions and `LWLock/LockManager` stalls, so host COPY rows are re-merged before each write.

The Boehm heap never shrinks after a dense file, so a worker over `sync_codon_worker_recycle_rss_mib` is replaced between files.
A hard heap cap (`GC_MAXIMUM_HEAP_SIZE`) aborted workers mid-file and is not used.

### malloc_trim after each file

`sync_ingest_malloc_trim_after_file` defaults to `no` because the per-file `gc.collect()` is stop-the-world on free-threaded 3.14t.
In a dev soak (Codon on, width 64) it held about 80% of wall time (178 collections, 852 s of 1066 s).
`no` measured 225-255 MiB/min against 84-101 (n=3 vs n=5) at width 64 and 127-175 at width 32 (n=3), with RSS max 24 GB -> 51-64 GB (w64) and 32 GB (w32).
`malloc_trim` is a no-op under the image's `LD_PRELOAD` jemalloc (RSS was unchanged after free, `gc.collect()`, `malloc_trim` and a 25 s jemalloc decay window), so `no` loses only the `gc.collect()`.

The default `no` assumes the roof (`sync_process_tree_rss_limit_cgroup_pct` x `memory.max`, 52428 MiB on a 128 GiB cgroup) is above the observed peak; above the roof ingest admit defers.
Set `yes` where the roof is too small: Horizon has a 128 GiB cgroup and a 40% roof of 51.2 GiB, while Stampede3 has a 256 GiB cgroup and a 102 GiB roof that fits `no` at width 64.

### Tree RSS roof

The roof scales with runtime `memory.max`: 40% of a 128g cgroup leaves about 76 GiB outside the sync soft roof for `listend` and `update_metrics`.
`limit_cgroup_pct=0` disables defer and the byte budget, and the gates fail open when `memory.max` is unknown.
Fair-share cooperative recycle remains post-task only.

The same roof gates new ingest admission on measured sync tree RSS, with no separate INI key.
At or above the roof, `_fill_ingest_band` is skipped (`fill_block=skip_rss_pressure`, log `queue_orchestrator rss_hold rss_mib=... limit_mib=... inflight=...`, 30 s rate limit, `suppressed_n=`) while completions keep draining.
With nothing in flight and RSS still over the roof, one `gc.collect()` + `malloc_trim` runs (`rss_release rss_mib_before=... rss_mib_after=...`) and admission resumes.
This is the memory control with release off, because the raw-byte budget does not track release-off RSS: measured RSS per in-flight raw byte ranged 5-57x.

Dev soaks (128 GiB cgroup, width 64, release off):

- Roof 30% = 39321 MiB: 286 MiB/min, the gate held once.
- Roof 15% = 19660 MiB: 184 MiB/min, RSS 11-30 GiB, the gate held for minutes.
- Uncapped: 275 MiB/min, peaking at 42.8 GiB.

`rss_release` has not fired in any soak (RSS fell under the roof when the first file cohort completed); unit tests cover it.
On a site, filter the full pipeline log for `rss_hold\|rss_release` ([`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`](../OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md)).

### In-flight raw-byte admit

Ingest threads share one heap, so slot count alone can put many multi-GiB files in memory at once; pool width is not concurrent raw volume.
`_fill_ingest_band` admits a file only when `sum(inflight st_size) + candidate` fits the in-flight raw budget; otherwise it requeues the job (`skip_budget_bytes`).
A file larger than the budget is admitted alone when nothing is in flight.
While blocked, the census line appends `ingest_mem_blocked=yes inflight_raw_mib=... budget_mib=...`.

Two cgroup gates also apply to multi-file ingest admit and to append fill: the headroom gate (`skip_cgroup_headroom`) and the file-cache gate (`skip_file_cache_pressure`).
An oversized file admitted alone passes the headroom gate, and the file-cache gate still admits a file while at most one other is in flight, so fill cannot deadlock at `local_inflight=0`.
`skip_budget_bytes`, `skip_cgroup_headroom` and `skip_file_cache_pressure` requeue without an attempt bump, so they never dead-letter.

`sync_ingest_stuck_inflight_recycle_s` requests an ingest pool recycle through the MainThread pause protocol when the oldest in-flight submit is too old (mem-telemetry event `stuck_cohort_recycle`); collateral jobs requeue without an attempt bump.

## update_metrics memory admission

`update_metrics` admits jobs by estimated memory as well as by count (sliding session admission in [`docs/UPDATE_METRICS_STATE_MACHINE.md`](../UPDATE_METRICS_STATE_MACHINE.md)).
Count alone would let a burst of large backfill jobs, each holding its fetch peak, a parked `HANDOFF` frame, a copy shipped to its prewarm worker and the plot objects, drive the process past host memory.

A job's estimate is `estimated_sample_count` x learned rows per host-minute (floored at `METRICS_ROWS_PER_HOST_MINUTE`) x `METRICS_PEAK_BYTES_PER_ROW`, plus the metrics pivot grids, which are linear in host-minutes.
Once a job's metrics finish, its measured frame rows replace the estimate.
The headroom gate compares `metrics_admit_headroom_mib` with cgroup `memory.max - (current - inactive_file)`, or host `MemAvailable` when the cgroup is unlimited; it only stops new jobs, so jobs already in flight can still grow.
A job too large for the free budget can wait at the head for `metrics_memory_budget_head_wait_s`; then its lane (both lanes for a fresh job) waits for it.

The cost model rests on production numbers:

- Production jobs on hpcperfstats04 carry 238-335 frame rows per host-minute (the single-host golden corpus ran about 200), and the float64 fetch plus compute peak is 122-144 bytes per row on frames above a few thousand rows.
  The model floors rows per host-minute at the production p90 (`METRICS_ROWS_PER_HOST_MINUTE`) and learns a higher value from finished jobs.
- Large multi-host jobs peak far above that per-row figure while metrics run, almost all of it metrics pivot grids.
  Pivots hold one row per (host, sample time) pair of a type (value and arc), so a grid is linear in host-minutes; `get_type` returns read-only views.
- The pivot cache is bounded by `PIVOT_CACHE_MAX_BYTES` (least recently used evicted; an evicted type pivots again with the same values).
  The cost adds the largest grid plus the cache, floored at `METRICS_GRID_BYTES_PER_HOST_MINUTE` per host-minute and learned from finished jobs (`grid_b_hmin:` in the census `admit=` group).
  The floor is the widest type's events x 16 bytes x pairs per host-minute, measured on a 162-host x 551-minute unaligned synthetic tiled from a single-host golden job whose widest type has 38 events.

With work in flight, the once-a-minute `metrics census` line carries `mem_gib=handoff:,rss:,est:,meas:,budget:` and `admit=inflight:,rows_hmin:,grid_b_hmin:`.
`rss` well above `est + meas` means memory that no in-flight job accounts for: allocator retention or a leak.

### Shared limit

`update_metrics`, `sync_timedb` (its tree RSS roof, its ingest pool and any `sync_ingest_*` workers), `listend` and the prewarm worker processes all draw on the same pipeline cgroup `memory.max`, and neither budget counts the others.
Keep `metrics_memory_budget_pct` plus the `sync_timedb` roof percent well under 100, so `listend` and the prewarm children fit beside them.
On a host where ingest runs near its roof, lower `metrics_memory_budget_pct` rather than relying on the headroom gate.

### Free-threaded scaling

On the 3.14t pipeline interpreter, `update_metrics.py` imports `hpcperfstats/analysis/metrics/lib/plot/bokeh_validation_patch.py` before any `bokeh.models` import; the shim hoists the per-call imports of Bokeh's `PropertyDescriptor.is_unstable` (Bokeh is pinned at 3.10.2 in `pyproject.toml`).
Prewarm time is about 94% Bokeh, mostly job detail artifacts and `json_item`.

Bokeh model construction in threads serializes on the `sys.modules` dict and `_abc` cache set locks.
On hpcperfstats-dev (28 cores, 108 cloned jobs), threads peaked at 16 with 7.3 jobs/min and 28 threads gave 6.6.
That is why the prewarm runs in worker processes: 8 / 16 / 28 processes give 13.4 / 25.8 / 40.4 jobs/min (near-linear), and the parent carries no Bokeh garbage.

The remaining parent cost at width is the stop-the-world cyclic GC (a whole-heap scan per collection, about 8% of wall at 8 threads).
`[PIPELINE] metrics_gc_threshold` trades fewer pauses for more uncollected Bokeh cycles between them; the `metrics census` line reports collections as `gc=n:,pause_s:` so production logs show the trade before you raise it.
`gc.freeze()` does not shorten free-threaded collections (measured) and is not used.

### Metrics thread reset

`update_metrics` stall recovery detaches the current executor, cancels Futures that have not started, and creates a fresh titled pool lazily.
Python cannot kill a running thread, so database statement and lock timeouts must bound ORM work, while `metrics_run_stall_timeout_s` lets the scheduler soft-fail pending JIDs and continue.

### Metrics statement timeouts

Each columnar `host_data` COPY runs in its own transaction with `[PIPELINE] metrics_copy_statement_timeout_ms`, below the 900 s stall clock.
The read is a bounded index scan whose cost is the rows it reads, so on slow storage a 120 s cancel only made the host bisect re-read the same rows (hs04 2026-11-02, about 10 ms reads during a 50 T `fstrim`).
Parent no-progress detection stays `metrics_run_stall_timeout_s`.
Simple-metric ORM probes also skip types and events absent from `jid_table.schema`, as SummaryPlot does.

## Metrics backfill lane

Behind the fresh 7-day window, `update_metrics` recomputes every older job whose `job_data.metrics_version` is NULL or older than `METRICS_CONTRACT_VERSION` (`lib/metrics.py`), newest first, plus old jobs that ingest stamped dirty.
The lane's slot cap is work-conserving.
With the fresh window idle (the common case), the sweep runs on every thread but the reserve: 32 threads with reserve 2 give 30 slots.
As fresh jobs queue, the cap shrinks one-for-one down to the floor (32 -> 6 at the default `metrics_pool_processes // 5`).

The consumer always pops fresh-window jobs before backfill jobs, and the reserve threads are never given to the lane, so a fresh job arriving mid-sweep starts at once instead of waiting for a backfill job.
Each backfill job is one `COPY (FORMAT BINARY)` reader plus the prewarm, like a fresh job; where the extra `host_data` reads must stay below a ceiling, set `metrics_backfill_max_jobs_per_hour`.
The lane keeps a scheduler pass open while old jobs remain and relists the fresh days in place every `metrics_scheduler_pass_interval_s`, so the sweep runs continuously at its slot share.
A bump of `METRICS_CONTRACT_VERSION` makes every job stale, so the whole database is swept again, newest first.
The once-a-minute `metrics census` line carries `backfill=slots:k/n,...` and the hourly-refreshed `backfill_db(hourly)=stale:,...`.
Details are in [`docs/UPDATE_METRICS_STATE_MACHINE.md`](../UPDATE_METRICS_STATE_MACHINE.md) section 4.5.

## Metrics window-coverage readiness

By default `update_metrics` defers each job until in-window `host_data` samples exist within the margins of Slurm `start_time` and `end_time`, judged on the job aggregate: any accounting host may satisfy each edge.
Jobs with long monitor prolog gaps (telemetry that begins hours after the Slurm start) stay deferred until early-window data exists or an operator disables the gate.
When metrics run, `telemetry_first_time` / `telemetry_last_time` on `job_data` and the plot artifact fingerprints include those bounds, so backfill invalidates stale summary plots.

Strict readiness and the coverage proxy both use `job_data.host_list` hostnames (FQDN) and the Slurm window, not `host_data.jid`.
Any in-window sample on an accounting host may satisfy a margin, including samples from other jobs on shared nodes in overlapping windows.
That trade-off fixes tail-only false-ready bugs but is weaker than per-jid isolation.

Jobs whose telemetry never reaches both margins (multi-hour prolog gaps with 600s margins) stay in the deferred-not-ready map, retried every `metrics_deferred_not_ready_retry_s`, then every `metrics_deferred_not_ready_quarantine_s`; they are not dropped from candidacy while still in range.
Large historical backfills of such jobs raise readiness-thread DB load and can keep compute workers idle; for an emergency backfill, disable the gate or reduce the margins, and watch the scheduler stall metrics.

When the coverage gate fails (`start_ok=False` or `end_ok=False`), `update_metrics` removes persisted metrics rows and plot and detail artifacts for that job.
It then writes the full metrics catalog with `no_data_reason = "Insufficient Data For Metrics Processing"`, and keeps the job in the daily candidate list (through `gate_failure_recheck` in `_jobs_queryset`) on every scheduler run until the margins pass and `Metrics.run` succeeds.
Jobs that ingest stamps `metrics_dirty_at` (late `host_data`) are listed again as well.
Readiness batches dedupe host aggregates across jobs sharing a window, and precomputed bounds pass into `Metrics.run`, so persist runs no second in-window aggregate.

## PostgreSQL connections

The stack uses no external pooler (no PgBouncer): sizing is direct Django -> Postgres, budgeted by the formula in [PostgreSQL connection budget](../reference/pipeline-configuration.md#postgresql-connection-budget).
Compose sets `max_connections=500` with reduced `work_mem`, parallel gather and maintenance buffers, which limits RAM spikes while keeping slot headroom.
`WEB_CONCURRENCY`, `metrics_pool_processes`, `sync_ingest_pool_processes` and `parallel_db_prefetch_max` keep concurrent heavy queries bounded.

A census of hundreds of idle backends from `pipeline` is a leak: detached `update_metrics` threads after `reset_pool_hard` whose Django sockets were not `connections.close_all()`'d.
Do not raise the GUC to paper over it; a worker `finally` must close thread-local backends, because `CONN_MAX_AGE` never fires on abandoned threads.

`CONN_MAX_AGE` (90 seconds by default) lowers how long idle Gunicorn workers hold a backend; it does not cap peak concurrency under full load.
The session `statement_timeout` (120000 ms) and `idle_in_transaction_session_timeout` (300000 ms) bound runaway sessions; tune them upward only if legitimate bulk jobs hit them, and adjust compose too if you remove the server defaults.

## Startup stagger and graceful stop

`services-conf/supervisord.conf` starts `listend` first (higher priority), then `sync_timedb` after 20s, `update_metrics` after 90s and `ingest_coverage_sweeper` after 120s, so a restart does not open every DB pool at the same instant.
The start `sleep` lines are boot stagger only; on stop, supervisord signals programs without them.

The drain programs (`hpcperfstats-rabbitmq-listener`, `sync_timedb`, `update_metrics`) set `stopsignal=TERM`, `stopasgroup=true` and `killasgroup=true`, and get `stopwaitsecs=130` because the `sync_timedb` cooperative drain is capped by `SHUTDOWN_DRAIN_TIMEOUT_S` (120s).
`ingest_coverage_sweeper` also stops on `TERM` as a group, with `stopwaitsecs=70`.
`rabbitmq-watcher` and `rsync_data` hold no state, so they set `stopsignal=KILL` with `stopwaitsecs=1`.
The Compose stop grace periods sit above those waits ([Stop grace periods](../reference/compose-services.md#stop-grace-periods)), so a container stop never SIGKILLs a drain early.

## Discovery and stalls

The orchestrator streams `fdfind`/`fd -X` GNU `stat --printf` records (`sync_timedb_stats_find`: path, mtime, size, inode; no GNU `find` fallback exists) into the ingest queue, so discovery does not re-`os.stat` fields stat already emitted.
The idle pass walks files changed within `sync_ingest_rescan_mtime_days`; whole-archive walks run at boot and every `sync_ingest_full_rescan_interval_hours`.
Files skipped past `sync_ingest_queue_max_size` are found again by the next whole-archive walk.

`sync_timedb` has no internal wall-clock soft-kill.
Soft-requeue uses `sync_ingest_stall_idle_s` when parse and write heartbeats stop, and Postgres `statement_timeout` stays the absolute DB ceiling.
For large sites with slow duplicate detection or bulk writes, the idle stall is the knob.

A DB-complete large raw file could otherwise sit in top-level `parse` (no `duplicate_scan_streaming` substage) until abort, when `head_timestamp_present_in_db` is true but a full `readlines()` and duplicate scan run before any checkpoint.
The guards:

- A head+tail DB probe skips the duplicate scan when both seconds are present.
- `sync_ingest_stream_duplicate_scan_bytes` routes duplicate detection through the streaming path.
- `sync_ingest_db_complete_tail_window_lines` probes the tail timestamp window before a full-file duplicate scan on large head-present files.
- `load_stats_file_lines` / `iter_stats_file_lines` touch ingest progress and check the idle stall every 1000 lines or 1 MiB read.
- `_run_ingest_timed` wraps each worker task in idle-stall progress tracking with no wall timer.

### Members store and populate

`sync_timedb` keeps complete member maps in process memory and persists them under `{archive_dir}/.sync_timedb_archive_members/`.
Dedicated `[thread:populate-pool]` workers (`sync_timedb:worker:populate-pool`) stream sealed and tar archives into that store; ingest-pool and archive-pool workers enqueue populate work and wait on the store.
Member maps are invalidated after tar append, dedupe, seal and archive finalize.

When `archive_keep_uncompressed_tar=no`, DB-complete files (`outcome=db_skip`) call `raw_stats_path_needs_tar_append`, which must not stream the full `.tar.zst` once per file across the ingest pool.
The store prevents that:

- Populate wait suspends the idle-stall clock (`respect_ingest_deadline=False`); populate stall and max limits bound the wait.
- When the store is fully warm (`complete=1`, non-empty map), duplicate-check uses a single store point lookup per member, not a full-map dump per worker L1 miss.
- With both a sibling `.tar` and a sealed `.tar.zst` and a warm store, duplicate-check never runs N parallel `get_existing_archive_members(tar)` under `file_read_lock_wait`.
- Per-process L1 plus the store coordinate single-flight populate per calendar day; the winner renews lock and progress on each store batch and on a derived heartbeat, so slow sealed scans do not false-stall.
- Stalls and timeouts raise `ArchiveMembersPopulateStalledError`.

When populate cannot read a sealed archive, the winner classifies the failure once (`zstd -t` vs tar stream) and sets store `archive_day_ingest_skip`, and all workers skip tar-append duplicate-check for that day, with no parallel per-worker point lookups.
Ingest never runs local sealed scans (`sync-timedb-ingest-pool-io-coordination.mdc`).
`populate_degraded` without a day skip raises `ArchiveMembersStoreUnavailableError`, with no local zstd fallthrough.
The ingest lookup path has no zstd decompress timeout, and `sync_timedb` hard-fails mid-ingest duplicate-check on a non-transient populate contract failure (`sys.exit(1)`, so supervisord restarts it).
Ingest member scans, seal and decompress all run plain zstd (no `nice`/`ionice`).
For sealed-only archives during catch-up, use `archive_keep_uncompressed_tar=yes`, tune `sync_ingest_pool_processes`, and rely on single-flight populate warming the day map rather than N parallel `zstd` per worker.

### Sealed-archive backfill CLI

`sync_timedb_archive.py` dispatches one task per sealed daily archive (`.tar.zst` or legacy `.tar.gz`) onto a `sealed-archive-pool` of `sync_archive_pool_processes` threads.
At most `sync_timedb_archive_max_concurrent_sealed_days` are in flight through `imap_sliding_window_watch_pool` (context `sync_timedb_archive pool`).
Before dispatch it prewarms the members store once per sealed day (`archive chunk prewarm begin sealed_paths=... days=...`, `Prewarming archive members store for day=... sealed=...`, `archive chunk prewarm complete elapsed_s=... days=...`).

The watcher polls every `sync_pool_poll_timeout_s`; an empty poll may log `WARN: pool imap stall deferred: members-store populate active for day=...`, `... worker progress active ...` or `... long ingest budget ...`, rate-limited by `sync_pool_stall_defer_log_interval_s`.
The tool has no poll-count abort (threshold 0; `dynamic_stall_abort_after` / `dynamic_stall_wall_s` stay 0), so a sealed day that never finishes keeps the process running: inspect DB locks, members-store populate or a stuck member parse.
A `MultiprocessingWorkerExitError` that reaches `main` logs `sync_timedb_archive exiting after pool worker death: ...` and exits through `_handle_pool_worker_exit_fatal` -> `hard_exit_pool_worker_error` (`Pool worker exit: hard exit code=<n> context=... likely_cause=...`, then `os._exit(<n>)`).
Its thread pool has no worker processes, so the dead-worker (exit 137), idle-ghost and reconcile checks in `multiprocessing_pool_health.py` never trigger.

## Archive safety and recovery

Raw stats on disk stay the source of truth until validated archive membership, the DB ingest gate and `day_close` raw removal all succeed.
With `sync_archive_require_db_ingest=yes`, the gate requires the first and last digit-leading timestamp seconds in `host_data`, read by a streaming head and an EOF-backward tail.

| Symptom                                    | Safe recovery                                                                                                                                                               |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bad `.tar.zst`, good legacy `.tar.gz`      | Remove or rename the corrupt `.tar.zst`; restore the sibling `.tar` from gzip (`decompress_compressed_to_tar` / `zstd -d --format=gzip`); raw stays until validation passes |
| Missing `.tar` after a mistaken drop       | Decompress the sealed `.tar.zst` (or legacy `.tar.gz`) to a sibling `.tar` before append; never delete raw while tar or zst validation fails closed                         |
| Corrupt daily `.tar` before append         | `replace_corrupt_tar_from_compressed_backup` tries zst then gz; append stays fail-closed (`False`) if restore fails, and raw files remain                                   |
| Persistence contract version mismatch      | Automatic: see below                                                                                                                                                        |
| Head and tail ingested, gaps in the middle | The gate checks only the first and last seconds; treat unexpected raw deletion as an incident review                                                                        |
| Unparsable closed raw blocking a day       | Inspect the quarantine manifest and directory; see below                                                                                                                    |

When a sealed `.tar.zst` is unreadable for ingest lookup, check `zstd -t /path/YYYY-MM-DD.tar.zst`:

- If it passes but the tar stream fails (`unexpected end of data`), the tar payload is truncated.
  Prefer the sibling mutable `.tar` when `tar tf` passes; populate falls back to it automatically and clears a stale `archive_day_ingest_skip` when the sealed file is gone.
  `sync_timedb` and the operator scripts may repair a truncated `.tar` in place (`repair_truncated_daily_tar_in_place`) when members remain extractable; otherwise restore or re-seal from the legacy `.tar.gz`.
  `scripts/verify_open_tar_matches_zst.py` validates the pair.
- If `zstd -t` fails, the zstd frame is corrupt.

When both the sealed stream and the tar scan fail, ingest logs `ERROR: daily sealed archive unusable for ingest lookup` once per day (`reason=zst_frame_invalid` or `tar_truncated_or_unreadable`), sets store `archive_day_ingest_skip`, and skips tar-append duplicate-check until repair.
DB-complete ingest continues (`need_archival=False` for skipped days).
`atomic_seal_tar_to_zst` refuses to seal a tar that fails `verify_tar_archive_readable`, and active-ingest decompress for append keeps the sealed sibling (`remove_compressed=False`).

On startup, `ensure_persistence_contract` compares `{archive_data_dir}/.sync_timedb_persistence.json` with the built-in version.
A mismatch logs `persistence reset old=... new=...` and deletes all registered `.sync_*` sidecars (checkpoint, hints, manifests), so ingest and archive reprocess from scratch; no manual sidecar delete is needed.

The DB gate checks first and last digit-leading timestamp seconds only (no full-file scan), so missing middle segments may still pass, while head-only or tail-only partial ingest fails.

Ingest moves permanently unparseable closed raw off the hot tree when parse fails (quarantine reason `ingest_parse_failed`), recording it in `{archive_data_dir}/.sync_timedb_unparsable_raw.json` and `{archive_data_dir}/.sync_timedb_unparsable_raw/`.
Restore a file only after fixing its content or a deliberate operator review, and do not delete manifest entries casually.
Parsable-but-unmapped raw still blocks until ingest and archive catch up.

## Alternatives and trade-offs

- A connection pooler (PgBouncer) would raise the effective slot count, but it hides leaks such as abandoned thread backends; the stack sizes pools directly against `max_connections` and logs the budget instead.
- Count-only admission is simpler, but one burst of large jobs can OOM the cgroup; byte, RSS and cgroup gates cost some throughput at the roof in exchange for no OOM.
- A per-file `gc.collect()` bounds RSS but stops the world on the free-threaded interpreter; the RSS roof gate bounds memory without paying it per file.
- Higher zstd levels shrink archives but run slower than the disk; level 9 keeps seals disk-bound.
- Threads for the Bokeh prewarm would share memory, but lock contention caps them near 16; worker processes scale linearly.

## Related

- [Pipeline configuration](../reference/pipeline-configuration.md)
- [Compose services](../reference/compose-services.md)
- [Architecture](architecture.md)
- [`docs/SYNC_TIMEDB_STATE_MACHINE.md`](../SYNC_TIMEDB_STATE_MACHINE.md)
- [`docs/UPDATE_METRICS_STATE_MACHINE.md`](../UPDATE_METRICS_STATE_MACHINE.md)
