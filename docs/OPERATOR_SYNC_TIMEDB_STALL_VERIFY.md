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
| **T1 progress** | T+4 h **or** after first giant `archive_job_done` for backlog head day | Oldest `waiting_on_ingest` day: `unprocessed` **not frozen** vs prior sample; no repeating `oldest_day_chunk_gate_stall` with same `blocked_n` |
| **T2 catch-up** | T+24 h or when head day advances | New `chunk ingest summary` cadence; June-scale head day `unprocessed` trending down; `ingest_stall_watchdog` absent |

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

- **`ERROR: ingest_stall_watchdog`** — MainThread idle ≥30 min with `blocked_n>0` and no giant imap budget in flight.
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

**T0 (first janitor pass on a backlog day):** grep shows **`pre_seal_verify`** before **`seal`** for the same `tar=` path; retryable paths log **`day_close handoff requeue`** and **no seal** for that pass.

**T1 (steady progress):** sealed days show **`post_seal_verify`** then **`delete`**; candidate report no longer lists **`closed_raw_on_disk`** as submit block; **`waiting_on_ingest`** remains only for **`checkpoint_incomplete`** days.

### T1 verify — janitor proactive day-close (backlog catch-up sites)

After deploy of janitor discover + janitor-only **`DAY_CLOSE`** (2026-07), grep pipeline logs for discovery enqueue and janitor progress (not legacy `async day_close submit` / `eligible_deferred`):

```bash
podman-compose logs pipeline 2>&1 | grep -E \
  'discover_ready_day_close|janitor: day_close enqueue|janitor: day_close submit|day_close candidate report|Archive janitor tick done' | tail -40
```

**Pass:** checkpoint-complete older calendar days show **`discover_ready_day_close enqueued=`** or **`janitor: day_close enqueue`**; candidate report uses **`waiting_on_ingest`** / **`disqualified`** (no **`eligible_deferred`**); ingest head day stays **`waiting_on_ingest`** while janitor processes prior days; **`Archive janitor tick done`** shows **`debt_popped>0`** or progressing **`day_phases`** without **`ingest_stall_watchdog`** within 30 min.

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

**Compare dispatch vs completion:**

```bash
grep -E 'chunk dispatch begin|ingest file path=' /tmp/pipeline-full.log | tail -40
```

Within one chunk, **`ingest file path=`** epochs may permute; across chunks, **`chunk dispatch begin`** `epochs` must not leap months ahead while earlier calendar days still have closed raw on disk.

### T2 verify — idle rescan stall + cap refill (hpcperfstats03, 2026-07)

After deploy of **idle rescan snapshot refill** fix, confirm empty-queue rescans do not block ~14 minutes on **`wait_for_snapshot`**, and cap refill restores **`ingest_queue_max`** backlog instead of collapsing to **`blocked_n`** only.

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

**Pass (T2):** Every **`pending rescan begin`** is followed by **`pending rescan done`** within minutes (not ~862s **`startup archive scan ready wait_s`** on MainThread alone). **`idle_rescan_snapshot_source=accrual|coordinator`** appears on idle refill. **`pending cap supplement`** or **`capped_pending`** near **`ingest_queue_max`** after queue drain despite **`blocked_n=2`** cross-day stragglers. **`oldest_day_chunk_gate_cross_day_db_complete`** after all-**`db_skip=head_tail`** defer chunks. **`pre_seal_verify classify progress`** during long verify; **`pre_seal_verify start`** without **`complete`** for >5m is a failure. No **`archive_job_begin`** for unrelated calendar days immediately after **`Files marked for archival: 0`** chunks.

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

**Expect:** one `archive-janitor` thread when janitor enabled; optional `startup-*` threads during boot preflights; separate `[worker:ingest-pool]` / `[worker:archive-pool]` PIDs (spawn), not threads for parse/append hot path.

**Why ingest/archive stay on spawn:** CPU/RSS isolation, `maxtasksperchild` recycle, L1 host cache, and pool stall diagnostics (`Pool imap stalled`, exit **124**). Janitor and startup coordinators use **session thread executors** by design (two-queue model).
