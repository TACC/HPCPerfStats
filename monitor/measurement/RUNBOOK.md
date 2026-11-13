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

The RPM to measure is **only** the `--rpm` path (installed with `rpm -Uvh --replacepkgs`, so
an older RPM is a downgrade and works); `--build-info` must be the `rpmbuild/build-info` dir
written by the build of **that** RPM (`monitor-build-capabilities.json`: CPU backend, LIKWID
overlay, IB MAD). Without it the driver groups fall back to x86 defaults.

**Governors (D13, D25).** The per-hour cost depends on the cpufreq governor: on c122-018 the
same daemon used ~8 core-s/h under `performance` and ~109 under `ondemand` at the same ~620
wakeups/s (finding 18). So every profile pins the governor: the default `--governors
performance` runs **one leg set** with every CPU at `performance` (the low-clock `ondemand`
cost was measured once in finding 18 and is not re-sampled by default). A comma list runs one
full leg set per governor, e.g. `--governors performance,dynamic`. `dynamic` resolves per
`scaling_driver`:
`ondemand` on `cppc_cpufreq` (Grace), `acpi-cpufreq` and passive `amd-pstate`; `powersave` on
`intel_pstate` and `amd-pstate-epp` (amd-pstate active mode), whose `powersave` is the dynamic
HWP/EPP policy. Explicit names work too (`--governors performance,schedutil`); `--governors
none` leaves the governor alone (one unnamed set; only for nodes without cpufreq). The
harness writes `scaling_governor` for every CPU before each set and writes each CPU's
original governor back on every exit path; `restore.sh` has the same lines. Legs are never
shortened to fit (see step 4): with two sets raise `--max-hours` (about 24) so both keep
1800 s legs.

### What it does

1. **Preflight** (fails closed): root; no other user's Slurm job on the node (your own
   reservation is found from `SUDO_USER`, `SLURM_JOB_USER`, the audit login uid that survives
   `su -`, then `USER`; from a direct root login pass `--allocation-user NAME`); 1-min load
   below 0.1 × nproc; ≥ 2 GiB free; the site conf must not set `disable_types` or a
   non-default `collection_profile`, and `/etc/sysconfig/hpcperfstatsd` must not set
   `HPCPERFSTATS_DISABLE_TYPES` / `HPCPERFSTATS_COLLECTION_PROFILE` (so ALL really is
   all); `<queue>_measure` must not exist with a consumer. perf, RAPL and PSI are probed
   and missing ones become `NOT_MEASURED`. `--force` lets a busy node run, flagged
   `not_idle: smoke only` (excluded from `aggregate_profiles.py`). Every requested governor
   must be in every CPU's `scaling_available_governors` (and `dynamic` needs a known driver),
   otherwise the run stops here with the governor untouched. **Busy node:** node busy cores
   minus the daemon's own CPU over 5 s (the OFF-state load) above 1 core refuses the run
   (round 1 ran with ~17 cores busy in every leg, OFF included, finding 13);
   `--allow-busy-node` runs anyway and the report carries a `busy node` banner.
   **Unbound uncore PMUs:** every `/sys/bus/platform/devices/arm-cs-arch-pmu.*` device must
   have a `driver` link. On c642-041 (2026-11-09) all 5 were unbound (`dmesg`:
   `No cpu associated with the PMU`, firmware APMT), so no `nvidia_scf_pmu_*` existed and
   `arm_aarch64_imc` silently disabled itself; preflight now refuses that node.
   `--allow-unbound-pmu` runs anyway, flagged `unbound_pmu`.
   **Binary identity:** the installed daemon's sha256 is recorded after install and checked
   before and after every leg; a change (i618-141 2026-11-10: the site pushed its own
   `hpcperfstatsd-3.0-15` RPM mid-run, and the next leg started that older binary) aborts the
   run naming the leg. Keep config management from reinstalling the package on a reserved node.
2. **Install**: saves the site conf, `rpm -Uvh --replacepkgs`, writes the site conf back
   (the spec lists it without `%config`, so the RPM overwrites it), checks `server`/`queue`.
   `restore.sh` is written to the result dir before any change. A fresh node needs
   `--conf PATH`. The measured package stays installed afterwards (reserved node).
3. **Discovery**: one 2-minute ALL start; the daemon's `Enabled types:` journal line is the
   active collector list. Each type maps to a **driver group** (`filesystem`, `likwid`,
   `arm_cpu` (Grace `host_cpu_hw` on the `arm` backend; older build-info saying `dcgm` maps
   the same), `dcgm_gpu` (`nvidia_gpu`), `hwmon_power` (`host_power_socket`), `perf_event`,
   `ib_mad`, …; `lib/driver_map.py`); an unmapped type fails.
4. **Blocks** (default 2): OFF → NONE → ALL → SOLO(each group, seeded random order) → ALL →
   OFF (shared with the next block). Legs are `--leg-seconds` (1800) after a 60 s settle;
   OFF/NONE/ALL add a `--fwq-seconds` (300) FWQ phase on every CPU but the sampler's. A
   schedule over `--max-hours` (12) **stops with the minimum hours** instead of shortening
   legs (2026-11-09: c122-018's legs were cut to 1200 s while Vista ran 1800 s, so no leg
   compared). `--allow-fit` restores the old auto-fit (shorten legs, drop the second ALL, then
   single-block SOLOs, each recorded in `fit_steps`); never use it for a campaign.
   `--dry-run` prints the legs and projected end. The whole block set
   repeats per governor (leg ids `<governor>-b1-OFF-` …; `--cadence-sweep` and
   `--profile-collectors` legs run once, in the first set). Each leg records its governor, the
   governors actually read at leg start, `scaling_cur_freq` min/p50/max (1 Hz) and node busy
   cores.
5. **Collector sets** come only from a runtime drop-in
   `/run/systemd/system/hpcperfstats.service.d/90-measure.conf`. ALL and OFF use the
   production unit and queue unchanged. NONE / SOLO / `--cadence-sweep` legs point
   `ExecStart` at `/run/hpcperfstats-measure/hpcperfstats.conf`, a copy of the site conf
   whose only change is `queue <queue>_measure` (CADENCE legs also change `sample_freq`).
   A copy is required because the daemon reads the conf after the CLI, so `-q` cannot
   override the conf queue. listend never consumes `_measure`, so no partial data reaches
   the database. Each leg's own `Enabled types:` line is checked against the intended set:
   a type the daemon kept although the drop-in disabled it marks the leg `type_mismatch`
   (`extra_types`) and aborts the run, because NONE/SOLO would measure the wrong set.
   Daemon CPU is the sum of every thread's schedstat (`/proc/<pid>/schedstat` alone is the
   main thread); reports from older harness versions rebuild correctly with
   `bin/summarize_profile.py`, since `raw/` keeps the per-thread values.
6. **Restore** (every exit path, including SIGINT/SIGTERM/SIGHUP): remove the drop-in,
   delete `<queue>_measure` through the management API (port 15672, conf credentials; if
   that fails it prints `rabbitmqctl delete_queue -p / <queue>_measure` and records
   `measure_queue_left: true`), restore the site conf, the service's active/enabled
   state and every CPU's original `scaling_governor`. If the harness is killed with SIGKILL,
   run `restore.sh` from the result dir.

### Output: `results/<host>_<arch>_<cpu>_<UTC>/`

| File | Content |
|---|---|
| `profile.json` | schema v1; every metric `{value, unit, ci95, n, source}` or `{status: NOT_MEASURED, reason}`; `governor_sets` (per governor: `headline`, `metrics`, `collector_matrix`, `baseline_drift`, from that set's legs only); top-level `metrics` / `collector_matrix` are the first set's (`metrics_governor`); `collector_matrix.variants` (paired VARIANT rows) and `schema_keys` (emitted keys per type) |
| `report.md` | **headline: daemon wakeups/s (voluntary + nonvoluntary context switches of every thread) next to core-s/h, one row per governor**, with the ALL-leg clock p50 and the OFF-leg node busy cores; then per governor the headline table and per-driver table (SOLO − NONE paired within block, share of ALL, interaction residual); hottest daemon threads per governor (`thread_top`: tid, comm, core-s/h, user s/h, system s/h, wakeups/s, active = share of 1 s windows with run time > 0; `—` where older raw lacks the field; embedded DCGM/NVML workers share the daemon's name, so match tids with `gdb -p PID -batch -ex 'thread apply all bt'`); legs with governor, busy cores, clock min/p50/max, wakeups/s |
| `profile_row.csv` | one row of headline metrics |
| `raw/` | `samples.jsonl` per leg (1 Hz), FWQ quanta, `perf.csv`, journal excerpts, `schema.txt` (last `$` schema message drained from `<queue>_measure` on NONE/SOLO/VARIANT/CADENCE legs), `provenance.json`, `run.json`, `commands.log` |

`bin/summarize_profile.py RESULT_DIR` rebuilds every output from `raw/` (it also runs after
each leg, so an interrupted run still has a valid partial report). Results from before the
governor dimension rebuild as one unnamed set; their wakeups/s is the main thread only and is
labelled `main_thread`. Merge architectures:

```bash
python3 bin/aggregate_profiles.py --out results/cross_arch results/*/profile.json
# profiles.csv, profiles.md, profiles.tex (booktabs fragment)
```

**Baseline RPM vs candidate RPM (D24).** Compare two RPMs only through two runs of this
harness on the same node with the same `--governors`, never against an older harness run or a
live A/B. Run the baseline first, then the candidate, each with its own build-info:

```bash
# root shell, from HPCPerfStats/monitor/ on the reserved node
./measurement/bin/run_overhead_profile.sh --rpm /path/to/hpcperfstatsd-BASELINE.aarch64.rpm \
  --build-info /path/to/BASELINE/rpmbuild/build-info --out measurement/results/baseline_$(date -u +%Y%m%dT%H%MZ)
./measurement/bin/run_overhead_profile.sh --rpm rpmbuild/RPMS/aarch64/hpcperfstatsd-CANDIDATE.aarch64.rpm \
  --build-info rpmbuild/build-info --out measurement/results/candidate_$(date -u +%Y%m%dT%H%MZ)
python3 measurement/bin/compare_schema_keys.py measurement/results/baseline_*/profile.json \
  measurement/results/candidate_*/profile.json
```

Read each `report.md` headline table row by row: wakeups/s compares across governors and
architectures, core-s/h only under the same governor. Before blaming a tool (perf, the 1 Hz
sampler, a build flag) for a CPU gap between two runs, A/B that tool on the live PID: perf, the
sampler and the build were each ruled out that way before the governor was found (findings 10,
12, 18).

**Grace SCF cost (overlay builds).** `arm_aarch64_imc` sits in `arm_cpu`, and each block adds
a `VARIANT arm_cpu_noscf` leg: `host_cpu_hw` alone with `HPCPERFSTATS_LIKWID_GRACE_SCF=0` in
the runtime drop-in (the overlay starts at CYC+INS, no SCF counters). `report.md` → *Paired
variants* gives SCF counters + `arm_aarch64_imc` = SOLO(`arm_cpu`) − VARIANT in the same block,
with perf FDs Δ (expect 4 on a 2-socket Grace). `NOT_MEASURED (... marker)` means the daemon did
not log `SCF uncore events off` — the RPM predates the knob, so the pair would be two identical
legs. Both RPMs of a comparison need the knob for a variant row in each.

**Emitted keys across two runs** (e.g. NVLink keys under `nvidia_gpu` after a DCGM change):

```bash
python3 bin/compare_schema_keys.py results/BASE/profile.json results/CAND/profile.json --type nvidia_gpu
# SCHEMA_KEYS_OK (exit 0) / SCHEMA_KEYS_REMOVED (exit 1) / NOT_MEASURED (exit 2: a run had no capture)
```

**Runs meant to be compared per leg** (a campaign across nodes or clusters):

```bash
python3 bin/compare_runs.py results/RUN_A results/RUN_B results/RUN_C --out results/compare.md
# exit 0: COMPARABLE (leg length, settle, FWQ, blocks, governor, daemon conf except the broker
#         connection all identical; no fit_steps) / exit 1: NOT COMPARABLE (DIFF rows) / 2: bad dir
```

Fleet build, OS, hardware and the collector groups/legs that follow from them are listed as
intended differences, never failures. Give every node the same harness-built `--conf` (only
`server`/`queue`/`user`/`password` per site) and the same `--blocks`/`--leg-seconds`.

### Per-architecture checklist

- [ ] Node reserved (your own allocation only), idle (preflight refuses > 1 busy core without `--allow-busy-node`).
- [ ] `--dry-run` shows the governor leg sets you expect (`performance` + the driver's dynamic governor).
- [ ] Site conf has no `disable_types` / non-default `collection_profile`.
- [ ] `--dry-run` projected end fits the reservation (raise `--max-hours`; never `--allow-fit`
      for a campaign).
- [ ] Every Grace node lists `nvidia_scf_pmu_*` (preflight refuses unbound CoreSight PMUs).
- [ ] Campaign: `bin/compare_runs.py` over all runs exits 0; commit its `compare.md` with the
      profiles.
- [ ] Run finished: `report.md` has no `baseline_drift` warning and no contaminated legs;
      cite wakeups/s, and core-s/h only with its governor.
- [ ] `raw/run.json` `state.cleanup.governor` is `ok` (every CPU back on its original governor).
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

## 0b. Grace DRAM CAS verification (`arm_aarch64_imc` via LIKWID SCF)

On a Grace node, as root, after `build_static_bundle.sh` has populated the static prefix:

```bash
./measurement/bin/verify_grace_scf_imc.sh
```

It stops `hpcperfstats.service` for the run (restored on exit) and checks, against a known
`likwid-bench` volume on socket 0 (fixed `-i 10`, no calibration passes): (A) the SCF units
through kernel `perf stat` on `nvidia_scf_pmu_0/cmem_rd_data/` and `/cmem_wr_total_bytes/`,
independent of LIKWID (reads = 32-byte beats, writes = bytes, idle subtracted; `NOT_RUN` when
`perf` or those events are missing); (B) a DEBUG
build of the daemon run as a transient unit (broker unreachable, dumps to the result dir,
`/dev/shm/hpcperfstatsd-scf-verify` mirror): `arm_aarch64_imc` `scf0` deltas match the volume
in 64-byte CAS within 0.75–1.5x and `scf1` stays below 0.25x; (B2) with DCGM refreshing at
`sample_freq`, `host_cpu_hw` user util on 8 pinned busy CPUs matches `host_cpu` `/proc/stat`
user time within 5% and is not a sample behind mid-load; (C) the DCGM NVSwitch module is
denied and not mapped. Output: `measurement/results/grace_scf_<host>_<UTC>/summary.txt` (daemon
stdout/stderr in `daemon_output.log`, not the journal, which rate-limits the DEBUG build), last
line `GRACE_SCF_VERIFY_OK` or `GRACE_SCF_VERIFY_FAIL`. Parsing and verdicts:
`lib/grace_scf_verify.py` (unit-tested in `tests/test_grace_scf_verify.py`).

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
