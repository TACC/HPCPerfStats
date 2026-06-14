# Stress row sweep: comparison and scaling plan

Compose workflow: `tests/run_stress_host_data_workflow.sh` (Docker `db` + `redis`, pytest `tests/stress_host_data/`). Host: Apple Silicon, **2026-04-06/07**.

The stress test calls `update_metrics(django_timezone.localtime(job.end_time), rerun=True)` so `end_time__date` matches Django’s `TIME_ZONE` (§5).

`tests/run_stress_host_data_inner.sh`: if `pip install -e ".[test]"` fails on the bind mount (e.g. cloud-sync **Errno 35**), it sets **`PYTHONPATH=/home/hpcperfstats`** and runs **`tests/pip_compose_test_extras_fallback.sh`** (pins match **`pyproject.toml`**: Django 6.x, pytest 9+, pytest-django 4.12+, pytest-cov 7.1+) so nothing writes `egg-info` on the sync tree.

## 1. Row-count sweep (single host, 1 s cadence, 40 metrics)

`jid_table` **token** is `full` until `host_data` rows for the job window exceed **`get_large_job_host_data_row_threshold()`** (default **1,500,000**); then time sampling uses up to **2048** distinct timestamps (`lb2048`).

| Main rows | Token | Report JSON | seed (s) | live_distinct (s) | jid_table (s) | update_metrics (s) |
|-----------|-------|-------------|----------|-------------------|---------------|---------------------|
| 80,000 | full | `stress_report_20260406T180559Z.json` | 0.62 | 0.025 | 0.052 | 3.28 |
| 160,000 | full | `stress_report_20260407T002508Z.json` | 1.16 | 0.046 | 0.102 | 6.27 |
| 256,000 | full | `stress_report_20260407T003132Z.json` | 1.88 | 0.082 | 0.160 | 10.32 |
| 320,000 | full | `stress_report_20260406T221549Z.json` | 2.27 | 0.108 | 0.203 | 13.58 |
| 640,000 | full | `stress_report_20260407T002601Z.json` | 4.78 | 0.216 | 0.392 | 31.99 |
| 1,280,000 | full | `stress_report_20260407T002755Z.json` | 9.79 | 0.461 | 0.826 | 88.42 |
| 2,560,000 | **lb2048** | `stress_report_20260407T003051Z.json` | 19.53 | 0.952 | 1.046 | **6.81** |

### Where scaling stays close to linear

From **80k → 640k** (all **full** window), `update_metrics` scales within ~**1.0–1.3× per 2× rows** (roughly linear to mildly superlinear).

### Where it is **not** linear

1. **640k → 1.28M (still full):** `update_metrics` ratio **88.42 / 31.99 ≈ 2.76** for **2× rows** — clearly **superlinear** in this band (larger ORM/DataFrame work per timestep, cache, or planner effects).

2. **1.28M → 2.56M:** Row count doubles but **`update_metrics` drops 88 s → 6.8 s** because the job crosses the **1.5M `host_data` row threshold**: `jid_table` switches to **time sampling** (~2048 buckets). Cost is **capped** in time dimension; seed/live/jid_table **still grow** with rows.

## 2. Bug fixed: large-job `time__in` vs `job_arc`

With **lb2048**, `_base_filter` uses **`time__in`** (no `time__gte` / `time__lte`). **`Metrics.job_arc`** and **`job_value_mean`** previously indexed `base["time__gte"]` → **`KeyError: 'time__gte'`** on jobs above the threshold.

**Fix:** `_jid_table_host_data_time_kwargs()` in `metrics.py` mirrors `jid_table._host_data_time_filter_kwargs()`. Unit tests: `test_jid_table_host_data_time_kwargs_full_and_sampled`, `test_job_arc_uses_time__in_when_jid_table_large_job_sampled`.

## 3. What to change **now**

1. **Timezone + stress:** keep `localtime(job.end_time)` for `update_metrics` in the stress test; align any cron/driver the same way.
2. **Large-job metrics:** keep **`job_arc` / `job_value_mean`** compatible with **`time__in`** (above).
3. **Pip on sync mounts:** rely on inner-script **PYTHONPATH + test extras** fallback (or run from a non-synced checkout).
4. **Redis key length:** `CacheKeyWarning` on `agg_df:...` including **`lb2048`** suffix — hash or shorten keys if Memcached is used.
5. **`pg_total_relation_size('host_data')`** in reports is not meaningful for hypertable parent size — use chunk/hypertable sizing when you need bytes (see [large_job_db_scaling_followup.md](large_job_db_scaling_followup.md)).

## 4. How to scale out (later)

| Direction | Idea |
|-----------|------|
| **Before 1.5M rows** | Expect **superlinear** `update_metrics` growth as timesteps grow; profile ORM + `list(qs)` + pandas paths. |
| **Above 1.5M rows** | Metrics/plots are **sampled**; tune **`HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`** and **`HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`** for accuracy vs latency tradeoffs. |
| **DB** | Indexes on `(host, time)`, `jid`; Timescale compression; avoid loading full-series DataFrames when a metric only needs aggregates. |
| **Workers** | Pool size vs PostgreSQL **max_connections**; shard by day/jid if many jobs per `update_metrics` day. |
| **Cache** | Shorter/hashed aggregate keys. |

## 5. Invalid / historical reports

- **`stress_report_20260406T221120Z.json`**, **`221241Z`**, **`221342Z`**, **`002834Z`**: pre-`localtime` fix or pre-`job_arc` fix — **do not use** for timing.
- Failed **640k** runs that stopped at **pip** on a cloud-sync bind mount are superseded by **2026-04-07** runs with the inner-script fallback.

## 6. Related docs

- [large_job_db_scaling_followup.md](large_job_db_scaling_followup.md)  
- `tests/run_stress_host_data_workflow.sh --help`
