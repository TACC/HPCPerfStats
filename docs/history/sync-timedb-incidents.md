# sync_timedb incidents

Historical record, 2026-11-27. Not maintained.

This record keeps the dated production evidence behind the rules in the [sync_timedb state machine](../reference/sync-timedb-state-machine.md) reference: host, date and count for each incident, plus the Phase 1 root-cause notes of 2026-10-26.
Host names are short (`01` = hpcperfstats01, and so on).

## Incident notes by state

### `_FULL_REDISCOVER_PENDING`

`_run_background_discover` logs `queue_orchestrator discover failed window= err=` because nothing reads its Future; before that log existed, a failed boot whole-archive walk was silent and left every older file for the 6 h periodic walk (hpcperfstats01 2026-10-31).

### `_BOOT_FULL_DISCOVER_PENDING`

Undiscovered raw for a just-sealed day forced full reseals (hpcperfstats04 2026-10-29).

### day_close seal `reason=unarchived_raw`

Before the check, the seal saw only raw the manifest and queues knew about.
hpcperfstats04 2026-10-29: 08-25 sealed and deleted its raw without 189 never-queued files, then restored, appended and resealed.

### Space pressure

- hpcperfstats02 2026-10-30: 97% full, restores of 300-600 GB sealed days for appends.
- hpcperfstats01 2026-11-11: clearing 2T ended pressure, parallel append restores expanded eight sealed days (about 1.9T of tar against 151G of zst) within hours, and every later append was refused `restore_slot_busy`.
- 2026-11-11: the freeze landed; the restore slot and the one-day append hold that gated restores and copies under pressure were removed.
- Before 2026-11-26, X = 0 admitted every append batch, restores included, unmeasured.

### `_SEALING_TARS`

- Dedupe joined the sealing flag on 2026-11-05.
  hpcperfstats02 2026-11-05: six concurrent full rewrites of clean multi-hundred-GB tars; discover waited 60 s per 10-03 file, 239 timeouts in 4 h, holding `discover=1/1` with `ingest=0/0` for 3.5 h, and all four append writers cycled on the same locks.
- hpcperfstats01 2026-11-02: discover read sealing tars, 647 waits, about 11 hours, on 2026-07-31.
- hpcperfstats01 2026-11-02: 325 `soft_skip reason=open_tar_members_unavailable` rounds on 07-31 before sealing days were parked.

### day_close seal `reason=seal_slot_busy`

hpcperfstats01 2026-11-02: four multi-TB seals at about 50 MB/s each on a saturated shared volume freed nothing for 6+ hours.

### `_DISCOVER_UNREADABLE_TARS`

Before the flag the `tvf` error escaped and killed every whole-archive walk at that day.
hpcperfstats01 2026-11-11: `2026-10-14.tar`, rc=2 `Unexpected EOF in archive`, `discover failed window=all` every 10 min or so; no file of 10-14 or later was found by a whole walk.

### `_discover_frontier_day`

hpcperfstats04 2026-10-29: host-order walks queued 50k files across every day and kept 56 days restoring, appending and resealing every batch.

### Queue dead letter cap (DE-34)

Before the cap rule the trim kept the newest 5000 entries.
On hpcperfstats01 2026-11-13, 40,118 append dead letters (unreadable 10-18/10-23 tars whose repair the pressure gate refused, returned as a bare `False` batch failure) evicted live ingest cooldowns: 647 already-ingested files had no mark, queue entry or dead letter until the next whole-archive walk.
Space parks landed on 2026-11-14.

### Append soft requeue `reason=sealed_members_unavailable`

A sealed-only day with no valid sealed member map used to restore the whole sealed day (hpcperfstats01 2026-11-02).

### DE-11/23 clone append rollback

The clone writer journal (truncate to the journalled old end) landed on 2026-11-01.

### DE-24

hpcperfstats04 2026-10-29: 08-07/09/11/12 claimed and vacated `complete` about 157 times each per run.

### DE-26

hpcperfstats04 2026-10-29: 09-18 tar 10541 vs zst 10526 members, `post_seal_verify` failed every claim.

### DE-33

Found on hpcperfstats04 2026-10-27 (3000 cap vs 387k queued: 1,999 raw files of 2026-08-24 never queued, `ingest_handoff` counted them).

### DE-35

- hpcperfstats01 2026-11-26: 09-15 listed 416 of 17,132 raw files; about 82 TiB across 08-01..09-29.
- hpcperfstats01 2026-11-26: about 24% of on-disk raw per done sealed-only day was late unarchived raw, for example 08-06 files modified 09-12..13, about 20 TiB; operator decision: restore and reseal.

### DE-36

The `./` spelling was most likely written by the truncated-tar extract-recreate repair (fixed in DE-37).
hpcperfstats01 2026-11-26: 09-28, 09-30, 10-02..10-05, 10-17 and 10-24 each held one `skipped_size_mismatch` keyed `/./hpcperfstats/archive/...`, for example disk 324,925,904 vs member 75,476,480; seal `deferred_raw_not_in_tar`, `post_seal_verify` `tar vs sealed member mismatch (uncompressed count=19941 sealed count=1519)`, `incomplete_raw` 40-50 times a day; the 10-17 sealed map held 7457 of 7694 `./` names.

### DE-37

`_repair_truncated_daily_tar_via_extract_recreate` recreated with `-C <dir> .`, which wrote every member as `./hpcperfstats/...`, a likely source of the DE-36 spelling.

## Phase 1 RCA notes and re-verification (2026-10-26, code at 40e9850b+)

| Item | Finding | Status |
| --- | --- | --- |
| Origin of `skipped_seal_invalid` on frozen `done` days | Two observed causes: an unreadable open tar (01 2026-09-29, DE-11) and tar a strict superset of zst after post-seal appends (04 2026-08-23, DE-26); DE-25 then wrote `done` with raw on disk. | closed (operator findings #14-#15); remediated (reopen + reseal + salvage) |
| Second producer of `ArchiveMembersStoreUnavailableError` on non-`day_skip` days (01: 10-17/18/19/22) | Producers in code: `request_archive_members_populate_and_wait` (lookup returned no members: populate stalled/refused), `execute_archive_members_populate_for_canonical` and `_ensure_populate_scan_allowed` (populate on a forbidden pool / scan refused), `_ensure_open_tar_readable_for_ingest_lookup` (append in flight, repair yield, transient probe), `_prewarm_archive_members_for_days`, `mark_archive_day_ingest_skip_and_raise` (transient sealed reads). Non-`day_skip` days fit the populate-wait miss under the 267k-job backlog. Which one dominated on 01 cannot be told without logs (01 is down). | bounded by type (A11): any of them now requeues without a file attempt and escalates into the env cooldown; no further code change needed |
| Why the pipeline flagged 10-04/05/24 unreadable | The pipeline probe holds the read lock, so the operator-probe race does not apply. Candidates: a transient failure classified as corrupt (EAGAIN spawning `tar`, as seen with zstd on 01; DE-29) or a probe during an interrupted append (DE-23). Neither is provable without the lost logs. | remediated by the tri-state probe (transient never sets `day_skip`), append rollback, and the stale-flag clears (A4/G3, `day_skip_blocks_tar_write`) |
| DE-22 `ingested` with `ingest_ok=False` (04: 3 entries) | Root cause found: both ingest paths packed `outcome="ingested"` even when a DB chunk write failed (`ingest_ok=False`). | fixed: outcome `write_failed` (env requeue); regression `test_write_failed_outcome_is_environment_requeue` |
| DE-19 `cannot unpack non-iterable` | No quarantined file can be replayed (01/02 down; 04 has none, finding #7). Every write/submit path in that code returned a tuple; the producer was most likely in the parse path rewritten by the Codon commit `d52590cd`. | classification fixed (code faults never quarantine; `code_fault` restorable); real-file replay is an open handoff |
| 02 2023-05-08 sibling | Sealed archive intact; `day_skip` stale (findings #15-#16). | closed; G3 clears it |
| Medium-confidence rows | DE-9: reconcile returned `skip/zst_unreadable` and the seal overwrote an unreadable zst. DE-15: thread-pool futures abandoned by `terminate()` stay pending. Day-remainder key mismatch: confirmed (first-sample day vs filename epoch). Discarded verify executors: `_run_day_close_job` builds a coordinator per job; manifests are re-read from disk on state creation, so stale in-memory state is not written back. | verified; DE-9/15/32 remediated |
| Census cross-check (01/02/04 values) | Every observed status, reason and kind is in the inventory or the outcome exit table: statuses (section 2), `day_skip` kinds incl. `read_error`, dead-letter reasons (`append_failed`, `ArchiveMembersStoreUnavailableError`, `OperationalError`, `RuntimeError`, `OSError`, `PermissionError`, `parse_fail`, `skip_fp`, `skip_cgroup_headroom`, `ingest_mark_failed`, `ingested`) map to section 2 and 3 exits. | closed |

## Removed names (2026-10-26 release)

Removed with no production callers: `SyncFileState`, `_SYNC_STATE_TRANSITIONS`, `_transition_file_state`, `DayCloseManifestCoordinator`, `quarantine_unparsable_closed_raw_paths`, `remove_verified_archived_raw_files`, `run_supervisor_day_raw_removal_delete_pass`, `advance_raw_removal_blockers`, `promote_phase_if_verify_stage_ahead`, `discover_*_handoffs`.

## Operator census recipe

The sidecar census and deleted-vs-`.zst` loss check recipe of 2026-10-25 lived in the gitignored `test_runs/sync-timedb-dead-end-census-recipe-2026-10-25.md`, outside git.

## Planning IDs

The state machine reference once labelled its rules with plan IDs: A2 (queue dead letter), A3 (quarantine), A4 (authoritative-archive `day_skip` clear), A7 (`reverify_cycles`), A11 (type-based environment errors), A16 (dedupe space admit), A18 (sealed integrity sweep), G1 (`_requeue_bounded_without_attempt_bump` payload counter), G2 (`SYNC_TIMEDB_NEGATIVE_STATE_EXITS` outcome table), G3 (age-gated sealed-only `day_skip` re-check), RC9 (penalty score on reband).
Rules in `hpcperfstats/cursor-rules/` still cite some of them.
