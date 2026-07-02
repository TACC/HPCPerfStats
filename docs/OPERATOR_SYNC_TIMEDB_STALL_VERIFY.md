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

**RC-F signature:** all pools idle; MainThread in `defer_for_ingest_handoff` ← `_requeue_day_close_handoff_paths` ← `requeue_closed_raw_paths_for_ingest` ← `submit_day_close` ← `_maybe_enqueue_immediate_day_close`.

**T1 pass grep** (after deploy):

```bash
podman-compose logs pipeline 2>&1 | grep -E 'chunk ingest summary|immediate day_close defer|ingest_stall_watchdog|oldest_day_unprocessed_frozen' | tail -40
```

Expect `chunk ingest summary` to resume after `immediate day_close defer` / `archive_finalize defer immediate day_close reason=closed_raw_guard`; no `ingest_stall_watchdog` within 30 min of handoff enqueue.

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
