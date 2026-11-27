# sync_timedb threads and processes

This page lists every thread pool, executor, worker process and subprocess `sync_timedb` runs, what each owns, and the APIs that coordinate them.
It is for `sync_timedb` developers and for operators tuning pool widths or reading `ps` titles.

The production coordinator is `run_sync_timedb_queue_orchestrator` (`sync-timedb-queue-orchestrator-contract.mdc`).
Why the pools are sized as they are is in [Pipeline concurrency](../explanation/pipeline-concurrency.md); the INI keys and defaults are in [Pipeline configuration](pipeline-configuration.md).

## Categories

| Category                    | Mechanism                                                                                   | Lifetime                             | Typical work                                                                |
| --------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------- |
| A. Hot-path thread pools    | `create_sync_timedb_thread_pool` (`SyncTimedbThreadPool`)                                   | orchestrator lifetime                | ingest parse and DB write, archive tar append, populate scans               |
| B. Session thread executors | `ThreadPoolExecutor` for day_close (max `sync_day_close_max_inflight`)                      | orchestrator lifetime                | seal -> raw removal -> tar-drop                                             |
| C. Ephemeral burst threads  | `ThreadPoolExecutor` in a `with` block (`run_bounded_thread_pool`)                          | one maintenance call                 | archive metadata reads, parallel seal, validation, migrate                  |
| D. Subprocess CLI           | `subprocess`                                                                                | one command                          | `zstd`, GNU `tar` reads, `dd` copy fallback for appends, decompress helpers |
| E. Codon worker processes   | `subprocess.Popen` stdio servers (`python -m hpcperfstats.dbload.lib.codon_process_worker`) | reused across files, recycled on RSS | one file parse per worker                                                   |

zstd compression runs the zstd CLI through `subprocess`, not a worker pool.
`create_sync_timedb_spawn_pool` stays off the `sync_timedb` hot path; it lives in `multiprocessing_pool_health.py` for `listend` and metrics only and raises unless those callers pass a pool kind.
`/dev/shm` and spawn pools are out of scope for `sync_timedb`, and no Valkey job or members bus exists.

## A. Hot-path thread pools

| Pool                  | Module                                 | Workers (INI)                                                             | Title role                     |
| --------------------- | -------------------------------------- | ------------------------------------------------------------------------- | ------------------------------ |
| `ingest_pool` (parse) | `sync_timedb_queue_orchestrator.py`    | `sync_ingest_pool_processes`                                              | `[thread:ingest-pool]`         |
| `ingest_write_pool`   | `sync_timedb_ingest_write_stage.py`    | `sync_ingest_write_pool_processes` (`max(1, parse//2)`, 32 when parse=64) | `[thread:ingest-write-pool]`   |
| `archive_pool`        | `sync_timedb.py` / orchestrator append | `sync_archive_pool_processes`                                             | `[thread:archive-pool]`        |
| `populate_pool`       | `sync_timedb_populate_pool.py`         | `sync_archive_members_populate_pool_processes`                            | `[thread:populate-pool]`       |
| `sealed-archive-pool` | `sync_timedb_archive.py`               | `sync_archive_pool_processes`                                             | `[thread:sealed-archive-pool]` |

The factory `create_sync_timedb_thread_pool()` is in `sync_timedb_session_executor.py`.

- Parse threads stream and parse; on the free-threaded image each concurrent file parse runs in a [Codon worker process](#e-codon-worker-processes).
- Write threads are long-lived and run COPY and upsert, reusing a connection until `get_db_conn_max_age()`.
  The write pool, not the parse width, is the database connection budget.
- The archive pool runs one daily tar per slot (I/O-heavy append); concurrent slots follow `sync_archive_pool_processes`.
- Populate threads scan sealed and tar members; ingest only enqueues and waits.
- `sealed-archive-pool` belongs to the standalone sealed-archive ingest CLI.

### Ingest dispatch

1. Orchestrator fill and drain submit `apply_async` on the parse pool (`_ingest_parse_worker`), refilling a sliding window on completion; nothing joins a whole batch before the next hop.
2. Parse workers blocking-`put` flush chunks (about `sync_ingest_parse_flush_max_mib`, default 512 MiB, sample-boundary overage allowed) onto a bounded queue of `sync_ingest_parse_queue_max` slots.
3. Write threads `get` and COPY or upsert; the job-store ACK runs on drain after the parse worker returns success.
4. Fill refuses a new parse submit when `qsize() >= parse_queue_max`.

Fill also enforces a raw-byte in-flight budget: the effective process-tree roof (`sync_process_tree_rss_limit_cgroup_pct` x runtime `memory.max`) divided by `PEAK_CGROUP_PER_RAW_FILE_BYTE` (1.0, so the budget is about the roof in bytes).
The gate is off when the percent is 0 or `memory.max` is unknown, and an oversized file waits for empty inflight, then is admitted alone.
The census shows `ingest_mem_blocked=...` when fill skips for budget, and `parse_put_block_s` / `parse_put_block_max_s` / `parse_put_block_n` for the last 600 s of parse `queue.put` waits; raise the write pool only with PostgreSQL headroom when those stay high.

Populate workers are long-lived `apply_async` loops that claim the in-process populate queue.

### Ingest band reservation

The ingest score map is one in-process structure with two score bands, and fill paths never pop the whole set (catchup would starve).

| Token         | Formula                                         | Score window                                                         |
| ------------- | ----------------------------------------------- | -------------------------------------------------------------------- |
| `hot_cap`     | `max(1, (2 * pool) // 3)`                       | `-inf` to `CATCHUP_SCORE_BASE - 1` (`10**15 - 1`), newest days first |
| `catchup_cap` | `pool - hot_cap`, floored to 1 when `pool >= 2` | `10**15` to `+inf`, oldest days first                                |

- Catchup may use unused pool slots only when the hot range is empty.
- A claim rebands the job: `zadd_ingest` the same path identity with a new score.
- The calendar day comes from the daily tar basename (`YYYY-MM-DD.tar`); an unresolved day skips the ingest enqueue and never substitutes today.
- Operator census: the job-store sidecar `{archive_dir}/.sync_timedb_job_store.json` plus live `queued_count` / `ingest_count_in_score_range` on the in-process store, not Valkey `job:v1`.

## B. Session thread executors

| Role                   | Module                              | Created                                                       |
| ---------------------- | ----------------------------------- | ------------------------------------------------------------- |
| day_close workers      | `sync_timedb_queue_orchestrator.py` | `ThreadPoolExecutor(max_workers=sync_day_close_max_inflight)` |
| Day raw removal verify | `sync_timedb_day_raw_removal.py`    | eager when preflight is enabled                               |

Two-queue law: MainThread plus the ingest, append and populate thread pools own the hot path; day_close threads own seal, validate, delete and tar-drop.
One orchestrator process owns ingest and day_close (the job-store `day_close` LIST plus this executor).

On shutdown the orchestrator `finally` force-persists the job store, then shuts down the day_close and hot-path thread pools.

## C. Ephemeral burst thread pools

Short-lived parallel I/O in archive maintenance runs through `run_bounded_thread_pool`:

| Site                       | Module                           | Worker count helper                    |
| -------------------------- | -------------------------------- | -------------------------------------- |
| Sampled timestamp metadata | `sync_timedb_archive_maint.py`   | `_get_archive_discovery_worker_count`  |
| Head metadata              | `sync_timedb_archive_maint.py`   | `_get_archive_discovery_worker_count`  |
| Batch validation           | `sync_timedb_archive_helpers.py` | `_get_archive_validation_worker_count` |
| Parallel seal              | `sync_timedb_archive_helpers.py` | `_get_archive_seal_worker_count`       |
| Legacy gzip migrate        | `sync_timedb_archive_helpers.py` | `_get_archive_seal_worker_count`       |

Each task sets `set_daemon_thread_title(role=...)` for operator `ps` and logs.
Validation uses `validation_cache=None` per thread, so no mutable cache is shared.

## D. Subprocess and in-process writers

| Tool                    | Module                                                                           | Purpose                                                                                              |
| ----------------------- | -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| zstd                    | `zstd_cli.py`                                                                    | compress, decompress, pipe                                                                           |
| In-process clone writer | `sync_timedb.py` `_append_to_tar` -> `sync_timedb_tar_clone_append.clone_append` | pax headers plus `FICLONERANGE` reflink of raw data (no subprocess); journal-protected               |
| dd                      | `sync_timedb_tar_clone_append._copy_range`                                       | copy fallback when the filesystem cannot reflink, under the `run_subprocess_with_progress` idle kill |
| Restore paths           | `sync_timedb_archive_helpers.py`                                                 | decompress archives                                                                                  |

`zstd_cli.decompress_compressed_to_tar` and the related CLI helpers name the thread argument `thread_count`; some archive helpers still name the third positional argument `zstd_threads` at internal call sites, and new public surfaces use `thread_count`.

## E. Codon worker processes

On the free-threaded image with the baked `.so`, file parses run in Codon worker processes (`_use_codon_file_process_pool` in `sync_timedb_codon_bridge.py`), one per concurrent file, so one per ingest thread.
Threads sharing one Codon runtime serialize on the Boehm allocation lock, so processes are the parallel unit.
A worker above `sync_codon_worker_recycle_rss_mib` is replaced between files.
Host fakes (`CODON_EXT_ENV`) stay in-process, and the legacy Popen pool behind `_use_codon_process_pool` is a test-only opt-in.
The runtime model is in [Codon runtime model](../explanation/codon-runtime-model.md#process-model), and the bake is in [Build Codon kernels](../how-to/develop/build-codon-kernels.md).

## Raw `threading.Thread`

| Site                                   | Purpose                                                        |
| -------------------------------------- | -------------------------------------------------------------- |
| `sync_timedb_archive_members_coord.py` | shared populate-wait rate-limit state (not a heartbeat thread) |

Its lifecycle is tied to the populate call, so it is not a `ThreadPoolExecutor`.

## Members-store populate locks

On active-ingest calendar days, populate-pool shared read locks (`file_read_lock_wait` on `*.fnctl.lock`) contend with archive-pool exclusive write locks during `.tar` append.
Tar populate waits on the read lock in one blocking attempt.
Transient timeouts recover (waiter re-enqueue, prewarm retry) rather than exiting the orchestrator on the first failure.

| INI key                                                | Default | Role                                                                                                                                                                         |
| ------------------------------------------------------ | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sync_archive_members_populate_max_seconds`            | 0       | tar populate read-lock wait when above 0; absolute waiter and prewarm recovery budget and no-progress bound while the populate lock is held (`0` = idle plus heartbeat only) |
| `sync_archive_members_fnctl_read_lock_timeout_seconds` | 180     | read-lock wait for verify and non-populate paths; the tar populate wait when `populate_max_seconds=0`                                                                        |

- Populate source: when the sibling `YYYY-MM-DD.tar` exists, a cold members-store populate uses `populate_source=tar`; `populate_source=sealed` only when the tar is absent (after tar-drop).
- The prewarm summary token `populate_recovering` means a transient lock timeout recovered after a retry.

## Census and blocking remaining-raw APIs

| Layer               | API                                                                                   | Use                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Census (inventory)  | `build_remaining_raw_stats_by_daily_gz`, `build_remaining_raw_for_daily_tar`          | maintenance snapshots, logging, verify worklists                                                      |
| Blocking (decision) | `blocking_closed_raw_remains_for_day` (alias `remaining_raw_blocking_day_incomplete`) | tar drop, FS-complete, `daily_tar_needs_day_close_work`, seal defer when `only_when_no_remaining_raw` |

- Day-close complete: `day_close_filesystem_complete` (disk plus blocking) is ground truth.
- Quiescent vs FS-complete: `daily_tar_filesystem_quiescent` uses the census for startup scheduling; `day_close_filesystem_complete` uses blocking for completion gates.

## Day-close state stores

| Store                        | Path                                          | Role                                                                                                  |
| ---------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Per-day raw removal manifest | `.sync_timedb_day_raw_removal/*.json`         | authority for delete pipeline phases                                                                  |
| Janitor hints                | `.sync_archive_maint_hints.json` `day_phases` | unused: `SYNC_ARCHIVE_MAINT_HINTS` is `False`, so nothing writes them and `day_phases` is always None |
| Day-close manifest           | `.sync_timedb_async_day_close.json`           | unused legacy registry kind `day_close_manifest`; only a contract reset deletes it                    |

The registry entries are in [Section 8 Unused registry kinds](sync-timedb-state-machine.md#section-8-unused-registry-kinds) of the state machine reference.

## Members-store populate call graph

| Entry                                            | Thread                               | Scan execution                                                                           |
| ------------------------------------------------ | ------------------------------------ | ---------------------------------------------------------------------------------------- |
| `request_archive_members_populate_and_wait`      | orchestrator prewarm, ingest workers | enqueue on the populate pool, or inline `execute_archive_members_populate_for_canonical` |
| `get_existing_archive_members_for_daily_archive` | ingest lookup                        | L1 cache -> inline populate on a populate-pool thread, else populate wait; no local scan |
| `sync_timedb_archive.py` backfill                | sealed-only CLI                      | `iter_sealed_daily_archive_member_paths` (no `.tar` restore)                             |

- Archive CLI vs orchestrator prewarm: `sync_timedb_archive.py` is an operator sealed-day tool (`all`, dates or paths) and never calls `ensure_daily_tar_restored_for_append` or the orchestrator chunk prewarm.
  The orchestrator and ingest workers own the hot-path populate through `request_archive_members_populate_and_wait`; day_close threads own seal, verify and delete; CLI scans never route through prewarm or maintenance snapshots.
- Defer split: day_close defer checks `ingest_tar_hot` (ingest pool activity); populate-pool tar scans also defer on `archive_append_inflight` (the append worker holds the day until merge).
  Both keys are intentional (`sync-timedb-ingest-pool-io-coordination.mdc` items 19 and 29).

## Stall signals

The live census is the job-store and members-store sidecars plus the `[thread:ingest-pool]` / `[thread:populate-pool]` titles; see [sync_timedb stall signatures](sync-timedb-stall-signatures.md).
Exit 124, `Pool imap stalled` and `MultiprocessingWorkerExitError` come from spawn pools, which the `sync_timedb` hot path does not run.
`sync_timedb.py` exposes `RuntimeError` stubs for retired supervisor maintenance names, so callers of those names fail closed; seal and raw helpers live in `sync_timedb_archive_helpers` and the day_close workers.
