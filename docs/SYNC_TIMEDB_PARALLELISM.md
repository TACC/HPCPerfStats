# sync_timedb parallelism model

This document describes how `sync_timedb` uses **in-process thread pools**, **day_close thread executors**, **ephemeral burst thread pools**, and **subprocess CLI** (zstd/tar). Production coordinator is **`run_sync_timedb_queue_orchestrator`** (`sync-timedb-queue-orchestrator-contract.mdc`). Complements operator stall verification (`OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`).

## Four categories

| Category | Mechanism | Lifetime | Typical work |
|----------|-----------|----------|--------------|
| **A. Hot-path thread pools** | `create_sync_timedb_thread_pool` (`SyncTimedbThreadPool`) | Session-static (orchestrator lifetime) | Ingest parse/DB, archive tar append, populate scans |
| **B. Session thread executors** | `ThreadPoolExecutor` for day_close (max `sync_day_close_max_inflight`) | Orchestrator lifetime | Seal → raw removal → tar-drop |
| **C. Ephemeral burst threads** | `ThreadPoolExecutor` in `with` block | Per maintenance call | Archive metadata reads, parallel seal/validation/migrate |
| **D. Subprocess CLI** | `subprocess` | Per command | `zstd`, GNU `tar -r`, decompress helpers |

**Not a Python pool:** zstd compression uses the **zstd CLI** via subprocess, not a worker pool. Do **not** restore `create_sync_timedb_spawn_pool` on the `sync_timedb` hot path.

## A. Hot-path thread pools

| Pool | Module | Workers | Title role | Why threads |
|------|--------|---------|------------|-------------|
| `ingest_pool` | `sync_timedb_queue_orchestrator.py` | INI `sync_ingest_pool_processes` | `[thread:ingest-pool]` | Combined parse+DB write on 3.14t; shared job/members stores |
| `archive_pool` | `sync_timedb.py` / orchestrator append | INI `sync_archive_pool_processes` | `[thread:archive-pool]` | One daily tar per slot; I/O-heavy append |
| `populate_pool` | `sync_timedb_populate_pool.py` | INI `sync_archive_members_populate_pool_processes` | `[thread:populate-pool]` | Sealed/tar member scans; ingest only enqueues and waits |
| `sealed-archive-pool` | `sync_timedb_archive.py` | INI archive worker count | `[thread:sealed-archive-pool]` | Standalone sealed-archive ingest CLI |

**Factory:** `create_sync_timedb_thread_pool()` in `sync_timedb_session_executor.py`. `create_sync_timedb_spawn_pool()` remains in `multiprocessing_pool_health.py` for listend/metrics only and raises unless those callers pass a pool kind.

**Archive dispatch:** append jobs use `archive_pool` with **one daily tar per slot**; concurrent slots follow **`sync_archive_pool_processes`**. Sliding-window refill on completion — never join an entire batch before the next hop.

**Ingest dispatch:** orchestrator fill/drain submits `apply_async` on the ingest thread pool. Populate workers are long-lived `apply_async` loops that claim the in-process populate queue.

## Ingest band reservation (job store)

The ingest ZSET is one in-process key (`hps:job:queue:ingest`) with two score bands. Fill paths never `ZPOPMIN` the whole set (catchup would starve).

| Token | Formula | Score window |
|-------|---------|--------------|
| **`hot_cap`** | `max(1, (2 * pool) // 3)` | `-inf` … `CATCHUP_SCORE_BASE - 1` (`10**15 - 1`) — newest days first |
| **`catchup_cap`** | `pool - hot_cap`, floored to **1** when `pool >= 2` | `10**15` … `+inf` — oldest days first |

Catchup may use unused pool slots **only when the hot range is empty**. Reband at claim time (`ZADD` the same path identity with a new score). Calendar day comes from the daily tar basename (`YYYY-MM-DD.tar`); unresolved day skips ingest enqueue (never substitute today). Operator census: job-store sidecar `{archive_dir}/.sync_timedb_job_store.json` plus live `zcard`/`zcount` on the in-process store (not Redis `job:v1`).

## B. Session thread executors (day_close + helpers)

| Role | Module | Created |
|------|--------|---------|
| day_close workers | `sync_timedb_queue_orchestrator.py` | `ThreadPoolExecutor(max_workers=sync_day_close_max_inflight)` |
| Day raw removal verify | `sync_timedb_day_raw_removal.py` | Eager when preflight enabled |

**Two-queue law:** MainThread + ingest/append/populate thread pools own hot path; day_close threads own seal/validate/delete/tar-drop.

**Shutdown:** orchestrator `finally` force-persists the job store, then shuts down day_close and hot-path thread pools. The B **`ArchiveJanitor`** single-flight tick executor is **retired**.

## C. Ephemeral burst thread pools

Short-lived parallel I/O in archive maintenance (via `run_bounded_thread_pool`):

| Site | Module | Worker count helper |
|------|--------|---------------------|
| Sampled timestamp metadata | `sync_timedb_archive_maint.py` | `_get_archive_discovery_worker_count` |
| Head metadata | `sync_timedb_archive_maint.py` | `_get_archive_discovery_worker_count` |
| Batch validation | `sync_timedb_archive_helpers.py` | `_get_archive_validation_worker_count` |
| Parallel seal | `sync_timedb_archive_helpers.py` | `_get_archive_seal_worker_count` |
| Legacy gzip migrate | `sync_timedb_archive_helpers.py` | `_get_archive_seal_worker_count` |

Per-task `set_daemon_thread_title(role=...)` for operator `ps`/logs. Validation uses `validation_cache=None` per thread (no shared mutable cache).

## D. Subprocess (external CLI)

| Tool | Module | Purpose |
|------|--------|---------|
| zstd | `zstd_cli.py` | compress/decompress/pipe |
| GNU tar | `sync_timedb.py` `_append_to_tar` | `tar -r` append |
| Restore paths | `sync_timedb_archive_helpers.py` | decompress archives |

## Raw `threading.Thread` (exception)

| Site | Purpose |
|------|---------|
| `sync_timedb_archive_members_coord.py` | Shared populate-wait rate-limit state (not a heartbeat thread) |

Not generalized to `ThreadPoolExecutor` — lifecycle tied to populate call.

## Members-store populate fnctl contention

On **active-ingest** calendar days, populate-pool **shared read locks** (`file_read_lock_wait` on `*.fnctl.lock`) contend with archive-pool **exclusive write locks** during `.tar` append. Tar populate waits up to **`sync_archive_members_populate_max_seconds`** (default **7200**) in one blocking attempt; transient timeouts must recover (waiter re-enqueue, prewarm retry) rather than supervisor **`sys.exit(1)`** on the first failure.

| INI key | Default | Role |
|---------|---------|------|
| `sync_archive_members_populate_max_seconds` | 7200 | Tar populate fnctl read-lock wait when > 0; absolute waiter/prewarm recovery budget (`0` = off) |
| `sync_archive_members_fnctl_read_lock_timeout_seconds` | 180 | Read-lock wait for verify/non-populate paths; tar populate fallback when `populate_max_seconds=0` |
| `sync_archive_members_populate_max_seconds` | 7200 | No-progress / waiter bound while populate lock held |

**Populate source:** when sibling **`YYYY-MM-DD.tar` exists**, cold members-store populate uses **`populate_source=tar`**; **`populate_source=sealed`** only when tar is absent (post tar-drop).

Prewarm summary token **`populate_recovering`** indicates transient fnctl recovery succeeded after retry.

## Operator stall signals

Historical exit **124** / `Pool imap stalled` / `MultiprocessingWorkerExitError` text is spawn-pool archaeology. Live `sync_timedb` census is job-store / members-store sidecars plus `[thread:ingest-pool]` / `[thread:populate-pool]` titles. See `OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`.

## Normalization policy

- **In scope:** titled `SyncTimedbThreadPool` for ingest/append/populate; burst thread helper; no Redis job/members bus.
- **Out of scope:** restoring spawn pools or `/dev/shm` for `sync_timedb`.

## Census vs blocking remaining-raw (decision gates)

| Layer | API | Use |
|-------|-----|-----|
| **Census (inventory)** | `build_remaining_raw_stats_by_daily_gz`, `build_remaining_raw_for_daily_tar` | Maintenance snapshots, logging, verify worklists |
| **Blocking (decision)** | `blocking_closed_raw_remains_for_day` (alias `remaining_raw_blocking_day_incomplete`) | Tar drop, FS-complete, `daily_tar_needs_day_close_work`, seal defer when `only_when_no_remaining_raw` |

**Precedence for day-close complete:** `day_close_filesystem_complete` (disk + blocking) is ground truth; `archive_maint_hints.day_phases` and `.sync_timedb_async_day_close.json` are hints/occupancy only.

**Quiescent vs FS-complete:** `daily_tar_filesystem_quiescent` uses census for startup scheduling; `day_close_filesystem_complete` uses blocking for completion gates.

## Day-close state stores (authority)

| Store | Path | Authoritative for |
|-------|------|-------------------|
| Janitor hints | `.sync_archive_maint_hints.json` `day_phases` | Skip scheduling when phase ≥ target (re-checked against disk) |
| Day-close manifest | `.sync_timedb_async_day_close.json` | Worker occupancy / enqueue guard |
| Per-day raw removal | `.sync_timedb_day_raw_removal/*.json` | Delete pipeline phases |

## Members-store populate call graph

| Entry | Thread | Scan execution |
|-------|--------|----------------|
| `request_archive_members_populate_and_wait` | Supervisor prewarm, ingest workers | Enqueue populate pool or inline `execute_archive_members_populate_for_canonical` |
| `get_existing_archive_members_for_daily_archive` | Ingest lookup | L1 cache → populate wait → local scan fallback |
| `sync_timedb_archive.py` backfill | Sealed-only CLI | `iter_sealed_daily_archive_member_paths` (no `.tar` restore) |

**Day-close ownership:** one orchestrator process owns ingest **and** day_close (job-store `day_close` LIST + thread pool). Dual CLI ``backlog``/``current`` is **retired**.

**Archive CLI vs orchestrator prewarm boundary:** `sync_timedb_archive.py` is an operator/CLI sealed-day tool (`all` / dates / paths) — it never calls `ensure_daily_tar_restored_for_append` or orchestrator chunk prewarm. The orchestrator and ingest workers own hot-path members-store populate via `request_archive_members_populate_and_wait`; day_close threads own seal/verify/delete. Do not route CLI scans through prewarm or maintenance snapshots.

**Defer split:** day_close defer checks `ingest_tar_hot` (ingest pool activity). Populate-pool tar scans **also** defer on `archive_append_inflight` (append worker holds day until merge). Both keys are intentional — see `sync-timedb-ingest-pool-io-coordination.mdc` §8b.

## zstd thread parameter naming

`zstd_cli.decompress_compressed_to_tar` and related CLI helpers use **`thread_count`** as the canonical name. Archive helpers may still name the third positional argument `zstd_threads` at internal call sites; new public surfaces should prefer `thread_count`.

## Future: sync_timedb.py module split (P4)

Extracting prewarm, checkpoint, and stall diagnostics from `sync_timedb.py` into `lib/` modules is **out of scope** for this audit — track as a separate plan.

## Supervisor maintenance stubs

`sync_timedb.py` may still expose **RuntimeError stubs** for retired supervisor maintenance names so older tests fail closed; seal/raw helpers live under **`sync_timedb_archive_helpers`** / day_close workers.
