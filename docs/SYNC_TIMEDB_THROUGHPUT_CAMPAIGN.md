# sync_timedb Throughput Campaign

Canonical interpreted baseline and ongoing optimization ledger for durable
`sync_timedb` backlog drain. Raw run artifacts stay under
`test_runs/sync_timedb_bench/` and `test_runs/sync_timedb_campaign/`.

Operational parallelism model: [`SYNC_TIMEDB_PARALLELISM.md`](SYNC_TIMEDB_PARALLELISM.md).
Live plan: workspace `.cursor/plans/sync-timedb-campaign-knee.plan.md`
(kickoff complete: `.cursor/plans/sync-timedb-campaign-kickoff.plan.md`;
parent design: `.cursor/plans/sync-timedb-throughput-scaling.plan.md`).

## Campaign goal

Maximize sustainable durable throughput (DB-persisted rows + correct archive
membership) without long-lived lock waits, correctness regressions, or unsafe
archive unique-copy unlinks. Thread-count selection is the first intervention,
not the end state.

## Environment identity (production evidence window)

| Site | Role | Notes |
|------|------|--------|
| hpcperfstats02 | LS6 / exemplar source | **LOSING** (~4.48h post-redeploy span; `--since-minutes 1440` dilutes absolutes): listend 0.5028/min, full-ingest 0.2174/min, ratio 2.3131; archive_done 0; ETA N/A (no disk_pending). Kickoff short-window WINNING superseded. |
| hpcperfstats04 | Horizon / ARM exemplars | **LOSING** (~4.52h span; same dilution): listend 11.8507/min, full-ingest 0.1771/min, ratio 66.9216; archive_done 0; ETA N/A. Severe arrival≫drain gap. |
| hpcperfstats01 | Stampede3 exemplars | Giant-file strata; corpus only in this baseline |

True calendar-24h still limited by ~4.5h continuous post-redeploy logs; ratios/verdicts usable. Absolute rates undiluted optional (omit `--since-minutes`).

Effective concurrency observed on hpcperfstats02 (campaign snapshot):

- ingest 96, archive 2, populate 4, day-close 8
- queue max 3000; bulk-create 10000
- Python free-threaded 3.14t; sync ~2.2 cores with ~125 threads; large futex wait census

PostgreSQL coexistence: large idle `ClientRead` footprint from sync +
`update_metrics` backends; point snapshots showed no ungranted locks. Treat
`postgres_s` outcome fields as client+ORM wall until closed-book phases are
enabled in production evidence windows.

## Analyzer corrections (must apply before trusting rates)

1. **Unknown backlog ETA:** `scripts/measure_pipeline_ingest_rate.py` must emit
   `N/A` for ETA/finish when no uncapped disk backlog sample exists (not `0`).
2. **Listend rolling windows:** listend `current file unlinks (last 10 minutes)`
   lines are overlapping; non-overlapping decimation is required before using
   arrival rate, ratio, or backlog-gap fields as campaign targets.
3. **Compose timestamps:** operator rate pastes must use `logs --timestamps`;
   with `--since-minutes`, untimestamped lines are dropped.

## Corpus

Immutable exemplars (never mutate):

- `stats_files/hpcperfstats02-exemplars` (LS6)
- `stats_files/hpcperfstats04-exemplars` (Horizon)
- `stats_files/hpcperfstats01-exemplars` (Stampede3)

Smoke derived corpus (kickoff): `test_runs/sync_timedb_bench/corpus_smoke/`
via `scripts/derive_sync_timedb_benchmark_corpus.py --max-files N --host-suffix
.cluster_name.domain.edu` (manifest + SHA-256 oracle; sources unchanged).

Steady corpus (knee): `test_runs/sync_timedb_bench/corpus_steady/` — 24
smallest exemplars across 01/02/04 (~636 MiB, identity-shifted; sources
unchanged).

## Bottleneck ranking (pre-study, production-informed)

Scored from wall share / sensitivity / contention growth / safety risk.
Confidence is **hypothesis** until controlled timing/scaling artifacts exist.

| Rank | Bottleneck | Evidence | Likely surface | Risk |
|------|------------|----------|----------------|------|
| 1 | Host-proc merge / parse CPU (LS6 shape) | py-spy `merge_proc_row_dicts`; parse≈elapsed on success lines | `sync_timedb_parsing.py` | Medium |
| 2 | ORM materialize + bulk_create client wall (Horizon shape) | py-spy ORM/pandas; DB mostly `ClientRead` | write path + phase telemetry | Medium |
| 3 | Ingest width oversubscription / futex waits | 96 threads, ~111 futex waits, ~2.2 cores busy | `sync_ingest_pool_processes` | Low (config) / High if wrong |
| 4 | Archive flock timeouts on hot days | read-lock timeouts on busy tars | archive/day-close concurrency | High (correctness) |
| 5 | Connection footprint coexistence | 97 sync + many metrics backends idle | pool sizing / connection reuse | Medium |
| 6 | Unknown absolute backlog / ETA | missing uncapped pending samples in short windows | telemetry + rescan logging | Low |

## Experiment backlog

Every item needs hypothesis, metric, expected gain, surface, regression, cohort,
acceptance, rollback, status.

| ID | Hypothesis | Target metric | Surface | Status |
|----|------------|---------------|---------|--------|
| E1 | Ingest width below 96 raises durable files/s and cuts lock wait | lower CI bound files/s; p95 lock wait | INI `sync_ingest_pool_processes` + scaling study | **knee evidence present** — artifact `test_runs/sync_timedb_bench/knee_6364b97ef38f45c4b0399ea3d20044ca.json` (widths 48/64/80/96, 5 replicates). Winner **48** threads (selection prefers smallest width within CI of peak; means nearly flat ≈0.050–0.053 files/s). Screening hint 64 superseded for knee candidate. Caveat: wall-clock used small/mid derived files under compose; not a production INI change. Harness fixes: `--knee` clears ambient `SCREEN_WIDTHS`/`REPLICATES`; early-shutdown watcher bound to ingest timeout + file-complete marks |
| E2 | Closed-book residual ≤5% mid-size cohort | residual fraction | write/lock/parse telemetry | **artifact present** — `test_runs/sync_timedb_bench/e2_closed_book_8768f659741f42a28d52ffc07a7ba49d.json` (8 mid hosts from corpus_knee/steady, wall≈165s). `residual_frac=1.0` / `residual_ok=false` (empty phase map — once-mode telemetry still incomplete; contract recorded, not a hard pytest fail) |
| E3 | Non-overlapping listend rate revises LOSING margin | listend/ingest ratio | analyzer (done) | **complete (LOSING)** — fixed analyzer + timestamps; 02/04 ~4.5h post-redeploy pastes with `--since-minutes 1440` both **verdict_full_ingest=LOSING** (ratios 2.3 / 66.9); absolute rates diluted; true calendar-24h optional later |
| E4 | Split ORM vs DB execute shrinks false “postgres” blame | phase shares | ingest write phases | helpers shipped; production enable pending |
| E5 | Reduce day-close overlap contention | flock timeouts / day-close wall | day-close inflight | pending study |
| E6 | Evidence-led parse merge optimization on LS6 shape | files/s on 02-derived tier | parsing hot path | blocked — needs controlled knee (E1/E2) before product patch |

Closed-book store locks: default-off ``TimedRLock`` on job/members stores emits
``job_store_wait_s`` / ``job_store_hold_s`` / ``members_store_wait_s`` /
``members_store_hold_s`` when ``reset_store_lock_timing(enabled=True)``.

## Harness entry points

Host-safe (no compose):

```bash
cd HPCPerfStats
../.venv/bin/python3 -m pytest tests/sync_timedb_benchmark -q --tb=short
../.venv/bin/python3 -m pytest hpcperfstats/tests/test_derive_sync_timedb_benchmark_corpus.py -q
```

Compose / free-threaded long study (rootless Podman; `podman-runtime.mdc`):

```bash
cd HPCPerfStats
tests/run_sync_timedb_benchmark_workflow.sh
tests/run_sync_timedb_benchmark_workflow.sh --screening
tests/run_sync_timedb_benchmark_workflow.sh --knee
tests/run_sync_timedb_benchmark_workflow.sh --e2
```

## Selection rule (when study artifacts exist)

Maximize lower-confidence-bound durable throughput subject to correctness, no
long-lived lock/DB wait, no queue starvation, and ≥20% CPU/memory/DB/storage
headroom. Prefer the smallest width within 5% of peak unless a larger width
materially improves backlog-drain SLA without degrading p95 latency >10%.

Screening winner ≠ deployable INI without knee confirmation (follow-on).

## Negative / invalid results

- 24h archive_done = 0 on analyzer output is **unverified** until token coverage
  is confirmed against current finalize log lines.
- Provisional listend rates from overlapping sums are **invalid** for SLA math.
- Short post-redeploy windows are noisy; absolute rates diluted when
  `--since-minutes` exceeds elapsed span.
- Local compose scaling execution fails closed only when rootless Podman or the
  `/data` storage contract in `podman-runtime.mdc` is unmet.

## Next actions

1. ~~Knee confirmation (≥5 replicates, widths 48–96)~~ — done; candidate **48** (flat curve). Still not an INI redeploy.
2. Closed-book E2 residual artifact + ledger update (same tranche).
3. Mature 24h analyzer recompute on 02/04 when continuous logs exist.
4. Amend the live plan with measured root-cause lines before any product
   bottleneck fix; retain only paired A/B wins.
5. Operator T0/T1/T2 stall verify on a backlog site when accessible
   (agent host lacks BatchMode SSH to 02/04 — Host key verification failed).
