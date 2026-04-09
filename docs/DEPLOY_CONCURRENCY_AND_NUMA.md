# Deployment: concurrency, PostgreSQL, and NUMA pinning

This document summarizes how **thread/process counts** and **Docker Compose CPU sets** relate to **PostgreSQL `max_connections`** and large hosts (including multi-NUMA systems).

## Services and parallelism (compose)

| Service | Role | Parallelism | DB impact |
|---------|------|----------------|-----------|
| **web** | Gunicorn + Django | Workers = `min(2 * min(os.cpu_count(), effective_cores) + 1, max_gunicorn_workers)` (default cap **32**); override with **`WEB_CONCURRENCY`** | Each worker may hold a persistent DB connection (`CONN_MAX_AGE`, default **90s** via `conf_parser.get_db_conn_max_age()`) |
| **web** | `api.py` | `ThreadPoolExecutor` size = **`api_small_executor_max_workers`**, or **`parallel_db_prefetch_max`** (default **6**) when the API key is unset | Extra concurrent ORM work per worker |
| **web** | `summaryplot.py` | Parallel prefetch uses **`get_parallel_db_prefetch_max_workers()`** (same default **6**) | Same cap family as API executor |
| **pipeline** | `update_metrics.py` | `multiprocessing.Pool` size = **`get_metrics_pool_process_count()`** (≤ **`metrics_pool_process_cap`**) | Many concurrent readers during metrics passes |
| **pipeline** | `sync_timedb.py` / archive | Ingest pool = **`get_sync_ingest_pool_processes()`** (same base as `get_worker_thread_count(2)`, optional **`sync_pool_process_cap`**); archive pool = **`get_sync_archive_pool_processes()`** (half of ingest, optional **`archive_pool_process_cap`**) | Load spikes + pigz CPU; `sync_timedb` is a long-lived loop (rescans archive after each wave; sleeps 5 minutes when no pending files) |
| **pipeline** | `listend.py` | Pika + a few daemon threads; no Django DB in this module | Low |
| **db** | PostgreSQL | `max_connections=500` in `docker-compose.yaml` | Hard ceiling for all clients |

**Sizing rule:** **`effective_cores = min(ini total_cores, os.cpu_count())`**. If **`[DEFAULT] total_cores`** is **missing** in `hpcperfstats.ini`, the code uses **40** as the ini budget. If the host has more CPUs than **`total_cores`**, the ini value **caps** app parallelism. If **ini > host**, **`os.cpu_count()`** (including cgroup/cpuset limits) wins.

## PostgreSQL connection budget (operator)

Rough peak connections:

`web_workers + metrics_pool_processes + sync_timedb_processes + overhead`

Keep this **below** `max_connections` minus headroom for admin, autovacuum, and monitoring. The stack does **not** use an external pooler (no PgBouncer): sizing is direct Django → Postgres. Use **`WEB_CONCURRENCY`**, **`metrics_pool_process_cap`**, **`sync_pool_process_cap`**, and **`parallel_db_prefetch_max`** to cap clients before raising `max_connections`.

## Connection lifetime, query timeouts, and staggered pipeline

- **`CONN_MAX_AGE`:** Default **90** seconds (`[DEFAULT] db_conn_max_age` or **`DJANGO_CONN_MAX_AGE`**). Lowers how long idle Gunicorn workers hold a backend. Does not cap peak concurrency under full load; pairs with the caps above.
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
- `best_effort`: `syslog-ng`, `logrotate.sh`, optional `rsync_data`, optional browser/API test traffic

Relevant ini keys:

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

## Related files

- [`hpcperfstats/conf_parser.py`](../hpcperfstats/conf_parser.py) — `get_effective_cores()`, caps, NUMA compose flags
- [`hpcperfstats/compose_cpu_layout.py`](../hpcperfstats/compose_cpu_layout.py) — linear responsive `cpuset` partition
- [`hpcperfstats/numa_topology.py`](../hpcperfstats/numa_topology.py) — sysfs parse and node-pair selection
- [`scripts/apply_compose_cpu_pinning.py`](../scripts/apply_compose_cpu_pinning.py) — writes CPU pinning fragments
- [`services-conf/django_startup.sh`](../services-conf/django_startup.sh) — Gunicorn worker count
- [`hpcperfstats/site/hpcperfstats_site/settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py) — `CONN_MAX_AGE`, PostgreSQL `OPTIONS`
- [`docker-compose.yaml`](../docker-compose.yaml) — Postgres `max_connections`, `statement_timeout`, `idle_in_transaction_session_timeout`
