# sync_timedb Throughput Campaign

Canonical interpreted baseline and ongoing optimization ledger for durable
`sync_timedb` backlog drain. Raw run artifacts stay under
`test_runs/sync_timedb_bench/` and `test_runs/sync_timedb_campaign/`.

Operational parallelism model: [`SYNC_TIMEDB_PARALLELISM.md`](SYNC_TIMEDB_PARALLELISM.md).
Live plan: workspace `.cursor/plans/sync-timedb-throughput-scaling.plan.md`.

## Campaign goal

Maximize sustainable durable throughput (DB-persisted rows + correct archive
membership) without long-lived lock waits, correctness regressions, or unsafe
archive unique-copy unlinks. Thread-count selection is the first intervention,
not the end state.

## Environment identity (production evidence window)

| Site | Role | Notes |
|------|------|--------|
| hpcperfstats02 | LS6 / exemplar source | Direct full-ingest ~0.62 files/min (24h); futex-heavy ingest pool at 96 threads |
| hpcperfstats04 | Horizon / ARM exemplars | Direct full-ingest ~0.15 files/min (24h); ORM/`bulk_create`/pandas dominant in py-spy |
| hpcperfstats01 | Stampede3 exemplars | Giant-file strata; corpus only in this baseline |

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

Recompute site arrival rates with the fixed analyzer before promoting any
listend-derived SLA margin.

## Corpus

Immutable exemplars (never mutate):

- `stats_files/hpcperfstats02-exemplars` (LS6)
- `stats_files/hpcperfstats04-exemplars` (Horizon)
- `stats_files/hpcperfstats01-exemplars` (Stampede3)

Derive identity-shifted copies with
`scripts/derive_sync_timedb_benchmark_corpus.py` (manifest + SHA-256 oracle).

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
| 6 | Unknown absolute backlog / ETA | missing uncapped pending samples in 24h logs | telemetry + rescan logging | Low |

## Experiment backlog

Every item needs hypothesis, metric, expected gain, surface, regression, cohort,
acceptance, rollback, status.

| ID | Hypothesis | Target metric | Surface | Status |
|----|------------|---------------|---------|--------|
| E1 | Ingest width below 96 raises durable files/s and cuts lock wait | lower CI bound files/s; p95 lock wait | INI `sync_ingest_pool_processes` + scaling study | blocked — compose study abandoned on this host |
| E2 | Closed-book residual ≤5% mid-size cohort | residual fraction | write/lock/parse telemetry | harness shipped; full run pending |
| E3 | Non-overlapping listend rate revises LOSING margin | listend/ingest ratio | analyzer (done) | analyzer fixed; recompute on sites |
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
```

## Selection rule (when study artifacts exist)

Maximize lower-confidence-bound durable throughput subject to correctness, no
long-lived lock/DB wait, no queue starvation, and ≥20% CPU/memory/DB/storage
headroom. Prefer the smallest width within 5% of peak unless a larger width
materially improves backlog-drain SLA without degrading p95 latency >10%.

## Negative / invalid results

- 24h archive_done = 0 on analyzer output is **unverified** until token coverage
  is confirmed against current finalize log lines.
- Provisional listend rates from overlapping sums are **invalid** for SLA math.
- Local compose scaling execution fails closed only when rootless Podman or the
  `/data` storage contract in `podman-runtime.mdc` is unmet.

## Next actions

1. Re-run fixed rate analyzer on hpcperfstats02/04 full logs; refresh arrival margin.
2. Extend `tests/run_sync_timedb_benchmark_workflow.sh` from the green 3.14t unit
   harness smoke under rootless Podman to the full 1–96 ingest-width matrix with
   derived corpus and Timescale state.
3. Amend the live plan with measured root-cause lines before any product
   bottleneck fix; retain only paired A/B wins.
