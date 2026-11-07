# Node monitor measurement kit (HUST 2026)

Operator scripts and parsers for **compute-node** captures: the whole-machine overhead
profile of the release RPM (`bin/run_overhead_profile.sh`, or
`scripts/prepare_rpmbuild_dirs.sh --measure-overhead` to build and measure in one step),
`hpcperfstatsd` cadence, legacy pidstat overhead capture, OS jitter, microbenchmarks,
and the hardware-error **catalog** (coverage vs reviewer classes — not a new collector).

| Piece | Role |
|---|---|
| `bin/run_overhead_profile.sh` → `lib/orchestrator.py` | preflight (incl. busy-node refusal), install, discovery, OFF/NONE/ALL/SOLO blocks per governor (`--governors`, default `performance`; a comma list adds a leg set per governor), restore |
| `lib/cpufreq.py` | governor resolution per cpufreq driver, set/restore of `scaling_governor`, `scaling_cur_freq` summary |
| `bin/sample_proc.py` (`lib/procsample.py`, `nodesample.py`, `tcpinfo.py`, `rapl.py`) | 1 Hz daemon + node sampler |
| `src/fwq.c`, `lib/fwq.py` | Fixed Work Quanta OS-noise probe (compiled at run time) |
| `lib/profiler_lines.py` | `metric-profiler:` journal parser for the optional `--profile-collectors` leg |
| `lib/stats.py`, `collector_matrix.py`, `driver_map.py`, `schedule.py`, `leg_summary.py`, `profile_report.py` | statistics, per-driver table, report |
| `bin/summarize_profile.py`, `bin/aggregate_profiles.py` | rebuild a report; cross-architecture CSV/Markdown/LaTeX |

**Runbook:** [RUNBOOK.md](RUNBOOK.md)

Paper-facing numbers still must not be invented. Copy JSON from `results/` into the
shared ledger under [`tests/site_measurement/results/`](../../tests/site_measurement/README.md)
and run `tests/site_measurement/bin/emit_paper_facts.py` before editing the `.tex`.

## Local checks (no production node)

From this directory, with the workspace `.venv` if available:

```bash
./bin/run_local_checks.sh
```

Or from the Cursor workspace root:

```bash
.venv/bin/python3 -m pytest HPCPerfStats/monitor/measurement/tests/ -q
```
