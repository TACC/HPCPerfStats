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
| `archive_pool` | `sync_timedb.py` | INI `sync_archive_pool_processes` | `apply_pool_worker_process_title` only | Per-job process recycle (`maxtasksperchild`), failure isolation, L1 cache; append is I/O-heavy but isolation wins |
| `sealed-archive-pool` | `sync_timedb_archive.py` | INI archive worker count | Same as ingest init | Standalone sealed-archive ingest CLI |

**Factory:** `create_sync_timedb_spawn_pool()` in `multiprocessing_pool_health.py` — shared spawn context + recycle kwargs; **distinct initializers per pool kind** (do not unify initargs).

**Archive dispatch:** `ArchiveDispatchCoordinator` uses `archive_pool.map_async(...)` with non-blocking slot finalize. Do **not** replace with `ThreadPoolExecutor.submit` on the hot path without redesigning stall/finalize/fatal-exit semantics (`async_result_get_watch_pool`, `dead_pool_worker_pids`, exit **124**/**137**).

**Ingest dispatch:** `imap_unordered_watch_pool` polls process liveness and aborts on worker death — thread pools have no equivalent worker-PID model.

## B. Session thread executors (supervisor background roles)

Single-flight (`max_workers=1`) roles share `SessionSingleFlightExecutor` (`sync_timedb_session_executor.py`):

| Role | Module | `thread_name_prefix` | Created |
|------|--------|---------------------|---------|
| Archive janitor tick | `sync_timedb_archive_janitor.py` | `archive-janitor` | Eager at `ArchiveJanitor.__init__` |
| Startup day-close discover | `sync_timedb_startup_day_close.py` | `startup-day-close` | Eager when preflight enabled |
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

## Operator stall signals (spawn-specific)

Exit **124** / `Pool imap stalled` / `MultiprocessingWorkerExitError` come from **spawn pool health** (`multiprocessing_pool_health.py`), not janitor threads. See `OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`.

## Normalization policy (2026-07)

- **In scope:** session executor helper, spawn pool factory, burst thread helper, replace duplicated Pool/executor boilerplate.
- **Out of scope:** hot-path Pool→threads (ingest; archive except explicit trial), renaming `archive_pool`, changing `map_async`/stall semantics.
