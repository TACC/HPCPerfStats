# `hpcperfstats.site.machine.api` coverage baseline

Host-side unit tests for `api.py` using `pytest.mark.django_db(databases=[])` and LocMem cache so no compose `db` is required. ORM, Redis, RabbitMQ management HTTP, TimescaleDB cursors, and heavy plot builders are mocked at boundaries.

## Test modules

| Module | Role |
|--------|------|
| `hpcperfstats/site/machine/tests/test_api_helpers.py` | Direct helper tests: Bokeh payload helpers, auth/visibility, cache invalidation digests, metrics-derived GPU/FSIO tuples, histogram merge, TimescaleDB/RabbitMQ/XALT admin stats (mocked), job-list queryset builder, inflight plot eviction, **TimescaleDB/RabbitMQ error branches**, **cache SCAN paths**, **sacct ingest 500 path** |
| `hpcperfstats/site/machine/tests/test_api_view_matrix.py` | One class per primary `@api_view`: session/home/API keys/job list/histograms/detail/plots/type detail/admin monitor; **`TestJobPlotsL2FinalizeAndZoom`**, **`TestJobDetailXaltFetch`**, **`TestJobMonitorAggregates`**, **`TestJobMonitorGpuFallbackBranches`**, **`TestHostPlotBuildCallback`** |
| `hpcperfstats/site/machine/tests/test_api_coverage_gaps.py` | Branch gaps: cache invalidation, `host_plot`, `job_monitor`, GPU monitor, `sacct_ingest`, queue-wait aggregates, `TestJobDetailApi`, `TestJobPlotsApi` |
| `hpcperfstats/site/machine/tests/test_api_misc.py` | Pre-existing focused tests (session, home, admin monitor refresh, visibility, log timestamps, cache stats, age buckets) |

Shared autouse settings (LocMem cache, `ALLOWED_HOSTS`) mirror `test_api_coverage_gaps.py` via duplicated `_API_COVERAGE_GAP_SETTINGS` fixtures in the new modules.

## Run command

```bash
cd HPCPerfStats && PYTHONPATH=. ../.venv/bin/python -m pytest \
  hpcperfstats/site/machine/tests/test_api_helpers.py \
  hpcperfstats/site/machine/tests/test_api_view_matrix.py \
  hpcperfstats/site/machine/tests/test_api_coverage_gaps.py \
  hpcperfstats/site/machine/tests/test_api_misc.py \
  --cov=hpcperfstats.site.machine.api \
  --cov-report=term-missing \
  --cov-branch -q
```

## Coverage summary (2026-06-05, updated)

```
Name                               Stmts   Miss Branch BrPart  Cover   Missing
------------------------------------------------------------------------------
hpcperfstats/site/machine/api.py    1527    148    562    103  87.7%
------------------------------------------------------------------------------
TOTAL                               1527    148    562    103  87.7%

192 passed in ~4s
```

**Line coverage: 87.7%** (up from 77.0%; 148 statements missed of 1527).

### Focus-area coverage added this pass

| Gap (baseline v1) | Tests added |
|-------------------|-------------|
| `job_plots` finalize / L2 / zoom / stale L1 | `TestJobPlotsL2FinalizeAndZoom`, `TestJobPlotsFetchErrors` |
| `job_detail` `_fetch_xalt` | `TestJobDetailXaltFetch` (mocked `run` / `join_run_object` / `lib`) |
| `_get_timescaledb_stats` error branches | `TestTimescaledbStatsErrorBranches` |
| `_get_rabbitmq_stats` HTTP/connection/decode errors | `TestRabbitmqStatsErrorBranches` |
| `_get_cache_stats` Redis SCAN | `TestCacheStatsScanPaths` |
| `job_monitor` SQL aggregate rows | `TestJobMonitorAggregates`, `TestJobMonitorGpuFallbackBranches` |

## Remaining gaps (documented)

| Area | Lines (approx.) | Reason |
|------|-----------------|--------|
| `job_plots` future.result exception / L2 cache.set failures | 2234–2242, 2289–2307 | Needs selective `cache.set` fault injection without breaking DRF throttle |
| `job_detail` XALT dedupe branches (missing lib, duplicate module) | 1932, 1935 | Narrow XALT join edge cases |
| `_get_timescaledb_stats` individual `fetchone` None arms | 826–964 | Partial stats when queries return empty rows |
| `_get_rabbitmq_stats` `import requests` failure | 995–996 | Import guard; covered by empty-return contract |
| `_get_cache_stats` outer/scan except | 791–797 | Full SCAN abort path |
| `_build_job_list_queryset_from_request` metric filter warnings | 547–581 | Malformed metrics query params |
| `_build_histogram_queryset` except | 1515–1516 | DB failure already returns empty nj |
| `_sanitize_hist_plot_item` json_item except | 1635–1636 | Bokeh serialize failure |
| `admin_monitor` refresh cache.delete except | 2757–2764 | Staff refresh error swallow |
| `job_monitor_gpu` invalid days clamp only | 2904–2905 | Minor branch |
| `sacct_ingest` body decode except | 3002–3003 | Hard to trigger with bytes body |
| `sacct_ingest` DEBUG re-raise | 3036 | Requires `settings.DEBUG=True` |

## Next steps toward higher coverage

1. Add L2 hydrate tests with `cache.set` fault injection keyed on `JOB_PLOTS` only (helper already exists: `_cache_set_fail_job_plots_only`).
2. Extend XALT mocks for missing `lib` rows and duplicate `module_name` dedupe.
3. Table-driven TimescaleDB cursor tests returning `None` from `fetchone` per query block.
