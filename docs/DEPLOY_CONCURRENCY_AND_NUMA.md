# Deployment: concurrency, PostgreSQL, and NUMA pinning

This document summarizes how **thread/process counts** and **Docker Compose CPU sets** relate to **PostgreSQL `max_connections`** and large hosts (including multi-NUMA systems).

## Services and parallelism (compose)

| Service | Role | Parallelism | DB impact |
|---------|------|----------------|-----------|
| **web** | Gunicorn + Django | Workers = `min(2 * min(os.cpu_count(), effective_cores) + 1, max_gunicorn_workers)` (default cap **32**); override with **`WEB_CONCURRENCY`** | Each worker may hold a persistent DB connection (`CONN_MAX_AGE`) |
| **web** | `api.py` | `ThreadPoolExecutor` size = **`api_small_executor_max_workers`** (default 8) | Extra concurrent ORM work per worker |
| **pipeline** | `update_metrics.py` | `multiprocessing.Pool` size = **`get_metrics_pool_process_count()`** (≤ **`metrics_pool_process_cap`**) | Many concurrent readers during metrics passes |
| **pipeline** | `sync_timedb.py` / archive | Pool size from **`get_worker_thread_count(4)`**; pigz threads use same divisor-based count | Load spikes + pigz CPU |
| **pipeline** | `listend.py` | Pika + a few daemon threads; no Django DB in this module | Low |
| **db** | PostgreSQL | `max_connections=500` in `docker-compose.yaml` | Hard ceiling for all clients |

**Sizing rule:** **`effective_cores = min(ini total_cores, os.cpu_count())`**. If **`[DEFAULT] total_cores`** is **missing** in `hpcperfstats.ini`, the code uses **40** as the ini budget. If the host has more CPUs than **`total_cores`**, the ini value **caps** app parallelism. If **ini > host**, **`os.cpu_count()`** (including cgroup/cpuset limits) wins.

## PostgreSQL connection budget (operator)

Rough peak connections:

`web_workers + metrics_pool_processes + sync_timedb_processes + overhead`

Keep this **below** `max_connections` minus headroom for admin, autovacuum, and monitoring. If you need more web workers than Postgres can accept, consider **PgBouncer** (transaction pooling) and shorter **`CONN_MAX_AGE`** — not bundled here; treat as a separate infrastructure change.

## NUMA topology and Compose pinning

Topology is read from **Linux sysfs**: `/sys/devices/system/node/node*/cpulist` (not hardcoded).

- **Auto pinning** applies when there are **2–16** NUMA nodes, **`effective_cores` ≥ `cpuset_pin_min_total_cores`** (default 32), and each **chosen** node has at least **`cpuset_pin_min_cores_per_node`** CPUs (default 16), unless you set **`web_numa_node`** and **`pipeline_numa_node`** explicitly (required when you have **>16** nodes and still want auto-generated cpusets).
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

On single-node or small hosts the script writes an **empty** `services: {}` overlay so the extra `-f` file is still safe to include.

## Related files

- [`hpcperfstats/conf_parser.py`](../hpcperfstats/conf_parser.py) — `get_effective_cores()`, caps, NUMA compose flags
- [`hpcperfstats/numa_topology.py`](../hpcperfstats/numa_topology.py) — sysfs parse and node-pair selection
- [`services-conf/django_startup.sh`](../services-conf/django_startup.sh) — Gunicorn worker count
