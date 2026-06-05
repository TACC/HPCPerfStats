# Test run log (2026-06-04)

Full-suite + chat regression audit.

| Tier | Command | Result | Notes |
|------|---------|--------|-------|
| Host pytest `--no-django` | `../.venv/bin/python scripts/run_tests.py --no-django` | **646 passed** (~73s) | Fresh confirmation 2026-06-04 PM |
| Vitest | `npm test -- --run` (frontend) | **210 passed** (44 files) | Fresh; JobDetail plots test uses deferred promise |
| hpcperfstats-tools | `python -m pytest -q` | **22 passed** | Fresh |
| Compose DB | `bash tests/run_db_pytest_workflow.sh --skip-build` | **not re-run here** | Last full run: **1057 passed, 30 failed**; fixes landed for fingerprint SQL, SPA/nginx test, throttles, DB guards—re-run compose locally |
| Redis live | `bash tests/run_redis_cache_pytest_workflow.sh --skip-build` | **not re-run here** | Last run: passed |
| Web E2E | `bash tests/run_web_e2e_workflow.sh --skip-build` | **not re-run here** | Last run: **17 passed** |
| Chat extractor | `scripts/extract_chat_failure_signatures.py` | **1045 rows** / 517 parent chats | `docs/chat_failure_registry.json` |
| P0 triage | manual | **6/6 covered** | `docs/chat_failure_registry_p0_triage.json` |

## Fixes in this effort

- `test_job_for_metrics.py`: `_OwnedPool` accepts pool `initializer` kwargs.
- `test_docker_compose_healthchecks.py`: RabbitMQ max message size 128 MiB.
- `test_sync_timedb_supervisor.py`: maintenance defer/forced tests; inline ThreadPoolExecutor; parse_payload `close_old_connections` mock.
- `api.py`: restored `_job_list_queue_bar_chart` for Bokeh regression tests/fixtures.
- `update_metrics.py`: `_iter_chunked_pks` uses keyset only on real `QuerySet`.
- `conftest.py`: auto `django_db` marks for `hpcperfstats/tests/` (host: `databases=[]`, compose: default DB).
- `test_cache_utils.py`, `test_job_list_staff_sample_count.py`, `test_job_plots_l2_cache.py`: compose-safe mocks.

## Remaining compose failures (investigate)

- `test_artifact_readiness_expressions.py` fingerprint hash drift (PostgreSQL).
- Some `test_update_metrics.py` scheduler mocks (`ensure_pool` / FakeMetrics).
- API tests returning 401 without `check_for_tokens` patch or session middleware.
- `test_http_headers_security_cache.py`, throttle tests (status code expectations).

## Opt-in tiers (not run in this pass)

- `test_bokeh_job_list_embed_browser_e2e.py` (Playwright + Bokeh smoke build)
- `tests/run_pipeline_e2e_workflow.sh --skip-build`
- `tests/run_update_metrics_diagnosis_workflow.sh --skip-build`
- `tests/run_stress_host_data_workflow.sh --skip-build`
