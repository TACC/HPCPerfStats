# Operator sync_timedb stall verify (tiered catch-up)

Backlog catch-up sites (months of `waiting_on_ingest` days) can run **many hours** of valid giant-archive work before an **idle spin** stall appears. Do **not** mark a deploy verified on a **15-minute T0 smoke alone**.

See also: `sync-timedb-change-regression-gate.mdc`, [SYNC_TIMEDB_PARALLELISM.md](SYNC_TIMEDB_PARALLELISM.md) (spawn vs thread pool taxonomy), [day-close-ingest-loop-fix plan](.cursor/plans/day-close-ingest-loop-fix.plan.md) Phase 6, [june-ingest-stall-prevention plan](.cursor/plans/june-ingest-stall-prevention.plan.md).

## Pre-deploy (every PR touching sync_timedb)

```bash
cd HPCPerfStats && tests/run_sync_timedb_regression_battery.sh
```

Attach the `test_runs/day-close-loop-regression-battery-*.log` path to the PR or deploy ticket.

**After sync_timedb dedup-audit deploy (blocking/census split, persistence v6):** run the same battery pre-deploy; post-deploy use T0/T1/T2 below. Persistence v6 resets orphan `startup_*` sidecars on contract bump — expect one-time empty maint hints if the contract file was stale. No separate stall signature is expected from this audit alone; treat regressions like any other sync_timedb deploy.

## Post-deploy tiers

| Tier | When | Pass criteria |
|------|------|---------------|
| **T0 smoke** | T+15 min after deploy | Pipeline up; at least one `chunk ingest summary` **or** documented giant-chunk defer (not an error by itself); optional boot thread census (below) |
| **T1 progress** | T+4 h **or** after first giant `archive_job_done` for backlog head day | Oldest `waiting_on_ingest` day: `unprocessed` **not frozen** vs prior sample; no repeating `oldest_day_chunk_gate_stall` with same `incomplete_n` |
| **T1 progress (`current`)** | Same window while CLI ``current`` (newest-first) runs | Descending `epochs=` on `chunk dispatch begin`; `youngest_day_chunk_gate` / `youngest_day_chunk_gate_pad` (not ascending oldest-gate for ``all``); heartbeat sidecar/Redis advances with active work |
| **T2 catch-up** | T+24 h or when head day advances | New `chunk ingest summary` cadence; June-scale head day `unprocessed` trending down; `ingest_stall_watchdog` absent |

### T0 / T1 — find-based pending discovery (every chunk + mtime window)

After deploy of **GNU find `-printf` stats discovery** (`sync_timedb_stats_find`, `rescan_every_chunks=1`, `sync_ingest_rescan_mtime_days=1`): multi-hour silent gaps between `pending rescan done` / `find_stats` lines on ``current``/idle must **not** return. Discovery must complete in seconds (operator baseline: full archive ~0.7s, `-mtime -1` ~2s on ~350k files).

```bash
# T0 — find cadence after deploy (full pipeline log; never --tail before grep)
podman-compose -p hpcperfstats logs pipeline 2>&1 | tee /tmp/pipeline-full.log
grep -E 'find_stats paths=|collect_stats_files_in_range: find paths=|Rescanned after 1 chunks|pending rescan done' /tmp/pipeline-full.log | tail -80

# T0 — fail-closed signature (should be absent on GNU find images)
grep -E 'FindStatsDiscoveryError|does not support -printf|GNU find not found' /tmp/pipeline-full.log | tail -20 || true
```

**Pass (T0):** `find_stats` / `collect_stats_files_in_range: find` lines appear with small `elapsed_s` (typically **&lt;5s**); after ingest chunks expect **`Rescanned after 1 chunks`**; no multi-hour silence with only occasional `idle_finalize`.

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

### T0 / T1 — tar append exit 2 / large member (`out of off_t range`, 2026-07)

Members larger than **8 GiB − 1** fail classic ustar without pax headers (`value N out of off_t range 0..8589934591`). Production always passes **`--posix`** on tar create/append (`-C /` + relative `-T` members). When the daily tar is **not pax-capable** (bare `POSIX tar archive` without pax headers; GNU labels need no convert), the **archive pool** job logs **`must_convert`**, attempts **extract + `tar --format=pax` recreate**, then appends. On convert failure: **`convert_fail_skip`** oversized members (original tar untouched) and continue with remaining paths. **`archive_job_done`** includes **`outcome=ok|fail`** (do not treat `archive_job_done` alone as success).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

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
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# Must be absent after deploy
grep -E 'Queue watermarks|high watermark|low watermark|adaptive_dispatch' /tmp/pipeline-full.log | tail -20 || true

# Confirm archival still progresses under caps
grep -E 'Archive dispatch submitted=|pending_archive_heap|archive_job_duty|discover_ready_day_close|Archive janitor tick done' /tmp/pipeline-full.log | tail -40
```

**Pass (T0):** no `Queue watermarks` / `above high watermark` / `below low watermark` lines; archive dispatch and day-close continue within configured pool/inflight.

**T0/T1 — multi-day mapping with pool ≪ N (site example pool=6):** after `Archive mapping: N tar(s)` with N larger than pool (or with overflow from an earlier narrow wave), expect later `archive_job_begin` / `Archive dispatch submitted=` for overflow calendar days **before** the next `chunk imap start` / IMAP for a new ingest chunk. Tiny `Archived batch (2|5|…)` plus `archive_job_duty … to_add=… appended=…` means Redis/tar already-present skips — not a missing day. If `post_finalize_reconcile oldest_tar=` advances past days that never logged `archive_job_*`, that is a drain regression.

### Pipeline ingest rate (listend vs sync_timedb)

Measure closed-segment production rate vs full-ingest and archive-done consumption from **full** pipeline logs (requires `--timestamps` for accurate windows and backlog ETA):

```bash
# Live compose logs (recommended: full dump, not --tail)
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs --timestamps pipeline 2>&1 \
  | python3 scripts/measure_pipeline_ingest_rate.py

# Saved log file; optional last-N-minutes window
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs --timestamps pipeline 2>&1 > /tmp/pipeline-full.log
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
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | tee /tmp/pipeline-full.log

grep -c 'removing stats file (day raw removal preflight)' /tmp/pipeline-full.log
grep -c 'janitor: day_close delete defer' /tmp/pipeline-full.log
grep -E 'janitor: day_close delete (start|defer)' /tmp/pipeline-full.log | tail -40
```

**T1 (post-deploy RC-S fix):** preflight count **> 0**; defer lines carry **`tar=`**, **`day=`**, **`skip_class=`** (`handoff`, `pending_stats`, `inflight`, `pending_append`, `paths_pending_delete`, `chunk_dispatch`). Legitimate ingest overlap (RC-P) still defers with **`skip_class=pending_stats`** or **`chunk_dispatch`** until the path leaves live ingest sets.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | \
  grep -E 'day raw removal preflight|day_close delete defer.*skip_class=|Day raw removal delete complete' | tail -60
```

**T2 (June-4 retryable-skip stall, RC-J4):** after RC-S deploy, sealed days with all verified paths deleted but retryable skips on disk should handoff (`day_close handoff requeue day=2026-06-04`) and manifest **`phase=done`** when skips clear.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec pipeline su hpcperfstats -c "sh -lc '
python3 -c \"import json, os; from hpcperfstats.dbload.lib.conf_parser import get_archive_dir_path
p=os.path.join(get_archive_dir_path(), \\\".sync_timedb_day_raw_removal\\\", \\\"2026-06-04.json\\\")
print(open(p).read() if os.path.isfile(p) else \\\"missing\\\")\"'"

docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | \
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

After deploy of **pad gated chunks / cut between-chunk tax** (Choice C): under saturated pending (`ingest_queue_max` ≈ `chunk_size`) with frozen `oldest_tar` / `incomplete_n>0`, chunks must fill toward `chunk_size` and reconcile must not dominate the duty cycle with multi‑minute identical accrual rescans.

```bash
# Full log first (never --tail before grep on backlog sites).
podman-compose logs pipeline 2>&1 | tee /tmp/pipeline-full.log

# T0 — pad + skip signals under backlog
grep -E 'oldest_day_chunk_gate |oldest_day_chunk_gate_pad|chunk_pad_n=|pending reconcile cap skipped|pending reconcile cap (begin|done)' /tmp/pipeline-full.log | tail -80

# T1 — chunk_len near chunk_size when pending saturated (INI chunk_size=3000 → expect chunk_len≈3000)
grep -E 'oldest_day_chunk_gate .*chunk_len=' /tmp/pipeline-full.log | tail -40
```

**Pass (T0):** `oldest_day_chunk_gate` / `chunk ingest summary` continues; when oldest day has fewer paths than `chunk_size` and pending is full, expect **`chunk_pad_n>`0** and **`chunk_len`** near configured **`chunk_size`** (not steady `chunk_len≪chunk_size` like pre-fix `419` vs `3000`). Under frozen `incomplete_n` + same `oldest_tar`, expect **`pending reconcile cap skipped reason=unchanged_incomplete`** (or `oldest_day_gate_stall_unchanged`) between waves — not back-to-back **`pending reconcile cap begin/done source=accrual`** with **`elapsed_s` hundreds–thousands** and identical `incomplete_n`.

**Pass (T1):** Oldest-day paths still lead the chunk (`epochs` / sample still prioritize head day); padded later-day paths may appear **after** oldest paths within the same chunk. Reconcile skip must clear after ingest progress / oldest advance (`incomplete_n` change) — next wave may show a full **`pending reconcile cap begin`** again.

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

Run **one** recommended ``current`` process per archive (last writer wins the heartbeat; multiple ``current`` processes are unsupported). Pair with ``all`` for backlog; ``all`` exits when its next oldest pending day is within ``sync_ingest_current_proximity_days`` (default **2**) of ``current``'s fresh heartbeat.

```bash
# T1 — current scheduling (descending epochs / youngest gate)
grep -E 'chunk dispatch begin|youngest_day_chunk_gate|newest-first / current mode|all exiting near current' /tmp/pipeline-full.log | tail -80

grep -E 'youngest_day_chunk_gate(_pad|_stall|_cross_day_defer|_fallback)? ' /tmp/pipeline-full.log | tail -40

# Heartbeat (sidecar under archive_dir, or Redis key hpcperfstats:sync_timedb:current_heartbeat)
ls -la /path/to/archive/.sync_timedb_current_heartbeat.json 2>/dev/null || true
```

**Pass (T1 ``current``):** ``chunk dispatch begin`` ``epochs=`` trend **descending** (newest first); expect **`youngest_day_chunk_gate`** / **`youngest_day_chunk_gate_pad`** (not the ``all`` ``oldest_day_*`` grammar for the same run). Missing/stale heartbeat must **not** stop ``all`` — only a fresh heartbeat near the pending head day triggers ``all exiting near current``.

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

**Pass (T1 — empty Redis after prewarm):** After `chunk prewarm complete` with a day token other than `no_daily_archive` / `day_ingest_skip`, Redis must be warm (`dbsize` / `archive_members*` keys) and **`chunk imap start`** must appear. **Failure signature (pre-fix):** `…:prewarmed` (or similar) with `dbsize=0` and no `chunk_elapsed` / no `chunk imap start`. **Post-fix:** supervisor **exits** with `archive members Redis empty after prewarm` rather than hanging.

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

### Idle `top` + `long_ingest_budget` stall defer (RC-A1 — not a hang)

**Misread:** Host `top` can look “idle” while ingest is healthy — supervisor MainThread often waits on imap; work lives on **`[sync_timedb:worker:ingest-pool]`** PIDs (often ~100%+ CPU). Filter `ps`/`top` for **`worker:ingest-pool`**, not only the main `sync_timedb.py [main]` line.

**Healthy WARN:** `WARN: pool imap stall deferred: long ingest budget` / `defer_reason=long_ingest_budget` means the sliding-window stall timer is deferred because an in-flight path’s **`effective_ingest_timeout_s`** exceeds the batch precompute — expected on giant files / long `db_write`. This is **not** exit **124** and **not** an idle spin.

**Shared stages (RC-A):** stall snapshots may show workers in **`populate_queue_wait`** while other workers parse giants. An idle-looking `populate_queue_wait` row next to `long_ingest_budget` is **normal shared-stage telemetry**, not proof that ingest is stuck on populate. Prefer `chunk ingest summary`, `giant pool supplement begin|replenish`, and busy `worker:ingest-pool` PIDs over a single stage token.

**RC-D queue semantics:** no-supplement process queue = **`sync_ingest_queue_max_size`** (default **3000**). Ingest **chunk size** follows the same knob (`get_sync_ingest_chunk_size` alias — leftover `sync_ingest_chunk_size=` INI lines are ignored). Giant-supplement reservoir = **queue × `sync_ingest_giant_pool_supplement_queue_multiplier`** (default **2 → 6000**) at **batch start and mid-imap refresh**. Grep **`giant pool supplement replenish`** when giants run for hours with disk backlog; **`giant pool supplement empty reason=exhausted|size_filter`** when the reservoir has nothing eligible.

```bash
# Full pipeline log first (no --tail before grep)
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 \
  | tee /tmp/pipeline-full.log

grep -E 'long_ingest_budget|stall deferred|Pool imap stalled|chunk ingest summary|giant pool supplement|populate_queue_wait' /tmp/pipeline-full.log | tail -80

docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec -T pipeline \
  sh -c 'ps -eLo pid,pcpu,args | grep -E "worker:ingest-pool|sync_timedb.py" | grep -v grep | head -40'
```

**Pass (T0):** `chunk ingest summary` continues; ingest-pool workers busy; only defer WARNs (no `ERROR: Pool imap stalled` → exit 124). **Fail:** true stall ERROR, or main+workers idle with no ingest progress for the T1 window.

### Leftover daily `.tar` day-close (VERIFYING+POST_SEAL / mixed skips)

After deploy of quarantine-transparent waiting_on_ingest + phase promote:

- **`phase=verifying` + `verify_stage=post_seal_complete`** must promote (log `promote phase=verification_complete`) then reach **`delete start`** or handoff — not seal-only forever.
- On-disk mix of **`skipped_not_in_archive` + `skipped_quarantine`** after verified delete must reach **`phase=done`** (waiting_on_ingest) + handoff retryables — not seal↔delete reloop.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 \
  | grep -E 'promote phase=verification_complete|day_close delete start|delete deferred tar=.*delete_disqualified|Day raw removal delete complete|waiting_on_ingest' \
  | tail -60
```

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

**False fatal exit 137 on maxtasksperchild recycle (T1):** during catch-up with fast `outcome=db_skip` lines, grep must show **`INFO: pool worker recycle in progress`** (and optional **`WARN: pool worker recycle slow`**) but **must not** show **`Pool worker exit: hard exit code=137`** with **`likely_cause=recycle`** while **`alive_workers`** shows replacements keeping pace (for example **15/16**, **19/22**, **23/24**, or **20/21** with `exitcode=0`). Pre-fix signature: **`grace_poll=1/2`**, **`grace_poll=2/2`**, then ERROR on a **third** dead PID. Post-fix (2026-07-08 hardening): tolerate healthy recycle when materialized workers are below **`sync_ingest_pool_processes`** / process cap during spawn; consecutive different dead PIDs at healthy alive counts are tolerated; fatal recycle-shaped exits log **`likely_cause=recycle_stuck`** (not bare **`recycle`**) plus **`ERROR: pool worker recycle gate rejected:`** with `alive`, `expected_total`, `materialized`, `gap`. **INI:** shipped default is **`sync_ingest_pool_maxtasksperchild=0`** (cooperative retire) — the bare **`maxtasksperchild`** key under `[PIPELINE]` is **not** read. **Archive pool** always recycles after one append task even when ingest uses **`sync_ingest_pool_maxtasksperchild=0`**. Ingest-only supervisor cooperative retire (`failure_reap` / `rss_reap` when **`maxtasksperchild=0`**) uses the same healthy-recycle contract (SIGTERM exitcode **-15** tracked per pool). Set **`maxtasks=1`** only when you want stdlib recycle after every file.

```bash
cd HPCPerfStats
docker compose logs pipeline --since 6h 2>&1 | grep -E 'pool worker recycle in progress|pool worker recycle gate rejected|Pool worker exit: hard exit code=137|likely_cause=recycle' | tail -50
```

**Expect:** `INFO: pool worker recycle in progress` during fast `db_skip` catch-up; **no** `hard exit code=137` with bare `likely_cause=recycle` at healthy alive ratios; if recycle replacement truly stalls, `recycle_stuck` + gate-rejected line before fatal.

**Idle-pool ghost / exit 124 after full redispatch thrash (T1, 2026-07-08 / 5th recurrence 2026-07-09):** during cooperative recycle + fast `db_skip`, pre-fix signatures included three **`INFO: pool imap idle reconcile redispatch round=… redispatched_n=N pending_async_n=N`** with identical `pending_sample`, workers `futex_wait_queue`, then soft hang or **`hard exit code=124`**. **5th-recurrence hang (pre–abandon-pool):** `pool_recover` → `skip_probe` (`duplicate_pending_n=0`) → `terminate begin` → `workers_before=N` — **no** `terminate outcome` / `pool_recover done` (MainThread stuck in stdlib `Pool.terminate` / `_help_stuff_finish`); live **`ingest_workers` ≈ 2× process_cap** after proactive swap without killing the old pool. **Post-fix (abandon-pool + recover wall + PPID census, 2026-07-16):** after full-redispatch thrash, expect **`INFO: pool imap idle reconcile pool_recover`** then **`pool_recover skip_probe begin`**, **`pool_recover terminate workers_before=…`**, **`pool_recover ppid_census kill`** (or reclaim), **`pool_recover terminate outcome=abandoned`** (or hard exit **124** within recover wall — never a silent multi-minute gap after `workers_before=`), **`pool_recover terminate elapsed_s=…`**, **`pool_recover respawn dispatch_probe ok`**, **`pool_recover resubmit n=…`**, and **`INFO: pool imap idle reconcile pool_recover done`** with resumed **`ingest file path=`**. **`dispatch_probe failed … err=`** with empty `err=` is **`TimeoutError`** (logs now include the type name). Preventive: path size alone never retires (no cooperative giant recycle); **no supervisor retire on `outcome=db_skip`** except RSS; live **`pending_inflight`**, refuse retire/swap while replacement **`gap>0`**, proactive swap **abandons** old workers **including orphans not in `pool._pool`**. If recover fails/times out, fatal must include **`likely_cause=idle_pool_taskqueue_dead`**. Optional WARN **`retire skipped missing worker_pid … likely_cause=meta_or_registry_gap`** must stay WARN-only. Distinguish from exit **137** recycle troubleshooting above. **Do not** treat multi-hour supervisord restart as the fix for 2×/N× `[worker:ingest-pool]` children — expect in-process reclaim (`child_ingest` ≤ configured `ingest_pool_processes`).

```bash
docker compose logs pipeline 2>&1 | grep -E 'idle reconcile redispatch|idle reconcile pool_recover|pool_recover skip_probe|pool_recover terminate (workers_before|outcome|elapsed_s)|pool_recover ppid_census|child_ingest over cap|pool_recover respawn dispatch_probe|pool_recover resubmit|ingest pool replacement lagging|retire deferred|proactive swap|outcome=abandoned|duplicate dispatch suppressed|duplicate_pending_n|idle_pool_ghost_inflight|idle_pool_taskqueue_dead|retire skipped missing worker_pid|hard exit code=124' | tail -80
```

**Expect:** redispatch → `pool_recover` with **`pool_recover done`** and **`terminate outcome=abandoned`** (or **124** `idle_pool_taskqueue_dead` within recover wall); **never** hang after `workers_before=` with no outcome; **`dispatch_probe ok`** and resumed ingest on success; **no** `likely_cause=unknown` on ghost fatals; after swap/recover, **`child_ingest` equals INI `ingest_pool_processes`** (see census below).

### T0 / T1 — ingest-pool orphan census (2× / N× after swap, 2026-07-16)

**Failure signature (pre-fix):** INI `ingest_pool_processes=N` but `ps` under main shows **`child_ingest` ≫ N** (often ~2× then grows: 48 → 71+) after **`dispatch_probe failed … err=`** (empty = `TimeoutError`) → **`proactive swap`** / idle `outcome=abandoned` that SIGKILL'd only `pool._pool`. Orphans sit in `queues.get` while a thin live cohort does work.

**Acceptance (post-deploy, no multi-hour restart required):** `child_ingest == ingest_pool_processes` (and ≤ `pool_process_cap`). Reclaim may log **`ERROR: ingest pool child_ingest over cap`** then cull; subsequent census must match configured size.

```bash
# T0 — INI + PPID census (compose cwd = git checkout with docker-compose.yaml)
docker compose exec -T pipeline sh -lc '
python3 - <<'"'"'PY'"'"'
from hpcperfstats.dbload.lib import conf_parser as c
print("ingest_pool_processes", c.get_sync_ingest_pool_processes())
print("pool_process_cap", c.get_sync_pool_process_cap())
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
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec pipeline su hpcperfstats -c 'python3 -c "
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
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|no_daily_archive|populate incomplete|Timed out waiting for archive members populate|Begining Chunk|chunk prewarm days=' | \
  tail -40
```

**Bucket E2 — Dirty-tar populate EOF thrash + self-hot + exit 124 (T0/T1, 2026-06-07 class):** pre-fix loop shows `populate_source_decision … dirty=True sealed_exists=True use_tar=True` then `transient tar populate EOF during hot/append` while Redis census has **`tar_hot=True`** / **`append_inflight=False`** (waiter self-hot alone), orphan clear `hlen≈6500`, stale clear `hlen=0`, forever retry; workers wchan-idle in populate wait → **`pool_recover exceeded wall_s=30.0`** → exit **124** `idle_pool_taskqueue_dead`. Candidate counters `unprocessed_cross_day_n` / `processed_cross_day_n` are **diagnostic only** (misbucket census) — they do **not** set `waiting_on_ingest` by themselves. Flood of `Unable to find first timestamp in N path(s)` after day-close delete is rate-limited/summarized (not the crash driver).

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml exec pipeline su hpcperfstats -c 'python3 -c "
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
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | \
  grep -E 'YYYY-MM-DD|populate_source_decision|prefer sealed fallback|sealed fallback after dirty-tar|transient tar populate EOF|clearing orphan incomplete|pool_recover skipped|pool_recover exceeded wall|idle_pool_taskqueue_dead|Unable to find first timestamp' | \
  tail -80
```

**Post-fix pass:** no forever `transient tar populate EOF during hot/append` with only `populate_wait` hot; expect **`prefer sealed fallback`** / **`populate sealed fallback after dirty-tar EOF`** then `populate_source=sealed` (or warm Redis); **no** orphan `hlen≈6500` clears after mid-scan fail; idle reconcile may log **`pool_recover skipped reason=populate_wait…`** instead of exit **124** while wait is live.
**Populate incomplete after lock release (tar exists):** grep for `Archive members populate incomplete after lock release`. Error key suffix `none:none:<tar_mtime>:<tar_size>` with concurrent `archive_job_done` / `redis_merge_warm` on the same day usually means **tar-identity drift** (waiter on pre-append fingerprint, merge on post-append). Post-fix waiters re-resolve identity and re-enqueue within `populate_max_seconds` rather than immediate `sys.exit(1)`.

**Transient fnctl read-lock timeout (T1):** grep for `transient fnctl read lock timeout during tar populate` and `transient fnctl during archive members prewarm`. **Healthy:** populate waits on fnctl (up to **`populate_max_seconds`**) then `populate_source=tar` when `.tar` exists; occasional WARNING + `populate incomplete after lock release; recovering` or `chunk prewarm days=...:populate_recovering:tar_populated` — supervisor must **not** restart (`L2 contract failed` absent or rare). **Unhealthy:** repeated `ERROR: archive members Redis L2 contract failed` with supervisor restart loop on the same calendar day. When **`.tar` is present**, expect **`populate_source=tar`** not sealed; sealed populate is normal only after tar-drop (`archive_keep_uncompressed_tar=no`).

**Populate-pool unavailable / refuse sealed stream (T1 — exit status 1 class):** grep for `populate-pool unavailable`, `refusing sealed stream`, `not an immediate L2 fatal`, and `archive members Redis L2 contract failed`.

```bash
docker compose -p hpcperfstats -f docker-compose.yaml -f docker-compose.app.yaml logs pipeline 2>&1 | \
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

**Unhealthy:** populate waits full **`populate_max_seconds`** with **`daily_tar_restore`** stuck (no `daily_tar_restore end`); gated prewarm with **`gated_tar_restore=True`** and no **`archive decompress restore begin`** / no `zstd -d` for that day while MainThread is busy; repeated fnctl timeout without preceding defer/yield/restore-wait logs; defer streak with no progress past **`defer_cap_exceeded`** without seal/dedupe completion.

**Why ingest/archive stay on spawn:** CPU/RSS isolation, `maxtasksperchild` recycle, L1 host cache, and pool stall diagnostics (`Pool imap stalled`, exit **124**). Janitor and startup coordinators use **session thread executors** by design (two-queue model).
