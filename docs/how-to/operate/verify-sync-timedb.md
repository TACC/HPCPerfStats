# Verify sync_timedb after a deploy

This page takes an operator through the T0, T1 and T2 checks that prove `sync_timedb` still makes progress after a pipeline redeploy.
Use it when a deploy changes `sync_timedb` ingest, archive or day_close code, or when you suspect a stall.

> [!IMPORTANT]
> Do not mark a deploy verified on T0 alone.
> Backlog sites (months of incomplete days) can run valid giant-archive work for hours before an idle-spin stall appears.

The log lines, census tokens and sidecars named here are defined in [sync_timedb stall signatures](../../reference/sync-timedb-stall-signatures.md).

## Prerequisites

- The host battery `tests/run_sync_timedb_regression_battery.sh` passed before the deploy ([sync_timedb regression battery](../develop/run-tests.md#sync_timedb-regression-battery)).
- The new pipeline image is running: T0, T1 and T2 are post-deploy checks, and an empty job store before the deploy is a false negative, not a pass.
- You run every block on the pipeline host, from the Compose checkout that holds `docker-compose.yaml`.
- You grep the full pipeline log and limit the output with `tail` after `grep`.
  Never pass `--tail` to `logs` before `grep`: it reads only the end of the buffer and misses stall lines from hours earlier.

## Choose the tiers

Production runs one `sync_timedb.py` process per `archive_dir`, the queue orchestrator (`run_sync_timedb_queue_orchestrator`), under an exclusive flock.
Each tier has a time and its pass criteria:

| Tier        | When                                                                                      | Pass criteria                                                                                                                                                                                             |
| ----------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T0 smoke    | 15 minutes after the deploy                                                               | Pipeline up; one orchestrator flock holder; job store present or reconstruct logged incomplete work; coordinator threads running; no second `sync_timedb` for the same `archive_dir`                      |
| T1 progress | 4 hours after the deploy, or after the first append or day_close progress on the head day | Ingest hot and catch-up depth trending (or reconstruct shows complete); `complete=` cadence on age-eligible days with no remaining raw; past-day `open_tar_n` and dual `.tar` + `.tar.zst` counts falling |
| T2 catch-up | 24 hours after the deploy, or when the head day advances                                  | Completed ingest claims keep their cadence; day_close drains age-eligible days; no persistence wipe or contract bump used as a fix                                                                        |

Some deploys need fewer tiers:

- Interpreter-only redeploy: run T0 with the ABI check; skip T1 and T2 unless ingest, day_close or pool behavior changed.
- Pool-size change (`sync_ingest_pool_processes`, `metrics_pool_processes`, `gunicorn_workers`, `listend_db_ingest_pool_processes`): run T0 and T1; run T2 only if a new stall signature appears.
- One-job re-ingest: run the [`--jid` smoke](#smoke-test-a---jid-re-ingest) instead of the tiers.

## Run T0

To run T0:

1. Print the archive paths and check the census sidecars:

   ```bash
   podman-compose -p hpcperfstats exec pipeline \
     su hpcperfstats -c 'python3 -c "
   import os
   from hpcperfstats.dbload.lib import conf_parser as cfg
   a = cfg.get_archive_dir_path()
   print(\"archive_dir\", a)
   print(\"daily_archive_dir\", cfg.get_daily_archive_dir_path())
   for n in (\".sync_timedb_job_store.json\",
             \".sync_timedb_archive_members\"):
       print(n, os.path.exists(os.path.join(a, n)))
   "'
   ```

   Paste back the printed lines.

2. Check the process, its interpreter and its thread roles:

   ```bash
   podman-compose -p hpcperfstats exec pipeline sh -c '
     ps -eo pid,nlwp,rss,args | grep "[s]ync_timedb.py"
     for p in $(pgrep -f "sync_timedb.py .main."); do
       readlink "/proc/$p/exe"
     done
   '
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -oE '\[sync_timedb:thread:[a-z-]+\]' \
     | sort | uniq -c
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -E 'python_abi|orchestrator flock' | tail -10
   ```

   Paste back the process rows, the `exe` path, the role counts and the last matches.

3. Grep the orchestrator summary lines:

   ```bash
   P='queue_orchestrator (progress day=|status |census )'
   P="$P|sync_timedb_mem_telemetry:|skip_cgroup_headroom"
   P="$P|skip_file_cache_pressure|stuck_cohort_recycle"
   P="$P|dead_letter|archive_job_done |ingest per-file timeout"
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -E "$P" | tail -80
   ```

   Paste back the matches.

T0 passes when all of these hold:

- One `sync_timedb.py [main]` row and one `readlink` result of `/opt/python3.14t/bin/python3.14t`.
  `python3` on the container PATH (`/usr/local/bin/python3`) is the GIL build and is not the daemon; `listend` and `update_metrics` run on the same free-threaded interpreter.
- A `python_abi ... Py_GIL_DISABLED=1` line.
- `.sync_timedb_job_store.json` exists, or reconstruct logs explain why it is empty.
- The role counts include `[sync_timedb:thread:populate-pool]`, `[sync_timedb:thread:ingest-coordinator]` and `[sync_timedb:thread:append-coordinator]`.
- `queue_orchestrator census` lines show `total_completed=` rising while listend is on.
- A SIGTERM of the process exits with 143, not 0.

T0 fails on two orchestrators for one `archive_dir`, on `deferred_age=` burning day_close attempts, or on any pattern in [Stall patterns](../../reference/sync-timedb-stall-signatures.md#stall-patterns).

`ps -L` cannot show the thread roles: each thread title is `sync_timedb.py [thread:<ROLE>]`, and the kernel keeps only the first 15 bytes of a thread name, so every thread shows as `sync_timedb.py`.
Count the roles from log prefixes instead, as in step 2.

`<ROLE>`: a thread role such as `populate-pool`.

## Run T1 and T2

To run T1 and then T2:

1. Grep day_close progress and the day_close lifecycle:

   ```bash
   P='queue_orchestrator progress day=.*(dc_run=|complete='
   P="$P|incomplete_raw=|deferred_age=|yielded=|verify_failed="
   P="$P|tar_delete=)"
   P="$P|queue_orchestrator day_close (tar_drop|claim|vacate"
   P="$P|stage_enter|stage_exit|stage_progress)|stall_confirmed"
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -E "$P" | tail -80
   ```

2. Grep the ingest fill state:

   ```bash
   P='queue_orchestrator status |ingest fill (under-capacity'
   P="$P|empty deep_queue)"
   P="$P|ingest fill skip_(fp|missing|reband) penalty"
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -E "$P" | tail -80
   ```

3. Repeat both greps at T2 and compare with the T1 output.

T1 passes when age-eligible days with no remaining raw show `complete=` (and `tar_delete=` once the tar is dropped), days with remaining raw show `incomplete_raw=` or `yielded=`, and `ingest_hot=` inflight tracks the hot cap while hot work is queued.
T2 passes when the same counters keep advancing across the day and archive free space rises.

Day-close progress counters often land on `[sync_timedb:thread:reconstruct-coordinator]` lines, so do not filter by the day-close thread alone.

## Smoke-test a --jid re-ingest

`sync_timedb.py --jid <JID>` re-ingests the raw files of one job: the job window padded by 1 hour on each side, plus one earlier and one later raw stats file per host.
It runs no archive, day_close or orchestrator work, so the tiers do not apply.

To run the smoke:

1. Run the re-ingest:

   ```bash
   podman-compose -p hpcperfstats exec pipeline \
     su hpcperfstats -c 'sync_timedb.py --jid <JID>'
   ```

   `<JID>`: the Slurm job ID.

2. Check the exit status and the `sync_timedb --jid:` lines.

The smoke passes on exit 0 with `sync_timedb --jid: jid=... hosts=...`, `discovered=` and `done jid=... ok=... fail=...` lines; zero matching files is a success (`nothing to ingest`).
It fails on exit 1: a missing job, empty hosts, a bad argument, or every ingest failing.

## Check the archive and delete DB gate

With `[PIPELINE] sync_archive_require_db_ingest = yes` (the default), tar append and raw delete wait until a raw file is in the database.
A file is ready when either condition holds:

- Its first and last digit-leading timestamp seconds are in `host_data` (a streaming head read and an EOF-backward tail read).
- It has a zero-host ingest mark: the ingest succeeded with no host rows (`stats_rows=0`, for example a proc-only file), and the mark is in `{archive_dir}/.sync_timedb_zero_host_ingest_mark.json`.
  Ingest writes the mark by itself (`record_zero_host_ingest_mark`).

To check the gate after a deploy:

1. Grep the gate reasons:

   ```bash
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep -E 'not_head_tail_ingested|zero_host_ingest' \
     | tail -40
   ```

2. At T0, confirm that proc-only successes (`outcome=ingested` with no host rows) move on to append and delete.

3. At T1 and T2, confirm that `not_head_tail_ingested` counts fall as the head day's unprocessed files decline.

Gate skips with ingest still progressing are not a stall.
Frozen `waiting_on_ingest` days with only gate skips and no ingest progress are a stall.
Manifest reasons `not_head_ingested` and `not_sample_ingested` stay retryable.

## Verify dead-end remediation

Dead-end recovery is described in the [sync_timedb state machine](../../reference/sync-timedb-state-machine.md).
Check these lines per tier:

| Tier | Expected lines                                                                                                                                                                                                                                                                                                                                                                       |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| T0   | `dead_end migration snapshot verified` (or an ERROR plus report-mode lines), `queue_orchestrator day_remainder rebuilt ingest=... append=...`, `quarantine boot restore restored=...`, `Day raw removal reopen done ... reason=blocking_raw_on_disk` for frozen days                                                                                                                 |
| T1   | Every dead letter carries `cycle` and `readmit_at`; `queue_orchestrator dead_letter readmit n=` each idle pass while entries are due; `skipped_seal_invalid` counts fall as frozen days reseal; `tar salvaged to valid prefix` (or `archive_space_admit deny ... reason=env:ENOSPC`) for truncated tars; `.sync_timedb_tar_salvage/` and `*.pre-restore.*` empty after the day seals |
| T2   | Archive free space rises; each `sealed integrity sweep failed` line maps to a `.sync_timedb_sealed_integrity.json` row with `action=`; `ERROR: queue_orchestrator dead_letter terminal` and `reverify_cycles exhausted` are the only operator-action signatures                                                                                                                      |

A `.sync_timedb_sealed_integrity.json` row with `action=operator_restore_needed` needs a restore of that day's archive from tape.

## Verify the Codon parse bridge

The pipeline image bakes the Codon ingest parser in the Dockerfile stage `codon-build` of `services-conf/pipeline_web.Dockerfile`, at the Codon release pinned by `CODON_VERSION`, by running `hpcperfstats/dbload/codon/build_codon_ext.sh`.
The runtime copies it to `/opt/hpcperfstats/codon_parse`, which supervisord puts on `PYTHONPATH` for `sync_timedb`; the bridge also adds that directory to `sys.path` when it exists.
Rebuild it with `./scripts/rebuild_pipeline.sh`; never prebuild the `.so` on the host or copy it into the build context.

When the extension is missing, ingest uses the CPython `IncrementalStatsParser` and the stall signatures are the same.
When it loads, file parses run in Codon worker processes (`codon_mode=process_pool`), one per ingest thread.
The worker count is `[PIPELINE] sync_ingest_pool_processes` (default `64`), capped at `MAX_CODON_SLOTS` (128).
Each worker parses a file in `[PIPELINE] sync_codon_parse_flush_max_mib` windows and is replaced between files once its RSS passes `[PIPELINE] sync_codon_worker_recycle_rss_mib` (default `1536`).
Worker RSS counts toward the tree RSS roof, so on a small roof lower `sync_ingest_pool_processes` (for example to `32`) if ingest admission defers.
A Codon failure or a worker that dies mid-file (`codon worker EOF`) fails that file and never falls back to the CPython parser.

To verify the bridge after a redeploy:

1. Grep the startup line:

   ```bash
   podman-compose -p hpcperfstats logs pipeline 2>&1 \
     | grep 'codon_parse status=' | tail -5
   ```

2. Confirm `codon_parse status=enabled ... api=parse_stats_lines_codon gil=off contexts=<N> codon_mode=process_pool codonrt_serialize=0`.
   `<N>`: the slot count.

3. Confirm the daemon interpreter is still `/opt/python3.14t/bin/python3.14t` (T0 step 2) and that `sys._is_gil_enabled()` stays false after the Codon import.

4. Grep the census lines for `codon_slot_occ=` (held over total slots).

5. Escalate a sustained flood of `codon parse_stats_lines_codon failed`, `gil_enabled_after_import` or mid-file `RuntimeError` lines.
   `HPCPERFSTATS_CODON_PARSE_DISABLE=1` turns the bridge off in tests and soaks (startup logs `status=disabled reason=HPCPERFSTATS_CODON_PARSE_DISABLE`).

To check a rebuilt image without starting the stack:

```bash
podman run --rm --entrypoint /bin/bash \
  localhost/hpcperfstats:latest -lc '
  export PYTHONPATH=/opt/hpcperfstats/codon_parse
  M=hpcperfstats.dbload.lib.sync_timedb_codon_bridge
  /opt/python3.14t/bin/python -c "
import importlib
b = importlib.import_module(\"$M\")
print(b.log_codon_parse_startup())"
'
```

Codon is not a fix for day_close, lock or discover stalls.
The micro A/B `tests/experiments/codon/run_codon_ab_baseline.sh` writes `test_runs/codon_ab_*.log` and passes on `gate_1p25x=True`.
Full-load Codon off and on soaks use `--loaded48` from [Run sync_timedb benchmarks](../develop/run-sync-timedb-benchmarks.md).

## Troubleshooting

Each symptom names its signature in [sync_timedb stall signatures](../../reference/sync-timedb-stall-signatures.md).

### Ingest fails with gnu tar tvf failed

Symptom: `queue_orchestrator ingest fail ... detail=gnu tar tvf failed path=.../<DAY>.tar`, often with `Unexpected EOF`.

```bash
P='daily_tar_repair caller=|archive_day_ingest_skip'
P="$P|open_tar_gnu_tvf_failed|gnu tar tvf failed path="
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep -E "$P" | tail -80
```

Expect `daily_tar_repair caller=<CALLER> outcome=ok` and then ingest, with `open_tar_gnu_tvf_failed` once per day.
Repeated ingest fails with no `daily_tar_repair` attempt on a day whose sibling `.tar` exists is a bug to escalate.

`<DAY>`: the calendar day `YYYY-MM-DD`; `<CALLER>`: `ingest_membership`, `populate_tar_scan` or an `append_` caller.

### Ingest per-file timeouts or slow writes

Symptom: `ingest per-file timeout` lines, or outcome lines with a large `postgres_s=`.

```bash
P='DB lock wait|ingest per-file timeout|ingest fail .*TimeoutError'
P="$P|file_complete_ingest_mark recorded|idle_stall|advancing=false"
P="$P|tar append idle stall|canceling statement"
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep -E "$P" | tail -80
podman-compose -p hpcperfstats exec db psql -h localhost \
  -U hpcperfstats -c "SELECT pid, state, wait_event_type,
  wait_event, left(query, 80) FROM pg_stat_activity
  WHERE datname = current_database()
  AND query ~ '(host|proc)_data' ORDER BY pid;"
```

`pg_stat_activity` may show up to `sync_ingest_pool_processes` concurrent ingest inserts plus listend.
If Timescale insert waits overload the site, lower `[PIPELINE] sync_ingest_pool_processes`.
Do not raise `[PORTAL] db_statement_timeout_ms` for `host_data_ingest_stage` statement timeouts; write-stage sessions set their own timeout.

To print the effective stall clocks:

```bash
podman-compose -p hpcperfstats exec pipeline \
  su hpcperfstats -c 'python3 -c "
from hpcperfstats.dbload.lib import conf_parser as cfg
print(\"stall_idle_s\", cfg.get_sync_ingest_stall_idle_s())
print(\"max_s\", cfg.get_sync_ingest_per_file_timeout_max_s())
"'
```

### Two restores of one daily tar

Symptom: two `zstd -d -o .../<DAY>.tar.decomp.tmp` processes, or two `daily_tar_restore begin` lines for one day without an owner `end`.

`zstd -d -c` is a members-store populate and may run once per day in parallel across days.
`zstd -d -o ...decomp.tmp` is a sealed-to-tar restore (`decompress_compressed_to_tar`) and runs at most once per day site-wide.
Extra day_close workers log `day_close yield ... reason=daily_tar_restore` and wait.

```bash
P='daily_tar_restore (begin|end)|reason=daily_tar_restore'
P="$P|archive decompress restore begin"
P="$P|Archive members cache invalidated.*reason=tar_restore"
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep -E "$P" | tail -60
podman-compose -p hpcperfstats exec pipeline \
  sh -c 'ps -eo pid,etime,args | grep "[z]std -d"'
```

After a restore, expect `Archive members cache invalidated ... reason=tar_restore_pre` and then `reason=tar_restore`, followed by a cold populate for that day.

To clear stale restore files, stop the pipeline, delete `*.tar.decomp.tmp` under `daily_archive_dir`, and start it again:

```bash
podman-compose -p hpcperfstats stop pipeline
podman-compose -p hpcperfstats run --rm --no-deps pipeline \
  su hpcperfstats -c 'python3 -c "
import glob, os
from hpcperfstats.dbload.lib import conf_parser as cfg
d = cfg.get_daily_archive_dir_path()
for p in glob.glob(os.path.join(d, \"*.tar.decomp.tmp\")):
    print(\"remove\", p)
    os.remove(p)
"'
podman-compose -p hpcperfstats up -d pipeline
```

### A day_close write lock looks held

Symptom: `day_close defer ... reason=write_lock_contended`, or `*.fnctl.lock` files under `daily_archive_dir`.

A lock sidecar file is not proof of a held lock.
Probe the lock from inside the container (`fuser` is often missing from the image; `lsof` is installed):

```bash
podman-compose -p hpcperfstats exec pipeline \
  su hpcperfstats -c 'python3 -c "
import os
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.file_locking import (
    try_file_write_lock)
tar = os.path.join(cfg.get_daily_archive_dir_path(),
                   \"<DAY>.tar\")
try:
    with try_file_write_lock(tar):
        print(\"OK_uncontended\", tar)
except Exception as e:
    print(\"contended\", type(e).__name__, str(e)[:120])
"'
```

`OK_uncontended` with a leftover sidecar is not a stall.
Hours of `write_lock_contended` while the probe still fails mean a live holder is stuck.

### Cascading already in progress inserts

Symptom: many `error in single host_data insert:` or `error in single proc_data insert:` lines with `another command is already in progress` for one file.

```bash
P='another command is already in progress'
P="$P|error in single (host_data|proc_data) insert"
podman-compose -p hpcperfstats logs pipeline 2>&1 \
  | grep -E "$P" | tail -80
```

A rare single line per file is acceptable; dozens per file mean the write path did not reset its connection after a failed bulk insert.

### Many days need members-store reassessment

After a mass tar crash, invalidate the member maps with the host CLI and restart `pipeline`, as in [Archive members store](../../reference/compose-services.md#archive-members-store).
After the restart, expect a cold populate for each invalidated day, not a warm map.

### Ingest rate looks low

Capture a timestamped log and read the rate, as in [Connections and ingest rate](../../reference/compose-services.md#connections-and-ingest-rate):

```bash
podman-compose -p hpcperfstats logs --timestamps pipeline \
  > pipeline.log 2>&1
../.venv/bin/python3 scripts/measure_pipeline_ingest_rate.py \
  --log-file pipeline.log --since-minutes 480
```

The default window starts at the last `queue_orchestrator ingest write stage started`; `--include-startup` widens it.
With `[PIPELINE] sync_ingest_telemetry = yes` in the site INI (never a pipeline environment variable), read `decision_next_dense=` and `dense_tier_top_parse_hold=` first.
Ignore `decision_next=write_timescale_path` when `pg_stat_activity` shows idle `ClientRead` sessions.

## Related

- [sync_timedb stall signatures](../../reference/sync-timedb-stall-signatures.md)
- [sync_timedb regression battery](../develop/run-tests.md#sync_timedb-regression-battery)
- [Compose services: pipeline diagnostics](../../reference/compose-services.md#pipeline-diagnostics)
- [Pipeline concurrency](../../explanation/pipeline-concurrency.md)
- [Upgrade the stack](upgrade-the-stack.md)
