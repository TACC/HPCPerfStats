# sync_timedb parallelism model

This document describes how `sync_timedb` uses **spawn process pools**, **session thread executors**, **ephemeral burst thread pools**, and **subprocess CLI** (zstd/tar). It complements the archive janitor contract (`sync-timedb-archive-janitor-contract.mdc`) and operator stall verification (`OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`).

## Four categories

| Category | Mechanism | Lifetime | Typical work |
|----------|-----------|----------|--------------|
| **A. Hot-path spawn pools** | `multiprocessing.get_context("spawn").Pool` | Session-static (supervisor lifetime) | Ingest parse/DB, archive tar append |
| **B. Session thread executors** | `ThreadPoolExecutor(max_workers=1)` in supervisor PID | Eager when role enabled; shutdown in supervisor `finally` | Janitor tick, startup preflights, day raw removal verify |
| **C. Ephemeral burst threads** | `ThreadPoolExecutor` in `with` block | Per maintenance call | Archive metadata reads, parallel seal/validation/migrate |
| **D. Subprocess CLI** | `subprocess` | Per command | `zstd`, GNU `tar -r`, decompress helpers |

**Not a Python pool:** zstd compression uses the **zstd CLI** via subprocess, not `multiprocessing.Pool`.

## A. Hot-path spawn pools (keep on spawn)

| Pool | Module | Workers | Initializer | Why spawn |
|------|--------|---------|-------------|-----------|
| `ingest_pool` | `sync_timedb.py` | INI `sync_ingest_pool_processes` | `apply_ingest_pool_worker_init` + diagnostics registry | Combined parse+DB write; CPU parse, RSS, Django test DB isolation, BLAS, stall exit **124**, L1 host cache |
| `archive_pool` | `sync_timedb.py` | INI `sync_archive_pool_processes` | `apply_pool_worker_process_title` only | Hardcoded **`maxtasksperchild=1`** per append job; failure isolation, L1 cache; append is I/O-heavy but isolation wins |
| `sealed-archive-pool` | `sync_timedb_archive.py` | INI archive worker count | Same as ingest init | Hardcoded **`maxtasksperchild=1`**; standalone sealed-archive ingest CLI |

**Factory:** `create_sync_timedb_spawn_pool()` in `multiprocessing_pool_health.py` — shared spawn context + recycle kwargs; **distinct initializers per pool kind** (do not unify initargs).

**Archive dispatch:** `ArchiveDispatchCoordinator` uses `archive_pool.map_async(...)` with **one daily tar per slot**; concurrent slots follow **`sync_archive_pool_processes`**. Non-blocking slot finalize drains overflow heap before long `post_finalize_reconcile`. Do **not** replace with `ThreadPoolExecutor.submit` on the hot path without redesigning stall/finalize/fatal-exit semantics (`async_result_get_watch_pool`, `dead_pool_worker_pids`, exit **124**/**137**).

**Ingest dispatch:** `imap_unordered_watch_pool` polls process liveness and aborts on worker death — thread pools have no equivalent worker-PID model.

## B. Session thread executors (supervisor background roles)

Single-flight (`max_workers=1`) roles share `SessionSingleFlightExecutor` (`sync_timedb_session_executor.py`):

| Role | Module | `thread_name_prefix` | Created |
|------|--------|---------------------|---------|
| Archive janitor tick | `sync_timedb_archive_janitor.py` | `archive-janitor` | Eager at `ArchiveJanitor.__init__` |
| Day raw removal verify | `sync_timedb_day_raw_removal.py` | `day-raw-removal` | Eager when preflight enabled |

**Two-queue law:** MainThread + ingest/archive pools own hot path; janitor thread owns seal/validate/delete/tar-drop. Janitor stays on **threads** (not spawn) — one queue, single-flight ticks.

**Shutdown:** supervisor `finally` calls `shutdown(wait=not pool_worker_exit)` on each coordinator. Disabled roles create no executor.

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
| `sync_timedb_archive_members_redis.py` | Daemon heartbeat during Redis populate lock renewal |

Not generalized to `ThreadPoolExecutor` — lifecycle tied to populate call.

## Redis L2 populate fnctl contention

On **active-ingest** calendar days, populate-pool **shared read locks** (`file_read_lock_wait` on `*.fnctl.lock`) contend with archive-pool **exclusive write locks** during `.tar` append. Tar populate waits up to **`sync_archive_members_redis_populate_max_seconds`** (default **7200**) in one blocking attempt; transient timeouts must recover (waiter re-enqueue, prewarm retry) rather than supervisor **`sys.exit(1)`** on the first failure.

| INI key | Default | Role |
|---------|---------|------|
| `sync_archive_members_redis_populate_max_seconds` | 7200 | Tar populate fnctl read-lock wait when > 0; absolute waiter/prewarm recovery budget (`0` = off) |
| `sync_archive_members_fnctl_read_lock_timeout_seconds` | 180 | Read-lock wait for verify/non-populate paths; tar populate fallback when `populate_max_seconds=0` |
| `sync_archive_members_redis_populate_stall_seconds` | 120 | No-progress detection while populate lock held |

**Populate source:** when sibling **`YYYY-MM-DD.tar` exists**, cold Redis populate uses **`populate_source=tar`**; **`populate_source=sealed`** only when tar is absent (post tar-drop).

Prewarm summary token **`populate_recovering`** indicates transient fnctl recovery succeeded after retry.

## Operator stall signals (spawn-specific)

Exit **124** / `Pool imap stalled` / `MultiprocessingWorkerExitError` come from **spawn pool health** (`multiprocessing_pool_health.py`), not janitor threads. See `OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`.

## Normalization policy (2026-07)

- **In scope:** session executor helper, spawn pool factory, burst thread helper, replace duplicated Pool/executor boilerplate.
- **Out of scope:** hot-path Pool→threads (ingest; archive except explicit trial), renaming `archive_pool`, changing `map_async`/stall semantics.

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

## Redis populate call graph

| Entry | Thread | Scan execution |
|-------|--------|----------------|
| `request_archive_members_populate_and_wait` | Supervisor prewarm, ingest workers | Enqueue populate pool or inline `execute_archive_members_populate_for_canonical` |
| `get_existing_archive_members_for_daily_archive` | Ingest lookup | L1 cache → populate wait → local scan fallback |
| `sync_timedb_archive.py` backfill | Sealed-only CLI | `iter_sealed_daily_archive_member_paths` (no `.tar` restore) |

**Archive CLI vs supervisor prewarm boundary:** `sync_timedb_archive.py` is an operator/CLI backfill tool — it scans **sealed** archives only and never calls `ensure_daily_tar_restored_for_append` or supervisor chunk prewarm. The supervisor and ingest workers own hot-path Redis populate via `request_archive_members_populate_and_wait`; janitor owns day-close seal/verify/delete. Do not route CLI scans through prewarm or maintenance snapshots.

**Defer split:** janitor day-close defer checks `ingest_tar_hot` (ingest pool activity). Populate-pool tar scans **also** defer on `archive_append_inflight` (append worker holds day until merge). Both keys are intentional — see `sync-timedb-ingest-pool-io-coordination.mdc` §8b.

## zstd thread parameter naming

`zstd_cli.decompress_compressed_to_tar` and related CLI helpers use **`thread_count`** as the canonical name. Archive helpers may still name the third positional argument `zstd_threads` at internal call sites; new public surfaces should prefer `thread_count`.

## Future: sync_timedb.py module split (P4)

Extracting prewarm, checkpoint, and stall diagnostics from `sync_timedb.py` into `lib/` modules is **out of scope** for this audit — track as a separate plan.

## Supervisor maintenance stubs

`sync_timedb.py` exposes `seal_dirty_daily_archives` / `remove_verified_*` as **RuntimeError stubs** so tests can monkeypatch names; real implementations live on `ArchiveJanitor` / helpers (`test_arch_supervisor_maintenance_stubs_raise_runtime_error`).
