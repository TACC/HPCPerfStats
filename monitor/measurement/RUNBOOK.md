# Node monitor measurement runbook

Operator instructions for **AUTHOR_NOTES** items that run on cluster **compute nodes**
(or a dedicated, reserved node for the overhead profile and jitter). Capture on the **same host class** you will
cite (Stampede3 SPR vs Horizon GPU are different machines and different site confs).

Copy result JSON into `monitor/measurement/results/` (gitignored), then merge into
`tests/site_measurement/results/` for `emit_paper_facts.py` (see
[`tests/site_measurement/RUNBOOK.md`](../../tests/site_measurement/RUNBOOK.md) § Paper ledger).

**Law:** if a script cannot measure it, the slot stays `NOT_MEASURED`.

Python: `python3` on the node, or `<workspace>/.venv/bin/python3` for local parser tests.
Make scripts executable (`chmod +x bin/*.sh bin/*.py`).

---

## 0. Whole-machine overhead profile (one command per architecture)

Builds the **release** RPM, installs it, runs it as the real `hpcperfstats.service`, and
writes a provenance-stamped metric suite. Run it on a node **reserved for this activity**,
one node per CPU architecture (x86_64 Intel, x86_64 AMD, aarch64 CPU-only classes).

From `HPCPerfStats/monitor/` on the reserved node (in `tmux` or under `nohup`; a full run
is up to 12 h):

```bash
./scripts/prepare_rpmbuild_dirs.sh --measure-overhead
# fleet hosts: the wrappers pass the flag through
./scripts/prepare_rpmbuild_stampede3.sh --measure-overhead --measure-arg --blocks --measure-arg 2
```

`--measure-overhead` checks `sudo -n true` and the build tools (`gcc make autoreconf
rpmbuild curl`, plus DCGM headers on aarch64) **before** building, runs the release
`rpmbuild -ba`, finds the one `rpmbuild/RPMS/<arch>/hpcperfstatsd-*.rpm`, then runs
`measurement/bin/run_overhead_profile.sh --rpm … --build-info rpmbuild/build-info` as root.
If sudo would prompt, it prints the exact root command instead. Pass harness options with
repeated `--measure-arg`. `--measure-overhead` and `--debug-build` are mutually exclusive.

Standalone (RPM already built):

```bash
./measurement/bin/run_overhead_profile.sh --dry-run --rpm rpmbuild/RPMS/x86_64/hpcperfstatsd-3.0-7.el9.x86_64.rpm
sudo ./measurement/bin/run_overhead_profile.sh --rpm rpmbuild/RPMS/x86_64/hpcperfstatsd-3.0-7.el9.x86_64.rpm \
  --build-info rpmbuild/build-info
```

### What it does

1. **Preflight** (fails closed): root; no other user's Slurm job on the node; 1-min load
   below 0.1 × nproc; ≥ 2 GiB free; the site conf must not set `disable_types` or a
   non-default `collection_profile`, and `/etc/sysconfig/hpcperfstatsd` must not set
   `HPCPERFSTATS_DISABLE_TYPES` / `HPCPERFSTATS_COLLECTION_PROFILE` (so ALL really is
   all); `<queue>_measure` must not exist with a consumer. perf, RAPL and PSI are probed
   and missing ones become `NOT_MEASURED`. `--force` lets a busy node run, flagged
   `not_idle: smoke only` (excluded from `aggregate_profiles.py`).
2. **Install**: saves the site conf, `rpm -Uvh --replacepkgs`, writes the site conf back
   (the spec lists it without `%config`, so the RPM overwrites it), checks `server`/`queue`.
   `restore.sh` is written to the result dir before any change. A fresh node needs
   `--conf PATH`. The measured package stays installed afterwards (reserved node).
3. **Discovery**: one 2-minute ALL start; the daemon's `Enabled types:` journal line is the
   active collector list. Each type maps to a **driver group** (`filesystem`, `likwid`,
   `dcgm`, `perf_event`, `ib_mad`, …; `lib/driver_map.py`); an unmapped type fails.
4. **Blocks** (default 2): OFF → NONE → ALL → SOLO(each group, seeded random order) → ALL →
   OFF (shared with the next block). Legs are `--leg-seconds` (1800) after a 60 s settle;
   OFF/NONE/ALL add a `--fwq-seconds` (300) FWQ phase on every CPU but the sampler's. The
   schedule auto-fits `--max-hours` (12) by shortening legs, dropping the second ALL, then
   single-block SOLOs; `--dry-run` prints the legs and projected end.
5. **Collector sets** come only from a runtime drop-in
   `/run/systemd/system/hpcperfstats.service.d/90-measure.conf`. ALL and OFF use the
   production unit and queue unchanged. NONE / SOLO / `--cadence-sweep` legs point
   `ExecStart` at `/run/hpcperfstats-measure/hpcperfstats.conf`, a copy of the site conf
   whose only change is `queue <queue>_measure` (CADENCE legs also change `sample_freq`).
   A copy is required because the daemon reads the conf after the CLI, so `-q` cannot
   override the conf queue. listend never consumes `_measure`, so no partial data reaches
   the database. Each leg's own `Enabled types:` line is checked against the intended set.
6. **Restore** (every exit path, including SIGINT/SIGTERM/SIGHUP): remove the drop-in,
   delete `<queue>_measure` through the management API (port 15672, conf credentials; if
   that fails it prints `rabbitmqctl delete_queue -p / <queue>_measure` and records
   `measure_queue_left: true`), restore the site conf and the service's active/enabled
   state. If the harness is killed with SIGKILL, run `restore.sh` from the result dir.

### Output: `results/<host>_<arch>_<cpu>_<UTC>/`

| File | Content |
|---|---|
| `profile.json` | schema v1; every metric `{value, unit, ci95, n, source}` or `{status: NOT_MEASURED, reason}` |
| `report.md` | headline table, per-driver table (SOLO − NONE paired within block, share of ALL, interaction residual), legs |
| `profile_row.csv` | one row of headline metrics |
| `raw/` | `samples.jsonl` per leg (1 Hz), FWQ quanta, `perf.csv`, journal excerpts, `provenance.json`, `run.json`, `commands.log` |

`bin/summarize_profile.py RESULT_DIR` rebuilds every output from `raw/` (it also runs after
each leg, so an interrupted run still has a valid partial report). Merge architectures:

```bash
python3 bin/aggregate_profiles.py --out results/cross_arch results/*/profile.json
# profiles.csv, profiles.md, profiles.tex (booktabs fragment)
```

### Per-architecture checklist

- [ ] Node reserved (your own allocation only), idle, CPU-only class.
- [ ] Site conf has no `disable_types` / non-default `collection_profile`.
- [ ] `--dry-run` projected end fits the reservation.
- [ ] Run finished: `report.md` has no `baseline_drift` warning and no contaminated legs.
- [ ] `measure_queue_left` is false (or the broker admin ran the printed command).
- [ ] Copy `profile.json` to `tests/site_measurement/results/` (it feeds the
      `overhead.*_collector_delta_cpu_pct` slots) and commit the profile under
      `docs/measurements/` (see its README).

### Optional: per-collector profile (`--profile-collectors`)

Adds one leg after the blocks that runs an `--enable-metric-profiler` build of the **same**
sources in the ALL configuration (production conf and queue, full payloads). The harness
builds it as root from `rpmbuild/SOURCES/hpcperfstats-*.tar.gz` under `raw/profiler-build/`
(never `monitor/.build-static`, never the installed RPM; about 15 min, counted in the
schedule), or takes a prebuilt `--profiler-binary`. The binary is copied to
`/run/hpcperfstats-measure/hpcperfstatsd-profiler`, and the leg is stopped with SIGINT so the
daemon prints its cumulative `metric-profiler:` report. `profile.json` `collector_profile` and
the `report.md` section "Instrumented collector profile" list CPU/wall/wait ms per call per
type and per driver group. Labelled **instrumented build, not production**: it ranks types
for diagnosis and never feeds a headline number.

```bash
./scripts/prepare_rpmbuild_dirs.sh --measure-overhead --measure-arg --profile-collectors
```

---

## 1. Monitor cadence (AUTHOR_NOTES §1)

On the **measured** compute node (the one you will pidstat):

```bash
cd monitor/measurement   # from HPCPerfStats git checkout
./bin/capture_monitor_cadence.sh
# or, after an overhead capture that already saved pidstat.txt:
./bin/capture_monitor_cadence.sh --pidstat /path/to/pidstat.txt
```

Reads `/etc/hpcperfstats/hpcperfstats.conf` (override with `HPCPERFSTATS_CONF`). Writes
`cadence.json` with `sample_freq`, `sample_freq_slow`, `send_freq`, `enable_slow_tier`.

Do **not** publish pidstat burst spacing as `sample_freq`. The paper already warns that
Horizon sample counts and Stampede3 busy bins are different quantities.

Repeat on Horizon if you cite Horizon jobs 2688 / 2699.

---

## 2. Daemon CPU, memory, I/O, network (AUTHOR_NOTES §2)

Same node, production `hpcperfstatsd`, ten-minute window matching the 2026-08-14 shape
(`pidstat` 10 s × 60):

```bash
./bin/capture_daemon_overhead.sh --interval 10 --count 60
# optional AMQP pcap (needs tcpdump + CAP_NET_RAW):
./bin/capture_daemon_overhead.sh --interval 10 --count 60 --tcpdump
```

Outputs `overhead.json`:

| Field | Source | Caveat |
|---|---|---|
| peak/average `%CPU` | pidstat | Percent of **one core**, not of the node |
| RSS / USS | `/proc/<pid>/status`, `smaps_rollup` | USS = private clean+dirty |
| `read_bytes` / `write_bytes` | `/proc/<pid>/io` | Block-layer storage |
| `rchar` / `wchar` | `/proc/<pid>/io` | Includes sockets |
| `total_tx_bytes` / `total_rx_bytes` | `/proc/net/dev` | **Namespace-wide upper bound**, not process-scoped |
| `ss_amqp` | `ss -tnp` port 5672 | Confirms the RabbitMQ connection |

Copy the printed directory into the paper audit folder (alongside `docs/measurements/` in
this repo if you want a second archive).

### Per-collector cost

Use the overhead profile (section 0). Its ALL / SOLO(driver) / NONE legs give the
per-driver cost table with CIs and the additivity residual. The old manual
`disable_types` protocol and its peak-%CPU subtraction were removed: peak deltas are one
outlier per leg, and editing the production conf is not needed (the profile uses a runtime
drop-in).

### OS jitter

```bash
./bin/capture_jitter.sh --label daemon_on --duration 60
# systemctl stop hpcperfstats  (dedicated node only)
./bin/capture_jitter.sh --label daemon_off --duration 60
python3 bin/summarize_jitter.py --on ON_DIR --off OFF_DIR --out results/jitter.json
```

`jitter.json` now carries p50/p99/p99.9 (from the `cyclictest -h` histogram) and
`p99_delta_us` next to the max. FWQ in the overhead profile is the primary noise metric.

### STREAM / IOR / OSU / one application

```bash
./bin/run_microbench_on_vs_off.sh
cp templates/apps_on_vs_off.template.json results/apps_on_vs_off.json
# fill measured numbers only
```

---

## 3. Hardware-error packet (AUTHOR_NOTES §5)

This is a **catalog** of what the monitor collectors expose today — not a new Job Detail feature.

```bash
python3 bin/catalog_hardware_error_packet.py --out results/hardware_error_packet.json
```

Confirms XIDs, ECC/row-remap, IB flaps, kernel OOM, Lustre evictions are **not**
first-class; `clocks_event_reasons` / power / temp / PCIe replay are partial.
