# Operator sync_timedb stall verify (tiered catch-up)

Backlog catch-up sites (months of incomplete days) can run **many hours** of valid giant-archive work before an **idle spin** stall appears. Do **not** mark a deploy verified on a **15-minute T0 smoke alone**.

See also: `sync-timedb-change-regression-gate.mdc`, `sync-timedb-queue-orchestrator-contract.mdc`, [SYNC_TIMEDB_PARALLELISM.md](SYNC_TIMEDB_PARALLELISM.md), live plan [sync-timedb-queue-redesign](../.cursor/plans/sync-timedb-queue-redesign.plan.md).

## Queue orchestrator era (2026-08 cutover)

### T0 — 10-minute progress + status (primary)

Prefer these lines over firehose greps for backlog diagnosis:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'queue_orchestrator progress day=|queue_orchestrator status |queue_orchestrator census |archive_job_done |ingest per-file timeout' | tail -80
```

- **`progress day=`** — omit-zeros day counters (`gate_skip`, `ingest_handoff`, ingest outcomes, archive, day_close, reconstruct, …).
- **Day-close on `progress day=`** — LIST identities are full daily tar paths. Expect `dc_run=` when a day_close slot is filled (covers long pre-seal verify), `complete=` when filesystem + 32h ACK, `incomplete_raw=` when remaining closed raw (requeued, not ACK), `deferred_age=` / `yielded=` / `ingest_handoff=` / `verify_failed=`. `sealed=` / `tar_delete=` only when those steps ran. Status `day_close=4/61` is inflight/queued, not a calendar day.
- **`status`** — Redis `current/queued` (`ingest_hot` / `ingest_catchup` / …), `busy=` (incl discover), `orphan_inflight`, queue `*_q_delta`, `oldest_day` / `oldest_age_s`, optional **`fill_block=`** (dominant ingest fill failure: `claim_none`, `skip_missing`, `skip_reband`, `skip_lock`, `skip_fp`, `band_cap`, `submit_err`). Set when a fill submits 0 while **`len(local) < pool`** and the ingest ZSET is deep (under-capacity, not only local-empty). Cleared when **Redis HLEN ≥ pool−1** (RC9).
- **`ingest fill empty deep_queue`** — rate-limited when local inflight is zero but Redis ingest ZSET is deep; includes `fill_block=` and per-reason `stats=` counters plus **`redis_hlen=` / `hot_used=` / `catch_used=`** census.
- **`ingest fill under-capacity`** — rate-limited when local inflight is **below pool** (may be non-zero) but ZSET is deep and the fill submitted 0; same `fill_block=` / `stats=` tokens and census fields.
- **`census`** — 60s depth; ratios are **inflight/queued**; `busy=` replaces opaque `local=I/A/D`.
- **`archive_job_done`** — single INFO per append job (`tar_bytes`, `members_source`, `mapped`/`to_add`/`appended`, `outcome=`). Do not require `archive_job_begin` / `archive_job_duty` / `Archived batch` at INFO.
- **`ingest per-file timeout`** — includes `size_bytes=` and `bytes_per_s=` for size/time judgment.
- **Not primary:** `Archive/delete gate: skipped` and `handoff_to_ingest … reason=gate_skip` (demoted; use day `gate_skip=` / `ingest_handoff=`).

**Agent stall heuristics (2–3 status lines):** (1) queues non-zero + Δqueued≈0 + no day acks → stall; (2) inflight without `busy=` → orphan; (3) `gate_skip` without `ingest_handoff` → ACK thrash class; (4) empty queues + no `reconstruct_enq`/`incomplete_seen` on backlog site → false done.

## Queue orchestrator era — remaining tiers

Production is **one** `sync_timedb.py` process per `archive_dir` (`run_sync_timedb_queue_orchestrator`, exclusive flock). Dual CLI ``backlog``/``current``, proximity heartbeat, `ArchiveJanitor` tick, handoff pins, and oldest-day **chunk gate** are **retired**. Operator census is Redis **`job:v1`** + disk/DB reconstruct predicates — empty job keys ≠ caught up.

### Post-deploy tiers (orchestrator)

| Tier | When | Pass criteria |
|------|------|---------------|
| **T0 smoke** | T+15 min after deploy | Pipeline up; **exactly one** orchestrator flock holder; Redis reachable; at least one ingest lease progress **or** reconstruct logged incomplete work with non-empty matching queue kind; no second `sync_timedb` CLI for the same `archive_dir` |
| **T1 progress** | T+4 h **or** after first append/day_close progress on head day | Hot and/or catchup ingest ZSET depth trending (or reconstruct shows complete); append/day_close LISTs not wedged forever with filesystem remaining-raw; `progress day=` shows `complete=` cadence on age-eligible no-remaining-raw days (not fake `sealed` ACK); no dual-process flock fight |
| **T2 catch-up** | T+24 h or when head day advances | Cadence of completed ingest leases continues; day_close jobs drain age-eligible days; no persistence wipe / contract bump used as “fix” |

```bash
# T0 — one process + job:v1 census (INI paths first; full logs never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"archive_dir\", cfg.get_archive_dir_path())
print(\"daily_archive_dir\", cfg.get_daily_archive_dir_path())
"'

docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'sh -lc "ps -ef | grep -E \"[s]ync_timedb.py\" || true"'

docker compose -p hpcperfstats -f docker-compose.yaml exec redis \
  sh -lc 'P=hpcperfstats:sync_timedb:job:v1; redis-cli -n 1 --scan --pattern "${P}*" | head -50; echo ---; redis-cli -n 1 ZCARD ${P}:queue:ingest; redis-cli -n 1 ZCOUNT ${P}:queue:ingest -inf 999999999999999; redis-cli -n 1 ZCOUNT ${P}:queue:ingest 1000000000000000 +inf; redis-cli -n 1 LLEN ${P}:queue:append; redis-cli -n 1 LLEN ${P}:queue:discover; redis-cli -n 1 LLEN ${P}:queue:day_close; echo ---leases; redis-cli -n 1 --scan --pattern "${P}:lease:*" | wc -l'
# Full keys: hpcperfstats:sync_timedb:job:v1:queue:ingest
#            hpcperfstats:sync_timedb:job:v1:queue:append
#            hpcperfstats:sync_timedb:job:v1:queue:discover
#            hpcperfstats:sync_timedb:job:v1:queue:day_close

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'queue orchestrator|job:v1|orchestrator flock|reconstruct|ZADD|day_close|Redis.*(down|unavailable)|sys.exit' | tail -80
```

**Pass (T0):** one `[main]` sync_timedb; flock acquired; job keys present or reconstruct explains empty; SIGTERM of the process yields exit **143** (not 0); ingest success logs / file-complete marks grow when listend is on. **Local agent T0** is `tests/run_sync_timedb_regression_battery.sh` (host pytest). **T1/T2** remain post-deploy on the backlog site (this Mac cannot claim production catch-up). **Fail:** two orchestrators, Redis down without exit, CLI `backlog`/`current` still in supervisord, no-arg run that only walks the last 5 days, or `deferred_age` burning day_close attempts.

**Fail (T0 — day-close never completes, hpcperfstats03 2026-08-27):** census `day_close=4/61` with **no** `complete=` on `progress day=` lines; drain parsed `identity[:10]` as `/hpcperfst` (LIST identities are `/…/YYYY-MM-DD.tar`); workers ACK fake `sealed` after `only_when_no_remaining_raw` no-op seal; `tar_drop` count 0. **Pass after fix:** age-eligible no-remaining-raw days show `complete=` (and `tar_delete=` when tar-drop ran); remaining-raw days show `incomplete_raw=` and stay on the LIST (attempt 0); `dc_run=` appears for tar-path identities while verify is in flight; cheap enqueue does not RPUSH today/yesterday when `sync_day_close_min_age_hours=32`.

**H1 (seal-defer confusion):** Narrow greps on `day-close` threads alone miss `complete=` / `tar_delete=` that often land on **`reconstruct-coordinator`** `progress day=` lines. Seal deferred while closed raw remains can be healthy — look for `incomplete_raw=` / `yielded=` and LIST requeue, not only `sealed=`.

**Fail (T0 — day-close reconcile slot stall / H6, hpcperfstats04 2026-08-29):** Redis shows `day_close` LIST backlog + **exactly** `sync_day_close_max_inflight` leases for days with **no** day_raw_removal manifest advance and sticky `day_close=N/M` while ingest/append move. py-spy: `day-close_*` in `tarfile.getmember` ← `_copy_union_member_into_tar` ← `rebuild_daily_tar_member_union_in_place`. **Pass after fix:** greppable `stage_progress … advancing=true|false`, `claim`/`vacate`/`stage_enter`/`stage_exit`; after long **confirmed** no-progress (`stall_confirmed`) cooperative `yielded` vacates the lease (not a wall-clock abort from merge start while `members_done` climbs). Limitation: hang **inside** one named GNU tar extract cannot yield until that subprocess returns.

**Fail (T0 — stale open daily tars, 2026-09-02):** hpcperfstats04 `stage_progress … advancing=true` on `reconcile_merge` (~70s/member, `members_done` still climbing — do **not** stall-confirm). Root: per-name `tarfile.getmember` union copy. hpcperfstats02 **append LLEN** ~3696 with mixed-host **FIFO** so fill `requeue`+`break` on the first other-tar item (batch size 1); `wait_on_ingest` yield is correct until append drains. **Pass after H1/H7:** named GNU tar extract; day-keyed append batches; append LLEN trends down; merge cadence ≫ 1 member/min while `advancing=true`.

```bash
# T0 — day-close complete vs remaining-raw (full logs never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'queue_orchestrator progress day=.*(dc_run=|complete=|incomplete_raw=|deferred_age=|yielded=|verify_failed=|tar_delete=)|queue_orchestrator day_close tar_drop' | tail -80

# T0 — day-close H6 progress / stall-confirm (full logs never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'queue_orchestrator day_close (claim|vacate|stage_enter|stage_exit|stage_progress)|stall_confirmed' | tail -80
```
**Fail (T0 — boot sealed-populate stall, hpcperfstats03 2026-08-25):** hours of `[sync_timedb:main]` sealed `populate_source_decision` / `populate_source=sealed` **without** `queue_orchestrator boot discover submitted` / `boot discover seen=` / ingest fill; `ps` shows **archive-pool only** (no `ingest-pool`, no `populate-pool`); Redis ingest ZCARD/bands **0** while append LIST may already be deep. Root cause was sync boot discover **before** pools + worker `populate_and_wait` on classify. After fix: expect `boot discover submitted` early, populate-pool + ingest-pool alive within minutes, and fill/leases without MainThread sealed streams.

**Fail (T0 — post-boot silence / discover_bg Lock deadlock, hpcperfstats03 2026-08-26):** logs show `populate-pool started` then `queue_orchestrator boot discover submitted` (or hang after pools with that log if an older build logged it **before** submit) then **no census / ingest / append / `boot discover seen=` for hours**. `ps` shows ingest+populate+archive kids alive. py-spy MainThread **idle** at `_discover_executor` ← `_submit_background_discover` (nested non-reentrant `_discover_bg_lock`). Redis: discover `LLEN=1` unclaimed, append may be deep, leases/inflight **0**, no `discover-bg` thread. **Pass after fix:** submit returns; `boot discover submitted` only after submit; fill census/leases appear within minutes; MainThread not stuck in `_discover_executor`.

**Fail (T0 — ingest empty + append flood / MainThread day_close find, hpcperfstats03 2026-08-26):** after Lock fix: one census, hours of populate/archive skips, **zero** `ingest file path=`, **zero** `boot discover seen=`, append LIST ~70k. py-spy MainThread in `_drain_append_ready` → `enqueue_day_close_if_needed` (default FS probe) → archive-wide `iter_find_printf_records_streaming`. **Pass after subsystem-threads fix:** `ps`/`top` show titled `thread:ingest-coordinator`, `thread:append-coordinator`, `thread:day-close-coordinator`, `thread:reconstruct-coordinator` (plus `discover-bg`); MainThread only populate reap / pause recycle / death watch; append drain uses **cheap** day_close (`filesystem_complete=False`, Redis dedupe ≤1 per tar); ingest ZADD/fill resumes while append drains; day_close **workers** own remaining-raw find. Do **not** paste `REPLACE_WITH` / `SAMPLE_PATH` placeholders into classify commands — use a real baked path from Redis/append LIST.

**Fail (T0 — gate-skip append ACK thrash, hpcperfstats03 2026-08-26 evening):** census `ingest=0/0` while append LIST ~68k slowly falling (~11 paths/min); flood of `Archive/delete gate: skipped`; **zero** `ingest file path=` in full pipeline log buffer; sticky `discover=0/2` with orphan `rescan|…|mtime=*` inflight leases; optional cosmetic `thread:thread:reconstruct-coordinator` double prefix. Classify append LIST head: `needs_ingest=True`, `gate_ready=False`, `listend_db_ingest=True` — gate is **correct**; bug is append ACK without ingest ZADD handoff. **Pass after gate-skip handoff fix:** gate skips still log; append drain calls ingest handoff before ACK; ingest ZCARD/leases grow; discover inflight orphans reaped by reconstruct-coordinator; coordinator titles show single `thread:` prefix.

```bash
# T0 — gate-skip ACK thrash (INI paths first; full logs never --tail before grep)
grep -cE 'Archive/delete gate: skipped|ingest file path=|queue_orchestrator census' /tmp/pipeline-full.log || true
# Append head classify (use real path from LRANGE job:v1:queue:append 0 0)
# Expect gate_ready=False needs_ingest=True before fix; ingest ZADD after fix
redis-cli -n 0 HGETALL 'hpcperfstats:sync_timedb:job:v1:inflight:discover'
```

**Fail (T0 — ingest underfill / unused-slot gate RC7, hpcperfstats04 2026-08-30):** Redis deep ingest ZSET (`hot_q`/`catchup_q` ≫0) with **`HLEN` inflight ≪ `sync_ingest_pool_processes`**; status may show `ingest_catchup` stuck near **`catchup_cap`** while total inflight decays; RC1–RC6 helpers present (`catchup_dispatch_cap` / orphan reconcile / steal). Root cause: unused-slot elevated hot + catchup expand required `hot_submitted==0`. **Pass after RC7:** when ZSET is deep, local/HLEN ingest inflight stays near pool; catchup inflight may exceed reserved `catchup_cap` into unused slots; do not treat status `X/Y` alone as capacity — use **HLEN vs pool**.

**Fail (T0 — local vs Redis desync RC8):** Redis **HLEN ≪ pool** with deep ZSET, leases ≈ HLEN, **and** empty greps for `ingest fill under-capacity` / `fill_block=` / `ingest runtime steal` (fill/hygiene gated on local-full). Root cause: phantom local `ingest_inflight` maps block fill + hygiene. **Pass after RC8:** `local_inflight_desync` may appear briefly then clear; HLEN rises toward pool; under-capacity/steal logs only when still stuck for real claim/lease reasons.

**Fail (T0 — decay-from-full skip storm RC9, hpcperfstats04 2026-08-30):** status time series shows **full pool then decay** (e.g. `21/39+11/77` → `2/517+3/736`) while ZSET grows; hot heads **`isfile True`** (not missing-path livelock); RC8 fingerprints deployed; **no** `fill_block=` in status during sustained underfill. Root cause: `skip_fp` / `skip_reband` same-score requeue burns skip budget while workers finish real jobs. **Pass after RC9:** penalty requeue deprioritizes skip identities; HLEN stays near pool; status shows persistent `fill_block=` until HLEN recovers; under-capacity logs include `redis_hlen=` census; `oldest_day` advances.

```bash
# T0 — RC9 decay-from-full / fill_block (full logs never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'queue_orchestrator status |ingest fill (under-capacity|empty deep_queue)|ingest fill skip_(fp|missing|reband) penalty' | tail -80
redis-cli -n 1 HLEN hpcperfstats:sync_timedb:job:v1:inflight:ingest
```

**Pass (T1/T2):** head-day work advances without restoring chunk_gate / handoff_priority / janitor tick signatures as the only progress metric. Prefer `ZCOUNT`/`LLEN` + lease keys over historical `oldest_day_chunk_gate_stall` greps. **T0/T1/T2 are post-deploy** — they cannot be claimed green until the orchestrator image is running; a pre-deploy `job:v1` census of all zeros is a false negative, not a pass. **Do not** `ZCARD job:v1:ingest` — that short name is always empty; use `hpcperfstats:sync_timedb:job:v1:queue:ingest` (hot `ZCOUNT -inf 999999999999999`, catchup `ZCOUNT 1000000000000000 +inf`).

Sections below marked **Historical B-era** document pre-cutover signatures for archaeology; do not treat them as production law after the queue cutover.

---

See also (listend note retained):

**After listend Redis `monitor_identity` on `$` rotation (2026-08):** identity SET is **Redis-only** on the existing `$` schema-rotation / `recent_host` worker path. It must **not** change RabbitMQ ack timing, pause/resume watermarks, sample completeness, or the archive/DB-ingest gate. Pre-deploy still run `tests/run_sync_timedb_regression_battery.sh` as a **no-regression guard** when `listend.py` touched the `$` branch; post-deploy **no T2 stall campaign** is required for this feature alone — confirm listend still acks and archives `$` payloads, and Admin Monitor can join `monitor_identity:{fqdn}` when present (`$build` optional until monitor RPM).

## Pre-deploy (every PR touching sync_timedb)

```bash
cd HPCPerfStats && tests/run_sync_timedb_regression_battery.sh
```

Attach the `test_runs/day-close-loop-regression-battery-*.log` path to the PR or deploy ticket.

**After absolute INI pool-size redeploy (`sync_ingest_pool_processes` / `metrics_pool_processes` / `metrics_pool_maxtasksperchild` / `gunicorn_workers` / `listend_db_ingest_pool_processes`, 2026-08):** this is a **pool sizing** change, not a stall-campaign fix. Post-redeploy use **T0/T1 only** — confirm ingest still advances (`chunk_elapsed_s`, `ingest file path=… outcome=…`, alive workers ≤ `sync_ingest_pool_processes`). Do **not** run a full **T2** multi-hour stall campaign solely for the absolute-INI cutover unless a new stall signature appears. Secondary caps/budget/overlap/`metrics_prewarm_*` keys are gone; stale site INI lines for those keys are ignored.

**After metrics pool terminate-wedge fix (2026-08-13):** on the affected site, after redeploy of `pipeline`, verify `update_metrics.py [main]` is **not** stuck in `Pool.terminate` / `p.join`, zombie census under `[main]` stays near **0**, and hygiene greps (`Zombie child reap`, `outcome=abandoned`, `pool created`) advance. Optional py-spy of `[main]` during/after a stall reset must **not** show `multiprocessing/pool.py` `_terminate_pool` join. See plan `update-metrics-pool-terminate-wedge`.

**After metrics `/pub` recycle zombie fix (2026-08-13):** post-redeploy in-container census under `update_metrics.py [main]` must show **`Z_COUNT=0`** after the boot `/pub` → `metrics-pool` recycle (not `N` defunct + `N` live with zombie PIDs outside the `Pool terminate SIGKILL` lists). Expect `outcome=abandoned` plus `Zombie child reap` / empty unreaped; use **`grep -F`** when counting `[worker:…]` titles (BRE `grep` of `[` → `Invalid range end`). See plan `metrics-pub-recycle-zombies`.

**After sync_timedb dedup-audit deploy (blocking/census split, persistence v6):** run the same battery pre-deploy; post-deploy use T0/T1/T2 below. Persistence v6 resets orphan `startup_*` sidecars on contract bump — expect one-time empty maint hints if the contract file was stale. No separate stall signature is expected from this audit alone; treat regressions like any other sync_timedb deploy.

**After chunk-cadence / RC-0 deploy (persistence v7):** contract bump clears poisoned `zero_host_ingest_mark` entries — expect a one-time re-gate/re-ingest of previously marked paths still on disk. Deleted-but-tarred raws (extract + re-ingest) are a separate operator restore decision after the fixed parser is live.

**After dual-zstd / exclusive restore deploy:** at most one `archive: daily_tar_restore begin` per calendar day until matching owner `end`; never two `zstd -d -o …decomp.tmp` for the same day. Classify: `zstd -d -c` = Redis populate (OK); `zstd -d -o …decomp.tmp` = sealed→tar restore (must be single-flight). On redeploy, stop pipeline, remove stale `*.tar.decomp.tmp` under INI `daily_archive_dir`, then up — see **T0 / T1 — exclusive daily tar restore (dual-zstd)** below.

## Chunk cadence attribution (T0 / T1)

Pair every `chunk_elapsed_s` sample with:

| Signal | What it tells you |
|--------|-------------------|
| `chunk_prewarm_elapsed_s=` / `chunk_ingest_elapsed_s=` / archive mapping elapsed | Phase split — ingest should dominate; multi-minute prewarm is unexpected |
| Gap from prior chunk end → next `chunk dispatch begin` | Between-chunk tax; dominate with `pending reconcile cap` lines |
| `pending cap supplement from snapshot n=` **without** `pending cap supplement replace` | Zero-yield cap (RC-1 signature) — full snapshot walked, queue unchanged |
| `post_finalize_reconcile` count per boundary | Should be **1** log line per finalize batch, with **one** following boundary cap (RC-2) — not 2–4 identical caps |
| `ingest file path=… stats_rows=… stats_rows_parsed=…` rate | Per-file throughput; `stats_rows_parsed>0` with `stats_rows=0` + collapse WARN is a delta path defect, not a legitimate zero-host |
| `giant pool supplement begin` during idle tail | RC-3 idle-slot fill while a **batch-own** path is still in flight; rare/absent with idle workers ⇒ supplement gated off |
| `giant pool supplement replenish` + `in_flight_giants=[]` for hours with **no** `chunk_elapsed_s` | **RC-E UnboundedReplenish** (pre-fix) — idle-slot OR without stop when only supplements remain; post-fix must see `giant pool supplement stop reason=batch_paths_complete` then chunk end |
| `giant pool supplement stop reason=batch_paths_complete` | Healthy RC-E stop: original batch paths done; remaining in-flight supplements drain; next chunk drains backlog via pending cap |

**Note:** above `sync_ingest_stream_duplicate_scan_bytes` (default 8 MiB), `parse_elapsed_s` spans the whole streaming parse+flush+DB loop — it does **not** exclude DB write.

```bash
# T0 — cadence attribution (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'chunk_elapsed_s|chunk_prewarm_elapsed_s|chunk_ingest_elapsed_s|chunk dispatch begin|pending reconcile cap|pending cap supplement|post_finalize_reconcile|giant pool supplement|stats_rows_parsed|collapsed to empty' | tail -80
```

## sync_timedb `--jid` smoke (ingest-only; stall T0/T1 N/A)

Surgical re-ingest for one job is **not** a backlog catch-up stall verify. Use this smoke instead of T0/T1/T2:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'sync_timedb.py --jid REPLACE_WITH_JOB_ID'
```

**Pass:** exit **0**; logs show `sync_timedb --jid: jid=… hosts=…`, host-scoped discover counts, and `done … ok=…` (zero matching files is success). Discovery uses the ±1h padded job window **plus** one earlier and later raw stats file per host. **Fail:** exit **1** (missing job / empty hosts / bad argv / all ingest failures). Expect **no** day_close / archive dispatch from this short-lived process. Continuous-deploy stall tiers are the **Queue orchestrator era** section above (not dual-mode ``backlog``/``current``).

## Historical B-era — Post-deploy tiers (pre-queue cutover)

> **Archaeology only.** Prefer **Queue orchestrator era** tiers. The table below and later B-era greps (`chunk_gate`, `handoff_priority`, `ArchiveJanitor`, dual-mode) apply to images **before** the greenfield coordinator cutover.

| Tier | When | Pass criteria |
|------|------|---------------|
| **T0 smoke** | T+15 min after deploy | Pipeline up; at least one `chunk ingest summary` **or** documented giant-chunk defer (not an error by itself); optional boot thread census (below) |
| **T1 progress** | T+4 h **or** after first giant `archive_job_done` for backlog head day | Oldest `waiting_on_ingest` day: `unprocessed` **not frozen** vs prior sample; no repeating `oldest_day_chunk_gate_stall` with same `incomplete_n` |
| **T1 progress (`current`)** | Same window while CLI ``current`` (newest-first) runs | Descending `epochs=` on `chunk dispatch begin`; `youngest_day_chunk_gate` / `youngest_day_chunk_gate_pad` (not ascending oldest-gate for ``backlog``); heartbeat sidecar/Redis advances with active work |
| **T2 catch-up** | T+24 h or when head day advances | New `chunk ingest summary` cadence; June-scale head day `unprocessed` trending down; `ingest_stall_watchdog` absent |

### T0 / T1 — ingest-first then async snapshot then day-close (CLI `current` / date-range, 2026-07)

**Failure signature (pre-fix):** on ``current``, empty pending → MainThread `day-scoped closed_raw` / `idle_finalize` / janitor `day_close` discover for older months **before** any `chunk dispatch begin` / `chunk ingest summary`.

```bash
# T0 — order of operations (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'chunk dispatch begin|ingest_going=yes|post-ingest startup archive scan|idle_finalize deferred|day_close_not_allowed|day_ingest_complete:idle_finalize|day-scoped closed_raw' | head -80
```

**Fail (T0):** `idle_finalize` / `day_close_not_allowed` absent while `day-scoped closed_raw` or `day_ingest_complete:idle_finalize` appears **before** the first `chunk dispatch begin`.

**Pass (T0):** first `chunk dispatch begin` (or `ingest_going=yes`) **before** day-close discover/execution; then `post-ingest startup archive scan begin|ready` (or `janitor: adopted post-ingest startup snapshot`); only after that may `day_ingest_complete:` / janitor discover proceed. Empty queue may log `idle_finalize deferred reason=awaiting_ingest_or_startup_snapshot` until unlock/snapshot; see empty-queue unlock section below.

**Pass (T1):** under ``current`` with older-month closed raw present, newest-day chunks continue while the async snapshot builds (`post-ingest startup archive scan begin` without blocking the next `chunk dispatch`); after `… scan ready`, janitor day-close for older days may start without starving ingest (populate BRPOP still prefers ingest-hot — see populate-queue section).

### T0 / T1 — empty-queue unlock day-close (no pending ingest, 2026-07)

**Failure signature (pre-fix, operator-verified on hpcperfstats01):** ``current`` with **no** host stats to ingest but many mutable `daily_archive/*.tar` files → loop of `idle_finalize deferred … ingest_going=False startup_snapshot_ready=False` then `Sleeping 30 s before exiting sync_timedb` → supervisord restart forever; never `ingest_going=yes reason=empty_pending_after_rescan`, never `post-ingest startup archive scan`, never day-close drain.

```bash
# T0 — empty-queue unlock + stay-alive (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'ingest_going=yes reason=empty_pending_after_rescan|kicking async post-ingest|awaiting startup_snapshot|day_close work remaining|idle_finalize deferred|Sleeping .* before exiting sync_timedb' | head -80
```

**Fail (T0):** empty pending + deferred forever with `ingest_going=False` and 30s exit while `daily_tar_count` &gt; 0 (INI `daily_archive_dir` still has `.tar` files).

**Pass (T0):** after confirmed empty rescan on ``current``/date-range, expect `ingest_going=yes reason=empty_pending_after_rescan` and `kicking async post-ingest startup archive snapshot`; then `awaiting startup_snapshot` and/or `day_close work remaining` polls (`EMPTY_QUEUE_DAY_CLOSE_POLL_SECONDS`, default **300s**) — **not** immediate 30s exit. When snapshot ready and day-close idle, expect the usual 30s / `run_once` exit.

**Pass (T1):** with a large daily-tar backlog and empty ingest queue, day-close discover/debt progresses (`janitor: day_close` / debt drain) without a restart loop; new stats files during poll resume `chunk dispatch`.

### T0 / T1 — idle empty stay-alive while day-close remains (2026-07)

**Failure signature (pre-fix):** pending empty after rescan → `Sleeping 30 s before exiting sync_timedb` → supervisor `break` → `archive_janitor.shutdown` while `janitor: day_close` / debt / in-flight DAY_CLOSE still running.

```bash
# T0 — idle stay-alive vs premature exit (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'day_close work remaining|Sleeping .* before exiting sync_timedb|once mode: no pending files|janitor: day_close|Archive janitor tick done|janitor_debt' | head -80
```

**Fail (T0):** `Sleeping 30 s before exiting sync_timedb` (or process exit) while the same window still shows active `janitor: day_close` / non-zero debt / in-flight day-close workers — without intervening `day_close work remaining` poll lines.

**Pass (T0):** when ingest queue is empty but day-close debt/inflight remains (and day-close is allowed), expect `idle ingest; day_close work remaining debt=… inflight=…; polling 300s` (5 min poll, no 30s exit). When both ingest and day-close are idle, expect the usual `Sleeping 30 s before exiting sync_timedb` (continuous) or `once mode: no pending files` (`run_once`).

**Pass (T1):** under ``current``/date-range after newest-first ingest drains, day-close for older months continues without a 30s supervisor teardown gap; new stats discovery during poll resumes `chunk dispatch` promptly.

### T0 / T1 / T2 — false `days_completed` + ghost reconcile thrash (stale `tar_dropped`, 2026-08)

**Failure signature (pre-fix, hpcperfstats04):** ingest idle (`pending=0`) but past-day mutable `.tar` remain with sealed siblings (`zst=True`) and raw-removal `phase=done`. Hints say `tar_dropped` while `.tar` still exists. Logs loop: `day_close ghost manifest reconcile` → `debt_drain_begin debt_remaining=N` → `Archive janitor tick done … debt_popped=N days_started=N days_completed=N debt_remaining=0` in **tens of seconds** (impossible for multi-10 GiB real tar-drop) → ghost reconcile requeues the same days. No seal/delete/tar-drop progress lines.

**Root cause class:** hint-only skip of `_tar_drop_one_day` + void finalize that discarded `finalize_complete_if_filesystem=False` while the mutable `.tar` remained.

```bash
# T0 — false completion + ghost thrash (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'ghost manifest reconcile|Archive janitor tick done|debt_drain_begin|tar drop|day_close' /tmp/pipeline-full.log | tail -120

# T0 — past-day open tar vs sealed sibling (INI paths; calendar-today may keep live .tar)
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as c
import glob, os, datetime as dt
d=c.get_daily_archive_dir_path(); today=dt.date.today().isoformat()
rows=[]
for tar in sorted(glob.glob(os.path.join(d,\"????-??-??.tar\"))):
  day=os.path.basename(tar)[:-4]
  rows.append((day, os.path.getsize(tar), os.path.isfile(tar+\".zst\"), day==today))
print(\"daily\", d)
for day, sz, zst, is_today in rows:
  print(day, \"bytes\", sz, \"zst\", zst, \"calendar_today\", is_today)
print(\"past_open_n\", sum(1 for r in rows if not r[3]))
"'
```

**Fail (T0):** repeating `days_completed=N` with matching `ghost manifest reconcile` for the same past days and **no** durable tar-drop / size decline; tick `duration_s` far too small for the open tar byte sizes; maint hints `tar_dropped` while `os.path.isfile` is still true for those past days.

**Pass (T0):** after deploy, either real tar-drop progress (unlink / reclaim logs, declining past-day sizes) **or** honest non-completion (`days_completed` does not claim success while past-day `.tar` remains). Ghost reconcile must not bounce the same filesystem-incomplete days every tick as false completions.

**Pass (T1):** past-day `open_tar_n` (excluding calendar-today) declines; ghost reconcile for those days stops once filesystem-complete; `days_completed` only advances with real reclaim.

**Pass (T2):** sealed past days reach no mutable `.tar`; async day-close rows are `complete` (not perpetual `queued` + `ghost_manifest_reconcile`); idle ingest no longer reports sticky day-close debt for those days.

### T0 / T1 — day-close `write_lock_contended` thrash + async between-chunk reconcile (2026-07)

**Failure signature (pre-fix, hpcperfstats03):** `Archive janitor tick done` with `debt_popped`/`days_started`>0 but **`days_completed=0`**, tick **`duration_s`≈500–1000**, `budget_exit leave_in_flight`, and repeated `janitor: day_close defer … reason=write_lock_contended` on the same June daily `.tar` while ingest holds flock. Separately, MainThread stuck in `_cap_pending_after_rescan` / live unprocessed rebuild between chunks under huge backlog (find itself is already async and cheap — **not** a `-newermt` problem).

```bash
# T0 — day-close thrash + tick summary (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'Archive janitor tick done|day_close defer.*write_lock_contended|deferred_preflight_n|deferred_reason_top|pending reconcile deferred|async pending reconcile|async pending rescan|kicked async pending rescan' | head -80
```

**Authoritative write_lock probe** (`try_file_write_lock` is a **context manager** — no `timeout_seconds=` kwarg, no `.release()`):

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.file_locking import try_file_write_lock
import os
daily = cfg.get_daily_archive_dir_path()
for day in (\"2026-06-08\", \"2026-06-09\"):
  tar = os.path.join(daily, day + \".tar\")
  try:
    with try_file_write_lock(tar):
      print(day, \"OK_uncontended\")
  except TimeoutError:
    print(day, \"write_lock_contended=True TimeoutError\")
"'
```

**Fail (T0):** repeating multi-minute ticks with `days_completed=0` + `write_lock_contended` and **`days_started`** counting every contended pop (preflight thrash); or MainThread blocked in sync `pending reconcile cap begin` / live rebuild every chunk while `pending` already ≥ chunk size.

**Pass (T0):** contended days log `day_close defer … phase=preflight reason=write_lock_contended` then tick summary shows `deferred_preflight_n≥1 deferred_reason_top=write_lock_contended` with **`days_started=0`** (or progress on other days) — not 500s+ zero-complete burns. Between chunks expect `pending reconcile deferred async_kick=yes` / `async pending reconcile begin|merge` and `async pending rescan` — **not** MainThread stuck in `_cap_pending_after_rescan` live rebuild under backlog. Day-close rescan after raw removal: `kicked async pending rescan` (not sync find on MainThread).

### T0 / T1 — `chunk_in_progress_day` defer (Branch H healthy, 2026-07-26)

```bash
# T0 — chunk_in_progress_day vs active chunk (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'Archive janitor tick done|deferred_reason_top=chunk_in_progress_day|chunk dispatch begin|chunk ingest summary|chunk_elapsed_s' | tail -80
```

**Pass (T0 / Branch H):** `deferred_reason_top=chunk_in_progress_day leave_in_flight=0 budget_exit=-` (or `budget_exit=leave_in_flight`) **interleaved** with `chunk dispatch begin` / `chunk ingest summary` / `chunk_elapsed_s`. Day-close correctly defers calendar days owned by the active imap chunk — **not** a stall.

**Fail (Branch S):** hour-scale only `chunk_in_progress_day` with **no** chunk progress lines and flat day-close debt / frozen `days_completed=0` while debt remains — sticky tokens (not observed on hpcperfstats01 2026-07-26).

**Do not** treat Branch H as the historical multi-hour `duration_s≈9217` after `budget_exit` hang.

**Pass (T1):** while ingest holds June tar locks, day-close advances other eligible days or backs off; when locks clear, seal completes (`days_completed>0`). Chunk ingest cadence continues without between-chunk reconcile waits.

### T0 / T1 — `wait_for_member_match` + `redis_warm` false non-defer → exit 124 (2026-07)

**Failure signature (pre-fix):** `ERROR: Pool imap stalled` with `stall_defer=off defer_reason=redis_warm`, `effective_ingest_timeout_s=-`, small in-flight `batch_max_ingest_timeout_s` ≈ floor, then `hard exit code=124`. py-spy on ingest-pool workers shows idle `wait_for_member_match` → `daily_archive_has_member_with_size` / tar-append decision while calendar-day Redis reports `complete=1`.

```bash
# T0 — stall / exit124 signatures (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'Pool imap stalled|defer_reason=redis_warm|defer_reason=member_match_wait|hard exit code=124|effective_ingest_timeout_s=' | tail -80
```

**Pass (T0):** no new `ERROR: Pool imap stalled` + `defer_reason=redis_warm` while workers are in member-match wait. Expect either imap completions, `stall deferred` with `defer_reason=member_match_wait` / `worker_progress_active` / `long_ingest_budget`, or `effective_ingest_timeout_s` numeric (not `-`). Stall wall should exceed in-flight `batch_max` by ~120s grace.

**Pass (T1):** under combined ingest with warm July Redis + concurrent June populate, ingest continues without exit 124 on the redis_warm small-window path; `chunk ingest summary` cadence resumes after large-file / member-wait windows.

### T0 / T1 — find-based pending discovery (every chunk + mtime window)

After deploy of **GNU find `-printf` stats discovery** (`sync_timedb_stats_find`, `rescan_every_chunks=1`, `sync_ingest_rescan_mtime_days=1`): multi-hour silent gaps between `pending rescan done` / `find_stats` lines on ``current``/idle must **not** return. Discovery must complete in seconds (operator baseline: full archive ~0.7s, `-mtime -1` ~2s on ~350k files).

```bash
# T0 — find cadence after deploy (full pipeline log; never --tail before grep)
podman-compose -p hpcperfstats logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'find_stats paths=|collect_stats_files_in_range: find paths=|Rescanned after 1 chunks|pending rescan done' /tmp/pipeline-full.log | tail -80

# T0 — fail-closed signature (should be absent on GNU find images)
grep -E 'FindStatsDiscoveryError|does not support -printf|GNU find not found' /tmp/pipeline-full.log | tail -20 || true
```

**Pass (T0):** `find_stats` / `collect_stats_files_in_range: find` lines appear with small `elapsed_s` (typically **&lt;5s**); after ingest chunks expect **`Rescan boundary after 1 chunks`** (or legacy **`Rescanned after 1 chunks`**); no multi-hour silence with only occasional `idle_finalize`.

### T0 / T1 verify — between-chunk exclude / handoff no full-snapshot stall (2026-07)

After deploy of **chunk-stall / startup fast-start** (MainThread must not build `build_archive_maintenance_snapshot` during `_rescan_processed_exclusions` / handoff):

**Failure signature (pre-fix):** after `chunk_archive_elapsed` + `Archive finalize deferred context=rescan_every_chunks`, MainThread sits in `_rescan_processed_exclusions` → `rescan_exclude_paths` → `handoff_paths_for_ingest` → `build_archive_maintenance_snapshot` / `collect_head_metadata_for_paths` for ~hour; no next `chunk imap start` / `chunk prewarm begin`. py-spy DUMP1==DUMP2 on that stack while ingest workers idle.

**Pass (T0):** next `chunk imap start` (or `chunk prewarm begin`) within a normal between-chunk window after `Archive finalize deferred` — **not** an hour-scale gap. Expect `async pending rescan begin|complete|merge` (optional body `ingest:` prefix) and/or `Rescan boundary after … async_inflight=`; do **not** see MainThread stuck in `build_archive_maintenance_snapshot` on the exclude path.

```bash
# T0 — between-chunk cadence (full pipeline log; never --tail before grep)
podman-compose -p hpcperfstats logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'Archive finalize deferred context=rescan_every_chunks|Rescan boundary after|async pending rescan|chunk imap start|chunk prewarm begin' /tmp/pipeline-full.log | tail -100
```

**Pass (T1):** under backlog with day-close handoff active, `waiting_on_ingest` paths stay excluded from pending until requeued; no premature tar_drop of days with closed raw still on disk; ingest continues while async find runs (`pending empty while async rescan in flight` may appear briefly without idle archival sleep).

**Pass (T1):** under backlog or ``current``, pending rediscovery continues every chunk without reintroducing Python `scandir`+`stat` wall-clock stalls; incremental lines show `mtime_days=1` (or configured N) except full sweeps / startup / maint (`mtime_days=None`).

### Archive/delete DB gate (host head+tail OR zero-host ingest mark)

When **`sync_archive_require_db_ingest=yes`**, tar append and raw delete require **either**:

1. **Both** the first and last digit-leading timestamp seconds in `host_data` (streaming head + EOF-backward tail; no full-file scan), **or**
2. A durable **zero-host ingest mark** after successful `outcome=ingested` with `stats_rows=0` (typically `proc_rows>0` — proc-only / empty host payload). Marks live at `{archive_dir}/.sync_timedb_zero_host_ingest_mark.json`. Archival marking (`archive=yes` / `Files marked for archival`) remains correct for these paths; they must **proceed to append + delete**, not stick in `Archive/delete gate: skipped N`.

After deploy:

- Expect **`not_head_tail_ingested`** / **`skipped_not_head_tail_ingested`** and day-close **handoff** until incomplete **host** ingest catches up.
- **Do not** treat `stats_rows=0` + `outcome=ingested` + `archive=yes` followed by persistent `Archive/delete gate: skipped` as normal — that is a regression of the zero-host mark / readiness OR path.
- Legacy manifest reasons **`not_head_ingested`** / **`not_sample_ingested`** remain retryable.
- **T0:** gate skips alone are not a stall if `chunk ingest summary` continues; confirm proc-only successes are **not** stuck behind the skip line after `Files marked for archival`.
- **T1/T2:** `not_head_tail_ingested` counts should fall as head-day `unprocessed` declines; frozen `waiting_on_ingest` with only gate skips and no ingest progress is a real stall.

**Pre-deploy stuck siblings (one-time):** paths that already logged `outcome=ingested stats_rows=0 … archive=yes` then `Archive/delete gate: skipped` **before** this fix do not have a mark yet. After redeploy, **re-queue those paths for ingest once** (or run a pipeline `python3 -c` that calls `record_zero_host_ingest_mark` with `ensure_django()` + `conf_parser.get_archive_dir_path()`) so the mark is written; subsequent archive/delete passes then proceed. New zero-host successes mint the mark automatically during ingest logging.

## Operator greps (pipeline service)

**Never** use `compose logs --tail=N` before grep on pipeline — tail can miss stall lines hours earlier.

### Full log → filter (recommended)

```bash
# Replace compose driver (docker compose / podman-compose) and service name as on site.
COMPOSE='podman-compose'   # or: docker compose
SVC='pipeline'
$COMPOSE logs "$SVC" 2>&1 | tee /tmp/pipeline-full.log

grep -E 'chunk ingest summary|oldest_day_chunk_gate_stall|ingest_stall_watchdog|oldest_day_unprocessed_frozen|archive_finalize defer|day_close handoff requeue|discover_ready_day_close|archive_job_done' /tmp/pipeline-full.log | tail -50

grep -c 'chunk ingest summary' /tmp/pipeline-full.log
grep -c 'oldest_day_chunk_gate_stall' /tmp/pipeline-full.log
grep -c 'ingest_stall_watchdog' /tmp/pipeline-full.log
```

### T0 / T1 — janitor discover fake-enqueue / sticky deferred / day-close stall (2026-07)

**Failure signature (pre-fix):** `discover_ready_day_close enqueued=N … deferred_waiting=N debt_heap=0 active_workers=0` then `days_started=0` for many hours while many mutable `.tar` remain (`open_tar_n` high). Often paired with `checkpoint_incomplete` skips, `populate incomplete … none:none`, and `delete deferred … delete_disqualified` thrash on handoff days.

```bash
# T0 — fake-enqueue / honest counters (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'discover_ready_day_close|deferred_noop|deferred cleared|immediate day_close defer|delete deferred|zero_pop|populate incomplete after lock release|mutation proceed reason=defer_cap_exceeded' /tmp/pipeline-full.log | tail -80

# T0 — open mutable daily tar census (INI paths first; single python3 -c — no nested sh -lc)
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "from hpcperfstats.dbload.lib import conf_parser as c; import os,glob; d=c.get_daily_archive_dir_path(); tars=sorted(glob.glob(os.path.join(d,\"????-??-??.tar\"))); print(\"daily\",d,\"open_tar_n\",len(tars)); print(\"sample\",tars[:8])"'
```

**Fail (T0):** repeating `enqueued=N` with `debt_heap=0` / `active_workers=0` / `days_started=0` while `deferred_waiting` stays flat and open `.tar` count does not fall; or `enqueued=` equals deferred count without `deferred_noop=` (old fake-ok contract).

**Pass (T0):** discover lines show real `enqueued=` only when debt/workers advance; `deferred_noop=` / `already_inflight=` split soft-state; `deferred cleared` after handoff drain **or** orphan-deferred clear when RAM `handoff_priority_paths` pins are empty; immediate defer is per source day (other days can still enqueue); **`delete_disqualified` stays fail-closed** — `defer_cap_exceeded` may force a cold-path mutation only when hot/populate/write-lock are clear, and **never** authorizes delete through handoff/pending while the day remains delete-disqualified; empty `none:none` incomplete recover is bounded (stall / fail-closed, not infinite stall-clock reset).

**Pass (T1):** over hours, `open_tar_n` declines and `Archive janitor tick done … days_started≥1` / `days_completed` progress under continuous ingest; no multi-day spin of fake discover with zero debt.

### T0 / T1 — verifying×exclude×handoff deadlock / June closed_raw thrash (2026-07)

**Failure signature (pre-fix):** stable `day-scoped closed_raw … paths=N` for a verifying day (e.g. 06-05 with all `skipped_not_in_archive`); no `day_close handoff requeue day=<that day>`; `chunk_day_histogram` omits the stuck day; async stays `deferred` / `waiting_on_ingest` with discover `deferred_noop≈ready_for_enqueue_n`; under CLI **current**, `handoff_cross_day_n == handoff_priority_n` and youngest-gate pads July while older deferred same-day pins starve.

```bash
# T0 — stuck verifying day vs handoff / histogram / orphan deferred
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'day-scoped closed_raw|day_close handoff requeue|verifying stuck handoff|post-ingest_going closed-raw|orphan deferred|chunk_day_histogram|youngest_day_chunk_gate|handoff_priority_age|deferred_noop|delete deferred.*delete_disqualified|defer_cap_exceeded' /tmp/pipeline-full.log | tail -120

# T0 — day-raw-removal phase + async day_close for a stuck ISO day
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as c
import json, os, glob
d=c.get_archive_dir_path()
raw=os.path.join(d,\".sync_timedb_day_raw_removal\")
asyncp=os.path.join(d,\".sync_timedb_async_day_close.json\")
day=\"2026-06-05\"
mp=os.path.join(raw, day+\".json\")
print(\"raw_manifest\", mp, \"exists\", os.path.isfile(mp))
if os.path.isfile(mp):
  m=json.load(open(mp)); print(\"phase\", m.get(\"phase\"), \"verify_stage\", m.get(\"verify_stage\"), \"entries\", len(m.get(\"entries\") or {}))
  from collections import Counter
  print(Counter((e or {}).get(\"status\") for e in (m.get(\"entries\") or {}).values() if isinstance(e, dict)))
if os.path.isfile(asyncp):
  a=json.load(open(asyncp)); e=(a.get(\"entries\") or {}).get(day) or (a.get(\"entries\") or {})
  print(\"async_sample\", list(a.get(\"entries\", {}).items())[:3] if isinstance(a.get(\"entries\"), dict) else a)
"'
```

**Fail (T0):** verifying day with retryables never appears in `day_close handoff requeue` / `chunk_day_histogram` for many hours; `deferred_noop` sticky while `handoff_priority_n=0` for that source day (orphan deferred); empty first boot requeue then permanent `same_boot_duplicate` for later pathful attempts.

**Pass (T0):** after deploy, verifying/retryable days get `verifying stuck handoff` and/or `post-ingest_going closed-raw handoff discover` / `day_close handoff requeue`; orphan lines `orphan deferred cleared|rehandoff` when pins empty; same-day deferred pins appear in youngest-gate lead/histogram; **no** delete of delete-disqualified days solely because `defer_cap_exceeded`.

**Pass (T1):** stuck June day census falls, async leaves `waiting_on_ingest`, tar-drop/`complete` progresses without multi-day closed_raw thrash on the same path count.

### T0 / T1 — Branch C deleting×retryable sticky open tar (hpcperfstats03, 2026-07)

**Failure signature (pre-fix):** many open mutable `daily_archive_dir/YYYY-MM-DD.tar` (`open_tar_n` high for days); sticky days sit in **`phase=deleting`** with **`verified_not_deleted=0`** but large **`retryable_skips`** that never clear; repeating `day_close handoff requeue … reason=batch_delete_waiting_on_ingest` with stable `paths=N` → `delete deferred … reason=delete_disqualified` → (healthy) Branch H `chunk_in_progress_day` while ingest owns those pins → cycle. Open `.tar` never reaches tar-drop.

**DbReadyNotInArchive (locked 2026-07-31, Branch C insufficient):** Branch C reclassify under `PHASE_DELETING` is deployed, but sample sticky pins are **already DB head-ready** and **not** members of the open daily tar (`status=skipped_not_in_archive` / `reason=not_in_sealed_archive`). Ingest-only handoff clears checkpoint and requeues; paths often finish as **`checkpoint_immediate`** without landing in `files_to_be_archived`, so membership never appears and reclassify upgrades stay 0. **Fix:** day-close handoff partitions DB-ready + `raw_stats_path_needs_tar_append` pins onto **`handoff_mode=archive_append`** (direct archive heap enqueue) while true not-DB-ready pins stay on ingest handoff. Preserve `delete_disqualified` and Branch H.

**Concurrent worseners (same path — amplifiers, not primary RC):** post-ingest re-handoff thrash without reclassify; broad fail-closed `delete_disqualified` while pins live (**preserve**); sticky `same_boot` handoff subset while hundreds of retryables remain; handoff/chunk path cap vs large skip sets; true DB-gate not ready for a subset; Branch H overlay latency (**preserve** — not RC).

```bash
# T0 — open tar census + sticky handoff / delete_disqualified / Branch H overlay
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as c
import glob, os
d=c.get_daily_archive_dir_path()
open_n=len(glob.glob(os.path.join(d,\"????-??-??.tar\")))
zst_n=len(glob.glob(os.path.join(d,\"????-??-??.tar.zst\")))
print(\"open_tar_n\", open_n, \"sealed_zst_n\", zst_n, \"daily_archive_dir\", d)
"'
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'batch_delete_waiting_on_ingest|handoff_mode=archive_append|delete deferred.*delete_disqualified|chunk_in_progress_day|day_close reclassify upgraded|Day raw removal reclassify' /tmp/pipeline-full.log | tail -80

# T0 — day_raw_removal census for a sticky ISO day (replace DAY=)
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as c
import json, os
DAY=\"2026-06-07\"
raw=os.path.join(c.get_archive_dir_path(),\".sync_timedb_day_raw_removal\", DAY+\".json\")
m=json.load(open(raw))
ents=m.get(\"entries\") or {}
verified_not_deleted=sum(1 for e in ents.values() if isinstance(e,dict) and e.get(\"status\")==\"verified\" and not e.get(\"deleted\"))
retryable=sum(1 for e in ents.values() if isinstance(e,dict) and str(e.get(\"status\",\"\")).startswith(\"skipped\") and e.get(\"status\")!=\"skipped_quarantine\")
deleted=sum(1 for e in ents.values() if isinstance(e,dict) and e.get(\"deleted\"))
print(\"phase\", m.get(\"phase\"), \"entries\", len(ents), \"verified_not_deleted\", verified_not_deleted, \"retryable_skips\", retryable, \"deleted\", deleted)
"'
```

**Fail (T0):** hours/days of `phase=deleting` + `verified_not_deleted=0` + flat/high `retryable_skips` with the same handoff `paths=N` and no `day_close reclassify upgraded` / skip drain; `open_tar_n` does not decline for those days. Also fail when sticky pins are DB-ready + `not_in_sealed_archive` but logs show only ingest-only handoff (`queue_head=yes`) with **no** `handoff_mode=archive_append` / June `Archived batch` / `pending_archive_heap` for that day.

**Pass (T0):** after deploy, sticky days log `janitor: day_close reclassify upgraded=` and/or `Day raw removal reclassify`; **and/or** DB-ready not-in-archive pins log **`handoff_mode=archive_append`** then archive duty for that calendar day; `retryable_skips` decline when membership+DB gate pass; handoff `paths=N` shrinks or clears; **`delete_disqualified` remains** while pins live; Branch H may still defer `chunk_in_progress_day` (healthy overlay).

**Pass (T1):** `open_tar_n` declines for lead/sticky days; sticky days leave `phase=deleting` (toward done/tar-drop/complete); no multi-day Branch C / DbReadyNotInArchive loop of identical handoff path counts with undrained retryables. **Do not** treat green `handoff_lead_uncapped` alone as day-close health while multi-hundred-GB mutable June `.tar` remain.

### T0 / T1 / T2 — skip-only deleting + empty done+live tar + two-queue handoff (hpcperfstats03, 2026-08-19)

Do **not** mark this class verified on **T0 smoke alone**. Use T0 then T1 then T2.

**Failure signature (pre-fix):** CLI ``current``; sealed June days (`*.tar.zst` present) stuck **`phase=deleting`**, histogram **100% `skipped_not_in_archive`**, **`deleted_count=0`**, **zero quarantine**. Separate day **`phase=done` + `entries=0` + live `.tar`**. py-spy **`day-close_N`**: `apply_batch_delete` → `complete_handoff_to_ingest` → `_finalize_ingest_archive_batch` → `oldest_checkpoint_incomplete_tar` (`isfile`). Janitor tick may sit in discover `isfile` so compose **`Archive janitor tick done` count 0** is not proof the thread is dead.

**Do not:** quarantine-only tar-drop for retryable skips; py-spy `pgrep … .[m]ain. | head -1` (Manager leftover); treat `supervisorctl` missing socket as ingest-down.

```bash
# T0 — skip-status histogram (Path.read_text fingerprint; no Django import of day_raw_removal)
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as c
from collections import Counter
import json, os
raw=os.path.join(c.get_archive_dir_path(), \".sync_timedb_day_raw_removal\")
for day in (\"2026-06-02\", \"2026-06-10\", \"2026-08-16\"):
  mp=os.path.join(raw, day+\".json\")
  print(\"day\", day, \"exists\", os.path.isfile(mp))
  if not os.path.isfile(mp):
    continue
  m=json.load(open(mp))
  ents=m.get(\"entries\") or {}
  print(\"phase\", m.get(\"phase\"), \"entries\", len(ents), \"deleted_count\", m.get(\"deleted_count\"))
  print(Counter((e or {}).get(\"status\") for e in ents.values() if isinstance(e, dict)))
"'
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'handoff_mode=archive_append|batch_delete_waiting_on_ingest|day_close reclassify|Archive janitor tick done|oldest_checkpoint_incomplete_tar' | tail -80
```

**Pass (T0):** skip-only days log reclassify **and/or** `handoff_mode=archive_append` / `day_close handoff requeue` without `day-close_*` stacks in `_finalize_ingest_archive_batch` / `oldest_checkpoint_incomplete_tar`. Empty `phase=done` days with a live `.tar` log tar-drop attempt (`janitor: day_close` tar drop / unlink), not silent complete.

**Pass (T1):** `retryable_skips` / skip-only `phase=deleting` counts decline; `open_tar_n` for those ISO days trends down; `day-close_*` inflight slots free (not 4/4 GIL in oldest-tar `isfile`). Dual ingest-pool `[main]` trees remain an **ops** finding, not a code substitute.

**Pass (T2):** sealed skip-only June days reach tar-drop / no mutable `.tar`; empty-done live tar is gone or `tar_dropped` matches disk; no multi-day skip-only freeze with `deleted_count=0`.

### T0 / T1 / T2 — skip-only archive_append thrash (`to_add=0` + skip invalidate, 2026-08-21)

**Signature (hpcperfstats03 soak after finding #8):** `handoff_mode=archive_append` works, but skip-only `phase=deleting` cycles forever: `archive_job_duty … to_add=0 appended=0` → `archive_finalize skip invalidate reason=no_tar_mutation_or_worker_invalidated` → deferred/pins clear → delete again with **flat** `retryable_skips` / handoff `paths=N`. Open-tar `member_hit False`. Massive `day-scoped closed_raw` spam. `Archive soft_requeue=0`, `same_boot_duplicate=0`. Not heap starvation.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# T0 — thrash census (filtered; no --tail before grep)
grep -cE 'handoff_mode=archive_append' /tmp/pipeline-full.log || true
grep -cE 'archive_job_duty.*to_add=0' /tmp/pipeline-full.log || true
grep -cE 'archive_finalize skip invalidate' /tmp/pipeline-full.log || true
grep -cE 'archive_finalize handoff_pin_hold' /tmp/pipeline-full.log || true
grep -cE 'day_close handoff requeue skip.*detail=(archive_append_inflight|active_append_or_inflight_paths|pending_archive_heap)' /tmp/pipeline-full.log || true
grep -cE 'day-scoped closed_raw' /tmp/pipeline-full.log || true
grep -cE 'Archive soft_requeue|same_boot_duplicate' /tmp/pipeline-full.log || true

# T0 — samples
grep -E 'archive_job_duty|archive_finalize skip invalidate|handoff_pin_hold|handoff_mode=archive_append|day_close handoff requeue skip' /tmp/pipeline-full.log | tail -80
```

**Fail (T0):** repeated `to_add=0` + `skip invalidate` with **no** `handoff_pin_hold`, pins/`waiting_on_ingest` cleared, flat handoff `paths=N`, and hundreds of `day-scoped closed_raw` per tick while `days_completed=0`.

**Pass (T0):** skip-invalidate on day-close pins logs **`handoff_pin_hold`** (pins retained) and/or re-handoff skip **`detail=archive_append_inflight|…`**; `day-scoped closed_raw` is not multi-dozen per single delete pass.

**Pass (T1):** `retryable_skips` / handoff `paths=N` for sticky June days decline or stay deferred without thrash re-enqueue storms; `open_tar_n` trends down when membership lands; tick `duration_s` no longer multi-hour from census spam alone.

**Pass (T2):** skip-only days reach Branch C upgrade → delete → tar-drop; no perpetual `to_add=0` / skip-invalidate pin-clear loop.

### T0 / T1 / T2 — open-tar vs Redis membership divergence (hpcperfstats03, 2026-08-23)

**Signature (pre-fix):** mutable daily `.tar` remains (`open_tar_n` high); `archive_job_begin … members_source=redis` (or `tar_scan`) with **`to_add=0 appended=0`** while `handoff_mode=archive_append` requeues keep **flat** `paths=N`; Branch C `member_hit False` on open tar; optional **`skip_invalidate`** without **`handoff_pin_hold`** when skip-only pin-hold fix is not deployed. Root cause: warm Redis/sealed claimed membership the **open mutable tar** lacked.

**Deploy:** ship **open-tar authority** + **skip-only pin-hold** in **one** pipeline image refresh (`rebuild_pipeline.sh` or site equivalent).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec -T pipeline \
  su hpcperfstats -c 'python3 -c "
import glob, os
from hpcperfstats.dbload.lib import conf_parser as c
d = c.get_daily_archive_dir_path()
print(\"open_tar_n\", len(glob.glob(os.path.join(d, \"????-??-??.tar\"))))
"'

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | awk '\''
/archive_finalize handoff_pin_hold/ { hp++ }
/archive_job_duty.*to_add=0/ { tz++ }
/archive_job_begin.*members_source=redis/ { mr++ }
/archive_append open_tar_redis_divergence/ { div++ }
/archive_finalize skip invalidate/ { si++ }
/archive_job_duty.*appended=[1-9]/ { ap++ }
END { printf "handoff_pin_hold %d\nto_add_zero %d\nmembers_source_redis %d\nopen_tar_redis_divergence %d\nskip_invalidate %d\nappended_positive %d\n", hp+0, tz+0, mr+0, div+0, si+0, ap+0 }'\''

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'archive_job_duty|archive_job_begin|handoff_pin_hold|open_tar_redis_divergence|handoff_mode=archive_append|day_close reclassify' | tail -25
```

**Fail (T0):** `members_source=redis` + `to_add=0` on huge `tar_bytes` + flat handoff `paths=` + **`handoff_pin_hold=0`** + **`open_tar_redis_divergence=0`** while `open_tar_n` unchanged for 24h.

**Pass (T0):** sticky June days log **`members_source=tar_scan`** and/or **`open_tar_redis_divergence`**; **`to_add>0`** or **`appended_positive>0`** for at least one sticky day; **`handoff_pin_hold>0`** when skip-invalidate thrash persists without open-tar member proof.

**Pass (T1):** `day_close reclassify upgraded>` for sticky days; handoff `paths=N` declines; `open_tar_n` trends down for lead/sticky ISO days.

**Pass (T2):** past-day mutable `.tar` dropped for completed days; no multi-day loop of identical handoff counts with `to_add=0` / flat `retryable_skips`.

### T0 / T1 / T2 — daily tar append stall / new tars (hpcperfstats03, 2026-08-23)

**Signature (pre-fix):** all open daily `.tar` mtimes stale >3d (`tars_mtime_older_than_3d` equals `tar_count`); **`Archived batch=0`**; **`archive_job_duty` with `to_add=0`** on every job (e.g. 47/47); newest calendar days (e.g. **2026-08-20+**) have **no** `.tar`; June days occupy oldest-first archive slots with **`mapped>0`** but **`appended=0`**. Often coexists with open-tar/redis divergence on older mutable tars and skip-invalidate thrash when pin-hold is not deployed.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec -T pipeline \
  su hpcperfstats -c 'python3 -c "
import glob, os, time
from hpcperfstats.dbload.lib import conf_parser as c
d = c.get_daily_archive_dir_path()
tars = sorted(glob.glob(os.path.join(d, \"????-??-??.tar\")))
now = time.time()
old3d = sum(1 for p in tars if now - os.path.getmtime(p) > 3 * 86400)
print(\"daily_archive_dir\", d)
print(\"tar_count\", len(tars))
print(\"tars_mtime_older_than_3d\", old3d)
for day in (\"2026-08-20\", \"2026-08-21\", \"2026-08-22\", \"2026-08-23\"):
    print(day, \"tar_exists\", os.path.isfile(os.path.join(d, day + \".tar\")))
"'

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | awk '\''
/archive_job_duty.*to_add=0/ { tz++ }
/archive_job_duty.*appended=[1-9]/ { ap++ }
/Archived batch/ { ab++ }
/archive_finalize skip invalidate/ { si++ }
END { printf "to_add_zero %d\nappended_positive %d\narchived_batch %d\nskip_invalidate %d\n", tz+0, ap+0, ab+0, si+0 }'\''

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'archive_job_duty|Archived batch|archive_job_begin|handoff_pin_hold|open_tar_redis_divergence' | tail -30
```

**Fail (T0):** `tars_mtime_older_than_3d == tar_count` with **`appended_positive=0`** and **`Archived batch=0`** for 24h+; missing newest-calendar-day `.tar` files while June-only `archive_job_duty` lines show **`to_add=0`**.

**Pass (T0):** at least one sticky day logs **`to_add>0`** / **`appended>0`** or **`Archived batch`**; newest calendar days gain `.tar` files or advancing mtimes; **`members_source=tar_scan`** on mutable-tar jobs (see open-tar/redis divergence section).

**Pass (T1):** `open_tar_n` declines for lead days; handoff `paths=N` trends down; August calendar days appear in `archive_job_duty` samples.

**Pass (T2):** daily `.tar` mtimes advance on active days; no perpetual all-`to_add=0` census while closed raw remains on disk.

### T0 / T1 — tar append exit 2 / large member (`out of off_t range`, 2026-07)

Members larger than **8 GiB − 1** fail classic ustar without pax headers (`value N out of off_t range 0..8589934591`). Production always passes **`--posix`** on tar create/append (`-C /` + relative `-T` members). When the daily tar is **not pax-capable** (bare `POSIX tar archive` without pax headers; GNU labels need no convert), the **archive pool** job logs **`must_convert`**, attempts **extract + `tar --format=pax` recreate**, then appends. On convert failure: **`convert_fail_skip`** oversized members (original tar untouched) and continue with remaining paths. **`archive_job_done`** includes **`outcome=ok|fail`** (do not treat `archive_job_done` alone as success).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# T0 — failure signatures
grep -E 'ERROR: (retry )?tar append failed|tar append stderr:|out of off_t range|marker=off_t_range' /tmp/pipeline-full.log | tail -40

# T0 — convert path / skip fallback
grep -E 'must_convert|convert_start|convert_done|convert_fail_skip|archive_job_done.*outcome=' /tmp/pipeline-full.log | tail -40

# T0 — confirm archive-pool progress continues
grep -E 'Archived batch|archive_job_done|chunk ingest summary' /tmp/pipeline-full.log | tail -30
```

**Pass (T0):** no new unexplained **`out of off_t range`** on giant-member append after convert; ERROR includes **`tar append stderr:`** and often **`marker=off_t_range`**. Convert is archive-worker only (never supervisor/ingest). High **`incomplete_n`** + **`handoff_priority`** can be **slow gated progress**, not a hard stall — confirm with subsequent `chunk ingest summary` / `archive_job_done outcome=ok`.

**False stall note:** a snapshot with large `incomplete_n` on the oldest day is not proof of a freeze if the pipeline later continues.

### T0 — queue watermarks / adaptive backlog removed (2026-07)

Soft ingest/archive **queue watermarks** and adaptive archive dispatch/janitor backlog backoff were removed. Archival concurrency is **pool/slot caps only**:

- Append: **`sync_archive_pool_processes`** (one daily tar per slot; concurrent slots = pool size). Legacy **`sync_archive_max_inflight_jobs`** is ignored (getter aliases pool size).
- Day-close: **`sync_day_close_max_inflight`** (one calendar day per worker)
- Overflow: when mapping days exceed pool size, remaining days sit on **`pending_archive_heap`** and **must** get `archive_job_begin` when a slot frees — **without** waiting for the next ingest chunk IMAP. Operator grep: `pending_archive_heap`, `archive_job_duty`, `Archive dispatch submitted=`.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# Must be absent after deploy
grep -E 'Queue watermarks|high watermark|low watermark|adaptive_dispatch' /tmp/pipeline-full.log | tail -20 || true

# Confirm archival still progresses under caps
grep -E 'Archive dispatch submitted=|pending_archive_heap|archive_job_duty|discover_ready_day_close|Archive janitor tick done' /tmp/pipeline-full.log | tail -40
```

**Pass (T0):** no `Queue watermarks` / `above high watermark` / `below low watermark` lines; archive dispatch and day-close continue within configured pool/inflight.

**T0/T1 — multi-day mapping with pool ≪ N (site example pool=6):** after `Archive mapping: N tar(s)` with N larger than pool (or with overflow from an earlier narrow wave), expect later `archive_job_begin` / `Archive dispatch submitted=` for overflow calendar days **before** the next `chunk imap start` / IMAP for a new ingest chunk. Tiny `Archived batch (2|5|…)` plus `archive_job_duty … to_add=… appended=…` means Redis/tar already-present skips — not a missing day. If `post_finalize_reconcile oldest_tar=` advances past days that never logged `archive_job_*`, that is a drain regression.

### T0/T1 — restore self-wait + dispatch skip blocked days (2026-07)

**Unhealthy (pre-fix):** day-close or append restore sets `daily_tar_restore`, then mid-restore `tar_restore_pre` invalidate triggers sync re-prewarm while `chunk_in_progress` → flood of `populate: wait daily_tar_restore` with **no** `daily_tar_restore end` in the same window (hook waits on the key the restore thread still holds). Archive slots may also park on the blocked calendar day while other heap days sit idle.

```bash
# T0 — restore self-wait (unhealthy) vs clear+deferred prewarm (healthy)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'daily_tar_restore begin|daily_tar_restore end|populate: wait daily_tar_restore|deferred prewarm flush|Archive dispatch skip|archive_job_begin|archive_job soft_skip' | tail -120
```

**Pass (T0):** `daily_tar_restore begin` is followed by `daily_tar_restore end` without a multi-hour gap of only `populate: wait daily_tar_restore`. After end, expect `deferred prewarm flush … reason=tar_restore_end` (or chunk-boundary `deferred_invalidation`) rather than sync wait during restore. When another day is restore-blocked, expect `Archive dispatch skip day=… reason=daily_tar_restore` and a later `archive_job_begin` for a **different** calendar day while the blocked day backs off.

**Pass (T1):** under scarce `sync_archive_pool_processes`, heap drain continues for unblocked days; restore-blocked days reappear after backoff (`Archive soft_requeue` / due drain) once restore clears — not permanent slot starvation on the blocked head day.

### T0/T1 — noop sealed archive job (membership before restore)

**Unhealthy (pre-fix):** sealed day missing sibling `.tar` → `archive decompress restore` / `_decompress_compressed_archive` for multi-hundred-GB days, then `archive_job_begin … members_source=tar_scan`, then `archive_job_duty … to_add=0 appended=0` after 1h+ (`Archive worker stall detected` warn-only). Slots stay full while oldest incomplete drains slowly.

**Healthy (post-fix):** membership from Redis/sealed **before** decompress. When candidates are already members:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'archive_job_begin|archive_job_duty|archive decompress restore|Archive worker stall detected|Zombie child reap|archive_finalize_prune' | tail -80
```

Expect `archive_job_begin … members_source=redis|sealed_stream` and `archive_job_duty … to_add=0` **without** a preceding multi-hour `archive decompress restore` for that day. When `to_add>0`, restore then append remains required (fail-closed). CLI `backlog` must **not** emit `immediate day_close defer` / `archive_finalize defer immediate day_close` (day_close disabled). Archive finalize/prune should eventually log `Zombie child reap` / throttled reap under load (`maxtasksperchild=1` recycle).

### Pipeline ingest rate (listend vs sync_timedb)

Measure closed-segment production rate vs full-ingest and archive-done consumption from **full** pipeline logs (requires `--timestamps` for accurate windows and backlog ETA):

```bash
# Live compose logs (recommended: full dump, not --tail)
docker compose -p hpcperfstats -f docker-compose.yaml logs --timestamps pipeline 2>&1 \
  | python3 scripts/measure_pipeline_ingest_rate.py

# Saved log file; optional last-N-minutes window
docker compose -p hpcperfstats -f docker-compose.yaml logs --timestamps pipeline 2>&1 > /tmp/pipeline-full.log
python3 scripts/measure_pipeline_ingest_rate.py --log-file /tmp/pipeline-full.log --since-minutes 240

# Default window starts at last ``startup ingest gate cleared`` (excludes supervisor startup).
# Opt in to pre-gate lines only when comparing full container uptime:
python3 scripts/measure_pipeline_ingest_rate.py --log-file /tmp/pipeline-full.log --include-startup
```

Stdout prints only outcome keys (`listend_closed_per_min`, `sync_full_ingest_per_min`, `verdict_full_ingest`, `eta_hours_*`, etc.). **WINNING** means sync is catching up or even; **LOSING** means listend outruns sync. Warnings go to stderr.

### Stats file disappeared vs day-close delete (delete race)

When MainThread logs **`fail_reason=Stats file disappeared`** while day-close workers log **`removing stats file (day raw removal preflight)`** for the same path, that is the **day-close delete race** signature (manifest-verified path deleted while ingest still dispatches it). After fix deploy, expect **`janitor: day_close delete defer path=… reason=active_ingest`** instead of delete lines for active-ingest paths.

```bash
PATH_SUFFIX='1780924752'   # filename epoch or path fragment
$COMPOSE logs "$SVC" 2>&1 | tee /tmp/pipeline-full.log

grep -E "${PATH_SUFFIX}|removing stats file|Stats file disappeared|day_close delete defer|chunk dispatch begin" /tmp/pipeline-full.log | tail -80

grep -c 'Stats file disappeared' /tmp/pipeline-full.log
grep -c 'day_close delete defer' /tmp/pipeline-full.log
```

**Pass (post-fix):** `Stats file disappeared` rate drops; correlated paths show **delete defer** or no delete line while chunk dispatch is active. **Fail:** same path shows delete preflight then immediate `Stats file disappeared` at `elapsed_s=0.0`.

### Checkpoint / janitor census (read-only exec)

```bash
$COMPOSE exec -T pipeline /home/hpcperfstats/.venv/bin/python3 -c "
import hpcperfstats.dbload.lib.conf_parser as cfg
print('archive_dir', cfg.get_archive_dir_path())
print('daily_tar_dir', cfg.get_tgz_archive_dir())
"
```

### Runtime watchdog

- **`ERROR: ingest_stall_watchdog`** — MainThread idle ≥30 min with `incomplete_n>0` and no giant imap budget in flight.
- **`WARN: oldest_day_unprocessed_frozen`** — oldest `waiting_on_ingest` `unprocessed` unchanged across consecutive janitor candidate reports.

Either line during catch-up is a **merge blocker** until RC is classified (see june-ingest-stall-prevention plan Phase 0 greps).

### py-spy (MainThread idle during handoff — RC-F)

Use a **single** `sh -lc` inside the container so PID resolution and `py-spy` share the same namespace. **Do not** pass a host `$MAIN_PID` or split across two `exec` calls — **podman-compose eats `--pid`** (`ParseIntError` / `InvalidDigit`).

```bash
cd hpcperfstats   # or HPCPerfStats checkout on site
podman-compose exec -T pipeline su hpcperfstats -c "sh -lc '
SUP=\$(pgrep -f \"[s]ync_timedb\" | head -1)
echo main_pid=\$SUP
py-spy dump --pid \"\$SUP\" 2>&1 | tail -80
'"
```

**RC-F signature:** all pools idle; MainThread in `defer_for_ingest_handoff` ← `_requeue_day_close_handoff_paths` ← `complete_handoff_to_ingest` (pre-seal retryable) ← `_maybe_enqueue_immediate_day_close`.

**T1 pass grep** (after deploy):

```bash
podman-compose logs pipeline 2>&1 | grep -E 'chunk ingest summary|immediate day_close defer|ingest_stall_watchdog|oldest_day_unprocessed_frozen' | tail -40
```

Expect `chunk ingest summary` to resume after `immediate day_close defer` for **`handoff_priority`** / **`handoff_recovery`** only (no **`closed_raw_guard`**); no `ingest_stall_watchdog` within 30 min of handoff enqueue.

### T1 verify — verify-before-seal day close (2026-07+)

After deploy, backlog sites with **closed raw on disk** but **checkpoint-complete** days should enter janitor **`DAY_CLOSE`** without waiting on ingest queue drain:

```bash
podman-compose logs pipeline 2>&1 | grep -E \
  'janitor: day_close pre_seal_verify|janitor: day_close seal|janitor: day_close post_seal_verify|janitor: day_close delete|day_close handoff requeue' | tail -60
```

**T0 (first janitor pass on a backlog day):** grep shows **`pre_seal_verify`** before **`seal`** for the same `tar=` path; retryable paths log **`day_close handoff requeue`** and **no seal** for that pass. Stall diagnostics use **`day_close=`** (not `async_day_close=`). Up to **`sync_day_close_max_inflight`** (default **4**) days may run **`DAY_CLOSE`** in parallel on `day-close-N` worker threads; tick logs use **`days_started=`** / **`days_completed=`**.

**T1 (steady progress):** sealed days show **`post_seal_verify`** then **`delete`**; candidate report no longer lists **`closed_raw_on_disk`** as submit block; **`waiting_on_ingest`** remains only for **`checkpoint_incomplete`** days.

### T0 / T1 / T2 verify — day-close delete defer (`active_ingest`, RC-S, 2026-07)

Up to **`sync_day_close_max_inflight`** (default **4**) janitor workers run as **`day-close-1` … `day-close-4`**. Different **`delete start tar=`** lines on the same prefix are **different workers or passes**, not one thread deleting two tars atomically. Interleaved prefixes like **`[day-close-1][day-close-3]`** are log line mashups from concurrent **`log_print`** (fixed with line lock in 2026-07).

**T0 (pre-deploy baseline):** `preflight_n=0` with massive **`delete defer reason=active_ingest`** means verified paths self-blocked via global **`paths_pending_delete`** in the quarantine skip union (RC-S).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

grep -c 'removing stats file (day raw removal preflight)' /tmp/pipeline-full.log
grep -c 'janitor: day_close delete defer' /tmp/pipeline-full.log
grep -E 'janitor: day_close delete (start|defer)' /tmp/pipeline-full.log | tail -40
```

**T1 (post-deploy RC-S fix):** preflight count **> 0**; defer lines carry **`tar=`**, **`day=`**, **`skip_class=`** (`handoff`, `pending_stats`, `inflight`, `pending_append`, `paths_pending_delete`, `chunk_dispatch`). Legitimate ingest overlap (RC-P) still defers with **`skip_class=pending_stats`** or **`chunk_dispatch`** until the path leaves live ingest sets.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'day raw removal preflight|day_close delete defer.*skip_class=|Day raw removal delete complete' | tail -60
```

**T2 (June-4 retryable-skip stall, RC-J4):** after RC-S deploy, sealed days with all verified paths deleted but retryable skips on disk should handoff (`day_close handoff requeue day=2026-06-04`) and manifest **`phase=done`** when skips clear.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c "sh -lc '
python3 -c \"import json, os; from hpcperfstats.dbload.lib.conf_parser import get_archive_dir_path
p=os.path.join(get_archive_dir_path(), \\\".sync_timedb_day_raw_removal\\\", \\\"2026-06-04.json\\\")
print(open(p).read() if os.path.isfile(p) else \\\"missing\\\")\"'"

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'day_close handoff requeue day=2026-06-04|Day raw removal delete complete day=2026-06-04' | tail -20
```

### T0/T1 — misbucket handoff pin vs soft wait vs post-progress discover (2026-07)

Sticky cross-day handoff (path under an older day-raw-removal manifest whose basename epoch maps to a **future** day with **`no_daily_archive`**) permanently deferred immediate day_close and polluted every chunk histogram. Separately, classify used to treat manifest **`deferred`/`waiting_on_ingest`** as **`day_close_in_progress`**, so cleared days never became **`ready_for_enqueue`** → **`enqueued=0` / `days_started=0`** with free slots.

```bash
docker compose -p hpcperfstats logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# T0 — misbucket pin (should age/clear after deploy; must not forever defer)
grep -E 'handoff_priority_age|handoff_cross_day_skip|immediate day_close defer.*handoff_priority|chunk prewarm days=.*no_daily_archive|oldest_day_chunk_gate.*handoff_cross_day_n=' /tmp/pipeline-full.log | tail -40

# T0 — soft wait only (expected while aligned unprocessed>0)
grep -E 'day_close candidate tar=.*waiting_on_ingest|unprocessed=[1-9]' /tmp/pipeline-full.log | grep day_close | tail -20

# T1 — post-progress discover must start work (not ready_for_enqueue_n≥1 with enqueued=0 forever)
grep -E 'discover_ready_day_close|Archive janitor tick done|janitor: day_close enqueue' /tmp/pipeline-full.log | tail -40
```

**Pass (T0):** after deploy, misbucket leads log **`handoff_priority_age … reason=no_daily_archive`** (or **`handoff_cross_day_skip`**) and **`immediate day_close defer reason=handoff_priority`** stops when the set is empty. Soft **`waiting_on_ingest`** with **`unprocessed>0`** on the ingest head day is **not** a stall.

**Pass (T1):** when oldest advances and prior days have **`unprocessed=0`**, expect **`discover_ready_day_close enqueued≥1`** (or durable **`discover_enqueue_reject`**) and **`Archive janitor tick done`** with **`days_started>0`** / progressing debt — **not** repeating **`ready_for_enqueue_n=1 enqueued=0 skipped_eligible=1 free_slots=4 active_workers=0`** with **`days_started=0`**. Cleared days must **not** stay **`status=queued reasons=day_close_in_progress`** with no live workers.

### T1 verify — janitor proactive day-close (backlog catch-up sites)

After deploy of janitor discover + janitor-only **`DAY_CLOSE`** (2026-07), grep pipeline logs for discovery enqueue and janitor progress (not legacy `async day_close submit` / `eligible_deferred`):

```bash
podman-compose logs pipeline 2>&1 | grep -E \
  'discover_ready_day_close|janitor: day_close enqueue|day_close candidate report|Archive janitor tick done' | tail -40
```

**Pass:** checkpoint-complete older calendar days show **`discover_ready_day_close enqueued=`** or **`janitor: day_close enqueue`**; candidate report uses **`waiting_on_ingest`** / **`ready_for_enqueue`** / **`disqualified`** (no **`eligible_deferred`**); ingest head day stays **`waiting_on_ingest`** while janitor processes prior days; **`Archive janitor tick done`** shows **`days_started>0`** / **`debt_popped>0`** or progressing **`day_phases`** without **`ingest_stall_watchdog`** within 30 min. Under backlog, expect up to **4** concurrent day-close workers (`sync_day_close_max_inflight`) unless tuned lower.

### T1 verify — day-close candidacy honesty (mutable `.tar` + report order, 2026-07)

After deploy of **day-close tar candidacy** fix, confirm report and discover behavior on backlog sites with large mutable daily `.tar` files:

```bash
podman-compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log

grep -E 'day_close candidate report|day_close candidate tar=|discover_ready_day_close' /tmp/pipeline-full.log | tail -80
```

**Pass (T1):**

- Candidate **per-tar lines are oldest calendar day first** (not status-bucket order). Summary still has `queued=` / `waiting_on_ingest=` / `ready_for_enqueue=` / `disqualified=` / `mutable_tar_n=`.
- **`queue_order=1..N` only on `status=queued`** (oldest queued first); non-queued lines show **`queue_order=`** (empty).
- **`checkpoint_incomplete` never appears with `unprocessed=0`** (pre-fix: May-30…Jun-06 `disqualified` + `awaiting_janitor_discover,checkpoint_incomplete` + `unprocessed=0`). Those days must be **`ready_for_enqueue`** (or `queued` once inflight frees) with `mutable_tar=yes`.
- **`status=queued` + `unprocessed=0` finishes** day-close (workers running).
- Days with **`waiting_on_ingest`** and **`unprocessed>0`** (aligned backlog) stay gated — not a skip; they wait on ingest.
- Discover logs **`ready_for_enqueue_n=`**; **`skipped_inflight`** counts only ready entries blocked by `max_inflight`, not all candidates.

### T1 verify — pipeline review fixes (manifest inflight + gate backoff)

After deploy of pipeline review fixes (2026-07), confirm manifest/debt inflight alignment and oldest-day gate backoff:

```bash
podman-compose logs pipeline 2>&1 | grep -E \
  'discover_ready_day_close skipped_inflight|janitor: day_close enqueue skip|status=complete|oldest_day_chunk_gate_stall' | tail -40
```

**Pass:** no sustained **`skipped_inflight`** with stale manifest **`queued`** and zero janitor debt progress; **`status=complete`** appears for finished calendar days after janitor tar-drop; **`oldest_day_chunk_gate_stall`** may appear but ingest resumes ( **`chunk ingest summary`** ) without CPU-only spin (backoff every 32 empty-chunk loops).

### T0 / T1 verify — gated chunk pad + between-chunk reconcile amortize (2026-07)

After deploy of **pad gated chunks / cut between-chunk tax** (Choice C) and the **reconcile-tax** follow-up (handoff defer skip + hard-ceiling cache reuse): under saturated pending (`ingest_queue_max` ≈ `chunk_size`) with frozen `oldest_tar` / `incomplete_n>0`, chunks must fill toward `chunk_size` and reconcile must not dominate the duty cycle with multi‑minute identical accrual rescans.

**Archive signal:** prefer `archive_job_begin` / `archive_job_done` / `Archived batch` / `Archive mapping` — **not** a bare `tar` grep (misses Redis-noop days and worker-role lines).

```bash
# Full log first (never --tail before grep on backlog sites).
podman-compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# T0 — pad + skip + archive_job (not bare tar) + handoff defer
grep -E 'oldest_day_chunk_gate |youngest_day_chunk_gate |oldest_day_chunk_gate_pad|chunk_pad_n=|pending reconcile cap skipped|pending reconcile cap (begin|done)|immediate day_close defer|archive_job_(begin|done)|Archived batch|chunk dispatch begin' /tmp/pipeline-full.log | tail -120

# T1 — chunk_len: ≤chunk_size when gate is oldest; lead_n+chunk_size when additive (gate newer)
grep -E 'oldest_day_chunk_gate .*chunk_len=|youngest_day_chunk_gate .*chunk_len=|handoff_lead_uncapped=' /tmp/pipeline-full.log | tail -40
```

**Pass (T0):** `oldest_day_chunk_gate` / `youngest_day_chunk_gate` / `chunk ingest summary` continues; when oldest/youngest day has fewer paths than `chunk_size` and pending is full, expect **`chunk_pad_n>`0**. **Chunk length:** when the gate day **is** the oldest incomplete/handoff day, **`chunk_len`** stays near configured **`chunk_size`** (not steady `chunk_len≪chunk_size` like pre-fix `419` vs `3000`). When the gate day is **newer** than the oldest non-ingested day (typical CLI ``current`` + June handoff), expect **`handoff_lead_uncapped=yes`** and **`chunk_len ≈ handoff_lead_n + chunk_size`** (may exceed `sync_ingest_queue_max_size`) — not subtractive `chunk_len==chunk_size` with June pins eating July slots. **Do not** treat July-only `Archived batch` as broken oldest handoff when lead telemetry is healthy — see **MisreadArchive** T0 below. Under frozen `incomplete_n` + same gate tar, expect **`pending reconcile cap skipped reason=unchanged_incomplete`** (or `oldest_day_gate_stall_unchanged`) between waves — including when prior cap **`elapsed_s` > soft TTL 120s** (hard ceiling ~900s) — not back-to-back **`pending reconcile cap begin/done source=accrual`** with **`elapsed_s` hundreds–thousands** and identical `incomplete_n`. After `archive_job_done`, expect `chunk dispatch begin` within minutes (not ~14 min of duplicate accrual rebuilds). `immediate day_close defer … handoff_priority` may appear; it must **not** be followed by another full reconcile solely for that defer.

**Pass (T1):** Oldest/youngest-day paths still lead the chunk (`epochs` / sample still prioritize head day); under additive handoff, the **entire** oldest non-ingested day leads first, then a full `chunk_size` of gate/pad — other older days' pins are held (F2/F3) but not prepended. Padded later-day paths may appear **after** head-day paths within the same chunk. Reconcile skip must clear after ingest progress / oldest advance (`incomplete_n` change) or hard-ceiling expiry — next wave may show a full **`pending reconcile cap begin`** again.

### T0 / T1 — MisreadArchive: July-only `Archived batch` ≠ broken oldest handoff (CLI `current`, 2026-07)

**Failure signature (operator misread):** under CLI ``current``, hours of `Archived batch … -> …/YYYY-07-….tar` (newest gate days) with little or no June `Archived batch`, interpreted as “oldest handoff not working.” That signal alone is **insufficient**.

**Why July archive can be healthy:** additive select (`handoff_lead_uncapped=yes`) prepends the whole oldest non-ingested day, then still takes a full `chunk_size` of the **youngest** incomplete gate (often July). Archive append follows **file calendar day** — July gate/pad files dominate `Archived batch` while June lead files that are **already tar members** finish as **`checkpoint_immediate`**. Then `checkpoint_immediate_n ≈ handoff_lead_n` and **no** June `Archived batch` line is expected.

**Not MisreadArchive — DbReadyNotInArchive:** when lead-day paths are DB-ready but **`not_in_sealed_archive`** (open June `.tar` still growing / never sealing), `checkpoint_immediate` for those pins is a **stall**, not health. Require `open_tar_n` decline / `handoff_mode=archive_append` / reclassify drain — see Branch C / DbReadyNotInArchive T0 above. Green `handoff_lead_uncapped` alone does **not** clear day-close health.

```bash
# T0 — lead vs archive day (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'handoff_lead_uncapped|youngest_day_chunk_gate |chunk_day_histogram|chunk ingest summary|Archived batch|archive_job_duty|soft_skip' /tmp/pipeline-full.log | tail -120
```

**Fail (T0) — real lead/archive bugs (not MisreadArchive):**

| Class | Signature |
|-------|-----------|
| **NoLead** | No `handoff_lead_uncapped=yes` while older closed/unprocessed days remain; `chunk_day_histogram` omits the calendar-oldest work day under youngest gate |
| **SubtractiveDeployGap** | Steady `chunk_len==chunk_size` with June pins eating July slots and **no** `handoff_lead_uncapped` (additive code not deployed) |
| **LeadIngestNoArchive** | `handoff_lead_uncapped=yes` **and** June lead paths appear in `archive_deferred_n` / `archive_job_duty` / `to_add=` with soft_skip or zero append — **not** when `checkpoint_immediate_n≈handoff_lead_n` **and** those pins are already tar members |
| **DbReadyNotInArchive** | `handoff_lead_uncapped=yes` + sticky `skipped_not_in_archive` / open multi-hundred-GB June `.tar` + DB-ready sample not in members; ingest-only handoff without `handoff_mode=archive_append` |

**Pass (T0) — MisreadArchive (healthy):** `handoff_lead_uncapped=yes` with June (or older) in `chunk_day_histogram`; `chunk_len ≈ handoff_lead_n + chunk_size` (or lead + gate + pad); July `Archived batch` / `archive_job_duty` for gate/pad days; chunk ingest summary shows **`checkpoint_immediate_n≈handoff_lead_n`** for the lead day while July paths account for **`archive_deferred_n`**. Do **not** open a NoLead incident from July-only `Archived batch` alone.

**Pass (T1):** Lead day rotates through older closed-raw / orphan-rehandoff days (`handoff_lead_day=…`) while youngest gate may flip among recent incomplete tars; lead telemetry stays uncapped; July-heavy archive waves continue without requiring June `Archived batch`.

### T1 verify — ingest oldest-first (hpcperfstats03 / cross_day_bucket gate defer)

After deploy of **ingest oldest-first** fix (2026-07), confirm dispatch order on backlog catch-up sites with **277k+** closed raw on disk. **Do not** infer skip from **`ingest file path=`** alone — parallel chunk workers complete out of order.

```bash
# Full log first (never --tail before grep on backlog sites).
podman-compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# Dispatch order: each chunk logs paths_sample + epochs before pool work.
grep -E 'chunk dispatch begin|oldest_day_chunk_gate_cross_day_defer|oldest_day_chunk_gate_fallback' /tmp/pipeline-full.log | tail -80

# Failure signature (pre-fix): May-26 oldest_tar with June calendar_days in fallback while pending_n >> chunk_size.
grep 'oldest_day_chunk_gate_fallback' /tmp/pipeline-full.log | grep -E '2026-05-2[0-9].*tar.*2026-06' || true

# Cap collapse (pre-fix): pending reconcile cap dropping global tail when handoff consumed budget.
grep 'pending reconcile cap' /tmp/pipeline-full.log | tail -20
```

**Pass (T1):** After May-22 head day clears, no **`oldest_day_chunk_gate_fallback`** whose **`calendar_days`** are **months ahead** of **`oldest_tar`** while **`pending_n`** remains large; **`oldest_day_chunk_gate_cross_day_defer`** may appear (expected — resumes global pending head). **`chunk dispatch begin`** `epochs` trend oldest-first at chunk boundaries. **`pending reconcile cap`** retains oldest global head (no 987→424 style collapse when only a few checkpoint-blocked paths remain for advanced `oldest_tar`).

### T1 — CLI ``current`` (newest-first) dual verify

Run **one** recommended ``current`` process per archive (last writer wins the heartbeat; multiple ``current`` processes are unsupported). Pair with ``backlog`` for backlog ingest; ``backlog`` exits when its next oldest pending day is within ``sync_ingest_current_proximity_days`` (default **2**) of ``current``'s fresh heartbeat.

**Dual-mode day-close ownership (2026-07):** CLI ``backlog`` is **ingest-only for the cold path** — it must **not** discover, enqueue, or run ``DAY_CLOSE`` (no immediate day_close, no janitor seal/verify/delete). Startup snapshot + boot handoff for ingest catch-up still run. Expect log ``day_close disabled mode=backlog owner=current_or_date_range``. ``current`` (or a date-range run) owns day-close and the day-close sidecars (``.sync_timedb_async_day_close.json``, ``.sync_archive_maint_hints.json`` day_phases/debt, ``.sync_timedb_day_raw_removal/``). Heartbeat remains one-way (``current`` writes / ``backlog`` reads). Shared ingest checkpoint / append metadata may still be written by both processes.

```bash
# T1 — dual-mode: all must not run day_close; current may
grep -E 'day_close disabled mode=backlog|janitor: day_close |discover_ready_day_close|newest-first / current mode|backlog exiting near current' /tmp/pipeline-full.log | tail -80

# T1 — current scheduling (descending epochs / youngest gate)
grep -E 'chunk dispatch begin|youngest_day_chunk_gate|newest-first / current mode|backlog exiting near current' /tmp/pipeline-full.log | tail -80

grep -E 'youngest_day_chunk_gate(_pad|_stall|_cross_day_defer|_fallback)? ' /tmp/pipeline-full.log | tail -40

# Heartbeat (sidecar under archive_dir, or Redis key hpcperfstats:sync_timedb:current_heartbeat)
ls -la /path/to/archive/.sync_timedb_current_heartbeat.json 2>/dev/null || true
```

**Pass (T1 dual-mode):** under concurrent ``current``+``backlog``, only ``current`` emits ``janitor: day_close`` / ``discover_ready_day_close`` work for overlapping days; ``backlog`` shows ``day_close disabled mode=backlog`` and proximity exit near the head. Solo ``backlog`` (no ``current``) will **not** seal/delete — cold path requires ``current`` or a date-range run.

**Pass (T1 ``current``):** ``chunk dispatch begin`` ``epochs=`` trend **descending** (newest first); expect **`youngest_day_chunk_gate`** / **`youngest_day_chunk_gate_pad`** (not the ``backlog`` ``oldest_day_*`` grammar for the same run). Missing/stale heartbeat must **not** stop ``backlog`` — only a fresh heartbeat near the pending head day triggers ``backlog exiting near current``.

**Pass (T1 — aligned backlog / day-close skip fix, 2026-07):** `oldest_tar` must be a day with **tar-aligned** on-disk unprocessed (filename/mtime calendar day matches the tar), not a day whose only remaining map entries are cross-day misbuckets (e.g. May-27 `oldest_tar` with `calendar_days={'2026-07-03': 1}` only). Day-close candidate lines: `unprocessed=` is **aligned** count; optional `unprocessed_cross_day_n=` for misbucketed paths that do **not** block that day. **`processed_but_on_disk=`** is aligned leftover closed raw; optional **`processed_cross_day_n=`** for first_ts misbuckets that must **not** reopen that calendar day. **`chunk dispatch begin`** / **`chunk imap start`** must not sit on a later month (e.g. June-07) while earlier days still report **aligned** `unprocessed>0`. Cap may log **`pending cap supplement replace`** when a full queue is rebuilt from older snapshot/unprocessed paths; **`pending cap supplement skipped reason=no_closed_paths`** is OK after accrual trim when Phase-B all-unprocessed merge already filled the head.

```bash
# Aligned gate: oldest_tar should not be pinned by cross-day-only misbuckets.
grep -E 'oldest_day_chunk_gate |pending reconcile cap done|pending cap supplement' /tmp/pipeline-full.log | tail -40

# Day-close report: unprocessed= is aligned; cross_day_n is diagnostic only.
grep 'janitor: day_close candidate' /tmp/pipeline-full.log | grep -E 'waiting_on_ingest|unprocessed_cross_day|processed_but_on_disk|processed_cross_day' | head -40
```

**Pass (T1 — cross-day remaining_raw blocking, 2026-07):** Sealed-only past days must **not** re-enter day-close / `missing_tar` restore solely because a path keyed under that tar has a **different filename epoch calendar day** (e.g. May-28 blocked by July-6 epoch `…/1783338980`). After fix: `day_close_filesystem_complete` / `needs_work` ignore those misbuckets; candidate lines may still show `processed_cross_day_n>0` without `ready_for_enqueue` for that wrong day. Grep:

```bash
grep -E 'discover_ready|missing_tar|processed_cross_day_n|processed_but_on_disk|filesystem_complete' /tmp/pipeline-full.log | tail -60
```

**Pass (T1 — empty Redis after prewarm):** After `chunk prewarm complete` with a day token other than `no_daily_archive` / `day_ingest_skip`, Redis must be warm (`dbsize` / `archive_members*` keys) and **`chunk imap start`** must appear. **Failure signature (pre-fix, claimed success hang):** `…:prewarmed` (or similar) with `dbsize=0` and no `chunk_elapsed` / no `chunk imap start`. **Post-fix (true empty):** supervisor **exits** with `archive members Redis empty after prewarm` rather than hanging.

**T0 / T1 — prewarm identity drift during tar append (2026-07):** concurrent `archive_pool` append/merge can change the Redis identity fingerprint while chunk prewarm holds entry-time keys.

```bash
# T0 — false empty-after-prewarm during append (unhealthy)
grep -E 'populate_wait identity_drift|empty after prewarm|archive members Redis L2 contract failed|tar_append redis merge|archive_job_done' /tmp/pipeline-full.log | tail -80

# T1 — healthy: identity_drift may appear, but prewarm re-resolves / retries; no L2 exit
grep -E 'populate_wait identity_drift|archive_append_inflight during archive members prewarm|chunk prewarm complete|chunk imap start|empty after prewarm' /tmp/pipeline-full.log | tail -80
```

**Unhealthy:** `INFO: populate_wait identity_drift day=…` then `ERROR: archive members Redis empty after prewarm … source=none members_n=N` (N often >0) + `L2 contract failed` while the same day shows `tar_append redis merge` / `archive_job_done`. **Healthy:** drift and/or `WARNING: archive_append_inflight during archive members prewarm … retrying` then `chunk prewarm complete` with `redis_warm` / populate source and **`chunk imap start`** — no empty-after-prewarm L2 exit.

**T0 / T1 — L1 hit + cold Redis (no append_inflight) (2026-07-21):** process L1 can return `members_n>0` with `source=none` while Redis stays cold; prewarm must **not** L2-exit on the first cold check when retries remain.

```bash
# T0 — false empty-after-prewarm from L1 / members_n>0 without inflight (unhealthy)
grep -E 'members returned but Redis cold|empty after prewarm|L2 contract failed|Prewarming archive members Redis|chunk prewarm begin|chunk imap start' /tmp/pipeline-full.log | tail -80

# T1 — healthy: WARNING members returned but Redis cold … retrying then warm / imap start
grep -E 'members returned but Redis cold|archive_append_inflight during archive members prewarm|chunk prewarm complete|chunk imap start|empty after prewarm' /tmp/pipeline-full.log | tail -80
```

**Unhealthy:** `Prewarming …` then within ~10ms `ERROR: … empty after prewarm … source=none members_n=N` + `L2 contract failed` with **no** `members returned but Redis cold` / inflight WARN (often tar-only day, Redis `hlen=0`). **Healthy:** `WARNING: members returned but Redis cold … retrying` (and/or inflight WARN) then prewarm success and **`chunk imap start`** — no immediate L2 exit.

**Compare dispatch vs completion:**

```bash
grep -E 'chunk dispatch begin|ingest file path=' /tmp/pipeline-full.log | tail -40
```

Within one chunk, **`ingest file path=`** epochs may permute; across chunks, **`chunk dispatch begin`** `epochs` must not leap months ahead while earlier calendar days still have **aligned** closed raw on disk.

### T2 verify — idle rescan stall + cap refill (hpcperfstats03, 2026-07)

After deploy of **idle rescan snapshot refill** fix, confirm empty-queue rescans do not block ~14 minutes on **`wait_for_snapshot`**, and cap refill restores **`ingest_queue_max`** backlog instead of collapsing to **`incomplete_n`** only.

```bash
podman-compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# Idle rescan must complete (no begin without done for >5m).
grep -E 'pending rescan begin|pending rescan done|idle_rescan_snapshot' /tmp/pipeline-full.log | tail -40

# Snapshot wait during idle refill (should be rare; accrual/coordinator fast-path).
grep 'idle_rescan_snapshot_wait' /tmp/pipeline-full.log | tail -20

# Cap must supplement from snapshot when cross-day stragglers remain.
grep -E 'pending reconcile cap|pending cap supplement' /tmp/pipeline-full.log | tail -30

# Cross-day db_skip micro-chunk should clear stall without endless gate spin.
grep 'oldest_day_chunk_gate_cross_day_db_complete' /tmp/pipeline-full.log | tail -20

# Janitor pre_seal_verify must not block ticks without progress logs.
grep -E 'pre_seal_verify (start|tar_restore|classify progress|complete)' /tmp/pipeline-full.log | tail -40

# Orphan giant archive job when chunk had zero archival (pre-fix).
grep -E 'Files marked for archival: 0|archive_job_begin' /tmp/pipeline-full.log | tail -40
```

**Pass (T2):** Every **`pending rescan begin`** is followed by **`pending rescan done`** within minutes (not ~862s **`startup archive scan ready wait_s`** on MainThread alone). **`idle_rescan_snapshot_source=accrual|coordinator`** appears on idle refill. **`pending cap supplement`** or **`capped_pending`** near **`ingest_queue_max`** after queue drain despite **`incomplete_n=2`** cross-day stragglers. **`oldest_day_chunk_gate_cross_day_db_complete`** after all-**`db_skip=head_tail`** defer chunks. During large-day pre-seal, **`pre_seal_verify classify progress`** should show rising **`verified_n/N`** within one worker session, then **`pre_seal_verify complete`** — **`verify budget exhausted`** must **not** appear (removed). **`pre_seal_verify start`** without **`complete`** for >5m is a failure. No **`archive_job_begin`** for unrelated calendar days immediately after **`Files marked for archival: 0`** chunks.

### T0 / T1 / T2 verify — idle-rescan stale snapshot stall (hpcperfstats04, 2026-08)

**Failure signature (pre-fix):** hosts keep appending `current` and rotating closed epochs; RabbitMQ drained; supervisor alive; yet **`Worker idle loops while waiting for pending files`** advances every ~300s with identical **`idle_rescan_snapshot_source=coordinator closed_paths=N`** (frozen N) and **`pending=0`**. Light janitor ticks show **`maintenance_pass_s=0.000`** (no heavy snapshot republish). Timescale **`host_data`** lag: **`newest_3h` NULL** while `newest_24h` is many hours old.

**Root cause:** empty-queue idle refill sets **`force_snapshot_paths=True`**; **`rescan_pending_stats_files`** used **only** the coordinator snapshot (all members already in `processed_files`) and never walked disk.

**Fix:** idle **`force_snapshot_paths`** unions snapshot with **`collect_stats_files_in_range`** (mtime by default; full find on Nth tick). Non-idle `should_force_full` alone stays snapshot-only.

```bash
# T0 — frozen snapshot + advancing idle counter (pre-fix signature)
docker compose -p hpcperfstats logs pipeline 2>&1 | \
  grep -E 'idle_rescan_snapshot_source=|Worker idle loops|idle_rescan_merge|ingest file path=|Number of host stats files to process' | tail -80

# T0 — Timescale freshness (cheap LIMIT 1; never multi-day GROUP BY max(time))
docker compose -p hpcperfstats exec db psql -h localhost -U hpcperfstats -c "SET statement_timeout='45s'; SELECT time AS newest_3h FROM host_data WHERE time > now() - interval '3 hours' ORDER BY time DESC LIMIT 1;" -c "SET statement_timeout='60s'; SELECT time AS newest_24h FROM host_data WHERE time > now() - interval '24 hours' ORDER BY time DESC LIMIT 1;"

# T1 — merge / pending recovery after deploy
docker compose -p hpcperfstats logs pipeline 2>&1 | \
  grep -E 'idle_rescan_merge|pending rescan done|chunk dispatch begin|chunk ingest summary|Worker idle loops' | tail -60
```

| Tier | Pass |
|------|------|
| **T0** | Pre-fix: frozen `closed_paths=N` + rising idle counter + `newest_3h` NULL confirms stall. Post-deploy smoke: supervisor still `sync_timedb.py [main]`; no crash-loop. |
| **T1 (~1h)** | **`idle_rescan_merge … find_only_n>0`** (or pending non-zero without further idle counter advance) when closed raw accrues after snapshot publish; **`chunk dispatch begin`** / **`ingest file path=`** resume; idle-loop counter stops advancing or resets. |
| **T2 (catch-up)** | **`newest_3h` non-NULL** and trending toward wall clock; `closed_paths=` / merge counts not stuck on a single frozen N for hours while rotations continue; no return of perpetual `pending=0` idle loops with healthy upstream. |

Regression tests: **`test_rescan_force_snapshot_merges_incremental_find_for_post_snapshot_closes`**, **`test_rescan_force_snapshot_paths_uses_closed_list_despite_rescan_count`**, **`test_rescan_force_full_snapshot_without_force_flag_stays_snapshot_only`**.

### T0 / T1 verify — enqueued but `days_started=0` (stale/ghost deferred trap, 2026-07)

**Failure signature (pre-fix / hpcperfstats03):** discover reports **`enqueued≥1`** (or repeated **`janitor: day_close enqueue`**) while the same or next tick ends with **`debt_popped=0 days_started=0 debt_remaining>0`** and **`active_workers=0`**. Async day-close sidecar shows heap days with **`status=deferred`** and **`detail=stale_manifest_recovery`** or **`ghost_manifest_reconcile`** (not classic **`waiting_on_ingest`**). No **`day-close-N`** worker progress lines. Multi-hour tick **`duration_s`** here is time spent in reconcile/discover before fill, not live workers.

```bash
# T0 — enqueue-without-start signature
grep -E 'discover_ready_day_close|Archive janitor tick done|janitor: tick zero_pop|stale manifest recovery|ghost manifest reconcile|day_close enqueue' /tmp/pipeline-full.log | tail -100

# T0 — sidecar: deferred stale/ghost vs queued (INI archive path first)
# Look for status=deferred detail=stale_manifest_recovery|ghost_manifest_reconcile on debt_queue days
```

**Pass (T0 after fix redeploy):** after stale/ghost reconcile lines, expect those tars restored to **`status=queued`** (detail cleared); subsequent **`Archive janitor tick done`** shows **`debt_popped>0`** / **`days_started>0`** when free slots exist, or durable **`janitor: tick zero_pop … disqualified_on_heap=`** when the heap is truly all disqualified (then chain-wake). Must **not** repeat **`enqueued≥1`** + **`debt_popped=0 days_started=0`** for hours with **`active_workers=0`** while non-ingest deferred rows sit on the debt heap.

**Pass (T1):** backlog head days progress through seal/verify/delete; **`waiting_on_ingest`** deferred remains only for true ingest handoff (do not auto-promote). Partial seal (`.tar` + `.tar.zst` present) still runs day-close workers.

### T0 / T1 verify — zero_pop budget starvation before fill (2026-07-17)

**Failure signature (pre-fix):** repeating **`janitor: tick zero_pop debt_remaining=N free_slots=N disqualified_on_heap=0 sample_tars=-`** with **`debt_popped=0 days_started=0`** and **`active_workers=0`**, often after multi-minute/hour **`duration_s`** spent in discover/reconcile. Drain budget was armed **before** `get_disqualified_daily_tars` / lock cleanup, so fill never entered; empty `sample_tars` is **not** proof the heap is clear.

**Acceptance (post-deploy):**

- Log **`janitor: tick debt_drain_begin budget_s=… debt_remaining=… free_slots=…`** after prefill.
- When `debt_remaining>0` and `free_slots>0`, expect **`debt_popped>0`** / **`days_started>0`** (or zero_pop with **`disqualified_on_heap>0`** + sample tars + **`budget_remaining_s=`**), not endless `sample_tars=-` starvation.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'debt_drain_begin|tick zero_pop|Archive janitor tick done|days_started=' | tail -80
```

### T1 verify — day-close idle threads + discover cap split (2026-07)

After deploy of **day-close idle thread recovery**, confirm discover enqueues NEW ready days when debt heap is non-empty but pool workers are idle, and mid-tick refill uses free slots.

```bash
podman-compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# Discover cap vs debt heap (RC-1): discover_cap should track live workers + manifest slots, not debt_heap alone.
grep -E 'discover_ready_day_close|Archive janitor tick done|janitor: tick budget_exit' /tmp/pipeline-full.log | tail -80

# Mid-tick slot-free discover when heap drains before pool fills.
grep 'discover_ready_day_close.*reason=tick_slot_free' /tmp/pipeline-full.log | tail -20

# Budget partial tick follow-up (RC-4).
grep 'janitor: tick budget_exit debt_remaining=' /tmp/pipeline-full.log | tail -20
```

**Pass (T1):**

- With **`active_workers=0`** and **`ready_for_enqueue_n>0`**, discover shows **`enqueued>0`** even when **`debt_heap>0`** (debt heap no longer blocks discover cap).
- Discover log distinguishes **`discover_cap=`** (live workers + manifest worker slots) from **`worker_occupancy=`** (legacy, includes debt heap) and **`debt_heap=`**.
- When some **`day-close-N`** workers are busy and others idle, **`reason=tick_slot_free`** discover may appear with **`free_slots=`**; **`Archive janitor tick done`** shows **`days_started>0`** without long silence while **`ready_for_enqueue`** days remain.
- After budget break with remaining debt: **`janitor: tick budget_exit debt_remaining=N scheduling_followup=yes`** followed by another janitor tick (not multi-hour idle pool threads).
- **Non-blocking budget_exit (2026-07):** after **`budget_exit`**, expect **`janitor: tick budget_exit leave_in_flight=N`** (or **`leave_in_flight=`**) and **`Archive janitor tick done … duration_s=`** in **seconds/minutes**, not multi-hour **`duration_s≈9217`** with **`days_completed=0`** while workers still run. Follow-up ticks must reap/fill siblings without waiting for the first day-close to finish.
- **Candidate counters:** `on_disk=` is **total** aligned closed-raw on disk; `unprocessed=` + `processed_but_on_disk=` partition it (`on_disk = unprocessed + processed_but_on_disk`). Do not treat equal three-way counters as proof of progress when leftover raw remains.

### T0 / T1 verify — ingest-pool sealed stream + populate stall (2026-07)

**Failure signature (pre-fix / contract violation):**

```bash
grep -E 'ingest-pool\].*sealed archive member stream|Archive members populate stalled|archive members populate stalled|budget_exit|leave_in_flight|duration_s=' /tmp/pipeline-full.log | tail -80
```

- **`[sync_timedb:worker:ingest-pool] … sealed archive member stream failed`** (often `ingest per-file timeout … elapsed_s=…`) → ingest illegally streamed sealed `.tar.zst` under SIGALRM.
- Then **`Archive members populate stalled (no progress for 120s)`** → supervisor **`exit status 1`** while Redis is still reachable.
- Orthogonal: **`janitor: tick budget_exit`** then **`Archive janitor tick done … duration_s=9xxx`** with **`days_completed=0`** → coordinator drain-wait bug (fixed by non-blocking leave_in_flight).

**Pass (T0/T1 post-fix):**

- **Zero** `ingest-pool].*sealed archive member stream` lines.
- Populate stall within `populate_max_seconds` with alive lock owner recovers (re-enqueue / keep waiting) — not immediate exit 1 on first 120s heartbeat gap.
- Stall defer prefers **`redis_populate_active`** over **`idle_pool_ghost_inflight`** while Redis populate lock is held.
- Day-close: **`leave_in_flight`** after **`budget_exit`**; tick **`duration_s`** stays near budget (not multi-hour).

**Root-cause decision tree (idle `day-close-N` threads):**

| Observation | Likely cause | Fix track |
|-------------|--------------|-----------|
| `skipped_inflight=N`, `active_workers=0`, `worker_occupancy>=max_inflight`, `debt_heap_n=0` | Ghost manifest slots block discover; heap empty | Reconcile (manifest ghost) |
| Same but `debt_heap_n>0`, no `Archive janitor tick done` after budget | Tick not waking / budget chain gap | Budget partial-tick wake |
| `skipped_inflight=N`, `active_workers=0`, `debt_heap_n>=N`, `discover_cap<max_inflight` | Debt counted as discover occupancy (pre-fix) | Discover cap split |
| `Archive janitor tick done` + `debt_remaining>0`, long silence | No `_pending_signal` after partial tick | Budget partial-tick wake |
| `active_workers>0`, `free_slots>0`, `enqueued=0`, heap empty | No mid-tick discover on slot-free refill (pre-fix) | `tick_slot_free` discover |
| Repeated `tar drop deferred`, one `handoff requeue` only | Ingest handoff starvation (separate plan) | Prior handoff plan |

**Pre-fix failure signature (hpcperfstats03 finding #4):** `discover_ready_day_close enqueued=0 skipped_inflight=8 ready_for_enqueue_n=8 max_inflight=4 active_workers=0 debt_heap=4 worker_occupancy=4 manifest_pending=0` — **`worker_occupancy` tracks `debt_heap`**, not live workers.

### T1 verify — discover silent reject + budget-wait heartbeat (RC-5, 2026-07)

After deploy of **discover enqueue reject logging** and **tick wait heartbeat**, confirm operators can distinguish **saturated pipeline** (busy workers) from **silent enqueue reject** and **janitor budget-wait**.

```bash
cd HPCPerfStats

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'discover_ready_day_close|discover_enqueue_reject|skipped_eligible|janitor: tick (budget_exit|waiting)|Archive janitor tick done' | \
  tail -60
```

**Pass (T1):**

- When **`ready_for_enqueue_n>0`**, **`enqueued=0`**, and **`skipped_inflight=0`** with **`free_slots>0`**: grep shows **`discover_enqueue_reject`** with explicit **`reason=`** (for example **`already_on_debt_heap`**, **`checkpoint_incomplete`**, **`disqualified`**) and discover summary includes **`skipped_eligible=N`**.
- After **`janitor: tick budget_exit`**, either **`Archive janitor tick done`** within minutes **or** **`janitor: tick waiting in_flight=N debt_remaining=M tars=…`** every **~5 minutes** while day-close workers remain busy (long **`pre_seal_verify`** / seal is **not** a hang by itself).

**RC-5 failure signature (pre-fix):** `ready_for_enqueue_n=2 enqueued=0 skipped_inflight=0 free_slots=1` with **no** `discover_enqueue_reject` lines — operator cannot tell reject reason from discover summary alone.

| Observation | Likely cause | Action |
|-------------|--------------|--------|
| `enqueued=0`, `skipped_inflight=0`, `skipped_eligible>0`, `discover_enqueue_reject reason=already_on_debt_heap` | Classify **`ready_for_enqueue`** race vs debt heap | Normal if day already queued; watch for duplicate heap push (should not occur) |
| `discover_enqueue_reject reason=checkpoint_incomplete` on **`ready_for_enqueue`** days | Classify/submit gate mismatch | File follow-up (Fix B); ingest head may still be incomplete |
| `budget_exit` then silence >5 min, no `tick waiting`, py-spy stuck in same frame | Worker hang (RC-6) | py-spy + targeted hang fix |
| `budget_exit` + periodic `tick waiting` + active day-close stacks | Saturated pipeline (normal) | Wait; no redeploy required |

## Log source attribution (`[sync_timedb:role]`)

After deploy of log-role prefixes (2026-06), pipeline lines identify **which actor** emitted them. Greps for **`[sync_timedb]`** still match (substring). As of 2026-08, **`format_log_prefix` always includes a role** — unset role defaults to **`main`** (`[sync_timedb:main]`). Bare **`[sync_timedb]`** (no role) appears only in **legacy** pre-fix logs.

### Body facets (2026-07 — outside brackets)

`log_print` may add a greppable body prefix **after** the brackets (never inside them):

| Body prefix | When |
|-------------|------|
| `janitor:` | Janitorial work when the role does **not** already contain `janitor` (e.g. `[sync_timedb:main] janitor: …`, `[sync_timedb:thread:day-close-0] janitor: …`). Role `thread:archive-janitor` **strips** a redundant body `janitor:` so the line is not double-labeled. |
| `ingest:` | **MainThread** ingest work / pre-work only (`[sync_timedb:main] ingest: pending reconcile …`, `chunk ingest summary`, `post_finalize_reconcile`, …). Pool workers (`worker:ingest-pool`, populate-pool) do **not** get this facet. |

Prefer: `grep -E 'janitor:|ingest:|thread:archive-janitor|thread:day-close-'`. Do **not** expect a second `sync_timedb:` in the message body — brackets already name the script.

### Log prefix → actor

| Log prefix | Process / thread | Responsibility |
|------------|------------------|----------------|
| `[sync_timedb:main]` | Supervisor main thread in pipeline PID | Chunk loop, handoff, oldest-day gate, rescan, enqueue day-close |
| `[sync_timedb:worker:ingest-pool]` | Spawned ingest worker | Parse + DB ingest (combined pool) |
| `[sync_timedb:worker:ingest-parse-pool]` | Spawned parse worker | Split pipeline parse stage |
| `[sync_timedb:worker:db-writer-pool]` | Spawned DB writer | Split pipeline DB write stage |
| `[sync_timedb:worker:archive-pool]` | Spawned archive append worker | **Hot-path tar append** (`map_async` dispatch) — **not** the janitor |
| `[sync_timedb:thread:archive-janitor]` | Daemon thread in supervisor PID | Cold path: seal → verify → delete → tar-drop; boot **`DAY_CLOSE`** discover |
| `[sync_timedb:thread:day-close-N]` | Day-close pool worker thread | Per-day seal → verify → delete → tar-drop |
| `[sync_timedb:thread:startup-raw-removal-preflight]` | Daemon thread | Boot raw removal verify/delete |
| `[sync_timedb:thread:startup-tail-ingest]` | Daemon thread | Optional tail ingest before steady state |
| `[sync_timedb:thread:archive-discovery]` | Short-lived helper thread | Archive metadata scan during heavy maintenance |
| `[sync_timedb]` (no role segment) | **Legacy only** (pre-2026-08 unset-role default) | Prefer message heuristics below; current runtime always has `:role` |

**Naming note:** cpuset / process-bucket text **"sync_timedb archive workers"** means the **append pool** (`worker:archive-pool`), not the janitor thread. The janitor runs inside the supervisor process on **`thread:archive-janitor`**.

### Legacy message heuristics (pre-role deploy)

When the prefix has no `:role` segment, use message substrings:

| Substring | Likely actor |
|-----------|----------------|
| `chunk ingest summary`, `oldest_day_chunk_gate`, `handoff`, `startup_elapsed_s`, body `ingest:` | Main supervisor ingest / pre-work |
| `janitor:`, `Archive janitor tick` | Janitor thread or MainThread day-close helpers |
| `discover_ready_day_close` | Janitor boot/steady-state DAY_CLOSE discover (role may already say `archive-janitor`) |
| `startup raw removal` | Startup raw removal preflight |
| `Pool imap stalled`, `worker_stages` | Ingest or parse pool worker |
| `Archive mapping`, `archive_job_done` (from worker context) | Often main coordinating append; append work runs on `archive-pool` workers |

### Idle `top` + `long_ingest_budget` stall defer (RC-A1 — not a hang)

**Misread:** Host `top` can look “idle” while ingest is healthy — supervisor MainThread often waits on imap; work lives on **`[sync_timedb:worker:ingest-pool]`** PIDs (often ~100%+ CPU). Filter `ps`/`top` for **`worker:ingest-pool`**, not only the main `sync_timedb.py [main]` line.

**Healthy WARN:** `WARN: pool imap stall deferred: long ingest budget` / `defer_reason=long_ingest_budget` means the sliding-window stall timer is deferred because an in-flight path’s **`effective_ingest_timeout_s`** exceeds the batch precompute — expected on giant files / long `db_write`. This is **not** exit **124** and **not** an idle spin.

**Shared stages (RC-A):** stall snapshots may show workers in **`populate_queue_wait`** while other workers parse giants. An idle-looking `populate_queue_wait` row next to `long_ingest_budget` is **normal shared-stage telemetry**, not proof that ingest is stuck on populate. Prefer `chunk ingest summary`, `giant pool supplement begin|replenish`, and busy `worker:ingest-pool` PIDs over a single stage token.

**RC-D queue semantics:** no-supplement process queue = **`sync_ingest_queue_max_size`** (default **3000**). Ingest **chunk size** follows the same knob (`get_sync_ingest_chunk_size` alias — leftover `sync_ingest_chunk_size=` INI lines are ignored). Giant-supplement reservoir = **queue × `sync_ingest_giant_pool_supplement_queue_multiplier`** (default **2 → 6000**) at **batch start and mid-imap refresh**. Grep **`giant pool supplement replenish`** when giants run for hours with disk backlog; **`giant pool supplement empty reason=exhausted|size_filter`** when the reservoir has nothing eligible.

**RC-E stop condition (2026-07):** pool supplement may run **only while a path from the batch's own set is in flight**. Primary-iterator exhausted + idle slots is the normal end-of-chunk condition — it must **not** by itself authorize unbounded replenish against a live-growing closed-path snapshot. **Fail (pre-fix / regression):** one `chunk imap start` then hours of `giant pool supplement replenish` / `begin … in_flight_giants=[]` with **no** `chunk_elapsed_s` / `chunk ingest summary`. **Pass:** `giant pool supplement stop reason=batch_paths_complete` after original paths complete; `chunk_elapsed_s` advances; healthy giant-tail still shows `supplement=yes` / `replenish` **while** the original giant remains in flight.

```bash
# T0/T1 — RC-E supplement stop vs unbounded replenish (full log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 \
  | tee /tmp/pipeline-full.log >/dev/null
echo "=== RC-E counts ==="
grep -cE 'giant pool supplement stop reason=batch_paths_complete' /tmp/pipeline-full.log || true
grep -cE 'giant pool supplement replenish' /tmp/pipeline-full.log || true
grep -cE 'chunk_elapsed_s|chunk ingest summary' /tmp/pipeline-full.log || true
echo "=== recent supplement/chunk ==="
grep -E 'giant pool supplement|chunk_elapsed_s|chunk dispatch begin|chunk imap start|chunk ingest summary' /tmp/pipeline-full.log | tail -80
```

```bash
# Full pipeline log first (no --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 \
  | tee /tmp/pipeline-full.log

grep -E 'long_ingest_budget|stall deferred|Pool imap stalled|chunk ingest summary|giant pool supplement|populate_queue_wait' /tmp/pipeline-full.log | tail -80

docker compose -p hpcperfstats -f docker-compose.yaml exec -T pipeline \
  sh -c 'ps -eLo pid,pcpu,args | grep -E "worker:ingest-pool|sync_timedb.py" | grep -v grep | head -40'
```

**Pass (T0):** `chunk ingest summary` continues; ingest-pool workers busy; only defer WARNs (no `ERROR: Pool imap stalled` → exit 124). **Fail:** true stall ERROR, or main+workers idle with no ingest progress for the T1 window.

### Leftover daily `.tar` day-close (VERIFYING+POST_SEAL / mixed skips)

After deploy of quarantine-transparent waiting_on_ingest + phase promote:

- **`phase=verifying` + `verify_stage=post_seal_complete`** must promote (log `promote phase=verification_complete`) then reach **`delete start`** or handoff — not seal-only forever.
- On-disk mix of **`skipped_not_in_archive` + `skipped_quarantine`** after verified delete must reach **`phase=done`** (waiting_on_ingest) + handoff retryables — not seal↔delete reloop.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 \
  | grep -E 'promote phase=verification_complete|day_close delete start|delete deferred tar=.*delete_disqualified|Day raw removal delete complete|waiting_on_ingest' \
  | tail -60
```

### Correlate logs with `ps` (read-only exec)

```bash
cd HPCPerfStats   # checkout with docker-compose.yaml on site
docker compose -f docker-compose.yaml -p hpcperfstats exec -T pipeline \
  sh -c 'ps -eLo pid,tid,pcpu,stat,args | grep -E "sync_timedb|worker:|thread:" | grep -v grep | head -30'
```

Filter live logs by role:

```bash
docker compose -f docker-compose.yaml -p hpcperfstats logs --names pipeline 2>&1 \
  | grep --line-buffered '\[sync_timedb:thread:archive-janitor\]'

docker compose -f docker-compose.yaml -p hpcperfstats logs --names pipeline 2>&1 \
  | grep --line-buffered '\[sync_timedb:main\]'
```

## Optional cron (backlog catch-up week)

Every 2 h: full-log grep for `oldest_day_chunk_gate_stall` and `ingest_stall_watchdog` on pipeline; alert on non-zero new matches.

## What T0 alone does not prove

- Handoff-after-giant-finalize idle spin (RC-F family) — requires **T1** after first head-day `archive_job_done`.
- State-transition log noise fixes — throughput unchanged.
- Cross-day bucket mismatch — may be benign if orphan reclaim runs.

### T0 optional — boot thread census

After deploy, confirm session background roles are present (supervisor PID) and hot-path pools use spawn workers — stall exit **124** applies to **process pools**, not janitor threads. See [SYNC_TIMEDB_PARALLELISM.md](SYNC_TIMEDB_PARALLELISM.md).

```bash
# Replace compose driver and service name as on site.
COMPOSE='podman-compose'
SVC='pipeline'
$COMPOSE exec -T "$SVC" sh -lc '
SUP=$(pgrep -f "[s]ync_timedb" | head -1)
echo supervisor_pid=$SUP
ps -T -p "$SUP" -o pid,tid,comm,args 2>/dev/null | grep -E "archive-janitor|startup-|day-raw|sync_timedb" | head -20
pgrep -af "sync_timedb.*worker:" | head -10
'
```

**Expect:** one `archive-janitor` thread when janitor enabled; optional `startup-*` threads during boot preflights; separate `[worker:ingest-pool]` / `[worker:archive-pool]` / `[worker:populate-pool]` PIDs (spawn), not threads for parse/append/populate hot path.

**Defunct children (T0/T1):** under the real supervisor PID, `ps` must **not** accumulate `[sync_timedb.py ] <defunct>` zombies. Do not identify the supervisor with `pgrep -n`: forked `multiprocessing.Manager` helpers can inherit the `sync_timedb.py [main]` title, while the resource tracker appears as `python3 -B -c from multiprocessing.resource_tracker …`. The supervisor is the `[main]` process whose direct-child list contains those helpers, pool workers, or zombies; verify PID/PPID for every `[main]` candidate.

Production Podman census (run from the checkout containing the deployment):

```bash
podman exec --env HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini hpcperfstats_pipeline_1 \
  su hpcperfstats -c 'sh -lc "
for p in \$(pgrep -f \"sync_timedb.py \\[main\\]\" || true); do
  echo ===PID=\$p===
  ps -o pid,ppid,stat,etime,rss,cmd -p \"\$p\" 2>/dev/null || true
  echo ---children---
  ps -o pid,ppid,stat,etime,cmd --ppid \"\$p\" 2>/dev/null || true
  echo ---zombies---
  ps -o pid,ppid,stat,etime,cmd --ppid \"\$p\" 2>/dev/null | grep -E \"[[:space:]]Z\" || true
done
"'
```

Chunk-boundary reap + populate-pool `reap_and_restart` should clear recycled children; mid-chunk throttled reap (stall poll / idle backstop) must also emit `Zombie child reap` or `Pool worker reap`. Archive append workers always use `maxtasksperchild=1`: during a long `archive_finalize` wait, supervisor child hygiene runs at the throttled interval (at most 60s), then each successful slot runs an unthrottled `archive_finalize_slot` reap. Long **pending reconcile cap** windows (observed up to ~310s) also run throttled hygiene (`context=pending_reconcile`) at begin/end. **Empty-queue idle sleeps** (day-close poll / empty rescan / async-rescan wait) must invoke throttled hygiene via `sleep_until_shutdown(..., on_tick=…)` (`context=idle_day_close_poll` / `idle_empty_rescan` / `idle_rescan_inflight` / `idle_snapshot_failed`) so orphans cannot starve for the full 300s poll. Supervisor child hygiene is **fault-isolated per step** — a closed/foreign `Process.is_alive()` raise during pool reap must not skip zombie `waitpid` or the unreaped WARN. **Pass:** no direct-child zombie survives beyond one 60s finalize-reap interval while finalize is waiting; after slot completion, any result-delivery/worker-exit race is cleared by the immediate slot reap or the next chunk/poll/idle hygiene surface; unreaped zombies log `WARN`/`ERROR: unreaped zombie children … max_age_s=` (ERROR when age ≥ 60s). **Fail:** a zombie spans the next `chunk ingest summary` / `chunk_prewarm_elapsed_s`, remains more than 60s during archive finalize, survives the first hygiene surface after `archive_job_done` / slot finalize, or persists for hours with **on-disk** die-zombies fingerprint true (Branch R — see below). Grep pipeline logs for `archive_finalize_wait`, `archive_finalize_slot`, `pending_reconcile`, `idle_day_close_poll`, `Zombie child reap`, `WARN: unreaped zombie children`, `ERROR: unreaped zombie children`, `WARN: supervisor child hygiene step failed`, and `WARN: on_stall_poll failed`. If zombies persist with `exit_code=9` (SIGKILL) and no live `worker:populate-pool`, expect prewarm failures.

**Branch D — checkpoint deque mutation (exit status 1, hpcperfstats01 2026-07-26):** production signature is day-close handoff / pending reconcile light immediately followed by `RuntimeError: deque mutated during iteration` in `resolved_checkpoint_path_set` (`for entry in checkpoint_entries`) during `_finalize_archive_slot` → `_apply_archive_finalize_results_inner` → `_live_unprocessed_by_tar_for_reconcile`, then supervisord `exited: sync_timedb (exit status 1)`. Shared live `checkpoint_entries` deque was iterated on MainThread while day-close mutated it. **Pass (post-fix):** `resolved_checkpoint_path_set` / remove helpers iterate a snapshot (`checkpoint_entries_snapshot`); no `deque mutated during iteration` under concurrent day-close. **Fail:** uncaught deque RuntimeError killing the supervisor.

**Branch Z-H2 — hygiene present but starved (overnight dual-site 2026-07-26):** RC-JT / `_reap_supervisor_pool_children` fingerprint True, yet full-buffer hygiene signature count is **0** while multi-hour `STAT=Z` remain under live `[main]` (including post-restart supervisors). **Pass:** idle day-close polls emit hygiene contexts above; with zombies present, expect `Zombie child reap` and/or `unreaped zombie children` within 60s. **Fail:** fingerprint True + hour-scale zombies + absolute hygiene silence.

### T0 / T1 — Branch D deque exit + Branch Z-H2 idle hygiene (2026-07-26)

```bash
# T0 — deque mutation exit signature (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats logs pipeline 2>&1 | \
  grep -E 'deque mutated during iteration|exited: sync_timedb \(exit status 1\)|resolved_checkpoint_path_set' | tail -40

# T0 — hygiene scheduled / unreaped (count on full buffer; never grep|tail && chain)
docker compose -p hpcperfstats logs pipeline 2>&1 | \
  grep -cE 'Zombie child reap|unreaped zombie children|idle_day_close_poll|idle_empty_rescan|archive_finalize_wait|archive_finalize_slot' || true
docker compose -p hpcperfstats logs pipeline 2>&1 | \
  grep -E 'Zombie child reap|WARN: unreaped zombie children|ERROR: unreaped zombie children|idle_day_close_poll' | tail -40
```

**Fail (T0):** new `RuntimeError: deque mutated during iteration` / supervisord `exit status 1` on that signature; or on-disk hygiene fingerprint True + hour-scale `STAT=Z` under live `[main]` with hygiene signature **count 0**. Do **not** treat RC-JT `terminate_pool_bounded` / `reap_zombie_children_of_self` source fingerprint alone as proof hygiene is scheduled on idle ticks.

**Pass (T0):** no deque-mutation traceback since redeploy; with direct-child zombies present, hygiene lines (`Zombie child reap` and/or `unreaped zombie children`) appear within **60s**; empty-queue day-close polls may show `idle_day_close_poll` context.

**Pass (T1):** zombie `etime` collapses under the live supervisor (no multi-hour `STAT=Z` pile while fingerprint True); day-close handoff + archive finalize continue without supervisor death.

**Branch R residual (post die-zombies, 2026-07-25):** when the running image’s on-disk module fingerprints true for `archive_finalize_wait` / `archive_finalize_slot` / `on_stall_poll` but direct-child zombies under the real supervisor still show `etime` of hours, treat as residual unreaped orphans (not “undeployed only”). Confirm the **live process** was restarted after the file update (`inspect.getsource` proves disk, not loaded bytecode). Expect logs to show either successful `Zombie child reap` / age-bearing unreaped WARN/ERROR, or a named `supervisor child hygiene step failed` / `on_stall_poll failed` line — silence with multi-hour `STAT=Z` is itself a bug. Redeploy + restart remains required to load this residual fix; fingerprint alone does not clear already-unreaped zombies.

**Join-timeout teardown without `/proc` zombie reap (RC-JT, 2026-07-25):** production signature is `Pool close join timeout; terminating lingering_workers=…` → `Pool workers terminated context=pool` → `Pool worker reap context=pool` **without** a following `Zombie child reap`, while `ps --ppid <supervisor>` still shows multi-hour `STAT=Z` children. Pre-fix non-abandon `terminate_pool_bounded` only `waitpid`’d PIDs still on `pool._pool`; orphans outside that list stayed defunct under `[main]`. **Pass (post-fix):** the same teardown sequence must emit `Zombie child reap` (or leave no direct-child `STAT=Z`). **Fail:** join-timeout tear down that only logs `Pool worker reap` while zombies persist.

Deploy fingerprint (split Python vs shell — do **not** nest `python3 -c` inside `sh -lc`):

```bash
podman exec --env HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini hpcperfstats_pipeline_1 \
  su hpcperfstats -c 'python3 -c "
import inspect
from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.multiprocessing_pool_health import async_result_get_watch_pool
src = inspect.getsource(st)
print(\"has_archive_finalize_wait\", \"archive_finalize_wait\" in src)
print(\"has_archive_finalize_slot\", \"archive_finalize_slot\" in src)
print(\"has_on_stall_poll_finalize\", \"on_stall_poll=_archive_finalize_stall_poll_reap\" in src)
print(\"has_pending_reconcile_reap\", \"pending_reconcile\" in src and \"_maybe_reap_supervisor_pool_children_throttled\" in src)
print(\"has_hygiene_step_isolation\", \"supervisor child hygiene step failed\" in src)
print(\"async_result_get_watch_pool_params\", list(inspect.signature(async_result_get_watch_pool).parameters))
"'
```

```bash
podman exec hpcperfstats_pipeline_1 \
  su hpcperfstats -c 'sh -lc "
for p in \$(pgrep -f \"sync_timedb.py \\[main\\]\" || true); do
  echo ===PID=\$p===
  ps -o pid,ppid,stat,etime,rss,cmd -p \"\$p\" 2>/dev/null || true
  echo ---children---
  ps -o pid,ppid,stat,etime,cmd --ppid \"\$p\" 2>/dev/null || true
  echo ---zombies---
  ps -o pid,ppid,stat,etime,cmd --ppid \"\$p\" 2>/dev/null | grep -E \"[[:space:]]Z\" || true
done
"'
```

**False fatal exit 137 on maxtasksperchild recycle (T1):** during catch-up with fast `outcome=db_skip` lines, grep must show **`INFO: pool worker recycle in progress`** (and optional **`WARN: pool worker recycle slow`**) but **must not** show **`Pool worker exit: hard exit code=137`** with **`likely_cause=recycle`** while **`alive_workers`** shows replacements keeping pace (for example **15/16**, **19/22**, **23/24**, or **20/21** with `exitcode=0`). Pre-fix signature: **`grace_poll=1/2`**, **`grace_poll=2/2`**, then ERROR on a **third** dead PID. Post-fix (2026-07-08 hardening): tolerate healthy recycle when materialized workers are below **`sync_ingest_pool_processes`** / process cap during spawn; consecutive different dead PIDs at healthy alive counts are tolerated; fatal recycle-shaped exits log **`likely_cause=recycle_stuck`** (not bare **`recycle`**) plus **`ERROR: pool worker recycle gate rejected:`** with `alive`, `expected_total`, `materialized`, `gap`. **INI:** shipped default is **`sync_ingest_pool_maxtasksperchild=0`** (cooperative retire) — the bare **`maxtasksperchild`** key under `[PIPELINE]` is **not** read. **Archive pool** always recycles after one append task even when ingest uses **`sync_ingest_pool_maxtasksperchild=0`**. Ingest-only supervisor cooperative retire (`failure_reap` / `rss_reap` when **`maxtasksperchild=0`**) uses the same healthy-recycle contract (SIGTERM exitcode **-15** tracked per pool). Set **`maxtasks=1`** only when you want stdlib recycle after every file.

```bash
cd HPCPerfStats
docker compose logs pipeline --since 6h 2>&1 | grep -E 'pool worker recycle in progress|pool worker recycle gate rejected|Pool worker exit: hard exit code=137|likely_cause=recycle' | tail -50
```

**Expect:** `INFO: pool worker recycle in progress` during fast `db_skip` catch-up; **no** `hard exit code=137` with bare `likely_cause=recycle` at healthy alive ratios; if recycle replacement truly stalls, `recycle_stuck` + gate-rejected line before fatal.

**Idle-pool ghost / exit 124 after full redispatch thrash (T1, 2026-07-08 / 5th recurrence 2026-07-09):** during cooperative recycle + fast `db_skip`, pre-fix signatures included three **`INFO: pool imap idle reconcile redispatch round=… redispatched_n=N pending_async_n=N`** with identical `pending_sample`, workers `futex_wait_queue`, then soft hang or **`hard exit code=124`**. **5th-recurrence hang (pre–abandon-pool):** `pool_recover` → `skip_probe` (`duplicate_pending_n=0`) → `terminate begin` → `workers_before=N` — **no** `terminate outcome` / `pool_recover done` (MainThread stuck in stdlib `Pool.terminate` / `_help_stuff_finish`); live **`ingest_workers` ≈ 2× process_cap** after proactive swap without killing the old pool. **Post-fix (abandon-pool + recover wall + PPID census, 2026-07-16):** after full-redispatch thrash, expect **`INFO: pool imap idle reconcile pool_recover`** then **`pool_recover skip_probe begin`**, **`pool_recover terminate workers_before=…`**, **`pool_recover ppid_census kill`** (or reclaim), **`pool_recover terminate outcome=abandoned`** (or hard exit **124** within recover wall — never a silent multi-minute gap after `workers_before=`), **`pool_recover terminate elapsed_s=…`**, **`pool_recover respawn dispatch_probe ok`**, **`pool_recover resubmit n=…`**, and **`INFO: pool imap idle reconcile pool_recover done`** with resumed **`ingest file path=`**. **`dispatch_probe failed … err=`** with empty `err=` is **`TimeoutError`** (logs now include the type name). Preventive: path size alone never retires (no cooperative giant recycle); **no supervisor retire on `outcome=db_skip`** except RSS; live **`pending_inflight`**, refuse retire/swap while replacement **`gap>0`**, proactive swap **abandons** old workers **including orphans not in `pool._pool`**. If recover fails/times out, fatal must include **`likely_cause=idle_pool_taskqueue_dead`**. Optional WARN **`retire skipped missing worker_pid … likely_cause=meta_or_registry_gap`** must stay WARN-only. Distinguish from exit **137** recycle troubleshooting above. **Do not** treat multi-hour supervisord restart as the fix for 2×/N× `[worker:ingest-pool]` children — expect in-process reclaim (`child_ingest` ≤ configured `ingest_pool_processes`).

```bash
docker compose logs pipeline 2>&1 | grep -E 'idle reconcile redispatch|redispatch skipped|idle reconcile pool_recover|pool_recover skip_probe|pool_recover terminate (workers_before|outcome|elapsed_s)|pool_recover ppid_census|child_ingest over cap|pool_recover respawn dispatch_probe|pool_recover resubmit|ingest pool replacement lagging|retire deferred|proactive swap|outcome=abandoned|duplicate dispatch suppressed|duplicate_pending_n|idle_pool_ghost_inflight|idle_pool_taskqueue_dead|retire skipped missing worker_pid|hard exit code=124' | tail -80
```

**Expect:** redispatch → `pool_recover` with **`pool_recover done`** and **`terminate outcome=abandoned`** (or **124** `idle_pool_taskqueue_dead` within recover wall); **never** hang after `workers_before=` with no outcome; **`dispatch_probe ok`** and resumed ingest on success; **no** `likely_cause=unknown` on ghost fatals; after swap/recover, **`child_ingest` equals INI `ingest_pool_processes`** (see census below).

**Post-recover sticky-attempted ghost (T1, 2026-07-19, hpcperfstats03):** pre-fix sequence was **`redispatch round=1/3`** → successful **`pool_recover done collected_n=0 pending_async_n=N`** (abandon + `dispatch_probe ok`) → **second** **`redispatch round=1/3`** → **`idle_pool_ghost_inflight` / `idle_pool_taskqueue_dead`** → hard exit **124** with trailing **`likely_cause=unknown`**. Root cause: successful recover cleared thrash/rounds but left **`pool_recover_attempted=True`**, so the ghost gate treated thrash+attempted as immediate fatal at round **1** without a second recover. **Post-fix:** successful recover **resets** `pool_recover_attempted` and increments `recover_count` (cap **`IDLE_POOL_RECOVER_MAX=3`**); a later thrash may recover again; hard exit must **not** print `likely_cause=unknown` when `exc.likely_cause=idle_pool_taskqueue_dead`.

```bash
# T1 — post-recover second thrash / recover cap (full pipeline log; never --tail before grep)
docker compose logs pipeline 2>&1 | grep -E 'idle reconcile pool_recover|pool_recover done|pool_recover cap exceeded|path soft-fail|idle_pool_unhealed_after_recover|redispatch round=|idle_pool_ghost_inflight|hard exit code=124|likely_cause=unknown' | tail -80
```

### T0 / T1 — unhealed recover / single stuck skip_no path (2026-07-20)

**Failure signature (pre-fix):** one unfinished path (often ~10–20 MiB, `head_ingested=False` → `skip_no`) stays on ghost **`dispatch:…`** for hours while peers show live `parse:*` / `db_write`. Idle reconcile runs **`pool_recover`** with **`dispatch_probe ok`**, resubmits the **same** pending, then burns **`recover_count=1/3…3/3`** → **`pool_recover cap exceeded`** → exit **124** `idle_pool_taskqueue_dead` even though the pool is healthy.

**Pass (T0):** after **`IDLE_POOL_UNHEALED_RECOVER_MAX`** (default **3**) identical probe-ok recovers, expect **`ERROR: … path soft-fail reason=idle_pool_unhealed_after_recover`** (escalate=`unhealed_streak` or `recover_cap`); the stuck **full normpath** is removed from `pending_async`; imap / `chunk ingest summary` continues for peers. **Do not** treat a lone stuck path as process-level taskqueue death when probe succeeded.

**Pass (T1):** **`pool_recover cap exceeded`** with **`action=path_soft_fail`** (or soft-fail before the fourth recover) — **not** hard exit **124** solely for identical `skip_no` pending. Exit **124** remains for recover wall hang, `dispatch_probe` failure, invalid recover return, or empty soft-fail at cap (true taskqueue death). Optional: **`pool_recover skipped reason=registry_redis_wait`** when a pending path has live `archive_member_lookup` / `redis_wait` (even if `ingest_tar_hot` cleared).

```bash
# T0 / T1 — unhealed recover quarantine vs recover-cap exit 124 (full pipeline log; never --tail before grep)
docker compose logs pipeline 2>&1 | grep -E 'pool_recover done|unhealed_streak=|path soft-fail|idle_pool_unhealed_after_recover|pool_recover cap exceeded|registry_redis_wait|hard exit code=124|idle_pool_taskqueue_dead' | tail -80
```

### T0 / T1 — populate_wait idle redispatch skip (2026-07-17)

**Failure signature (pre-fix):** `INFO: pool imap idle reconcile pool_recover skipped reason=populate_wait…` (or `populate_enqueue`) correctly skips recover, but the same idle window still emits **`redispatch round=1/3…3/3`** with identical `pending_sample` / `redispatched_n=pending_async_n` while workers sit in `futex_wait_queue` during Redis populate wait.

**Pass (T0):** while populate wait is live, expect **`pool_recover skipped reason=populate_wait…`** and **`redispatch skipped reason=populate_wait…`** (or `populate_enqueue` / `chunk_prewarm`); **no** `redispatch round=` for that pending sample; orphan collect / later `chunk ingest summary` may continue when workers finish waiting.

**Pass (T1):** no thrash into `pool_recover` / exit **124** `idle_pool_taskqueue_dead` solely because populate wait looked wchan-idle; genuine dead-taskqueue thrash (no skip reason) still recovers via abandon-pool as above.

```bash
# T0 — populate_wait skip vs redispatch thrash (full pipeline log; never --tail before grep)
docker compose logs pipeline 2>&1 | grep -E 'pool_recover skipped|redispatch skipped|redispatch round=|idle_pool_taskqueue_dead' | tail -80
```

### T1 verify — CLI ``current`` June populate vs July gate (two-queue contention, 2026-07-17)

**Failure signature (pre-fix, operator-verified on hpcperfstats03):** under CLI ``current``, July ingest gate is healthy (`youngest_tar=2026-07-17.tar`, **`chunk_pad_n=0`**, `chunk_day_histogram` / `chunk prewarm … days=['2026-07-17']` only) while populate-pool still logs June `populate_source_decision` interleaved with MainThread **`day_close enqueue … reason=day_ingest_complete:idle_finalize`** / janitor `day_close` for early June. Idle reconcile may show **`populate_wait day=2026-06-…`** and falsely skip recover/redispatch for July pending (global `ingest_tar_hot` scan). **Not** cross-day chunk pad (pad falsified when `chunk_pad_n=0`).

**Pass (T1):**
- Idle skip only when **pending-path** calendar days are populate-hot (`pool_recover skipped` / `redispatch skipped` reason names the pending day, not an unrelated June day while July paths are pending).
- Populate-pool prefers ingest-hot days (`chunk_prewarm` / `populate_wait` over bare `populate_enqueue`) so July chunk prewarm is not indefinitely blocked behind cold June day-close populate on the shared FIFO queue.
- June day-close populate may still appear when no ingest-hot jobs are waiting; July gate/prewarm stay July-only when `chunk_pad_n=0`.

```bash
# T1 — June populate vs July gate / false populate_wait skip (full pipeline log; never --tail before grep)
docker compose logs pipeline 2>&1 | grep -E 'youngest_day_chunk_gate_pad|chunk_pad_n=|chunk prewarm|populate_source_decision|day_close enqueue|idle_finalize|pool_recover skipped|redispatch skipped|populate_wait day=' | tail -120
```

### T0 / T1 — ingest-pool orphan census (2× / N× after swap, 2026-07-16)

**Failure signature (pre-fix):** INI `ingest_pool_processes=N` but `ps` under main shows **`child_ingest` ≫ N** (often ~2× then grows: 48 → 71+) after **`dispatch_probe failed … err=`** (empty = `TimeoutError`) → **`proactive swap`** / idle `outcome=abandoned` that SIGKILL'd only `pool._pool`. Orphans sit in `queues.get` while a thin live cohort does work.

**Acceptance (post-deploy, no multi-hour restart required):** `child_ingest == ingest_pool_processes` (and ≤ `sync_ingest_pool_processes`). Reclaim may log **`ERROR: ingest pool child_ingest over cap`** then cull; subsequent census must match configured size.

```bash
# T0 — INI + PPID census (compose cwd = git checkout with docker-compose.yaml)
docker compose exec -T pipeline sh -lc '
python3 - <<'"'"'PY'"'"'
from hpcperfstats.dbload.lib import conf_parser as c
print("ingest_pool_processes", c.get_sync_ingest_pool_processes())
print("sync_ingest_pool_processes", c.get_sync_ingest_pool_processes())
PY
MAIN=$(pgrep -fo "[s]ync_timedb.py \[main\]" || pgrep -fo "[s]ync_timedb" | head -1)
echo "MAIN=$MAIN"
# MAIN= is the supervisor PID (not child count)
ps -o pid=,ppid=,args= -ax 2>/dev/null | awk -v m="$MAIN" '\''$2==m && /\[worker:ingest-pool\]/ {n++} END{print "child_ingest=" (n+0)}'\''
'
# T0/T1 — probe / swap / reclaim
docker compose logs pipeline 2>&1 | grep -E 'dispatch_probe failed|proactive swap|ppid_census|child_ingest over cap|outcome=abandoned' | tail -40
```

**Pass (T0):** `child_ingest` equals configured processes shortly after any `proactive swap` / idle recover. **Pass (T1):** census stays ≤ configured across further per-file timeouts; thin `in_flight_n` under long budgets is a separate utilization question, not proof of orphans.

### T0 / T1 — Manager `DB lock wait` vs Postgres + ingest timing tokens (2026-08-27)

**Failure signature (pre-fix):** multi-hour `DB lock wait proc|host batch file=… wait=…` immediately before `ERROR: ingest per-file timeout` and coordinator `queue_orchestrator ingest fail … err=TimeoutError`, while `ingest_catchup` queued depth rises under a saturated pool. Operators may misread `DB lock wait` as Postgres `lock_timeout`.

**What the token means:** `DB lock wait` is **multiprocessing Manager write-shard `acquire` wait** (`sync_write_lock_shards`), **not** Postgres. Per-file SIGALRM / monotonic deadline now **suspends/extends during acquire** (same class as Redis populate wait); time **holding** the shard for ORM/`bulk_create` remains charged.

**Acceptance (post-deploy):**

- Long `DB lock wait … wait=` lines may still appear under shard contention, but they must **not** alone exhaust the per-file budget into false timeouts.
- Packed timeouts soft-requeue (`outcome=timeout`) — prefer **no** `queue_orchestrator ingest fail … TimeoutError` for per-file budget expiry (coordinator may log `ingest timeout` then soft-requeue).
- Success / outcome lines include timing tokens: `parse_elapsed_s=`, `db_shard_lock_s=` (sum of acquire waits), `postgres_s=` (sum of hold/`bulk_create`), `elapsed_s=` (total wall). Residual wall time may remain (populate wait, duplicate scan).
- One INFO `file_complete_ingest_mark recorded` per successful path (worker); coordinator persists without a second INFO.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'DB lock wait|ingest per-file timeout|ingest timeout identity=|ingest fail .*TimeoutError|file_complete_ingest_mark recorded|db_shard_lock_s=|postgres_s=|parse_elapsed_s=' | tail -80
```

### T0 / T1 — sticky ingest 0/N + bare TimeoutError thrash (H7) + idle stall (2026-08-29)

**Failure signature (pre-fix):** status sticky `ingest_hot=0/N` with deep Redis ingest ZSET; mass `queue_orchestrator ingest timeout … elapsed_s=0.0 stage=unknown err=TimeoutError` while workers still log `ingest file path=`. Optional `*.fnctl.lock` identity on ZRANGE.

**Acceptance (post-deploy):**

- Bare drain `TimeoutError` must **not** soft-requeue or clear local inflight (no thrash sticky 0/N).
- Soft-requeue uses packed/rich timeout or `stage=idle_stall`; coordinator submit-age watchdog is **retired** (no fallback).
- Image defaults: `sync_ingest_per_file_timeout_s` always **0** (wall soft-kill **deleted**, cannot re-arm), `sync_ingest_stall_idle_s=1800`, `sync_pool_stall_abort_after_timeouts=0`.
- Discover/fill do not keep `*.fnctl.lock` on the ingest ZSET.
- Progress SOP: `progress stage=… advancing=true|false idle_s=… metric=bytes|lines|members`; tar append idle stall → `tar append idle stall`.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'elapsed_s=0\.0 stage=unknown|idle_stall|advancing=false|tar append idle stall|ingest fill empty deep_queue|queue_orchestrator ingest timeout|fnctl\.lock' | tail -80
```

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"per_file_timeout_s\", cfg.get_sync_ingest_per_file_timeout_s())
print(\"stall_idle_s\", cfg.get_sync_ingest_stall_idle_s())
print(\"stall_abort\", cfg.get_sync_pool_stall_abort_after_timeouts())
print(\"max_s\", cfg.get_sync_ingest_per_file_timeout_max_s())
"'
```

### T0 / T1 — ingest per-file timeout floor 3600 (throughput near-miss, 2026-07)

**Archaeology (wall B deleted 2026-08-29):** internal per-file SIGALRM / size-proportional soft-kill is **removed** — not demoted. Grep `advancing=false` / `idle_stall` / `tar append idle stall` instead of wall-timeout primary signatures. Postgres `statement_timeout` remains the external ceiling.

**Failure signature (pre-fix / old floor 900):** under `ingest_pool_processes=32`, many paths die with `ERROR: ingest per-file timeout … stage=ingest` / `outcome=timeout` at **elapsed == size-scaled budget** (~925–1654s for small/mid files). Slow cohort can still finish on retry (e.g. ~14 MiB in **2304.7s**). Not an idle/stall class if `remaining` advances.

**Acceptance (when wall floor re-enabled ≥3600):**

- `sync_ingest_per_file_timeout_s` reads the configured floor inside the pipeline image.
- Timeout rate drops vs pre-deploy; slow successes with `elapsed_s` in the 1800–3600 band complete as `outcome=ingested`.
- Per-file **`ingest file path=… outcome=… size_bytes=… timeout_s=…`** (and timing tokens) is the SOP report — large-file budgets are not logged as a separate pre-work warning.
- Soft-requeue timeouts also log `queue_orchestrator ingest timeout … size_bytes=… timeout_s=… stage=…`.
- `remaining=` still trends down; no `ERROR: Pool imap stalled` / exit **124** from this alone.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"per_file_timeout_s\", cfg.get_sync_ingest_per_file_timeout_s())
print(\"per_mib\", cfg.get_sync_ingest_per_file_timeout_s_per_mib())
print(\"max_s\", cfg.get_sync_ingest_per_file_timeout_max_s())
"'

docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'ingest file path=|queue_orchestrator ingest timeout|outcome=timeout|outcome=ingested|ERROR: ingest per-file timeout|remaining=' | tail -80
```

### T0 / T1 — post_retire timeout→quarantine thrash → exit 124 (2026-07-17)

**Failure signature (pre-fix):** under `maxtasksperchild=0` + `recycle_on_failure=True`, a timeout wave logs **`Quarantined unparsable raw`** / **`outcome=quarantine … fail_reason=ingest per-file timeout`** (file moved to DLO; manifest `reason=ingest_parse_failed` + `error_detail` containing the timeout string) then every retire runs **`post_retire_maintenance`**: **`dispatch_probe failed … TimeoutError`** on a busy pool, **`child_ingest over cap alive≫expected`** with **`Pool terminate SIGKILL`**, eventually **`idle_pool_ghost_inflight` / `idle_pool_taskqueue_dead`** → **`hard exit code=124`**.

**Acceptance (post-deploy):**

- **No** `outcome=quarantine` whose `fail_reason` contains `ingest per-file timeout` (timeouts must stay `outcome=timeout` / `ingest_ok=no` with the raw path still under `archive_dir`).
- **No** repeating `dispatch_probe failed … context=post_retire_maintenance` every ~10s while the chunk is busy; expect **`skip_probe reason=workers_busy`** and/or **`post_retire_maintenance coalesced reason=workers_busy`**.
- `child_ingest` returns to configured `ingest_pool_processes` without SIGKILL thrash of registered keep; no hard exit **124** from this cascade.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'dispatch_probe failed|skip_probe reason=workers_busy|post_retire_maintenance coalesced|child_ingest over cap|outcome=quarantine|fail_reason=ingest per-file timeout|idle_pool_ghost_inflight|idle_pool_taskqueue_dead|hard exit code=124|Pool terminate SIGKILL' | tail -80
```

**Duplicate dispatch suppressed flood (T1, post-fix 2026-07-09):** dense `WARN: pool imap duplicate dispatch suppressed path=<timestamp>` lines (basename-only) during fast `db_skip` usually meant **non-prefix chunk accounting** — `pending[len(chunk):]` re-offered in-flight paths after `select_ingest_chunk_paths` (oldest-tar / handoff). **Post-fix:** pending advance and giant-supplement tail use **`pending_minus_chunk`** (normpath set-difference); chunk paths are deduped before imap; WARN shows **`path=host/basename`** (and `suppressed_n=` on first hit). Steady-state expect **near-zero** duplicate-suppressed WARNs; shared timestamp basenames across hosts are distinct (`c637-051/1780788583` vs `c637-062/1780788583`). Occasional single WARNs during idle reconcile redispatch remain benign.

**Worker memory soak (T1/T2, default `maxtasksperchild=0`):** enable **`sync_ingest_worker_memory_telemetry=yes`** and grep **`sync_timedb worker_memory: event=batch_summary`**. Anti-collapse: **`tasks_on_worker_p50≥10`** on small-file batches; **`keep_worker`** dominates **`retires_total`**; **`failure_reap_pct`** / **`rss_reap_pct`** low outside RSS pressure. Supervisor retire kinds are **failure + RSS only** (no `giant_reap`). Example:

```bash
cd HPCPerfStats
docker compose logs pipeline --since 24h 2>&1 | grep -F 'sync_timedb worker_memory: event=batch_summary' | tail -30
docker compose logs pipeline --since 24h 2>&1 | grep -F 'sync_timedb worker_memory: event=batch_summary' | grep -oE '(failure|rss)_reap_pct=[^ ]+' | tail -20
```

**Archive populate lock timeout / F6 self-defer (T0/T1 post-fix):** fatal `Timed out waiting for archive members populate lock` during archive append when **`archive_append_inflight`** is set **before** pre-append lookup. **Pre-fix signature:** tail of pipeline logs shows **only** `populate: defer tar scan day=YYYY-MM-DD reason=archive_append_inflight` from **archive-pool** with **no** `archive_job_begin` / `archive_job_done` for that day; may end in duplicate populate lock timeout lines. Redis census post-crash may show `complete=0`, `hlen=0`, `lock_value=None`, `append_inflight=False` (orphan incomplete). **Post-fix pass:** same day shows `archive_job_begin` then `archive_job_done` / `archive_finalize`; **no** fatal populate lock timeout; janitor `Archive janitor tick done` clears `in_flight` without multi-hour `duration_s` stalls tied to stuck finalize.

```bash
docker compose logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|Timed out waiting for archive members populate lock|archive_job_begin|archive_job_done|archive_finalize|populate: defer tar scan|lock acquire timeout|janitor: tick' | \
  tail -60
```

Replace `YYYY-MM-DD` with the failing calendar day. Expect **`lock acquire timeout`** diagnostics (with `lock_owner_pid`, `append_inflight`, `pre_append_exempt`) only when a **live** external lock holder blocks acquire — not during normal archive append with inflight-first ordering.

**Bucket E — No daily archive (T1/T2 post-fix):** thousands of alternating `archive members populate incomplete after lock release` / `clearing stale incomplete archive members Redis` for hash suffix **`…:YYYY-MM-DD:none:none:none:none`** (`hlen=0 complete=- lock=0`) from **`[sync_timedb:worker:ingest-pool]`**, ending in **`ERROR: Timed out waiting for archive members populate (max_seconds=7200)`** when **no** `.tar`/`.tar.zst`/`.tar.gz` exists on disk for that day.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cp
import os
day = \"YYYY-MM-DD\"
dad = cp.get_daily_archive_dir_path()
for name in (\"%s.tar\" % day, \"%s.tar.zst\" % day, \"%s.tar.gz\" % day):
    p = os.path.join(dad, name)
    print(name, \"exists=\" + str(os.path.isfile(p)), \"size=\" + (str(os.path.getsize(p)) if os.path.isfile(p) else \"-\"))
"'
```

**Pre-fix:** populate wait loop with no on-disk source. **Post-fix pass:** chunk prewarm shows **`no_daily_archive`** for that day; ingest resumes (`Begining Chunk` / `chunk ingest summary`); **no** 7200s populate timeout storm for days with no archive. Grep:

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|no_daily_archive|populate incomplete|Timed out waiting for archive members populate|Begining Chunk|chunk prewarm days=' | \
  tail -40
```

**Bucket E1 — Sealed-only dead populate-lock owner → orphan wipe → stale-clear stampede (T0/T1, 2026-06-02 class):** hash suffix **`…:YYYY-MM-DD:sealed_mtime:sealed_size:none:none`** (sealed present, **no** sibling `.tar`). Pre-fix sequence: workers in `populate_queue_wait` / `archive_member_lookup:redis_wait`; idle reconcile correctly skips recover (`reason=populate_wait`); then **`populate lock owner pid=… dead; releasing stale lock`** → **`clearing orphan incomplete … hlen≈N complete=0`** (partial sealed map wiped) → flood of **`clearing stale incomplete … hlen=0 complete=-`** from many ingest-pool workers + main (process-local log gate cannot coalesce).

**Post-fix pass:** at most **one** orphan clear WARN; **at most one** stale-incomplete WARN per day within Redis NX TTL (~300s); **one** recovery re-enqueue (peers wait only); noop clears are silent; populate recovers to `complete=1` without WARN stampede. Redis census keys (correct names):

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec redis sh -lc 'echo "=== scan"; redis-cli --scan --pattern "*archive_members:hash:v1:YYYY-MM-DD*" | head -20; echo "=== degraded"; redis-cli GET "hpcperfstats:sync_timedb:archive_populate_degraded:v1:YYYY-MM-DD"; echo "=== day_skip"; redis-cli GET "hpcperfstats:sync_timedb:archive_day_ingest_skip:v1:YYYY-MM-DD"'
```

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|lock owner pid=|clearing orphan incomplete|clearing stale incomplete|populate incomplete after lock release|pool imap idle reconcile' | \
  grep -v 'suppressed_n=' | head -80
```

**Bucket E2 — Dirty-tar populate EOF thrash + self-hot + exit 124 (T0/T1, 2026-06-07 class):** pre-fix loop shows `populate_source_decision … dirty=True sealed_exists=True use_tar=True` then `transient tar populate EOF during hot/append` while Redis census has **`tar_hot=True`** / **`append_inflight=False`** (waiter self-hot alone), orphan clear `hlen≈6500`, stale clear `hlen=0`, forever retry; workers wchan-idle in populate wait → **`pool_recover exceeded wall_s=30.0`** → exit **124** `idle_pool_taskqueue_dead`. Candidate counters `unprocessed_cross_day_n` / `processed_cross_day_n` are **diagnostic only** (misbucket census) — they do **not** set `waiting_on_ingest` by themselves. Flood of `Unable to find first timestamp in N path(s)` after day-close delete is rate-limited/summarized (not the crash driver).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
import os
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import is_daily_tar_sealed_dirty
from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    archive_append_inflight_for_day, ingest_tar_hot_for_day, ingest_tar_hot_reason_for_day,
)
ad=cfg.get_archive_dir_path(); dd=cfg.get_daily_archive_dir_path()
day=\"YYYY-MM-DD\"
tar=os.path.join(dd, day+\".tar\"); zst=os.path.join(dd, day+\".tar.zst\")
print(\"dirty\", is_daily_tar_sealed_dirty(tar, zst, \"\") if os.path.isfile(tar) else \"n/a\")
print(\"append_inflight\", archive_append_inflight_for_day(day))
print(\"tar_hot\", ingest_tar_hot_for_day(day), \"reason\", ingest_tar_hot_reason_for_day(day))
print(\"tar_size\", os.path.getsize(tar) if os.path.isfile(tar) else \"-\")
print(\"zst_size\", os.path.getsize(zst) if os.path.isfile(zst) else \"-\")
"'
```

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|populate_source_decision|prefer sealed fallback|sealed fallback after dirty-tar|transient tar populate EOF|clearing orphan incomplete|pool_recover skipped|redispatch skipped|redispatch round=|pool_recover exceeded wall|idle_pool_taskqueue_dead|Unable to find first timestamp' | \
  tail -80
```

**Post-fix pass:** no forever `transient tar populate EOF during hot/append` with only `populate_wait` hot; expect **`prefer sealed fallback`** / **`populate sealed fallback after dirty-tar EOF`** then `populate_source=sealed` (or warm Redis); **no** orphan `hlen≈6500` clears after mid-scan fail; idle reconcile may log **`pool_recover skipped reason=populate_wait…`** and **`redispatch skipped reason=populate_wait…`** instead of **`redispatch round=`** / exit **124** while wait is live.
**Populate incomplete after lock release (tar exists):** grep for `Archive members populate incomplete after lock release`. Error key suffix `none:none:<tar_mtime>:<tar_size>` with concurrent `archive_job_done` / `redis_merge_warm` on the same day usually means **tar-identity drift** (waiter on pre-append fingerprint, merge on post-append). Post-fix waiters re-resolve identity and re-enqueue within `populate_max_seconds` rather than immediate `sys.exit(1)`.

**Empty recover during live append (RC-ER, 2026-07-25):** pre-fix signature is `ERROR: … (empty recover bound)` then `ERROR: archive members populate stalled or timed out` while the same day still logs `populate: defer … reason=archive_append_inflight` / `populate_wait identity_drift` / `archive_job_duty … appended=`. That fatal path `sys.exit(1)`’d a healthy supervisor into join-timeout pool teardown. **Pass:** identity drift mid-append may INFO-churn and may log `populate empty recover deferred … reason=archive_append_inflight|populate_lock|ingest_tar_hot_*|populate_source_within_max` **at most once per ~120s per day cluster-wide** (optional `suppressed_n=` on resume — not a continuous stream); waiter must **not** produce `empty recover bound` / populate-stalled fatal while any RC-ER defer applies (append inflight, alive populate lock, populate-class hot, or on-disk source exists with wait **&lt; `populate_max_seconds`**). **Fail-closed preserved:** three empty recovers with **no** populate source, **or** source exists but **`populate_max_seconds`** exhausted, still raise `empty recover bound` (hpcperfstats03 June-02 ~989GB tar census: append/hot/lock idle at fatal — defer must include **`populate_source_within_max`**).

**Transient fnctl read-lock timeout (T1):** grep for `transient fnctl read lock timeout during tar populate` and `transient fnctl during archive members prewarm`. **Healthy:** populate waits on fnctl (up to **`populate_max_seconds`**) then `populate_source=tar` when `.tar` exists; occasional WARNING + `populate incomplete after lock release; recovering` or `chunk prewarm days=...:populate_recovering:tar_populated` — supervisor must **not** restart (`L2 contract failed` absent or rare). **Unhealthy:** repeated `ERROR: archive members Redis L2 contract failed` with supervisor restart loop on the same calendar day. When **`.tar` is present**, expect **`populate_source=tar`** not sealed; sealed populate is normal only after tar-drop (`archive_keep_uncompressed_tar=no`).

**Populate-pool unavailable / refuse sealed stream (T1 — exit status 1 class):** grep for `populate-pool unavailable`, `refusing sealed stream`, `not an immediate L2 fatal`, and `archive members Redis L2 contract failed`.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'populate-pool unavailable|refusing sealed stream|not an immediate L2 fatal|L2 contract failed|populate-pool worker restarted|chunk prewarm|exit status' | \
  tail -80
```

**Healthy (post-fix):** cross-day cold Redis miss from ingest-pool **enqueues** populate work (no sealed stream on ingest-pool); MainThread may log WARNING + ensure/restart populate-pool; prewarm may show `populate_recovering`; supervisor must **not** `sys.exit(1)` / supervisord `exit status 1` solely from refuse-stream. **Unhealthy (pre-fix signature):** `ERROR: populate-pool unavailable; refusing sealed stream on ingest-pool for …/YYYY-MM-DD.tar.zst` immediately followed by `ERROR: archive members Redis L2 contract failed` and supervisor restart — often on a calendar day **outside** the current chunk day set (cross-day handoff).

**Startup heavy-pass classify once (T0/T1 perf):** after boot, `janitor: heavy maintenance sub_phases reason=startup` should not show near-equal multi-thousand-second `candidate_report_s` **and** `scheduled_submit_s` from double `classify_day_close_candidates` (pre-fix ~2× ~1300s). Post-fix shares one classify for report+discover.

**Ingest-wins janitor lock priority (T1):**

```bash
docker compose logs pipeline --since 24h 2>&1 | grep -E 'day_close defer|day_close yield|yield signal|populate: wait daily_tar_restore|daily_tar_restore begin|daily_tar_restore end|archive decompress restore begin|filesystem_complete|tar drop deferred|defer_cap_exceeded' | tail -60
```

**Healthy:** `janitor: day_close defer … reason=populate_active|ingest_tar_hot|daily_tar_restore|write_lock_contended`; `janitor: day_close yield … reason=chunk_prewarm|ingest_tar_hot` during dedupe/seal overlap; `populate: wait daily_tar_restore day=…` then successful populate; `archive: daily_tar_restore begin|end reason=missing_tar|corrupt_tar`; gated prewarm shows **`archive decompress restore begin`** for the oldest incomplete day before long decompress; sealed-only finished days do **not** storm `discover_ready_*` / `zstd -t` after persistence reset; tar-drop after delete-path unlink completes without false **`waiting_on_ingest`** when `handoff_paths=0`.

**`write_lock_contended` vs orphan `*.fnctl.lock` (T0/T1):** Sidecar files under `daily_archive` are **not** proof of a held flock. `write_lock_contended` means exclusive `try_file_write_lock` on the daily `.tar` failed (live SH/EX holder). Prefer an in-container `try_file_write_lock` probe (and `lsof`); **`fuser` is often absent** in the pipeline image (`which fuser` → empty). Read-lock leave-behinds and sealed-stream orphans are cleaned by debt-day tick + non-startup heavy orphan passes — do **not** treat `ls *.fnctl.lock` alone as a stall.

```bash
# T0 — flock probe vs orphan sidecar (INI paths; prefer try_file_write_lock over fuser)
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.file_locking import try_file_write_lock
import os, shutil
d = cfg.get_daily_archive_dir_path()
print(\"fuser\", shutil.which(\"fuser\"), \"lsof\", shutil.which(\"lsof\"))
for day in (\"YYYY-MM-DD\",):
  tar = os.path.join(d, day + \".tar\")
  lock = tar + \".fnctl.lock\"
  print(\"probe\", tar, \"lock_exists_before\", os.path.isfile(lock))
  try:
    with try_file_write_lock(tar):
      print(\"try_file_write_lock\", day, \"OK_uncontended\")
  except Exception as e:
    print(\"try_file_write_lock\", day, type(e).__name__, str(e)[:120])
  print(\"lock_exists_after\", day, os.path.isfile(lock))
"'
```

```bash
# T0 — day_close defer / lock_cleanup greps (full pipeline log; never --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | grep -E 'janitor: day_close defer.*write_lock_contended|janitor: lock_cleanup|day_close seal start' | tail -40
```

**Unhealthy:** populate waits full **`populate_max_seconds`** with **`daily_tar_restore`** stuck (no `daily_tar_restore end`); gated prewarm with **`gated_tar_restore=True`** and no **`archive decompress restore begin`** / no `zstd -d` for that day while MainThread is busy; repeated fnctl timeout without preceding defer/yield/restore-wait logs; defer streak with no progress past **`defer_cap_exceeded`** without seal/dedupe completion; multi-hour `write_lock_contended` **with** `try_file_write_lock` still failing (true live holder stuck) — distinct from leftover orphan sidecars where the probe returns `OK_uncontended`.

### T0 / T1 — exclusive daily tar restore (dual-zstd / `decomp.tmp`, 2026-07)

**Failure signature (pre-fix):** two concurrent `zstd -d -o …/YYYY-MM-DD.tar.decomp.tmp` processes on the same day; three day-close workers logging `daily_tar_restore begin` for one calendar day; `daily_tar_restore end ok=no` from a non-owner clearing the Redis key and reopening the gate.

**Classify zstd cmdline:**

| Cmdline | Path | Expected concurrency |
|---------|------|----------------------|
| `zstd -d -c` | Redis archive-members populate | One populate winner per day; OK across different days |
| `zstd -d -o …decomp.tmp` | `decompress_compressed_to_tar` (sealed→tar) | **At most one** process site-wide per calendar day |

**Deploy-time cleanup (single wave with code; no pre-code INI edit):**

```bash
# pipeline — stop, clear stale decomp.tmp under INI daily_archive_dir, up
docker compose -p hpcperfstats -f docker-compose.yaml stop pipeline && \
docker compose -p hpcperfstats -f docker-compose.yaml run --rm --no-deps pipeline su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(cfg.get_daily_archive_dir_path())
"' && \
docker compose -p hpcperfstats -f docker-compose.yaml run --rm --no-deps pipeline su hpcperfstats -c 'sh -lc "
rm -f \"$(python3 -c \"from hpcperfstats.dbload.lib import conf_parser as cfg; print(cfg.get_daily_archive_dir_path())\")\"/*.tar.decomp.tmp
"' && \
docker compose -p hpcperfstats -f docker-compose.yaml up -d pipeline
```

**Post-restart verify (paste back):**

```bash
# pipeline — restore lease logs + live zstd census
docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | \
  grep -E 'daily_tar_restore begin|daily_tar_restore end|day_close defer reason=daily_tar_restore|day_close pre_seal_verify' | tail -60 && \
docker compose -p hpcperfstats -f docker-compose.yaml exec pipeline su hpcperfstats -c 'sh -lc "
ps -eo pid,lstart,etime,cmd | grep -E \"zstd -d\" | grep -v grep || true
"'
```

**Pass (T0):** at most one `archive: daily_tar_restore begin` per calendar day until matching owner `end`; at most one `zstd -o …decomp.tmp` process; extra day-close workers may log `janitor: day_close defer … reason=daily_tar_restore` / `phase=pre_seal_verify`. **Fail (T0):** two `zstd -o …decomp.tmp` for the same day, or interleaved `begin` from multiple callers without owner `end`, or non-owner `end ok=no` reopening a second `begin` on the same day.

**T0 / T1 — membership invalidate after sealed→tar restore:** successful `decompress_compressed_to_tar` (gated prewarm / `ensure_daily_tar_restored_for_append` / corrupt replace) must log **`Archive members cache invalidated … reason=tar_restore_pre`** then **`reason=tar_restore`** so sealed+`tar=None` Redis maps cannot stay warm and skip populate. Expect a cold re-prewarm / populate for that day after restore — **not** a stall from thrashing the same day forever.

```bash
# T0/T1 — restore invalidates memberships (full pipeline log; never --tail before grep)
podman-compose -p hpcperfstats logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'archive decompress restore begin|Archive members cache invalidated.*reason=tar_restore|daily_tar_restore (begin|end)|chunk prewarm' /tmp/pipeline-full.log | tail -80
```

**Pass:** after a gated restore day, both `tar_restore_pre` and `tar_restore` invalidate lines appear near `archive decompress restore begin` / `daily_tar_restore end ok=yes`; subsequent prewarm/populate for that day proceeds (cold or re-warm). **Fail:** restore completes (`daily_tar_restore end ok=yes`) but no `reason=tar_restore` invalidate, while Redis stays warm under sealed+`tar=None` and prewarm skips with `redis_warm` without rescanning.

### T0 — ingest InterfaceError / ``another command is already in progress`` (2026-07)

**Failure signature (pre-fix):** ingest-pool workers spam ``error in single host_data insert:`` / ``error in single proc_data insert:`` with ``sending query failed: another command is already in progress`` after a failed ``bulk_create`` (often interleaved with ``IngestPerFileTimeoutError`` / SIGALRM ``_handler``). Cascading single-insert logs for every remaining row in the frame.

**Root cause (fixed):** write path swallowed timeouts into individual fallback without ``rollback`` / ``close_old_connections``, and did not hold ``write_lock`` on fallback. See ``sync-timedb-change-regression-gate.mdc`` → *Write-path connection reset*.

```bash
# T0 — InterfaceError spam check (full pipeline log; never --tail before grep)
docker compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'another command is already in progress|error in single (host_data|proc_data) insert' /tmp/pipeline-full.log | tail -80
```

**Pass (T0):** after redeploy, no cascading identical ``already in progress`` lines on single-insert fallback during normal ingest; occasional one-line desync + abort for a file is acceptable if rare. Chunk ingest / archive progress continues.

**Fail (T0):** dozens/hundreds of identical ``already in progress`` lines per file after ``bulk_create`` failure or per-file timeout.

**T1/T2 stall matrix:** N/A for this fix unless ``tests/run_sync_timedb_regression_battery.sh`` regresses or handoff/chunk_gate signatures reappear — then use the standard T1/T2 tiers above.

### T0 — host bulk membership invalidate + pipeline restart (post-crash)

After a mass tar crash (or when many days need membership reassessment), restore-only invalidate is not enough: warm Redis L2 for existing on-disk tar identities stays hot, and worker **L1** survives a Redis clear until process recycle. Use the **host-side** CLI (not `docker compose exec pipeline` as primary):

```bash
# Working directory: Compose checkout with docker-compose.yaml (typically HPCPerfStats/)
# Dry-run first (counts only; no DELETE; no restart)
../.venv/bin/python3 scripts/invalidate_archive_members.py \
  --day YYYY-MM-DD --dry-run --compose-dir .

# Real invalidate for one day (SCAN+DELETE via compose redis-cli; then restart pipeline)
../.venv/bin/python3 scripts/invalidate_archive_members.py \
  --day YYYY-MM-DD --compose-dir .

# All days (requires --yes). Opt out of restart with --no-restart if you will recycle separately.
../.venv/bin/python3 scripts/invalidate_archive_members.py \
  --all --yes --compose-dir .
```

**Pass (T0):** dry-run prints `scanned=… deleted=0 dry_run=True` and does not restart; real `--day` prints `deleted=` matching scanned membership keys, then `pipeline restart requested ok`; after pipeline is up, expect cold `chunk prewarm` / populate for that day (not sticky `redis_warm` on a known-stale map). Coordination keys (`ingest_tar_hot`, `archive_append_inflight`, `daily_tar_restore`) remain. **Fail:** `--all` without `--yes` (non-dry); Redis cleared but no restart when L1 must be cold (omit `--no-restart`); wrong `--compose-dir` / project.

**Why ingest/archive stay on spawn:** CPU/RSS isolation, `maxtasksperchild` recycle, L1 host cache, and pool stall diagnostics (`Pool imap stalled`, exit **124**). Janitor and startup coordinators use **session thread executors** by design (two-queue model).
