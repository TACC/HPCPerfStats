# update_metrics review (2026-10-30, code at 0a232d38)

Scope: `update_metrics.py`, `lib/metrics.py` (run / compute / persist /
gate failure), `lib/metrics_sliding_session.py`,
`lib/metrics_idle_slot_supplement.py`, `job_plot_artifacts.py`,
`job_detail_artifacts.py`, `services-conf/supervisord.conf`. State model:
`docs/UPDATE_METRICS_STATE_MACHINE.md`. A **finished job** = metrics persisted
**and** every artifact written from real data under the current fingerprint.

Findings come from reading the code; none was reproduced against a live
database. Items the 2026-05-12 stall audit already raised are marked
"(prior #n)".

## Summary

The pipeline is good at not wedging, but it has no definition of a
"finished job". `processed` counts metrics persists. A plot or detail failure
either disappears (counted as success) or gets written as a permanent
"generation failed" artifact under the current fingerprint, so nothing retries
it. On the correctness side, the biggest gap is readiness. It checks
coverage on the union of hosts, and nothing triggers a recompute when late
host data lands, so multi-node jobs can keep metrics and plots built from
a subset of their hosts. On throughput, the biggest losses are fixed
per-pass dead time (600 s sleep + 90 s restart + `/pub/` phase twice + a
900 s stall tail whenever any job is never ready), idle slots at the end of
each sliding session, and reading host_data three or more times per job.

## Silent failures

| ID | Where | What happens | Fix |
|---|---|---|---|
| S1 | `update_metrics.py:5932-5934`, `6135-6151`, `6108-6121`, `5704-5709` | Prewarm (detail + plots) failure never changes the job outcome: in both the sliding and batch paths `ok` is the metrics outcome, and artifact-only refs are hard-coded `ok: True`. `processed` / `completed_last_hour` / `jobs_per_min` count jobs with no plots. The only signal is `prewarm_success_ratio` in the end-of-pass line. | Make job `ok = metrics_ok and prewarm_ok`; add `finished_jobs_total` and `prewarm_failed_jobs` to the hourly line. |
| S2 | `job_plot_artifacts.py:992-1004`; `job_detail_artifacts.py:1020-1041`, `899/911/930/941` | A plot-builder or type-detail exception (including a transient DB timeout) is persisted as an `unavailable_reason` payload with the **current** fingerprint. Phase B compares fingerprints only, so it never re-lists the job: the failure is permanent until the inputs change. FSIO / NFS / BeeGFS / GPU-inventory sub-query errors are swallowed and the detail is written without those sections, also with the current fingerprint. (Prior #5/#6 described churn; the code now fails the other way.) | Write failure payloads with a distinct fingerprint (e.g. `fail:<fp>`) so Phase B re-lists them, and cap retries with a per-row attempt counter; keep the "no data" (legitimately empty) payloads under the real fingerprint. |
| S3 | `job_plot_artifacts.py:1024-1032` | `upsert_job_plot_artifact_batch` failure is logged at warning level and the function returns normally, so prewarm reports `ok`. | Re-raise (the prewarm retry and S1 accounting then see it). |
| S4 | `metrics.py:3474-3479`, `3841-3846` | The scheduler passes `SimpleNamespace` candidate refs (`_candidate_ref`) to `compute_metrics`, so `job.save(update_fields=["host_data_schema_json"])` raises `AttributeError`, which is swallowed. `host_data_schema_json` is never written by the scheduler (and sync_timedb ingest sets it to NULL, `cache_utils.py:590`). `HostDataSchemaKeyCount` is then 0, so the Phase B type-detail completeness checks (`update_metrics.py:2954`, `3023`) never fire: missing type-detail rows are never re-listed. Found by reading; worth one test with a real ref. | Write the schema with `job_data.objects.filter(pk=jid).update(...)` in the parent persist transaction (also removes the worker-side write from prior #20). |
| S5 | `update_metrics.py:4972-4973` | Rescan thread `except Exception: continue` with no counter or log (prior #11). | Count and log once per interval. |
| S6 | `update_metrics.py:2863-2864`, `2870-2871` | Phase A2 drops a row on any DB / type error without a trace. | Count `live_distinct_probe_errors`. |
| S7 | `metrics_sliding_session.py:447-466` | A stall while an item is in its prewarm phase marks the job failed even though its metrics rows were already committed, so `failed` and the DB disagree. | Report `metrics_ok, prewarm_failed` separately (follows from S1). |
| S8 | `update_metrics.py:7082`, `7120` | `processed` and `completed_last_hour` only advance when a sliding session returns; a long session (supplements keep it alive) shows 0/hour. | Bump counters per completed jid from the session's progress callback. |
| S9 | `update_metrics.py:6886-6887` | `stall_reason = "compute_stuck_inflight"` is set whenever the queue is momentarily empty with work in flight and is never cleared; healthy passes end with a stall reason in the final line. | Clear on progress; set only from the consumer stall timer. |
| S10 | `update_metrics.py:5510-5533` | The producer only exits cleanly when `deferred_not_ready` is empty. One job whose data never arrives keeps it alive, so 900 s later the pass ends with `stall_reason=no_ready_candidates`, `MetricsSchedulerStallExit`, exit 1. In steady state almost every pass ends as a "stall", which hides real stalls. | Treat "only deferred jids left" as done for this pass (or stop deferring after quarantine); reserve stall exit for queue-empty-with-candidates. |
| S11 | `update_metrics.py:5089-5104`, `7173`; `public_metrics_artifacts.py:859` | A degraded `/pub/` phase is logged and counted, nothing else (prior #24). The end-of-pass refresh is a full swallow. | Fine as a policy, but expose `public_ef_degraded` in the hourly line and alert on it. |
| S12 | `metrics.py:1561-1569` | A payload with no rows is returned as `ok` without writing anything (not even the sample count); Phase A re-lists it every pass. Unlikely today because compute always emits the catalog, but it is unguarded. | Treat empty rows as `empty_rows` failure. |
| S13 | whole scheduler | No failure memory across passes: a jid that fails deterministically (compute error, persist error) is recomputed in full every pass, forever, with only a per-pass log line. No dead letter, no attempt counter, no census of top failing jids. | Add a small `metrics_job_failure` table (jid, attempts, last_error, retry_at) with backoff and a terminal state, mirroring sync_timedb's A2 dead letter. |

## Correctness and consistency

| ID | Where | Problem | Fix |
|---|---|---|---|
| C1 | `update_metrics.py:3664-3692`, `3846-3884`, `3929-3967`, `4648-4677` | Readiness takes the **min over hosts** of each host's first sample and the **max over hosts** of each host's last sample; hosts with no rows are skipped. A 100-node job passes when one host covers the start and one covers the end. sync_timedb ingests per-host files out of order (backfill days, discover frontier), so metrics and plots are routinely built from a subset of hosts. | Require per-host coverage (or a configurable fraction of `host_list` with rows in both margins); report `hosts_missing` in `metrics_deferred_coverage`. |
| C2 | `job_plot_artifacts.py:47`, `235-236`; `update_metrics.py:2850-2873`; `sync_timedb.py:3063-3089` | Nothing recomputes when late data lands. With `LIVE_DISTINCT_SQL_PROBE_ENABLED=False`, `get_live_distinct_time_count_for_jid` returns the stored count, so Phase A2 only fires for a stored 0/NULL. sync_timedb ingest only invalidates caches and the schema column. C1's partial results are permanent and the plot fingerprint (built from the stale count) agrees with them. | Have sync_timedb mark touched jids dirty (e.g. `job_data.metrics_dirty_at`, set in `_invalidate_jid_caches`), and list dirty jobs in Phase A; this also replaces the A2 scan (T8). |
| C3 | `update_metrics.py:3577-3634`, `4694-4706`, `3957-3966`; `metrics.py:4098-4197` | The first not-ready probe of a just-ended job (data still arriving) **deletes all metric rows** and writes `INSUFFICIENT_DATA_FOR_METRICS_PROCESSING` placeholders. The delete (`metrics.py:4177`) and insert (`4191`) are separate transactions, so a crash in between leaves zero rows. Existing plots and detail are not touched, so the page shows plots next to "insufficient data". `_gate_failure_catalog_already_clean` returns False whenever artifacts exist, so the delete + insert + cache invalidation repeats on every reprobe (every 10 s for 5 min, then every 5 min). Under `rerun=True` a not-ready probe would wipe good metrics. | Persist a gate failure only once a jid leaves the deferred retry budget (quarantine / max age), in one transaction; skip when artifacts exist or clear them in the same transaction; never on a rerun. |
| C4 | `update_metrics.py:2695-2719`; `metrics.py:1489-1495` | Completeness is a row **count** (`md_count >= expected`), and persist only upserts, so it never deletes non-catalog rows. After a catalog rename, the old row keeps the count at `expected` and the new metric is never computed. | Count only catalog `(type, metric)` pairs (or delete non-catalog rows in the persist transaction). |
| C5 | `update_metrics.py:5345-5372`, `4898-5036`, `metrics_sliding_session.py:399-421` | The same jid can be in flight twice. The date iterator does not consult the rescan seen set, the seen set evicts past 4096, and in-flight jobs still match `_jobs_queryset` until persisted. Upserts keep the data sane, but `processed` double-counts and two prewarms race. | Keep an `inflight_or_queued` set checked on enqueue. |
| C6 | `metrics_idle_slot_supplement.py:120-228`; `metrics_sliding_session.py:419-421` | Supplements ignore `artifact_only`. An artifact-only ref taken as a supplement gets a full `compute_metrics` + persist, which can change the sample count and invalidate the very plots it was queued to build. | Route artifact-only supplements straight to `_submit_prewarm`. |
| C7 | `update_metrics.py:2144-2162`, `7547-7566` | Only jobs that ended in the last 7 days are ever processed. Jobs on days sync_timedb backfills later (weeks-old days are normal per `SYNC_TIMEDB_STATE_MACHINE.md`) never get metrics or plots, and `gate_failed` rows written while data was missing stay forever. Intended? If yes, document the operator run with explicit dates; if no, drive the window from C2's dirty mark. | Dirty-mark driven catch-up, independent of the trailing window. |
| C8 | `update_metrics.py:5696-5733` + prewarm ordering | Metrics persist changes `metrics_distinct_time_count`, which changes the plot fingerprint; if prewarm then fails, the old plots are stale and the job has no current plots until a later pass (≥ 11.5 min, S10/T1). | Follows from S1/S2 retry. |
| C9 | `update_metrics.py:5257-5267`, `5497` | `deferred_meta` entries are only removed when a jid becomes ready; never-ready jids grow the map for the life of the pass. Small, but it is the same set S10 trips on. | Prune on quarantine / pass end. |

## Throughput (finished jobs per hour)

Rough order by expected gain.

| ID | Where | Cost | Change |
|---|---|---|---|
| T1 | `update_metrics.py:7589-7594`, `6764`, `7173`; `supervisord.conf:106`; S10 | Per pass: 600 s sleep, process exit, 90 s supervisor sleep, Django and pool start-up, `/pub/` EF phase before any job and again at the end, plus ≥ 900 s idle tail whenever a never-ready job exists. Jobs ending in that time wait for the next pass. | Run the scheduler as a continuous loop (rescan + listing refresh instead of exit / restart); move `/pub/` to its own timer thread; fix S10. |
| T2 | `metrics_sliding_session.py:389-425`; `update_metrics.py:6840-7164` | A sliding session takes supplements only while an original ref is in flight; once the originals finish, the slots drain to zero before the consumer pops the next batch. With heavy-tailed jobs, each session ends with mostly idle slots. | One long-lived session that keeps `max_inflight` full from `ready_queue` until the producer is done; drop the batch boundary. |
| T3 | `update_metrics.py:7053-7057`, `7139-7147` | The cap halves when a batch's wall time is ≥ 120 s (almost always for metrics + prewarm), and grows only after a full batch in < 5 s (practically never), so it sits at 16. That makes T2's tails more frequent. | Moot after T2; otherwise use per-job latency, not batch wall time. |
| T4 | `metrics.py:3463-3918`; `job_detail_artifacts.py:857-1042`; `job_plot_artifacts.py:968-980` | host_data is read at least 3 times per job by separate `jid_table` instances: metrics (per-metric queries + the full `_JobForMetrics` frame + GPU / FSIO), job detail (FSIO fallbacks, GPU inventory, one `TypeDetailDataProvider` query per type), plots (aggregate bundle). The `PerJidComputeContext.artifact_context` is never shared between compute and prewarm. | Run metrics and prewarm in **one** worker task with a shared `jid_table` memo / frame; pass the computed metric values to detail so FSIO / GPU come from metrics (the reuse path already exists). |
| T5 | C6 | Artifact-only jobs taken as supplements pay a full metrics compute. | Fix C6. |
| T6 | `job_detail_artifacts.py:952-975` | `job_detail` and `multiprecision_mix` (plus the FSIO / GPU fallback queries) are rebuilt and upserted every time, even when their fingerprint is current; only type-detail is skipped. | Skip when both rows carry the current fingerprint. |
| T7 | C3; `update_metrics.py:5269-5306` | Every not-ready jid is reprobed every 10 s for 5 min (per-host min/max over host_data), then every 5 min, each time possibly rewriting its catalog and invalidating caches. That DB load competes with compute. | Exponential backoff; persist the gate failure once (C3). |
| T8 | `update_metrics.py:2808-2874`, `2877-3035`, `3139-3191` | Phase A2 and B re-scan **every** metrics-complete job in the 7-day window every pass: A2 issues one PK query per row, B issues 5-7 queries per 500-row page plus fingerprint SQL. The cost scales with window size, not with work. | Dirty marks (C2) for A2; for B, a partial index or a `artifacts_fp_current` flag maintained at write time. |
| T9 | `update_metrics.py:4933-5028` | The rescan thread replays `_jobs_queryset` (two correlated count subqueries per row) for every day every 5-60 s (prior #12). | Rescan only today / yesterday, or key off `end_time > last_seen_end_time`. |
| T10 | `metrics_sliding_session.py:480-525` | Metrics persist runs on the session thread between polls, so one slow upsert (lock timeout 10 s, statement 120 s) delays harvesting and resubmission for all 32 slots. | Persist inside the worker task (with T4) or on a dedicated persist thread. |
| T11 | `update_metrics.py:1504-1512`, `1543-1554` | Prewarm retries the full detail + plot pair on any escaped exception, which is usually deterministic. | Retry only DB / transient classes. |
| T12 | C5 | Duplicate in-flight jids. | Fix C5. |

## Measurement first

Before tuning, make the number honest. Add `finished_jobs_total` /
`finished_last_hour` (metrics ok **and** prewarm ok **and** no failure payload
written) next to `completed_last_hour`, plus per-phase p50 / p95 for
`metrics_s`, `persist_s`, `detail_s`, `plots_s` (the telemetry plumbing exists
behind `HPCPERFSTATS_METRICS_TELEMETRY`). Then measure idle slot-seconds per
session (T2) and pass dead time (T1) directly; those two plus T4 are where
the bulk of the gain should be.

## Suggested order

1. S1 + measurement: define and count finished jobs.
2. S2 / S3: failure payloads under a retryable fingerprint; re-raise upsert errors.
3. C3 + S10 + T7: stop writing gate failures for jobs that are only late; stop the stall exit on deferred-only passes.
4. C1 + C2 (+ C7, T8): per-host readiness and a sync_timedb dirty mark that drives recompute.
5. T1 + T2: continuous scheduler, one long-lived sliding session.
6. T4 + T10 + C6: fused per-job worker task with a shared host_data memo.
7. S4, C4, C5, the rest.

## Fix status (branch `update-metrics-review-fixes`)

One commit per finding; T4 (fused per-job worker task) is not done yet.

| ID | Commit | Notes |
|---|---|---|
| S1 | `317a1ecb` | `ok` = metrics and prewarm; `metrics_ok_total`, `prewarm_failed_total` |
| S2 | `25a2c49f` | retry fingerprints, `ArtifactBuildFailedError`, 3 builds then terminal |
| S3 | `c81e5ad4` | upsert errors propagate |
| S4 | `0a28b5e0` | schema persisted with the metric rows |
| S5 | `37e30918` | rescan errors counted and logged |
| S6 | `77588a40` | Phase A2 removed (with C2) |
| S7 | `58d2a3de` | prewarm-phase stall keeps the committed metrics outcome |
| S8 | `308b98ad` | per-job accounting via `on_result` / `on_outcome` |
| S9 | `fd69d8c2` | consumer stall reason cleared on progress |
| S10 | `a7efd734` | deferred-only pass ends normally |
| S11 | `2fc3131d` | degraded `/pub/` refresh warnings and counters |
| S12 | `61c615ba` | empty rows are `empty_rows` failures |
| S13 | `12899107` | failure memory with backoff (process-level) |
| C1 | `78025052` | per-host coverage, `metrics_readiness_host_wait_seconds` |
| C2 | `77588a40` | `job_data.metrics_dirty_at` (migration 0041) set by sync_timedb ingest |
| C3 | `d2518f26` | gate failure after the host wait, atomic, never over values |
| C4 | `dfba1ebe` | completeness counts catalog names only |
| C5 | `8e0064c1` | `scheduled_jids` dedupe (also T12) |
| C6 | `95026958` | artifact-only refs skip compute (also T5) |
| C7 | `255ef9b9` | dirty catch-up before the window (migration 0042 partial index) |
| C8 | `317a1ecb`, `12899107` | covered by S1 + S13 |
| C9 | `31a34eff` | deferred meta released; `deferred_meta_size` |
| T1 | `70539cb4`, `3e7fe115` | continuous process loop, background `/pub/` refresher |
| T2 | `10b68a86` | one continuous sliding session |
| T3 | `4bb49800` | barrier-path cap recovers |
| T4 | — | open |
| T5 | `95026958` | with C6 |
| T6 | `c06df356` | current detail/mix not rebuilt |
| T7 | `759b819c` | exponential deferred backoff |
| T8 | `2f21a175` (+ A2 removal in `77588a40`) | Phase B ≤ once an hour per day |
| T9 | `e0a4e5ef` | rescan two newest days |
| T10 | `408b1dc2` | sliding workers persist |
| T11 | `030908e9` | prewarm retries DB errors only |
| T12 | `8e0064c1` | with C5 |

Residual risks after these fixes: listend's live ingest path does not stamp
`metrics_dirty_at` (only sync_timedb does), so late data that arrives through
RabbitMQ after a job's metrics were built is not recomputed; failure memory
and the Phase B throttle reset on process restart; DB-backed tests
(`django_db` with a database) were not run in this environment.
