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
| E1 | Ingest width below 96 raises durable files/s and cuts lock wait | lower CI bound files/s; p95 lock wait | INI `sync_ingest_pool_processes` + scaling study | **shipped default 48** — knee artifact `test_runs/sync_timedb_bench/knee_6364b97ef38f45c4b0399ea3d20044ca.json` (widths 48/64/80/96, 5 replicates). Winner **48**. **hpcperfstats04 2026-09-24 steady @ width 96:** window **289 min**, listend **6.15**/min, full-ingest **1.65**/min, ratio **3.72 LOSING** (warm-up ~16 min had ~5.3 ingest/min — not steady). Mid-file walls multi-ks with `postgres_s` ≈ half. Default registry/example → **48**; do **not** raise pool. Analyzer now emits `sync_full_ingest_mib_per_min` + size-tier median elapsed/postgres for post-48 soak decisions. |
| E2 | Closed-book residual ≤5% mid-size cohort | residual fraction | write/lock/parse telemetry | **pass after telem fix** — `test_runs/sync_timedb_bench/e2_closed_book_da92427028ed4dfbb430053defac3d72.json` (wall≈148s, **23 phases**, `residual_frac=0.0`, `residual_ok=true`). Dominant campaign holds: `parse_feed_s`, `parse_build_df_s`, `parse_proc_merge_s`, then `write_postgres_s` / `write_db_execute_s`. (Prior empty-phases artifact `…8768f659…` superseded.) Note: campaign phase seconds can exceed wall when workers run in parallel — residual uses closed-book accounting against wall |
| E3 | Non-overlapping listend rate revises LOSING margin | listend/ingest ratio | analyzer (done) | **complete (LOSING)** — fixed analyzer + timestamps; 02/04 ~4.5h post-redeploy pastes with `--since-minutes 1440` both **verdict_full_ingest=LOSING** (ratios 2.3 / 66.9); absolute rates diluted; undiluted ~3.2h recompute: **02 ratio 2.76**, **04 ratio 1.24** (both LOSING; archive_done 0) |
| E4 | Split ORM vs DB execute shrinks false “postgres” blame | phase shares | ingest write phases | helpers + process-global write telem shipped; E2 now shows `write_postgres_s` / `write_db_execute_s` / `write_orm_materialize_s` populated |
| E4b | host/proc COPY insert beats ORM bulk_create on large writes | write-only `mean_write_s` | Podman `--host-insert` / `--proc-insert` | **retain** — `host_data_insert_ab_fde1031c9ce64cf89ae2d3f572c84b12.json` (100k rows, baseline≈21.4s → candidate≈15.0s) and `proc_data_insert_ab_e59d4a4941444365947636f42f14280c.json` (100k rows, ≈24.4s → ≈11.4s). Defaults ON (opt-out `HPCPERFSTATS_SYNC_HOST_DATA_COPY=0` / `HPCPERFSTATS_SYNC_PROC_DATA_COPY=0`). Multi-writer gate rejected (width-48 worse than 96; ClientRead idle watches; prior writer-lock removal). Loaded48 1h@48 insert A/B files/s **flat** while `postgres_s` mean **183→1.2** — do not use files/s as write meter. **hpcperfstats04 overnight decision soak** (plan `04-overnight-decision-soak`): enable outcome telem via INI `sync_ingest_telemetry=yes` (default still off); analyzer emits `decision_next` |
| E4c | Log-visible write/COPY/parse telem + overnight decision pack | `decision_next` + phase medians | `ingest file` log tokens + `measure_pipeline_ingest_rate.py` | **CODE ready** — write phases (`orm_materialize_s`/`db_execute_s`/`copy_s`/`conflict_insert_s`) + parse stages + insert arms on outcome lines when INI **`sync_ingest_telemetry=yes`** (default off; one knob; not dual env). Operator: ≥8h soak on 04 then `--since-minutes 480` → lock next CODE from `decision_next` |
| E5 | Reduce day-close overlap contention | flock timeouts / day-close wall | day-close inflight | **knobs matrix complete** — `knobs_cf29f0b42561480f99280ce0616c973d.json` @ ingest width 48 ×3 reps. Candidates (smallest within 5% of peak): day_close **1**, archive **1**, populate **1**, bulk **2000**. Curves for day_close/archive/populate nearly flat (~0.045–0.047 files/s); bulk slowest absolute (~0.023–0.025) — treat as campaign hint only, early-shutdown clock excludes most day_close wall |
| E6 | Evidence-led parse merge optimization on LS6 shape | files/s on 02-derived tier | parsing hot path | **complete-negative (feed_line)** — paired A/B @ width 48 ×5 reps: baseline `e6_arm_baseline_1e7943f6b3c6424d879996f32b6ca407.json` (mean≈0.0472 files/s, lo≈0.0457); candidate after surgical `feed_line` micro-opts `e6_parse_feed_ab_31a8582e4c864e34a5af28cac99ee189.json` (mean≈0.0459, lo≈0.0451, **retain=false**). Product patch **reverted**. Harness `--e6` retained. |
| E7 | Residual `parse_proc_merge_s` / `parse_build_df_s` after Wave4 online merge | files/s on 02-derived tier | parsing hot path | **complete-negative** — paired A/B @ width 48 ×5 reps: baseline `e7_arm_baseline_c710cd12e05a4831bb40004125265cb9.json` (mean≈0.0505 files/s, lo≈0.0446); candidate skip-redundant-dedupe + columnar `take_proc` `e7_proc_build_ab_da062d9bdc6f45ce8a1a11f95e02ebb6.json` (mean≈0.0488, lo≈0.0443, **retain=false**). Product patch **reverted**. Harness `--e7` retained. High baseline CI width (lo≪mean) — noise floor; no CI win. |
| E8 | Cut `delta_s` + `collapse_s` after 04 overnight lock | mid-tier median **`delta_s`** and **`collapse_s`** seconds (`retain_delta_s` AND `retain_collapse_s`) | `_apply_counter_deltas` / `_collapse_stats_with_deltas` | **retain** — `e8_delta_collapse_ab_a4c17ad6f4714829a343e11943914970.json` (host-unit Horizon frame ×5): delta mean **0.089→0.058**, collapse **0.158→0.063**; both hold gates true. CODE: identity short-circuit in `_groupby_sum_min_count` + categorical group keys / numpy wrap in `_apply_counter_deltas`. files/s report-only. Harness `--e8`. |
| CONTENTION | FT contention P0–P3 sequential A/B @ width 48 | files/s + store wait | caches / park / manifest / members shard / … | **complete** (`--contention`). **retain=true (original A/B):** `caches`, `thread_id`. **Loaded-96 land 2026-09-24 (operator keep, no revert):** `members_shard`, `log_drain`, `manifest_io`, `claim_heap`, `tar_ex` (restaged) — concurrency review **pass** after manifest snap/save + log drain barrier fixes. Still out of tree: `park_resume`, `pool_split`, `discover`, `telem_tls`. Loaded-48 queue had retain=false on these five; land is operator override for width-96 soak (`tests/run_loaded96_landed_compare.sh`, H1 pin `a20a234d…`). |
| WIDTH_SWEEP | Same-tree loaded soak @ 48/64/96 × 3h vs this-run @48 | files/s + occupancy; report-only gate | `tests/run_loaded_width_sweep_ab.sh` (nohup+setsid) | **complete 2026-09-25** — artifact `test_runs/sync_timedb_bench/width_sweep_ab_0062cefe4a704ed793ae6b077ba4ef86.json`. 3h means ≈**0.2596** files/s at all three widths (48/64/96 flat); occupancy_ok=True sustained at each (peaks 48/62/96). Report-only `gates` **64=False, 96=False** (neither clears E6 lower-CI vs this-run @48). Prefer **48**; more threads do not raise durable files/s on this harness. Not a production INI change. |

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
tests/run_sync_timedb_benchmark_workflow.sh --knobs
# E6 A/B: HPCPERFSTATS_E6_ARM=baseline|candidate
tests/run_sync_timedb_benchmark_workflow.sh --e6
# E7 A/B: HPCPERFSTATS_E7_ARM=baseline|candidate
tests/run_sync_timedb_benchmark_workflow.sh --e7
```

Supporting knobs (Test 2 stage 3): sequential factor sweeps at fixed ingest
width **48** via `--knobs` → `knobs_*.json` (day-close / archive / populate /
bulk-create). Live compose matrix complete 2026-09-18
(`knobs_cf29f0b42561480f99280ce0616c973d.json`, 9h on 3.14t). Winners remain
**campaign candidates only** — not production INI.

E6 parse_feed A/B (Test 2 evidence-led CODE): `--e6` with
`HPCPERFSTATS_E6_ARM=baseline` then `candidate` → `e6_arm_baseline_*.json` /
`e6_parse_feed_ab_*.json`. Feed_line micro-opt arm **retain=false** 2026-09-18;
product patch reverted.

E7 residual proc_merge/build_df A/B: `--e7` with
`HPCPERFSTATS_E7_ARM=baseline` then `candidate` → `e7_arm_baseline_*.json` /
`e7_proc_build_ab_*.json`. Skip-dedupe + columnar take arm **retain=false**
2026-09-19; product patch reverted.
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

1. ~~Knee confirmation (≥5 replicates, widths 48–96)~~ — done; candidate **48** (flat curve). **Shipped** as `sync_ingest_pool_processes` default **48** (2026-09-24 rate-match plan); do not raise toward 96 without new evidence.
2. ~~Compose E2 re-run after telem fix~~ — done 2026-09-17; `residual_frac=0.0`, 23 phases (`e2_closed_book_da92427028ed4dfbb430053defac3d72.json`).
3. ~~Compose `--knobs` matrix @48~~ — done 2026-09-18; candidates day_close/archive/populate **1**, bulk **2000** (`knobs_cf29f0b42561480f99280ce0616c973d.json`). Flat day-close/archive/populate — do not raise inflight from knobs alone.
4. ~~E6 feed_line product CODE A/B~~ — done 2026-09-18 **negative** (`e6_parse_feed_ab_31a8582e4c864e34a5af28cac99ee189.json`, retain=false); patch reverted.
5. ~~E7 residual proc_merge/build_df product CODE A/B~~ — done 2026-09-19 **negative** (`e7_proc_build_ab_da062d9bdc6f45ce8a1a11f95e02ebb6.json`, retain=false); patch reverted. Harness `--e7` kept. Evidence-led Python CODE tranche on closed-book parse holds is exhausted for corpus_steady @48 without a new flamegraph / larger cohort.
6. **Post width-48 redeploy soak (hpcperfstats04):** ≥4 h `measure_pipeline_ingest_rate.py` — compare ratio vs baseline **3.72**; use `sync_full_ingest_mib_per_min` + `tier_*_median_elapsed_s` / `tier_*_median_postgres_s` to pick write-path vs parse vs giant scheduling (see plan decision table). Short ≤30 min windows are warm-up only.
7. Coexistence matrix (`update_metrics` + listend) at width 48.
8. Finalists + soak (Test 2 stage 5).
9. Mature 24h analyzer recompute on 02/04 when continuous logs exist.
10. Operator T1/T2 stall verify on a backlog site when accessible
   (agent host lacks BatchMode SSH to 02/04 — Host key verification failed).
   Undiluted rates + T0 census already recorded (02 ratio 2.76 / 04 ratio 1.24 @ earlier redeploy; 04 Sep-24 width-96 steady ratio **3.72**).