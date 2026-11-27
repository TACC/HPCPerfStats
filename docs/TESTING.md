# Testing hpcperfstats

Local run logs and workflow artifacts: **`test_runs/`** (gitignored; see **`test_runs/README.md`**).

## Quick start

Compose-backed tests run with unprivileged **rootless Podman** and
**`podman-compose`**. The shared adapter fails closed unless all container
storage/cache/temp paths are under `/data`; see
**`hpcperfstats/cursor-rules/podman-runtime.mdc`**.

From the project root (directory containing `pyproject.toml`):

```bash
# Install test extras once
../.venv/bin/pip install -e ".[test]"

# Tests that need no database (deselects compose_db tests)
../.venv/bin/python3 scripts/run_tests.py --no-django

# Full pytest collection (default-DB tests skip on the host)
../.venv/bin/python3 scripts/run_tests.py
```

`--no-django` runs only the tests that need no database (no Postgres,
Valkey or compose); a green run is not a full check. See
[Complete testing](#complete-testing).

You can also run `pytest` directly:

```bash
# Unit-only path
../.venv/bin/python3 -m pytest -q hpcperfstats/tests

# Django app tests (requires DB/settings)
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/site/lib/machine/tests
```

### Python docstring + type-hint inventory gate

Every in-scope production module/class/def must pass the full Google/Napoleon surface in `python-docstring-and-typing-contract.mdc` (behavior summaries that are not name-echoes or upgrade-helper AI slop, Args/Returns/Yields, Raises when applicable, Examples with real usage on every def, module/class Attributes including private, subclass/override superclass prose, plus signature annotations). The inventory gate also rejects call-site deferrals, `Internal helper for` / `Compute or apply` templates, `Value polymorphism` boilerplate, and `>>> name(...)  # doctest: +SKIP` placeholder Examples. Check both repos from the HPCPerfStats checkout:

```bash
# Fail if any in-scope surface is non-compliant
../.venv/bin/python3 scripts/python_def_inventory.py --check

# Regenerate committed snapshot
../.venv/bin/python3 scripts/python_def_inventory.py --write \
  docs/python_def_inventory.json

# Gate unit tests (fixtures + full-tree green)
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/tests/test_python_def_inventory_gate.py

# Tools package (in-tree under hpcperfstats-tools/)
../.venv/bin/python3 -m pytest -q \
  hpcperfstats-tools/tests/test_python_def_inventory_gate.py
```

Optional bulk upgrade helper (review the diff; must not emit call-site / ellipsis-placeholder boilerplate; annotations may use `Any` with explicit Args prose; `# doctest: +SKIP` only with concrete example calls):

```bash
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py \
  --path-filter hpcperfstats/dbload --apply

# Rewrap / fill full surface to Ruff line-length (80 - main repo and
# hpcperfstats-tools)
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py \
  --force-docs --line-length 80 --apply
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py \
  --root hpcperfstats-tools \
  --force-docs --line-length 80 --apply
```

### Operator deploy scripts (not test workflows)

**`scripts/rebuild_frontend.sh`** rebuilds the SPA with npm and copies artifacts into the running **`web`** container's **`staticfiles_data`** staging volume without stopping **`pipeline`**, then re-publishes **static** ram so **`proxy`** `/srv/static` matches. Use it as an **optional SPA-only hot path**. The **primary** way to land SPA after code changes is a from-scratch image rebuild (`hpcperfstats-full` / `docker compose up --build`) plus recreating **`web`**: startup [`spa_static_root_heal`](../hpcperfstats/site/lib/spa_static_root_heal.py) fingerprint-syncs package frontend into the volume when `machine/index.html` sha256 diverges (see unit tests in `hpcperfstats/site/hpcperfstats_site/tests/test_spa_static_root_heal.py`). `django_startup.sh` runs **`collectstatic --noinput --clear`** so unused hashed files do not persist on the volume (host tests: `hpcperfstats/tests/test_valkeywait.py`, `hpcperfstats/site/hpcperfstats_site/tests/test_collectstatic_ignore_map.py`). After heal, [`compress_static_sidecars`](../hpcperfstats/site/lib/compress_static_sidecars.py) writes Brotli/Gzip siblings for nginx `brotli_static`/`gzip_static` (host tests: `hpcperfstats/site/hpcperfstats_site/tests/test_compress_static_sidecars.py`), then [`staticfiles_ram_publish`](../hpcperfstats/site/lib/staticfiles_ram_publish.py) mirrors `STATIC_ROOT` and `MEDIA_ROOT` onto tmpfs `staticfiles_ram` / `media_ram` (host tests: `hpcperfstats/site/hpcperfstats_site/tests/test_staticfiles_ram_publish.py`). Test overlay remaps those ram volumes to **`test_staticfiles_ram`** / **`test_media_ram`** (plain named volumes). Collectstatic omits `*.map` via [`HPCStaticFilesConfig`](../hpcperfstats/site/hpcperfstats_site/staticfiles_config.py).

**`scripts/rebuild_pipeline.sh`** rebuilds the shared **`hpcperfstats`** image (Python/pipeline; skips npm): preserves live **`STATIC_ROOT/frontend`**, stops/removes **web** + **pipeline**, takes **proxy** down (does not rebuild proxy image), builds **`hpcperfstats-pipeline-refresh`**, then starts **web**, **proxy**, and **pipeline** through the shared Podman adapter; leaves pipeline cgroup **`memory.high`** at the default **`max`**. Leaves **db** / **valkey** / **rabbitmq** running. Scratch cleanup on EXIT. **`scripts/rebuild_full_site.sh`** **`podman build`**s **web**, **proxy**, and **`db_pg18`** (image only; the profile **`pg18-migrate`** container is not started), then **`podman-compose down`** and **`up -d --no-build`**. Images bake ThinLTO via **`services-conf/bake_clang_flags.sh`**; CPython always uses upstream **`--enable-optimizations`**. The pipeline **`memory.high`** write is commented out in both scripts (cgroup default **`max`**); **`scripts/test_rebuild_pipeline.sh`** and **`scripts/test_rebuild_full_site.sh`** fail if it is re-enabled. Do not use **`podman-compose up --build`** for a full-site refresh. Fire-and-forget recreate without build: **`./scripts/recreate_web_pipeline.sh`**. Proxy-only rebuild: **`./scripts/rebuild_proxy.sh`** builds **`hpcperfstats-proxy`** while the old proxy serves, recreates only **proxy** (`--no-deps --no-build --force-recreate`), then checks the container image id and **`nginx -t`** (host tests with fake podman: **`hpcperfstats/tests/test_rebuild_proxy_script.py`**). Static: **`./scripts/test_rebuild_pipeline.sh`**, **`./scripts/test_rebuild_full_site.sh`**. Host pytest absence guard: **`hpcperfstats/tests/test_no_custom_pgo.py`**.

**`hpcperfstats/site/lib/machine/tests` on the host:** tests that need the default PostgreSQL database are **skipped** unless the environment sets **`HPCPERFSTATS_COMPOSE_NETWORK=1`** (the compose workflows `tests/run_db_pytest_workflow.sh` and `tests/run_valkey_cache_pytest_workflow.sh` export this inside the `web` container). Tests that only need Django settings and mocks use **`django_db(databases=[])`** and still run on the host. Pure mocks with **no** Django DB fixture use **`@pytest.mark.machine_unit_mock`** (see **`hpcperfstats/conftest.py`**) so pytest does not auto-attach **`django_db`** or skip them for missing Compose. During pytest, Django switches the default cache to **LocMem** unless **`HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`** (live Valkey workflow).

**Nginx static + SPA-shell contract (WSGI):** In production, nginx serves both **`/static/*`** and the SPA shells for **`/machine/*`** and **`/pub/*`**; the **`proxy`** image is **source-built** (**nginx 1.31.x** + jemalloc/zlib-ng/OpenSSL/ngx_brotli/static zstd; see **`test_proxy_dockerfile_source_builds_nginx_with_pinned_deps`**) and **`include`**s **`hps-proxy-allowed-hosts.inc`** generated from **`[DEFAULT] server=`** via **`services-conf/parse_hpcperfstats_proxy_hosts.py`** and **`write_nginx_proxy_allowed_hosts_include.py`** (see **`hpcperfstats/tests/test_write_nginx_proxy_allowed_hosts_include.py`**), forwards only an explicit allowlist of paths to Gunicorn (nested **`/api/`** via committed **`nginx-api-route-allowlist.inc`** from **`write_nginx_api_route_allowlist_include.py`** / **`EXPECTED_ROUTE_TEMPLATES`**; drift-locked by **`hpcperfstats/tests/test_write_nginx_api_route_allowlist_include.py`**; catch-all **`location /api/ { return 404; }`** - no blanket **`^~ /api/`**), and returns **404** for unknown paths at the edge. Every Django location is an exact **`=`** match, or an anchored **`~ ^...$`** regex for API templates with path converters; **`test_nginx_proxies_django_root_routes_by_exact_match_only`** and **`test_probe_paths_under_real_api_routes_not_covered`** lock that, and **`test_nginx_rejects_non_normalized_raw_uris_before_location_match`** locks the server-level **`$request_uri`** guard (404 for **`//`**, dot segments, backslash, **`;`**, encoded **`. / \`** or control bytes) that keeps probes like **`//oast.me//..`** from matching **`location = /`**. **`docker-compose.yaml`** bind-mounts **`./services-conf/nginx-main.conf`** onto **`/etc/nginx/nginx.conf`**, committed **`./services-conf/nginx.conf`** onto **`default.conf`** for **`proxy`**, mounts **`proxy_ssl_source:/mnt/ssl-source:ro`**, and bind-mounts shared snippets (**`nginx-static-files.conf`**, **`nginx-api-route-allowlist.inc`**, edge/CSP/proxy-common, and hybrid **`nginx-compress-proxy.inc`** / **`nginx-compress-static.inc`**) as the **only** runtime source for those snippets (**`proxy.Dockerfile`** does not **`COPY`** them). Host pytest **`hpcperfstats/site/hpcperfstats_site/tests/test_compress_static_sidecars.py`** locks Python **`.br`/`.gz`** sidecar generation after collectstatic/heal (`copy-next-export` does not compress). SPA **`/machine/`** and **`/pub/`** set **`open_file_cache off`**. TLS PEMs are **materialized at container start** from the settings mount via **`resolve_proxy_ssl_certs_dir.py`** in **`proxy_entrypoint.sh`** (fixed nginx paths under **`/etc/ssl/hpcperfstats/`**; no build-time bake, no **`additional_contexts`**, no **`.env`** / **`HPCPERFSTATS_SSL_CERTS_DIR`**; test overlay binds **`test_proxy_ssl_source`** to **`./tests/fixtures/proxy-ssl`**). Edge HSTS/framing/COOP/Permissions-Policy/Referrer-Policy live in **`nginx-edge-security-headers.inc`**; SPA CSP hashes are emitted by **`copy-next-export.mjs`** into **`nginx-csp-machine.inc`** / **`nginx-csp-pub.inc`** on the static volume; OCSP resolver generation is covered by **`hpcperfstats/tests/test_write_nginx_resolver_include.py`**. Gunicorn/Django must still not answer **`STATIC_URL`** or SPA shell routes directly. Host pytest includes **`hpcperfstats/site/hpcperfstats_site/tests/test_nginx_static_wsgi_contract.py`**, which asserts the Django **`Client`** (WSGI) does not resolve arbitrary **`STATIC_URL`** paths or SPA shell routes and snapshots the nginx allowlist snippets. For **local dev** matching that split, prefer compose with **`proxy`** or **`manage.py runserver --nostatic`** plus nginx for **`/static/`** and `/machine/*` shell delivery (see **`README.md`** and **`hpcperfstats/cursor-rules/nginx-static-url-prefix.mdc`**).

### Opt-in stress tests (massive `host_data`)

The directory **`tests/stress_host_data/`** is **outside** the default `pytest` `testpaths` (`hpcperfstats` only), so `python scripts/run_tests.py` and `pytest hpcperfstats` never collect it.

**Default way to run (rootless Podman Compose `db` + `valkey`, migrate, pytest inside `web`):**

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_stress_host_data_workflow.sh
```

The workflow sets **`HPCPERFSTATS_STRESS_HOST_DATA=1`** and **`HPCPERFSTATS_COMPOSE_NETWORK=1`** inside the container, and defaults **`HPCPERFSTATS_STRESS_HOST_DATA_ROWS`** to **400000** for a faster smoke (override for the full **34,560,000**-row case). The test drives the real **`update_metrics(..., rerun=True)`** path (readiness, pooled metrics, plot prewarm) and writes **`stress_report_<utc>.json`** under **`HPCPERFSTATS_STRESS_REPORT_DIR`** (default **`test_runs/stress/`** on the repo mount). The test passes **`timezone.localtime(job.end_time)`** into **`update_metrics`** so the job is selected under Django's **`end_time__date`** semantics (large 1 Hz windows can otherwise miss the queryset). Inside the container, **`tests/run_stress_host_data_inner.sh`** falls back to **`PYTHONPATH=/home/hpcperfstats`** plus installing pytest extras only if **`pip install -e`** fails on the bind mount. Options: **`--skip-build`**, **`--keep-env`**; extra pytest args after `--`.

**Scale / report env** (forwarded by `tests/run_stress_host_data_workflow.sh`; see `--help`): **`HPCPERFSTATS_STRESS_USE_TIME_SCALE`**, **`HPCPERFSTATS_STRESS_N_HOSTS`**, **`HPCPERFSTATS_STRESS_INTERVAL_SEC`**, **`HPCPERFSTATS_STRESS_DURATION_SEC`**, **`HPCPERFSTATS_STRESS_JID`**, **`HPCPERFSTATS_STRESS_REPORT_DIR`**, **`HPCPERFSTATS_STRESS_EXPLAIN`**, **`HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY`**, **`HPCPERFSTATS_STRESS_SAMPLE_PATH`**.

### Opt-in `update_metrics` diagnosis (mixed-scale jobs, phase timings)

Seeds two jobs ending the same local calendar day: **small** in-window `host_data` scale **100-300** rows (default **10x15 = 150**) and **large** **300-5000** rows (default **25x32 = 800**), runs **`update_metrics_for_dates`**, and asserts **`LAST_UPDATE_METRICS_DIAGNOSTICS`** phase totals when **`HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS=1`**.

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_update_metrics_diagnosis_workflow.sh
```

Options: **`--skip-build`**, **`--keep-env`**; extra pytest args after **`--`**. Tunables (forwarded by the workflow): **`HPCPERFSTATS_UM_DIAG_SMALL_HOSTS`**, **`HPCPERFSTATS_UM_DIAG_SMALL_STEPS`**, **`HPCPERFSTATS_UM_DIAG_LARGE_HOSTS`**, **`HPCPERFSTATS_UM_DIAG_LARGE_STEPS`**.

**Artifact:** `tests/run_update_metrics_diagnosis_inner.sh` sets **`HPCPERFSTATS_UM_DIAG_JSON_OUT`** (default **`test_runs/diagnosis/update_metrics_diagnosis.json`** on the bind-mounted repo) so a copy of the JSON report is written next to the tree, not only under pytest's temp directory. Override the path by exporting **`HPCPERFSTATS_UM_DIAG_JSON_OUT`** before invoking the workflow.

**Throughput / backlog tuning (production):** set absolute **`[PIPELINE] sync_ingest_pool_processes`**, **`metrics_pool_processes`** (metrics + plot/detail prewarm on the same pool), **`listend_db_ingest_pool_processes`**, and **`[PORTAL] gunicorn_workers`** against Django **`max_connections`**. Ready-queue depth is **`metrics_scheduler_ready_queue_target`** alone (default **100**). With **`metrics_idle_slot_supplement_enabled=yes`**, the compute session uses a pool-sized sliding feeder and may fill idle slots from the ready queue using **`estimated_sample_count`** soft/hard caps (`metrics_supplement_sample_soft_max` / `metrics_supplement_sample_hard_max`, defaults **10000** / **80000**; RC-E stops when no original-batch work remains in flight). Optional **`metrics_compute_batch_max_window_s`** / **`metrics_compute_batch_max_single_job_s`** still limit how many long-window jobs share one primary compute batch; **`metrics_compute_watchdog_s`** and **`metrics_compute_total_watchdog_s`** treat metrics+prewarm as one batch wall-time unit; **`metrics_deferred_not_ready_*`** tune readiness backoff.

**Stall triage counters:** scheduler logs and diagnosis JSON include queue + compute visibility: **`ready_enqueued_total`**, **`ready_dequeued_total`**, **`inflight_jids`**, **`attempted_total`**, **`compute_batches_total`**, **`batch_compute_exceptions_total`**, and **`per_jid_fallback_failures_total`**, in addition to split readiness/deferred counters (**`proxy_not_ready_jids`**, **`strict_not_ready_jids`**, **`strict_ready_jids`**, **`strict_cooldown_skips`**, **`deferred_not_ready_queue_size`**, **`deferred_not_ready_due_now`**, **`deferred_quarantined_jids`**). The once-a-minute **`metrics census`** line prints them grouped (`queue=`, `readiness=`, `deferred=`, `batch=`, `fail=`; `docs/UPDATE_METRICS_STATE_MACHINE.md` section 7), with the mid-batch heartbeat as **`batch=phase:,done:c/n,age_s:,idle_s:`**.

**Idle-slot fill triage:** while a large original-batch jid stays in metrics or prewarm, smaller ready-queue jobs should appear as additional in-flight work (sample estimate under soft/hard caps). If the pool idles with a non-empty ready queue of small jobs and large originals still inflight, check **`metrics_idle_slot_supplement_enabled`** and sample caps. When only supplement work remains, RC-E must stop pulling new ready-queue jobs.

**Thread reset / prewarm stall:** `MetricsPrewarmStallError` at partial `completed=k/n` must keep partial successes, soft-fail the rest, detach the current executor, and lazily create a fresh titled pool before the next batch (do not wipe results to `[]`). `test_update_metrics_architecture_contract.py` rejects metrics process pools, process-only timers/reaping, and retired metrics recycle INI keys, and locks `configure_blas_thread_env()` before Django/metrics numpy imports.

**Slow batch vs hang:** `inflight_jids ~ ready_dequeued_total` with **`compute_batches_total=0`** / **`processed_total=0`** for a long window may be a **slow first compute batch** (metrics and/or **`pipeline_required`** prewarm under DB load), not a dead pool. Confirm **`[thread:metrics-pool]`** titles under `update_metrics.py [main]`, watch the census **`batch=phase:,done:c/n,age_s:,idle_s:`** (rising `done` and small `idle_s` = slow, not hung), then wait for `compute batch N ... compute_batch_elapsed_s=... next_batch_cap=...`. **`metrics_compute_watchdog_s`** downshifts **`next_batch_cap`** from **metrics+prewarm `batch_wall_s`** (default total-wall INI `metrics_compute_total_watchdog_s=0` is not required for that downshift).

If progress remains zero for a long window **after** heartbeats show no advancing `completed_jids` and workers are dead/missing, the scheduler emits **`stall_exit_triggered=1`** and classifies the reason in **`stall_reason`**:
- **`no_ready_candidates`**: starvation at readiness/candidate stage (listing not finished and no ready enqueue / mid-batch / inflight progress for `STALL_EXIT_AFTER_SECONDS`). When listing is finished and only deferred not-ready jids remain, the pass ends normally once the consumer is idle (no stall exit; those jids are retried next pass). Must **not** fire while `ready_queue` is non-empty, `inflight_jids > 0`, or mid-batch `compute_batch_completed_jids` / `ready_enqueued_total` is still advancing - a long first metrics+prewarm batch with rising heartbeat `completed_jids` is a slow batch, not this stall.
- **`listing_query_failed`**: unexpected exception escaped listing/`_fill_ready_queue` (statement-timeout recovery should catch `OperationalError` inside fill; this stall is the producer safety net). Partial `local_ready` is still flushed before exit.
- **`compute_stuck_inflight`**: queue drained but work remains inflight.
- **`compute_all_failed`**: compute keeps attempting but every job fails.

**Candidate listing SQL (two-phase):** Phase A `_jobs_queryset` keyset pages must **omit** correlated `live_distinct` `host_data` aggregates and plot/detail `encode(sha256...)` fingerprints (cheap `md_count` / `stale_null` over current catalog metric names / gate-failure / `metrics_dirty_at` stamp only). Late `host_data` for a finished job is signalled by `job_data.metrics_dirty_at` (set by `sync_timedb` ingest via `mark_jids_metrics_dirty`, cleared by the metrics persist). Phase B `_jobs_queryset_artifact_catchup` + `_page_rows_needing_artifact_refresh` lists artifact-only jobs via cheap existence then bounded page FP checks. Empty-pass `listed=` census uses cheap Phase A only (not live-refresh backlog). Host unit coverage: `test_jobs_queryset_listing_sql_omits_live_distinct_and_sha256`, `test_jobs_queryset_selects_jobs_dirtied_by_late_host_data`, `test_iter_date_listing_pks_runs_phase_a_then_phase_b`, `test_jobs_queryset_still_lists_artifact_only_via_phase_b`, `test_census_listed_avoids_live_distinct_and_fingerprint_sql`, `test_sliding_supplements_invoke_on_supplements_taken` in `hpcperfstats/site/lib/machine/tests/test_update_metrics.py`.

**Backfill lane (out-of-window recompute):** behind the fresh window the scheduler runs a slot-capped backfill lane (`docs/UPDATE_METRICS_STATE_MACHINE.md` section 4.5): dirty old jobs first, then every job whose `job_data.metrics_version` predates `METRICS_CONTRACT_VERSION`, newest first with a persisted cursor (`metrics_scheduler_state`), skip-instead-of-defer readiness and a quiescence partial-final rule. Host unit coverage: `test_backfill_*` in `hpcperfstats/site/lib/machine/tests/test_update_metrics.py` (listing SQL, cursor, slot cap + fresh priority, skip/quiescence, failed-final, CLI + census), `hpcperfstats/analysis/metrics/tests/test_metrics_persist_provenance.py` (version / coverage stamps, skip recorder, failed-final persist), `test_mark_jids_metrics_dirty_stamps_never_processed_jobs_outside_window` in `test_metrics_sample_persist.py`, `test_0043_*` in `test_migrations.py`, `hpcperfstats/tests/test_update_metrics_state_doc.py` (doc drift guard), and the `metrics_backfill_*` INI getters in `hpcperfstats/tests/test_conf_parser.py`. Real-PostgreSQL verification in the dev container (seeds old jobs, drives the lane listing, cursor, skips, census and the widened dirty stamp):

```bash
T=hpcperfstats/site/lib/machine/tests
tests/run_dev_container_pytest.sh \
  "$T/test_update_metrics_backfill_compose.py" -q
```

Operator CLI: `update_metrics.py --backfill-status` (census) and `update_metrics.py --backfill-finalize-partials [--before YYYY-MM-DD]` (compute still-skipped old jobs now).

When **`stall_exit_triggered=1`**, **`update_metrics.py`** logs the stall summary, exits with code **1**, and **supervisord** restarts the process. Without a stall, the supervised default (no CLI dates) runs passes back to back in one process (`_run_continuous_scheduler`; idle `metrics_scheduler_pass_interval_s`, default 60 s) and refreshes `/pub/` EF artifacts on a background `_PublicEfRefresher` thread every `metrics_public_ef_interval_s` (default 3600 s); explicit-date runs keep the single blocking pass followed by the 600 s sleep. Host unit coverage: `test_main_sleep_after_runs_passes_in_process`, `test_main_default_runs_continuous_scheduler`, `test_public_ef_refresher_refreshes_and_records_stats`.

**One-shot job recalculate:** `update_metrics.py --jid <JID>` (also `--jid=<JID>`) invalidates Valkey/DB plot+detail artifacts and per-jid derived caches for that job, then recomputes metrics plus detail/plot artifacts and exits **0**/**1** without entering the date-range scheduler or post-run sleep. Do not combine `--jid` with positional date args. Host unit coverage: `test_main_jid_*` / `test_parse_jid_cli_arg_forms` in `hpcperfstats/site/lib/machine/tests/test_update_metrics.py`.

Optional env: **`HPCPERFSTATS_STRESS_PLOT_SEC`** (with manual plot sanity), **`HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`**, **`HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`**, **`HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL`** (seconds; `0` disables cached window `COUNT(*)` for large-job gating), **`HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S`** / **`HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS`** (plot aggregate hostxtime chunks and materialised-row budget for design capacity **`5000x48x60`** host-samples; INI `metrics_plot_aggregate_time_slice_s` / `metrics_plot_aggregate_max_host_time_points`), **`HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST`** (`1` restores `unnest(host_list)` live distinct SQL; emergency rollback only - see `get_live_distinct_use_legacy_hostlist()` docstring), **`HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER`** (default on; `0`/`no`/`false` turns it off, so the **`update_metrics.py`** script entrypoint runs one pass over the date range and exits instead of entering the continuous scheduler or the post-pass sleep).

### Columnar `update_metrics` path: golden corpus and Codon kernels

The metrics / Job Detail / plot path is pandas-free (rule
`hpcperfstats/cursor-rules/update-metrics-columnar-codon-contract.mdc`): one
COPY BINARY fetch per job -> `JobFrame` -> NumPy reference reductions
(`lib/columnar/segments.py`) or the baked Codon kernels
(`hpcperfstats/analysis/metrics/codon/`, pyext `hpcperfstats_codon_metrics`).

| Check | Command | Where |
|---|---|---|
| Unit kernels + bridge | `pytest -q hpcperfstats/analysis/metrics/tests/test_columnar_segments.py .../test_columnar_kernels.py .../test_columnar_bridge.py .../test_jobframe.py .../test_copy_decode.py .../test_copy_fetch.py` | host |
| No pandas on the path | `pytest -q hpcperfstats/tests/test_update_metrics_import_graph.py` | host |
| Monitor value sentinels (reasons, transient/static folds, gap fill, audience split, Admin Monitor findings) | `pytest -q hpcperfstats/analysis/metrics/tests/test_metrics_sentinel_reasons.py .../test_sentinel_classify_series.py .../test_metrics_sentinel_aggregation.py .../test_fill_interior_gaps.py hpcperfstats/site/lib/machine/tests/test_metrics.py hpcperfstats/site/lib/machine/tests/test_job_detail_openapi_wire_contract.py hpcperfstats/site/lib/machine/tests/test_admin_monitor_telemetry_health.py` | host |
| Bake contract (Dockerfile / supervisord / runner) | `pytest -q hpcperfstats/tests/test_codon_metrics_bake_contract.py` | host |
| Golden parity (series, metric rows, detail, Bokeh JSON) | `HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1 pytest -q hpcperfstats/analysis/metrics/tests/test_columnar_golden.py hpcperfstats/analysis/metrics/tests/test_bokeh_from_arrays.py` | host (corpus mounted) |
| Codon vs NumPy, GIL off, threads, golden frames | `HPCPERFSTATS_CODON_METRICS_REQUIRED=1 tests/run_dev_container_pytest.sh --ft hpcperfstats/analysis/metrics/tests/test_codon_kernels.py -q` | dev container (3.14t) |
| Thread scaling (>= 0.6x linear at 8 threads per kernel) | `tests/run_dev_container_pytest.sh --ft --exec '$PY' scripts/metrics_codon_scaling_report.py run` then `scripts/metrics_codon_scaling_report.py --check` | container run, host check |

The golden corpus lives **outside git** at `HPCPERFSTATS_METRICS_GOLDEN_DIR`
(dev host default `/data/user/sharrell/hpcperfstats-metrics-golden`; the
runner mounts it): `manifest.json` (`pandas_path_commit`), `edge_jobs.json`,
`jobs/<jid>.golden.json.gz` (pandas-path metric rows, detail payloads, plot
JSON, pre-Bokeh series) and `jobs/<jid>.frame.npz` (the JobFrame). Golden
tests skip with a reason when the corpus is absent and fail only with
`HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1`. Rebuild with
`scripts/metrics_golden_corpus.py seed-exemplars / seed-job-data /
rewrite-host-suffix / seed-edge-jobs / tag-edge-jobs / capture / verify
--min-jobs 27` inside `tests/run_dev_container_pytest.sh --exec`.

DB-backed metrics checks read the corpus jobs from the
hpcperfstats-dev database, not from the fixtures. If that database has been
reset or emptied (`job_data` count 0), rebuild every corpus job from the raw
exemplars and check that each manifest job has its `job_data` and
`host_data` rows. The exemplars are 7.3 GB under
`/data/user/sharrell/metrics-corpus/raw`. Mount them read-write, because
ingest writes `*.fnctl.lock` sidecars beside each file.

```bash
X='$PY scripts/metrics_golden_corpus.py'
X="$X reseed-dev-db --raw-root /corpus/raw"
HPCPERFSTATS_DEV_BASE_IMAGE=<image id> \
  tests/run_dev_container_pytest.sh --ft \
  --mount /data/user/sharrell/metrics-corpus/raw:/corpus/raw:rw \
  --exec "$X"
```

When a manifest job cannot be served, `reseed-dev-db` exits 1 and prints
`missing=` followed by each `<jid>:job_data` / `<jid>:host_data` gap.
It applies no migrations, so run `manage.py migrate` first when the dev
schema is behind.

Tolerances: base `rtol=1e-9`, `atol=0`, NaN-equal, ints/strings exact. The
looser per-metric allowances (pandas-path float4 SQL sums `1e-5`, the
conv-less GPU link/mem-bw cache scale, PostgreSQL float4 text rounding,
Bokeh JSON floats at `1e-5`) are listed in the rule and written to
`test_runs/columnar_golden_deviation.json` by the golden test.

Before a bake is available in the image, build the metrics pyext in a
`codon-build` container with the host Codon mounted at `/opt/codon`
(`CODON_SRC=.../analysis/metrics/codon CODON_OUT=<dir>
build_codon_metrics_ext.sh`) and point
`HPCPERFSTATS_CODON_METRICS_BUILD_DIR=<dir>` at it; the runner mounts it at
`/opt/hpcperfstats/codon_metrics`. In the image the scheduler refuses to
start on `/opt/python3.14t` without `hpcperfstats_codon_metrics.so`
(`codon_metrics status=disabled ...` then `SystemExit`).

### Opt-in pipeline E2E (RabbitMQ -> listend -> sync_timedb -> metrics -> live web)

The directory **`tests/pipeline_e2e/`** is outside default `pytest` `testpaths` (`hpcperfstats` only). Tests are skipped unless **`HPCPERFSTATS_PIPELINE_E2E=1`** (the workflow sets this inside the container).

**Default entry point:**

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_pipeline_e2e_workflow.sh
```

This uses the project-scoped command assembled by
`tests/compose_test_cmd.sh` (`podman-compose --project-name
hpcperfstats-test` with the base and test overlay). It starts **db**, **valkey**,
and **rabbitmq**, migrates, runs the synthetic ingest/metrics phase, then starts
**web** for the endpoint and browser phases. The workflow installs Chromium
unless `--skip-playwright-install` is set. Its test-project host ports are fixed
at HTTP `18080`, HTTPS `18443`, and RabbitMQ `15673`; inherited development
ports (`8080`, `8443`, and `5673`) cannot make the projects contend.

**URL drift guard:** canonical template set lives in **`hpcperfstats/tests/urlconf_route_catalog.py`** as **`EXPECTED_ROUTE_TEMPLATES`**. **`hpcperfstats/tests/test_endpoint_route_snapshot.py`** asserts the live Django resolver matches that set. **`build_pipeline_http_endpoint_specs()`** in the same module expands every template to concrete paths; phase 2 drives each check via Playwright (**`page.goto`** for HTML/redirects, **`APIRequestContext`** for JSON APIs) with status and **`Content-Type`** checks. Adding a URL without updating the catalog + builder fails CI.

Options: **`--skip-build`**, **`--keep-env`**, **`--skip-playwright-install`**, **`--skip-browser`** (skips the phase 2 browser/navigation/API matrix; see script **`--help`**).

### sync_timedb throughput timing and scaling benchmark

Host-safe unit harness (accounting, winner selection, schema contracts; no compose):

```bash
cd HPCPerfStats
../.venv/bin/python3 -m pytest tests/sync_timedb_benchmark -q \
  --tb=short
```

Long compose/Podman study (free-threaded `/opt/python3.14t/bin/python` via
rootless Podman; see `podman-runtime.mdc`):

```bash
cd HPCPerfStats
tests/run_sync_timedb_benchmark_workflow.sh
```

Ingest-width screening (derived smoke corpus under
`test_runs/sync_timedb_bench/corpus_smoke`, Timescale via compose db/valkey,
no `update_metrics` in the timed window):

```bash
cd HPCPerfStats
# Optional CSV overrides; defaults are 1..96 widths and 2
# replicates.
# HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS=1,2,4,8
# HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=2
tests/run_sync_timedb_benchmark_workflow.sh --screening
```

Artifacts land in `test_runs/sync_timedb_bench/screening_*.json` (winner is a
screening hint only, not a production INI change).

Knee confirmation (derived steady corpus under
`test_runs/sync_timedb_bench/corpus_steady`, default widths `48,64,80,96`,
5 replicates, longer per-replicate timeout):

```bash
cd HPCPerfStats
# Ambient HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS / _REPLICATES from
# a prior
# --screening shell are cleared unless KNEE_ALLOW_SCREEN_ENV=1.
tests/run_sync_timedb_benchmark_workflow.sh --knee
```

Artifacts land in `test_runs/sync_timedb_bench/knee_*.json` (knee candidate
only; still not a production INI change).

Closed-book mid-size E2 timing (same steady corpus; residual <=5% recorded in
artifact):

```bash
cd HPCPerfStats
tests/run_sync_timedb_benchmark_workflow.sh --e2
```

Artifacts land in `test_runs/sync_timedb_bench/e2_closed_book_*.json`.

Supporting-knob sweeps at fixed ingest width **48** (sequential day-close /
archive / populate / bulk-create; >=3 replicates; `corpus_steady` by default):

```bash
cd HPCPerfStats
# Ambient SCREEN_WIDTHS/REPLICATES cleared unless
# KNEE_ALLOW_SCREEN_ENV=1.
# Optional: HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH=48
tests/run_sync_timedb_benchmark_workflow.sh --knobs
```

Artifacts land in `test_runs/sync_timedb_bench/knobs_*.json` (campaign candidates
only; not a production INI change).

E6 `parse_feed` paired A/B at fixed ingest width **48** (baseline arm, then
candidate arm after a `feed_line` product patch; >=5 replicates by default):

```bash
cd HPCPerfStats
# Ambient SCREEN_WIDTHS/REPLICATES cleared unless
# KNEE_ALLOW_SCREEN_ENV=1.
# Optional: HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH=48
# Optional: HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=5
HPCPERFSTATS_E6_ARM=baseline \
  tests/run_sync_timedb_benchmark_workflow.sh --e6
# Apply feed_line candidate patch, then:
HPCPERFSTATS_E6_ARM=candidate \
  tests/run_sync_timedb_benchmark_workflow.sh --e6
```

Artifacts: `test_runs/sync_timedb_bench/e6_arm_baseline_*.json` then
`e6_parse_feed_ab_*.json` (`retain` true/false per lower-CI gate). Still not a
production INI change.

E7 residual `proc_merge` / `build_df` paired A/B at fixed ingest width **48**
(baseline arm, then candidate arm after skip-redundant-dedupe + columnar
`take_proc` product patch; >=5 replicates by default; same retain gate as E6):

```bash
cd HPCPerfStats
# Ambient SCREEN_WIDTHS/REPLICATES cleared unless
# KNEE_ALLOW_SCREEN_ENV=1.
# Optional: HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH=48
# Optional: HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=5
HPCPERFSTATS_E7_ARM=baseline \
  tests/run_sync_timedb_benchmark_workflow.sh --e7
# Apply residual merge/build_df candidate patch, then:
HPCPERFSTATS_E7_ARM=candidate \
  tests/run_sync_timedb_benchmark_workflow.sh --e7
```

Artifacts: `test_runs/sync_timedb_bench/e7_arm_baseline_*.json` then
`e7_proc_build_ab_*.json` (`retain` true/false). Still not a production INI
change.

E8 `delta_s` / `collapse_s` hold-seconds A/B (retain is decided on the
per-hold seconds, not files/s), and the write-only `host_data` /
`proc_data` `bulk_create` vs COPY A/Bs (at least 100,000 rows):

```bash
cd HPCPerfStats
# Optional: HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=5
tests/run_sync_timedb_benchmark_workflow.sh --e8
# Optional: HPCPERFSTATS_HOST_INSERT_ROWS (default 100000) and
# HPCPERFSTATS_HOST_INSERT_REPLICATES (default 5)
tests/run_sync_timedb_benchmark_workflow.sh --host-insert
# Optional: HPCPERFSTATS_PROC_INSERT_ROWS / _REPLICATES (same
# defaults)
tests/run_sync_timedb_benchmark_workflow.sh --proc-insert
```

Artifacts: `test_runs/sync_timedb_bench/e8_delta_collapse_ab_*.json`,
`host_data_insert_ab_*.json` and `proc_data_insert_ab_*.json` (`retain`
per arm pair). Not a production INI change.

Loaded-48 continuous-refill soak at ingest width **48** (mixed
small/medium/large corpus size terciles; occupancy oracle after warm-up).
Default wall **6h**; use `0.1` for smoke:

```bash
cd HPCPerfStats
# Ambient SCREEN_WIDTHS/REPLICATES cleared unless
# KNEE_ALLOW_SCREEN_ENV=1.
# Optional: HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH=48
HPCPERFSTATS_LOADED48_HOURS=0.1 \
  tests/run_sync_timedb_benchmark_workflow.sh --loaded48
HPCPERFSTATS_LOADED48_HOURS=6 \
  tests/run_sync_timedb_benchmark_workflow.sh --loaded48
```

Artifacts: `test_runs/sync_timedb_bench/loaded48_arm_baseline_*.json`
(`occupancy_ok`, files/s). Patch index:
`docs/SYNC_TIMEDB_CAMPAIGN_PATCH_INDEX.md`.

Each run also keeps the soak child's output as a timestamped log,
`test_runs/sync_timedb_bench/loaded48_child_<utc>_<pid>.log` (path recorded as
`child_log` in the artifact). The artifact records `complete_files`, `orm_rows`
and `measure_s`, because the headline files/s uses `max(complete_files,
orm_rows)`. Set the **test-only** telemetry override to get parse-stage and
write-phase tokens on the `ingest file` lines (production uses INI
`sync_ingest_telemetry`), then run the production analyzer on the log:

```bash
cd HPCPerfStats
HPCPERFSTATS_SYNC_INGEST_TELEMETRY=1 \
  HPCPERFSTATS_LOADED48_HOURS=0.5 \
  tests/run_sync_timedb_benchmark_workflow.sh --loaded48 \
  --skip-build
../.venv/bin/python3 scripts/measure_pipeline_ingest_rate.py \
  --log-file \
  test_runs/sync_timedb_bench/loaded48_child_<utc>_<pid>.log
```

The analyzer needs Python 3.11+ (`datetime.UTC`). To see where threads are parked, add
`HPCPERFSTATS_LOADED48_STACKDUMP_S=30`: the child then writes an all-thread
Python stack dump every 30 s to `<child_log>.stacks` (opt-in, off by default).
`HPCPERFSTATS_LOADED48_GC_MODE=trace` logs a `gc_probe` line every 30 s (GC count
and summed pause seconds; on free-threaded CPython a collection parks every
other thread); `=off` also disables automatic GC for an A/B (memory grows, test
hosts only). `HPCPERFSTATS_LOADED48_MALLOC_TRIM=yes|no` is a test-only override of
INI `sync_ingest_malloc_trim_after_file` for the soak child (`no` skips the per-file and
mid-chunk `gc.collect()` + `malloc_trim`, `yes` forces them on; production uses the INI
key). `HPCPERFSTATS_LOADED48_MEM_TELEMETRY=yes` is a test-only override of INI
`sync_timedb_mem_telemetry`: the child log then carries `sync_timedb_mem_telemetry:`
lines with `inflight_raw_mib`, `budget_mib`, `peak_factor`, tree RSS and cgroup
`anon_mib`/`file_mib`, which is the evidence for `PEAK_CGROUP_PER_RAW_FILE_BYTE`.
`HPCPERFSTATS_TEST_PIPELINE_MEM=128g` (compose test overlay `mem_limit`, default `0` =
unlimited) gives the container a cgroup `memory.max`, which turns on the
`sync_process_tree_rss_*` roof and the in-flight raw-byte budget that are otherwise off
(`budget_mib=0`) in the harness. `HPCPERFSTATS_LOADED48_RSS_LIMIT_PCT=30` is a test-only
override of INI `sync_process_tree_rss_limit_cgroup_pct`; with the cap above it makes the
ingest RSS-pressure gate trip in a short soak (`queue_orchestrator rss_hold` /
`rss_release` lines and `skip_rss_pressure` in `fill_block=`).
`HPCPERFSTATS_LOADED48_BIG_CLONE_CAP=N` (test-only) limits how many times any corpus template of at
least 256 MiB is cloned into the soak (default unlimited); use it with a corpus that contains a
multi-GB real file (`HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS=test_runs/sync_timedb_bench/<corpus>`)
so a 14 GB file is not copied hundreds of times. Planting stops when every template is exhausted.
`HPCPERFSTATS_LOADED48_WRITE_POOL=N` and `HPCPERFSTATS_LOADED48_PARSE_QUEUE=N` (1-4096)
are test-only overrides of INI `sync_ingest_write_pool_processes` /
`sync_ingest_parse_queue_max` (default write pool is a quarter of the ingest width) for
write-bottleneck experiments. **Wait attribution:** with
`HPCPERFSTATS_SYNC_INGEST_TELEMETRY=1` (INI `sync_ingest_telemetry`) the `ingest file`
lines carry `thr_cpu_s`/`thr_runq_s`/`thr_sleep_s` (schedstat split of the ingest thread's
wall), per-stage `*_cpu_s`, `wq_put_s`/`wq_resid_s`/`wq_join_s`/`wq_n` (write-queue
handoff), `w_exec_s`/`w_cpu_s`/`w_runq_s` (writer thread), `slot_wait_s`, `copy_encode_s`
(Python COPY encoding) and `codon_primary`/`emit_start` (parse path); the 60 s census adds
`psi_*10`, `sys_runnable`, per-role `cpu_*`/`runq_*` cores, `pygc_*`, `boehm_*`, `wq_depth`
and `w_busy`. `tests/sync_timedb_benchmark/sample_phase2.sh OUT_PREFIX [interval]` samples
container CPU and Postgres wait events beside a soak, and
`scripts/analyze_ingest_wait_breakdown.py --log-file CHILD.log` /
`scripts/analyze_ingest_stacks.py --stacks CHILD.log.stacks` (needs
`HPCPERFSTATS_LOADED48_STACKDUMP_S=30`) turn the artifacts into tier tables. Host unit
tests that use the Django DB fixtures (for example `test_sync_timedb_host_data_insert.py`,
`test_sync_timedb_proc_data_insert.py`) error with `failed to resolve host 'db'` outside the
compose workflows; `zstd` for `test_sync_timedb_day_raw_removal.py` must be on `PATH`. To run
with a freshly baked Codon extension instead of the one in
the image, set `HPCPERFSTATS_CODON_PYEXT_DIR` to a directory containing the whole
`/opt/hpcperfstats/codon_parse` tree, and confirm the child log shows
`codon_parse status=enabled`.

Host-runnable unit modules for the ingest throughput path (no DB needed):
`test_codon_file_process_pool.py` (Codon worker-process pool: real `codon_process_worker` subprocess against the
importable fake `codon_worker_fake_mod.py`; COPY-frame IPC, large-frame reads, abandon/respawn, recycle by RSS,
host-row coalescing, 1 MiB window cap, `codon_worker_mem` lines), `test_sync_timedb_host_copy_vectorized.py` /
`test_sync_timedb_proc_copy_vectorized.py` (vectorized COPY encoders byte-identical to the per-cell path),
`test_sync_timedb_wait_telemetry.py`, `test_sync_timedb_ingest_rss_pressure.py`, and
`test_analyze_ingest_stacks.py` / `test_analyze_ingest_wait_breakdown.py` (soak analyzers).

Codon dynamic exclusion (`test_codon_exclude_parity.py`): the kernel/bridge/IPC cases run on
the host venv; the three real-pyext cases skip unless `HPCPERFSTATS_CODON_PYEXT_DIR` holds a bake
built from this tree **and** the interpreter is the free-threaded 3.14t (host `.venv` is not, so
they skip there). The bake and `tests/run_dev_container_pytest.sh --ft` command in the
parity paragraph below also run `test_codon_exclude_parity.py` against a real pyext.

Codon / CPython parity (`codon-python-parity-contract.mdc`): `hpcperfstats/tests/test_codon_parity_registry.py`
lists every twin implementation and its parity tests, and fails on an unregistered Codon export,
a NumPy reference without a twin, or a new direct CPython-parser call site.
`test_codon_canonical_parity.py` and `test_listend_codon_divert.py` compare the kernel source with
`IncrementalStatsParser` on the host for:
- legacy CTL/CTR decode;
- the resume `start_idx` gate (rows, marks, stamps);
- the `%jobs` arm across file-session windows;
- `proc`/`host_proc` rows;
- listend samples.

Its real-pyext cases run against a bake built from this tree:
`podman build --target codon-build -t localhost/hpcperfstats-codon-build:parity -f services-conf/pipeline_web.Dockerfile .`,
`podman cp` `/opt/hpcperfstats/codon_parse/.` into `test_runs/codon_parity/codon_parse`, then
`HPCPERFSTATS_CODON_PYEXT_DIR=/home/hpcperfstats/test_runs/codon_parity/codon_parse tests/run_dev_container_pytest.sh --ft hpcperfstats/tests/test_codon_canonical_parity.py hpcperfstats/tests/test_codon_exclude_parity.py -q`.

Codon process-pool soaks: the worker count equals the ingest width (`HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH`; with 1 MiB windows N=32
peaks at ~26 GiB combined worker RSS, ~52 GiB cgroup; with 16 MiB windows N=24 hit the 100g cap and OOM-killed a worker).
Workers log `codon_worker_mem` lines (RSS split, Boehm heap/free/unmapped, GC count, frame sizes) when ingest
telemetry is on. Compare Codon and CPython by DB `tup_inserted` over time, not completed-file MiB: dense files
expand to millions of rows and finish in bursts, and
`HPCPERFSTATS_LOADED48_PRECREATE_CHUNKS=<epoch_min>:<epoch_max>` pre-creates the daily `host_data`
chunks (a fresh DB otherwise serializes every first insert of a day under a hypertable-wide
`ShareUpdateExclusiveLock`). Planted queue clones rename their own `$hostname` per clone (verbatim
clones share keys and block each other's `ON CONFLICT`) and publish atomically via `.plant_tmp`. A killed
soak leaves the `hpcperfstats-test_*` volume behind: `podman volume rm` it before the next run. Check a
soak ~5 minutes after launch (files/min, quarantines, `parse_file failed`, OOM) instead of waiting for it.

Width sweep A/B (same continuous-refill harness; **report-only**): serial
**48 -> 64 -> 96**, each **0.1h smoke + 3h soak**; baseline = this-run 3h@48.
Must survive SSH disconnect - launch with `nohup setsid -f`:

```bash
cd HPCPerfStats
TS=$(date +%Y%m%dT%H%M%S)
LOG="test_runs/loaded-width-sweep-$TS.log"
PIDF="test_runs/loaded-width-sweep.pid"
nohup setsid bash tests/run_loaded_width_sweep_ab.sh \
  >"$LOG" 2>&1 < /dev/null &
echo $! >"$PIDF"
```

Artifacts: `test_runs/sync_timedb_bench/width_sweep_ab_*.json` (`gates` per
candidate width). Not a production INI change.

FT contention wave A/B at fixed ingest width **48** (one wave product patch at a
time; >=5 replicates by default). Set `HPCPERFSTATS_CONTENTION_WAVE` to a wave
id (`caches`, `park_resume`, `manifest_io`, `members_shard`, `claim_heap`,
`tar_ex`, `thread_id`, `pool_split`, `log_drain`, `discover`, `telem_tls`):

```bash
cd HPCPerfStats
# Ambient SCREEN_WIDTHS/REPLICATES cleared unless
# KNEE_ALLOW_SCREEN_ENV=1.
# Optional: HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH=48
# Optional: HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES=5
HPCPERFSTATS_CONTENTION_WAVE=caches \
  HPCPERFSTATS_CONTENTION_ARM=baseline \
  tests/run_sync_timedb_benchmark_workflow.sh --contention
# Apply that wave's candidate product patch, then:
HPCPERFSTATS_CONTENTION_WAVE=caches \
  HPCPERFSTATS_CONTENTION_ARM=candidate \
  tests/run_sync_timedb_benchmark_workflow.sh --contention
```

Artifacts: `test_runs/sync_timedb_bench/contention_<wave>_arm_baseline_*.json`
then `contention_<wave>_ab_*.json` (`retain` plus `gate` =
`throughput`|`no_regression`). Not a production INI change. Per-wave results:
`docs/SYNC_TIMEDB_THROUGHPUT_CAMPAIGN.md` CONTENTION row.

Derived corpus (identity rewrite; never mutates exemplars):

```bash
cd HPCPerfStats
../.venv/bin/python3 \
  scripts/derive_sync_timedb_benchmark_corpus.py \
  --source-dir /path/to/exemplars \
  --output-dir test_runs/sync_timedb_bench/corpus_smoke \
  --host-suffix .cluster_name.domain.edu \
  --max-files 6
# Steady tier example (N smallest across exemplar trees):
../.venv/bin/python3 \
  scripts/derive_sync_timedb_benchmark_corpus.py \
  --source-dir /path/to/hpcperfstats02-exemplars \
  --source-dir /path/to/hpcperfstats04-exemplars \
  --source-dir /path/to/hpcperfstats01-exemplars \
  --output-dir test_runs/sync_timedb_bench/corpus_steady \
  --host-suffix .cluster_name.domain.edu \
  --max-files 24
```

Campaign ledger: `docs/SYNC_TIMEDB_THROUGHPUT_CAMPAIGN.md`.

### Pool-size printout (`cpuset_budget_bench.py`)

`tests/pipeline_e2e/cpuset_budget_bench.py` prints the absolute INI pool
sizes that `conf_parser` resolves (`sync_ingest_pool_processes`,
`metrics_pool_processes`, `gunicorn_workers`,
`listend_db_ingest_pool_processes`) as one dict, for operator comparison. It takes no
arguments:

```bash
cd HPCPerfStats
../.venv/bin/python3 tests/pipeline_e2e/cpuset_budget_bench.py
```

## Complete testing

A full check runs every runner below, in this order, from `HPCPerfStats/`.
`scripts/run_tests.py --no-django` alone is not one: it deselects every
test that needs the default database.

```bash
# 1. Host suite (default-DB tests skip on the host)
../.venv/bin/python3 scripts/run_tests.py
# 2. Frontend unit tests, then the full static export that the
#    Bokeh browser workflow serves
(cd hpcperfstats/site/frontend && npm ci && npm test -- --run)
(cd hpcperfstats/site/frontend && npm run build)
# 3. hpcperfstats-tools package
(cd hpcperfstats-tools && ../../.venv/bin/python3 -m pytest -q)
# 4. Compose workflows, logged to
#    test_runs/test_run_log_podman_compose.md
tests/run_all_compose_workflows.sh
# 5. Git hook suites (commit stage, then push stage)
../.venv/bin/pre-commit run --all-files
../.venv/bin/pre-commit run --hook-stage pre-push --all-files
```

`tests/run_all_compose_workflows.sh` (`[log-path] [--skip-build]`) runs,
in order: `tests/run_db_pytest_workflow.sh` (Django/Postgres suite plus
browser E2E), `tests/run_valkey_cache_pytest_workflow.sh`,
`tests/run_web_e2e_workflow.sh`, `tests/run_pipeline_e2e_workflow.sh`,
`tests/run_update_metrics_diagnosis_workflow.sh`,
`tests/run_stress_host_data_workflow.sh` (400,000 rows),
`tests/run_security_audit_workflow.sh` and
`tests/run_bokeh_browser_workflow.sh`. To run one of them alone, see
[the compose-backed gate](#3-full-compose-backed-gate-django-db-playwright-browser-e2e-live-valkey).
Monitor (C) tests are separate: see `monitor/README.md`.

## Testing on Linux (rootless Podman + full suite)

### 1. Verify the runtime and `/data` policy

Install Podman and `podman-compose` with DNF, configure subordinate IDs and
rootless storage as described in `README.md`, then verify:

```bash
F='{{.Host.Security.Rootless}} {{.Store.GraphRoot}}'
F="$F {{.Store.VolumePath}}"
podman info --format "$F"
podman-compose version
```

Expected: rootless is `true`; graph/image/volume/cache/temp paths resolve below
`/data/podman/$USER`. Host npm, pip, Playwright, XDG, and temporary paths resolve
below `/data/user/$USER`.

### 2. Python and frontend dependencies

From the `HPCPerfStats/` directory that contains `pyproject.toml`, use the canonical workspace virtualenv one level up:

```bash
../.venv/bin/pip install -e ".[test]"
```

For Vitest:

```bash
cd hpcperfstats/site/frontend && npm ci
```

`npm ci` / `npm install` install dependencies only; Bokeh is the stock **`@bokeh/bokehjs`** UMD (`bokeh.min.js`) synced to **`public/vendor/`** by **`scripts/sync-bokeh-vendor.mjs`** (via `write-site-identity`) and loaded by **`src/bokehjs-bundle.ts`** as a same-origin script - Turbopack must not bundle Bokeh sources (package `main` bare imports and ESM rewrites both break model registration). On any **`@bokeh/bokehjs`** version change, follow **`hpcperfstats/cursor-rules/bokeh-version-and-vendor-patch-upgrade.mdc`** (JS/Python pin sync, Vitest, Playwright Bokeh embed test).

The SPA pins Bokeh via **`@bokeh/bokehjs`** in `package.json`; keep its version aligned with the **`bokeh==...`** pin in `pyproject.toml` so `json_item` embeds stay compatible.

**Frontend stack:** Next.js 16 App Router (static export, Turbopack), strict TypeScript 6 (`typescript-eslint` peer holds TypeScript below 6.1), TanStack Query, Orval 8 + Zod 4 (from committed `hpcperfstats/site/openapi/openapi.yaml`), React Hook Form + Zod, Vitest 5 + RTL.

**Production static export** (OCI image built by Podman,
`scripts/rebuild_frontend.sh` - omits test-only routes such as
`bokeh-playwright-smoke/`):

```bash
cd hpcperfstats/site/frontend && npm ci && npm run build:prod
```

**Full static export** (local dev, CI before Playwright Bokeh bundle test):

```bash
cd hpcperfstats/site/frontend && npm ci && npm run build
```

See **`hpcperfstats/cursor-rules/frontend-prod-test-build-boundary.mdc`**. Test-only source lives under `hpcperfstats/site/frontend/test/` (Vitest mocks, wire-audit fixtures, Bokeh JSON fixtures).

Regenerate API client after OpenAPI changes:

```bash
(cd hpcperfstats/site && ../../../.venv/bin/python3 manage.py \
  spectacular --file openapi/openapi.yaml --format openapi)
(cd hpcperfstats/site/frontend && npm run generate:api)
../.venv/bin/python3 -m pytest \
  hpcperfstats/site/lib/machine/tests/test_openapi_schema_drift.py
```

### 3. Full compose-backed gate (Django DB, Playwright browser E2E, live Valkey)

All commands below assume your current directory is **`HPCPerfStats/`** (the one with `docker-compose.yaml`).

**Test compose overlay:** Every workflow sources
**`tests/compose_test_cmd.sh`**, which verifies rootless Podman, creates missing
local files from their `.example` templates, and invokes
**`podman-compose --project-name hpcperfstats-test`** with the base and test
overlay. The overlay replaces production binds with isolated test volumes and
fixture TLS. Production uses project `hpcperfstats`; the long-running local
development stack uses `hpcperfstats-dev`.

| Step | Command | What it covers |
|------|---------|----------------|
| 1 | `tests/run_db_pytest_workflow.sh` | Resets compose volumes, builds `web`, starts **db** + **valkey**, migrates dev DB, installs **Playwright/Chromium** in the container, runs **`pytest -q hpcperfstats`** (entire tree, including **`test_web_pages_browser_e2e.py`** unless you pass `--skip-browser-e2e`). |
| 2 | `tests/run_valkey_cache_pytest_workflow.sh --skip-build` | Fresh compose session, Django **`test_valkey_cache_live.py`** against real **Valkey** (`HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`). |
| 3 | `tests/run_web_e2e_workflow.sh --skip-build` | Dedicated compose session: **`migrate`** + **`HPCPERFSTATS_COMPOSE_NETWORK=1`** + **`test_web_pages_e2e.py`**, **`test_web_pages_browser_e2e.py`**, **`test_nginx_static_wsgi_contract.py`** (isolated web-path parity vs step 1). |
| 4 | `tests/run_pipeline_e2e_workflow.sh --skip-build` | Opt-in **full pipeline + browser** (`tests/pipeline_e2e/`): RabbitMQ ingest, **`sync_timedb once`**, **`update_metrics`**, then live **web** + Playwright endpoint matrix (see **Opt-in pipeline E2E** above). |
| 5 | `tests/run_stress_host_data_workflow.sh --skip-build` | Opt-in **`host_data`** stress (`tests/stress_host_data/`): seed + **`update_metrics`**, JSON report under **`test_runs/stress/`**, default **400000** rows (override row/time-scale env vars; see section above). |

The complete Podman matrix is:

1. `tests/run_db_pytest_workflow.sh`
2. `tests/run_valkey_cache_pytest_workflow.sh`
3. `tests/run_web_e2e_workflow.sh`
4. `tests/run_pipeline_e2e_workflow.sh`
5. `tests/run_update_metrics_diagnosis_workflow.sh`
6. `tests/run_stress_host_data_workflow.sh` (400,000-row orchestrator smoke)
7. `tests/run_security_audit_workflow.sh` (pip-audit + npm audit)
8. `tests/run_bokeh_browser_workflow.sh`

**Full orchestrator:** `tests/run_all_compose_workflows.sh` runs all eight in
that order and logs to `test_runs/test_run_log_podman_compose.md`.

Faster iteration after the first successful build:

```bash
tests/run_db_pytest_workflow.sh --skip-build
tests/run_valkey_cache_pytest_workflow.sh --skip-build
tests/run_web_e2e_workflow.sh --skip-build
tests/run_pipeline_e2e_workflow.sh --skip-build
tests/run_stress_host_data_workflow.sh --skip-build
```

Each workflow tears down only its project containers and named volumes unless
you pass `--keep-env`. Global Podman prune is forbidden on this shared server.

Manual cleanup from `HPCPerfStats/`:

```bash
podman-compose -p hpcperfstats-test \
  -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml \
  down -v --remove-orphans
```

If a follow-up script cannot resolve `db`, run the project-scoped teardown above
and retry. Do not remove unrelated networks.

### 4. In-tree tools package and SPA unit tests

From `HPCPerfStats/` (git checkout; tools live at `hpcperfstats-tools/`):

```bash
cd hpcperfstats-tools && ../../.venv/bin/python3 -m pytest -q
cd ../hpcperfstats/site/frontend && npm test -- --run
```

`hpcperfstats-tools/pytest.ini` disables the Django plugin (`-p no:django`) so in-tree runs do not inherit the parent checkout's `DJANGO_SETTINGS_MODULE`.

## Code coverage

Coverage settings live in `pyproject.toml` (`[tool.coverage.*]`). Typical commands:

```bash
# Tests that need no database only (not a full check)
../.venv/bin/python3 scripts/run_tests.py --no-django \
  --cov=hpcperfstats --cov-report=term-missing --cov-report=html

# Full tree including Django tests (run through the Podman Compose
# workflow)
../.venv/bin/python3 scripts/run_tests.py --cov=hpcperfstats \
  --cov-report=term-missing --cov-report=html
```

The `--no-django` run measures only tests that need no database (no
Postgres, Valkey or compose); see [Complete testing](#complete-testing).
HTML output is written to `htmlcov/` (gitignored by convention).

**React (Vitest + v8)** - from `hpcperfstats/site/frontend/`:

```bash
npm run test:coverage -- --run
```

Reports are written to `hpcperfstats/site/frontend/coverage/` (ignored via `hpcperfstats/site/frontend/.gitignore`).

**Bokeh embed (Vitest):** `src/components/BokehEmbed.tsx` defaults to viewport-gated embedding and a short post-`Document.idle` settle in production builds; Vitest skips deferral unless `deferEmbedUntilVisible` is set explicitly (see `src/utils/bokeh-embed-defaults.ts`). Targeted runs:

```bash
cd hpcperfstats/site/frontend && npm test -- --run \
  src/components/BokehEmbed.test.tsx \
  src/components/HistogramThumbnails.test.tsx \
  src/utils/bokeh-embed-defaults.test.ts
```

**`hpcperfstats-tools`** (in-tree client package under this checkout):

```bash
cd hpcperfstats-tools && ../../.venv/bin/python3 -m pytest \
  --cov=hpcperfstats_tools --cov-report=term-missing
```

## Best practices

Canonical guidance lives in **`hpcperfstats/cursor-rules/testing-best-practices.mdc`**. Summary:

### Test pyramid

| Layer | When | Runner |
|-------|------|--------|
| Unit | Pure helpers, mocked API views (`django_db(databases=[])`), SPA page shells with jest-axe (`axeSeriousViolations`) | `python scripts/run_tests.py --no-django`, Vitest `npm test -- --run` |
| Integration | Postgres, Valkey, Timescale | `tests/run_db_pytest_workflow.sh`, `tests/run_valkey_cache_pytest_workflow.sh` |
| E2E | Browser UX, nginx, full pipeline | `tests/run_web_e2e_workflow.sh`, `tests/run_pipeline_e2e_workflow.sh` |

Prove correctness at the **narrowest** layer first; escalate only when mocks cannot exercise the contract. `--no-django` covers the Unit layer only (no Postgres, Valkey or compose); a green run is not a full check ([Complete testing](#complete-testing)).

### Colocation and drift guards

- **Python (general):** `test_*.py` beside modules for small leaf packages, `hpcperfstats/tests/` for cross-cutting daemons, or a dedicated `tests/` package when the subtree has many modules.
- **`analysis/metrics/`:** all pytest modules under `hpcperfstats/analysis/metrics/tests/` only - not beside `update_metrics.py` or under `lib/plot/` / `lib/gen/`.
- **Frontend:** `*.test.{ts,tsx}` colocated under `hpcperfstats/site/frontend/src/`.
- Cross-layer registries (routes, metric labels, extended-search params) need **drift tests** that fail when source and consumer lists diverge.

### Refactor for testability

Split monolithic functions when it materially reduces mock surface - lock behavior with tests before extracting helpers (`testing-best-practices.mdc` + `refactor-dedup-priorities.mdc`).

### Pre-merge matrix

| Change touches | Minimum run |
|----------------|-------------|
| Pure Python utils / dbload helpers | `python scripts/run_tests.py --no-django` + targeted module |
| Django API / machine models | Host mock tests + `tests/run_db_pytest_workflow.sh` if DB semantics change |
| Ingest / archive / metrics | Compose db pytest + contract tests |
| Web UI / routes | Vitest + `tests/run_web_e2e_workflow.sh` |
| Metrics catalog / monitor types | Unit + pipeline E2E when payloads change |

Before merging a change that spans layers, run [Complete testing](#complete-testing).

Local git hooks: commit-stage **memray** growth smoke (`python-memory-leak-check`); tracemalloc remains offline/ad-hoc only - see **Optional memory profiling** below.

### `api.py` coverage

**100% line coverage** on `hpcperfstats.site.lib.machine.api` is enforced by host-side mock tests (LocMem cache, `django_db(databases=[])`):

- `test_api_helpers.py`
- `test_api_view_matrix.py`
- `test_api_coverage_gaps.py`
- `test_api_misc.py`
- `test_api_coverage_closure.py`

```bash
cd HPCPerfStats && ../.venv/bin/python3 -m pytest \
  hpcperfstats/site/lib/machine/tests/test_api_helpers.py \
  hpcperfstats/site/lib/machine/tests/test_api_view_matrix.py \
  hpcperfstats/site/lib/machine/tests/test_api_coverage_gaps.py \
  hpcperfstats/site/lib/machine/tests/test_api_misc.py \
  hpcperfstats/site/lib/machine/tests/test_api_coverage_closure.py \
  --cov=hpcperfstats.site.lib.machine.api \
  --cov-config=tests/coverage_api_py_line_only.ini \
  --cov-report=term-missing:skip-covered \
  --cov-fail-under=100 \
  -q
```

The dedicated `--cov-config` disables branch measurement so the gate tracks **line** coverage only (the project-wide `[tool.coverage.run]` setting stays `branch = true`).

### Frontend coverage inventory (Vitest)

| Area | Status |
|------|--------|
| Pages | Every view in `src/views/` has a test under `src/views/__tests__/` |
| Components | Every top-level component in `src/components/` is exercised; `FilterMultiCombobox`, `LayoutRouteChromeReset` and `NavLink` only through other components' tests |
| Hooks | Most of `src/hooks/` is fully line-covered; `useFocusTrap` and several `/pub/` and session hooks are only partly covered, and `use-pub-dashboard` / `use-test-login-user` are not exercised |
| Utils | Catalog/drift guards for search params, metric labels, monitor events, robots prefixes |
| Config | `src/api-paths.ts`, `src/config/publicRobotsAllowPrefixes.ts` |

Targeted runs:

```bash
cd hpcperfstats/site/frontend
npm test -- --run src/components/PageBreadcrumbs.test.tsx
npm test -- --run src/utils/extended-search-parameters.test.ts
npm run test:coverage -- --run
```

## Test layout

| Location | Description |
|---------|-------------|
| `scripts/lib/podman_runtime.sh` | Canonical rootless runtime, `/data` cache/storage validation, project names, and Podman health inspection. |
| `tests/podman_compose_teardown.sh` | Project-scoped `down -v --remove-orphans`; never global prune. |
| `tests/verify_podman_project_isolation.sh` | Positive-control regression proving teardown does not remove unrelated Podman assets. |
| `tests/run_podman_development_stack_verify.sh` | Starts/verifies the isolated `hpcperfstats-dev` stack on unprivileged ports. |
| `tests/pip_compose_test_extras_fallback.sh` | When `pip install -e ".[test]"` fails on a bind mount, `tests/run_pipeline_e2e_workflow.sh` sources this helper to install the Django / pytest / pytest-django / pytest-cov test floors. Other inner compose scripts use the same pins through `compose_inner_pip_install` in `tests/compose_inner_pip_install.sh`. |
| `hpcperfstats/tests/test_sync_timedb_parsing_canonical.py` | Canonical stats-line ingest (semantic PMC/IMC events, no CTL/CTR eventmaps). |
| `hpcperfstats/tests/test_sync_timedb_parsing_legacy.py` | Legacy ingest path (`map_hardware_counter_vals`, hex eventmaps, KNL type aliases). |
| `hpcperfstats/tests/test_sync_timedb_parse_efficiency.py` | Columnar schema compile, packed carry, unix-seconds collapse, streaming incremental chunks, listend one-sample kernel (host unit). |
| `hpcperfstats/tests/test_monitor_naming_resolve.py` | Dual-read probe order for `monitor_naming/resolve.py`. |
| `hpcperfstats/tests/test_monitor_analysis_typename_contract.py` | Monitor `stats_type.st_name` values vs `canonical.py` lists (`INTEL_IMC_STATS_TYPES`, `ARM_IMC_STATS_TYPES`, `INTEL_CORE_PMC_TYPES_ORDERED`) and roofline peak rows; skips when monitor sources are absent. |
| `hpcperfstats/tests/test_archive_compress.py` | Pure path helpers in `archive_compress.py` (detect format, tar/zst/gz siblings, member maps). |
| `hpcperfstats/tests/test_sync_timedb_startup_archive_scan.py` | Canonical startup snapshot coordinator helpers (host unit). `day_close` discover/seal/raw/tar-drop is covered by **`test_sync_timedb_queue_orchestrator.py`** + the `day_close` predicate contracts (rediscovered lock and remaining raw become ingest/append jobs; `day_close` = filesystem complete + minimum age) in **`test_sync_timedb_architecture_contract.py`**. |
| `hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py` | **`run_sync_timedb_queue_orchestrator`**: exclusive `archive_dir` flock, `from_parsed` wiring, no `run_sync_timedb_supervisor_loop` symbol, sliding-window ingest->append enqueue while other ingest inflight, `day_close` tar-drop when sealed + no closed raw, **day-close `on_handoff_to_ingest`** requeue, **space reclaim** (`post_seal` False, reseal-after-delete, `phase=done` dual `tar_drop`) (host unit). Quick run: `cd HPCPerfStats && ../.venv/bin/python3 -m pytest -q hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py`. |
| `tests/run_sync_timedb_day_close_soak_workflow.sh` | Day-close **`open_tar` / dual reclaim** soak without prod wall-clock. Default: host pytest (`tests/sync_timedb_day_close_soak/` + reclaim regressions). Opt-in compose: `HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1 HPCPERFSTATS_DAY_CLOSE_SOAK_COMPOSE=1`. Logs `test_runs/day-close-space-reclaim-soak-*.log`. |
| `hpcperfstats/tests/test_sync_timedb_ingest_file_timeout.py` | Idle-stall / `_run_ingest_timed` wiring and leftover wall-timeout absence (host unit). |
| `hpcperfstats/tests/test_leftover_retired_purge.py` | Deleted leftover no-op names (watchdog, deadline ContextVars, always-0 wall getters). |
| `hpcperfstats/tests/test_sync_timedb_archive.py` (member cache) | DB-complete ingest path: identity-keyed `get_existing_archive_members_for_daily_archive` cache, `daily_archive_has_member_with_size`, ingest **store-backed single-flight populate**, **bad sealed day skip**, L1 skip LRU, sealed point lookup, `invalidate_daily_archive_members_cache`. Drift guard: **`sync-timedb-ingest-pool-io-coordination.mdc`**. |
| `hpcperfstats/tests/test_sync_timedb_archive_members_coord.py` | In-process member-store populate + wait (`test_populate_store_single_flight_and_warm_lookup`, `test_sticky_skip_survives_reload`). |
| `hpcperfstats/tests/test_sync_timedb_job_queue.py` | In-process job-store reconstruct/discover/streaming helpers plus leftover host claim tests. Operator census must use `.sync_timedb_job_store.json` and `[thread:populate-pool]` (`test_operator_census_uses_disk_sidecars_and_thread_titles`). Django cache live Valkey remains `test_valkey_cache_live.py`. |
| `hpcperfstats/tests/test_sync_timedb_job_discover.py` | Streaming discover calendar-day resolver, ingest queue cap stop, mtime window, CLI date-range filter. |
| `hpcperfstats/tests/test_sync_timedb_tar_clone_append.py` | Clone append writer on a reflink filesystem (host `/tmp` XFS `reflink=1`; the suite fails rather than skips without reflink): GNU/tarfile roundtrip, 4 KiB data alignment, GNU b20 base clones every member, copy fallback and hung-copy idle kill, >8 GiB sparse member on a GNU-format tar, ENOSPC rollback, journal recovery (stale/corrupt/orphan), SIGKILL at every commit step, salvage of a truncated clone-written tar. |
| `hpcperfstats/tests/test_sync_timedb_append_tar_race.py` | `_append_to_tar` samples existence and appends (`clone_append`) under the write lock, never `tar -c`/`-r`; append jobs group per daily tar. |
| `hpcperfstats/tests/test_sync_timedb_subprocess_hardening.py` | zstd pipe drain, nested `already_held` write lock, pax convert path gone, decompress stderr capture, append copy fallback under progress idle kill. |
| `hpcperfstats/tests/test_sync_timedb_archive_members_store_required.py` | Asserts the process archive-members store is required (`require_process_archive_members_store`). |
| `hpcperfstats/tests/test_sync_timedb_file_complete_ingest_mark.py` | Durable file-complete mark helpers (empty/missing path, malformed sidecar, `db_skip` tokens, live-listend completeness). Host unit; no Postgres. |
| `hpcperfstats/tests/test_sync_timedb_zero_host_ingest_mark.py` | Zero-host mark helpers (gate on host rows written to the DB, empty archive dir, malformed entries). Host unit; no Postgres. |
| `hpcperfstats/tests/test_sync_timedb_day_close_cooperation.py` | Day-close yield/defer tracker, empty tar-path `normpath` (`.` ), write-lock backoff, chunk-day defer. Host unit mocks. |
| `hpcperfstats/tests/test_sync_timedb_host_itimes.py` | Mocked `host_data` Unix-second probes: empty second sets, 24h chunk bounds, overflow, statement-timeout overflow. Host unit; no Postgres. |
| `hpcperfstats/tests/test_sync_timedb_db_offload_high_confidence.py` | Compose-DB oracles for ingest DB offloads (proc peak upsert, batched second-presence). Run via `tests/run_db_pytest_workflow.sh -- hpcperfstats/tests/test_sync_timedb_db_offload_high_confidence.py` (`HPCPERFSTATS_COMPOSE_NETWORK=1`). |
| `hpcperfstats/tests/test_update_metrics_db_offload_medium_confidence.py` | Compose-DB oracles for metrics DB offloads (`job_arc` SQL bucket drop, live distinct PL/pgSQL, large-job time-slot COPY (each host per slot), `jid_table` index probes, plot sum function, bounds `time__lt` slices). Requires migration `0034_metrics_host_data_sql_functions`. Same compose runner as above. |
| `hpcperfstats/tests/test_sync_timedb_pipeline_edge_cases.py` | Cross-module empty/malformed/boundary units for parsing, `--jid` coercion, persistence envelopes, ingest timeout (always-0 walls), reconstruct bands, readiness live-on, append `peek_first`, idle progress. Host unit; mocks ORM/DB. |
| `hpcperfstats/tests/test_sync_timedb_*_harvest.py` | Host-unit harvest coverage: absence of deleted leftovers including the retired file-state machine (`test_sync_timedb_leftover_harvest.py`), the always-0 ingest-timeout shells and helpers (`test_sync_timedb_ingest_timeout_harvest.py`), and the removable-raw classify wrappers plus `prune_day_phases_hints` on sealed-only days (`test_sync_timedb_h14_n5_harvest.py`). No Postgres. |
| `hpcperfstats/tests/test_sync_acct.py` | Accounting ingest (`sync_acct_from_content`, restricted queues, bulk fallback, cache notify, dead-letter `--dead-letter-unimportable`, end-of-run dead-letter summary) with mocked ORM. |
| `hpcperfstats/tests/test_listend_drain.py` | RabbitMQ drain loop (ack/nack, empty queue) with mocked pika. |
| `hpcperfstats/tests/test_listend_dup_guard.py` | Raw-archive duplicate-sample guard: a run re-sent after newer samples is skipped, mixed body appends only new samples, distinct same-second / same-timestamp-other-jid samples kept, `$` rotation keeps the guard, stale-generation archive items not archived, `listend dup_samples` counters (redelivered/fresh, stale acks, nacks). Append throughput: `../.venv/bin/python3 scripts/bench_listend_archive_append.py` (ns/msg and MB/s per payload shape; scratch dir `test_runs/listend_dedupe/bench`). |
| `hpcperfstats/tests/test_listend_archive_pool.py` | Host-affine archive worker pool: per-host FIFO, ack/nack only after durable archive, drop-mode DB submit off the consume thread (archive threads = 2 x `listend_amqp_consumer_count`). |
| `hpcperfstats/tests/test_django_bootstrap.py` | `ensure_django()` thread safety: `django.setup()` runs once under a lock (concurrent listend DB threads), failures are not latched, reentrant calls no-op. |
| `hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage.py` | Window-coverage readiness helpers and legacy fallback (host unit mocks). |
| `hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage_compose.py` | Compose-backed defer->ready when early `host_data` is inserted (`tests/run_db_pytest_workflow.sh`). |
| `hpcperfstats/site/lib/machine/tests/test_job_data_jid_length.py` | Host `machine_unit_mock` asserts `job_data.jid` `max_length` is 512 and the logged heterogeneous JobIDs are longer than 32. Compose Postgres `bulk_create` of the logged 36-character jid via `tests/run_db_pytest_workflow.sh`. |
| `hpcperfstats/tests/` | Non-Django unit/integration tests (config parsing, service startup/health, listend behavior, sync helpers, cache/date/print/file-locking helpers, XALT models, API key mobile checks, dbload `zstd`/row-builder helpers; Django's `django.utils.timezone.utc` is restored in **`hpcperfstats_site/settings.py`** for Django 5+). `test_conf_parser.py` covers the absolute `get_metrics_pool_processes()` thread count, default `total_cores=40`, DB options, and sync/listend concurrency getters. `test_pg_connection_stats_command.py` exercises the `pg_connection_stats` management command with a mocked DB connection (no live Postgres) and groups backends by `application_name`. `test_pg_slot_budget.py` locks `estimate_django_pg_slot_peak()` (gunicorn x executor + listend + `metrics_pool` + ingest + `day_close` + reserve) and the 80% WARN against compose `max_connections=500`. `test_sync_timedb_archive.py` includes deferred-archive / atomic-seal helpers, `tar tf` integrity checks, zstd gzip restore, tar dedupe (largest wins per path), and helpers used for scheduled archive maintenance + raw-file removal (`get_existing_archive_members_for_daily_archive`, `remove_verified_archived_raw_files`, `validate_sealed_daily_archive_for_raw_removal`); integration tests call real `zstd` and skip when `zstd` is not on `PATH`. |
| `hpcperfstats/analysis/metrics/tests/` | Metrics/plot/gen unit tests (summary/roofline behavior, roofline peak inference from `host_data.type` schema keys, hover tooltips, shared job-window parsing, metrics helpers, Bokeh embed sizing). `test_update_metrics_architecture_contract.py` rejects metrics process pools, SIGALRM, zombie reaping, process-worker titles, and retired recycle INI, and requires `configure_blas_thread_env()` before numpy-pulling imports. `test_add_node_power_est_column.py` locks vectorized Summary `node_power_est_w` against the frozen iloc priority chain. `test_metrics_telemetry_bounds.py` locks `telemetry_first_time` / `telemetry_last_time` persistence during metrics batch writes. `test_per_interval_rate.py` covers node-imbalance variants (DRAM, LNET, GPU util/tensor) and GPU peak helpers. `test_job_for_metrics.py` and `test_summaryplot_no_data.py` exercise metrics/summaryplot edge cases. `test_job_metric_display_labels.py` and `test_metrics_add_arrays.py` cover label maps and small `metrics.py` helpers. `test_bokeh_job_embed.py` is a pure-Python unit test for `bokeh_job_embed.figure_embed_kw` (no Django). |
| `hpcperfstats/site/lib/machine/tests/` | Django + web tests (ORM/query/update helpers, job detail file-system llite vs NFS fallback, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). **`test_api_helpers.py`** and **`test_api_view_matrix.py`** add host-side `api.py` helper and view coverage with mocks. Includes `test_type_detail_api.py`, which asserts type-detail `host_data` ORM SQL does not reference `jid` (job scope is start/end time + accounting hosts) with `django_db(databases=[])` so it does not need a live DB. `test_api_coverage_gaps.py` hits additional `api.py` branches (cache invalidation, `host_plot`, job monitor, `sacct_ingest`, `job_detail`, `job_plots`) with mocks and locmem cache - no Postgres host required. `test_oauth2.py`, `test_test_login.py`, `test_cache_middleware.py`, `test_renderers.py`, and `test_update_xalt.py` cover OAuth helpers, dynamic cache TTL middleware, JSON NaN sanitization, and the XALT log script loop. `test_metrics.py` includes `test_job_metric_short_labels_cover_catalog`, which asserts every `job_metrics_catalog_entries()` metric has a matching entry in `job_metric_display_labels.JOB_METRIC_SHORT_LABELS` (parity with the frontend short-label map). `test_job_plot_artifacts.py` covers gzip-serialized Bokeh `json_item` persistence (`job_plot_artifact`), fingerprinting, and invalidation (requires Postgres like other `django_db` machine tests). **`test_public_api.py`** covers anonymous **`GET/POST /api/pub/cluster-dashboard/`** with `django_db(databases=[])` + mocks so host pytest does not need Compose Postgres. **`test_public_metrics_artifacts.py`** runs EF formula checks under **`machine_unit_mock`** off Compose; refresh/invalidate integration rows still require Postgres (`HPCPERFSTATS_COMPOSE_NETWORK=1` via `tests/run_db_pytest_workflow.sh`). `test_job_list_performance_summary.py` covers job list `performance` labels / `sort_rank` and ORM `performance_sort_rank` ordering vs `summarize_performance()`. `test_valkey_cache_live.py` hits Django `ValkeyCache` against a live Valkey when `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1` (`tests/run_valkey_cache_pytest_workflow.sh`); otherwise those tests skip. `test_query_utils.py` includes `get_job_list_order_by` allowlist (`performance_sort_rank`; `has_metrics` is rejected). **API:** `/api/jobs/` list entries expose `performance`; sort with `order_by=performance_sort_rank` or `-performance_sort_rank`. |
| `hpcperfstats/site/lib/machine/tests/test_job_detail_staff_sample_count.py` | Staff-only `staff_metrics_distinct_time_count` on `job_detail` JSON (mocked ORM; no live DB required). |
| `hpcperfstats/site/frontend` | React SPA unit tests (Vitest): `npm test` from that directory. Vitest picks up `*.test.{ts,tsx}` / `*.spec.{ts,tsx}` under `src/` and `app/`, plus `scripts/**/*.test.ts` and `test/**/*.test.{ts,tsx}` (`vitest.config.ts`), including `src/api.test.ts` (fetch/CSRF/401 paths), `src/components/ExtendedSearch.test.tsx`, `src/utils/normalize-job-list-histogram-entry.test.ts`, `src/utils/table-sort-a11y.test.ts`, `src/utils/job-list-route-title-context.test.ts`, `src/views/__tests__/Search.test.tsx` (browse-by-time **Calendar** tab first and selected by default), `src/views/__tests__/JobList.test.tsx` (includes narrow-viewport Jobs/Charts tab behavior), `src/views/__tests__/JobDetail.test.tsx` (job detail **Job data** inner tabs - including per-plot tabs (Summary, Heatmap, rooflines) before Bokeh assertions - progressive plot loading, and metrics tab single- vs two-table layout), **`src/components/LayoutPub.test.tsx`** (public `/pub/` navbar: branding, cluster label, **Login to see individual job data** -> `login_prompt?next=/machine/`), **`src/views/__tests__/PageClusterDashboard.test.tsx`** (`PubDashboardBundleContext`, expansion-factor tab panel, yearly-before-monthly anchors, `pub-expansion-factor-*` Bokeh ids), `src/utils/jobMetricDisplayLabels.test.ts` (short labels for job-level metrics table), and `src/utils/useDocumentTitle.test.ts`. `test/vitest/setupTests.ts` stubs `HTMLElement.prototype.scrollIntoView` for jsdom. |
| `hpcperfstats-tools/tests/` | Client CLI/API helpers: `test_api_client.py`, `test_api_auth_headers.py`, `test_job_dataframe.py`, `test_sacct_gen_security.py`, plus `test_config.py` (INI `base_url`), `test_api_key_cache.py` (`~/.hpcperfstats-api` parsing), and `test_jobstats_cli.py` (`jobstats` formatting and `main()` exit codes). Run from the `hpcperfstats-tools` directory with `python -m pytest`. |

### Monitor event metadata (frontend tooltips)

When approved monitor changes add or rename `host_data.event` strings, regenerate the bundled catalog from the repo root:

```bash
D=hpcperfstats/site/frontend/src/utils
python3 "$D/generate-variable-metadata-monitor-events.py"
```

Then follow `HPCPerfStats/hpcperfstats/cursor-rules/variable-metadata-*.mdc` for merging into `src/utils/variableMetadata.ts` and operator docs (`docs/regenerate_monitor_variables_catalog.py`, etc.).

## Test runners

### `scripts/run_tests.py` (default local runner)

`scripts/run_tests.py` wraps `pytest` and is the easiest default runner:

- `python scripts/run_tests.py --no-django` passes `-m "not compose_db"`,
  defaults collection to `hpcperfstats`, and uses quiet output.
  `hpcperfstats/conftest.py` marks `compose_db` on every test that needs the
  default PostgreSQL database (outside compose that is the set it skips);
  `django_db(databases=[])` and `machine_unit_mock` tests still run. It runs
  only the tests that need no database (no Postgres, Valkey or compose), so a
  green run is not a full check: see [Complete testing](#complete-testing)
- `python scripts/run_tests.py` runs `pytest -v hpcperfstats` by default
- extra pytest args are forwarded (for example `python scripts/run_tests.py -k metrics`)

### `tests/run_web_e2e_workflow.sh` (compose E2E runner)

Use this for web-page E2E modules plus the nginx/WSGI route contract:

- `hpcperfstats/site/lib/machine/tests/test_web_pages_e2e.py` (**PostgreSQL on Compose**: root **`conftest.py`** applies **`django_db`** to **`site/lib/machine/tests`** by default when unset; **`pytest_collection_modifyitems`** skips Postgres-backed machine tests unless **`HPCPERFSTATS_COMPOSE_NETWORK=1`**. This workflow passes **`HPCPERFSTATS_COMPOSE_NETWORK=1`** into the **`web`** container and runs **`manage.py migrate --noinput`** before pytest.)
- `hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py` (Playwright: Django stub server + **`/machine/*`** and **`/pub/*`** SPA shells return **404** from WSGI per nginx ownership contract)
- `hpcperfstats/site/hpcperfstats_site/tests/test_nginx_static_wsgi_contract.py` (**`/static/`**, **`/machine/`**, **`/pub/`** WSGI 404 contract + **`/robots.txt`** nginx static / WSGI 404 + edge HSTS/framing headers + OCSP/CSP include contracts + hybrid compression location split)
- **`test_bokeh_job_list_embed_browser_e2e.py`** uses bundled Bokeh and a
  **Next-built** static tree under
  **`hpcperfstats_site/static/frontend/`**. Run it through
  **`tests/run_bokeh_browser_workflow.sh`** after **`npm run build`** (full
  export, not `build:prod`). Fixtures:
  `hpcperfstats/site/frontend/test/fixtures/`.

The workflow script handles the isolated Podman lifecycle and runs **`migrate`**
plus those modules in one session:

```bash
tests/run_web_e2e_workflow.sh
```

Useful options:

```bash
# Seed/recreate test data before E2E tests
tests/run_web_e2e_workflow.sh --seed-cmd \
  "python your_seed_script.py"

# Keep services/volumes running after test run
tests/run_web_e2e_workflow.sh --keep-env

# Skip image rebuild or Playwright browser install when appropriate
tests/run_web_e2e_workflow.sh --skip-build --skip-playwright-install
```

Equivalent seed environment variable:

```bash
E2E_SEED_CMD="python your_seed_script.py" \
  tests/run_web_e2e_workflow.sh
```

### `tests/run_db_pytest_workflow.sh` (Podman Compose full Python suite)

Use this when you need **Django / Postgres** tests against the compose `db`
service. The baked ini uses `host = /run/postgresql`: `web` / `pipeline`
connect over the shared `postgres_runtime` tmpfs socket (password auth via
`services-conf/postgres_pg_hba.conf`); host-side pytest has neither that
socket nor the `db` alias. `tests/run_dev_container_pytest.sh` mounts the dev
project's `postgres_runtime` volume, so the dev `db` must be recreated with it.

From the `HPCPerfStats/` directory (where `docker-compose.yaml` lives):

```bash
tests/run_db_pytest_workflow.sh
```

The script copies `hpcperfstats.ini.example` to `hpcperfstats.ini` if the latter is missing (required for the Compose bind mount), resets compose volumes (unless `--keep-env`), starts **db** and **valkey**, runs `manage.py migrate` on the dev database, installs Playwright in the container, and runs `pytest -q hpcperfstats`.

Useful options:

```bash
# Faster iteration: skip browser E2E module and Playwright install
tests/run_db_pytest_workflow.sh --skip-browser-e2e

# Skip image rebuild
tests/run_db_pytest_workflow.sh --skip-build

# Keep db/valkey volumes after the run
tests/run_db_pytest_workflow.sh --keep-env

# Optional seed inside the web container after migrate
tests/run_db_pytest_workflow.sh --seed-cmd \
  "python path/in/repo/seed.py"
# or: DB_TEST_SEED_CMD="python ..." tests/run_db_pytest_workflow.sh

# Skip migrate on the dev database (pytest still creates its own
# test_* DB)
tests/run_db_pytest_workflow.sh --skip-migrate

# Forward pytest arguments (use -- if an option starts with -)
tests/run_db_pytest_workflow.sh -- -k job_plot
```

**`sync_timedb` stall regression battery (host, mandatory before stall-related PR close):**

```bash
cd HPCPerfStats
tests/run_sync_timedb_regression_battery.sh
```

Logs to **`test_runs/day-close-loop-regression-battery-<timestamp>.log`**. Covers job-store reconstruct / discover helpers (`test_sync_timedb_job_queue.py`, `test_sync_timedb_job_discover.py`, including digit-epoch calendar-day and over-cap hot ZADD via `-k` tokens `digit_epoch` / `over_cap` / `calendar_day`), exclusive flock + entry wiring (`test_sync_timedb_queue_orchestrator.py`), append lock/clone writer (`test_sync_timedb_append_tar_race.py`), subprocess drain/timeout (`test_sync_timedb_subprocess_hardening.py`), member-store smoke (`test_sync_timedb_archive_members_coord.py`), `day_close` predicate contracts `test_arch_*` (`test_sync_timedb_architecture_contract.py`), fd `-X` GNU stat discover, day-raw-removal samples (including ghost-deleted retry), day-close hot-path `populate_active` owner vs complete-map (`test_sync_timedb_day_close_cooperation.py`), gz-only migrate nested lock (`already_locked` / `nested_lock` in `test_sync_timedb_archive.py`), and **`--jid` ingest-only** (`test_sync_timedb_jid.py`). See **`sync-timedb-change-regression-gate.mdc`**, **`sync-timedb-queue-orchestrator-contract.mdc`**, and **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`** (**Queue orchestrator era** T0/T1/T2; `--jid` uses the smoke note there).

**`sync_timedb` `--jid` one-shot (host unit):**

```bash
cd HPCPerfStats
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/tests/test_sync_timedb_jid.py
```

Operator CLI (pipeline image, after `job_data` exists): `sync_timedb.py --jid <JID>` - ingest-only (+/-1h pad around job start/end, plus one earlier and one later raw stats file per host); no archival/day-close. See **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`** -> *sync_timedb --jid smoke*.

**`sync_timedb` ingest archive member cache (host, no compose):** use
`scripts/run_tests.py --no-django` with explicit targets (tests that need no
database only; see [Complete testing](#complete-testing)). The first command
runs two whole files plus five node IDs (210 tests); the second filters
`test_sync_timedb_archive.py` with one `-k` (23 tests):

```bash
cd HPCPerfStats
T=hpcperfstats/tests
C=test_sync_pipeline_tunable_defaults_and_overrides
Z=test_archive_zstd_never_runs_under_nice_or_ionice
H=$T/test_multiprocessing_pool_health.py
P=$H::test_abort_if_pool_workers_dead
../.venv/bin/python3 scripts/run_tests.py --no-django \
  $T/test_sync_timedb_archive_members_coord.py \
  $T/test_sync_timedb_queue_orchestrator.py \
  $T/test_zstd_cli.py::$Z $T/test_conf_parser.py::$C \
  ${P}_raises ${P}_log_does_not_claim_oom \
  ${P}_recycle_invokes_idle_reconcile
K="members_cache or raw_stats_needs_append or single_member"
K="$K or invalidate_daily or dedupe_sealed or day_skip"
K="$K or classify_stream or ingest_sealed or concurrent_waiters"
../.venv/bin/python3 scripts/run_tests.py --no-django \
  $T/test_sync_timedb_archive.py -k "$K"
```

Compose smoke for the same contracts:

```bash
N=test_daily_archive_members_cache_hit_skips_second_scan
tests/run_db_pytest_workflow.sh -- \
  hpcperfstats/tests/test_sync_timedb_archive_members_coord.py \
  hpcperfstats/tests/test_sync_timedb_archive.py::$N \
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py
```

Implementation detail: `tests/run_db_pytest_inner.sh` streams into `web` via
`compose_run_inner_script`. Bind-mount work copies and all growth-prone
temporary state use `/data/user/$USER/tmp`; they must never fall back to
`$HOME/.cache`, `/tmp`, or `/var/tmp`. Extra pytest arguments are passed through
the workflow-managed temporary file. The same adapter is used by Valkey, stress,
diagnosis, web, and pipeline workflows.

### `tests/run_valkey_cache_pytest_workflow.sh` (live Valkey cache integration)

Runs `hpcperfstats/site/lib/machine/tests/test_valkey_cache_live.py` inside the `web` container with **`HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`**, so Django keeps **`ValkeyCache`** against the Compose **valkey** service instead of switching to `LocMemCache` during pytest. `sync_timedb` job queues and member maps are in-process stores and are not part of this workflow. The tests carry the **`live_valkey`** marker. They assert `server_name` is `valkey` with `valkey_version` major **>= 9**, Django cache set/get/add/incr/touch/delete_many through **`django_valkey.cache.ValkeyCache`**, and raw `get_valkey_client()` SADD/SMEMBERS/SCAN/MEMORY USAGE against the Unix socket **`/run/valkey/valkey.sock`** (`[CACHE] valkey_location = unix:///run/valkey/valkey.sock?db=1`; TCP **6379** remains for `valkey-cli` and external URLs). After pytest, the inner script runs a free-threaded check on **`/opt/python3.14t`** with **`-W error::RuntimeWarning`**: `django.setup()`, a cache set/get and `get_valkey_client().ping()`, then it prints **`VALKEY_FT_GIL_OFF`** only when `sys._is_gil_enabled()` is False, so a dependency that re-enables the GIL fails the workflow. Compose **`web`** / **`pipeline`** do **not** `depends_on` `valkey` (podman-compose **`condition:`** can create the container and never start it). Startup wait (`valkeywait.py`) remaps hostname **`valkey`** to the Unix socket so containers do not DNS-timeout before `valkey` joins `hpcperfstats_net`.

```bash
tests/run_valkey_cache_pytest_workflow.sh
```

Without that env var, the live Valkey tests are **skipped** so normal `python scripts/run_tests.py` on a laptop does not require Valkey.

After changing the Compose **valkey** image tag, confirm the running server is Valkey 9.x:

```bash
podman-compose -p hpcperfstats exec valkey sh -lc '
valkey-cli INFO server | grep -E "^(server_name|valkey_version):"
valkey-cli ping
valkey-cli -s /run/valkey/valkey.sock ping
valkey-cli CONFIG GET io-threads'
```

Options: `--keep-env`, `--skip-build`. Forward pytest args the same way as the DB workflow (`-- -vv`).

## Accessibility (WCAG 2.2 AA target)

The SPA and standalone HTML pages aim for **WCAG 2.2 Level AA** for in-app flows (keyboard, screen readers, zoom, contrast). **Bokeh canvas plots** and the **OAuth provider** have inherent limits; the UI mitigates with text alternatives, landmarks, and documented manual checks.

**Automated (frontend - Vitest + jest-axe)**

`jest-axe` is a **devDependency** only (it pulls in `axe-core` as a dev-only transitive package). They are imported from `*.test.{ts,tsx}`, [`test/vitest/setupTests.ts`](../hpcperfstats/site/frontend/test/vitest/setupTests.ts), and [`test/vitest/axe-test-utils.ts`](../hpcperfstats/site/frontend/test/vitest/axe-test-utils.ts); they are **not** part of the Next production bundle. The production container image does not run `npm install` for the app and excludes `node_modules/` and `frontend/test/` from the build context; the frontend builder uses **`npm run build:prod`**, so axe and test-only static routes are not shipped with production static assets.

Colocated page/component tests call `axeSeriousViolations()` (WCAG 2.x / 2.1 AA tag scope, asserting **no serious or critical** violations) for: `Layout` (including Extended Search open), `JobList` (including charts tab with histograms), `JobDetail`, `Search`, `PageApiKey`, `PageTestLogin`, `AdminMonitor`, `ExtendedSearch` (dialog shell), and `HistogramThumbnails` (thumbnail + open popover). New or materially changed page shells must extend this set per **`frontend-a11y-regression.mdc`**.

jsdom cannot compute colour contrast, so jest-axe never reports `color-contrast`. [`src/utils/muted-surface-contrast.test.ts`](../hpcperfstats/site/frontend/src/utils/muted-surface-contrast.test.ts) statically rejects any component class string that pairs `text-muted-foreground` with solid `bg-muted` (about 4.3:1 in the light theme, under the AA 4.5:1 minimum). Playwright axe flagged that pair on the job-list histogram **Unavailable** placeholders; use `text-foreground` on `bg-muted` surfaces.

```bash
cd hpcperfstats/site/frontend && npm test -- --run
```

**Automated (Playwright - Python E2E)**

A **vendored** [`axe.min.js`](../hpcperfstats/tests/fixtures/axe-core/axe.min.js) (pinned version in that folder's `README.md`) is injected at test time by [`hpcperfstats/tests/playwright_axe.py`](../hpcperfstats/tests/playwright_axe.py). This avoids relying on `node_modules` inside the `web` container, where Node dev dependencies are not installed.

- **Harness only:** [`hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py`](../hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py) runs axe on a minimal empty HTML document (validates the Playwright helper after `robots.txt` checks). It does **not** scan the React SPA.
- **Real SPA axe (compose):** pipeline browser phase of `tests/run_pipeline_e2e_workflow.sh` (runs unless `--skip-browser`) - [`tests/pipeline_e2e/test_a11y_axe_browser.py`](../tests/pipeline_e2e/test_a11y_axe_browser.py) scans `_AXE_SMOKE_PATHS` (`/machine/`, `/machine/jobs/`, `/machine/home/`, `/machine/api-key`, plus one job detail URL) and Extended Search open on the job list; [`tests/pipeline_e2e/test_job_detail_browser.py`](../tests/pipeline_e2e/test_job_detail_browser.py) runs axe on the live job detail SPA.

**Manual / optional**

Browser extensions or an external audit pipeline remain useful for full-page audits beyond what CI runs.

**Manual spot-check (each release or major UI change)**

Treat the following as a **release gate** alongside automated tests: run through the checklist below before tagging or deploying UI-heavy changes.

- Keyboard only: Tab through navbar, **Extended search** dialog (Escape closes, focus returns), **Find Job**, tables, pagination, job detail **Job data** plot tabs.
- Screen reader: VoiceOver (Safari) or NVDA (Firefox) - **Search jobs** -> job list -> job detail; confirm **route focus** lands on the page heading after navigation.
- Zoom: browser **200%** on job list and job detail; tables scroll inside `.table-responsive` where needed.

**Standalone pages**

- `/machine/api-key` (React): `<main>`, confirm before key rotation, live region when a new key is shown.
- Django **admin**: branding includes a link back to `/machine/`.

## Requirements

- **General**: Python 3.14+, `pip install -e ".[test]"`. The only host environment is `/data/HPCPerfStats/.venv`, rebuilt with `/bin/python3.14`. Compose images are built by rootless Podman from **`debian:trixie`** (`python-build`) and **`debian:trixie-slim`** (`hpcperfstats-base`) in `services-conf/pipeline_web.Dockerfile`. Web and helper `python3` is GIL `/opt/python3.14`, symlinked into `/usr/local/bin`; supervisord runs the pipeline daemons on free-threaded `/opt/python3.14t/bin/python`, so container `python3` is **not** the ingest interpreter (`python-image-interpreter-contract.mdc`). Both CPython builds use upstream `--enable-optimizations` plus the `-march=native` ThinLTO flags from `services-conf/bake_clang_flags.sh`, and link jemalloc (the build checks `ldd ... | grep libjemalloc` on each interpreter). zlib-ng is built under `/opt/zlib-ng` with `ZLIB_COMPAT`, and zstd under `/opt/zstd`; the build asserts the zstd CLI version and that CPython `_zstd` links `/opt/zstd` and reports a `1.6.` version. Image **`python-build`** source-builds numpy (`-Dblas=mkl`, `-Dlapack=mkl`, `cpu-baseline=native`), numexpr (Intel VML via an injected `site.cfg`; an unversioned `libmkl_rt.so` symlink is created for GNU ld), pandas and brotli against pip Intel MKL for both ABIs. numexpr and pandas install with **`--no-deps`** so pip cannot replace MKL numpy with a manylinux OpenBLAS wheel. The build itself fails unless, on both ABIs, `numpy.show_config(mode="dicts")` mentions MKL after the numpy, numexpr and pandas installs and `numexpr.use_vml is True`. **`python-dateutil`** is pinned in `project.dependencies` and installed with the rest wheel layer; pandas meson needs **`versioneer[toml]`** from the `image-build` extra (`--no-build-isolation`). **Build on the deployment architecture** (`-march=native` is not portable). At runtime `hpcperfstats-base` writes `/etc/ld.so.preload` and sets `ENV LD_PRELOAD` to `/opt/jemalloc/lib/libjemalloc.so.2`. Host unit tests that lock these Dockerfile steps: `hpcperfstats/tests/test_dockerfile_python_deps_cache.py`, `test_dockerfile_mkl_source_stack.py`, `test_dockerfile_jemalloc_cpython.py`, `test_dockerfile_pyspy.py`, `test_pyproject_image_build_extra.py`. Do **not** install `.[image-build]` into the host `.venv`.
- **Django tests**: For a full run matching production hostnames (`db`, `valkey`), use `tests/run_db_pytest_workflow.sh`. Host-side `../.venv/bin/python3 scripts/run_tests.py` needs no Postgres: tests that need the default PostgreSQL database are marked `compose_db` and skipped unless `HPCPERFSTATS_COMPOSE_NETWORK=1`, which the compose workflows set inside `web` (`hpcperfstats/conftest.py`).
- **Live Valkey cache tests**: Optional; use `tests/run_valkey_cache_pytest_workflow.sh` (sets `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`).
- **Browser E2E tests**: Playwright/Chromium tooling (installed by the DB workflow or E2E workflow script unless skipped).

## Optional memory profiling (development)

**Commit-stage hard gate (memray):** when staged paths match
`^(hpcperfstats/|scripts/).*\.py$`, pre-commit runs
`scripts/run_commit_memory_leak_check.py` (hook id `python-memory-leak-check`).
It exercises curated **no-Django** workloads under memray, discards warm-up
iterations, and **hard-fails** if late-window mean heap growth or peak/leaked
bytes exceed fixed ceilings. Requires workspace venv with `pip install -e ".[dev]"`
(memray is in the `[dev]` extra). Exit `2` with an install hint if memray is
missing. Manual run:

```bash
cd HPCPerfStats
../.venv/bin/python3 scripts/run_commit_memory_leak_check.py
# or:
pre-commit run python-memory-leak-check --all-files
```

Set `HPCPERFSTATS_MEMORY_LEAK_CHECK_LOG=1` for per-workload measurement lines.
This is the **only** commit hard gate for memory growth - do not add a second
tracemalloc hard-fail hook alongside it.

**Offline / ad-hoc only (not hooks):** use these with DB/Valkey available (for
example the Podman Compose `web` service) when investigating RSS growth in
long-lived workers or CLI jobs.

**tracemalloc** (stdlib) around a focused pytest node:

```bash
../.venv/bin/python3 -X tracemalloc=25 -m pytest -q \
  hpcperfstats/site/lib/machine/tests/test_job_plots_timeout.py \
  --tb=no 2>&1 | tail -20
```

**memray flamegraphs** when investigating a hook failure or a single test file:

```bash
../.venv/bin/memray run -m pytest -q \
  hpcperfstats/site/lib/machine/tests/test_job_plots_timeout.py
../.venv/bin/memray flamegraph memray-*.bin
```

For ingestion-style workloads, run `memray` against a short `sync_timedb` invocation or a targeted dbload test module instead of the full archive scan.

**Static analysis (local git hooks):** install dev extras and register hooks from the git checkout root:

```bash
cd HPCPerfStats
../.venv/bin/pip install -e ".[dev]"
./scripts/install-git-hooks.sh
```

**Pre-commit** (staged files): **`ruff check --fix`** and **`ruff format`** on staged Python under `hpcperfstats/`, `cursor-hooks/`, `scripts/`, `services-conf/`, and `hpcperfstats-tools/` (policy from `pyproject.toml`: isort **`I`**, whitespace **`W291`/`W292`/`W293`**, plus **`E`/`F`/`B`/`UP`/`SIM`/`RUF`/`C4`/`PIE`/`PGH`/`S`** (Bandit); **`E501`** ignored in the linter - **line length is enforced by `ruff format --check`** at **`line-length = 80`**); ESLint on staged `hpcperfstats/site/frontend` TypeScript; python def inventory `--check`; **memray** curated memory-leak smoke (`python-memory-leak-check`) on staged `hpcperfstats/` / `scripts/` Python; **ShellCheck** (`shellcheck`, pinned `shellcheck-py` mirror `v0.11.0.1`, which bundles the binary) on staged shell scripts, selected by `.sh`/`.bash` extension or a shell shebang, with policy in the root **`.shellcheckrc`** (`external-sources=true`, `source-path=SCRIPTDIR`). Fix findings; when a pattern is intentional (word-split flag lists, printf format variables, literal `$` in grep patterns, trap handlers), add a scoped `# shellcheck disable=SCxxxx  # reason` on that line instead of a repo-wide disable. Drift guard: `hpcperfstats/tests/test_static_analysis.py::test_shellcheck_clean_on_all_tracked_scripts` (skips when `shellcheck` is not on `PATH`).

**Pre-push:** frontend `npm run typecheck` and `npm run lint:dead` (knip); `vulture hpcperfstats scripts/vulture_whitelist.py --min-confidence 80`.

**Plan close (required):** implementation plans are not done until **both** hook stages are green (`plan-creation-contract.mdc` / `PLAN_TEMPLATE.md` todo `git-hooks-pre-close`). From the git checkout root:

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

Does not require creating a commit or pushing - only running the hook suites and fixing failures.

Manual equivalents:

```bash
# Python lint + format (2-space indent, line-length 80 - main repo +
# hpcperfstats-tools)
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools --fix
../.venv/bin/ruff format hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools
../.venv/bin/ruff format --check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools

# Narrow unused-import check (subset of F rules; full tree uses
# pyproject select above)
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools --select F401,F841,F811

# Python dead symbols (high confidence)
../.venv/bin/vulture hpcperfstats scripts/vulture_whitelist.py \
  --min-confidence 80

# Frontend
cd hpcperfstats/site/frontend
npm run typecheck
npm run lint
npm run lint:dead
```

Host pytest drift guards: `pytest -q hpcperfstats/tests/test_static_analysis.py`.

Use `ruff check ... --fix` only after reviewing the diff (tests and dynamic imports can confuse static analysis).

## Security scanning (optional)

See **[`docs/SECURITY_AUDIT.md`](SECURITY_AUDIT.md)** for the latest memo and **[`docs/SECURITY_REMEDIATION_BACKLOG.md`](SECURITY_REMEDIATION_BACKLOG.md)** for follow-ups.

**Python (CVE audit on an environment):** install and run `pip-audit` against the same dependency set you ship (prefer the Docker build / production install; a raw `pip freeze` from a dev machine may include extra tools):

```bash
../.venv/bin/pip install pip-audit
../.venv/bin/pip-audit -r <(../.venv/bin/pip freeze)
```

**Production-context audit workflow (recommended):**

```bash
cd HPCPerfStats
tests/run_security_audit_workflow.sh
```

This builds the compose `web` image unless `--skip-build` is passed, runs `pip-audit` inside that image, and runs frontend `npm audit`. GitHub Actions installs rootless Podman, points storage at `/data/podman/$USER`, and calls this runner with `permissions: contents: read`.

**Frontend:**

```bash
cd hpcperfstats/site/frontend && npm audit
```

**CI cadence:** `.github/workflows/security-audit.yaml` runs this workflow weekly and on pull requests that touch dependency/security-audit files.

**CSP report endpoint:** regression tests live in `hpcperfstats/site/hpcperfstats_site/tests/test_csp_report_endpoint.py` (POST without CSRF token must succeed).

## Notes

- `pyproject.toml` exposes canonical runner paths under `[tool.hpcperfstats.testing]`: `web_e2e_runner`, `db_pytest_runner`, `valkey_cache_pytest_runner`.
- Prefer the compose E2E runner for page-level tests so DB/Valkey/networking match expected service topology.

## Chat failure registry (maintainer)

To mine parent agent transcripts for failure signatures into a registry JSON
for triage (written to the script's default `--output` path, or pass
`--output PATH`; the file is generated, not committed):

```bash
cd HPCPerfStats
T="$HOME/.cursor/projects/<project-id>/agent-transcripts"
../.venv/bin/python3 scripts/extract_chat_failure_signatures.py \
  --transcripts-dir "$T" --since 2026-01-04 --until 2026-06-04
```
