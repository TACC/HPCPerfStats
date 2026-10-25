# sync_timedb Throughput Campaign

Living ledger for the fresh throughput campaign that started 2026-10-15.
The ranked work below is the active theme. Everything through 2026-10-14
(E1–E18, width studies, knee, contention) is preserved under
**Historical campaign** and is archaeology: it does not pick the next change.

Raw run artifacts stay under `test_runs/sync_timedb_bench/` and
`test_runs/sync_timedb_campaign/`.

| Document | Role |
| --- | --- |
| This file | Rank, protocol, site facts, and the evidence ledger. Update it whenever a plan records a result. |
| `.cursor/plans/sync-timedb-fresh-throughput-campaign.plan.md` | Full candidate designs (approach groups). |
| `.cursor/plans/sync-timedb-fresh-baseline.plan.md` | First execution plan: baseline soak on 01 and 04. |
| Later `.cursor/plans/sync-timedb-fresh-fN-*.plan.md` | One plan per rank, written from this document after the previous soak. |

## How this document stays current

1. Read the rank table and the evidence ledger before writing the next plan.
2. The next product change is the highest rank that has not been retained or rejected. Start at **F1**.
3. After each production soak, add a ledger row (site, window, image, medians, verdict) in the same task that records the plan result.
4. If a baseline or soak median shows a different hold is larger than the current #1, rewrite the rank here before the next implementation plan. Do not silently skip.
5. A retained commit becomes the next master image (arm A).

## Campaign goal

Raise sustainable durable ingest throughput (rows in Timescale plus correct
archive membership) on the two live clusters. Local tests are correctness
screens. The throughput verdict is a sequential production soak: arm A is the
current master image, arm B is one candidate, equal windows, one writer.

## Active sites

| Host | Cluster | Scale (operator, 2026-10-15) | Role in this wave |
| --- | --- | --- | --- |
| hpcperfstats04 | Horizon | about 1,000 nodes | Baseline and every later A/B |
| hpcperfstats01 | Stampede3 | about 2,000 nodes | Baseline and every later A/B |

hpcperfstats02 (LS6) is historical only. It is not a baseline host for this wave.

Known from prior operations, **not** a live census (confirm with the pending
commands in the baseline plan):

- Pipeline daemons run free-threaded CPython 3.14t. Host `.venv` stays GIL 3.14.
- Registry ingest width default is **64**. Do not raise toward 96. The flat
  width sweep did not buy files/s, and width 96 inflated walls on 04.
- 04 dense files are often about 13–17 MiB (`8mib_64mib`). 01 carries giant-file
  strata. Discover-pipe and `max_connections` exhaustion have both been seen on 01.
- On 01, `exec -u hpcperfstats` is the pipeline user. `su hpcperfstats` fails.
- DB role is `hpcperfstats`. When `max_connections` is exhausted, count backends
  with `ps`, not `psql`.
- COPY insert is the default host and proc arm unless
  `HPCPERFSTATS_SYNC_HOST_DATA_COPY=0` or `HPCPERFSTATS_SYNC_PROC_DATA_COPY=0`.

Slurm facts still needed from the login nodes (not compose). Paste both into
the baseline plan and back into this table:

- `sinfo -h -o "%P %D %c %m %G"` on Horizon and on Stampede3 (partition, node
  count, CPUs, memory, GRES).
- GPU node count versus CPU node count. A large GPU share can raise `arc_s` /
  `collapse_gpu_s` relative to F1/F2 after the baseline; it does not change
  tonight's meter set.

## Ranked optimizations

Rank is structural speedup on durable ingest (MiB/min and giant-file wall),
**#1 largest**, from the 2026-10-17 soak. Historical 01 `feed_s` medians from
telem-on soaks before the **feed_s telem tax** fix (per-line campaign lock +
nested additive holds) are untrusted for product ranking. After that CODE,
telem-on `feed_s` / `proc_merge_s` are exclusive-time holds flushed once per
outermost stage. Ranks are not taken from the historical E-series. F2 is
rejected. `conflict_insert` and F1 are retained. The in-place proc merge is
in the tree and waits on the 01 soak. The next untried cut is the giant-file
`start_s` scan.

| Rank | ID | Change | Estimated effect | Meter |
| --- | --- | --- | --- | --- |
| 1 | F2 | Overlap one parse chunk with the PostgreSQL write | Rejected 2026-10-16 and removed from the tree. 04 dense elapsed fell; 01 MiB/min fell 41 percent. | **`overlap_headroom_s`** |
| 2 | conflict_insert | Shrink the proc `ON CONFLICT` after COPY | Retained 2026-10-17. 04 dense `conflict_insert_s` 38.3s (was 64.3s). | `conflict_insert_s` |
| 3 | F1 | COPY rows without Django model construction | Retained 2026-10-17. `orm_materialize_s` 0 and `orm_rows` 0 (was 36.8s). | `orm_materialize_s`, **`orm_rows`** |
| 4 | proc_merge | Update a repeat host_proc row in one pass | In tree 2026-10-17. Retain if 01 64 MiB–1 GiB `proc_merge_s` is at least 20 percent below 139.5s. | `proc_merge_s` |
| 5 | start_scan | Stop the duplicate prefix scan on a head-present giant | After proc merge. 01 64 MiB–1 GiB `start_s` 129.4s. Live listend disables both DB-complete fast paths. | `start_s` |
| 6 | F3 | Vectorize ARC diff, carry apply, and carry finalization | Large on long counter series. Behind proc merge and `start_s`. | `arc_s` |
| 7 | F4 | Reuse the COPY temp stage on the connection | Retained 2026-10-20 (operator keep). 04 dense `stage_ddl_s` 0.000 (was 5.500); nz `stage_ddl_s=` lines 302 (was 2917). Columnar COPY body also retained: dense `write_setup_s` 0.000 (was 8.750). | `stage_ddl_s`, `write_setup_s` |
| 8 | F5 | Reuse the PostgreSQL session on the worker | Dropped as a candidate: `reconnect_s` is 0 on every line (socket opens before the reconnect meter). | `reconnect_s` |
| 9 | F6 | Stream COPY bytes with a bound | Cuts peak allocation on giant batches after F1. | `rss_mib`, `copy_s` |
| 10 | F7 | Invalidate each job id once per successful file | Repeated cache invalidation on dense files. | `jid_invalidate_s` |
| 11 | F8 | Coalesce job-store checkpoints | Snapshot I/O off the mutation path. | `job_store_wait_s`, `job_store_hold_s` |
| 12 | F9 | Linearize find-buffer parsing | Removes repeated prefix copies on the stream. | `feed_s` / `decode_s` |
| 13 | F10 | Capped discovery fast path | Stops catch-up discovery from scanning past the cap. | pending-rescan count and census `ingest=` queued depth |
| 14 | F11 | Coalesce archive-members sidecar writes | Fewer durable sidecar writes; safety flags stay immediate. | `members_store_wait_s`, `members_store_hold_s` |
| 15 | F12 | Adaptive heap release | Keep end-of-file trim; skip mid-file trim when pressure is low. | `heap_release_s` |
| 16 | F13 | O(1) ingest census | One snapshot per tick instead of a walk. | `census_s` |
| 17 | F14 | Once-per-tick cgroup admit | Stop re-reading cgroup and re-summing in-flight bytes per file. | `admit_probes`, `fill_block` |
| 18 | F15 | Collapse microstructure (separate pairs) | DCGM slices, one partition pass, drop a redundant sort, fixed proc column order. | `collapse_*` |
| 19 | F16 | One inode map for streaming rescan | Drops a second full fd capture. | `rescan_s` |
| 20 | F17 | Carry-stable counter-delta categories | Stop refactorizing stable keys on every flush. | `delta_s` |
| 21 | F18 | Collapse duplicate proc keys inside the batch | Shrinks COPY upsert work when keys repeat. | `proc_rows`, `copy_s` |
| 22 | F19 | Positive sealed-member match | Skip full populate on a conclusive hit. | `populate_s` |
| 23 | F20 | Single-pass sealed populate | One member feed and one sidecar write. | `populate_s` |
| 24 | F21 | Bisect a failed bulk write | Error path only. Near zero on the steady state. | failure logs |
| 25 | F22 | Durable retry backoff | Stability under faults, not steady MiB/min. | `dead_letter_*` |

Not ranked as speedups: the harness (fresh-plan group 1) and the production
A/B protocol (groups 25 and 26). Those are how a rank is accepted.

Design detail for each ID stays in
`.cursor/plans/sync-timedb-fresh-throughput-campaign.plan.md`.

## Protocol

- One writer. Never run two ingest versions against the same database or archive.
- Arm A is the image built from current master. Arm B is exactly one candidate.
- Windows are equal and at least 6 hours, on both 04 and 01 when the change can
  affect either file shape.
- Compare MiB/min and the named hold for that rank, inside the same size tier.
  files/s alone is not the retain meter.
- If workload, host, or database conditions drift, run A again (A2) before
  keeping B. Roll back immediately on a correctness, archive, or stall regression.
- The retained commit becomes the next A.
- Local pytest and the regression battery are required before a candidate image.
  They do not decide throughput.

## Baseline meters (shipped with this wave)

New tokens are gated by the existing INI knob **`[PIPELINE] sync_ingest_telemetry = yes`**.
There is no second key and no production environment variable. Site INI `yes`
ships in the same image as the code.

| Token | Meaning | Log form |
| --- | --- | --- |
| `orm_rows` | Host plus proc rows that constructed a Django model in the telem-on bulk path | integer |
| `overlap_headroom_s` | `min(max(0, parse_elapsed_s - postgres_s), max(0, postgres_s))` | 1 decimal |

They are **not** write-phase keys. The analyzer prints
`tier_<tier>_median_orm_rows` and `tier_<tier>_median_overlap_headroom_s`
and does not add them into write share. `orm_materialize_s` and `postgres_s`
stay the phase clocks.

Images built before this change cannot emit the two tokens. Tonight's baseline
image is current master plus these meters, not an optimization.

Later ranks use the same telemetry gate. `stage_ddl_s` and `reconnect_s` are
write-detail clocks: the analyzer medians them per size tier and does not add
them into write share. `census_s`, `rescan_s`, `populate_s`, `admit_probes`,
`rss_mib`, and the store-lock totals are process-level. The analyzer reports
`median_<name>` for those, and sums `dead_letter_<kind>` on a status line into
`median_dead_letter`. Zero values are omitted from the log.

## Evidence ledger

Append one row per soak. Leave the rank table unchanged until a row contradicts it.

| Date | Plan | Sites | Window | Image / git | What was measured | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-15 | `sync-timedb-fresh-baseline` | 04 Horizon, 01 Stampede3 | scored ~8h, telem on | master + `orm_rows` / `overlap_headroom_s` | 04 dense overlap 131.7s, conflict_insert 64.3s, orm 36.8s. 01 dense overlap 2.4s. 01 `feed_s` is telem-on and untrusted. | Re-rank: F2, then conflict_insert, then F1 |
| 2026-10-16 | `f2-overlap-reject` | 04 Horizon, 01 Stampede3 | 8h, telem on, pool 64 | overlap image, later removed from HEAD | 04 dense elapsed 198.5s, overlap 76.3s, 15.0808 MiB/min. 01 dense elapsed 7421.6s, proc_merge 4079s, 75.1626 MiB/min (was 128.2226). Telem was on in both arms. | Reject F2. Next code change is conflict_insert |
| 2026-10-17 | `conflict-insert-shrink` | 04 Horizon, 01 Stampede3 | 8h, telem on | conflict_insert + F1 image (no deadlock fix) | 04 dense conflict_insert 38.3s, orm 0. 01 64 MiB-1 GiB proc_merge 139.5s, start_s 129.4s. | Retain both. Next: in-place proc merge |
| 2026-10-19 | `soak-readout-next` | 04 Horizon, 01 Stampede3 | 8h, telem on, rejected parse-cut image | delta/ARC parse cut (`cda5da57`) | 04 dense conflict_insert 43.750s (bar 38.3), stage_ddl 5.500s, write_setup 8.750s, 16.4921 MiB/min. 01 48.6328 MiB/min (was 133.6808). reconnect_s 0 on every line. | Do not retain. Revert the parse cut before the next image. Next plan: `.cursor/plans/soak-readout-next.plan.md` (stage reuse and columnar COPY). |
| 2026-10-20 | `soak-readout-next` | 04 Horizon, 01 Stampede3 | 8h, telem on, stage-reuse + columnar image | F4 stage reuse + columnar COPY bodies (parse cut reverted) | 04 dense n=483: stage_ddl 0.000 (was 5.500), write_setup 0.000 (was 8.750), conflict_insert 64.500, copy 3.200, db_execute 103.800, gap_proxy 36.100, 18.0079 MiB/min. nz stage_ddl lines 302/4169 (was 2917/6169). 01 MiB/min 142.8009 (bar 133.6808); dense/giant feed_s 498.850 / 1666.350; nz stage_ddl 2/1912. decision_next 04=`write_timescale_path`, 01=`parse_hold_feed_s`. | Operator retain both cuts (meters passed; conflict_insert guardrail miss overridden). Next cut: 04 conflict_insert / Timescale write path. |
| 2026-10-20 | `ft-ingest-parse-write-split` | _(code land; soak pending)_ | n/a | tree + battery `day-close-loop-regression-battery-20261020-001427.log` | Parse pool + write stage (`sync_ingest_write_pool_processes` default ¼ parse, `parse_queue_max` 32, `parse_flush_max_mib` 512); retired `sync_bulk_create_batch_size`; census `parse_put_block_*`. | **Code landed** — production soak (T1/T2 + ≥8h CPU≥20 on 04) still required before retain/reject. |
| 2026-10-20 | `write-telem-cross-thread` | _(code land; soak pending)_ | n/a | write timers on `IngestWritePayload.write_timing` | Stage-split soaks showed `postgres_s=0` / `conflict_insert_s=0` on parse-thread outcome lines while DB backends were busy; ferry snapshot across the write queue. | **Do not reject** F18 or stage-split. Pending re-soak: require non-zero dense write tokens before scoring F18 (≤32.250) or stage-split CPU. Cite `docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md` T1/T2 after deploy. |
| 2026-10-21 | `feed-s-telem-tax` | _(code land; soak pending)_ | n/a | deferred campaign flush + exclusive nested `_held_parse_stage` | Removed per-line `_parse_stage_campaign_lock` from `_add_parse_stage_s`; nested `proc_merge_s` pauses outer `feed_s`. Do **not** re-land E6/E7/`cda5da57`. | **Code landed** — operator soak at **`sync_ingest_pool_processes=64`** on same CODE redeploy; meter = dense/mid `median_feed_s` drop vs `/tmp/ingest-rate-parse128-before64.txt` and `sync_full_ingest_per_min` / MiB/min not below prior losing baseline; expect `futex_pct` down vs ~95% at parse=128. |
| 2026-10-24 | `phase0-gc-serializer` | dev host (28 cores), Codon on | 15-min loaded48 soaks, GC trace, RSS sampler | `test_runs/phase0/PHASE0_REPORT.md`; battery `day-close-loop-regression-battery-20261024-200201.log` | Per-file `gc.collect()` (`release_spawn_pool_worker_memory`) held ~80% of wall at width 64 (178 collections, 852 s). `sync_ingest_malloc_trim_after_file=no`: w64 244.6 / 225.0 MiB/min vs release-on 84-101 (n=5); w32 175.3 / 139.0 / 127.3; RSS max 24 GB (on) vs 51-64 GB (off, w64) / 32 GB (off, w32). `malloc_trim` is a no-op under the image jemalloc. | **Per-site INI**: Stampede3 `no` at width 64; Horizon stays `yes` until cgroup headroom known. Meter = `sync_full_ingest_mib_per_min`. |

## Next plan after the baseline

F2 was tried and rejected. The one-chunk writer is gone from the current tree.
`conflict_insert` and F1 are retained on the 2026-10-17 image: 04 dense
`conflict_insert_s` 38.3s (was 64.3s) and `orm_materialize_s` 0 with `orm_rows`
0. That image did not contain the proc-upsert deadlock fix. The one-pass
host_proc merge is in the tree after that baseline. Retain it only if 01
64 MiB–1 GiB `proc_merge_s` is at least 20 percent below 139.5s with n at
least 12, and 04 dense `conflict_insert_s` stays at or under 38.3s. The
following untried cut is the giant-file `start_s` scan (129.4s). Telemetry
was on in the 2026-10-15, 2026-10-16, and 2026-10-17 windows. Pre–feed-s-telem-tax
01 `feed_s` medians (telem-on) remain untrusted for ranking; post-fix soaks
may use telem-on `feed_s` as exclusive wall. The 2026-10-19 parse-cut image is not
retained. Stage reuse and columnar COPY are retained on the 2026-10-20 soak
(operator keep): 04 dense `stage_ddl_s` and `write_setup_s` are both 0.000;
nz `stage_ddl_s=` lines fell from 2917 to 302. The next cut is 04
`conflict_insert_s` / Timescale write path (`decision_next=write_timescale_path`),
after the feed-s-telem-tax + parse=64 soak meters.

## Historical campaign (through 2026-10-14)

The text below is the previous campaign. Keep it. Do not use it to choose F1–F22.

### Archived campaign text

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
| E1 | Ingest width below 96 raises durable files/s and cuts lock wait | lower CI bound files/s; p95 lock wait | INI `sync_ingest_pool_processes` + scaling study | **shipped default 48** — knee artifact `test_runs/sync_timedb_bench/knee_6364b97ef38f45c4b0399ea3d20044ca.json` (widths 48/64/80/96, 5 replicates). Winner **48**. **hpcperfstats04 2026-09-24 steady @ width 96:** window **289 min**, listend **6.15**/min, full-ingest **1.65**/min, ratio **3.72 LOSING** (warm-up ~16 min had ~5.3 ingest/min — not steady). Mid-file walls multi-ks with `postgres_s` ≈ half. The 2026-09-24 knee shipped registry/example **48**. The 2026-10-14 registry default is **64** as an operator override of the later flat WIDTH_SWEEP, not a new knee. Do **not** raise toward 96. Analyzer now emits `sync_full_ingest_mib_per_min` + size-tier median elapsed/postgres for post-48 soak decisions. |
| E2 | Closed-book residual ≤5% mid-size cohort | residual fraction | write/lock/parse telemetry | **pass after telem fix** — `test_runs/sync_timedb_bench/e2_closed_book_da92427028ed4dfbb430053defac3d72.json` (wall≈148s, **23 phases**, `residual_frac=0.0`, `residual_ok=true`). Dominant campaign holds: `parse_feed_s`, `parse_build_df_s`, `parse_proc_merge_s`, then `write_postgres_s` / `write_db_execute_s`. (Prior empty-phases artifact `…8768f659…` superseded.) Note: campaign phase seconds can exceed wall when workers run in parallel — residual uses closed-book accounting against wall |
| E3 | Non-overlapping listend rate revises LOSING margin | listend/ingest ratio | analyzer (done) | **complete (LOSING)** — fixed analyzer + timestamps; 02/04 ~4.5h post-redeploy pastes with `--since-minutes 1440` both **verdict_full_ingest=LOSING** (ratios 2.3 / 66.9); absolute rates diluted; undiluted ~3.2h recompute: **02 ratio 2.76**, **04 ratio 1.24** (both LOSING; archive_done 0). Post-2026-10 listend idle lines add **`unlink_bytes_last_10=`**; analyzer also prints **`verdict_full_ingest_mib`** (sealed MiB/min vs sync ingest MiB/min). |
| E4 | Split ORM vs DB execute shrinks false “postgres” blame | phase shares | ingest write phases | helpers + process-global write telem shipped; E2 now shows `write_postgres_s` / `write_db_execute_s` / `write_orm_materialize_s` populated |
| E4b | host/proc COPY insert beats ORM bulk_create on large writes | write-only `mean_write_s` | Podman `--host-insert` / `--proc-insert` | **retain** — `host_data_insert_ab_fde1031c9ce64cf89ae2d3f572c84b12.json` (100k rows, baseline≈21.4s → candidate≈15.0s) and `proc_data_insert_ab_e59d4a4941444365947636f42f14280c.json` (100k rows, ≈24.4s → ≈11.4s). Defaults ON (opt-out `HPCPERFSTATS_SYNC_HOST_DATA_COPY=0` / `HPCPERFSTATS_SYNC_PROC_DATA_COPY=0`). Multi-writer gate rejected (width-48 worse than 96; ClientRead idle watches; prior writer-lock removal). Loaded48 1h@48 insert A/B files/s **flat** while `postgres_s` mean **183→1.2** — do not use files/s as write meter. **hpcperfstats04 overnight decision soak** (plan `04-overnight-decision-soak`): enable outcome telem via INI `sync_ingest_telemetry=yes` (default still off); analyzer emits `decision_next` |
| E4c | Log-visible write/COPY/parse telem + overnight decision pack | `decision_next` + phase medians | `ingest file` log tokens + `measure_pipeline_ingest_rate.py` | **CODE ready** — write phases (`orm_materialize_s`/`db_execute_s`/`copy_s`/`conflict_insert_s`) + parse stages + insert arms on outcome lines when INI **`sync_ingest_telemetry=yes`** (default off; one knob; not dual env). Operator: ≥8h soak on 04 then `--since-minutes 480` → lock next CODE from `decision_next` |
| E5 | Reduce day-close overlap contention | flock timeouts / day-close wall | day-close inflight | **knobs matrix complete** — `knobs_cf29f0b42561480f99280ce0616c973d.json` @ ingest width 48 ×3 reps. Candidates (smallest within 5% of peak): day_close **1**, archive **1**, populate **1**, bulk **2000**. Curves for day_close/archive/populate nearly flat (~0.045–0.047 files/s); bulk slowest absolute (~0.023–0.025) — treat as campaign hint only, early-shutdown clock excludes most day_close wall |
| E6 | Evidence-led `feed_line` / `feed_s` cut | **hold_seconds** (`feed_s`) | parsing hot path | **hold-seconds rescore 2026-09-26 retain=true** — host-unit microbench `e6_parse_feed_ab_7ab967581383443ca55bda88de23cf02.json` (base feed_s mean≈0.0137 → cand≈0.0126). Gate `hold_seconds_retain_candidate`. Historical files/s A/B `…31a8582e…` archaeology. |
| E7 | Residual `proc_merge_s` / `build_df_s` after Wave4 | **hold_seconds** (`proc_merge_s` AND `build_df_s`) | parsing hot path | **hold-seconds rescore 2026-09-26 retain=false (AND)** — `e7_proc_build_ab_dfea4364164845139cb880d7318b40d1.json`: `retain_proc_merge_s=true` (0.022→0), `retain_build_df_s=false` (0.0243→0.0252). **OnlineMerged kept** — parsing-collapse law forbids reintroducing batch dedupe on hot path; build_df columnar not a CI win on this corpus. Historical files/s A/B archaeology. |
| E8 | Cut `delta_s` + `collapse_s` after 04 overnight lock | mid-tier median **`delta_s`** and **`collapse_s`** seconds (`retain_delta_s` AND `retain_collapse_s`) | `_apply_counter_deltas` / `_collapse_stats_with_deltas` | **retain** — `e8_delta_collapse_ab_a4c17ad6f4714829a343e11943914970.json` (host-unit Horizon frame ×5): delta mean **0.089→0.058**, collapse **0.158→0.063**; both hold gates true. CODE: identity short-circuit in `_groupby_sum_min_count` + categorical group keys / numpy wrap in `_apply_counter_deltas`. files/s report-only. Harness `--e8`. |
| E9 | Post-E8 overnight decision soak on hpcperfstats04 | `decision_next` + E8 T0 mid-file `delta_s`/`collapse_s` vs 2026-09-26 cohort | INI **`sync_ingest_telemetry=yes`** (one knob) + `measure_pipeline_ingest_rate.py` | **CODE ready / soak pending operator** — analyzer ranks `parse_unaccounted_investigate` when residual ≥ top named hold; `telem_incomplete_re_soak` when parse tokens present but write phases absent. Single redeploy: E8 + single-INI telem + analyzer + site INI yes. Morning: `logs --timestamps` → `--since-minutes 480`. Plan `04-post-e8-overnight`. |
| E10 | Multi-gap residual holds + optional multi_dev collapse cut | mid-tier medians for **new** hold set; `decision_next` named (not unaccounted) after one soak | `_held_parse_stage` parts + `batch_iter_s` + analyzer | **CODE** — ship collapse sub-holds (`collapse_normalize_s`…`collapse_sort_s`; derived `collapse_s`), `chunk_setup_s` / `take_cols_s` / `heap_release_s`, write `batch_iter_s`; `telem_incomplete` also when **all** phases absent; multi_dev category-before-groupby on host sum (`assume_duplicates=True`). Plan `04-parse-unaccounted-instrument`. **Soak #2 (2026-09-29) FAIL** — tokens present; top named=`collapse_gpu_s`; residual still dominates → `decision_next=parse_unaccounted_investigate`. Dense Horizon files sat in former `lt_64mib` (~13–17 MiB). |
| E11 | Collapse GPU cut + residual wave2 + dense mid-tier | mid-tier medians for `collapse_gpu_s` / new holds; dense `8mib_64mib` drives decision | `_collapse_nvidia_gpu_vectorized` + heap cadence + analyzer | **CODE** — single-pass NVIDIA event-class map; heap cadence; `write_setup_s` + `jid_invalidate_s` holds; analyzer `lt_8mib` / `8mib_64mib`. Plan `04-collapse-gpu-residual-wave2`. **Soak 2026-10-01 (partial): product PASS** — dense `collapse_gpu_s` ~172–217 s (was ~850–1230), `parse_unaccounted_s` ~0–11 s, `heap_release_s` ~12–18 s; **decision FAIL** — 4× `64mib_1gib` hijacked pack → false `write_timescale_path` while ClientRead idle. |
| E12 | Analyzer dense decision + ingest-fast jid invalidation | `decision_next_dense` + lower `jid_invalidate_s`; `delta_s` no-cut unless retain | `measure_pipeline_ingest_rate.py` + `cache_utils` ingest_fast | **SOAK PASS (2026-10-02, 04)** — `8mib_64mib` n=362; `decision_next_dense=parse_hold_delta_s`; `jid_invalidate_s` median **40** (was ~102–129); top parse **`delta_s` ~134**. Plan `04-e11-soak-e12-parse`. |
| E13 | Cut dense `delta_s` after E12 lock | `tier_8mib_64mib_median_delta_s` down vs **133.75**; `decision_next_dense` stays named parse hold | `_apply_counter_deltas` lexsort + vectorized group diff | **SOAK PASS (2026-10-05, 04, ~70.6h full-gate)** — n=**102**; `median_delta_s` **17.75** (was **133.75**); `decision_next_dense=parse_hold_collapse_gpu_s`; throughput **LOSING** (admit stall, not parse). Plan `04-e12-soak-e13-delta` + readout `04-48h-soak-readout`. Log `/tmp/pipeline-soak-48h-20261005.log`. |
| E14 | Cut dense `collapse_gpu_s` after E13 lock | `tier_8mib_64mib_median_collapse_gpu_s` down vs **~24.05** full-gate | `_collapse_nvidia_gpu_vectorized` | **CODE (2026-10-05)** — `groupby(..., sort=False)` on max/mean/OR NVIDIA partitions (final order from `collapse_sort_s`). Plan `04-e14-collapse-gpu-parse`. Post-P0 admit verify before live ratio is decision-grade. |
| E15 | Unique-key NVIDIA collapse after E14 soak | `tier_8mib_64mib_median_collapse_gpu_s` down vs **40.25** (n=450) / **43.2** (8h) / **38.6** (full) | `_collapse_nvidia_gpu_vectorized` | **CODE (2026-10-08)** — when group keys are unique, project the rows and mask `clocks_event_reasons` in one array pass (NaN/blank → 0.0). Duplicate keys keep max/mean/OR `groupby`. Plan `e15-approach-lock`. **Soak 2026-10-09:** dense `collapse_gpu_s` **5.2** (n=1071) vs 40.25. Ratio **2.62** (480m) / **2.90** (full), still LOSING. |
| E16 | Vectorized counter-delta carry after the post-E15 soak | dense `delta_s` median ≤ **25.0** (half of **49.9**, n=1071). Ratio strictly below **2.90**. Not expected to reach 1.0 | `_apply_counter_deltas` carry blocks | **SOAK PASS (2026-10-10, 04)** — full dense n=517: `delta_s` **21.9**; ratio **2.2584** vs 2.90. Last 480m n=183: `delta_s` **18.6**, ratio **1.6310**. Both still LOSING. Pool stayed full (hot 28/32 + catchup 16/16); DB had 378 ClientRead-idle sessions. Plan `04-post-e15-soak-e16`. |
| E17 | Reduce the top post-E16 dense hold, `heap_release_s` | profile-selected body ≥50%; dense `heap_release_s` ≤ **12.4** (half of **24.8**, n=517). Ratio below **2.2584** | `_release_ingest_worker_heap` collect/trim cadence | **SOAK (2026-10-12, 04)** — dense n=243, `heap_release_s` **13.0** vs gate **12.4** (narrow miss; E16 was 24.8). Full ratio **16.6076** vs 2.2584, LOSING. Last 480m completed 0 full ingests. Cgroup pinned (file cache 99.1 GiB, headroom 194 MiB, both admit gates shut, catchup inflight median 0). DB 372 ClientRead idle. Admission is a confounder, not an import miss to retune. Plan `04-post-e16-soak-e17`. |
| E18 | Pointer-identity group codes cut completed-file `delta_s` | local `retain_delta_s`; production dense `delta_s` below **28.7** | `_apply_counter_deltas` | **CODE (2026-10-12)** — on a 230400-row Horizon body the string category cast was ~60% (0.105 s of 0.175 s). Coding the StringArray object buffer by pointer identity, then remapping to sorted category codes: mean **0.1749→0.0996**, candidate upper **0.1048** ≤ 95% of baseline lower **0.1699**, `retain_delta_s` true, frame equality held. Collapse on that output **0.270→0.261** s; traced peak unchanged. Production 8h soak pending. Admission unchanged. Plan `04-post-e17-diagnosis`. |
| CONTENTION | FT contention P0–P3 sequential A/B @ width 48 | files/s + store wait | caches / park / manifest / members shard / … | **complete** (`--contention`). **retain=true (original A/B):** `caches`, `thread_id`. **Loaded-96 land 2026-09-24 (operator keep, no revert):** `members_shard`, `log_drain`, `manifest_io`, `claim_heap`, `tar_ex` (restaged) — concurrency review **pass** after manifest snap/save + log drain barrier fixes. Still out of tree: `park_resume`, `pool_split`, `discover`, `telem_tls`. Loaded-48 queue had retain=false on these five; land is operator override for width-96 soak (`tests/run_loaded96_landed_compare.sh`, H1 pin `a20a234d…`). |
| WIDTH_SWEEP | Same-tree loaded soak @ 48/64/96 × 3h vs this-run @48 | files/s + occupancy; report-only gate | `tests/run_loaded_width_sweep_ab.sh` (nohup+setsid) | **complete 2026-09-25** — artifact `test_runs/sync_timedb_bench/width_sweep_ab_0062cefe4a704ed793ae6b077ba4ef86.json`. 3h means ≈**0.2596** files/s at all three widths (48/64/96 flat); occupancy_ok=True sustained at each (peaks 48/62/96). Report-only `gates` **64=False, 96=False** (neither clears E6 lower-CI vs this-run @48). Prefer **48** on the harness; more threads do not raise durable files/s. The registry default **64** (2026-10-14) is an operator override of that flat result, not a new knee and not a throughput claim. |
| QUEUE_SLOPE | Ingest queued depth falls over a multi-hour window while completions still move | census `ingest=` queued slope ≤ 0 | `zadd_ingest` catchup cap + analyzer stop-gate | **CODE (2026-10-14)** — 04 census queued about **+6.6/min** while `total_completed` about **+3.6/min** and `verdict_full_ingest=WINNING`. New catchup stops at `discover_ingest_capacity_limit` (live **3000**). Analyzer returns `stop_ingest_rate_watch_archive` only when that slope is present and ≤ 0. `host_data` `_STAGE_INSERT` waits until a post-admission window is still non-negative. |

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

1. ~~Knee confirmation (≥5 replicates, widths 48–96)~~ — done; candidate **48** (flat curve). Registry default is **64** as an operator override of that flat WIDTH_SWEEP, with no throughput gain claimed. Do not raise toward 96 without new evidence.
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