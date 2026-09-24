# Sync timedb campaign patch index

Recovered product patches for loaded-48 re-A/B (see
`.cursor/plans/loaded48-retest-nonlanded.plan.md`). Unified diffs live under
gitignored `test_runs/campaign_patches/` (not committed). Per-wave notes:
`test_runs/campaign_patches/*.NOTES.md` and
`test_runs/campaign_patches/contention_INDEX.md`.

Recovery sources (2026-09-19): E6 feed_line from prior campaign transcript;
contention ×9 from contention A/B transcript windows. E7 restored into product
tree (user keep); `e7_proc_build.patch` is the applied snapshot.

| Patch file | Source plan | In tree | `git apply --check` (HEAD) | Apply order |
| --- | --- | --- | --- | --- |
| `e7_proc_build.patch` | e7-proc-build | **yes** (operator keep) | fails (already applied) | 1 |
| `e6_feed_line.patch` | campaign-e6 | no | fails (context drift) | 2 |
| `contention_park_resume.patch` | contention-ab | no | fails (context drift) | 3 |
| `contention_manifest_io.patch` | contention-ab | **yes** (loaded96 land 2026-09-24) | applied + concurrency fixes | 4 |
| `contention_members_shard.patch` | contention-ab | **yes** (loaded96 land 2026-09-24) | applied | 5 |
| `contention_claim_heap.patch` | contention-ab | **yes** (loaded96 land 2026-09-24) | applied | 6 |
| `contention_tar_ex.patch` | contention-ab | **yes** (restaged 2026-09-24) | restaged surgically | 7 |
| `contention_pool_split.patch` | contention-ab | no | see NOTES | 8 |
| `contention_log_drain.patch` | contention-ab | **yes** (loaded96 land 2026-09-24) | applied + flush barrier | 9 |
| `contention_discover.patch` | contention-ab | no | see NOTES | 10 |
| `contention_telem_tls.patch` | contention-ab | no | see NOTES | 11 |

Already landed (not non-landed): `caches`, `thread_id`, plus loaded-96 land set above.

**Loaded-96 land (2026-09-24):** operator directed permanent land of
`members_shard`, `log_drain`, `manifest_io`, `claim_heap`, `tar_ex` with **no
revert**. Senior concurrency review **pass** after fixing manifest_io
unbound-snap / save-outside-lock correctness and log_drain flush barrier.
Compare runner: `tests/run_loaded96_landed_compare.sh` (WIDTH=96, 3h vs pinned
H1 `a20a234d…`; report-only gate).

**Before each loaded-48 candidate arm:** restage the wave from its `.patch` +
`.NOTES.md` (often `git apply --3way` or hand-merge on drifted hunks); run that
wave’s host unit tests; then
`HPCPERFSTATS_LOADED48_HOURS=6 … --e6|--e7|--contention`.

Loaded-48 refill mixes **small / medium / large** corpus size terciles
(`refill_corpus_epochs(..., mix_size_tiers=True)`).

```bash
cd HPCPerfStats
# Smoke:
HPCPERFSTATS_LOADED48_HOURS=0.1 tests/run_sync_timedb_benchmark_workflow.sh --loaded48
# Full baseline:
HPCPERFSTATS_LOADED48_HOURS=6 tests/run_sync_timedb_benchmark_workflow.sh --loaded48
```
