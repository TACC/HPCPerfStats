# Test layout

This page describes where HPCPerfStats tests live, which markers and environment gates select them, and what each module covers.
It is for developers choosing where to add a test or which suite to run; the commands are in [Run tests](../how-to/develop/run-tests.md).

## Test layers

- Unit (`python scripts/run_tests.py --no-django`, Vitest `npm test -- --run`): Pure helpers, mocked API views (`django_db(databases=[])`), SPA page shells with jest-axe (`axeSeriousViolations`)
- Integration (`tests/run_db_pytest_workflow.sh`, `tests/run_valkey_cache_pytest_workflow.sh`): Postgres, Valkey, Timescale
- E2E (`tests/run_web_e2e_workflow.sh`, `tests/run_pipeline_e2e_workflow.sh`): Browser UX, nginx, full pipeline

Prove correctness at the narrowest layer first; escalate only when mocks cannot exercise the contract.
`--no-django` covers the Unit layer only, so a green run is not a full check ([Run the complete check](../how-to/develop/run-tests.md#run-the-complete-check)).
Split a monolithic function when that materially reduces the mock surface, and lock behavior with tests before extracting helpers.

## Where tests live

- Python: `test_*.py` beside modules for small leaf packages, `hpcperfstats/tests/` for cross-cutting daemons, or a dedicated `tests/` package when the subtree has many modules.
- `analysis/metrics/`: every pytest module goes under `hpcperfstats/analysis/metrics/tests/`, never beside `update_metrics.py` or under `lib/plot/` / `lib/gen/`.
- Frontend: `*.test.{ts,tsx}` colocated under `hpcperfstats/site/frontend/src/`.
- Opt-in suites outside the default `testpaths`: `tests/stress_host_data/`, `tests/pipeline_e2e/`, `tests/sync_timedb_benchmark`, `tests/sync_timedb_day_close_soak/`.
- Cross-layer registries (routes, metric labels, extended-search params) need drift tests that fail when source and consumer lists diverge.

## Markers and environment gates

`hpcperfstats/conftest.py` sets the markers:

- `compose_db` marks every test that needs the default PostgreSQL database; outside compose that is the set that skips, and `scripts/run_tests.py --no-django` deselects it.
  The root `conftest.py` applies `django_db` to `site/lib/machine/tests` by default, and `pytest_collection_modifyitems` skips the Postgres-backed ones unless `HPCPERFSTATS_COMPOSE_NETWORK=1`.
- `django_db(databases=[])` tests need only Django settings and mocks and still run on the host.
- `@pytest.mark.machine_unit_mock` marks pure mocks with no Django DB fixture, so pytest neither attaches `django_db` nor skips them for missing Compose.
- `live_valkey` marks the live cache tests.
- During pytest Django switches the default cache to LocMem unless `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`.

Opt-in gates:

| Variable                                 | Turns on                                                                                         |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `HPCPERFSTATS_COMPOSE_NETWORK=1`         | Default-DB tests (set inside `web` by `tests/run_db_pytest_workflow.sh` and the other workflows) |
| `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`      | Live Valkey cache tests                                                                          |
| `HPCPERFSTATS_STRESS_HOST_DATA=1`        | `tests/stress_host_data/`                                                                        |
| `HPCPERFSTATS_PIPELINE_E2E=1`            | `tests/pipeline_e2e/`                                                                            |
| `HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1` | Golden tests fail instead of skipping when the corpus is absent                                  |
| `HPCPERFSTATS_CODON_METRICS_REQUIRED=1`  | Codon metrics kernel tests fail instead of skipping without the pyext                            |

## Runners

`pyproject.toml` names the canonical runners under `[tool.hpcperfstats.testing]`: `web_e2e_runner`, `db_pytest_runner`, `valkey_cache_pytest_runner`.

| Script                                         | Role                                                                                                      |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `scripts/run_tests.py`                         | Host pytest wrapper                                                                                       |
| `tests/run_all_compose_workflows.sh`           | Runs the eight compose workflows in order                                                                 |
| `tests/compose_test_cmd.sh`                    | Project-scoped `podman-compose` command with the test overlay                                             |
| `tests/run_dev_container_pytest.sh`            | Pytest in the dev container (`--ft` for the free-threaded interpreter, `--exec`, `--mount`)               |
| `scripts/lib/podman_runtime.sh`                | Canonical rootless runtime, `/data` cache and storage validation, project names, Podman health inspection |
| `tests/podman_compose_teardown.sh`             | Project-scoped `down -v --remove-orphans`; never a global prune                                           |
| `tests/verify_podman_project_isolation.sh`     | Positive-control regression proving teardown does not remove unrelated Podman assets                      |
| `tests/run_podman_development_stack_verify.sh` | Starts and verifies the isolated `hpcperfstats-dev` stack on unprivileged ports                           |
| `tests/pip_compose_test_extras_fallback.sh`    | Installs the Django, pytest, pytest-django and pytest-cov test floors when the editable install fails     |

## Test modules

Each list names a module and the contract it locks.
Modules under `hpcperfstats/tests/` are host unit tests with no Postgres unless the entry says compose.

### sync_timedb

- `hpcperfstats/tests/test_sync_timedb_parsing_canonical.py`: Canonical stats-line ingest (semantic PMC/IMC events, no CTL/CTR eventmaps)
- `hpcperfstats/tests/test_sync_timedb_parsing_legacy.py`: Legacy ingest path (`map_hardware_counter_vals`, hex eventmaps, KNL type aliases)
- `hpcperfstats/tests/test_sync_timedb_parse_efficiency.py`: Columnar schema compile, packed carry, unix-seconds collapse, streaming incremental chunks, listend one-sample kernel
- `hpcperfstats/tests/test_archive_compress.py`: Pure path helpers in `archive_compress.py` (detect format, tar/zst/gz siblings, member maps)
- `hpcperfstats/tests/test_sync_timedb_startup_archive_scan.py`: Canonical startup snapshot coordinator helpers
- `hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py`: `run_sync_timedb_queue_orchestrator`: exclusive `archive_dir` flock, `from_parsed` wiring, no `run_sync_timedb_supervisor_loop` symbol, sliding-window ingest->append enqueue while other ingest is inflight, `day_close` tar-drop when sealed with no closed raw, day-close `on_handoff_to_ingest` requeue, space reclaim (`post_seal` False, reseal-after-delete, `phase=done` dual `tar_drop`)
- `hpcperfstats/tests/test_sync_timedb_architecture_contract.py`: The `day_close` predicate contracts: rediscovered lock and remaining raw become ingest or append jobs; `day_close` = filesystem complete + minimum age
- `tests/run_sync_timedb_day_close_soak_workflow.sh`: Day-close `open_tar` / dual reclaim soak without production wall-clock; host pytest by default (`tests/sync_timedb_day_close_soak/` + reclaim regressions), compose with `HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1 HPCPERFSTATS_DAY_CLOSE_SOAK_COMPOSE=1`; logs `test_runs/day-close-space-reclaim-soak-*.log`
- `hpcperfstats/tests/test_sync_timedb_ingest_file_timeout.py`: Idle-stall / `_run_ingest_timed` wiring and the absence of the leftover wall timeout
- `hpcperfstats/tests/test_leftover_retired_purge.py`: Deleted leftover no-op names stay deleted (watchdog, deadline ContextVars, always-0 wall getters)
- `hpcperfstats/tests/test_sync_timedb_archive.py`: DB-complete ingest path: identity-keyed `get_existing_archive_members_for_daily_archive` cache, `daily_archive_has_member_with_size`, store-backed single-flight populate, bad sealed day skip, L1 skip LRU, sealed point lookup, `invalidate_daily_archive_members_cache`; deferred-archive and atomic-seal helpers, `tar tf` integrity, zstd gzip restore, tar dedupe (largest wins per path), `validate_sealed_daily_archive_for_raw_removal`; integration tests call real `zstd` and skip without it
- `hpcperfstats/tests/test_sync_timedb_archive_members_coord.py`: In-process member-store populate and wait (`test_populate_store_single_flight_and_warm_lookup`, `test_sticky_skip_survives_reload`)
- `hpcperfstats/tests/test_sync_timedb_job_queue.py`: In-process job-store reconstruct, discover and streaming helpers plus leftover host claim tests; the operator census uses `.sync_timedb_job_store.json` and `[thread:populate-pool]` (`test_operator_census_uses_disk_sidecars_and_thread_titles`)
- `hpcperfstats/tests/test_sync_timedb_job_discover.py`: Streaming discover calendar-day resolver, ingest queue cap stop, mtime window, CLI date-range filter
- `hpcperfstats/tests/test_sync_timedb_tar_clone_append.py`: Clone append writer on a reflink filesystem (host `/tmp` XFS `reflink=1`; fails rather than skips without reflink): GNU/tarfile roundtrip, 4 KiB data alignment, GNU b20 base clones every member, copy fallback and hung-copy idle kill, >8 GiB sparse member on a GNU-format tar, ENOSPC rollback, journal recovery (stale/corrupt/orphan), SIGKILL at every commit step, salvage of a truncated clone-written tar
- `hpcperfstats/tests/test_sync_timedb_append_tar_race.py`: `_append_to_tar` samples existence and appends (`clone_append`) under the write lock, never `tar -c`/`-r`; append jobs group per daily tar
- `hpcperfstats/tests/test_sync_timedb_subprocess_hardening.py`: zstd pipe drain, nested `already_held` write lock, pax convert path gone, decompress stderr capture, append copy fallback under progress idle kill
- `hpcperfstats/tests/test_sync_timedb_archive_members_store_required.py`: The process archive-members store is required (`require_process_archive_members_store`)
- `hpcperfstats/tests/test_sync_timedb_file_complete_ingest_mark.py`: Durable file-complete mark helpers (empty or missing path, malformed sidecar, `db_skip` tokens, live-listend completeness)
- `hpcperfstats/tests/test_sync_timedb_zero_host_ingest_mark.py`: Zero-host mark helpers (gate on host rows written to the DB, empty archive dir, malformed entries)
- `hpcperfstats/tests/test_sync_timedb_day_close_cooperation.py`: Day-close yield/defer tracker, empty tar-path `normpath` (`.` ), write-lock backoff, chunk-day defer
- `hpcperfstats/tests/test_sync_timedb_host_itimes.py`: Mocked `host_data` Unix-second probes: empty second sets, 24h chunk bounds, overflow, statement-timeout overflow
- `hpcperfstats/tests/test_sync_timedb_db_offload_high_confidence.py`: Compose: DB oracles for ingest DB offloads (proc peak upsert, batched second-presence); run via `tests/run_db_pytest_workflow.sh -- hpcperfstats/tests/test_sync_timedb_db_offload_high_confidence.py`
- `hpcperfstats/tests/test_sync_timedb_pipeline_edge_cases.py`: Cross-module empty, malformed and boundary units for parsing, `--jid` coercion, persistence envelopes, ingest timeout (always-0 walls), reconstruct bands, readiness live-on, append `peek_first`, idle progress
- `hpcperfstats/tests/test_sync_timedb_*_harvest.py`: Absence of deleted leftovers: the retired file-state machine (`test_sync_timedb_leftover_harvest.py`), the always-0 ingest-timeout shells (`test_sync_timedb_ingest_timeout_harvest.py`), the removable-raw classify wrappers plus `prune_day_phases_hints` on sealed-only days (`test_sync_timedb_h14_n5_harvest.py`)
- `hpcperfstats/tests/test_codon_exclude_parity.py`: Codon dynamic exclusion; kernel, bridge and IPC cases run on the host venv; the three real-pyext cases need a bake from this tree in `HPCPERFSTATS_CODON_PYEXT_DIR` and the free-threaded 3.14t (the host `.venv` is not, so they skip there)
- `hpcperfstats/tests/test_codon_parity_registry.py`: Lists every Codon/CPython twin implementation and its parity tests; fails on an unregistered Codon export, a NumPy reference without a twin, or a new direct CPython-parser call site
- `hpcperfstats/tests/test_codon_canonical_parity.py`, `test_listend_codon_divert.py`: Kernel source vs `IncrementalStatsParser`: legacy CTL/CTR decode, the resume `start_idx` gate (rows, marks, stamps), the `%jobs` arm across file-session windows, `proc`/`host_proc` rows, listend samples

To run the real-pyext parity cases against a bake from this tree:

```bash
podman build --target codon-build \
  -t localhost/hpcperfstats-codon-build:parity \
  -f services-conf/pipeline_web.Dockerfile .
```

Then `podman cp` `/opt/hpcperfstats/codon_parse/.` into `test_runs/codon_parity/codon_parse` and run:

```bash
D=/home/hpcperfstats/test_runs/codon_parity/codon_parse
T=hpcperfstats/tests
HPCPERFSTATS_CODON_PYEXT_DIR=$D tests/run_dev_container_pytest.sh \
  --ft $T/test_codon_canonical_parity.py \
  $T/test_codon_exclude_parity.py -q
```

The benchmark-path modules are in [Run sync_timedb benchmarks](../how-to/develop/run-sync-timedb-benchmarks.md#run-the-unit-harness).

### listend and accounting

- `hpcperfstats/tests/test_sync_acct.py`: Accounting ingest (`sync_acct_from_content`, restricted queues, bulk fallback, cache notify, dead-letter `--dead-letter-unimportable`, end-of-run dead-letter summary) with mocked ORM
- `hpcperfstats/tests/test_listend_drain.py`: RabbitMQ drain loop (ack/nack, empty queue) with mocked pika
- `hpcperfstats/tests/test_listend_dup_guard.py`: Raw-archive duplicate-sample guard: a run re-sent after newer samples is skipped, a mixed body appends only new samples, distinct same-second and same-timestamp-other-jid samples are kept, `$` rotation keeps the guard, stale-generation items are not archived, `listend dup_samples` counters (redelivered/fresh, stale acks, nacks)
- `hpcperfstats/tests/test_listend_archive_pool.py`: Host-affine archive worker pool: per-host FIFO, ack/nack only after durable archive, drop-mode DB submit off the consume thread (archive threads = 2 x `listend_amqp_consumer_count`)
- `hpcperfstats/tests/test_django_bootstrap.py`: `ensure_django()` thread safety: `django.setup()` runs once under a lock (concurrent listend DB threads), failures are not latched, reentrant calls no-op
- `hpcperfstats/tests/test_monitor_naming_resolve.py`: Dual-read probe order for `monitor_naming/resolve.py`
- `hpcperfstats/tests/test_monitor_analysis_typename_contract.py`: Monitor `stats_type.st_name` values vs the `canonical.py` lists (`INTEL_IMC_STATS_TYPES`, `ARM_IMC_STATS_TYPES`, `INTEL_CORE_PMC_TYPES_ORDERED`) and roofline peak rows; skips when monitor sources are absent

`hpcperfstats/tests/` also holds config parsing, service startup and health, cache, date, print and file-locking helpers, XALT models, API key mobile checks, and dbload `zstd` and row-builder helpers.
Django's `django.utils.timezone.utc` is restored in `hpcperfstats_site/settings.py` for Django 5+.
`hpcperfstats/tests/test_conf_parser.py` covers the absolute `get_metrics_pool_processes()` thread count, default `total_cores=40`, DB options, the sync and listend concurrency getters and the `metrics_backfill_*` getters.
`test_pg_connection_stats_command.py` runs the `pg_connection_stats` management command with a mocked DB connection and groups backends by `application_name`.
`test_pg_slot_budget.py` locks `estimate_django_pg_slot_peak()` (gunicorn x executor + listend + `metrics_pool` + ingest + `day_close` + reserve) and the 80% WARN against compose `max_connections=500`.

### update_metrics

- `hpcperfstats/site/lib/machine/tests/test_update_metrics.py`: Two-phase listing SQL (`test_jobs_queryset_listing_sql_omits_live_distinct_and_sha256`, `test_jobs_queryset_selects_jobs_dirtied_by_late_host_data`, `test_iter_date_listing_pks_runs_phase_a_then_phase_b`, `test_jobs_queryset_still_lists_artifact_only_via_phase_b`, `test_census_listed_avoids_live_distinct_and_fingerprint_sql`, `test_sliding_supplements_invoke_on_supplements_taken`); the backfill lane `test_backfill_*` (listing SQL, cursor, slot cap + fresh priority, skip/quiescence, failed-final, CLI + census); `test_main_sleep_after_runs_passes_in_process`, `test_main_default_runs_continuous_scheduler`, `test_public_ef_refresher_refreshes_and_records_stats`; one-shot `test_main_jid_*` / `test_parse_jid_cli_arg_forms`
- `hpcperfstats/analysis/metrics/tests/test_metrics_persist_provenance.py`: Version and coverage stamps, skip recorder, failed-final persist
- `test_metrics_sample_persist.py`: `test_mark_jids_metrics_dirty_stamps_never_processed_jobs_outside_window`
- `hpcperfstats/site/lib/machine/tests/test_migrations.py`: `test_0043_*` migration checks (run through `tests/run_db_pytest_workflow.sh`)
- `hpcperfstats/tests/test_update_metrics_state_doc.py`: Drift guard for `docs/reference/update-metrics-state-machine.md`
- `hpcperfstats/tests/test_update_metrics_db_offload_medium_confidence.py`: Compose: DB oracles for metrics DB offloads (`job_arc` SQL bucket drop, live distinct PL/pgSQL, large-job time-slot COPY per host per slot, `jid_table` index probes, plot sum function, bounds `time__lt` slices); needs migration `0034_metrics_host_data_sql_functions`
- `hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage.py`: Window-coverage readiness helpers and legacy fallback (mocks)
- `hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage_compose.py`: Compose: defer->ready when early `host_data` is inserted
- `hpcperfstats/site/lib/machine/tests/test_update_metrics_backfill_compose.py`: Real PostgreSQL backfill lane in the dev container ([Verify the backfill lane](../how-to/develop/diagnose-update-metrics.md#verify-the-backfill-lane))
- `test_update_metrics_architecture_contract.py`: Rejects metrics process pools, SIGALRM, process-only timers and zombie reaping, process-worker titles and retired recycle INI keys; requires `configure_blas_thread_env()` before Django and numpy-pulling imports

`hpcperfstats/analysis/metrics/tests/` holds the metrics, plot and gen unit tests (summary and roofline behavior, roofline peak inference from `host_data.type` schema keys, hover tooltips, shared job-window parsing, Bokeh embed sizing).
Notable modules:

- `test_add_node_power_est_column.py` locks vectorized Summary `node_power_est_w` against the frozen iloc priority chain.
- `test_metrics_telemetry_bounds.py` locks `telemetry_first_time` / `telemetry_last_time` persistence during metrics batch writes.
- `test_per_interval_rate.py` covers node-imbalance variants (DRAM, LNET, GPU util/tensor) and GPU peak helpers.
- `test_job_for_metrics.py` and `test_summaryplot_no_data.py` cover metrics and summaryplot edge cases.
- `test_job_metric_display_labels.py` and `test_metrics_add_arrays.py` cover label maps and small `metrics.py` helpers.
- `test_bokeh_job_embed.py` is a pure-Python unit test for `bokeh_job_embed.figure_embed_kw` (no Django).

Listing runs in two phases.
Phase A `_jobs_queryset` keyset pages omit correlated `live_distinct` `host_data` aggregates and the plot/detail `encode(sha256...)` fingerprints; they use cheap `md_count` / `stale_null` over current catalog metric names, gate failures and the `metrics_dirty_at` stamp, which `sync_timedb` ingest sets via `mark_jids_metrics_dirty` and the metrics persist clears.
Phase B `_jobs_queryset_artifact_catchup` plus `_page_rows_needing_artifact_refresh` lists artifact-only jobs by a cheap existence check, then bounded page fingerprint checks; the empty-pass `listed=` census uses Phase A only.

The columnar parity tests are in [Diagnose update_metrics](../how-to/develop/diagnose-update-metrics.md#check-columnar-parity).

### Web and API

`hpcperfstats/site/lib/machine/tests/` holds the Django and web tests: ORM, query and update helpers, job detail file-system llite vs NFS fallback, security headers, API and misc endpoints, SPA rendering, and page and browser end-to-end tests.

- `test_api_helpers.py`, `test_api_view_matrix.py`, `test_api_coverage_gaps.py`, `test_api_misc.py`, `test_api_coverage_closure.py`: Host-side `api.py` helper and view coverage with mocks and the LocMem cache (cache invalidation, `host_plot`, job monitor, `sacct_ingest`, `job_detail`, `job_plots`); together they hold 100% line coverage ([Measure coverage](../how-to/develop/run-tests.md#measure-coverage))
- `test_type_detail_api.py`: Type-detail `host_data` ORM SQL does not reference `jid` (job scope is start and end time plus accounting hosts), with `django_db(databases=[])`
- `test_oauth2.py`, `test_test_login.py`, `test_cache_middleware.py`, `test_renderers.py`, `test_update_xalt.py`: OAuth helpers, dynamic cache TTL middleware, JSON NaN sanitization, the XALT log script loop
- `test_metrics.py`: `test_job_metric_short_labels_cover_catalog`: every `job_metrics_catalog_entries()` metric has an entry in `job_metric_display_labels.JOB_METRIC_SHORT_LABELS` (parity with the frontend short-label map)
- `test_job_plot_artifacts.py`: Gzip-serialized Bokeh `json_item` persistence (`job_plot_artifact`), fingerprinting and invalidation (needs Postgres)
- `test_public_api.py`: Anonymous `GET/POST /api/pub/cluster-dashboard/` with `django_db(databases=[])` and mocks
- `test_public_metrics_artifacts.py`: EF formula checks under `machine_unit_mock`; refresh and invalidate rows need Postgres (`HPCPERFSTATS_COMPOSE_NETWORK=1`)
- `test_job_list_performance_summary.py`: Job list `performance` labels and `sort_rank`, and ORM `performance_sort_rank` ordering vs `summarize_performance()`
- `test_query_utils.py`: The `get_job_list_order_by` allowlist (`performance_sort_rank`; `has_metrics` is rejected); `/api/jobs/` entries expose `performance`, sorted with `order_by=performance_sort_rank` or `-performance_sort_rank`
- `hpcperfstats/site/lib/machine/tests/test_job_detail_staff_sample_count.py`: Staff-only `staff_metrics_distinct_time_count` on `job_detail` JSON (mocked ORM)
- `hpcperfstats/site/lib/machine/tests/test_job_data_jid_length.py`: `machine_unit_mock`: `job_data.jid` `max_length` is 512 and the logged heterogeneous JobIDs are longer than 32; compose: Postgres `bulk_create` of the logged 36-character jid
- `test_valkey_cache_live.py`: Django `ValkeyCache` against live Valkey when `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`; otherwise skipped
- `hpcperfstats/site/lib/machine/tests/test_web_pages_e2e.py`: Compose PostgreSQL page tests (the web workflow sets `HPCPERFSTATS_COMPOSE_NETWORK=1` and runs `manage.py migrate --noinput` first)
- `hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py`: Playwright: Django stub server, and the `/machine/*` and `/pub/*` SPA shells return 404 from WSGI per the nginx ownership contract
- `test_bokeh_job_list_embed_browser_e2e.py`: Bundled Bokeh in the Next-built static tree; fixtures in `hpcperfstats/site/frontend/test/fixtures/`

### Frontend and tools

Vitest picks up `*.test.{ts,tsx}` / `*.spec.{ts,tsx}` under `src/` and `app/`, plus `scripts/**/*.test.ts` and `test/**/*.test.{ts,tsx}` (`vitest.config.ts`); `test/vitest/setupTests.ts` stubs `HTMLElement.prototype.scrollIntoView` for jsdom.

- `src/api.test.ts`: fetch, CSRF and 401 paths
- `src/views/__tests__/Search.test.tsx`: Browse-by-time Calendar tab first and selected by default
- `src/views/__tests__/JobList.test.tsx`: Narrow-viewport Jobs/Charts tab behavior
- `src/views/__tests__/JobDetail.test.tsx`: Job data inner tabs, including per-plot tabs (Summary, Heatmap, rooflines) before Bokeh assertions, progressive plot loading, single- vs two-table metrics layout
- `src/components/LayoutPub.test.tsx`: Public `/pub/` navbar: branding, cluster label, Login to see individual job data -> `login_prompt?next=/machine/`
- `src/views/__tests__/PageClusterDashboard.test.tsx`: Yearly period keys before monthly after a grouping switch, the loading state, interactivity during refetch
- `src/components/ExtendedSearch.test.tsx`, `src/utils/normalize-job-list-histogram-entry.test.ts`, `src/utils/table-sort-a11y.test.ts`, `src/utils/job-list-route-title-context.test.ts`, `src/utils/jobMetricDisplayLabels.test.ts`, `src/utils/useDocumentTitle.test.ts`: Extended search, histogram entries, sortable-table a11y, route titles, short labels for the job-level metrics table, document titles

`hpcperfstats-tools/tests/` covers the client CLI and API helpers: `test_api_client.py`, `test_api_auth_headers.py`, `test_job_dataframe.py`, `test_sacct_gen_security.py`, `test_config.py` (INI `base_url`), `test_api_key_cache.py` (`~/.hpcperfstats-api` parsing) and `test_jobstats_cli.py` (`jobstats` formatting and `main()` exit codes).
Run them from `hpcperfstats-tools` with `python -m pytest`.

## Deploy and proxy contract tests

The rebuild scripts are in [Upgrade the stack](../how-to/operate/upgrade-the-stack.md#choose-a-rebuild-script) and the proxy contract is in [Compose services](compose-services.md#proxy); these tests lock them.

- `scripts/test_rebuild_pipeline.sh`, `scripts/test_rebuild_full_site.sh`: The pipeline `memory.high` write stays commented out in both rebuild scripts (`rebuild_pipeline.sh` builds `hpcperfstats-pipeline-refresh`)
- `hpcperfstats/tests/test_rebuild_proxy_script.py`: `scripts/rebuild_proxy.sh` with a fake podman: build while the old proxy serves, recreate only `proxy`, image id and `nginx -t` checks
- `hpcperfstats/tests/test_no_custom_pgo.py`: No custom Clang profile-guided bake surface; CPython uses upstream `--enable-optimizations`
- `hpcperfstats/site/hpcperfstats_site/tests/test_spa_static_root_heal.py`: `spa_static_root_heal` fingerprint sync when `machine/index.html` sha256 diverges
- `hpcperfstats/tests/test_valkeywait.py`, `hpcperfstats/site/hpcperfstats_site/tests/test_collectstatic_ignore_map.py`: `django_startup.sh` `collectstatic --noinput --clear`; `HPCStaticFilesConfig` omits `*.map`
- `hpcperfstats/site/hpcperfstats_site/tests/test_compress_static_sidecars.py`: `compress_static_sidecars` Brotli/Gzip siblings for `brotli_static`/`gzip_static` after collectstatic and heal (`copy-next-export` does not compress)
- `hpcperfstats/site/hpcperfstats_site/tests/test_staticfiles_ram_publish.py`: `staticfiles_ram_publish` mirrors `STATIC_ROOT` and `MEDIA_ROOT` onto tmpfs `staticfiles_ram` / `media_ram`
- `test_proxy_dockerfile_source_builds_nginx_with_pinned_deps`: The source-built nginx 1.31.x proxy image and its pinned libraries
- `hpcperfstats/tests/test_write_nginx_proxy_allowed_hosts_include.py`: `hps-proxy-allowed-hosts.inc` from `[DEFAULT] server=` via `services-conf/parse_hpcperfstats_proxy_hosts.py` and `write_nginx_proxy_allowed_hosts_include.py`
- `hpcperfstats/tests/test_write_nginx_api_route_allowlist_include.py`: `nginx-api-route-allowlist.inc` from `write_nginx_api_route_allowlist_include.py` matches `EXPECTED_ROUTE_TEMPLATES`; catch-all `location /api/ { return 404; }`
- `test_nginx_proxies_django_root_routes_by_exact_match_only`, `test_probe_paths_under_real_api_routes_not_covered`: Every Django location is an exact `=` match or an anchored `~ ^...$` regex
- `test_nginx_rejects_non_normalized_raw_uris_before_location_match`: The server-level `$request_uri` guard, so probes like `//oast.me//..` never match `location = /`
- `hpcperfstats/tests/test_write_nginx_resolver_include.py`: OCSP resolver include generation
- `hpcperfstats/site/hpcperfstats_site/tests/test_nginx_static_wsgi_contract.py`: The Django `Client` (WSGI) never resolves `STATIC_URL` paths or SPA shells: 404 for `/static/`, `/machine/`, `/pub/` and `/robots.txt`; edge HSTS and framing headers; OCSP and CSP includes (`copy-next-export.mjs` writes `nginx-csp-machine.inc` / `nginx-csp-pub.inc`); the hybrid compression location split, allowlist snapshots
- `hpcperfstats/site/hpcperfstats_site/tests/test_csp_report_endpoint.py`: The CSP report endpoint accepts a POST without a CSRF token

## Image build contract tests

`services-conf/pipeline_web.Dockerfile` builds from `debian:trixie` (`python-build`) and `debian:trixie-slim` (`hpcperfstats-base`); [Architecture](../explanation/architecture.md#runtime-image) explains the runtime image.
The build itself fails unless, on both ABIs, `numpy.show_config(mode="dicts")` mentions MKL after the numpy, numexpr and pandas installs and `numexpr.use_vml is True`; it also checks `ldd ... | grep libjemalloc` on each interpreter and that CPython `_zstd` links `/opt/zstd` and reports a `1.6.` version.

- `hpcperfstats/tests/test_dockerfile_python_deps_cache.py`: `python-dateutil` pinned in `project.dependencies` and installed with the rest wheel layer
- `test_dockerfile_mkl_source_stack.py`: numpy source build (`-Dblas=mkl`, `-Dlapack=mkl`, `cpu-baseline=native`), numexpr with Intel VML via an injected `site.cfg` and an unversioned `libmkl_rt.so` symlink for GNU ld, pandas and brotli against pip Intel MKL; numexpr and pandas install with `--no-deps` so pip cannot swap in a manylinux OpenBLAS numpy
- `test_dockerfile_jemalloc_cpython.py`: Both CPython builds (GIL `/opt/python3.14`, symlinked into `/usr/local/bin`, and free-threaded `/opt/python3.14t/bin/python` under supervisord) link jemalloc; `hpcperfstats-base` writes `/etc/ld.so.preload` and sets `ENV LD_PRELOAD` to `/opt/jemalloc/lib/libjemalloc.so.2`
- `test_dockerfile_pyspy.py`: py-spy in the image
- `test_pyproject_image_build_extra.py`: pandas meson needs `versioneer[toml]` from the `image-build` extra (`--no-build-isolation`)

Both CPython builds use upstream `--enable-optimizations` plus the `-march=native` ThinLTO flags from `services-conf/bake_clang_flags.sh`, with zlib-ng under `/opt/zlib-ng` (`ZLIB_COMPAT`) and zstd under `/opt/zstd`.
Build on the deployment architecture, because `-march=native` is not portable.
Container `python3` is the GIL interpreter, not the ingest interpreter.

## Frontend coverage

- Pages: Every view in `src/views/` has a test under `src/views/__tests__/`
- Components: Every top-level component in `src/components/` is exercised; `FilterMultiCombobox`, `LayoutRouteChromeReset` and `NavLink` only through other components' tests
- Hooks: Most of `src/hooks/` is fully line-covered; `useFocusTrap` and several `/pub/` and session hooks are only partly covered, and `use-pub-dashboard` / `use-test-login-user` are not exercised
- Utils: Catalog and drift guards for search params, metric labels, monitor events, robots prefixes
- Config: `src/api-paths.ts`, `src/config/publicRobotsAllowPrefixes.ts`
- A11y: `axeSeriousViolations()` in `Layout` (including Extended Search open), `JobList` (including the charts tab with histograms), `JobDetail`, `Search`, `PageApiKey`, `PageTestLogin`, `AdminMonitor`, `ExtendedSearch` (dialog shell) and `HistogramThumbnails` (thumbnail and open popover); new or materially changed page shells extend this set

## Pre-merge matrix

| Change touches                     | Minimum run                                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ |
| Pure Python utils / dbload helpers | `python scripts/run_tests.py --no-django` + targeted module                                      |
| Django API / machine models        | Host mock tests + `tests/run_db_pytest_workflow.sh` if DB semantics change                       |
| Ingest / archive / metrics         | Compose db pytest + contract tests; stall-related changes add the sync_timedb regression battery |
| Web UI / routes                    | Vitest + `tests/run_web_e2e_workflow.sh`                                                         |
| Metrics catalog / monitor types    | Unit + pipeline E2E when payloads change                                                         |

Before merging a change that spans layers, run [the complete check](../how-to/develop/run-tests.md#run-the-complete-check).
