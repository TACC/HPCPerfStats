# Deployment: concurrency, PostgreSQL, and NUMA pinning

This document summarizes how **thread/process counts** and **Docker Compose CPU sets** relate to **PostgreSQL `max_connections`** and large hosts (including multi-NUMA systems).

**Ini sections:** **`[DEFAULT]`** — install identity, PostgreSQL connection, compose NUMA/cpuset (pinning keys last). **`[PORTAL]`** — Gunicorn/Django web tuning. **`[PIPELINE]`** — `sync_timedb`, archive/seal, `update_metrics`, and accounting paths. Integration sections **`[RMQ]`**, **`[SYSLOG]`**, **`[CACHE]`**, **`[XALT]`**, **`[OAUTH2]`** are unchanged. See **`hpcperfstats.ini.example`** and **`hpcperfstats/cursor-rules/hpcperfstats-ini-format.mdc`**.

## Services and parallelism (compose)

| Service | Role | Parallelism | DB impact |
|---------|------|----------------|-----------|
| **web** | Gunicorn + Django | Workers = `min(2 * min(os.cpu_count(), effective_cores) + 1, max_gunicorn_workers)` (default cap **32**); override with **`WEB_CONCURRENCY`** | Each worker may hold a persistent DB connection (`CONN_MAX_AGE`, default **90s** via `conf_parser.get_db_conn_max_age()`) |
| **web** | `api.py` | `ThreadPoolExecutor` size = **`api_small_executor_max_workers`**, or **`parallel_db_prefetch_max`** (default **4**) when the API key is unset | Extra concurrent ORM work per worker |
| **web** | `summaryplot.py` | Aggregate prefetch uses **`compute_summary_aggregate_prefetch_pool_size()`**: at most **2** inner threads, then **`parallel_db_prefetch_max`** (default **4**) | Prevents nested pools from multiplying against `api.py` under `job_plots` |
| **pipeline** | `update_metrics.py` | `multiprocessing.Pool` size = **`get_metrics_pool_process_count()`** (≤ **`metrics_pool_process_cap`**) | Many concurrent readers during metrics passes |
| **pipeline** | `sync_timedb.py` / archive | Ingest pool = **`get_sync_ingest_pool_processes()`**; archive append pool = **`get_sync_archive_pool_processes()`** with up to **`sync_archive_max_inflight_jobs`** (default **2**) disjoint daily-tar slots; **cold-path** seal/raw/`.tar` work runs in the background **`ArchiveJanitor`** (time-sliced; does not block ingest) | Seal load is spread across janitor ticks; see **Archive janitor** and **Archive zstd priority** below |
| **pipeline** | `listend.py` | Pika + a few daemon threads; no Django DB in this module | Low |
| **db** | PostgreSQL | `max_connections=500`; lowered `work_mem`, parallel worker caps, and maintenance buffers in `docker-compose.yaml` to limit RAM spikes | Hard slot ceiling; per-query memory bounded by GUCs |

**Sizing rule:** **`effective_cores = min(ini total_cores, os.cpu_count())`**. If **`[DEFAULT] total_cores`** is **missing** in `hpcperfstats.ini`, the code uses **40** as the ini budget. If the host has more CPUs than **`total_cores`**, the ini value **caps** app parallelism. If **ini > host**, **`os.cpu_count()`** (including cgroup/cpuset limits) wins.

**Ini reference:** optional tuning keys and defaults are documented in **`hpcperfstats.ini.example`** (one comment per key). The canonical list of wired options is **`INI_OPTION_REGISTRY`** in **`hpcperfstats/dbload/lib/dbload/lib/conf_parser.py`**; drift tests in **`hpcperfstats/tests/test_hpcperfstats_ini_example.py`** keep the example aligned.

## Archive zstd priority (unpinned hosts)

Daily monitor archives are sealed inside the **`pipeline`** container. On hosts **without** Docker CPU pinning, **`web`**, **`db`**, and **`pipeline`** share the same CPU scheduler and often the same physical disk (`postgres_data` vs `/opt/hpcperfstats_data/`).

| `[PIPELINE]` key | Default | Role |
|----------------|---------|------|
| **`archive_zstd_threads`** | **`0`** | Niced seal/restore/decompress: **`0`** → zstd **`-T0`** (all logical cores per zstd process); **`N>0`** → explicit **`-TN`** |
| **`ingest_zstd_threads`** | **`4`** | Un-niced ingest/populate sealed streams: **`4`** → **`-T4`**; **`0`** → **`-T0`** |
| **`archive_seal_parallel_workers`** | **`4`** | Max concurrent daily tar seals during maintenance |
| **`archive_zstd_nice`** | **`10`** | CPU deprioritization for archive (niced) zstd children (`0` disables) |
| **`archive_zstd_ionice_class`** / **`archive_zstd_ionice_level`** | **`2`** / **`6`** | Best-effort I/O class; higher level yields disk to Postgres |
| **`archive_zstd_level`** | **`7`** | Compression level; benchmark before raising above **9** |
| **`archive_zstd_drop_page_cache`** | **`yes`** | Linux-only **`posix_fadvise`** hints around archive zstd reads/writes to drop hot pages after one-shot seal/decompress/integrity paths; set **`no`** to disable |

**Tuning:** If seals are too slow, raise **`sync_day_close_max_inflight`** (parallel day-close workers, default **4**) or **`archive_janitor_budget_seconds`** slightly (still bounded per tick). If web/API or Postgres latency spikes during maintenance, lower janitor budget first, then raise **`archive_zstd_nice`** or **`archive_zstd_ionice_level`**. Override env **`SYNC_ARCHIVE_SEAL_WORKERS`** mirrors validation fanout.

**Page cache:** Large daily archives fill the Linux page cache during zstd I/O. With **`archive_zstd_drop_page_cache=yes`** (default), **`zstd_cli.py`** issues **`POSIX_FADV_SEQUENTIAL`** before reads and **`POSIX_FADV_DONTNEED`** after successful one-shot access. This is a hint only (not **`O_DIRECT`**); macOS dev hosts no-op. Decompress restore materializes to a temp ``.tar``, verifies with **`tar tf`** on that tmp, then replaces the canonical sibling (one zst pass; no pipe preflight on the restore path).

## Archive janitor (continuous ingest)

Ingest is treated as always-on: the supervisor no longer runs blocking seal/raw/`.tar` maintenance bursts. Cold-path archive work accrues as **day debt** and is consumed in background **micro-batches** (`ArchiveJanitor` thread).

| `[PIPELINE]` key | Default | Role |
|----------------|---------|------|
| **`archive_janitor_budget_seconds`** | **`30`** | Max wall time per janitor **debt-drain** (after scheduled maintenance) |
| **`sync_day_close_max_inflight`** | **`4`** | Pipeline occupancy **and** parallel `DAY_CLOSE` worker count (continuous refill) |
| **`archive_keep_uncompressed_tar`** | **`no`** | Drop prior-day `.tar` at seal when raw is gone; global `yes` retains until tar-drop |
| **`archive_today_uncompressed_tar_grace_hours`** | **`8`** | Keep calendar-today `.tar` after local midnight (hours) when global keep is `no` |
| **`archive_janitor_debt_high_watermark`** | **`50`** | Debt depth before temporary burst scaling |
| **`archive_janitor_debt_burst_factor`** | **`1.5`** | Budget multiplier under high debt |
| **`archive_janitor_debt_max_entries`** | **`200`** | In-memory debt cap; lowest-priority entries evicted with a warning when full |
| **`archive_janitor_raw_paths_per_tick`** | **`1000`** | Incremental raw deletes per `RAW_REMOVE` debt item (large-day safety) |
| **`archive_maintenance_idle_seconds`** | **`300`** | Optional idle-only 2× budget bonus (not required for progress) |
| **`sync_archive_max_inflight_jobs`** | **`2`** | Concurrent disjoint daily-tar append jobs |
| **`sync_archive_worker_stall_seconds`** | **`600`** | Log stalled append workers (observability) |
| **`sync_enable_ingest_first_durability_mode`** | **`yes`** | Checkpoint after DB even when append is deferred |

Progress and resume state persist in **`.sync_archive_maint_hints.json`** (version **2**: `debt_queue`, `day_phases`, `validated_days`). Under ingest backlog, **full** accrual waits for an empty ingest queue; **partial** prior-day accrual remains on scheduled passes. **Per-batch calendar-day drain:** after each ingest chunk (and on `archive_finalize` / idle finalize), the supervisor enqueues **`DAY_CLOSE`** for prior days whose ingest+tar-append is quiescent and whose oldest-first stream has moved on — within **≤1 batch** of quiescence, without waiting for every-N-chunk maintenance. Scheduled startup / every **`rescan_every_chunks`** / ingest-queue-drain passes remain the snapshot backstop. Legacy triple `SEAL`/`RAW`/`TAR` hints coalesce to `DAY_CLOSE` on load. Janitor **chains ticks** while processable debt remains; budget/exception paths re-queue unprocessed debt (no silent loss).

**Multi-week backlog / disk pressure:** raise **`sync_day_close_max_inflight`** and **`archive_janitor_budget_seconds`** modestly; keep **`archive_janitor_raw_paths_per_tick`** high enough for your daily file count (e.g. 15k/day may need several internal batches within one day-close worker pass). Pre-seal verify completes on the **`day-close-N`** worker in a single **`DAY_CLOSE`** invocation (monotonic **`pre_seal_verify classify progress`** logs); it is not re-enqueued mid-verify for wall-clock limits. Default **`archive_keep_uncompressed_tar=no`** drops prior-day `.tar` at seal once raw is gone; calendar-today keeps `.tar` for **`archive_today_uncompressed_tar_grace_hours`** (default 8h after local midnight). Set global **`archive_keep_uncompressed_tar=yes`** during heavy same-day append if you need the uncompressed `.tar` for late raw appends.

**Hard isolation (recommended long-term):** run [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) so **`pipeline`** gets a dedicated cpuset; you can then run zstd at lower nice/ionice and/or raise janitor budget with less impact on **`web`**/**`db`**.

## OOM and `sync_timedb` process tree

Kernel OOM may kill an ingest pool worker (`[worker:ingest-pool]`) with a **transient multi‑GiB anon spike** while parsing giant raw segments (`readlines()` on multi‑GiB–28 GiB files). **`sync_supervisor_rss_limit_mb`** checks **supervisor `/proc/self` only** — it does not see worker RSS. Spawn pool workers used to survive as orphans when the supervisor died first; **`PR_SET_PDEATHSIG`** addresses that case.

| Mitigation | Role |
|------------|------|
| **`PR_SET_PDEATHSIG` (SIGKILL)** on pool workers | Workers exit when the supervisor dies so **supervisord** can restart a clean tree |
| **`pipeline` `mem_limit` / `memswap_limit`** (Compose) | Cgroup cap before host global OOM on swapless hosts; **128 GiB** is a reasonable default on **192 GiB** RAM (see `docker-compose.app.yaml.example`) |
| **`sync_ingest_max_file_read_bytes`** (default **512 MiB**) | Stream-parse larger segments instead of **`readlines()`** |
| **`sync_bulk_create_batch_size`** (default **10000**) | Combined ingest: flush parse → delta/arc → **`bulk_create`** every N stats rows (complete time sample first); same knob sizes DB write batches |
| **`sync_pool_process_cap`** (default **16**) | Cap live ingest pool width and archive metadata discovery threads; sliding-window **imap inflight equals pool size** (RSS guard for giant-file parse) — not sequential fixed sub-batches |
| **`sync_ingest_pool_maxtasksperchild`** (default **0**) | **Ingest pool only.** Default **`maxtasks=0`**: workers stay alive; supervisor retires only on **failure** / **RSS** (fair-share **`sync_ingest_cooperative_recycle_rss_fraction`**) plus in-worker release; path size alone does **not** retire. Set **`maxtasks=1`** to recycle after every file (drops idle pandas/Django heap). Soak with **`sync_ingest_worker_memory_telemetry=yes`**. **Archive** and **sealed-archive CLI** spawn pools always use hardcoded **`maxtasksperchild=1`** (not this INI). **Idle-pool recover (2026-07-09):** after full-redispatch thrash, recover uses **abandon-pool** (`abandon_after_kill=True` — SIGKILL workers, **never** stdlib `Pool.terminate()`/`_help_stuff_finish`) with a recover wall → exit **124** `idle_pool_taskqueue_dead` on timeout; proactive swap abandons the old pool before recreate. |
| **`sync_ingest_malloc_trim_after_file`** (default **yes**) | Linux **`gc.collect()`** + **`malloc_trim(0)`** after each ingest pool task; full release also clears daily archive member L1 |
| **`sync_process_tree_rss_limit_mb`** / **`sync_process_tree_rss_exit_mb`** (default defer **110000** MiB on **128 GiB** cgroup hosts) | Defer chunks or exit **137** on **supervisor + pool worker** RSS |
| **`sync_supervisor_rss_limit_mb`** | Supervisor-only fail-fast (legacy; insufficient alone for worker spikes) |
| **`abort_if_pool_workers_dead`** | Parent exits **137** when a worker is OOM-killed first (fail-fast vs hang) |

**Catch-up INI starting points** (until backlog of multi‑GiB segments clears): defaults above ship in **`hpcperfstats.ini.example`** with the release — **`sync_pool_process_cap=16`**, **`sync_ingest_pool_maxtasksperchild=0`**, **`sync_ingest_malloc_trim_after_file=yes`**, **`sync_process_tree_rss_limit_mb=110000`**. On **small dev compose** hosts (e.g. 8–16 GiB pipeline cgroup), set **`sync_process_tree_rss_limit_mb=0`** to disable tree defer and optionally lower **`sync_pool_process_cap`** if RAM-bound. Historical catch-up tuning: `sync_ingest_chunk_size=8–16`. On very large trees (~100k+ closed paths), tune **`sync_startup_snapshot_wait_seconds`** (default **300**) and **`sync_day_close_max_inflight`** (default **4**, pipeline occupancy and parallel workers). Boot **`DAY_CLOSE`** discover runs on the janitor thread only; ingest begins after handoff recover without waiting for day-close completion.

**Post-fix disk-release progress grep:** `janitor: discover_ready_day_close`, `janitor: day_close enqueue`, `Archive janitor tick done` (expect `debt_popped>0` or progressing day-close), `sync_timedb: startup archive scan ready`, `sync_timedb: pending rescan done`, `idle_rescan_snapshot_source=`, `pending cap supplement from snapshot`.

**Idle queue drain:** after **`pending=0`**, supervisor idle rescan uses coordinator/accrual **`closed_paths`** (not incremental tree walk every cycle) and **`merge_rescan_discovered_into_pending`** before cap — same parity as periodic **`rescan_every_chunks`** path.

**Forensics checklist:** inside the pipeline container read `memory.max`, `memory.current`, `memory.peak`, `memory.events` (`oom_kill`); `ps -eo pid,rss:10,cmd` for `[worker:ingest-pool]` vs `[main]`; `find` largest raw stats paths. Log a short packet under **`test_runs/`** when investigating.

**Docker/cgroup OOM vs kernel OOM:** cgroup kills often leave **host `dmesg` empty**. Inside the pipeline container:

```bash
docker compose exec pipeline sh -c '
  echo "=== memory.events ==="
  cat /sys/fs/cgroup/memory.events 2>/dev/null || cat /sys/fs/cgroup/memory.events
  echo "=== memory.current / max / peak ==="
  for f in memory.current memory.max memory.peak; do
    printf "%s: " "$f"; cat /sys/fs/cgroup/$f 2>/dev/null || echo n/a
  done
'
```

| `memory.events` field | Meaning |
|-----------------------|---------|
| `oom_kill` | Processes SIGKILL'd by the **container memory cgroup** (non-zero ⇒ Docker/cgroup OOM in this container lifetime) |
| `oom` / `oom_group_kill` | Related cgroup OOM counters |

Also: `docker inspect <pipeline_container> --format '{{.State.OOMKilled}}'` (main PID only). **`memory.events` counters reset** when the container is recreated — compare `StartedAt` and historical `docker events` if investigating a past incident.

**Pool worker death diagnostics (exit 137):** when a worker dies, `sync_timedb` logs **`ERROR: pool worker death diagnostics:`** with `likely_cause`, `dead_workers` (pid/exitcode/signal), `alive_workers`, `cgroup_oom_kill`, tree RSS breakdown, and `in_flight_sample`. **`likely_cause=recycle`** with exitcode **0** and replacements keeping pace (`alive_workers` ≥ `total_workers - dead`) is normal **`maxtasksperchild`** churn — **must not** exit **137**; supervisor logs **`INFO: pool worker recycle in progress`** (once per dead PID) and optional **`WARN: pool worker recycle slow`** after **`sync_pool_worker_recycle_grace_seconds`** (default **60**). **`likely_cause=recycle_stuck`** when all dead workers have exitcode **0** but no replacements remain alive. Genuine SIGKILL with `cgroup_oom_kill=0` is logged as **`sigkill_non_cgroup`**. The deprecated **`sync_pool_worker_recycle_grace_polls`** global counter must not fatal across consecutive healthy recycles (different PIDs).

Tune tree RSS limits to ~**70–80%** of the pipeline cgroup cap; grep kernel logs for `oom-kill` + `sync_timedb.py` and correlate with `pending_stats` / janitor debt in application logs.

## Archive recovery (operators)

Raw stats on disk remain the **source of truth** until validated archive membership, DB ingest gate (when enabled; **`sync_archive_require_db_ingest=yes`** requires first **and** last digit-leading timestamp seconds in `host_data`, via streaming head + EOF-backward tail reads), and janitor raw removal all succeed.

| Symptom | Safe recovery |
|---------|----------------|
| **Bad `.tar.zst`, good legacy `.tar.gz`** | Remove or rename the corrupt `.tar.zst`; restore sibling `.tar` from gzip (`decompress_compressed_to_tar` / `zstd -d --format=gzip`). Raw stats stay until validation passes. |
| **Sealed `.tar.zst` unreadable for ingest lookup** | Check `zstd -t /path/YYYY-MM-DD.tar.zst`. If **passes** but tar stream fails (`unexpected end of data`), the tar **payload** is truncated — prefer sibling **mutable `.tar`** when `tar tf` passes (populate auto-fallback; clears stale `archive_day_ingest_skip` when sealed is gone). Otherwise restore/re-seal from legacy `.tar.gz`. If **`zstd -t` fails**, the zstd frame is corrupt. When both sealed stream and tar scan fail, ingest logs **`ERROR: daily sealed archive unusable for ingest lookup`** once per day (`reason=zst_frame_invalid` or `tar_truncated_or_unreadable`), sets Redis **`archive_day_ingest_skip`**, and skips tar-append duplicate-check until repair. **`atomic_seal_tar_to_zst`** refuses seal when tar fails **`verify_tar_archive_readable`**. Active-ingest decompress for append keeps sealed sibling (`remove_compressed=False`). DB-complete ingest continues (`need_archival=False` for skipped days). |
| **Missing `.tar` after mistaken drop** | Decompress sealed `.tar.zst` (or legacy `.tar.gz`) to sibling `.tar` before append; never delete raw while tar/zst validation fails closed. |
| **Persistence contract bump (v2+)** | On startup, **`ensure_persistence_contract`** compares **`{archive_data_dir}/.sync_timedb_persistence.json`** to the built-in version. Mismatch logs **`persistence reset old=… new=…`** and deletes all registered **`.sync_*`** sidecars (checkpoint, hints, manifests) so ingest/archive reprocesses from scratch — **no manual sidecar delete required** after deploys that bump the contract. |
| **Stuck janitor debt after crash** | Inspect **`.sync_archive_maint_hints.json`** v2 (`debt_queue`, `day_phases`); restart **`sync_timedb`**. Janitor **proactive discover** enqueues checkpoint-complete **`DAY_CLOSE`** each tick (`discover_ready_day_close`); startup heavy pass + supervisor immediate hooks also enqueue. |
| **Corrupt daily `.tar` before append** | `replace_corrupt_tar_from_compressed_backup` tries zst then gz; append stays fail-closed (`False`) if restore fails—raw files remain. |
| **`ingest_first_archive_abandoned_raw` in logs** | With **`sync_enable_ingest_first_durability_mode=yes`**, exhausted append retries checkpoint paths as processed while raw may never reach tar; janitor/partial accrual must eventually enqueue raw debt—grep logs for this marker and verify raw still on disk until cold path succeeds. |
| **Head+tail ingested, gaps in the middle (M2)** | Gate checks first and last digit-leading timestamp seconds only (no full-file scan); missing middle segments may still pass—head-only or tail-only partial ingest fails. Treat unexpected raw deletion as incident-driven review, not routine ops. After deploy, expect more **`not_head_tail_ingested`** / day-close handoff until ingest catches up. |
| **Unparsable closed raw blocking a day** | Inspect **`{archive_data_dir}/.sync_timedb_unparsable_raw.json`** and **`{archive_data_dir}/.sync_timedb_unparsable_raw/`**; ingest moves permanently unparseable closed raw off the hot tree when parse fails (look for `error: process data failed` / `Possibly corrupt file` in `sync_timedb` logs). Restore only after fixing content or deliberate operator review—do not delete manifest entries casually. Parsable-but-unmapped raw still blocks until ingest/archive catches up. |
| **Canonical startup archive scan** | One **`build_archive_maintenance_snapshot`** per boot: janitor calls **`begin_build()`** → build → **`publish(from_janitor=True)`** → **`invalidate_unmapped_disqualify_cache()`**; supervisor rescan may **`wait_for_snapshot(allow_build=True)`** after **`sync_startup_snapshot_wait_seconds`**. Deep-copied coordinator snapshot retains **`closed_paths`** after accrual trim for unmapped disqualify. Log: **`sync_timedb: startup archive scan ready paths=N`**. |
| **Quiescent dirty `.tar` at startup** | When a daily **`.tar`** is dirty but **no closed raw remains on disk** for that calendar day, janitor **`_discover_and_enqueue_ready_day_close(reason=startup)`** enqueues **`DAY_CLOSE`** debt (`awaiting_janitor_discover`). Requires filesystem truth (`os.path.isfile` on every unprocessed path). |
| **Checkpoint-complete DAY_CLOSE at startup** | When all **mapped** closed raw for a calendar day is checkpoint-complete but **`.tar`** / raw remain on disk, janitor boot discover enqueues via manifest coordinator → **`DAY_CLOSE`** debt. Cap (startup and steady-state): **`sync_day_close_max_inflight`** (default **4** = occupancy and parallel workers). Candidate log: **`queued` / `waiting_on_ingest` / `disqualified`**. |
| **Day-close verify/delete on janitor thread** | **Pre-seal verify (open `.tar`) → dedupe → seal → post-seal parity verify → `apply_batch_delete`** in **`ArchiveJanitor._close_one_day`**. Grep: **`janitor: day_close pre_seal_verify`**, **`janitor: day_close seal`**, **`janitor: day_close post_seal_verify`**, **`janitor: day_close delete`**, **`Archive janitor tick done`**. |

## PostgreSQL connection budget (operator)

Rough peak connections:

`web_workers + metrics_pool_processes + sync_timedb_processes + overhead`

Compose sets **`max_connections=500`** with **reduced `work_mem` / parallel gather / maintenance buffers** to limit RAM spikes while keeping slot headroom. The stack does **not** use an external pooler (no PgBouncer): sizing is direct Django → Postgres. Still use **`WEB_CONCURRENCY`**, **`metrics_pool_process_cap`**, **`sync_pool_process_cap`**, and **`parallel_db_prefetch_max`** so concurrent heavy queries stay bounded.

## Connection lifetime, query timeouts, and staggered pipeline

- **`CONN_MAX_AGE`:** Default **90** seconds (`[PORTAL] db_conn_max_age` or **`DJANGO_CONN_MAX_AGE`**). Lowers how long idle Gunicorn workers hold a backend. Does not cap peak concurrency under full load; pairs with the caps above.
- **`statement_timeout` / `idle_in_transaction_session_timeout`:** Defaults **120000 ms** and **300000 ms** for PostgreSQL sessions via Django **`OPTIONS`** (`conf_parser.build_postgres_connection_options()`). The **`db`** service in **`docker-compose.yaml`** sets the same server parameters so non-Django clients inherit them. Disable per-session timeouts by setting **`DJANGO_DB_STATEMENT_TIMEOUT_MS=0`** and **`DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=0`** (and adjust compose if you remove server defaults). Tune upward only if legitimate bulk jobs hit the limit.
- **Staggered supervisord jobs:** [`services-conf/supervisord.conf.example`](../services-conf/supervisord.conf.example) starts **`listend`** first (higher priority), then **`sync_timedb`** after **20s**, then **`update_metrics`** after **90s**, so restarts do not open every DB pool at the same instant. The example sets **`stopasgroup=true`** and **`killasgroup=true`** on **`sync_timedb`** so pool workers are torn down when the supervisor exits via **`os._exit(124)`** (reduces orphan **`sync_timedb [main]`** / idle DB sessions after stall). Adjust sleeps, **`priority`**, and kill-as-group for your site.

## Pipeline cpuset priority budgeting (full workflow scope)

`sync_timedb` and `update_metrics` now support a cpuset-aware priority budget from `conf_parser.derive_pipeline_cpuset_priority_budget()`:

- `S` (sync ingest cap): default `floor(0.60 * C)`
- `A` (sync archive cap): default `floor(0.15 * C)`
- `M` (metrics cap): default `floor(0.20 * C)`
- `R` (reserve for maintenance/jitter): default `floor(0.05 * C)`

Where `C = min(total_cores, os.cpu_count())` for the pipeline container cpuset. If `S + A + M + R` exceeds `C`, the reducer lowers `M` first, then `A`, then `S` (sync-first policy). Minimum floors for `M` and `A` are configurable to keep bounded forward progress in normal-class work.

Priority buckets used for accounting and deprioritization:

- `real_time`: listener feed path + sync ingest (+ db-writer path when enabled)
- `normal`: sync archive/retries + update_metrics + startup migrations/bootstrap
- `best_effort`: `syslog-ng`, `seal_syslog_daily.py`, optional `rsync_data`, optional browser/API test traffic

Relevant ini keys (all under **`[PIPELINE]`** unless noted):

- `sync_enable_cpuset_priority_budget`
- `sync_budget_ingest_ratio`
- `sync_budget_archive_ratio`
- `sync_budget_metrics_ratio`
- `sync_budget_reserve_ratio`
- `sync_budget_min_metrics_percent`
- `sync_budget_min_archive_percent`
- `pipeline_overlap_mode` (`balanced` or `ingest_priority`)
- `metrics_ingest_priority_scale`
- `metrics_min_processes`
- `sync_enable_overprovision_mode`
- `sync_budget_overcommit_factor`
- `sync_overprovision_ingest_multiplier`
- `sync_overprovision_archive_multiplier`
- `sync_overprovision_metrics_multiplier`

Archive append and day-close concurrency are fixed thread/slot caps only (**`sync_archive_max_inflight_jobs`**, **`sync_day_close_max_inflight`** — one calendar day / daily tar per slot). There is no adaptive queue burst/backoff or soft queue watermark logging.

### Metrics window-coverage readiness (summary plots)

By default **`update_metrics`** defers each job until in-window `host_data` samples exist within configurable margins of Slurm **`start_time`** and **`end_time`** (job aggregate: any accounting host may satisfy each edge). Defaults: **`metrics_readiness_require_window_coverage = yes`**, **`metrics_readiness_start_margin_seconds = 600`**, **`metrics_readiness_end_margin_seconds = 600`**.

Jobs with long monitor prolog gaps (telemetry begins hours after Slurm start) stay deferred until early-window data exists or an operator disables the gate. Set **`metrics_readiness_require_window_coverage = no`** in **`[PIPELINE]`** only for recovery/backfill emergencies. When metrics run, **`telemetry_first_time`** / **`telemetry_last_time`** on **`job_data`** and plot artifact fingerprints (schema version **10**) incorporate those bounds so backfill invalidates stale summary plots.

**Scoping:** strict readiness and the coverage proxy both use **`job_data.host_list`** hostnames (FQDN) and the Slurm window — not **`host_data.jid`**. Any in-window sample on an accounting host may satisfy a margin (including samples from other jobs on shared nodes during overlapping wall-clock windows). That trade-off fixes tail-only false-ready bugs but is weaker than per-jid isolation.

**Permanent defer / stall watchdog:** jobs whose telemetry never reaches both margins (for example multi-hour prolog gaps with default 600s margins) remain in the deferred-not-ready map with 10s retries, then quarantine intervals — they are not dropped from candidacy while still in range. Large historical backfills of such jobs increase readiness-thread DB load and can keep compute workers idle; disable the gate or reduce margins for emergency backfill, and watch scheduler stall metrics.

**Gate failure stamp:** when the coverage gate fails (`start_ok=False` or `end_ok=False`), **`update_metrics`** removes persisted metrics rows and plot/detail artifacts for that job, writes the full metrics catalog with **`no_data_reason = "Insufficient Data For Metrics Processing"`**, and keeps the job in the daily candidate list (via **`gate_failure_recheck`** in **`_jobs_queryset`**) on every scheduler run until margins pass and **`Metrics.run`** succeeds. **`live_distinct_needs_refresh`** provides a secondary re-admission path when ingest adds new in-window sample times. Pre-existing jobs with numeric metrics from before this behavior need **`--rerun`** or live-distinct/fingerprint drift to enter the gate-failure path.

**Performance:** readiness batches dedupe host aggregates across jobs sharing the same window; precomputed bounds are passed into **`Metrics.run`** to avoid a second in-window aggregate during persist.

## Observability

- Run **`python hpcperfstats/site/manage.py pg_connection_stats`** from the repo root (with **`HPCPERFSTATS_INI`** / config and DB reachable) to print **`pg_stat_activity`** totals for the current database (`machine` app management command).

## Docker Compose CPU pinning (all services)

The stack **includes** two optional merge fragments (committed as **`services: {}`** so clones stay **unbound** by default):

- [`docker-compose.cpu-pinning.infra.yaml`](../docker-compose.cpu-pinning.infra.yaml) — **`db`**, **`redis`**, **`proxy`**, **`rabbitmq`**
- [`docker-compose.cpu-pinning.app.yaml`](../docker-compose.cpu-pinning.app.yaml) — **`web`**, **`pipeline`**

Both fragments are **`include`d from [`docker-compose.yaml`](../docker-compose.yaml)** (same directory as [`docker-compose.app.yaml`](../docker-compose.app.yaml)); use `docker compose -f docker-compose.yaml ...` from the repo root so merges apply.

**Unbound (default):** empty fragments let the host scheduler place containers (often best on small or uneven hosts).

**Pinned:** run [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) on the **Linux deployment host**. It uses **`min([DEFAULT] total_cores, os.cpu_count())`** and [`hpcperfstats/compose_cpu_layout.py`](../hpcperfstats/compose_cpu_layout.py) to assign **contiguous** cpusets with **db** and **web** first, small slices for **Redis** / **RabbitMQ**, **pipeline** last. **`proxy`** uses the same cpuset string as **`web`** (allowed overlap). To force **unbound** fragments again: `python scripts/apply_compose_cpu_pinning.py --inactive`.

```bash
export HPCPERFSTATS_INI=/path/to/hpcperfstats.ini
python scripts/apply_compose_cpu_pinning.py --dry-run   # prints infra + app YAML, separated by ---
python scripts/apply_compose_cpu_pinning.py             # overwrites both fragment files
# If the host reports fewer logical CPUs than your ini budget (e.g. cgroup), pin layout to 40:
python scripts/apply_compose_cpu_pinning.py --total-cpus 40
```

Then start the stack as usual (no extra `-f` flags):

```bash
docker compose -f docker-compose.yaml up -d
```

**Note:** The old **`docker-compose.numa-pinning.yaml`** overlay is obsolete; use the fragments above only. The filename remains in **`.gitignore`** so local experiments do not get committed; no workflow scripts reference that compose file.

## NUMA overrides (web / pipeline / proxy)

Topology is read from **Linux sysfs**: `/sys/devices/system/node/node*/cpulist` (not hardcoded).

When [`should_apply_numa_pinning`](../hpcperfstats/numa_topology.py) is true **and** **two different** NUMA nodes are selected for web vs pipeline, the generator **replaces** the linear **`web`**, **`pipeline`**, and optionally **`proxy`** cpusets with those nodes’ sysfs **`cpulist`** values. On a **single** NUMA node, web and pipeline would otherwise each get the **full** socket and erase the db/web/pipeline split — the script **keeps** the **linear** layout from [`compose_cpu_layout.py`](../hpcperfstats/compose_cpu_layout.py) instead. **`db`**, **Redis**, and **RabbitMQ** always use the linear layout in this phase — on multi-NUMA hosts their cpusets may **overlap** numerically with the web node’s CPUs; Docker allows overlapping `cpuset`s between containers. Tighter **Postgres-on-socket** placement is a possible future refinement.

## Archive janitor and seal/append lock contention

The background **`ArchiveJanitor`** runs up to **`sync_day_close_max_inflight`** (default **4**) **`DAY_CLOSE`** days in parallel per tick (continuous refill; full seal → raw → `.tar` pipeline per day). Tune **`archive_janitor_budget_seconds`**, **`archive_janitor_raw_paths_per_tick`**, and **`archive_janitor_debt_high_watermark`** / **`archive_janitor_debt_burst_factor`** on sites ingesting **~15k raw files/day**.

**Seal vs append:** `atomic_seal_tar_to_zst` holds an exclusive **`file_write_lock`** on the daily `.tar` for the full compress/replace window. Hot-path append uses the same lock (default **60s** timeout). Large-day seals during ingest can surface append **`TimeoutError`** (fail-closed). Mitigations: use **`archive_keep_uncompressed_tar=yes`** (or rely on today's grace window) during heavy same-day append, isolate **`sync_timedb`** archive work on a dedicated cpuset (see above), and rely on janitor disqualification for in-flight days.

**Validation read locks:** parallel raw-remove validation defaults to **`sync_archive_validation_max_workers=2`** (INI `[PIPELINE]`). Raise only when append/read-lock contention is acceptable.

**Pool stall guard (exit 124):** When `imap_unordered_watch_pool` or **`imap_sliding_window_watch_pool`** sees **N** consecutive poll timeouts with **no** completed task while all workers are still alive, it raises `MultiprocessingPoolStallError` and `sync_timedb` exits with status **124**. The imap **poll** interval remains **`sync_pool_poll_timeout_s`** (default **5s**); repeated **`WARN: pool imap stall deferred`** lines during legitimate long-ingest or Redis populate waits are **rate-limited** by **`sync_pool_stall_defer_log_interval_s`** (default **60s**; **`0`** = every poll). **Idle-pool ghost in-flight** (workers idle in `futex_wait`/`pipe_read` but `pending_async` non-empty — main thread at **`imap_sliding_window_watch_pool`** poll **`time.sleep`**) is handled **reconcile-first**: **`try_collect_async_result`** (`get(timeout=0)` even when `ready()` is false) collects orphan completions (**H1**); **`reconcile_idle_pending_async`** redispatches stale `AsyncResult` entries after **`sync_pool_idle_reconcile_polls_per_round`** idle polls (default **4**), using ingest idempotency via **`_resolve_streaming_ingest_start`** (not path-tail `host_data` probes). Up to **`sync_pool_idle_reconcile_max_rounds`** redispatch rounds (default **3**) run before **`idle_pool_ghost_inflight`** exit **124** via **`idle_pool_ghost_abort_polls`** (`max(12, min(120, stall_abort_after // 20))` at default **5s** poll). Reconcile also runs during **`maxtasksperchild`** recycle grace when `pending_async` is non-empty (**H2**). **`long_ingest_budget`** defer is **suppressed** when idle ghost is detected (`defer_reason=idle_pool_ghost_inflight`). **Giant-tail supplement** (`supplement=yes` fast completions while one giant parses) is **healthy** — not ghost stall; supplement prefers **sub-1 GiB** then **[1 GiB, 8 GiB)** from a **supplement_queue** reservoir (`sync_ingest_queue_max_size` × `sync_ingest_giant_pool_supplement_queue_multiplier`, default **3000×2=6000**) at batch start and mid-imap refresh; paths **≥ 8 GiB** stay chunk-only. **Per in-flight set**, the legacy dynamic **N** is computed from the largest resolved per-file ingest budget among paths currently dispatched: `int(max_timeout / poll_s) + 1`, clamped between the per-file floor (**900s** → **181** polls at **5s**) and **`sync_pool_stall_abort_after_timeouts`** (**17320** default ceiling = **86600s** wall, **200s above** max per-file). Small-file in-flight sets therefore abort faster than multi‑GiB stragglers. Keep the **ceiling** product **above** `sync_ingest_per_file_timeout_max_s` when raising the per-file max. **`long_ingest_budget`** defer remains a safety net when worker-registry `effective_ingest_timeout_s` exceeds the batch precompute **and workers are not idle**. At startup, when **ceiling** wall ≤ max per-file, the supervisor logs a **WARN**. Per-file budgets are **size-proportional**: `clamp(floor + ceil_mib(size) × per_mib, floor, max)` with defaults mapping **30 GiB → max (24h)**. Workers log **WARN: ingest per-file timeout budget** when resolved budget **≥ 1800s** (30 minutes). On expiry the file returns `ingest_ok=False` for retry instead of blocking the chunk. Logs include **`batch_max_ingest_timeout_s`**, **`dynamic_stall_abort_after`**, **`dynamic_stall_wall_s`**, plus **`ERROR: Pool imap stalled`** (with optional diagnostics suffix) before exit, and **`WARN: pool imap stall progress`** at 50%/75% of the **current** abort threshold. Stall diagnostics (grep these on the next incident):

**Ingest sliding window (why only a few large ingests looked “blocking”):** Within each chunk, `_imap_ingest_paths_batched` caps concurrent pool tasks at the live pool size (**`sync_pool_process_cap`**, default **16**). Historically this used **sequential sub-batches**: when three of sixteen paths were multi‑MiB stragglers, the other workers finished and sat **idle** until those three completed before the next sub-batch started — it looked like ingest was “stuck” on a few large files even though the backlog had smaller files waiting. The sliding-window dispatcher (`imap_sliding_window_watch_pool`) keeps at most `inflight_cap` **`apply_async`** tasks outstanding and **refills** each completion from the oldest-first chunk queue, preserving RSS bounds while avoiding tail idle slots. When the chunk queue is exhausted and at least one **≥ 2 GiB** file is in flight, **giant pool supplement** (`sync_ingest_giant_pool_supplement_*`) may dispatch paths from a **supplement_queue** reservoir (default **6000** = `sync_ingest_queue_max_size` × multiplier) built via **`pending_minus_chunk(pending, chunk)`** plus closed-path snapshot, preferring **sub-1 GiB** then **[1 GiB, 8 GiB)**. Mid-imap **`giant pool supplement replenish`** rebuilds that same reservoir when the frozen tail runs dry while giants remain in flight. The batch **waits for all in-flight work (including giants) before returning** — supplement does not advance the chunk counter early. Supplement paths already dispatched or completed in the **same batch** are excluded via the ingest tracker **`batch_seen_paths`** set (prevents re-dispatch spin on DB-complete tail files). The first supplement dispatch per chunk logs **`INFO: sync_timedb: giant pool supplement begin`** with `pending_tail_n`, `pending_tail_sample`, `in_flight_giants` (`basename:size_bytes:timeout_s`), and `selected`. Per-file ingest emits **one** supervisor line per stats file: **`ingest file path=… outcome=… elapsed_s=… ingest_ok=… archive=… db_skip=… size_bytes=…`** with optional **`remaining=`** (chunk backlog after this file), **`supplement=yes`** (giant-pool supplement path), **`parse_elapsed_s`**, **`stats_rows`**, **`proc_rows`**, **`fail_reason`**, and **`sealed_remaining=`** during sealed-archive backfill. DB-complete skips use **`outcome=db_skip`** with **`db_skip=head_tail|tail_window|full_scan`** (no separate skip line). **`sync_pool_process_cap`** (live pool size) still limits concurrent giant parses, not total queue depth. Ingest uses a single combined parse+write path in **`ingest_pool`**.

**Dispatch order vs log order:** **`ingest file path=`** lines reflect **pool completion order** within a chunk (parallel workers); they may permute among simultaneous dispatches. **Scheduling order** is **`pending_stats_files` head** (oldest-first by filename epoch). Each chunk logs **`sync_timedb: chunk dispatch begin`** with `paths_sample` and `epochs` before pool dispatch — compare that line to completion lines when investigating skips. **Not** within-chunk noise: **`oldest_day_chunk_gate_fallback`** with **`calendar_days`** showing months ahead of **`oldest_tar`** (cross_day_bucket) while **`pending_n`** is large — fixed by **`oldest_day_chunk_gate_cross_day_defer`** (resume global pending head). **Aligned-only gate:** `oldest_tar` and day-close `unprocessed=` count only paths whose filename/mtime day matches the tar; cross-day misbuckets do not pin early days or inflate waiting_on_ingest. Pending cap merges **all** days’ on-disk unprocessed and may log **`pending cap supplement replace`** when older snapshot paths displace a full newer queue. Grep: `chunk dispatch begin`, `oldest_day_chunk_gate_cross_day_defer`, `pending reconcile cap`, `pending cap supplement replace`.

| Field | Meaning |
|-------|---------|
| `pool_workers_alive` / `in_flight_n` | Alive workers vs imap tasks not yet returned |
| `stall_defer` / `defer_reason` | Why populate defer did or did not reset the stall counter (`redis_warm`, `redis_populate_active`, `no_day_hint`, **`idle_pool_ghost_inflight`**) |
| `sync_ingest_per_file_timeout_s` | Per-file budget floor (compare to `effective_ingest_timeout_s` on stragglers) |
| `sync_ingest_per_file_timeout_max_s` | Per-file budget ceiling (batch dynamic abort clamped below this) |
| `batch_max_ingest_timeout_s` | Max resolved per-file budget for current in-flight ingest paths |
| `dynamic_stall_abort_after` / `dynamic_stall_wall_s` | In-flight-scoped poll abort count and wall seconds |
| `effective_ingest_timeout_s` | Max resolved budget from worker registry (`-` if unknown) |
| `imap_batch_cap` / `chunk_batch` / `imap_batch` | Why `in_flight_n` may be ≪ chunk size |
| `distinct_in_flight_days` / `in_flight_file_meta` | Calendar days and file sizes in the stall sample |
| `seconds_since_last_imap_completion` | Time since last imap yield (`-` = none yet this chunk) |
| `ingest_pipeline` | `split_parse_write`, `combined`, or inline |
| `day_close` | Active DAY_CLOSE pipeline (`tar:status:last_progress:age_s`; historical stall token `day_close_manifest`) |
| `chunk_prewarm` | One-line chunk prewarm summary (`INFO: chunk prewarm days=…`) |
| `worker_stages` | `pid:stage:basename:age_s` from ingest workers (includes `archive_member_lookup:hget`, `duplicate_scan_streaming`, `itimes_overflow_db`, `db_write`) |
| `worker_registry_n` / `worker_registry_gap` | Registry entries vs `in_flight_n` (gap > 0 means missing worker stage wiring) |
| `redis_by_day` | Per-day Redis populate snapshot when multiple days in flight |

**Parse-stage stall (DB-complete large raw):** When `head_timestamp_present_in_db` is true but the file is multi‑MiB, ingest used to `readlines()` the entire segment and run a full duplicate scan before any deadline checkpoint — workers stayed in top-level **`parse`** (no `duplicate_scan_streaming` substage) until pool abort. Mitigations: **head+tail DB probe** skips the duplicate scan when both seconds are present; **`sync_ingest_stream_duplicate_scan_bytes`** (default **8 MiB**) routes duplicate detection through the streaming path; **`sync_ingest_db_complete_tail_window_lines`** (default **500**) probes the tail timestamp window before a full-file duplicate scan on large head-present files; **`load_stats_file_lines` / `iter_stats_file_lines`** honor the monotonic ingest deadline every 1000 lines / 1 MiB read; combined ingest uses **one** `SIGALRM` per worker task (no nested `_run_ingest_timed` on parse).

**Archive maintenance I/O:** Full-tree `build_archive_maintenance_snapshot` runs on **heavy** passes only — **`reason=startup`** (adopts the coordinator snapshot when already published; never a second 500s+ collect) and **`reason=day_ingest_complete:<YYYY-MM-DD>`** when a calendar day has **checkpoint-complete** ingest (no mapped closed raw minus on-disk checkpoint for that daily `.tar`; pending-queue drain alone is insufficient). **`every_n_chunks`** and **`ingest_queue_empty`** trigger **light** passes (candidate report + async submit from existing accrual; no tree walk). **`day_ingest_complete` heavy passes adopt the janitor accrual snapshot** when present (mapping or closed_paths) instead of re-walking the raw tree; full collect only when accrual is missing (cold start). **Heavy passes defer** while the supervisor chunk is active (`chunk_in_progress`) or the ingest pool has in-flight imap tasks (`janitor: heavy maintenance deferred reason=chunk_in_progress` / `ingest_in_flight`). **First ingest** may begin after snapshot wait + **`sync_timedb: startup ingest gate cleared; ingest may begin (heavy maintenance may still run on janitor thread)`** — gate clear is **not** heavy finished; janitor logs **`janitor: heavy maintenance begin|finished`** separately. Chunk‑0 imap should not overlap the post-publish remaining-map / candidate-report filesystem work when the startup heavy pass is still running (heavy defers while chunk/imap active).

**Ingest “complete” vs checkpoint-complete vs day-close:** A log line **`ingest file path=… outcome=ingested|db_skip … ingest_ok=yes`** (especially **`outcome=db_skip db_skip=head_tail`**) does **not** imply checkpoint-complete when tar append is still pending — those paths checkpoint only after **archive finalize**. **`sync_timedb: checkpoint day-close progress oldest_days=…`** (when **`sync_day_close_candidate_report=yes`**) logs the three oldest calendar days with **`unprocessed=`** and **`checkpoint_complete=`** after each chunk; **`checkpoint deferred archive finalize count=N`** counts chunk paths waiting on archive before checkpoint. Immediate async **`DAY_CLOSE`** submit scans **all** checkpoint-complete days oldest-first via **`days_ingest_complete_by_checkpoint`** (not only calendar days touched in the current chunk). Candidate reports reuse the janitor **accrual snapshot** for **`build_unprocessed_raw_by_daily_tar`** when present. Restart with **`phase=done`** startup manifest still **boot-reconciles** when async day-close work is incomplete or checkpoint-complete days remain unsubmitted.

Chunk handlers call **`hard_exit_pool_worker_error`** (`os._exit`) immediately after bounded pool terminate — not after `archive_pool` context-manager join. **`Pool workers terminated context=…`** and optional **`Pool terminate SIGKILL`** explain ingest pool teardown.

**Cold sealed day vs exit 124:** DB-complete catch-up on one calendar day can block many ingest workers on Redis L2 populate/wait while workers remain alive—this is **not** a dead pool, but imap sees zero completions until populate finishes. Mitigations (code): before each ingest chunk the supervisor **prewarms** Redis member maps once per unique calendar day (`Prewarming archive members Redis for day=…`); **`complete=1` with an empty HASH is not treated as warm** — prewarm re-populates; **DAY_CLOSE seal** invalidates Redis and triggers **supervisor re-prewarm** when the sealed day overlaps an active chunk; while **`in_flight_day_hint`** matches a day with an active populate **lock** (`complete != 1`), the stall counter **defers** even when progress timestamps are stale (`WARN: pool imap stall deferred: Redis populate active for day=…`); ingest workers **suspend per-file `SIGALRM` during Redis populate wait** (`suspend_ingest_sigalrm_for_populate_wait`) and extend the monotonic ingest deadline by wait wall time — populate wait is bounded by **`sync_archive_members_redis_populate_stall_seconds`** / **`sync_archive_members_redis_populate_max_seconds`**, not `sync_ingest_per_file_timeout_s`. Non-wait ingest work (parse, duplicate scan, DB writes) still honors the per-file budget. Raising only `sync_pool_stall_abort_after_timeouts` without prewarm prolongs hangs without fixing the root cause.

**sync_timedb_archive backfill (sealed-day pool tasks):** `sync_timedb_archive.py` dispatches **one pool task per sealed daily archive** (`.tar.zst` / `.tar.gz`), not per raw stats file. Per-file **`ingest file path=`** lines from the main supervisor therefore do **not** reset the archive backfill stall counter — only whole sealed-day task completion does. The archive tool uses the same stall guard stack as `sync_timedb`: **Redis chunk prewarm** (`INFO: archive chunk prewarm days=…`), **sealed-day dynamic abort** (`stall_abort_polls_for_sealed_archives`, logged as `sealed_archive_stall_budget_s` / `dynamic_stall_abort_after`), **worker-progress defer** (`defer_reason=worker_progress_active` while worker registry stages are fresh), plus Redis populate and long-ingest-budget defer. Context string remains **`sync_timedb_archive pool`** for log grep. If stall ERROR persists with **`worker_stages`** showing stale ages on all workers, inspect DB locks, Redis populate, or a stuck member parse — not merely raise the INI ceiling.

**Troubleshooting — idle-pool ghost in-flight (multi-hour hang at fixed `remaining`):** Symptom: **`remaining` stuck** (e.g. **102**), no **`loading time`**, ingest workers idle (`futex_wait`/`pipe_read`), main supervisor in **`imap_sliding_window_watch_pool`** → **`time.sleep(poll_timeout_s)`**. Workers show **`pool.worker` → `queues.get()`** (idle, not parsing). **Not** the same as giant-tail supplement (`supplement=yes` fast completions while one giant parses). **Mitigation (code):** reconcile-first — **`INFO: pool imap idle reconcile redispatch round=…`** collects via **`get(timeout=0)`** or redispatches. When a reconcile round redispatches **all** pending paths (`redispatched_n == pending_async_n`) and workers stay idle, treat as **full-redispatch thrash** and run **one** **`INFO: pool imap idle reconcile pool_recover`** — dedupe pending, skip idempotent DB-complete paths, **abandon-pool** teardown (**`terminate outcome=abandoned`** — SIGKILL known PIDs, **no** stdlib `Pool.terminate()`), recreate Pool, **`probe_ingest_pool_dispatch`**, re-`apply_async` unique remainder. Recover is wall-bounded (**~30s**); timeout → exit **124** **`idle_pool_taskqueue_dead`** (never soft hang after **`workers_before=`**). Proactive post-retire swap must abandon the old pool so **`ingest_workers` never exceeds process_cap**. Supervisor retire when **`maxtasks=0`** is **failure/RSS only** (no cooperative giant recycle). Sliding-window dispatch suppresses duplicate normpaths (**`WARN: pool imap duplicate dispatch suppressed`**). Exit **124** also after reconcile rounds exhausted **and** recover absent/failed. Distinguish from exit **137** recycle gate (**`likely_cause=recycle_stuck`**). **Operator capture (before restart):** save main + worker py-spy to `/tmp`; grep **`pool_recover`** / **`outcome=abandoned`** / **`pool_recover done`** / **`idle_pool_taskqueue_dead`**. Then restart pipeline if recover does not resume.

**Troubleshooting — no prewarm log + sealed-only day + stall:** If logs show **`ERROR: Pool imap stalled`** with **`in_flight_day_hint`** on a sealed-only day but **no** `Prewarming archive members Redis for day=…`, check (1) Redis keys for that day (`lock`, `complete`, `hlen` in stall WARN snapshot), (2) whether DAY_CLOSE **sealed after chunk prewarm** (invalidate without re-prewarm race), (3) silent skip reasons (`Skipping archive members prewarm day=… reason=redis_warm` when HASH was empty-but-complete). After stall, the process must **`os._exit(124)`** immediately — if `[main]` lingers for hours with defunct children, upgrade to a build with **`_handle_pool_worker_exit_fatal`** and **`SIGKILL`** after pool terminate timeout.

**Exit 137 vs 124:** Exit **137** (`MultiprocessingWorkerExitError`) means a pool worker was **no longer alive** when the supervisor polled finalize/`get()`—it is **not** proof of kernel or Docker OOM (see diagnostics log and cgroup checklist above). Benign **`maxtasksperchild`** recycle (worker exitcode **0**, replacements keeping pace) must **not** fatal exit **137**—only **`recycle_stuck`** (all recycle-shaped exits, zero alive replacements), **SIGKILL**, or worker exceptions. A prior ingest stall may terminate pools and, without teardown guards, a forced archive finalize in `finally` could mask the intended **124** with **137**; current code skips finalize when `pool_worker_exit` is set after stall/worker death and uses **`shutdown(wait=False)`** on startup prefights/async day-close when exiting after pool fatal.

**Startup snapshot (preflights):** Raw-removal, day-close, and tar-seal prefights call **`wait_for_snapshot(allow_build=False)`** only—they wait on the janitor publish and never trigger a second full-tree `build_archive_maintenance_snapshot`. Supervisor **`_rescan_pending_with_progress`** alone may fallback-build after **`sync_startup_snapshot_wait_seconds`**.

**DB-complete ingest + sealed-only days:** When `archive_keep_uncompressed_tar=no`, DB-complete files (`outcome=db_skip`) call `raw_stats_path_needs_tar_append`, which must not stream the full `.tar.zst` once per file **across the ingest pool**. **Populate-pool** workers (`sync_timedb:worker:populate-pool`) run sealed/tar streams; **ingest-pool** and **archive-pool** workers enqueue populate work and wait on Redis. **Populate wait suspends per-file `SIGALRM` and extends the monotonic ingest deadline** — wait duration is bounded by populate stall/max limits, not `sync_ingest_per_file_timeout_s`. **When Redis L2 is fully warm (`complete=1`, non-empty HASH), duplicate-check uses a single Redis `HGET` per member lookup — not `HGETALL` per worker L1 miss.** When both sibling `.tar` and sealed `.tar.zst` exist and Redis L2 is warm, duplicate-check must not N× parallel `get_existing_archive_members(tar)` under `file_read_lock`. Per-process L1 plus **Redis L2** coordinate **single-flight populate** per calendar day. The populate winner renews lock/progress on each HSET batch **and** on a derived heartbeat interval `max(5, min(30, populate_stall_seconds // 4))` seconds (for example **30s** when stall is **120s**) so slow sealed scans do not false-stall while Redis is healthy. Populate locks record `{token}:{pid}`; waiters release stale locks when the owner PID is dead so another worker can retry without a full supervisor restart. Stall/timeouts raise `ArchiveMembersPopulateStalledError` (Redis may still be reachable); connection failures raise `ArchiveMembersRedisConnectionError`. When populate cannot read a sealed archive, the winner classifies failure once (`zstd -t` vs tar stream), sets Redis **`archive_day_ingest_skip`**, and all workers skip tar-append duplicate-check for that day (**no** parallel per-worker point lookups). Ingest does **not** run local sealed scans when Redis L2 is enabled (see **`sync-timedb-ingest-pool-io-coordination.mdc`**). **`populate_degraded` without day skip** raises `ArchiveMembersRedisUnavailableError` (no local zstd fallthrough). Local sealed scan applies only when Redis is disabled. There is **no zstd decompress timeout** on the ingest lookup path; exit **124** is pool-level (zero task completions), not mid-stream zstd abort. When Redis L2 is enabled, `sync_timedb` **hard-fails** startup and **mid-ingest duplicate-check** on Redis connection loss or populate stall (`sys.exit(1)` — supervisord restart). Ingest member scans use **plain zstd** (no `nice`/`ionice`); janitor seal/decompress keeps archive priority wrappers.

| Knob | Default | Effect |
|------|---------|--------|
| `sync_pool_poll_timeout_s` | 5 | Poll interval between imap progress checks |
| `sync_pool_stall_defer_log_interval_s` | 60 | Min wall seconds between repeated `WARN: pool imap stall deferred` lines (`0` = every poll) |
| `sync_pool_stall_abort_after_timeouts` | 17320 | **Maximum** consecutive timeouts before exit **124** (ceiling; per-batch abort is dynamic from largest file, clamped to this × `sync_pool_poll_timeout_s` ≈ **86600s** wall) |
| `sync_pool_worker_recycle_grace_polls` | 2 | Deprecated poll counter (superseded by grace_seconds) |
| `sync_pool_worker_recycle_grace_seconds` | 60 | Wall-clock seconds before WARN on slow per-PID maxtasksperchild replacement |
| `sync_pool_idle_reconcile_max_rounds` | 3 | Redispatch rounds for idle-pool ghost `pending_async` before exit **124** last resort |
| `sync_pool_idle_reconcile_polls_per_round` | 4 | Idle polls between reconcile redispatch attempts |
| `sync_ingest_per_file_timeout_s` | 900 | Floor seconds for size-proportional per-file ingest budget (`0` disables) |
| `sync_ingest_per_file_timeout_s_per_mib` | (86400−900)/30720 | Added seconds per ceiling MiB (default maps **30 GiB → max**) |
| `sync_ingest_per_file_timeout_max_s` | 86400 | Ceiling seconds (**24h**) for any file; no hard file-size reject |
| `sync_ingest_giant_pool_supplement_enabled` | yes | Backfill idle pool slots from pending tail while **≥ 2 GiB** giants run |
| `sync_ingest_queue_max_size` | 3000 | No-supplement in-memory pending/process queue |
| `sync_ingest_giant_pool_supplement_queue_multiplier` | 2 | Supplement reservoir = queue × multiplier (default **6000**) at batch start **and** mid-imap refresh |
| `sync_ingest_giant_pool_supplement_max_bytes` | 1073741824 | Soft max for preferred supplement pass (**1 GiB** exclusive) |
| `sync_ingest_giant_pool_supplement_large_max_bytes` | 8589934592 | Hard max for second pass (**8 GiB** exclusive; ≥ stays chunk-only) |
| `sync_ingest_giant_pool_supplement_trigger_budget_s` | 6600 | Min resolved per-file budget for in-flight path to count as giant (**2 GiB** at default slope) |
| `sync_ingest_stream_duplicate_scan_bytes` | 8388608 | Route duplicate scan through streaming path above this size (even when below `sync_ingest_max_file_read_bytes`) |
| `sync_bulk_create_batch_size` | 10000 | Incremental parse flush threshold (combined ingest) and `host_data`/`proc_data` `bulk_create` batch size |
| `sync_ingest_db_complete_tail_window_lines` | 500 | Tail timestamp lines probed before full duplicate scan on large head-present files |
| `sync_archive_members_cache_enabled` | yes | Per-process L1 cache on ingest duplicate-check path |
| `sync_archive_members_cache_max_entries` | 64 | Max cached days per ingest/archive worker process |
| `sync_archive_members_redis_enabled` | yes | Cross-worker Redis HASH + single-flight populate (ingest + bulk/janitor) |
| `sync_archive_members_redis_ttl_seconds` | 86400 | TTL for Redis member HASH / complete keys |
| `sync_archive_members_redis_populate_lock_seconds` | 3600 | Populate lock lease; renewed on HSET batches and populate heartbeat |
| `sync_archive_members_redis_populate_stall_seconds` | 120 | Waiter abort when populate shows no lock renewal, HASH growth, or heartbeat |
| `sync_archive_members_redis_populate_max_seconds` | 7200 | Optional absolute populate/wait cap (`0` = off) |
| `sync_archive_members_redis_wait_poll_seconds` | 0.25 | Waiter poll for incremental `HGET` + `complete` |
| `sync_archive_members_redis_hset_batch_size` | 500 | Winner `HSET` pipeline batch size during scan |
| `sync_archive_members_redis_max_payload_bytes` | 8388608 | Refuse oversized HASH populate |
| `sync_archive_members_populate_pool_processes` | 4 | Dedicated `populate-pool` workers for sealed/tar Redis L2 streaming (ingest/archive pools enqueue + wait only) |

**Post-deploy populate-pool verification (`pipeline` logs):**

```bash
cd HPCPerfStats
docker compose -p hpcperfstats logs pipeline 2>&1 | \
  grep -E 'populate-pool|chunk prewarm|sealed archive member stream failed|ingest per-file timeout' | tail -40
```

Expect `sync_timedb:worker:populate-pool` with **`populate_source=tar`** when sibling `.tar` exists (active or closed days), or **`populate_source=sealed`** only when tar was dropped; **no** `ingest per-file timeout` from `ingest-pool` workers while Redis populate lock is held and progressing (populate wait suspends per-file SIGALRM; stall/max limits bound wait duration).

Duplicate file members detected during populate set a Redis **`dedupe_hint`**; the archive janitor enqueues **`DAY_CLOSE`** (inline `.tar` dedupe or sealed-only `dedupe_sealed_daily_archive` last resort).

For large DB sites with slow duplicate-detection or bulk writes, rely on **size-proportional** per-file budgets (`sync_ingest_per_file_timeout_s` floor + `sync_ingest_per_file_timeout_s_per_mib`, capped by `sync_ingest_per_file_timeout_max_s`) so one multi‑GiB straggler does not hit the flat floor and retry forever. Raise the **`sync_pool_stall_abort_after_timeouts` ceiling** together with **`sync_ingest_per_file_timeout_max_s`** when increasing the per-file max. Dynamic abort follows the largest file in the **current in-flight sliding window**; the ceiling only caps multi‑GiB stragglers. Catch-up mitigations for sealed-only archives: **`archive_keep_uncompressed_tar=yes`**, tune **`sync_ingest_pool_processes`**, and rely on Redis **single-flight populate** warming the day map (not N parallel `zstd` per worker). Ingest-time DLO quarantine for permanently corrupt raw is separate from exit **124**.

**Unmapped closed raw:** when ingest backlog prevents full accrual snapshots, the supervisor unions a cached unmapped-closed-raw scan into janitor disqualification so `.tar` drop cannot proceed while unparseable closed raw remains on disk.

## Related files

- [`hpcperfstats/dbload/lib/dbload/lib/conf_parser.py`](../hpcperfstats/dbload/lib/conf_parser.py) — `get_effective_cores()`, caps, NUMA compose flags
- [`hpcperfstats/compose_cpu_layout.py`](../hpcperfstats/compose_cpu_layout.py) — linear responsive `cpuset` partition
- [`hpcperfstats/numa_topology.py`](../hpcperfstats/numa_topology.py) — sysfs parse and node-pair selection
- [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) — writes CPU pinning fragments
- [`services-conf/django_startup.sh`](../services-conf/django_startup.sh) — Gunicorn worker count
- [`hpcperfstats/site/hpcperfstats_site/settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py) — `CONN_MAX_AGE`, PostgreSQL `OPTIONS`
- [`docker-compose.yaml`](../docker-compose.yaml) — Postgres `max_connections`, `statement_timeout`, `idle_in_transaction_session_timeout`
