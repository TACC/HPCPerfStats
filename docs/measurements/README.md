# Committed monitor measurements

Measurement records that a paper or report cites. Raw run logs stay under `test_runs/`.

| File | What it is |
|---|---|
| `monitor_overhead_stampede3_spr_2026-08-14.md` | Historical record: pidstat 10 s × 60 on one Stampede3 SPR node (CPU only). Kept as-is; superseded for new numbers by the overhead profile below. |
| `hpcperfstatsd_pidstat_c512-122_stampede3_spr_2026-08-14.txt` | Raw pidstat output for that record. |
| `monitor_overhead_campaign_v31_2026-11-26.md` | Campaign summary: monitor 3.1 overhead profiles on 12 node types (daemon CPU, CPU counter rotation, counterfactual checks, other perf-counter users), with limits and citation wording. |
| `overhead_profile/` | One directory per node of that campaign, the cross-node tables (`profiles.md` / `.csv` / `.tex`) and the comparability report (`campaign_v31_compare.md`). |

Kept current by `monitor/cursor-rules/monitor-measurement-records.mdc`: every new or rerun
measurement, counterfactual check, or harness change that alters a reported number lands here in
the same task.

## Per-architecture overhead profiles

Produced by `monitor/measurement/bin/run_overhead_profile.sh` (or
`monitor/scripts/prepare_rpmbuild_dirs.sh --measure-overhead`) on an idle, reserved node per
CPU architecture; procedure in `monitor/measurement/RUNBOOK.md` section 0.

After a run, commit one directory per architecture:

```text
docs/measurements/overhead_profile/<host>_<arch>_<cpu-slug>_<UTC stamp>/
  profile.json                       # schema v1; every slot measured or NOT_MEASURED
  report.md
  profile_row.csv
  counter_rotation_<host>_<UTC>.txt  # bin/counter_rotation_check.sh after the run (0 or more)
```

`<cpu-slug>` is the CPU model plus the accelerator when one distinguishes the node type
(`xeon-8468-h100`, `xeon-8468-pvc`, `neoverse-v2-gh200`). A campaign also commits its
`compare_runs.py` report as `overhead_profile/campaign_<name>_compare.md` and one summary record
`monitor_overhead_campaign_<name>_<date>.md` (same sections as the 2026-08-14 record: Summary,
What this supports, Limits, Suggested citation wording).

Keep the full `raw/` tree with the run archive (it can be large; every number in
`profile.json` is recomputable from it with `bin/summarize_profile.py`). Do not commit
profiles flagged `not_idle` (smoke runs). Build the cross-architecture table with
`bin/aggregate_profiles.py --out <dir> docs/measurements/overhead_profile/*/profile.json`, and
copy each `profile.json` into `tests/site_measurement/results/` for `emit_paper_facts.py`.
