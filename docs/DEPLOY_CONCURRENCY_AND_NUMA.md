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

**Ini reference:** optional tuning keys and defaults are documented in **`hpcperfstats.ini.example`** (one comment per key). The canonical list of wired options is **`INI_OPTION_REGISTRY`** in **`hpcperfstats/conf_parser.py`**; drift tests in **`hpcperfstats/tests/test_hpcperfstats_ini_example.py`** keep the example aligned.

## Archive zstd priority (unpinned hosts)

Daily monitor archives are sealed inside the **`pipeline`** container. On hosts **without** Docker CPU pinning, **`web`**, **`db`**, and **`pipeline`** share the same CPU scheduler and often the same physical disk (`postgres_data` vs `/opt/hpcperfstats_data/`).

| `[PIPELINE]` key | Default | Role |
|----------------|---------|------|
| **`archive_zstd_threads`** | **`0`** | **`0`** → zstd **`-T0`** (all logical cores per zstd process); **`N>0`** → explicit **`-TN`** override |
| **`archive_seal_parallel_workers`** | **`4`** | Max concurrent daily tar seals during maintenance |
| **`archive_zstd_nice`** | **`10`** | CPU deprioritization for zstd children (`0` disables) |
| **`archive_zstd_ionice_class`** / **`archive_zstd_ionice_level`** | **`2`** / **`6`** | Best-effort I/O class; higher level yields disk to Postgres |
| **`archive_zstd_level`** | **`7`** | Compression level; benchmark before raising above **9** |

**Tuning:** If seals are too slow, raise **`archive_janitor_days_per_tick`** or **`archive_janitor_budget_seconds`** slightly (still bounded per tick). If web/API or Postgres latency spikes during maintenance, lower janitor budget first, then raise **`archive_zstd_nice`** or **`archive_zstd_ionice_level`**. Override env **`SYNC_ARCHIVE_SEAL_WORKERS`** mirrors validation fanout.

## Archive janitor (continuous ingest)

Ingest is treated as always-on: the supervisor no longer runs blocking seal/raw/`.tar` maintenance bursts. Cold-path archive work accrues as **day debt** and is consumed in background **micro-batches** (`ArchiveJanitor` thread).

| `[PIPELINE]` key | Default | Role |
|----------------|---------|------|
| **`archive_maintenance_interval_seconds`** | **`28800`** | **Deprecated** (ignored); maintenance pass runs at startup, every N ingest chunks, and on ingest queue drain |
| **`archive_janitor_budget_seconds`** | **`30`** | Max wall time per janitor tick |
| **`archive_janitor_days_per_tick`** | **`2`** | Max **distinct calendar days** (`DAY_CLOSE` pipeline) per tick—not raw heap entry count |
| **`archive_keep_uncompressed_tar`** | **`no`** | Drop prior-day `.tar` at seal when raw is gone; global `yes` retains until tar-drop |
| **`archive_today_uncompressed_tar_grace_hours`** | **`8`** | Keep calendar-today `.tar` after local midnight (hours) when global keep is `no` |
| **`archive_janitor_debt_high_watermark`** | **`50`** | Debt depth before temporary burst scaling |
| **`archive_janitor_debt_burst_factor`** | **`1.5`** | Budget/days multiplier under high debt |
| **`archive_janitor_debt_max_entries`** | **`200`** | In-memory debt cap; lowest-priority entries evicted with a warning when full |
| **`archive_janitor_raw_paths_per_tick`** | **`1000`** | Incremental raw deletes per `RAW_REMOVE` debt item (large-day safety) |
| **`archive_maintenance_idle_seconds`** | **`300`** | Optional idle-only 2× budget bonus (not required for progress) |
| **`sync_archive_max_inflight_jobs`** | **`2`** | Concurrent disjoint daily-tar append jobs |
| **`sync_archive_worker_stall_seconds`** | **`600`** | Log stalled append workers (observability) |
| **`sync_enable_ingest_first_durability_mode`** | **`yes`** | Checkpoint after DB even when append is deferred |

Progress and resume state persist in **`.sync_archive_maint_hints.json`** (version **2**: `debt_queue`, `day_phases`, `validated_days`). Under ingest backlog, **full** accrual waits for an empty ingest queue; **partial** prior-day accrual remains on scheduled passes. **Per-batch calendar-day drain:** after each ingest chunk (and on `archive_finalize` / idle finalize), the supervisor enqueues **`DAY_CLOSE`** for prior days whose ingest+tar-append is quiescent and whose oldest-first stream has moved on — within **≤1 batch** of quiescence, without waiting for every-N-chunk maintenance. Scheduled startup / every **`rescan_every_chunks`** / ingest-queue-drain passes remain the snapshot backstop. Legacy triple `SEAL`/`RAW`/`TAR` hints coalesce to `DAY_CLOSE` on load. Janitor **chains ticks** while processable debt remains; budget/exception paths re-queue unprocessed debt (no silent loss).

**Multi-week backlog / disk pressure:** raise **`archive_janitor_days_per_tick`** and **`archive_janitor_budget_seconds`** modestly; keep **`archive_janitor_raw_paths_per_tick`** high enough for your daily file count (e.g. 15k/day may need several ticks per day). Default **`archive_keep_uncompressed_tar=no`** drops prior-day `.tar` at seal once raw is gone; calendar-today keeps `.tar` for **`archive_today_uncompressed_tar_grace_hours`** (default 8h after local midnight). Set global **`archive_keep_uncompressed_tar=yes`** during heavy same-day append if you need the uncompressed `.tar` for late raw appends.

**Hard isolation (recommended long-term):** run [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) so **`pipeline`** gets a dedicated cpuset; you can then run zstd at lower nice/ionice and/or raise janitor budget with less impact on **`web`**/**`db`**.

## OOM and `sync_timedb` process tree

Kernel OOM may kill an ingest pool worker (`[worker:ingest-pool]`) with a **transient multi‑GiB anon spike** while parsing giant raw segments (`readlines()` on multi‑GiB–28 GiB files). **`sync_supervisor_rss_limit_mb`** checks **supervisor `/proc/self` only** — it does not see worker RSS. Spawn pool workers used to survive as orphans when the supervisor died first; **`PR_SET_PDEATHSIG`** addresses that case.

| Mitigation | Role |
|------------|------|
| **`PR_SET_PDEATHSIG` (SIGKILL)** on pool workers | Workers exit when the supervisor dies so **supervisord** can restart a clean tree |
| **`pipeline` `mem_limit` / `memswap_limit`** (Compose) | Cgroup cap before host global OOM on swapless hosts; **128 GiB** is a reasonable default on **192 GiB** RAM (see `docker-compose.app.yaml.example`) |
| **`sync_ingest_max_file_read_bytes`** (default **512 MiB**) | Stream-parse larger segments instead of **`readlines()`** |
| **`sync_ingest_imap_inflight_cap`** + **`sync_pool_process_cap`** | Limit concurrent giant-file parses until streaming is deployed |
| **`sync_ingest_pool_maxtasksperchild`** (default **50**) | Recycle worker pandas/Django heap between files |
| **`sync_process_tree_rss_limit_mb`** / **`sync_process_tree_rss_exit_mb`** | Defer chunks or exit **137** on **supervisor + pool worker** RSS |
| **`sync_supervisor_rss_limit_mb`** | Supervisor-only fail-fast (legacy; insufficient alone for worker spikes) |
| **`abort_if_pool_workers_dead`** | Parent exits **137** when a worker is OOM-killed first (fail-fast vs hang) |

**Catch-up INI starting points** (until backlog of multi‑GiB segments clears): `sync_pool_process_cap=12–16`, `sync_ingest_chunk_size=8–16`. On very large trees (~100k+ closed paths), optional interim disable until fixes ship: `sync_startup_tar_seal_preflight=no` / `sync_startup_day_close_preflight=no` (janitor **`DAY_CLOSE`** debt remains). After startup-scan + janitor budget fixes, prefer leaving prefights **on** and tune `sync_startup_snapshot_wait_seconds` (default **300**), `sync_startup_tar_seal_rediscover_interval_seconds` (default **600**), `archive_janitor_days_per_tick`, `sync_day_close_async_workers`.

**Post-fix disk-release progress grep:** `janitor: day_close scheduled reason=startup`, `janitor: async day_close submit`, `Archive janitor tick done` (expect `debt_popped>0`, `async_submitted>0` or `days_completed>0` after maintenance), `sync_timedb: startup archive scan ready`, `sync_timedb: pending rescan done`.

**Forensics checklist:** inside the pipeline container read `memory.max`, `memory.current`, `memory.peak`, `memory.events` (`oom_kill`); `ps -eo pid,rss:10,cmd` for `[worker:ingest-pool]` vs `[main]`; `find` largest raw stats paths. Log a short packet under **`test_runs/`** when investigating.

Tune tree RSS limits to ~**70–80%** of the pipeline cgroup cap; grep kernel logs for `oom-kill` + `sync_timedb.py` and correlate with `pending_stats` / janitor debt in application logs.

## Archive recovery (operators)

Raw stats on disk remain the **source of truth** until validated archive membership, DB head-ingest gate (when enabled), and janitor raw removal all succeed.

| Symptom | Safe recovery |
|---------|----------------|
| **Bad `.tar.zst`, good legacy `.tar.gz`** | Remove or rename the corrupt `.tar.zst`; restore sibling `.tar` from gzip (`decompress_compressed_to_tar` / `zstd -d --format=gzip`). Raw stats stay until validation passes. |
| **Sealed `.tar.zst` unreadable for ingest lookup** | Check `zstd -t /path/YYYY-MM-DD.tar.zst`. If **passes** but tar stream fails (`unexpected end of data`), the tar **payload** is truncated — restore/re-seal from sibling `.tar` or legacy `.tar.gz`. If **`zstd -t` fails**, the zstd frame is corrupt. In both cases ingest logs **`ERROR: daily sealed archive unusable for ingest lookup`** once per day (`reason=zst_frame_invalid` or `tar_truncated_or_unreadable`), sets Redis **`archive_day_ingest_skip`**, and skips tar-append duplicate-check for that day until the archive is repaired or the Redis key TTL expires. DB-complete ingest continues (`need_archival=False` for that day). |
| **Missing `.tar` after mistaken drop** | Decompress sealed `.tar.zst` (or legacy `.tar.gz`) to sibling `.tar` before append; never delete raw while tar/zst validation fails closed. |
| **Stuck janitor debt after crash** | Inspect **`.sync_archive_maint_hints.json`** v2 (`debt_queue`, `day_phases`); restart **`sync_timedb`** (startup mtime scan re-enqueues seal debt) or wait for interval accrual when the ingest queue is idle. |
| **Corrupt daily `.tar` before append** | `replace_corrupt_tar_from_compressed_backup` tries zst then gz; append stays fail-closed (`False`) if restore fails—raw files remain. |
| **`ingest_first_archive_abandoned_raw` in logs** | With **`sync_enable_ingest_first_durability_mode=yes`**, exhausted append retries checkpoint paths as processed while raw may never reach tar; janitor/partial accrual must eventually enqueue raw debt—grep logs for this marker and verify raw still on disk until cold path succeeds. |
| **Head ingested, tail missing (M2)** | DB head-ingest gate does not prove full-file ingest; truncated raw with matching archive size could pass validation—treat unexpected raw deletion as incident-driven review, not routine ops. |
| **Unparsable closed raw blocking a day** | Inspect **`{archive_data_dir}/.sync_timedb_unparsable_raw.json`** and **`{archive_data_dir}/.sync_timedb_unparsable_raw/`**; ingest moves permanently unparseable closed raw off the hot tree when parse fails (look for `error: process data failed` / `Possibly corrupt file` in `sync_timedb` logs). Restore only after fixing content or deliberate operator review—do not delete manifest entries casually. Parsable-but-unmapped raw still blocks until ingest/archive catches up. |
| **Bulk reclaim of sealed archived raw after restart** | On **`sync_timedb`** startup (default **`sync_startup_raw_removal_preflight=yes`**), a background pass verifies closed raw against **existing sealed** daily archives and writes **`{archive_data_dir}/.sync_timedb_startup_raw_removal.json`**. Verification runs in parallel with ingest; deletes run only after all days are checked, briefly pausing new ingest chunks. After deletes, the supervisor rescans pending files so removed paths do not linger in the ingest queue. Disable with **`sync_startup_raw_removal_preflight=no`**; tune slice budget with **`sync_startup_raw_removal_verify_budget_seconds`** / **`sync_startup_raw_removal_verify_days_per_slice`**. The archive janitor remains the ongoing backstop. |
| **Canonical startup archive scan** | One **`build_archive_maintenance_snapshot`** per boot: janitor calls **`begin_build()`** → build → **`publish(from_janitor=True)`** → **`invalidate_unmapped_disqualify_cache()`**; prefights **`wait_for_snapshot()`** (never `None`) with **`note_startup_maintenance_pending()`** on supervisor startup pass. Deep-copied coordinator snapshot retains **`closed_paths`** after accrual trim for unmapped disqualify. Wait up to **`sync_startup_snapshot_wait_seconds`** (default **300**, min **120**). Log: **`sync_timedb: startup archive scan ready paths=N`**. |
| **Quiescent daily `.tar` seal at startup** | Separate from raw removal and janitor **`DAY_CLOSE`**: when a daily **`.tar`** exists with **no closed raw on disk** for that calendar day, **`StartupTarSealPreflight`** (default **`sync_startup_tar_seal_preflight=yes`**) seals to **`.tar.zst`** and drops the uncompressed **`.tar`** on thread `startup-tar-seal-preflight`, **in parallel with ingest** (no ingest gate). Manifest: **`{archive_data_dir}/.sync_timedb_startup_tar_seal.json`**. **`phase=done`** means tar-seal has no actionable work (blocked dirty tars remain for **`DAY_CLOSE`**). Tunables: **`sync_startup_tar_seal_budget_seconds`** (default **300**), **`sync_startup_tar_seal_days_per_slice`** (default **1**), **`sync_startup_tar_seal_rediscover_interval_seconds`** (default **600**). |
| **Checkpoint-complete async `DAY_CLOSE` at startup** | When all **mapped** closed raw for a calendar day is in **`.sync_timedb_state.json`** but large **`.tar`** / raw remain on disk, **`StartupDayClosePreflight`** (default **`sync_startup_day_close_preflight=yes`**) submits **`AsyncDayCloseCoordinator`** work on thread `startup-day-close-preflight` — **not blocked** by unrelated ingest backlog. Submit budget applies **after** snapshot-derived scans. Manifest: **`{archive_data_dir}/.sync_timedb_startup_day_close.json`**. Tunables: **`sync_startup_day_close_budget_seconds`** (default **300**), **`sync_startup_day_close_days_per_slice`** (default **1**), optional **`sync_startup_day_close_scan_budget_seconds`** (warn-only), **`sync_day_close_async_workers`** (default **1**), **`sync_day_close_max_inflight`** (default = async workers), **`sync_startup_day_close_backoff_seconds`** (default **30** when async pool saturated — skips expensive rescans). Candidate log: **`sync_day_close_candidate_report=yes`** (`queued` / `waiting_on_ingest` / `eligible_deferred` / `disqualified`; queued `async_in_progress` lines include **`async_last_progress`** / **`async_age_s`**). |
| **Async DAY_CLOSE raw-removal stall** | After seal, the async worker waits up to **`sync_day_close_raw_removal_wait_seconds`** (default **3600**; **`0`** = unbounded debug only) for **`DayRawRemovalCoordinator`** delete completion. Progress logs every **60s**; timeout → manifest **`deferred`** / **`raw_removal_timeout`** and worker release so queued days can run. Pipeline verify/delete early exits log **`Day raw removal verify budget exhausted`** / **`Day raw removal delete incomplete`**. Restart recovery: **`sync_day_close_async_stale_seconds`** (default **7200**) marks stale **`submitted`/`sealing`/`raw_removal`** manifest entries **`deferred`** when no live future. Grep: **`janitor: async day_close raw_removal stall`**, **`stale manifest recovery`**. |

## PostgreSQL connection budget (operator)

Rough peak connections:

`web_workers + metrics_pool_processes + sync_timedb_processes + overhead`

Compose sets **`max_connections=500`** with **reduced `work_mem` / parallel gather / maintenance buffers** to limit RAM spikes while keeping slot headroom. The stack does **not** use an external pooler (no PgBouncer): sizing is direct Django → Postgres. Still use **`WEB_CONCURRENCY`**, **`metrics_pool_process_cap`**, **`sync_pool_process_cap`**, and **`parallel_db_prefetch_max`** so concurrent heavy queries stay bounded.

## Connection lifetime, query timeouts, and staggered pipeline

- **`CONN_MAX_AGE`:** Default **90** seconds (`[PORTAL] db_conn_max_age` or **`DJANGO_CONN_MAX_AGE`**). Lowers how long idle Gunicorn workers hold a backend. Does not cap peak concurrency under full load; pairs with the caps above.
- **`statement_timeout` / `idle_in_transaction_session_timeout`:** Defaults **120000 ms** and **300000 ms** for PostgreSQL sessions via Django **`OPTIONS`** (`conf_parser.build_postgres_connection_options()`). The **`db`** service in **`docker-compose.yaml`** sets the same server parameters so non-Django clients inherit them. Disable per-session timeouts by setting **`DJANGO_DB_STATEMENT_TIMEOUT_MS=0`** and **`DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=0`** (and adjust compose if you remove server defaults). Tune upward only if legitimate bulk jobs hit the limit.
- **Staggered supervisord jobs:** [`services-conf/supervisord.conf.example`](../services-conf/supervisord.conf.example) starts **`listend`** first (higher priority), then **`sync_timedb`** after **20s**, then **`update_metrics`** after **90s**, so restarts do not open every DB pool at the same instant. Adjust sleeps and **`priority`** for your site.

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
- `sync_db_writer_pool_multiplier`
- `sync_db_writer_pool_cap`
- `sync_adaptive_dispatch_enabled`
- `sync_dispatch_burst_factor`
- `sync_dispatch_archive_backoff_ratio`
- `sync_dispatch_step_size`

Default alignment note:
- `conf_parser` now exposes `get_conf_parser_defaults_audit_snapshot()` to provide a categorized default/fallback accounting for platform constraints, sync throughput, overlap contention, and stability guardrails.

### Metrics window-coverage readiness (summary plots)

By default **`update_metrics`** defers each job until in-window `host_data` samples exist within configurable margins of Slurm **`start_time`** and **`end_time`** (job aggregate: any accounting host may satisfy each edge). Defaults: **`metrics_readiness_require_window_coverage = yes`**, **`metrics_readiness_start_margin_seconds = 600`**, **`metrics_readiness_end_margin_seconds = 600`**.

Jobs with long monitor prolog gaps (telemetry begins hours after Slurm start) stay deferred until early-window data exists or an operator disables the gate. Set **`metrics_readiness_require_window_coverage = no`** in **`[PIPELINE]`** only for recovery/backfill emergencies. When metrics run, **`telemetry_first_time`** / **`telemetry_last_time`** on **`job_data`** and plot artifact fingerprints (schema version **10**) incorporate those bounds so backfill invalidates stale summary plots.

**Scoping:** strict readiness and the coverage proxy both use **`job_data.host_list`** hostnames (FQDN) and the Slurm window — not **`host_data.jid`**. Any in-window sample on an accounting host may satisfy a margin (including samples from other jobs on shared nodes during overlapping wall-clock windows). That trade-off fixes tail-only false-ready bugs but is weaker than per-jid isolation.

**Permanent defer / stall watchdog:** jobs whose telemetry never reaches both margins (for example multi-hour prolog gaps with default 600s margins) remain in the deferred-not-ready map with 10s retries, then quarantine intervals — they are not dropped from candidacy while still in range. Large historical backfills of such jobs increase readiness-thread DB load and can keep compute workers idle; disable the gate or reduce margins for emergency backfill, and watch scheduler stall metrics.

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

The background **`ArchiveJanitor`** processes up to **`archive_janitor_days_per_tick`** **distinct calendar days** per tick via **`DAY_CLOSE`** debt (full seal → raw → `.tar` pipeline per day). Tune **`archive_janitor_budget_seconds`**, **`archive_janitor_raw_paths_per_tick`**, and **`archive_janitor_debt_high_watermark`** / **`archive_janitor_debt_burst_factor`** on sites ingesting **~15k raw files/day**.

**Seal vs append:** `atomic_seal_tar_to_zst` holds an exclusive **`file_write_lock`** on the daily `.tar` for the full compress/replace window. Hot-path append uses the same lock (default **60s** timeout). Large-day seals during ingest can surface append **`TimeoutError`** (fail-closed). Mitigations: use **`archive_keep_uncompressed_tar=yes`** (or rely on today's grace window) during heavy same-day append, isolate **`sync_timedb`** archive work on a dedicated cpuset (see above), and rely on janitor disqualification for in-flight days.

**Validation read locks:** parallel raw-remove validation defaults to **`sync_archive_validation_max_workers=2`** (INI `[PIPELINE]`). Raise only when append/read-lock contention is acceptable.

**Pool stall guard (exit 124):** When `imap_unordered_watch_pool` sees **N** consecutive poll timeouts with **no** completed task while all workers are still alive, it raises `MultiprocessingPoolStallError` and `sync_timedb` exits with status **124**. Default wall time is `sync_pool_stall_abort_after_timeouts` × `sync_pool_poll_timeout_s` (**120** × **5s** ≈ **10 minutes**). Logs include an **`ERROR: Pool imap stalled`** line before exit, plus **`WARN: pool imap stall progress`** at 50%/75% of the abort threshold with **`pool_workers_alive`**, **`in_flight_day_hint`**, **`in_flight_sample`**, and a hint when **`sync_ingest_per_file_timeout_s=0`**. After stall, **`Pool workers terminated context=…`** explains why ingest pool children disappear from `ps` while `[main]` may linger briefly.

**Exit 137 vs 124:** Exit **137** (`MultiprocessingWorkerExitError`) means a pool worker was **no longer alive** when the supervisor polled finalize/`get()`—it is **not** proof of kernel OOM. A prior ingest stall may terminate pools and, without teardown guards, a forced archive finalize in `finally` could mask the intended **124** with **137**; current code skips finalize when `pool_worker_exit` is set after stall/worker death and uses **`shutdown(wait=False)`** on startup prefights/async day-close when exiting after pool fatal.

**Startup snapshot (preflights):** Raw-removal, day-close, and tar-seal prefights call **`wait_for_snapshot(allow_build=False)`** only—they wait on the janitor publish and never trigger a second full-tree `build_archive_maintenance_snapshot`. Supervisor **`_rescan_pending_with_progress`** alone may fallback-build after **`sync_startup_snapshot_wait_seconds`**.

**DB-complete ingest + sealed-only days:** When `archive_keep_uncompressed_tar=no`, DB-complete files (`No missing timestamps found`) call `raw_stats_path_needs_tar_append`, which must not stream the full `.tar.zst` once per file **across the ingest pool**. Per-process L1 (`sync_archive_members_cache_enabled`) plus **Redis L2** (`sync_archive_members_redis_enabled`, keyed by archive identity) coordinate **single-flight populate** per calendar day: at most **one** `zstd -d` on a sealed day while Redis is cold; other ingest workers wait on incremental HASH / `complete=1` with stall detection (`populate_stall_seconds`). When populate cannot read a sealed archive, the winner classifies failure once (`zstd -t` vs tar stream), sets Redis **`archive_day_ingest_skip`**, and all workers skip tar-append duplicate-check for that day (**no** parallel per-worker point lookups). Ingest does **not** run local sealed scans when Redis L2 is enabled (see **`sync-timedb-ingest-pool-io-coordination.mdc`**). **`populate_degraded` without day skip** raises `ArchiveMembersRedisUnavailableError` (no local zstd fallthrough). Local sealed scan applies only when Redis is disabled. There is **no zstd decompress timeout** on the ingest lookup path; exit **124** is pool-level (zero task completions), not mid-stream zstd abort. When Redis L2 is enabled, `sync_timedb` **hard-fails** startup and **mid-ingest duplicate-check** if `[CACHE] redis_location` is unreachable (`sys.exit(1)` — supervisord restart). Ingest member scans use **plain zstd** (no `nice`/`ionice`); janitor seal/decompress keeps archive priority wrappers.

| Knob | Default | Effect |
|------|---------|--------|
| `sync_pool_poll_timeout_s` | 5 | Poll interval between imap progress checks |
| `sync_pool_stall_abort_after_timeouts` | 120 | Consecutive timeouts before exit **124** |
| `sync_ingest_per_file_timeout_s` | 900 | Wall-clock cap per ingest worker task (`0` disables); on expiry the file returns `ingest_ok=False` for retry instead of blocking the chunk |
| `sync_archive_members_cache_enabled` | yes | Per-process L1 cache on ingest duplicate-check path |
| `sync_archive_members_cache_max_entries` | 64 | Max cached days per ingest/archive worker process |
| `sync_archive_members_redis_enabled` | yes | Cross-worker Redis HASH + single-flight populate (ingest + bulk/janitor) |
| `sync_archive_members_redis_ttl_seconds` | 86400 | TTL for Redis member HASH / complete keys |
| `sync_archive_members_redis_populate_lock_seconds` | 3600 | Populate lock lease; renewed on each HSET batch during scan |
| `sync_archive_members_redis_populate_stall_seconds` | 120 | Waiter abort only when populate shows no progress |
| `sync_archive_members_redis_populate_max_seconds` | 7200 | Optional absolute populate/wait cap (`0` = off) |
| `sync_archive_members_redis_wait_poll_seconds` | 0.25 | Waiter poll for incremental `HGET` + `complete` |
| `sync_archive_members_redis_hset_batch_size` | 500 | Winner `HSET` pipeline batch size during scan |
| `sync_archive_members_redis_max_payload_bytes` | 8388608 | Refuse oversized HASH populate |

Duplicate file members detected during populate set a Redis **`dedupe_hint`**; the archive janitor enqueues **`DAY_CLOSE`** (inline `.tar` dedupe or sealed-only `dedupe_sealed_daily_archive` last resort).

For large DB sites with slow duplicate-detection or bulk writes, prefer enabling **`sync_ingest_per_file_timeout_s`** (for example **900**) so one straggler path does not hold the whole chunk until the pool stall abort. Raising only `sync_pool_stall_abort_after_timeouts` prolongs hangs without identifying the path. Catch-up mitigations for sealed-only archives: **`archive_keep_uncompressed_tar=yes`**, tune **`sync_ingest_pool_processes`**, and rely on Redis **single-flight populate** warming the day map (not N parallel `zstd` per worker). Ingest-time DLO quarantine for permanently corrupt raw is separate from exit **124**.

**Unmapped closed raw:** when ingest backlog prevents full accrual snapshots, the supervisor unions a cached unmapped-closed-raw scan into janitor disqualification so `.tar` drop cannot proceed while unparseable closed raw remains on disk.

## Related files

- [`hpcperfstats/conf_parser.py`](../hpcperfstats/conf_parser.py) — `get_effective_cores()`, caps, NUMA compose flags
- [`hpcperfstats/compose_cpu_layout.py`](../hpcperfstats/compose_cpu_layout.py) — linear responsive `cpuset` partition
- [`hpcperfstats/numa_topology.py`](../hpcperfstats/numa_topology.py) — sysfs parse and node-pair selection
- [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) — writes CPU pinning fragments
- [`services-conf/django_startup.sh`](../services-conf/django_startup.sh) — Gunicorn worker count
- [`hpcperfstats/site/hpcperfstats_site/settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py) — `CONN_MAX_AGE`, PostgreSQL `OPTIONS`
- [`docker-compose.yaml`](../docker-compose.yaml) — Postgres `max_connections`, `statement_timeout`, `idle_in_transaction_session_timeout`
