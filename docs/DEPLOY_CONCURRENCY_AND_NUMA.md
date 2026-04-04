# Deployment: concurrency, PostgreSQL, and NUMA pinning

This document summarizes how **thread/process counts** and **Docker Compose CPU sets** relate to **PostgreSQL `max_connections`** and large hosts (including multi-NUMA systems).

## Services and parallelism (compose)

| Service | Role | Parallelism | DB impact |
|---------|------|----------------|-----------|
| **web** | Gunicorn + Django | Workers = `min(2 * min(os.cpu_count(), effective_cores) + 1, max_gunicorn_workers)` (default cap **32**); override with **`WEB_CONCURRENCY`** | Each worker may hold a persistent DB connection (`CONN_MAX_AGE`, default **90s** via `conf_parser.get_db_conn_max_age()`) |
| **web** | `api.py` | `ThreadPoolExecutor` size = **`api_small_executor_max_workers`**, or **`parallel_db_prefetch_max`** (default **6**) when the API key is unset | Extra concurrent ORM work per worker |
| **web** | `summaryplot.py` | Parallel prefetch uses **`get_parallel_db_prefetch_max_workers()`** (same default **6**) | Same cap family as API executor |
| **pipeline** | `update_metrics.py` | `multiprocessing.Pool` size = **`get_metrics_pool_process_count()`** (≤ **`metrics_pool_process_cap`**) | Many concurrent readers during metrics passes |
| **pipeline** | `sync_timedb.py` / archive | Ingest pool = **`get_sync_ingest_pool_processes()`** (same base as `get_worker_thread_count(4)`, optional **`sync_pool_process_cap`**); archive pool = **`get_sync_archive_pool_processes()`** (half of ingest, optional **`archive_pool_process_cap`**) | Load spikes + pigz CPU |
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

## Observability

- Run **`python hpcperfstats/site/manage.py pg_connection_stats`** from the repo root (with **`HPCPERFSTATS_INI`** / config and DB reachable) to print **`pg_stat_activity`** totals for the current database (`machine` app management command).

## NUMA topology and Compose pinning

Topology is read from **Linux sysfs**: `/sys/devices/system/node/node*/cpulist` (not hardcoded).

- **Auto pinning** applies when there is **1** NUMA node and **`effective_cores` ≥ `cpuset_pin_min_total_cores`** (default 32) and that node has at least **`cpuset_pin_min_cores_per_node`** CPUs (default 16), **or** when there are **2–16** nodes with the same thresholds **per chosen node** (two-node case), unless you set **`web_numa_node`** and **`pipeline_numa_node`** explicitly (required when you have **>16** nodes and still want auto-generated cpusets). On a **single** node, web and pipeline both get the **same** `cpulist` (no NUMA isolation; explicit `cpuset` only).
- **`web`** and **`pipeline`** each get **one full node `cpulist`**; **`proxy`** can match **`web`** if **`pin_proxy_in_compose = yes`**.
- **`db`**, **Redis**, and **RabbitMQ** stay **unpinned** by default so they can use remaining nodes.

### Script: `scripts/apply_compose_numa_pinning.py`

Run on the **deployment host** after provisioning (needs sysfs):

```bash
export HPCPERFSTATS_INI=/path/to/hpcperfstats.ini
python scripts/apply_compose_numa_pinning.py
# or
python scripts/apply_compose_numa_pinning.py --dry-run
```

This writes **`docker-compose.numa-pinning.yaml`** at the repo root (gitignored by default). Start the stack with:

```bash
docker compose -f docker-compose.yaml -f docker-compose.numa-pinning.yaml up -d
```

On hosts **without** sysfs NUMA (or **below** pinning thresholds, or unsupported explicit node ids), the script writes an **empty** `services: {}` overlay so the extra `-f` file is still safe to include.

## Related files

- [`hpcperfstats/conf_parser.py`](../hpcperfstats/conf_parser.py) — `get_effective_cores()`, caps, NUMA compose flags
- [`hpcperfstats/numa_topology.py`](../hpcperfstats/numa_topology.py) — sysfs parse and node-pair selection
- [`services-conf/django_startup.sh`](../services-conf/django_startup.sh) — Gunicorn worker count
- [`hpcperfstats/site/hpcperfstats_site/settings.py`](../hpcperfstats/site/hpcperfstats_site/settings.py) — `CONN_MAX_AGE`, PostgreSQL `OPTIONS`
- [`docker-compose.yaml`](../docker-compose.yaml) — Postgres `max_connections`, `statement_timeout`, `idle_in_transaction_session_timeout`
