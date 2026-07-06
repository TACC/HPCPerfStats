# Operator sync_timedb stall verify (tiered catch-up)

Backlog catch-up sites (months of `waiting_on_ingest` days) can run **many hours** of valid giant-archive work before an **idle spin** stall appears. Do **not** mark a deploy verified on a **15-minute T0 smoke alone**.

See also: `sync-timedb-change-regression-gate.mdc`, [SYNC_TIMEDB_PARALLELISM.md](SYNC_TIMEDB_PARALLELISM.md) (spawn vs thread pool taxonomy), [day-close-ingest-loop-fix plan](.cursor/plans/day-close-ingest-loop-fix.plan.md) Phase 6, [june-ingest-stall-prevention plan](.cursor/plans/june-ingest-stall-prevention.plan.md).

## Pre-deploy (every PR touching sync_timedb)

```bash
cd HPCPerfStats && tests/run_sync_timedb_regression_battery.sh
```

Attach the `test_runs/day-close-loop-regression-battery-*.log` path to the PR or deploy ticket.

## Post-deploy tiers

| Tier | When | Pass criteria |
|------|------|---------------|
| **T0 smoke** | T+15 min after deploy | Pipeline up; at least one `chunk ingest summary` **or** documented giant-chunk defer (not an error by itself); optional boot thread census (below) |
| **T1 progress** | T+4 h **or** after first giant `archive_job_done` for backlog head day | Oldest `waiting_on_ingest` day: `unprocessed` **not frozen** vs prior sample; no repeating `oldest_day_chunk_gate_stall` with same `incomplete_n` |
| **T2 catch-up** | T+24 h or when head day advances | New `chunk ingest summary` cadence; June-scale head day `unprocessed` trending down; `ingest_stall_watchdog` absent |

### Head+tail archive DB gate (stricter than head-only)

When **`sync_archive_require_db_ingest=yes`**, tar append and raw delete require **both** the first and last digit-leading timestamp seconds in `host_data` (streaming head + EOF-backward tail; no full-file scan). After deploy:

- Expect more **`not_head_tail_ingested`** / **`skipped_not_head_tail_ingested`** and day-close **handoff** (`day_close handoff requeue`) until ingest writes the tail second.
- Legacy manifest reasons **`not_head_ingested`** / **`not_sample_ingested`** remain retryable.
- **T0:** gate skips alone are not a stall if `chunk ingest summary` continues.
- **T1/T2:** `not_head_tail_ingested` counts should fall as head-day `unprocessed` declines; frozen `waiting_on_ingest` with only gate skips and no ingest progress is a real stall.

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

**Pass (T1 — aligned backlog / day-close skip fix, 2026-07):** `oldest_tar` must be a day with **tar-aligned** on-disk unprocessed (filename/mtime calendar day matches the tar), not a day whose only remaining map entries are cross-day misbuckets (e.g. May-27 `oldest_tar` with `calendar_days={'2026-07-03': 1}` only). Day-close candidate lines: `unprocessed=` is **aligned** count; optional `unprocessed_cross_day_n=` for misbucketed paths that do **not** block that day. **`chunk dispatch begin`** must not sit on a later month (e.g. June-07) while earlier days still report **aligned** `unprocessed>0`. Cap may log **`pending cap supplement replace`** when a full queue is rebuilt from older snapshot/unprocessed paths; **`pending cap supplement skipped reason=no_closed_paths`** is OK after accrual trim when Phase-B all-unprocessed merge already filled the head.

```bash
# Aligned gate: oldest_tar should not be pinned by cross-day-only misbuckets.
grep -E 'oldest_day_chunk_gate |pending reconcile cap done|pending cap supplement' /tmp/pipeline-full.log | tail -40

# Day-close report: unprocessed= is aligned; cross_day_n is diagnostic only.
grep 'janitor: day_close candidate' /tmp/pipeline-full.log | grep -E 'waiting_on_ingest|unprocessed_cross_day' | head -40
```

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

docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | \
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

After deploy of log-role prefixes (2026-06), pipeline lines identify **which actor** emitted them. Greps for **`[sync_timedb]`** still match (substring).

### Log prefix → actor

| Log prefix | Process / thread | Responsibility |
|------------|------------------|----------------|
| `[sync_timedb:main]` | Supervisor main thread in pipeline PID | Chunk loop, handoff, oldest-day gate, rescan, enqueue day-close |
| `[sync_timedb:worker:ingest-pool]` | Spawned ingest worker | Parse + DB ingest (combined pool) |
| `[sync_timedb:worker:ingest-parse-pool]` | Spawned parse worker | Split pipeline parse stage |
| `[sync_timedb:worker:db-writer-pool]` | Spawned DB writer | Split pipeline DB write stage |
| `[sync_timedb:worker:archive-pool]` | Spawned archive append worker | **Hot-path tar append** (`map_async` dispatch) — **not** the janitor |
| `[sync_timedb:thread:archive-janitor]` | Daemon thread in supervisor PID | Cold path: seal → verify → delete → tar-drop; boot **`DAY_CLOSE`** discover |
| `[sync_timedb:thread:startup-raw-removal-preflight]` | Daemon thread | Boot raw removal verify/delete |
| `[sync_timedb:thread:startup-tail-ingest]` | Daemon thread | Optional tail ingest before steady state |
| `[sync_timedb:thread:archive-discovery]` | Short-lived helper thread | Archive metadata scan during heavy maintenance |
| `[sync_timedb]` (no role segment) | Legacy logs or non-daemon callers | Use message heuristics below |

**Naming note:** cpuset / process-bucket text **"sync_timedb archive workers"** means the **append pool** (`worker:archive-pool`), not the janitor thread. The janitor runs inside the supervisor process on **`thread:archive-janitor`**.

### Legacy message heuristics (pre-role deploy)

When the prefix has no `:role` segment, use message substrings:

| Substring | Likely actor |
|-----------|----------------|
| `chunk ingest summary`, `oldest_day_chunk_gate`, `handoff`, `startup_elapsed_s` | Main supervisor |
| `janitor:`, `Archive janitor tick` | Janitor thread |
| `janitor: discover_ready_day_close` | Janitor boot/steady-state DAY_CLOSE discover |
| `startup raw removal` | Startup raw removal preflight |
| `Pool imap stalled`, `worker_stages` | Ingest or parse pool worker |
| `Archive mapping`, `archive_job_done` (from worker context) | Often main coordinating append; append work runs on `archive-pool` workers |

### Correlate logs with `ps` (read-only exec)

```bash
cd HPCPerfStats   # checkout with docker-compose.yaml on site
docker compose -f docker-compose.app.yaml -f docker-compose.yaml -p hpcperfstats exec -T pipeline \
  sh -c 'ps -eLo pid,tid,pcpu,stat,args | grep -E "sync_timedb|worker:|thread:" | grep -v grep | head -30'
```

Filter live logs by role:

```bash
docker compose -f docker-compose.app.yaml -f docker-compose.yaml -p hpcperfstats logs --names pipeline 2>&1 \
  | grep --line-buffered '\[sync_timedb:thread:archive-janitor\]'

docker compose -f docker-compose.app.yaml -f docker-compose.yaml -p hpcperfstats logs --names pipeline 2>&1 \
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

**Defunct children (T0/T1):** under supervisor PID, `ps` must **not** accumulate `[sync_timedb.py ] <defunct>` zombies. Chunk-boundary reap + populate-pool `reap_and_restart` should clear them; mid-chunk throttled reap (stall poll / idle backstop) must also emit `Zombie child reap` or `Pool worker reap` when workers recycle during long imap. Grep pipeline logs for `Zombie child reap` and `WARN: unreaped zombie children` — failure if zombies span **past** the next `chunk ingest summary` / `chunk_prewarm_elapsed_s` line. If zombies persist with `exit_code=9` (SIGKILL) and no live `worker:populate-pool`, expect prewarm failures.

**False fatal exit 137 on maxtasksperchild recycle (T1):** during catch-up with fast `outcome=db_skip` lines, grep must show **`INFO: pool worker recycle in progress`** (and optional **`WARN: pool worker recycle slow`**) but **must not** show **`Pool worker exit: hard exit code=137`** with **`likely_cause=recycle`** while **`alive_workers`** shows replacements keeping pace (for example **15/16**). Pre-fix signature: **`grace_poll=1/2`**, **`grace_poll=2/2`**, then ERROR on a **third** dead PID. Post-fix: consecutive different dead PIDs at healthy alive counts are tolerated; **`likely_cause=recycle_stuck`** only when all pool workers are dead with recycle-shaped exits. **Archive pool** always recycles after one append task even when ingest uses **`sync_ingest_pool_maxtasksperchild=0`**. Ingest-only supervisor cooperative retire (`failure_reap` / `rss_reap` / `giant_reap` when **`maxtasksperchild=0`**) uses the same healthy-recycle contract (SIGTERM exitcode **-15** tracked per pool).

**Worker memory soak (T1/T2, when `maxtasksperchild=0`):** enable **`sync_ingest_worker_memory_telemetry=yes`** and grep **`sync_timedb worker_memory: event=batch_summary`**. Anti-collapse: **`tasks_on_worker_p50≥10`** on small-file batches; **`keep_worker`** dominates **`retires_total`**; **`failure_reap_pct`** / **`rss_reap_pct`** low outside giant backlog; **`giant_reap_pct`** may be high during giant catch-up (expected). Example:

```bash
cd HPCPerfStats
docker compose logs pipeline --since 24h 2>&1 | grep -F 'sync_timedb worker_memory: event=batch_summary' | tail -30
docker compose logs pipeline --since 24h 2>&1 | grep -F 'sync_timedb worker_memory: event=batch_summary' | grep -oE '(failure|rss|giant)_reap_pct=[^ ]+' | tail -20
```

**Archive populate lock timeout / F6 self-defer (T0/T1 post-fix):** fatal `Timed out waiting for archive members populate lock` during archive append when **`archive_append_inflight`** is set **before** pre-append lookup. **Pre-fix signature:** tail of pipeline logs shows **only** `populate: defer tar scan day=YYYY-MM-DD reason=archive_append_inflight` from **archive-pool** with **no** `archive_job_begin` / `archive_job_done` for that day; may end in duplicate populate lock timeout lines. Redis census post-crash may show `complete=0`, `hlen=0`, `lock_value=None`, `append_inflight=False` (orphan incomplete). **Post-fix pass:** same day shows `archive_job_begin` then `archive_job_done` / `archive_finalize`; **no** fatal populate lock timeout; janitor `Archive janitor tick done` clears `in_flight` without multi-hour `duration_s` stalls tied to stuck finalize.

```bash
docker compose logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|Timed out waiting for archive members populate lock|archive_job_begin|archive_job_done|archive_finalize|populate: defer tar scan|lock acquire timeout|janitor: tick' | \
  tail -60
```

Replace `YYYY-MM-DD` with the failing calendar day. Expect **`lock acquire timeout`** diagnostics (with `lock_owner_pid`, `append_inflight`, `pre_append_exempt`) only when a **live** external lock holder blocks acquire — not during normal archive append with inflight-first ordering.

**Populate incomplete after lock release:** grep for `Archive members populate incomplete after lock release`. Error key suffix `none:none:<tar_mtime>:<tar_size>` with concurrent `archive_job_done` / `redis_merge_warm` on the same day usually means **tar-identity drift** (waiter on pre-append fingerprint, merge on post-append). Post-fix waiters re-resolve identity and re-enqueue within `populate_max_seconds` rather than immediate `sys.exit(1)`.

**Transient fnctl read-lock timeout (T1):** grep for `transient fnctl read lock timeout during tar populate` and `transient fnctl during archive members prewarm`. **Healthy:** populate waits on fnctl (up to **`populate_max_seconds`**) then `populate_source=tar` when `.tar` exists; occasional WARNING + `populate incomplete after lock release; recovering` or `chunk prewarm days=...:populate_recovering:tar_populated` — supervisor must **not** restart (`L2 contract failed` absent or rare). **Unhealthy:** repeated `ERROR: archive members Redis L2 contract failed` with supervisor restart loop on the same calendar day. When **`.tar` is present**, expect **`populate_source=tar`** not sealed; sealed populate is normal only after tar-drop (`archive_keep_uncompressed_tar=no`).

**Ingest-wins janitor lock priority (T1):**

```bash
docker compose logs pipeline --since 24h 2>&1 | grep -E 'day_close defer|day_close yield|yield signal|populate: wait daily_tar_restore|daily_tar_restore begin|daily_tar_restore end|defer_cap_exceeded' | tail -60
```

**Healthy:** `janitor: day_close defer … reason=populate_active|ingest_tar_hot|daily_tar_restore|write_lock_contended`; `janitor: day_close yield … reason=chunk_prewarm|ingest_tar_hot` during dedupe/seal overlap; `populate: wait daily_tar_restore day=…` then successful populate; `archive: daily_tar_restore begin|end reason=missing_tar|corrupt_tar`.

**Unhealthy:** populate waits full **`populate_max_seconds`** with **`daily_tar_restore`** stuck (no `daily_tar_restore end`); repeated fnctl timeout without preceding defer/yield/restore-wait logs; defer streak with no progress past **`defer_cap_exceeded`** without seal/dedupe completion.

**Why ingest/archive stay on spawn:** CPU/RSS isolation, `maxtasksperchild` recycle, L1 host cache, and pool stall diagnostics (`Pool imap stalled`, exit **124**). Janitor and startup coordinators use **session thread executors** by design (two-queue model).
