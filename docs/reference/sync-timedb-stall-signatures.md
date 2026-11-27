# sync_timedb stall signatures

This page lists the `sync_timedb` log lines, census tokens, sidecars and thread titles that show whether ingest, archive and day_close make progress.
It is for operators and developers reading pipeline logs; the checks that use them are in [Verify sync_timedb after a deploy](../how-to/operate/verify-sync-timedb.md).

Every token is a literal from the code at `HEAD`; `...` stands for fields left out.

## Census sidecars

The orchestrator keeps its census on disk, not in Valkey.
An empty job-store snapshot does not mean the site is caught up: reconstruct predicates over disk and the database decide that.

| Path                                                    | Holds                                                                        |
| ------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `<ARCHIVE_DIR>/.sync_timedb_job_store.json`             | Job-store snapshot: ingest, append, discover and day_close queues and leases |
| `<ARCHIVE_DIR>/.sync_timedb_archive_members/<DAY>.json` | Members-store map, skip and degraded state for one day                       |
| `<ARCHIVE_DIR>/.sync_timedb_zero_host_ingest_mark.json` | Zero-host ingest marks for the archive and delete DB gate                    |
| `<DAILY_DIR>/.sync_timedb_sealed_integrity.json`        | Sealed-archive integrity sweep results, one row per failure with `action=`   |
| `<DAILY_DIR>/.sync_timedb_tar_salvage/`                 | Truncated-tar salvage backups; empty after post-seal parity                  |

`<ARCHIVE_DIR>`: `[PIPELINE] archive_dir`; `<DAILY_DIR>`: `[PIPELINE] daily_archive_dir`.
`<DAY>`: the calendar day `YYYY-MM-DD`.

## Thread titles and log prefixes

Each long-lived thread sets the title `sync_timedb.py [thread:<ROLE>]` and the log prefix `[sync_timedb:thread:<ROLE>]`.
The kernel keeps only the first 15 bytes of a thread name, so `ps -L` shows `sync_timedb.py` for every thread; count roles from log prefixes.
The process title stays `sync_timedb.py [main]`.

| Role                      | Work                                                     |
| ------------------------- | -------------------------------------------------------- |
| `populate-pool`           | Members-store populate from the daily tar or sealed file |
| `ingest-pool`             | Raw file parse and ingest                                |
| `ingest-write-pool`       | Database write stage                                     |
| `ingest-coordinator`      | Ingest fill, claims and ACKs                             |
| `append-coordinator`      | Daily tar append jobs                                    |
| `day-close-coordinator`   | day_close claims                                         |
| `day-close`               | day_close workers                                        |
| `reconstruct-coordinator` | Idle reconstruct passes and progress counters            |
| `integrity-sweep`         | Sealed-archive integrity sweep                           |

`<ROLE>`: one of the roles in the table, for example `[thread:populate-pool]`.
The Python thread names `discover-bg` (background discover walk) and `job-store-persist` (the only writer of the job-store sidecar) show in `py-spy dump`.

## Orchestrator summary lines

`queue_orchestrator progress day=` prints omit-zeros day counters every interval:

| Counter                                       | Meaning                                                                              |
| --------------------------------------------- | ------------------------------------------------------------------------------------ |
| `gate_skip=`, `ingest_handoff=`               | Gate skips and handoffs to ingest                                                    |
| `dc_run=`                                     | A day_close slot is busy, including a long pre-seal verify                           |
| `complete=`                                   | Day finished: filesystem complete and past `[PIPELINE] sync_day_close_min_age_hours` |
| `incomplete_raw=`                             | Closed raw remains; the day is requeued, not acknowledged                            |
| `deferred_age=`, `yielded=`, `verify_failed=` | day_close deferred by age, yielded cooperatively, or failed verify                   |
| `sealed=`, `tar_delete=`                      | Seal and tar drop ran                                                                |
| `reconstruct_enq=`, `incomplete_seen=`        | Reconstruct found incomplete days                                                    |

Day-close identities are full daily tar paths, and `day_close=4/61` on a status line is inflight over queued jobs, not a day count.

`queue_orchestrator status` prints `<KIND>=<INFLIGHT>/<QUEUED>` per kind and always includes `ingest_hot=` and `ingest_catchup=`, even idle (`ingest_hot=0/0`, `ingest_catchup=0/0`).
It also prints `busy=`, `orphan_inflight`, `<KIND>_q_delta=`, `oldest_day=`, `oldest_age_s=` and, when a fill submits nothing, `fill_block=`.
Do not grep `ingest_hot` alone as a stall probe.

| `fill_block=` value                                   | Fill failure                                           |
| ----------------------------------------------------- | ------------------------------------------------------ |
| `claim_none`                                          | No claimable ingest job                                |
| `skip_missing`, `skip_reband`, `skip_fp`, `skip_lock` | Path missing, re-banded, fingerprint changed or locked |
| `band_cap`                                            | Hot or catch-up band at its cap                        |
| `submit_err`                                          | Submit raised                                          |
| `skip_rss_pressure`                                   | Tree RSS at the roof (`rss_hold`)                      |

`queue_orchestrator census` prints every 60 seconds:

- `<KIND>=<INFLIGHT>/<QUEUED>` per kind and `busy=`.
- `total_ingested=`: successful ingests with rows written since process start, including `0`, excluding `db_skip`.
- `total_completed=`: every successful ACK since process start, including `db_skip` and other archive-complete outcomes.
- `codon_slot_occ=<HELD>/<N>` when the Codon bridge is on, and `fill_block=` while set.

A rising `total_completed` means files complete even when `ingest=0/0` or `total_ingested` is frozen during `db_skip` catch-up.
A frozen `total_completed` with `ingest=0/0` is idle ingest.
A restart resets both counters to 0.
An ingest-zero window between `stuck_cohort_recycle` and `ingest pool recycled via pause protocol` is the recycle join.

`<KIND>`: `ingest`, `append`, `discover` or `day_close`; `<INFLIGHT>`, `<QUEUED>`, `<HELD>`, `<N>`: counts.

The ingest pool splits into a hot band and a catch-up band: `hot_cap = (2 * pool) // 3` and `catchup_cap` takes the rest (`ingest_band_slot_caps`; 64 slots give 42 and 22).

## Ingest lines

| Line or token                                                                                                       | Meaning                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `ingest file path=... outcome=... elapsed_s=... timeout_s=... ingest_ok=... archive=... db_skip=... size_bytes=...` | One line per file                                                                                           |
| `parse_elapsed_s=`, `postgres_s=`, `elapsed_s=`                                                                     | Parse time, ORM write time, total wall time                                                                 |
| `ingest per-file timeout`                                                                                           | Budget expired; includes `size_bytes=` and `bytes_per_s=`; the file soft-requeues (`outcome=timeout`)       |
| `idle_stall`, `advancing=false`, `tar append idle stall`                                                            | Heartbeat stopped for `[PIPELINE] sync_ingest_stall_idle_s`; the file soft-requeues                         |
| `ingest fill empty deep_queue`                                                                                      | Local inflight is zero but the score map is deep; with `fill_block=`, `stats=`, `hot_used=`, `catch_used=`  |
| `ingest fill under-capacity`                                                                                        | Inflight below the pool, deep map, fill submitted 0; same tokens                                            |
| `queue_orchestrator ingest defer` with `transient defer open-tar repair during append inflight`                     | Expected while append writers hold the day                                                                  |
| `queue_orchestrator dead_letter readmit n=`                                                                         | Dead letters re-admitted after their `cycle` / `readmit_at` cooldown                                        |
| `ERROR: queue_orchestrator dead_letter terminal`                                                                    | Dead letter out of cycles; operator action                                                                  |
| `stuck_cohort_recycle`                                                                                              | Census `oldest_inflight_s` past `[PIPELINE] sync_ingest_stuck_inflight_recycle_s` (default `3600`) recycled |
| `file_complete_ingest_mark recorded`                                                                                | One INFO per successful path                                                                                |
| `zero_host_ingest_mark recorded`                                                                                    | Zero-host mark written for the DB gate                                                                      |
| `not_head_tail_ingested`, `skipped_not_head_tail_ingested`                                                          | DB gate: head or tail seconds not yet in `host_data`                                                        |
| `daily_tar_repair caller=<CALLER> outcome=ok`                                                                       | Truncated open tar repaired before ingest; `open_tar_gnu_tvf_failed` logs once per day                      |

`<CALLER>`: `ingest_membership`, `populate_tar_scan` or an `append_` caller.

Long ingest, tar and zstd work logs progress as `progress stage=... advancing=true|false idle_s=... metric=bytes|lines|members`; `idle_s` counts seconds since the last progress, and liveness alone never resets it.

With `[PIPELINE] sync_ingest_telemetry = yes`, `ingest file path=` lines add parse-stage tokens (`lock_s`, `decode_s`, `feed_s`, `proc_merge_s`, `hw_df_s`, `proc_df_s`, `build_df_s`, `delta_s`, `collapse_normalize_s`, `collapse_host_sum_s`, `collapse_ccm_s`, `collapse_gpu_s`, `collapse_concat_s`, `collapse_sort_s`, `collapse_s`, `arc_s`, `concat_s`, `start_s`, `chunk_setup_s`, `take_cols_s`, `heap_release_s`, `jid_invalidate_s`, `stages_sum_s`, and `parse_unaccounted_s` = `parse_elapsed_s - stages_sum_s - postgres_s`), write tokens (`orm_materialize_s`, `orm_bulk_prep_s`, `batch_iter_s`, `write_setup_s`, `db_execute_s`, `db_commit_s`, `copy_s`, `conflict_insert_s`), `overlap_headroom_s` and `orm_rows`.
Every line carries `host_insert_arm=` and `proc_insert_arm=`.
`scripts/measure_pipeline_ingest_rate.py` reads them and prints `decision_next_dense=`, `dense_tier_top_parse_hold=`, `decision_next=` and `mid_tier_name=`; a tier needs `_MID_TIER_MIN_SAMPLES` files, `telem_incomplete_re_soak` means the phase samples are missing, and `parse_unaccounted_investigate` flags a large residual.
Size tiers are `lt_8mib`, `8mib_64mib`, `64mib_1gib`, `1_4gib` and `ge_4gib`; `decision_next=write_timescale_path` fires when `postgres_frac` is high.

## Memory admission lines

| Line or token                                                                       | Meaning                                                                                                                                                      |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `queue_orchestrator rss_hold rss_mib=... limit_mib=... inflight=<N>`                | Tree RSS at the roof (`[PIPELINE] sync_process_tree_rss_limit_cgroup_pct` of `memory.max`); admission pauses, at most one line per 30 s with `suppressed_n=` |
| `queue_orchestrator rss_release rss_mib_before=... rss_mib_after=... limit_mib=...` | Nothing in flight and RSS over the roof, so one `gc.collect()` and `malloc_trim` ran; a small gap means the heap is live                                     |
| `skip_cgroup_headroom`, `skip_budget`, `skip_file_cache_pressure`                   | Admission skipped under cgroup, budget or file-cache pressure; never a `dead_letter` reason                                                                  |
| `file_cache_cgroup_pct=`, `file_cache_cfg_mib=`, `file_cache_ok=`, `file_mib=`      | File-cache gate census when `[PIPELINE] sync_cgroup_admit_max_file_cache_cgroup_pct` (default `60`) is above 0                                               |
| `admit_alone`, `alone_progress=`                                                    | With nothing in flight, one ingest or append is admitted despite pressure                                                                                    |
| `sync_timedb_mem_telemetry:`                                                        | Memory telemetry lines when `[PIPELINE] sync_timedb_mem_telemetry = yes`                                                                                     |

A sustained `rss_hold` with `inflight=0`, or with `inflight` not falling, is a gate bug: an idle pool always admits.
A small `rss_release` gap means you raise the roof percentage or lower `[PIPELINE] sync_ingest_pool_processes`; with `[PIPELINE] sync_ingest_malloc_trim_after_file = no`, holds are expected.

## Archive and day_close lines

| Line or token                                                                                                                                      | Meaning                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `INFO: archive_job_done day=... outcome=... tar_bytes=... members_source=... mapped=... to_add=... appended=... cloned=... copied=... write_s=...` | One line per append job                                                                                                                                                                     |
| `cloned=` equal to `appended=`, `copied=0`                                                                                                         | Reflink clone append; `copied>0` means the copy fallback ran (no reflink on that filesystem)                                                                                                |
| `archive append recovered tar= old_end= dropped_bytes=`                                                                                            | A killed append rolled back at the next startup, lock holder or sweep (`archive scratch recovered journal`)                                                                                 |
| `Gate tail metadata`                                                                                                                               | Gate tail metadata batch for paths that need it                                                                                                                                             |
| `queue_orchestrator day_close claim`, `stage_enter`, `stage_exit`, `vacate ... outcome=`                                                           | day_close lifecycle; `yielded` vacates are rate limited                                                                                                                                     |
| `stage_progress ... advancing=true` with `members_done` rising                                                                                     | Long merge that is progressing, not a stall                                                                                                                                                 |
| `stall_confirmed`                                                                                                                                  | Confirmed no progress; the worker yields its lease cooperatively                                                                                                                            |
| `wait_on_ingest skip_yield reason=append_idle`                                                                                                     | Idle append and no hot ingest: day_close continues instead of yielding                                                                                                                      |
| `queue_orchestrator day_close tar_drop`                                                                                                            | Open tar dropped after seal and raw delete                                                                                                                                                  |
| `pre_seal_verify classify progress`                                                                                                                | Pre-seal classify advancing on a large day                                                                                                                                                  |
| `daily_tar_restore begin`, `daily_tar_restore end`                                                                                                 | Exclusive sealed-to-tar restore; at most one owner per day                                                                                                                                  |
| `archive decompress restore begin`                                                                                                                 | Restore started before a long decompress                                                                                                                                                    |
| `Archive members cache invalidated ... reason=tar_restore_pre`, `reason=tar_restore`                                                               | Member maps invalidated around a restore                                                                                                                                                    |
| `day_close yield tar=... phase=... reason=<REASON>`                                                                                                | day_close yields to `populate_active`, `ingest_tar_hot` or `daily_tar_restore`                                                                                                              |
| `janitor: day_close defer ... reason=write_lock_contended`                                                                                         | A live reader or writer holds the exclusive write lock on the daily `.tar`; the legacy gzip migrate also logs `janitor: day_close defer ... phase=migrate_legacy_gz` with a hot-path reason |
| `defer_cap_exceeded`                                                                                                                               | Defer streak passed its cap                                                                                                                                                                 |

`<REASON>`: one of the listed yield reasons.

A smaller `[PIPELINE] sync_timedb_tar_append_batch_size` (default `256`) shortens each append job; `[PIPELINE] sync_archive_pool_processes` (default `4`) sets how many days append at once.

## Members-store populate lines

| Line                                                                                                                            | Meaning                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `transient tar populate EOF during hot/append`, then `prefer sealed fallback` or `populate sealed fallback after dirty-tar EOF` | A dirty open tar fell back to the sealed copy; a forever loop of the EOF line alone is a stall |
| `transient fnctl read lock timeout during tar populate`                                                                         | Populate waited on a tar lock; healthy when `populate_source=tar` follows                      |
| `WARNING: populate-pool unavailable is not an immediate L2 fatal`                                                               | The populate pool restarts and ingest waits within `populate_max_seconds`; recoverable         |
| `populate_recovering`                                                                                                           | Prewarm summary token: populate recovered after a retry                                        |
| `ERROR: archive members populate stalled or timed out`                                                                          | Populate gave up; escalate                                                                     |
| `Quarantined unparsable raw` with `outcome=quarantine`                                                                          | File moved to quarantine; its `fail_reason` never contains `ingest per-file timeout`           |

## Lines that must not appear

These lines mark a regression:

| Line                                                                                     | Means                                                                                |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `DB lock wait`                                                                           | An in-process lock around ORM writes; it must not appear on the ingest write path    |
| `canceling statement due to statement timeout` on `host_data_ingest_stage`               | A write-stage session without its ingest statement timeout                           |
| Dozens of `another command is already in progress` per file                              | The write path did not roll back and reset its connection after a failed bulk insert |
| Two `zstd -d -o .../<DAY>.tar.decomp.tmp` processes for one day                          | Two concurrent restores of one daily tar                                             |
| `dead_letter kind=ingest ... ArchiveMembersStoreUnavailableError` growing during appends | Append-inflight defer not applied                                                    |

`DB lock wait` is not a Postgres lock: uniqueness comes from Postgres unique indexes with `ON CONFLICT`, and `postgres_s=` is the ORM write time.

## Stall patterns

Read 2 or 3 consecutive status and census lines before you call a stall:

| Pattern                                                                                                                   | Diagnosis                             |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Queues non-zero, queued delta near 0, no day ACKs                                                                         | Stall                                 |
| Inflight without `busy=`                                                                                                  | Orphan inflight                       |
| `gate_skip=` without `ingest_handoff=`                                                                                    | ACK thrash                            |
| Empty queues and no `reconstruct_enq=` or `incomplete_seen=` on a backlog site                                            | False done                            |
| Census `ingest=` at the full pool, deep map, frozen `total_completed`                                                     | Completion-rate stall                 |
| `ingest_catchup` inflight above `catchup_cap` while `ingest_hot` is queued deep                                           | Hot-band starvation                   |
| `ingest fill empty deep_queue ... local_inflight=0 ... fill_block=skip_file_cache_pressure` with frozen `total_completed` | Alone-progress admit missing          |
| `day_close=` sticky at `[PIPELINE] sync_day_close_max_inflight` with no `stage_enter` lines                               | day_close hang before its first stage |

For a completion-rate stall, compare `parse_elapsed_s=` with `postgres_s=`, check thread `wchan` (`futex_do_wait` across most threads means lock contention), and confirm `readlink /proc/<PID>/exe` is `/opt/python3.14t/bin/python3.14t`.
`<PID>`: the `sync_timedb.py [main]` process ID.

Do not fix a stall by raising `[PIPELINE] sync_day_close_max_inflight` or `[PIPELINE] sync_ingest_queue_max_size`, by capping ingest fill below `[PIPELINE] sync_ingest_pool_processes`, or by wiping persistence.
