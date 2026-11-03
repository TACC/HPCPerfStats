# Committed monitor measurements

Measurement records that a paper or report cites. Raw run logs stay under `test_runs/`.

| File | What it is |
|---|---|
| `monitor_overhead_stampede3_spr_2026-08-14.md` | Historical record: pidstat 10 s × 60 on one Stampede3 SPR node (CPU only). Kept as-is; superseded for new numbers by the overhead profile below. |
| `hpcperfstatsd_pidstat_c512-122_stampede3_spr_2026-08-14.txt` | Raw pidstat output for that record. |

## Per-architecture overhead profiles

Produced by `monitor/measurement/bin/run_overhead_profile.sh` (or
`monitor/scripts/prepare_rpmbuild_dirs.sh --measure-overhead`) on an idle, reserved node per
CPU architecture; procedure in `monitor/measurement/RUNBOOK.md` section 0.

After a run, commit one directory per architecture:

```text
docs/measurements/overhead_profile/<host>_<arch>_<cpu-model>_<UTC stamp>/
  profile.json       # schema v1; every slot measured or NOT_MEASURED
  report.md
  profile_row.csv
```

Keep the full `raw/` tree with the run archive (it can be large; every number in
`profile.json` is recomputable from it with `bin/summarize_profile.py`). Do not commit
profiles flagged `not_idle` (smoke runs). Build the cross-architecture table with
`bin/aggregate_profiles.py --out <dir> docs/measurements/overhead_profile/*/profile.json`, and
copy each `profile.json` into `tests/site_measurement/results/` for `emit_paper_facts.py`.
