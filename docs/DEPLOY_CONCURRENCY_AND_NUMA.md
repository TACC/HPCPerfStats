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
| **`archive_maintenance_interval_seconds`** | **`28800`** | Debt **accrual** scan interval (not blocking maintenance execution) |
| **`archive_janitor_budget_seconds`** | **`30`** | Max wall time per janitor tick |
| **`archive_janitor_days_per_tick`** | **`2`** | Max calendar days processed per tick |
| **`archive_janitor_debt_high_watermark`** | **`50`** | Debt depth before temporary burst scaling |
| **`archive_janitor_debt_burst_factor`** | **`1.5`** | Budget/days multiplier under high debt |
| **`archive_janitor_debt_max_entries`** | **`200`** | In-memory debt cap; lowest-priority entries evicted with a warning when full |
| **`archive_janitor_raw_paths_per_tick`** | **`1000`** | Incremental raw deletes per `RAW_REMOVE` debt item (large-day safety) |
| **`archive_maintenance_idle_seconds`** | **`300`** | Optional idle-only 2× budget bonus (not required for progress) |
| **`sync_archive_max_inflight_jobs`** | **`2`** | Concurrent disjoint daily-tar append jobs |
| **`sync_archive_worker_stall_seconds`** | **`600`** | Log stalled append workers (observability) |
| **`sync_enable_ingest_first_durability_mode`** | **`yes`** | Checkpoint after DB even when append is deferred |

Progress and resume state persist in **`.sync_archive_maint_hints.json`** (version **2**: `debt_queue`, `day_phases`, `validated_days`). Under ingest backlog, **full** accrual waits for an empty ingest queue; **partial** prior-day accrual and **day-complete reclaim** (chunk-end enqueue of seal → raw → `.tar` for completed prior calendar days) keep disk reclaim moving. Janitor ticks continue whenever debt exists; budget/exception paths re-queue unprocessed debt (no silent loss).

**Multi-week backlog / disk pressure:** raise **`archive_janitor_days_per_tick`** and **`archive_janitor_budget_seconds`** modestly; keep **`archive_janitor_raw_paths_per_tick`** high enough for your daily file count (e.g. 15k/day may need several ticks per day). Set **`archive_keep_uncompressed_tar=no`** after verifying sealed `.tar.zst` if disk is tight.

**Hard isolation (recommended long-term):** run [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) so **`pipeline`** gets a dedicated cpuset; you can then run zstd at lower nice/ionice and/or raise janitor budget with less impact on **`web`**/**`db`**.

## OOM and `sync_timedb` process tree

Kernel OOM may kill the **`sync_timedb.py [main]`** supervisor before proactive RSS limits trigger. Spawn pool workers (`[worker:ingest-pool]`, `[worker:archive-pool]`, etc.) used to survive as orphans and leave ingest/archive in an indeterminate state.

| Mitigation | Role |
|------------|------|
| **`PR_SET_PDEATHSIG` (SIGKILL)** on pool workers | Workers exit when the supervisor dies so **supervisord** can restart a clean tree |
| **`sync_supervisor_rss_limit_mb`** / **`sync_supervisor_rss_check_interval_s`** | Exit **137** before kernel OOM when RSS is trending over limit |
| **`abort_if_pool_workers_dead`** | Parent exits **137** when a worker is OOM-killed first (fail-fast vs hang) |

Tune RSS limits for your container memory cap; grep kernel logs for `oom-kill` + `sync_timedb.py` and correlate with `pending_stats` / janitor debt in application logs.

## Archive recovery (operators)

Raw stats on disk remain the **source of truth** until validated archive membership, DB head-ingest gate (when enabled), and janitor raw removal all succeed.

| Symptom | Safe recovery |
|---------|----------------|
| **Bad `.tar.zst`, good legacy `.tar.gz`** | Remove or rename the corrupt `.tar.zst`; restore sibling `.tar` from gzip (`decompress_compressed_to_tar` / `zstd -d --format=gzip`). Raw stats stay until validation passes. |
| **Missing `.tar` after mistaken drop** | Decompress sealed `.tar.zst` (or legacy `.tar.gz`) to sibling `.tar` before append; never delete raw while tar/zst validation fails closed. |
| **Stuck janitor debt after crash** | Inspect **`.sync_archive_maint_hints.json`** v2 (`debt_queue`, `day_phases`); restart **`sync_timedb`** (startup mtime scan re-enqueues seal debt) or wait for interval accrual when the ingest queue is idle. |
| **Corrupt daily `.tar` before append** | `replace_corrupt_tar_from_compressed_backup` tries zst then gz; append stays fail-closed (`False`) if restore fails—raw files remain. |
| **`ingest_first_archive_abandoned_raw` in logs** | With **`sync_enable_ingest_first_durability_mode=yes`**, exhausted append retries checkpoint paths as processed while raw may never reach tar; janitor/partial accrual must eventually enqueue raw debt—grep logs for this marker and verify raw still on disk until cold path succeeds. |
| **Head ingested, tail missing (M2)** | DB head-ingest gate does not prove full-file ingest; truncated raw with matching archive size could pass validation—treat unexpected raw deletion as incident-driven review, not routine ops. |

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

The background **`ArchiveJanitor`** consumes **debt items** per tick (`archive_janitor_days_per_tick`), not calendar days. Day-complete reclaim enqueues up to **three** items per prior day (SEAL, RAW_REMOVE, TAR_DROP). Tune **`archive_janitor_budget_seconds`**, **`archive_janitor_raw_paths_per_tick`**, and **`archive_janitor_debt_high_watermark`** / **`archive_janitor_debt_burst_factor`** on sites ingesting **~15k raw files/day**.

**Seal vs append:** `atomic_seal_tar_to_zst` holds an exclusive **`file_write_lock`** on the daily `.tar` for the full compress/replace window. Hot-path append uses the same lock (default **60s** timeout). Large-day seals during ingest can surface append **`TimeoutError`** (fail-closed). Mitigations: keep **`archive_keep_uncompressed_tar=yes`** during heavy ingest, isolate **`sync_timedb`** archive work on a dedicated cpuset (see above), and rely on janitor disqualification for in-flight days.

**Validation read locks:** parallel raw-remove validation defaults to **`sync_archive_validation_max_workers=2`** (INI `[PIPELINE]`). Raise only when append/read-lock contention is acceptable.

**Pool stall guard:** `sync_pool_stall_abort_after_timeouts` (default **120** poll intervals) aborts `imap_unordered_watch_pool` when a worker is alive but stuck; supervisor may exit and supervisord restarts the tree.

**Unmapped closed raw:** when ingest backlog prevents full accrual snapshots, the supervisor unions a cached unmapped-closed-raw scan into janitor disqualification so `.tar` drop cannot proceed while unparseable closed raw remains on disk.

## Related files

- [`hpcperfstats/conf_parser.py`](../hpcperfstats/conf_parser.py) — `get_effective_cores()`, caps, NUMA compose flags
- [`hpcperfstats/compose_cpu_layout.py`](../hpcperfstats/compose_cpu_layout.py) — linear responsive `cpuset` partition
- [`hpcperfstats/numa_topology.py`](../hpcperfstats/numa_topology.py) — sysfs parse and node-pair selection
- [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) — writes CPU pinning fragments
- [`services-conf/django_startup.sh`](../services-conf/django_startup.sh) — Gunicorn worker count
- [`hpcperfstats/site/hpcperfstats_site/settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py) — `CONN_MAX_AGE`, PostgreSQL `OPTIONS`
- [`docker-compose.yaml`](../docker-compose.yaml) — Postgres `max_connections`, `statement_timeout`, `idle_in_transaction_session_timeout`
