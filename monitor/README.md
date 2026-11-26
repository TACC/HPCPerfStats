# HPCPerfStats monitor

C implementation of the **hpcperfstats** data collector: either a **RabbitMQ daemon** (`hpcperfstatsd`) that streams UTF-8 text payloads to a broker, or a **file-mode client** that appends to local stats archives. Downstream ingestion expects a stable host field and `$`-prefixed rotation lines; the consumer contract is defined by `HPCPerfStats/hpcperfstats/listend.py` (see workspace **monitor-workspace-contract**).

## Directory layout

| Path | Purpose |
|------|---------|
| `src/` | Production C sources and headers (`hpcperfstatsd`, collectors, schema, buffers). |
| `tests/` | `check` targets: C unit drivers, shell regression, `Makefile.am`, `run_tests.sh`. Details: [tests/README.md](tests/README.md). |
| `measurement/` | HUST 2026 **node** measurement kit (cadence, overhead, jitter); [measurement/RUNBOOK.md](measurement/RUNBOOK.md). Site/pipeline kit: `tests/site_measurement/`. |
| `scripts/` | Static bundle and prefix helpers (`build_static_bundle.sh`, `ensure_dotbuild_prefix_static.sh`); plan-close **`run_valgrind_check.sh`** and **`run_cpp_linter.sh`**. |
| `site/` | Per-checkout RPM site overrides: untracked `hpcperfstats.site.conf` (broker, credentials), tracked `.example` template ([Site conf](#site-conf-broker-credentials-and-rpm-release)). |
| `m4/`, `configure.ac` | Autotools. |
| `README` | Legacy reference for **on-disk stats archive** format (headers, schema lines, record groups). |
| [`LIKWID_MIGRATION.md`](LIKWID_MIGRATION.md), `VARIORUM_MIGRATION.md` | Historical notes only (LIKWID (3.0) removed in 3.1 Phases 2+3, Variorum earlier); the current x86 counter design is in [x86 counters](#x86-counters-direct-perf-31-phases-23) below. |

## Build flavors

`hpcperfstatsd` is always the RabbitMQ daemon (`monitor.c`, `monitor_cli.c`, `monitor_daemon.c`, `stats_buffer.c`; AMQP + libev; hardware counters by direct `perf_event_open`, no PMU library): `configure` requires rabbitmq-c. The old non-RabbitMQ batch client (`main.c`, `stats_file*.c`) was removed in 2026-11.

Configure selects optional collectors via Automake conditionals; use **`./configure --help`** for options (including **`--enable-all-static`**, **`--enable-metric-profiler`**, **`--with-metric-profiler-backend={none,ebpf}`**, **`--enable-opa`**, **`--enable-intel-gpu`**, and **`--enable-shared-node-multi-jid`**).

### Shared-node multi-jid (`--enable-shared-node-multi-jid`)

- **Default (off):** file `jobid_file` / `/var/run/stats_jobid` with `%begin` / `%end`; no `%jobs` / `%affinity`; no `host_mem_job`.
- **Shared-node build:** discovers Slurm jids from `/proc` (`slurmstepd.scope` + `SLURM_JOB_ID`), emits one RabbitMQ sample per jid with `%jobs` / `%affinity` from read-only `sched_getaffinity`. A sole jid that owns every online CPU and every available GPU uses the ordinary unmarked single-jid payload. On `|J|≥2`, jid payloads keep that job’s GPUs and affinity CPUs (`host_cpu` / `host_cpu_hw`); the `-` payload keeps unscheduled GPUs and the online CPU remainder. Does **not** read `stats_jobid` and never execs `scontrol` / `nvidia-smi`. **Idle allocations stay tagged:** once a node's job steps end only root `slurmstepd` processes remain (the `/proc` walk skips root pids), so a token no job process names is named from its extern stepd title (`step_extern/slurm/cgroup.procs` → `/proc/<pid>/cmdline` `slurmstepd: [<jid>.extern]`). Such title-named jobs never emit `%affinity`: alone they send the unmarked payload; with other jobs they are listed in `%jobs` and send only `host_mem_job`. Unit-tested in `tests/test_job_proc_discover.c` and `tests/test_job_discovery_marks.c` (`src/job_emit_filter.c` holds the per-payload row filter).
- **`host_mem_job`:** per-jid cgroup memory at `slurmstepd.scope/<token>/` (`memory.current` / `peak` / `stat`), device `j:<jid>`, KEYS overlap a subset of `host_mem` (`mem_used`, `anon_pages`, …) plus `mem_peak` (`U=KB`; `NOT_PRESENT` (`-2`) when the scope has no `memory.peak`, i.e. kernels before 5.19 without the backport, and for a stat key `memory.stat` does not list; an unreadable `memory.stat` gives the stat keys its errno sentinel). Not full NUMA `host_mem` parity. Present on jid payloads (including full-resource unmarked); omitted on `-`. Override cgroup root in tests: `HPCPERFSTATS_CGROUP_ROOT`.
- **Deploy:** upgrade consumer (listend) **before** shared-node monitor RPMs on shared fleets. Conf keys (shared build only): `jid_require_step_id`, `jid_discover_pid_cap`, `affinity_pid_sample_limit`.
- **Horizon RPM (first shared fleet):** `./scripts/prepare_rpmbuild_horizon.sh` then the printed `rpmbuild -ba …` footer (`HPCS_BUNDLE_FLEET=horizon` / `scripts/fleet/horizon.force` — never commit the force file).
- Verify shared flavor: `./scripts/run_shared_node_static_check.sh` (or `./scripts/build_static_bundle.sh --enable-shared-node-multi-jid` then `make -C .build-static check`).

### Omni-Path / Cornelis (`host_opa`)

- **`host_opa` is always built.** It collects Cornelis CN5000 and Intel Omni-Path HFI 100 Series devices (`hfi1_*` under `/sys/class/infiniband`). Device ids use a slash (`hfi1_0/1`), not the IB dot form.
- **Sysfs fallback (default):** reads overlapping utilization counters from `ports/N/counters` first (Stampede3 OPA100). Maps `port_*_packets` → schema `*_pkts`; a schema key without its counter file (e.g. without `multicast_*`) emits `NOT_PRESENT` (`-2`). On **Cornelis CN5000 NDR**, classic `counters/` files often exist but read **EINVAL**; when no classic keys are readable, fall back to `ports/N/hw_counters` map: `TxWords`/`RxWords`/`TxPkt`/`RxPkt`/`TxWait` → `port_xmit_data`/`port_rcv_data`/`port_xmit_pkts`/`port_rcv_pkts`/`port_xmit_wait`. Do not map `DmaWait` / `RcvHdrOvr*` into KEYS. STL-only keys (FECN/BECN, bubbles, …) emit `NOT_BUILT` (`-10`) on a build without OPA MAD, and `NOT_PRESENT` (`-2`) on a MAD-dlopen build whose `liboib_utils` is missing at runtime.
- **Collectible ports only:** inactive/DOWN ports are skipped (`ib_port_collectible`) — e.g. CN5000 dual-port with port1 Offline emits only `hfi1_0/2`.
- **`--enable-opa-mad-dlopen`:** compiles STL MAD against vendored `third_party/oib-shim/` and **`dlopen`s `liboib_utils`** at runtime (`HPCPERFSTATS_OIB_LIB`). No link-time `-loib_utils`. Stampede3 fleet default. If the `.so` is missing (common on PVC and other nodes that only have `opa-basic-tools`), MAD is **probed once then disabled**—no recurring MAD journal lines; **sysfs `host_opa` continues**. `opa-basic-tools` alone does **not** provide `liboib_utils` for MAD.
- **`--enable-opa`:** legacy link-time Cornelis/Intel IFS **`liboib_utils`** (+ system `oib_utils.h`). Requires IFS devel on the build host.
- **`host_ib` never claims `hfi1_*` HCAs** — those belong to `host_opa` only.
- Stampede3 PCI examples: Cornelis CN5000 HFI (SPR); Intel Omni-Path HFI Silicon 100 Series (ICX/SKX/H100).

### InfiniBand MAD (`host_ib`)

- **Sysfs** counters under `/sys/class/infiniband` (non-`hfi1_*`) are always compiled.
- **Sysfs** supplies every host counter: `ports/<p>/counters/` and `hw_counters/`, including the 64-bit `port_{xmit,rcv}_data` and `port_{xmit,rcv}_packets` (cumulative since boot). There is no host-port MAD query: the PortCountersExtended keys (`port_select`, `counter_select`, `port_*_pkts`) are retired (*IB MAD extended keys retired* below).
- **MAD** serves only the switch-side keys `sw_rx_bytes`, `sw_rx_packets`, `sw_tx_bytes`, `sw_tx_packets` (SMP PortInfo to the attached switch port, then its PortCountersExtended; per-port backoff `BACKOFF` / `BACKEND_ERROR`, `NOT_PRESENT` once the port refused the switch MAD open, `NOT_BUILT` without MAD).
- Ports must be ACTIVE or phys LinkUp **and** `link_layer=InfiniBand` when that sysfs file exists (Ethernet-mode mlx HCAs, e.g. Horizon `mlx5_2`, are skipped so MAD is not attempted on them).
- **`--enable-ib-mad-dlopen`:** compiles MAD against vendored `third_party/ibmad-shim/` and **`dlopen`s `libibmad`** (`HPCPERFSTATS_IBMAD_LIB`). No link-time `-libmad`. Stampede3 fleet default.
- **`--enable-infiniband` (default, non-fleet):** link-time `-libmad` when devel is present; bundle may pass `--disable-infiniband` when the probe fails (sysfs-only).

### Site conf (broker, credentials) and RPM Release

Node checkouts never edit the tracked `src/hpcperfstats.conf` or `hpcperfstats.spec`. Site
settings go in **`site/hpcperfstats.site.conf`**: untracked (gitignored), per checkout, same
`key value` syntax as the conf. Start from the tracked template:

```bash
cd HPCPerfStats/monitor
cp site/hpcperfstats.site.conf.example site/hpcperfstats.site.conf
chmod 600 site/hpcperfstats.site.conf   # then set server/queue/port/user/password
```

- **How it reaches the RPM:** `make dist` (`dist-hook`) copies the site file into the tarball
  when it exists; the spec `%build` runs `scripts/site_conf.sh overlay`, which replaces the value
  of every key the default conf has, appends keys it lacks, and keeps the default's comments and
  order. No site file: the RPM ships the repo default (`server localhost`). The script prints key
  names only, never values. The tarball and SRPM under `rpmbuild/` then hold the credentials
  (`make dist` makes every member world-readable), so keep that tree private.
- **Fleet broker guard:** a fleet prepare (`prepare_rpmbuild_stampede3.sh` / `_ls6.sh` /
  `_horizon.sh`, or any `HPCS_BUNDLE_FLEET` / `scripts/fleet/*.force`) runs
  `scripts/site_conf.sh fleet-guard` before any build step and refuses when the effective
  `server` is empty, `localhost` or loopback. `HPCS_ALLOW_DEFAULT_BROKER=1` downgrades that to a
  warning for a deliberate local-broker build. Plain `prepare_rpmbuild_dirs.sh` dev builds need
  no site file.
- **Installed conf:** `/etc/hpcperfstats/hpcperfstats.conf` is `%config(noreplace)`, mode
  `0600`. An upgrade keeps a conf edited on the node and writes the packaged one as
  `hpcperfstats.conf.rpmnew`; an unedited conf is replaced by the packaged one.
- **Release:** `Release: %{?hpc_release}%{!?hpc_release:1}%{?dist}`. The committed number is the
  fleet release for every cluster; it changes only for a user-approved production release
  (`cursor-rules/monitor-version-and-packaging.mdc`). For a one-off build (debug, canary, a node
  that needs a higher release) set `HPCS_RELEASE=N` before the prepare script (the printed
  `rpmbuild` lines gain `--define "hpc_release N"`) or before `RPM_BUILD=1
  ./scripts/rpm_debug_shm_verify.sh`, or pass `--define "hpc_release N"` to `rpmbuild` yourself.

**Migrating an existing node checkout** (local edits in the tracked conf / spec): copy the broker
lines into the site file, add any other key you changed (see `git diff`), restore the tracked
files, then pull and build:

```bash
cd HPCPerfStats/monitor
git diff src/hpcperfstats.conf hpcperfstats.spec
mkdir -p site
grep -E '^(server|queue|port|user|password)[[:space:]:=]' \
  src/hpcperfstats.conf > site/hpcperfstats.site.conf
chmod 600 site/hpcperfstats.site.conf
git checkout -- src/hpcperfstats.conf hpcperfstats.spec
git pull
./scripts/prepare_rpmbuild_stampede3.sh
```

Use the prepare wrapper for your cluster (`_ls6.sh`, `_horizon.sh`). If the guard refuses, the
site file does not set a real `server`.

### Stampede3 one-build RPM

One x86_64 RPM covers all Stampede3 LSPCI profiles (skx/clx/icx/spr/h100/pvc/amd-rtx) after PVC + OPA collectors land:

```bash
cd HPCPerfStats/monitor
./scripts/prepare_rpmbuild_stampede3.sh              # or --debug-build
# Footer prints only the requested rpmbuild (release by default):
rpmbuild -ba --define "_topdir ${PWD}/rpmbuild" "${PWD}/rpmbuild/SPECS/hpcperfstats.spec"
```

| Queue | Collectors (when hardware/libs present) | Runtime libs for full fidelity |
|-------|------------------------------------------|--------------------------------|
| skx / icx / spr | `host_opa` (sysfs); CPU direct perf | `liboib_utils` optional (MAD) |
| clx | `host_ib` (CX6); CPU direct perf; no NVIDIA | `libibmad` optional |
| h100 | `nvidia_gpu` + `host_ib` + `host_opa` | `libdcgm`, `libibmad`; `liboib_utils` optional |
| pvc | `intel_gpu` + `host_opa` | `libxpum` / xpumanager; `liboib_utils` optional |
| amd-rtx | NVIDIA Blackwell + IB + OPA + AMD CPU (not `amd_gpu`) | `libdcgm`, `libibmad`; `liboib_utils` optional |

Fleet matrix (opt-in via `prepare_rpmbuild_stampede3.sh`: `HPCS_BUNDLE_FLEET=stampede3` and/or `scripts/fleet/stampede3.force` created for that dist only — never commit the force file; default `prepare_rpmbuild_dirs.sh` does not ship it): `--enable-ib-mad-dlopen`, `--enable-opa-mad-dlopen`, `--disable-amd-gpu`, `--enable-intel-gpu` when vendored XPUM headers exist. Binary `NEEDED` must not list `libibmad` / `liboib_utils`. On aarch64, default prepares do not auto-enable intel-gpu from vendored headers alone (`HPCS_BUNDLE_ENABLE_INTEL_GPU=1` or Stampede3 fleet required).

**Per-queue shm validate (one binary × Stampede3 profiles):** after installing the same DEBUG fleet RPM/binary on a node of each queue class, run:

```bash
# On each of skx, clx, icx, spr, h100, pvc, amd-rtx (same CAPS_JSON / fleet slug):
./scripts/validate_stampede3_profile.sh --profile h100
```

Artifacts: `expectations_<slug>__<profile>.json` and
`test_runs/monitor/validate_<slug>__<profile>_YYYY-MM-DD.txt`. Profile names and
LSPCI dumps live under `tests/fixtures/stampede3_lspci_profiles/` (in git).

**Vista LSPCI fixtures (future validate):** `tests/fixtures/vista_lspci_profiles/{gg,gh}`
are vendored for a future Vista path; the Stampede3 wrapper rejects `gg`/`gh`.

**Horizon LSPCI fixtures (inventory):** `tests/fixtures/horizon_lspci_profiles/gb`
(GB200 / HGX) are vendored for roofline / fleet inventory; not wired into Stampede3 shm validate.

### Horizon one-build RPM (shared-node)

First fleet with **`--enable-shared-node-multi-jid`** (`%jobs` / `%affinity` / `host_mem_job`). aarch64 Grace + GB200; IB MAD dlopen; no OPA / AMD / Intel GPU collectors.

```bash
cd HPCPerfStats/monitor
./scripts/prepare_rpmbuild_horizon.sh              # or --debug-build
rpmbuild -ba --define "_topdir ${PWD}/rpmbuild" "${PWD}/rpmbuild/SPECS/hpcperfstats.spec"
```

Fleet matrix (opt-in via `prepare_rpmbuild_horizon.sh`: `HPCS_BUNDLE_FLEET=horizon` and/or `scripts/fleet/horizon.force` — never commit the force file): `--enable-shared-node-multi-jid`, `--enable-ib-mad-dlopen`, `--disable-amd-gpu`, `--disable-intel-gpu`. Does **not** enable OPA MAD. Never auto `--disable-infiniband`. Binary `NEEDED` must not list `libibmad`. **Deploy listend before** installing this RPM on Horizon.

LSPCI dumps: `tests/fixtures/horizon_lspci_profiles/gb`.

### Lonestar6 one-build RPM

One x86_64 RPM covers Lonestar6 LSPCI profiles (`genoa` / `milan` / `a100`):

```bash
cd HPCPerfStats/monitor
./scripts/prepare_rpmbuild_ls6.sh              # or --debug-build
rpmbuild -ba --define "_topdir ${PWD}/rpmbuild" "${PWD}/rpmbuild/SPECS/hpcperfstats.spec"
```

| Queue | Collectors (when hardware/libs present) | Runtime libs for full fidelity |
|-------|------------------------------------------|--------------------------------|
| genoa / milan | `host_ib` (CX6); AMD CPU direct perf; no NVIDIA | `libibmad` optional |
| a100 | `nvidia_gpu` + `host_ib` + AMD CPU direct perf | `libdcgm`, `libibmad` optional |

Runtime on a100: A100 has no GPM, so `nvidia_gpu` stays on the DCGM path. Since round 2 no watch profile carries the soft-defined board-power field IDs 1132/1133 that DCGM **3.1.x** rejected at `FieldGroupCreate`, so the old `basic-no-board-power` fallback is gone (the keys it guarded were retired). DCGM 3.1.x on the LS6 A100 still does not take `full-prof`: `dcgmi profile -l` there lists 1013/1014 but not 1015 (DFMA) or 1016 (integer pipe), so the watch falls back (seen in #116) and now lands on `split-prof-no-dfma`, which keeps `tensor_imma_active` / `tensor_hmma_active`; `tensor_dfma_active` and `integer_active` publish `NOT_PRESENT`. If verify reports no `nvidia_gpu` rows with devices expected, check journal for `field group creation failed` / `stage=6` and `Driver/stack probe` `libdcgm=` / `libnvidia-ml=`.

Fleet matrix (opt-in via `prepare_rpmbuild_ls6.sh`: `HPCS_BUNDLE_FLEET=ls6` and/or `scripts/fleet/ls6.force` — never commit the force file): `--enable-ib-mad-dlopen`, `--disable-amd-gpu`, `--disable-intel-gpu` (forced even when vendored XPUM headers exist). Does **not** enable OPA MAD (`--enable-opa-mad-dlopen`). Never auto `--disable-infiniband`. Binary `NEEDED` must not list `libibmad`. NVIDIA stays on the default x86 DCGM runtime-dlopen path.

LSPCI dumps: `tests/fixtures/ls6_lspci_profiles/{genoa,milan,a100}`. Per-queue shm validate wrapper is **not** wired yet (future; same pattern as Stampede3).

### Intel Data Center GPU / PVC (`intel_gpu`)

- **`--enable-intel-gpu={auto,yes,no}`** (default **auto** via `scripts/gpu_lspci_probe.sh intel`). Compiles against vendored **`third_party/intel-xpum/`** headers (XPUM **1.2.33** only — not system `/usr/include`).
- **Default collect backend: `xpumcli`** (short-lived child). Avoids in-process `libxpum` / Level Zero, which on Stampede3 PVC held hundreds of `i915.gem` maps (~16 TiB **VSZ**) and stressed GuC error capture. Conf/env: **`intel_gpu_backend xpumcli|libxpum`** or **`HPCPERFSTATS_INTEL_GPU_BACKEND`** (default **xpumcli**).
- **Opt-in `libxpum`:** runtime **`dlopen`** (`/usr/lib64/libxpum.so`, ...); override path with **`HPCPERFSTATS_XPUM_LIB`**. No link-time `-lxpum`. Before `xpumInit`, sets `XPUM_DISABLE_PERIODIC_METRIC_MONITOR=1` and `XPUM_METRICS=0,1,4,6-10,15,30,34,35,36` (overwrite; 15 = compute engine group utilization, 30 = memory temperature). Documents GuC/VSIZE/segfault risk - use only for lab.
- **xpumcli path:** `discovery` + `dump -d <ids> -m 0,1,2,3,4,5,9,17,18,31,35 -i 1 -n 1` (`INTEL_GPU_XPUMCLI_DUMP_METRICS`; dump IDs are not API `XPUM_STATS_*`; dump power **1** -> `power_usage`, dump **31** -> `compute_engine_util`). PCIe/Xe Link counters emit `NOT_BUILT` on this path (the dump has throughput, not cumulative counters). Bounded fork/exec timeout (~8 s).
- **`compute_engine_util`**: compute engine group utilization in % (max over tiles), from dump metric 31 (xpumcli) or `XPUM_STATS_ENGINE_GROUP_COMPUTE_ALL_UTILIZATION` (libxpum, `XPUM_METRICS` 15).
- **`sm_active` (EU Array Active) on Stampede3 PVC is `NOT_PRESENT`**: XPUM prints `N/A` for it because the PVC GFX firmware (`PVC2_1.23263`) is below the XPUM minimum (`PVC2_1.23423`). A metric the dump omits or prints as `N/A` is `NOT_PRESENT` for that collect only, so `sm_active` reports after a firmware update with no monitor change.
- **Backend failure keeps the rows** (`xpumd` unreachable, `xpumcli` discovery or dump failure, libxpum init failure, every device fetch failing): each GPU still emits its row with every key a value sentinel, `BACKEND_ERROR` on a failed attempt and `BACKOFF` while skipping. After `INTEL_GPU_MISS_STREAK_MAX` failed attempts in a row the collector waits `INTEL_GPU_BACKOFF_SEC` before the next one, so a dead `xpumd` costs one `xpumcli` attempt per cooldown, not one per cycle. The type is never disabled: when `xpumd` comes back, the next attempt publishes real values with no restart. Rows use the last discovered device ids. When discovery never succeeded, they are `"0"`..`"N-1"`, where N counts the Data Center GPU Max PCI functions in sysfs (`intel_gpu_pci.c`: vendor 8086, display or accelerator class, the Ponte Vecchio device ids from the PCI ID database). On the xpumcli path the PCIe / Xe Link keys stay `NOT_BUILT` in failure rows too. A node without such a function emits no `intel_gpu` rows. The validator probe `probe_intel_gpu_devices` counts the same sysfs functions.
- Stampede3 PVC: four Data Center GPU Max 1550 → device rows `"0"`…`"3"`. Ops emergency: **`disable_types intel_gpu`**, **`--disable-types intel_gpu`**, or **`HPCPERFSTATS_DISABLE_TYPES=intel_gpu`** (clears GEM/VSZ). Startup logs **Enabled types** and **Disabled types**.
- Force enable for testing: **`HPCPERFSTATS_FORCE_INTEL_GPU`**. Static bundle enables intel_gpu when vendored headers are present on **x86**, or on aarch64 only with Stampede3 fleet / **`HPCS_BUNDLE_ENABLE_INTEL_GPU=1`**.

## Metric profiler build options

- `--enable-metric-profiler` enables compile-time collector timing instrumentation (per-type `st_collect` and per-metric `stats_set`/`stats_inc` paths).
- `--with-metric-profiler-backend=none` keeps attribution internal (wall vs thread CPU vs inferred wait).
- `--with-metric-profiler-backend=ebpf` enables the backend path that emits host-side attribution signals with each periodic profile report.
- Static bundle integration: when `--with-metric-profiler-backend=ebpf` is passed through `scripts/build_static_bundle.sh`, the script also downloads and builds pinned static `libbpf` into the same `PREFIX` tree.

Profiling output is emitted periodically as `metric-profiler:*` lines and includes aggregated call counts, total/avg/min/max wall time, CPU time, and inferred wait time (`wall-cpu` clamped at zero).
Both the RabbitMQ daemon and the batch client profile every collect cycle through `stats_runtime_collect_cycle` (`metric_profiler_cycle_begin` / `_end`). The periodic report comes every 120 cycles (one hour at `sample_freq 30`), so the daemon also prints the cumulative report once at shutdown (`metric_profiler_report`, under `#ifdef MONITOR_METRIC_PROFILER` in `monitor.c`). Without `--enable-metric-profiler` none of this is compiled. `measurement/bin/run_overhead_profile.sh --profile-collectors` uses it for one labelled, non-headline leg.

## Key source modules

Small, testable units and daemons are split along these lines (non-exhaustive):

| Area | Files (under `src/`) |
|------|----------------------|
| Stats type registry (`st_name` table for lookup / iteration; ordered by legacy variable names, so `stats_type_get` scans linearly) | `stats_registry.c`, `stats_registry.h` (optional modules guarded by configure **`MONITOR_WITH_*`** / backend macros — keep aligned with `Makefile.am` **`TYPES`**). |
| Direct perf helpers shared by the aarch64 and x86 readers (portable; unit-tested on every host through `tests/test_fake_perf.{c,h}`): open, raw / sysfs attrs, per-boot PMU `type`, `cpumask`, `{value, time_enabled, time_running}` read scaled by Δenabled / Δrunning, open errno → sentinel; never pinned | `perf_util.c`, `perf_util.h`. |
| `host_cpu_hw` core counters on Grace: nine events per CPU (CYC, INS, FP DP/SP/HP, FIXED, SCALE, SVE INT8/INT16), one fd each on `armv8_pmuv3*`; sysfs event names first, then a MIDR-keyed raw-code table (Neoverse V2) | `arm_core_perf.c`, `arm_core_perf.h`; key mapping in `publish_arm_measured_stats` (`cpu_counter_metrics_arm_publish.c`). |
| Grace DRAM CAS (`arm_aarch64_imc`): one `scf<N>` device per `nvidia_scf_pmu_<N>` (1 on GH200, 2 on GB200 / Grace-Grace), `cmem_rd_data` + `cmem_wr_total_bytes` by direct perf; perf `dmc`/`imc` scan as fallback; re-scans every 10 ticks while no memory PMU is bound | `arm_imc.c`, `arm_imc_scf.c`, `arm_imc_scf.h`. |
| `host_cpu_hw` on aarch64 (`arm` backend): `/proc/stat` jiffy util, cpufreq `scaling_cur_freq` clock (nominal chain fallback), measured counters from `arm_core_perf`; no DCGM, no LIKWID (3.1+) | `cpu_counter_metrics.c`, `cpu_counter_metrics_arm_{publish,util}.{c,h}`, `cpu_counter_metrics_arm_state.h`. |
| `host_power_socket`: ACPI `<Name> Power Socket <N>` meters (CPU, Grace/socket, SysIO, Module), one row per socket; disabled where no meter exists | `arm_power_hwmon.{c,h}` (label index, EL10 `hwmonN/` and EL9 `hwmonN/device/` layouts, one fd per meter), `arm_power_socket.{c,h}`. |
| `nvidia_gpu` source: NVML + GPM on GPM-capable GPUs (dlopen `libnvidia-ml`, `HPCPERFSTATS_NVML_LIB`), DCGM otherwise | `nvidia_gpu_nvml.{c,h}`, `nvidia_gpu_nvml_convert.{c,h}`, vendored `third_party/nvidia-nvml/nvml.h`; DCGM: `nvidia_gpu_dcgm_watch.c`, `dcgm_gpu_dyn.c` (runtime `dlopen` on every arch). |
| DCGM watch cadence: `dcgmWatchFields` refresh = collect interval (`sample_freq`, set on conf load/SIGHUP), history = 2 refreshes; batch client keeps each caller's fallback | `dcgm_watch_policy.c`, `dcgm_watch_policy.h` (used by `nvidia_gpu_dcgm_watch.c`). `dcgm_session.c` starts the embedded engine with the NVSwitch module denied (`DcgmModuleIdNvSwitch`) and logs which start path ran. |
| Prepare/teardown and sink-facing collect cycles | `stats_runtime.c`, `stats_runtime.h`, `stats_sink.h`. |
| Shared UTF-8 text for archive/buffer rows, schema suffixes, `%` marks | `stats_text_format.c`, `stats_text_format.h`. |
| Long **`configure`/daemon-style options** and argv parsing | `monitor_options.c`, `monitor_options.h`; thin entrypoints `monitor_cli.c`, `monitor_cli.h` (shared literals `monitor_cli_lit_*` with `monitor_daemon.c`). |
| Daemon loop, RMQ send, ring buffer | `monitor_daemon.c`, `monitor_daemon.h`, `monitor_release_log.c` (release: compact startup + hourly status + first-fail RMQ errors; DEBUG: verbose tick status / resend chatter; full `$` schema header every 6h and when JOBID unloads to `-`). Sample timer: after each collect `monitor_daemon_reanchor_sample_timer` re-arms a one-shot timer to the next `sample_freq` boundary measured from libev's cached `ev_now()` (which predates the collect), and a boundary less than a quarter period after the last collect start counts as served, so each boundary gets one collect (`monitor_timing_reanchor_wait` in `monitor_timing.c`). |
| Schema text parsing | `schema_entry_parse.c` (`parse_schema_entry`); `schema.c` builds full schemas for types. |
| RMQ text payloads | `stats_buffer.c` + `stats_buffer_data_append.c` (persistent AMQP; cached `uname` for header + sample lines; batched rows; declare `syslog` INFO in `DEBUG` only). Soft merge cap **`STATS_BUFFER_RMQ_SOFT_MAX_BYTES` (~32 MiB)** stops adding samples to one AMQP body when the next would exceed the limit; the resend loop still publishes **multiple** such messages in one drain call. A single oversize sample/`$` may publish alone. |
| DEBUG shm mirror (`@fast`/`@full` snapshots) | `stats_buffer_debug_shm.c`, `stats_buffer_debug_shm.h` (`DEBUG` builds only). |
| Private dirs / privileged `dlopen` path gate | `secure_path.c`, `dyn_lib_path.c` (`ensure_private_dir`, `dyn_lib_path_allowed`). |
| Intel CPUID / generation gating | `cpuid.c`, `intel_cpuid_match.c`, `intel_processor.c` |
| AMD EPYC CPUID | `amd_cpuid_match.c`, `amd_processor.c` |
| `host_cpu_hw` core counters on x86 (`x86` backend): one fd per event per CPU on the sysfs `cpu` PMU; generic `instructions` / `cpu-cycles` / `ref-cycles` from sysfs, every other event from a per-class raw row verified on hardware (Intel SKX–GNR, AMD Milan / Genoa / Turin); scaled software lifetimes, D6 time sums | `x86_core_perf.c`, `x86_core_perf.h`; begin / collect in `cpu_counter_metrics.c`; key mapping in `publish_x86_measured_stats` (`cpu_counter_metrics_x86_publish.{c,h}`). |
| x86 uncore DRAM counters: Intel IMC (`uncore_imc_<N>`), SPR / EMR HBM (`uncore_hbm_<N>`), AMD DF (`amd_df`); per-generation profiles, sysfs event names first, else verified raw terms placed through the PMU `format/` files; one fd per (event, cpumask CPU), each row sums its cpumask CPUs | `x86_uncore_perf.c`, `x86_uncore_perf.h`; thin collectors `intel_{skx,icx,spr,emr,gnr,srf}_imc.c`, `amd_x86_uncore_df.c`. |
| x86 RAPL energy per socket: perf `power` PMU `energy-*` events (scaled by `.scale`), powercap `energy_uj` per domain as fallback; running totals in mJ since begin | `x86_rapl.c`, `x86_rapl.h`, `rapl_powercap.c`; types `intel_x86_rapl` (`intel_rapl.c`), `amd_x86_rapl` (`amd64_rapl.c`). |
| Omni-Path / Cornelis HFI (`host_opa`) | `opa.c`, `opa_sysfs.c`, `opa_mad_backoff.c`, `opa_mad_dyn.c`, `host_opa.h` (sysfs always; STL MAD via `--enable-opa-mad-dlopen` or link-time `--enable-opa`) |
| NFS client (`host_nfs`) | `nfs.c`, `host_nfs.h` (`/proc/self/mountstats`; one row per `fstype nfs` or `nfs4` mount point; positional `events:` / `xprt:` / per-op fields read by kernel field index) |
| BeeGFS client (`beegfs_client`) | `beegfs.c`, `beegfs_client.h`, `beegfs_mount.c`, `beegfs_tcp.c` (mounts + `statvfs`; I/O bytes from the kernel TCP counters of the client's storage connections) |
| Intel Data Center GPU / PVC (`intel_gpu`) | `intel_gpu.c`, `intel_gpu.h`, `intel_gpu_xpumcli.c` (default backend), `xpum_gpu_dyn.c` (vendored `third_party/intel-xpum/`; runtime `libxpum` dlopen), `intel_gpu_pci.c` (sysfs Data Center GPU Max count for backend-failure rows) |
| IB MAD dlopen | `ib_mad.c`, `ib_mad_dyn.c`, `ib_mad_api.h` (vendored `third_party/ibmad-shim/` when `--enable-ib-mad-dlopen`) |
| IB vs HFI routing | `ib_common.c` (`ib_hca_is_opa_hfi`), `ib_family.c` |

### x86 counters (direct perf, 3.1 Phases 2+3)

The monitor links **no LIKWID** (or any other PMU library) on any architecture. On x86
(`--with-cpu-counter-backend=x86`, the `auto` choice on x86_64) core, uncore and RAPL counters are
read by direct `perf_event_open`, the same model as Grace (see the 3.1 migration notes below and
[LIKWID_MIGRATION.md](LIKWID_MIGRATION.md) for the LIKWID (3.0) history). There is no MSR access
and no MSR fallback: legacy `intel_x86_pmc_gpr4` / `gpr8`, `amd_x86_pmc` and bare `amd_x86_uncore_df`
collectors stay removed.

- **Never pinned, always scaled.** One fd per event (no groups), opened at startup / SIGHUP
  only and read each tick with `time_enabled` / `time_running` scaling, like `perf stat`. When
  another program holds counters (`perf`, VTune, a user's LIKWID job, the NMI watchdog) the
  kernel multiplexes and the value is a scaled estimate instead of being stolen or zeroed;
  nothing is re-armed per tick.
- **Fail soft.** A missing PMU or failed open leaves `host_cpu_hw` (and the uncore / RAPL types)
  enabled; each key without a counter emits its value sentinel (`NOT_PRESENT`,
  `PERMISSION_DENIED`, `PMU_BUSY`, `READ_FAILED`, `NOT_SCHEDULED`, `COUNTER_RESET`) and one
  journal line names the event.
- **`host_cpu_hw` event table** (`x86_core_perf.c`, every raw code verified on that class):

  | Class | Keys filled |
  |-------|-------------|
  | Intel SKX / CLX / ICX / SPR / EMR / GNR | `instr_retired_any` + `instr_retired` (instructions), `cycles_unhalted_core` + `aperf` (cycles), `cycles_unhalted_ref` + `mperf` (ref-cycles), `l1d_replacement`, `mem_load_uops_retired_{l1,l2,llc}_hit` (`0xd1/0x01,0x02,0x04`), the eight `fp_arith_inst_retired_*` widths |
  | AMD Milan / Genoa (Zen3 / Zen4) | `retired_instructions`, `retired_branch_instr`, `retired_misp_branch_instr`, `ls_dispatch`, `fp_ops_retired` (total FLOPs), plus `instr_retired` / `aperf` from the generic events |
  | AMD Turin (Zen5) | the Zen3 / Zen4 set plus `amd_fp_dp_ops` (packed + scalar double FLOPs; single = total − double) |
  | Other x86 (SRF, Rome, client parts) | the generic events only (Intel: the instructions / cycles / ref-cycles key pairs; AMD: `instr_retired` / `aperf`); one journal line; every other key `NOT_PRESENT`; no `pmu_solo_running_time` |

  On AMD the Intel fixed-counter names (`instr_retired_any`, `cycles_unhalted_core`,
  `cycles_unhalted_ref`) and `mperf` emit `NOT_PRESENT` (Genoa has no `ref-cycles`). Util /
  clock / `arm_*` / `dram_chan*` keys emit `NOT_PRESENT` on x86 rows (Grace fills the util /
  clock / `arm_*` keys; AMD DRAM traffic is on the `amd_x86_uncore_df_*` `chan<N>` rows). Shared counter keys are `W=48` (lifetime modulo 2^48); `fp_ops_retired`,
  `amd_fp_dp_ops` and the `pmu_*` times are `W=64`. **Idle CPUs may show flat rows**; judge
  health under load (`taskset -c N bash -c 'while true; do :; done'`). With `@full` tier tokens
  census awk uses **CPU = `$2`**, Intel fixed counters **`$10 $11 $12`**.
- **PMU sharing (D6) on x86.** `host_cpu_hw` rows carry `pmu_enabled_time`, `pmu_running_time`
  and, on a class with an event row, `pmu_solo_running_time` (see
  [Grace PMU sharing keys](#grace-pmu-sharing-keys-31-phase-1b-d6) for the arithmetic). Intel
  fixed-counter events count as always running; general events share 8 counters per CPU on Intel
  (assumes Hyper-Threading **off**, true on every measured node; with HT on there are 4 and the
  solo share is overstated) and 6 on AMD (event `0x03` FLOPs use a merged pair, two slots). The
  kernel NMI watchdog holds Intel's fixed cycles counter, so on nodes with `nmi_watchdog=1` the
  cycles event multiplexes and shows up as outside sharing; that is expected.
- **Uncore** (`x86_uncore_perf.c`): `intel_x86_uncore_imc_{skx,icx,spr,emr,gnr,srf}` read CAS
  counts on every `uncore_imc_<N>` PMU (`mbox<N>` rows; ICX keeps `mdev<N>`), SPR / EMR also
  `hbm_cas_*` on every `uncore_hbm_<N>` (`hbm<N>` rows, up to 32; each HBM CAS is 32 bytes),
  GNR / SRF scheduler 0 (`CAS_COUNT_SCH0`). `amd_x86_uncore_df_{rome,milan,genoa,turin}` read
  64-byte DRAM data beats on `amd_df`, one `chan<N>` row per DRAM channel (8 on Rome / Milan,
  12 on Genoa / Turin): Zen2 / Zen3 count read + write in one event (`dram_data_beats`), Zen4 /
  Zen5 split `dram_read_beats` / `dram_write_beats`. Events come from sysfs names first, else
  verified raw codes; no box count is assumed. Each row sums every CPU of its PMU's `cpumask`
  (one per socket or die), so a row covers **all sockets**. SKX `dram_act_count` /
  `dram_pre_count_miss` and the DF rows of Rome channels 2–7 emit `NOT_BUILT` (no
  hardware-verified encoding yet; Turin channels 0–11 are verified, and Genoa channels 0–11
  are verified on c318-005, 0.982). Each
  row also carries `pmu_enabled_time` / `pmu_running_time`. On el8 kernels `amd_df` counts
  socket 0 only (LS6 Milan, D13); begin logs that once.
- **RAPL** (`x86_rapl.c`): see the `intel_x86_rapl` / `amd_x86_rapl` bullet under
  [Power telemetry](#power-telemetry-hwmon-nvmldcgm-rapl-and-interpretation).
- **CPU classes.** Intel types target SKX+ server parts: Skylake-X (`06_55` stepping &lt; 5),
  Cascade Lake (`06_55` stepping ≥ 5), Ice Lake server (`06_6a`/`06_6c`), Sapphire Rapids
  (`06_8f`), Emerald Rapids (`06_cf`), Granite Rapids (`06_ad`) and Sierra Forest (`06_af`).
  Intel CHA/CBOX collectors are not shipped; Sandybridge through Broadwell are not classified
  (`intel_x86_pcu` and pre-SKX uncore types removed). EPYC types use server model ranges only
  (`amd_cpuid_match.c`).

`host_roofline_peak` adds
`cpu_peak_hbm_bw_bytes_per_s` (EDAC HBM/DDR split, `peak_calc_version` 2).

**Peak source enums:**

`gpu_peak_source`:

| Value | Meaning |
|------:|---------|
| 0 | Fail-open (unknown / zero) |
| 1 | Probed (DRM sysfs) |
| 2 | Vendor NVML (GPU FLOPS) |
| 3 | Vendor `nvidia-smi` (FLOPS / PCIe IO) |
| 4 | GPU identity table (allowlisted HBM peak by name) |

`cpu_peak_source`:

| Value | Meaning |
|------:|---------|
| 0 | Fail-open |
| 1 | Probed (EDAC / procfs) |
| 2 | Identity table (Grace CPU part `0xd4f`, or x86/AMD CPUID DRAM/HBM allowlist) |

**Horizon / Vista / Stampede3 / Lonestar6 GPU + CPU constants** (datasheet / TACC docs; allowlisted only — no soft invent):

| Identity | Peak key | Value |
|----------|----------|------:|
| GPU name contains `GB200` / `B200` | `gpu_peak_mem_bw_bytes_per_s` per GPU | `8e12` B/s (HBM3e) |
| GPU name `GB200` / `B200` | SM count for smi FLOPS fallback | `148` |
| GPU name contains `GH200` | SM count for smi FLOPS fallback | `132` |
| GPU name `GH200`, `memory.total` &lt; 140000 MiB | `gpu_peak_mem_bw_bytes_per_s` per GPU | `4e12` B/s (HBM3; Vista `97871`) |
| GPU name `GH200`, `memory.total` ≥ 140000 MiB | `gpu_peak_mem_bw_bytes_per_s` per GPU | `4.9e12` B/s (HBM3e) |
| GPU name contains `GH200` | `gpu_peak_io_link_bw_bytes_per_s` per GPU | `900e9` B/s (NVLink-C2C) |
| GPU name contains `H100` | SM / HBM per GPU | `132` / `3.35e12` B/s |
| GPU name contains `A100` | SM / HBM per GPU | `108` / `1.555e12` B/s (A100-40) |
| GPU name `RTX PRO 6000` / `GB202` | SM / GDDR per GPU | `188` / `1.597e12` B/s |
| CPU part `0xd4f` (Grace) | `cpu_peak_dram_bw_bytes_per_s` | `512e9` B/s (LPDDR5X) |
| `SKYLAKE_X` (EDAC speed miss) | DRAM | `256e9` B/s (TACC S3 SKX) |
| `CASCADE_LAKE` | DRAM | `282e9` B/s (DDR4-2933 6ch×2) |
| `ICELAKE_SERVER` | DRAM | `409.6e9` B/s (TACC S3 ICX) |
| `SAPPHIRE_RAPIDS` + model `Max` | `cpu_peak_hbm_bw_bytes_per_s` | `3.2768e12` B/s (2×1638.4 GB/s) |
| `SAPPHIRE_RAPIDS` (non-Max, e.g. 8468) | DRAM | `614.4e9` B/s (DDR5-4800 8ch×2) |
| `AMD_MILAN` | DRAM | `409.6e9` B/s (LS6 DDR4-3200) |
| `AMD_GENOA` | DRAM | `921.6e9` B/s (2×12ch DDR5-4800) |
| `AMD_TURIN` | DRAM | `1.228e12` B/s (2×614 GB/s AMD 9555) |

Match order for GPU names: `GH200` → `GB200`/`B200` → `RTX PRO 6000`/`GB202` → `H100` → `A100`.
Inventory fixtures under `tests/fixtures/{stampede3,ls6,vista,horizon}_lspci_profiles/`.

FLOPS path: NVML-first (SM count + SM clock); smi fallback uses confirmed fields
`name,clocks.max.sm,compute_cap` plus the SM identity table — **never** invalid
`cuda.cores` / `multiprocessor.count` on this driver stack. The FP64:FP32 ratio
comes from compute capability (`roofline_nvidia_fp64_ratio_from_cc`): Kepler CC 3.5/3.7
non-GeForce parts and GeForce TITAN use 1/3, other Kepler 1/24, so on K20/K40/K80 hosts
`gpu_peak_fp64_flops_per_s` is ~10.7x higher than from monitors that applied 1/32. When DRM mem attrs are
empty, mem BW uses allowlisted HBM/GDDR tables. For **GH200**, DRM may
expose misleading **PCIe x1** while C2C is the real CPU–GPU link — published C2C
replaces weaker PCIe-derived IO. Do **not** infer HBM3e from marketing substrings
like `120GB` in the product name; use smi `memory.total`. When EDAC speeds are
missing, Grace DRAM uses the CPU-part table, then x86/AMD CPUID DRAM/HBM identity.
CPU FLOPS remain procfs/cpufreq. Fleet note: probed S3/LS6 classes often have
EDAC DIMMs without `dimm_mem_speed` — CPU identity is the primary DRAM path there.

**Verify peaks by host + time** (never `jid` alone on `host_data`), e.g.:

```sql
WITH j AS (
  SELECT host_list[1] AS host0,
         COALESCE(telemetry_first_time, start_time) AS t0,
         COALESCE(telemetry_last_time, end_time) AS t1
  FROM job_data WHERE jid = '<jid>'
)
SELECT hd.event, COUNT(*) AS n, MIN(hd.value) AS vmin, MAX(hd.value) AS vmax
FROM host_data hd CROSS JOIN j
WHERE hd.host = j.host0
  AND hd.time >= j.t0 AND hd.time <= j.t1
  AND hd.type IN ('host_roofline_peak', 'roofline_hw_peak')
  AND hd.event LIKE '%peak%'
GROUP BY 1 ORDER BY 1;
```

**Consumer follow-up (analysis):** roofline/mbw still probe `dram_cas_*` only;
HBM-primary SPR needs `hbm_cas_*` pairs in analysis (see plan
`spr-imc-mbox-nan`).

## Value sentinels

A key the daemon could not read this cycle is emitted as the token `-<n>` in place of its value,
so an emitted `0` always means "read, and the counter was 0". Codes come from the catalog in
`src/stats_sentinel.h` (append-only; the consumer mirror is `hpcperfstats/lib/monitor_sentinel.py`):

| Token | Slug | Meaning | Counter chain |
|-------|------|---------|---------------|
| `-1` | `NOT_UPDATED` | no collector set the key this cycle (skipped tick, early return) | bridgeable |
| `-2` | `NOT_PRESENT` | the counter does not exist on this host (absent file, foreign CPU key) | breaks |
| `-3` | `READ_FAILED` | open / read / parse failed | bridgeable |
| `-4` | `PERMISSION_DENIED` | `EACCES` / `EPERM` | breaks |
| `-5` | `PMU_BUSY` | counter register in use by another program | breaks |
| `-6` | `NOT_SCHEDULED` | perf event not scheduled this interval | breaks |
| `-7` | `BACKEND_ERROR` | a backend library call (DCGM / NVML / MAD / XPUM) failed | bridgeable |
| `-8` | `INVALID_VALUE` | the backend returned a blank / NaN / poisoned value | bridgeable |
| `-9` | `BACKOFF` | source in failure backoff or a slow-cycle cooldown | bridgeable |
| `-10` | `NOT_BUILT` | not compiled in, or not programmed by this build's eventset | breaks |
| `-11` | `COUNTER_RESET` | the counter re-seeded; that interval's activity is lost | breaks |

- **Per-cycle reset.** Each collect cycle marks every key active in the phase `NOT_UPDATED`;
  `s_val` keeps the last reading, so `E` counter arithmetic (mod 2^W) is unchanged. Rows of
  transient entities (`host_proc` processes) are dropped after a cycle that touched none of
  their keys; real-device rows (cores, ports, GPUs, sockets, filesystems) persist and an
  untouched key keeps emitting `-1`.
- **Wire.** `$sentinels <catalog version>` follows `$build` in the `$` banner and the capability
  slug carries `sent<N>`. Bridgeable codes mean the source kept counting, so the next valid value
  is >= the last one (mod 2^W); every other code starts a new chain. Both CPU backends (`x86perf`
  and `arm`) bridge `host_cpu_hw` the same way, and the cross-sample validators FAIL a
  `host_cpu_hw` bridge violation on every slug (no backend is downgraded to a note).
- **Knob.** `emit_value_sentinels 1` (default) in `hpcperfstats.conf`; `0` prints `s_val` exactly
  as before sentinels (byte-identical legacy wire, no `$sentinels` banner); the slug keeps
  `sent<N>` because it names the build, not the runtime setting.
- **Rollout: consumer first.** Deploy the consumer that decodes `-<n>` (ingest, readers, Job
  Detail reasons) on every site before installing a monitor with the knob on; an older consumer
  would store a sentinel as a huge unsigned value. `emit_value_sentinels 0` is the escape hatch.
- **No key renames**: `docs/monitor_variable_rename_map.yaml` is unchanged.
- **Validators.** `scripts/lib/row_validate.py` accepts an unsigned integer or a catalog `-<n>`;
  `cross_sample_validate.py` skips chain-breaking gaps, FAILs a bridgeable gap whose next value
  dropped, and prints `NOTE sentinel` counts per code. A capture without the `$sentinels`
  banner reports the sentinel layers as `SKIP`, never `PASS`, and any sentinel token in it FAILs.
- **DEBUG verification layers** (`rpm_debug_shm_verify.sh` / `validate_stampede3_profile.sh`, on by
  default; opt out per layer with `STRICT_SENTINELS=0`, `SENTINEL_PROBE_CHECK=0`,
  `INDEPENDENT_READ_CHECK=0`, `CONSUMER_PARSE_CHECK=0`): the expectations manifest carries
  `allowed_sentinels` / `expect_not_built` per type and key (a code outside it FAILs; a key the
  build cannot read that emits a value FAILs); `NOT_PRESENT` is checked against `/proc/vmstat`,
  IB / OPA sysfs counters and the hwmon power meters (nvidia_gpu reports SKIP: no read-only DCGM
  field probe); a key `NOT_UPDATED` in two consecutive captures FAILs with its collector site; a
  live counter read (`/proc/stat`, IB / OPA counters, `/sys/block/<dev>/stat`) between two
  captures must lie between the emitted values, and a gauge (node `meminfo`, hwmon power) emitted
  0 in both captures while its source is non-zero FAILs; the
  captures go through the consumer parser (`scripts/consumer_parse_check.py`, repo `.venv`).
  The full list of checks (and the counter-accuracy layer that follows validation) is in
  [Correctness check inventory](#correctness-check-inventory).
- **DEBUG provenance** (`--enable-debug` only): every `stats_set_missing*` call site registers in
  a linker-section registry; `/dev/shm/hpcperfstatsd-debug/sentinels` lists each non-OK key as
  `type dev key code slug errno site first_ts last_ts count`, `sentinel_sites` lists every site
  with its hit count, `fault_fired` lists each configured `debug_fault_inject` entry as
  `<entry> <fires>` (empty without one), and each state transition is logged once. `debug_fault_inject` in the conf
  forces failures at the fault points `open`, `opendir` (I/O primitives), `perf_open` (event
  name), `mad_query` (HCA), `dcgm_fetch`, `nvml_fetch` (the NVML+GPM sample; NVML is given up
  and the next collect uses DCGM), `hwmon_read` (meter name), `scandir` (host_proc),
  `statfs` (host_tmpfs) and `net_rebuild_slow` (host_net), as
  `point:subject-substring!errno@[delay+]cycles` (`@1+3`: one clean cycle, so the rows exist,
  then three failing ones). `tests/test_fault_inject_matrix.c` and
  `tests/test_fault_inject_hw.c`, `_fs.c`, `_cpu.c` and the coverage twins of the collector
  sentinel tests (all built with `-DDEBUG` in every build) FAIL on any linked failure site never
  exercised; together they link every src file with a site (`scripts/lib/fault_ci_files.py`,
  checked by `tests/test_fault_ci_files.sh`).
- **Live fault injection** (`FAULT_INJECT`, default 1; `FAULT_INJECT=0` to skip; release RPMs
  print a SKIP line): after validation `rpm_debug_shm_verify.sh` runs
  `scripts/fault_inject_verify.sh`, which restarts the DEBUG daemon with `debug_fault_inject` for
  `FAULT_INJECT_TICKS` (3) cycles after one clean cycle on every injectable point the node has
  (the `host_ib` points only when a non-OPA port's `link_layer` is InfiniBand or absent, as the
  daemon collects, so RoCE-only nodes skip them; `perf_open` from begin; the
  `/sys/class/infiniband` opendir fault last and alone, since it stops the port walk and its
  directory latch lasts until the restart), captures the samples the plan needs, restores the conf, and checks that the mapped codes land on only the planned keys, values come
  back after release (where the read path does not latch), bridgeable gaps keep counters
  monotonic, and every site in `sentinel_sites` fired: a never-fired site is a NOTE when make check
  forces its file (`fault_ci_files.py`) or its collector is not active on the node, else FAIL.
  The plan counts collect cycles; with the slow tier a cycle writes either `fast` or `full`, so
  the capture reads both mirrors, numbers their samples by header timestamp, and a code counts
  when either mirror shows it inside the entry's window (a one-cycle fault often lands only in
  `fast`); the resume check reads the first `fast` and the first `full` sample after release (the
  slow-tier keys come back only in `full`). With `nvidia_gpu` in the schema both GPU points are
  planned, since only the run shows which source serves the node: `nvml_fetch` first, then
  `dcgm_fetch` after the `nvml_fetch` resume check, so on an NVML+GPM node it fires on the DCGM
  fallback. A fired `nvml_fetch` passes when, after release (outside the `dcgm_fetch` window),
  every GPU row is still emitted and reads values not all frozen at the pre-fault reading, or
  carries `BACKEND_ERROR` on every key (DCGM unusable on the node); rows that vanish or values
  frozen at the pre-fault reading FAIL, and the DCGM path's `NOT_PRESENT` / `READ_FAILED` /
  `INVALID_VALUE` keys (GPM-only `tensor_dmma_active`, fields its watch profile lacks) are not out
  of plan. A planned point that `fault_fired` reports with 0 fires (`nvml_fetch` on a
  DCGM-served node, `dcgm_fetch` when DCGM never fetches) is a NOTE, not a FAIL; an older daemon
  without `fault_fired` keeps the FAIL. When no planned point fired at all
  the layer is `NOT_RUN` (`fault_inject: NOT_RUN` line, `fault_inject` check `NOT_RUN` in the
  campaign JSON).
- **Release log**: the hourly status adds `sentinels: SLUG=n ...` (tokens emitted per code since
  the last hour; `sentinels: none` when there were none).
- **Error-path lint**: `scripts/check_sentinel_error_paths.py src` (in `make check`) flags
  `stats_set(..., 0)` or a silent `continue` / `return` / `goto` after a failed read; intentional
  cases go in `scripts/check_sentinel_error_paths.allowlist` with a reason.

## Two-tier collection (fast/slow) and sparse rows

The daemon supports a two-tier sampling scheme to reduce the volume of data sent
without losing freshness on performance metrics. It is **on by default**
(`enable_slow_tier 1`). Set `enable_slow_tier 0` to restore legacy single-tier
behavior (every key collected and emitted full-width every `sample_freq`).

| Config key | Default | Role |
|-----------|---------|------|
| `sample_freq` | `30` | Fast-tier collection interval (performance keys). |
| `sample_freq_slow` | `600` | Full-collection interval; clamped to be `>= sample_freq`. |
| `send_freq` | `300` | RabbitMQ drain interval (unchanged, independent of the tiers). |
| `enable_slow_tier` | `1` | Master switch for tiering and sparse rows. |

When `enable_slow_tier 1` (default):

- **Key tiers** come from a static `(type, key)` table plus an auto-rule for any
  key ending in `_error`/`_errors` (`collect_tier.c`). Slow keys carry a new
  `,R=S` suffix on `!` schema lines so consumers can learn tier membership;
  fast keys are unmarked. Use `R=S` directly in a `KEYS` macro to mark a key slow
  at the schema level.
- **Fast samples** (every `sample_freq`) collect and emit only fast-tier keys.
- **Full samples** (every `sample_freq_slow`) collect and emit every key.
- **Sample rows** gain a tier marker right after the device field:
  - `type dev @fast v...` — fast-tier values only, in schema order.
  - `type dev @full v...` — all values, in schema order.
  - `type dev v...` (no `@` token) — legacy full-width row (slow tier disabled).

### Invariant: `$` messages are always full

Any payload whose first non-whitespace byte is `$` (schema/rotation message) is
**always emitted full**: the schema block lists every key (with `,R=S` on slow
keys) and the appended sample rows use `@full`, never `@fast`. This is enforced
in `stats_buffer_row_tier_decide()` (`stats_buffer_rows.c`) and by forcing
`COLLECT_FULL` on every `write_hdr=1` collect in `monitor_daemon.c`, preserving
the `current`-file rotation semantics that `listend.py` relies on.

### Consumer rollout

`@fast`/`@full` sample rows and the `,R=S` schema suffix are a **new monitor
output contract**. Downstream ingestion in
`HPCPerfStats/hpcperfstats/dbload/lib/sync_timedb_parsing.py` now strips tier
markers and zips values against the fast vs full schema subset. Deploy monitor
and consumer updates together on clusters that ingest sparse rows.

Set `enable_slow_tier 0` only as an escape hatch for sites still running an
older consumer stack (restores legacy full-width rows with no `@` tokens).

Collection gating skips slow-key `stats_set`/`stats_inc` on fast ticks when the
slow tier is enabled, reducing host CPU as well as wire volume.

### IB driver merge (`host_ib`)

The former `host_ib_ext` and `host_ib_sw` typenames are merged into **`host_ib`**
with a single sysfs walk per cycle. Migration notes for historical archives:

| Legacy | Current |
|--------|---------|
| `host_ib_ext` / `host_ib_sw` typename | `host_ib` |
| Device id `mlx5_0/1` | `mlx5_0.1` |
| Switch keys `rx_bytes`, `tx_bytes`, … | `sw_rx_bytes`, `sw_tx_bytes`, … |

See `HPCPerfStats/docs/monitor_variable_rename_map.yaml` for rename-map entries.

### IB MAD extended keys retired (`host_ib`)

The host-port MAD PortCountersExtended query (from the 2016 `ib_ext` collector, merged into
`host_ib`) is gone. On Horizon GB200 (c122-018) it timed out against the port's own LID
(`BACKEND_ERROR`), and where it answered it read about 4x below the kernel counters because
fabric management clears the PortCounters behind it. The same row already carries the kernel's
64-bit sysfs counters.

| Retired key | Use instead | Notes |
|-------------|-------------|-------|
| `port_xmit_pkts`, `port_rcv_pkts` | `port_xmit_packets`, `port_rcv_packets` (sysfs `counters/`, `E,W=64`) | **Keys removed from the schema.** The consumer reads `port_*_packets` first and falls back to these keys for archives written before this monitor; **deploy the consumer change first**. |
| `port_unicast_xmit_pkts`, `port_unicast_rcv_pkts`, `port_multicast_xmit_pkts`, `port_multicast_rcv_pkts` | none | Removed; nothing consumed them, so they do not move to sysfs. |
| `port_select`, `counter_select` | none | Query metadata, not counters. |

`port_xmit_data` / `port_rcv_data` keep their names and are now sysfs on every port (the MAD
query used to overwrite them where it answered: a one-time step on such ports at the upgrade).
The switch-side `sw_*` keys stay on MAD, unchanged. Rename-map entries live in
`HPCPerfStats/docs/monitor_variable_rename_map.yaml` (`removed_legacy`).

`hpcperfstats/` uses the IB packet counters only in the `avg_packetsize` and `max_packetrate` job
metrics (`analysis/metrics/lib/metrics.py`): since 33a11f77 they read host_ib
`port_xmit_packets` / `port_rcv_packets` first and fall back to `port_xmit_pkts` /
`port_rcv_pkts` for history, and `avg_packetsize` scales `port_*_data` from 4-byte words to bytes
(x4).

### `host_cpu_hw` util accumulators (`cpu_util_*_accum_us`, aarch64 `arm` backend, formerly `dcgm`)

`cpu_util_{total,user,sys,irq,nice}_accum_us` are now **CPU microseconds** (util% / 100 x wall
µs). Earlier builds published util **percent** x µs, i.e. 100x the real value (found on Horizon
c122-018: DCGM user time was 100.04x `/proc/stat` user time on loaded CPUs). Key names and the
`E` schema are unchanged; archives span a 100x step at the upgrade. No `hpcperfstats/` code
reads these keys today; anything that derived seconds from them divided by 1e6 and must now
not also divide by 100.

### Grace `host_cpu_hw` counters (3.1: direct perf on aarch64)

3.1 replaces the aarch64 LIKWID (3.0) overlay with direct `perf_event` readers (`arm_core_perf`,
`arm_imc_scf`). Every read is scaled by time_enabled / time_running, like `perf stat`.
On GB200 / GH200 `nvidia-powerd` holds most Grace core PMCs, so the kernel multiplexes the
monitor's events and the unscaled LIKWID reads of 3.0 were low by the time-sharing factor:
measured about 3.3× (3.2–3.5×) on a GB200 node with `nvidia-powerd` (c122-018; see the
rate-step note below). The 3.1 scaled counts themselves are accurate to about 1–2% even when
`nvidia-powerd` leaves the monitor 17% of the counter time (`scripts/canary_counter_check.sh`
against `perf stat` and the GEMM's own FLOP rate). GH200 and Grace-Grace steps are not
measured; do not assume nodes without `nvidia-powerd` are unchanged.

| Before (≤ 3.0) | From 3.1 | Notes |
|----------------|----------|-------|
| `instr_retired`, `aperf` / `mperf` / `cpu_clock_est_cycles` (measured part) from LIKWID (3.0), unscaled | same keys, scaled direct perf | **Rate step at the upgrade:** Grace instructions and cycles rise by the old time-sharing factor, measured about 3.3× (3.2–3.5×) on GB200 with `nvidia-powerd` (expected, not a workload change). GH200 and Grace-Grace are not measured; the few GH200 3.0 samples without `nvidia-powerd` also rose (roughly 1.4–1.8×). No backfill; older jobs keep the old values. Widths unchanged: W=48 keys carry the lifetime modulo 2^48, `cpu_clock_est_cycles` is W=64. |
| — | `arm_fp_dp_spec`, `arm_fp_sp_spec`, `arm_fp_hp_spec` (`E,W=64`) | FP **instructions** by precision (all vector widths; `FP_*_SPEC`). |
| `arm_est_flops` = scalar SP + DP (always 0: never programmed) | `arm_est_flops` = `FP_FIXED_OPS_SPEC` + `FP_SCALE_OPS_SPEC` | Measured FLOPs. |
| `arm_int8_ops`, `arm_int16_ops` (always 0) | `ASE_SVE_INT8_SPEC`, `ASE_SVE_INT16_SPEC` | SVE integer instructions, W=48. |
| `fp_arith_inst_retired_scalar_{single,double}` | stay 0 on Grace | Intel event names. |
| `arm_aarch64_imc` via SCF counters in the LIKWID (3.0) group (`PENDING` until the overlay was up) | own perf fds per `nvidia_scf_pmu_<N>`; re-scan while no memory PMU is bound | Same keys and units (64-byte CAS). GH200 nodes report one socket. |

FP and SVE events are resolved from sysfs names first, then from a MIDR-keyed raw-code table with
one row (Arm Neoverse V2, MIDR `0x410fd4f0`). Another core logs its MIDR once and leaves those keys
at 0 until a verified row is added. A key whose event is not open is not published. The analysis
side reads the new keys as `avg_fp{64,32,16}_instr`; see
`HPCPerfStats/docs/monitor_variable_rename_map.yaml`.

### Grace PMU sharing keys (3.1 Phase 1b, D6)

Phase 1b (still version 3.1) publishes how much of the time the Grace counters actually ran, so a scaled count can be told
from a directly counted one. The keys come from each scaled read's own enabled / running times (no
process-name checks, no extra syscalls):

| Type | Key | Meaning |
|------|-----|---------|
| `host_cpu_hw` | `pmu_enabled_time` (`E,W=64,U=ns`) | Time the CPU's open core events were enabled, summed over events. |
| `host_cpu_hw` | `pmu_running_time` (`E,W=64,U=ns`) | Time they actually ran on a counter. |
| `host_cpu_hw` | `pmu_solo_running_time` (`E,W=64,U=ns`) | Time they would run if the monitor were the only PMU user: the cycle event on the dedicated cycle counter runs all the time; general events get min(1, general counters ÷ open general events). Published only for a core with a verified MIDR row (Neoverse V2: 6 general counters, so the monitor's 8 general events run ~75% on an otherwise idle PMU). |
| `arm_aarch64_imc` | `pmu_enabled_time`, `pmu_running_time` (`E,W=64,U=ns`) | Same for each SCF device's read and write events (no solo baseline for uncore). |

They are counters rather than a percent gauge because ingest sums `host_cpu_hw` rows across a
host's CPUs: the host's share is Δrunning ÷ Δenabled, and the share lost to other programs (for
example `nvidia-powerd` on GB200) is 1 − Δrunning ÷ Δsolo. Analysis reports these as
`avg_pmu_running_pct` and `avg_pmu_outside_pct` (`METRICS_CONTRACT_VERSION` 9). Deploy the analysis
before the RPM build that carries these keys. `scripts/canary_counter_check.sh` prints the three shares per load.

### x86 `host_cpu_hw`, uncore and RAPL (3.1 Phases 2+3: direct perf, LIKWID removed)

Phases 2 and 3 (still version 3.1) replace every LIKWID (3.0) path on x86 with the direct perf readers
described in [x86 counters](#x86-counters-direct-perf-31-phases-23). Type names, device names and
key names are unchanged (new keys are additive), but several values change meaning or scale at
the upgrade. No backfill; older jobs keep the old values. Deploy the analysis release with
`METRICS_CONTRACT_VERSION` 12 together with this RPM.

| Before (LIKWID, 3.0) | From 3.1 Phases 2+3 | Notes |
|-----------------|---------------------|-------|
| `mem_load_uops_retired_l1_hit` on ICX / SPR / EMR / GNR = **all loads** (`MEM_INST_RETIRED.ALL_LOADS` alias) | **L1 hits** (`0xd1/0x01`), as on SKX / CLX | **Meaning change (D18):** new values are lower (hits only). Do not compare the two across the upgrade. |
| `mem_load_uops_retired_l2_hit` / `_llc_hit` on ICX+ `NOT_BUILT` | L2 / L3 hits (`0xd1/0x02`, `0x04`) | Every Intel class now fills all three load-hit keys. |
| Intel `fp_arith_inst_retired_*` `NOT_BUILT` (zeros before sentinels) | real counts (`0xc7`, umasks `0x01`–`0x80`) | Intel FLOP rates and the CPU precision mix get values. |
| AMD `instr_retired`, `aperf` `NOT_BUILT` | generic instructions / cycles | `instr_retired_any`, `cycles_unhalted_core`, `cycles_unhalted_ref`, `mperf` stay `NOT_PRESENT` on AMD. |
| — | `fp_ops_retired` (`E,W=64,U=FLOP`) on Milan / Genoa / Turin | Total retired SSE/AVX FLOPs (no precision split on Zen3 / Zen4). |
| — | `amd_fp_dp_ops` (`E,W=64,U=FLOP`) on Turin | Double-precision FLOPs (packed + scalar); single = `fp_ops_retired` − `amd_fp_dp_ops` (bf16 lands in single). |
| — | `host_cpu_hw` `pmu_enabled_time` / `pmu_running_time` / `pmu_solo_running_time` on x86 | D6 sharing keys, same arithmetic as Grace; analysis `avg_pmu_running_pct` / `avg_pmu_outside_pct` cover x86 rows. |
| core counters unscaled, core group re-armed every tick (another holder could zero or steal them) | scaled by enabled / running, never re-armed | Under counter contention old values read low; new ones are scaled estimates. No fleet-wide factor is measured. W=48 keys carry the lifetime modulo 2^48 as before. |
| Intel IMC / HBM rows from the LIKWID uncore group (about one socket's traffic on 2-socket nodes) | each row sums its PMU `cpumask` CPUs (**all sockets**) | **Scale step:** about **2×** on 2-socket nodes for `dram_cas_*` / `hbm_cas_*`. Every `uncore_imc_<N>` PMU is read (no MBOX0–11 limit), SPR / EMR up to 32 `hbm<N>` rows (no channel ladder). |
| Zen4 / Zen5 `dram_chanN_bytes` = read beats | read **+ write** beats | **Scale step** on Genoa / Turin: channel traffic now includes writes. Zen2 / Zen3 unchanged (one combined event). |
| one `df` row with `dram_chan0_bytes` .. `dram_chan3_bytes` (channels 0–3 only; the values were 64-byte beats despite the name) | one `chan<N>` row per DRAM channel: `dram_data_beats` (Zen2 / Zen3, read + write) or `dram_read_beats` / `dram_write_beats` (Zen4 / Zen5) | **Breaking rename and scale step.** Milan channels 4–7 were never read, so DF bandwidth read half (c317-016: daemon 0.506 of the load's bytes, all 8 channels 0.997). Turin rows 4–11 now carry beats (c573-002: all 12 channels 0.986 of the read load's bytes, channels 0–3 alone 4/12), so Turin DF bandwidth steps up about 3x at that upgrade (no backfill). Genoa rows 4–11 carry beats too (c318-005: all 12 channels 0.982 of the read load's bytes, channels 0–3 alone 0.328), with the same step. Consumers sum the beat keys over every `chan<N>` row (x 64 B) and dual-read the old keys for history; deploy the consumer change with or before this monitor. |
| — | IMC / HBM / DF rows `pmu_enabled_time` / `pmu_running_time` (`E,W=64,U=ns`) | Uncore sharing; no solo key. |
| `intel_x86_rapl` / `amd_x86_rapl` published each read's energy window | running total in mJ since begin, modulo 2^32 | Keys and `E,W=32,U=mJ` unchanged; Δ / Δt is now watts (fixes finding #48). |
| capability slug token `likwid` | `x86perf` (`configure.cpu_backend: "x86"`) | Consumers treat x86 `host_cpu_hw` chains as bridgeable only with `x86perf`. |
| `amd_df` on el8 (LS6) | unchanged: socket 0 only, now logged at begin | Until LS6 moves to el9 (D13). |

Build, configure and validation changes in the same release:

- **Configure.** `--with-cpu-counter-backend={auto|x86|arm}`: `auto` (default) is `x86` on x86
  and `arm` elsewhere; `x86` is rejected on non-x86 hosts. The x86 backend defines
  `MONITOR_CPU_BACKEND_X86` (Automake conditional `CPU_BACKEND_X86`); `likwid` is
  now rejected as a bad value. `--with-monitor-arch` and its Intel / AMD macros are gone: the
  vendor is read at runtime from the processor table.
- **Static bundle.** `build_static_bundle.sh` pins only libev 4.33 and rabbitmq-c 0.18.1, plus
  libbpf for the eBPF metric profiler.
- **Capabilities.** `monitor-build-capabilities.json` carries `configure.cpu_backend: "x86"` and
  the slug token `x86perf` (for example
  `x86_64-ver3.1-hw-ib-nvgpu-intelgpu-lustre-beegfs-x86perf-sent1-slowtier1`); `likwid_overlay` /
  `papi_hybrid` are no longer emitted.
- **Begin order.** Every type begins in plain registry order, `host_cpu_hw` included (each
  direct perf reader opens its own fds; there is no "begin `host_cpu_hw` first" case).
- **Validators.** An `x86perf` build does not allow `NOT_BUILT` on `host_cpu_hw`; the
  cross-sample validators no longer downgrade x86 `host_cpu_hw` bridge violations; the DEBUG
  fault plan adds `perf_open:instructions!16` (EBUSY) on x86. The live device probes list the
  sysfs `uncore_imc_<N>` / `uncore_hbm_<N>` / `amd_df` PMUs (rows `mbox<N>`, `hbm<N>`, `mdev<N>`
  on ICX, `df`); the EDAC-based IMC probe is gone.
- **Environment.** `HPCPERFSTATS_LIKWID_*`, `HPCPERFSTATS_MONITOR_ARCH` and `LIKWID_FORCE` are
  removed; nothing in the daemon or the build scripts reads them.
- **Overhead harness.** x86 core, uncore and RAPL types form the `x86_perf` driver group
  (`measurement/lib/driver_map.py`).

The analysis side also halves SPR Max HBM bandwidth (`hbm_cas_*` are 32-byte CAS, D21) in the
same contract bump. See `HPCPerfStats/docs/MONITOR_VARIABLES.md`.

### Lustre collectors (`lustre_mdc` / `lustre_osc` / `lustre_llite`)

| Topic | Behavior |
|-------|----------|
| `lustre_mdc` / `lustre_osc` sources | Prefer modern `md_stats` / `osc_stats`, fall back to legacy `.../stats` |
| `lustre_osc` | Built and registered when `--enable-lustre` (previously source-only) |
| `lustre_llite` KEYS | Renamed to usable `vfs_*` names; capacity gauges `fs_bytes_*` / `fs_files_*` from `/sys/fs/lustre/llite` |
| llite opcode `stats` | `/proc/fs/lustre/llite/<inst>/stats` when that dir exists, else debugfs `/sys/kernel/debug/lustre/llite/<inst>/stats` (Lustre 2.15 moved it; root only); with neither, the `vfs_*` keys get the errno code (`NOT_PRESENT`) instead of staying `NOT_UPDATED`. Opcodes are emitted under the **new** KEY names only |
| Consumer follow-up | Separate plan: `.cursor/plans/lustre-llite-consumer-followup.plan.md` (ship consumer dual-read first or same release; do not deploy dashboards that hard-code old KEY strings without that work) |

Old→new llite event map lives under `type_events.lustre_llite` in
`HPCPerfStats/docs/monitor_variable_rename_map.yaml`.

### BeeGFS collector (`beegfs_client`)

| Topic | Behavior |
|-------|----------|
| Detection | Mounts with fstype `beegfs` / `beegfs_nodev`; device id = mount path |
| Capacity | `statvfs` → `fs_bytes_*` / `fs_files_*` every sample |
| I/O bytes | `vfs_write_bytes` / `vfs_read_bytes` = bytes acknowledged / received on the client's established TCP connections to its storage servers (`NETLINK_SOCK_DIAG` `tcp_info`, unprivileged); no fork, no server request, no DNS. These are wire bytes: they include BeeGFS message headers (+0.03% on 1 GiB direct I/O), count buffered writes when the client flushes them, and miss reads served from the client page cache |
| Mount to client | `/proc/fs/beegfs/<id>/config` `cfgFile` matched with the mount's `cfgFile=` option; else the only instance; else `NOT_PRESENT` |
| Totals | Per-socket growth (keyed by socket cookie) is added to a per-mount total, so reconnects never make the counter go backwards |
| Not countable | `NOT_PRESENT` when the client's `storage_nodes` lists RDMA or SDP connections (that traffic bypasses TCP), a node without a readable connection line, fewer matching sockets than its listed TCP connection counts (the listing names one address per server, so connections to a multi-homed server's other addresses would be missed), or no instance; a listing whose nodes are all idle (`<none>`) is a real 0 growth |
| Metadata ops | The 15 `vfs_*_ops` keys are `NOT_PRESENT`: the 7.3 client keeps no op counters and `beegfs-ctl --clientstats` makes every server dump its whole client table per call |
| Cadence | Slow/full when `enable_slow_tier`; else <= once per `max(sample_freq, 30s)`; skipped ticks re-publish the cached totals or code |
| Configure | `--enable-beegfs` (default yes); capability slug fragment `beegfs` |
| Consumer follow-up | Website FSIO includes BeeGFS alongside Lustre and NFS (`beegfs_client` → Resources / Summary / `detail_fsio_beegfs_*`) |

### DEBUG `/dev/shm` mirror (daemon only)

When built with **`--enable-debug`**, the RabbitMQ daemon mirrors the latest **full
outbound payloads** under **`/dev/shm/hpcperfstatsd-debug/`**:

| File | Content |
|------|---------|
| `schema` | Complete `$` rotation payload (`write_hdr=1`) |
| `fast` | Latest `@fast` sample (one payload) |
| `full` | Latest `@full` (or legacy full-width) sample (one payload) |
| `fast.burst` / `full.burst` | **All** payloads from the latest sample tick, concatenated (shared-node: N jid + host `-`) |
| `sentinels` / `sentinel_sites` / `fault_fired` | Value-sentinel provenance, failure-site hit counts and `debug_fault_inject` fire counts (see **DEBUG provenance** under value sentinels) |

On **shared-node** builds (`--enable-shared-node-multi-jid`), a multi-jid sample
burst emits N jid payloads plus one node-wide `-` payload. DEBUG shm keeps the
**last jid** sample in `fast`/`full` (the trailing `-` is still published to
RabbitMQ but does not overwrite those single-slot mirrors). The same tick’s
complete set is in `fast.burst` / `full.burst` (parts separated by
`#---burst-part---`). Idle (`disc.n==0`) still mirrors `-` into `fast`/`full`
and the matching `.burst` file.

At startup the daemon removes `fast`, `full` and both `.burst` files left by the previous
daemon (the new `schema` is written at once, the samples only on the first tick), so a reader
never pairs an old build's sample with the new schema. `validate_shm_messages.py
--wait-shm-seconds N` also waits until `fast` / `full` exist and their header timestamp is not
older than the `schema` header, and fails naming the stale file after `N` seconds.
`rpm_debug_shm_verify.sh` and `validate_stampede3_profile.sh` pass `N = 300` unless
`WAIT_SHM_SECONDS` is set: the first `fast` arrives one `sample_freq` after the first `full`.

Override the base directory with **`HPCPERFSTATS_DEBUG_SHM_DIR`** (must be an
absolute path under **`/dev/shm/`** with no `..` components). Payloads
contain job id, hostname, and workload metrics — treat as sensitive on shared
nodes. Files are created mode `0600` under a `0700` directory owned by the
daemon euid (`ensure_private_dir`; atomic `O_EXCL|O_NOFOLLOW` `*.tmp` +
`rename`). If the directory is removed while the daemon runs, the next sample
recreates it (writes do not abort the process).

### Dumpfiles and conf permissions

When RabbitMQ send fails, the daemon may spool samples under the configured
**dumpfile** directory (default **`/var/lib/hpcperfstats/dump`**, mode `0700`
via `ensure_private_dir` — not world-writable `/tmp`). Append creates use
`O_NOFOLLOW` and mode `0600`. RPM install places **`hpcperfstats.conf`** mode
**`0600`** (broker credentials) as `%config(noreplace)`; site values come from
`site/hpcperfstats.site.conf` (*Site conf (broker, credentials) and RPM Release*).

Replay loads a dumpfile into the ring and deletes it before anything from it is
sent. If the delete fails, the loaded samples are dropped from the ring again
and the file stays on disk unsent (one `Cannot delete dumpfile` error until a
delete succeeds), so a later replay cannot send them twice. A load that fails
partway leaves nothing from that file in the ring (`ring_buffer_load_file`).

### Privilege model

`hpcperfstatsd` runs as **root** (euid 0) for the life of the process: perf_event (core, uncore, power PMUs),
InfiniBand/OPA MAD, DCGM/GPU, and helper binaries need elevated access. There is no
in-process `setuid` drop today.

The shipped **`hpcperfstats.service`** adds a systemd sandbox (defense in depth, not
a substitute for root):

- `PrivateTmp=true`, `ProtectSystem=strict`, `ProtectHome=true`
- `ProtectKernelTunables=true`, `ProtectControlGroups=true`, `NoNewPrivileges=true`
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`
- `ReadWritePaths=/var/lib/hpcperfstats /run` (dumpfiles + pid/runtime)
- `BindPaths=-/tmp/xpum_p.sock -/tmp/xpum_up.sock`: the XPU Manager daemon's sockets in
  the host `/tmp`, which `PrivateTmp` would otherwise hide from `xpumcli` (PVC
  `intel_gpu` emitted no rows: `XPUM Service Status Error`). Bound when the monitor
  starts, so restart the monitor after restarting `xpumd`. `After=xpum.service` plus a
  privileged `ExecStartPre` wait (up to 60 s, only while `xpum.service` is active) cover boot:
  `xpumd` is `Type=simple` and creates the sockets about 10 s after it reports started. Both
  are no-ops on nodes without XPU Manager.
- **`PrivateDevices` is intentionally unset** so `/dev/infiniband`, GPU nodes, and
  perf remain available.

**Residual risk:** code execution or library injection inside the daemon is still uid
0 within that sandbox. Follow-up work may narrow **`CapabilityBoundingSet`** or add a
dedicated **`User=`** after measuring device and perf ACLs on fleet nodes.

**RPM debug path** (symbols + behavioral DEBUG for `/dev/shm`):

```bash
cd HPCPerfStats/monitor
./scripts/prepare_rpmbuild_dirs.sh --debug-build
# Run the printed debug rpmbuild && rpm_debug_shm_verify.sh line
# (verify notes appear only with --debug-build).
```

The debug `%install` stashes `monitor-build-capabilities.json` under
`rpmbuild/debug-verify/` (outside `BUILD/`). That path survives EL10
`rpmbuild` **rmbuild**, which deletes `BUILD/hpcperfstats-<ver>/` after a
successful package build. `./scripts/rpm_debug_shm_verify.sh` prefers the
stash; `BUILD/.../.build-static` is only a fallback (e.g. `rpmbuild --noclean`).
After install, `hpcperfstats.spec` `%post` starts `hpcperfstats.service`; shm
payloads appear under `/dev/shm/hpcperfstatsd-debug/` (override with
`HPCPERFSTATS_DEBUG_SHM_DIR`).

The default **release** `rpmbuild` (no `hpc_debug_build`) strips the binary and
passes `--disable-debug` — no `/dev/shm` mirror. Compiler `-g` alone does not
enable the mirror; the spec sets `HPC_BUNDLE_ENABLE_DEBUG=1` only for
`hpc_debug_build` RPMs.

### `/dev/shm` message correctness testing

On a data host with a DEBUG build (static bundle or debug RPM):

```bash
cd HPCPerfStats/monitor
./scripts/build_static_bundle.sh --enable-debug   # or debug RPM via prepare --debug-build
make -C .build-static capabilities
# start hpcperfstatsd; wait for schema + fast + full updates under /dev/shm/...
SLUG=$(python3 -c "import json; print(json.load(open('.build-static/monitor-build-capabilities.json'))['capability_slug'])")
python3 scripts/build_message_expectations.py \
  --capabilities .build-static/monitor-build-capabilities.json \
  --shm-dir /dev/shm/hpcperfstatsd-debug \
  --enable-slow-tier 1 \
  --out ".build-static/expectations_${SLUG}.json"
python3 scripts/validate_shm_messages.py \
  --capabilities .build-static/monitor-build-capabilities.json \
  --manifest ".build-static/expectations_${SLUG}.json" \
  --shm-dir /dev/shm/hpcperfstatsd-debug \
  --live-spot-check \
  --report "../../test_runs/monitor/validate_${SLUG}_$(date +%F).txt"
```

Validation layers: structural (schema keys, row width, tier markers, uint values,
manifest device IDs, listend host contract); **strict** plausibility and live
`/proc`/`/sys` spot checks by default on debug verify (`STRICT_*=0` to soften);
and **cross-sample** cadence + `E`-counter monotonicity by default
(`CROSS_SAMPLE_CHECK=0` to disable). Live cross-sample injects brief CPU + `/tmp`
IO (and Lustre touch when a mount is present) so flat counters mean stuck/unused
rather than a quiet node; disable with `--no-cross-sample-stimulus`. Sparse
error/idle flats at 0 are classified (silenced or aggregated); must-move canaries
emit one WARN per stuck class. Debug RPM installs `sample_freq=30` and
`sample_freq_slow=60` in `/etc/hpcperfstats/hpcperfstats.conf`; cross-sample wait
bounds are derived from that file at validate time.

Optional emit drift check (opt-in): copy shm files to
`tests/expected/shm_{schema,fast,full}_<slug>[__<profile>].txt` and set
`GOLDEN_DIR=auto` / `GOLDEN_CHECK=1` or `GOLDEN_DIR=/path` (and `--profile` when
using profile-scoped goldens).
Stampede3 fleet DEBUG builds emit slug tokens `ibdyn` / `opadyn` / `intelgpu` when
those macros are compiled in, plus JSON `fleet: "stampede3"` when the fleet
signature matches. Prefer `./scripts/validate_stampede3_profile.sh --profile …`
so expectations/reports include the `__<profile>` suffix and the type-presence
contract (e.g. h100 requires `nvidia_gpu`+`host_ib`+`host_opa`). Sysfs `host_opa`
is always built; slug tokens `opa`/`opadyn` reflect MAD compile flags.

For **debug RPM**, use `./scripts/prepare_rpmbuild_dirs.sh --debug-build` — it prints a
single chained command:

```bash
rpmbuild -ba ... hpc_debug_build 1 ... && ./scripts/rpm_debug_shm_verify.sh
```

Run from `HPCPerfStats/monitor/`. No exports needed. Re-validate only:
`SKIP_INSTALL=1 ./scripts/rpm_debug_shm_verify.sh`

Defaults include cross-sample + strict checks (~`FULL` post-install
`stress-ng --cpu 0` soak, then ~`sample_freq_slow` for a second full snapshot). Soften with
`CROSS_SAMPLE_CHECK=0 STRICT_PLAUSIBILITY=0 STRICT_LIVE_SPOT_CHECK=0`.
Opt-in golden: `GOLDEN_CHECK=1` or `GOLDEN_DIR=auto`.

**Capability slug** — `monitor-build-capabilities.json` includes
`capability_slug` (compile flags + `slowtier0`/`slowtier1`; Stampede3 fleet may
add `ibdyn`/`opadyn`/`intelgpu` and `fleet: "stampede3"`). The same slug is
embedded at compile time (`src/monitor_capability_slug.h` via `BUILT_SOURCES` /
`emit_build_capabilities.py`) and emitted on the shared `$` property banner as
**`$build {capability_slug}`** (after `$uptime`) for RabbitMQ schema rotation and
DEBUG shm `schema`. Consumers may omit parsing when `$build` is absent (old
daemons). Golden fixtures and expectations must use the same slug in filenames
(`tests/expected/shm_*_<slug>.txt`, `expectations_<slug>.json`; optional
`__<profile>` suffix for multi-queue fleet validates). CI runs
`tests/test_shm_message_correctness.sh` (synthetic fixture always; live slug
goldens when present; **exit 77 skip** otherwise). Local run logs belong under
**`<workspace-root>/test_runs/`** (see **test-runs-output-directory**).

## Power telemetry (hwmon, NVML/DCGM, RAPL, and interpretation)

### Round 2 migration: per-socket power and retired power keys

| Before | From round 2 | Notes |
|--------|--------------|-------|
| `host_cpu_hw` `dcgm_cpu_power_util_w` (DCGM 1130, same value on every CPU of a socket) | `host_power_socket` `cpu_power_w` (one row per socket, `dev` = socket) | Horizon GB200 published **0** before this release (DCGM reads no Grace power there); the hwmon meter reads ~42–46 W per socket |
| `host_cpu_hw` `dcgm_cpu_power_limit_w` (DCGM 1131, always 0 on Grace) | retired | no `power1_cap` exists on Grace |
| `nvidia_gpu` `module_power_usage` (DCGM 1133, per GPU) | `host_power_socket` `module_power_w` | GH200 only (~130 W); 0 on GB200 and Grace/Grace |
| `nvidia_gpu` `sysio_power_usage` (DCGM 1132, per GPU) | `host_power_socket` `sysio_power_w` | SoC rail per socket (~0.6 W GH200, ~6 W GB200) |
| — | `host_power_socket` `socket_power_w` | whole socket (`Grace Power Socket N`): GB200 88–91 W, Grace/Grace 67–76 W |

Consumers keep the per-socket rows (`dev` 0/1) and sum by `dev`; on shared nodes the type rides
only the node-wide `-` payload (never `j:` stamped). The analysis node power estimate becomes
Σ `module_power_w` when > 0 (GH200), else Σ `socket_power_w` + GPU power (GB200; Σ
`socket_power_w` alone on GPU-less Grace/Grace and Vera), which raises GB200 and Grace/Grace
estimates by the SoC + memory share of the socket. History with only the old keys keeps the old
formula. See `HPCPerfStats/docs/monitor_variable_rename_map.yaml`.


- **`cpu_counter_metrics` (`arm` backend on Grace)**: util accumulators from `/proc/stat` jiffies over the daemon's collect interval; the clock comes from cpufreq **`scaling_cur_freq`** (never `cpuinfo_cur_freq`, which reports bogus CPPC values on Grace), falling back to `base_frequency` / `cpuinfo_max_freq` / `scaling_max_freq`. **Fail-soft cycle keys** (accumulated from util + `clock_khz`): **`mperf`** = reference cycles (`ref = clock_khz × Δt_us / 1000`), **`aperf`** = active cycles (`act = ref × (util_total / 100)`), **`cpu_clock_est_cycles`** = same as **`aperf`**. When the direct-perf CYC event (`arm_core_perf`) is open and its scaled lifetime is > 0, all three carry measured cycles instead. `instr_retired`, `arm_fp_{dp,sp,hp}_spec`, `arm_est_flops` (FIXED + SCALE) and `arm_int8_ops` / `arm_int16_ops` come only from measured events; nothing synthesizes instructions or FLOPs from util. The backend uses no DCGM: embedded DCGM ran five 100–200 Hz timer loops (~615 wakeups/s) whatever the watch cadence, ≈ 8 core-s/h under the `performance` governor and ≈ 109 under `ondemand`.
- **`host_power_socket`**: one row per socket from the ACPI power meters (`acpi_power_meter` hwmon, `power1_average` µW → W): `cpu_power_w`, `socket_power_w`, `sysio_power_w`, `module_power_w`; a missing meter emits `NOT_PRESENT` (`-2`), a failed read `READ_FAILED` (`-3`), and unknown `<Name> Power Socket <N>` labels are logged once. `socket_power_w` already contains `cpu_power_w` and `sysio_power_w`; `module_power_w` (GH200) contains the GPU — do not add them together.
- **`nvidia_gpu`**: **`power_usage`** is the per-GPU draw. On GPM-capable GPUs (Hopper and newer) every key comes from **NVML + GPM** (`libnvidia-ml` loaded at runtime, override **`HPCPERFSTATS_NVML_LIB`**, allowlisted like the DCGM override): GPM percent x 0.01 = the DCGM ratio, GPM MiB/s x 1048576 = DCGM PROF bytes/s (checked against DCGM under load on GB200). NVML+GPM runs no background threads (embedded DCGM ran ~400 wakeups/s for the same fields). GPUs without GPM, or a node without a usable `libnvidia-ml`, use the DCGM path; an NVML failure mid-run switches to DCGM for the rest of the process (logged once; DEBUG builds force one with the `nvml_fetch` fault point). `dev` = NVML index, which equals the DCGM GPU id order on GB200 and GH200. The GPM window is the collect interval; a collect less than 150 ms after the last GPM sample (`NVIDIA_GPU_NVML_GPM_MIN_GAP_US`) takes no new sample and republishes the window that just closed, so the GPM keys are `NOT_UPDATED` only before the first window has closed.
- **`nvidia_gpu` pipe splits**: **`tensor_imma_active`** / **`tensor_hmma_active`** / **`tensor_dfma_active`** come from DCGM PROF 1013 / 1014 / 1015 or GPM IMMA / HMMA / DFMA, **`integer_active`** from DCGM 1016 or GPM metric 4, and **`tensor_dmma_active`** (FP64 tensor MMA) from GPM metric 8 only: DCGM has no DMMA field, so it is `NOT_PRESENT` on the DCGM path. All are duty cycles, 0-100. IMMA is a **low-precision tensor / FP8-adjacent signal only**, not a dedicated FP8 FLOP counter. **`tensor_dfma_active`** is the FP64 FMA pipe, not FP64 tensor: it equals `fp64_active` on H100, GH200, GB200 and RTX (measured). On the DCGM path a delivered-fields bitmask marks every field the active watch profile did not deliver `NOT_PRESENT` (`nvidia_gpu_dcgm_row_mark_unseen`), never 0.
- **`nvidia_gpu` DCGM extras**: per-direction link counters (**`gpu_pcie_*_bytes`**, **`gpu_nvlink_*_bytes`**, schema suffix **`E`** — monotonic hardware counters), **`gpu_mem_free_mb`**, **`gpu_sm_clock`**, **`gpu_pcie_replay_counter`**, and **`gpu_dram_active`** when the active watch profile includes those fields. **`gpu_io_link_total_bytes`** is a separate delta-accumulated event counter built from those PROF byte fields.
- **DCGM watch fallback ladder** for `nvidia_gpu` (DCGM path only, `nvidia_gpu_dcgm_profiles.c`; `NVIDIA_GPU_NFIELDS` 25): `full-prof` (all 25 fields, incl. 1013-1016) -> `split-prof-no-dfma` (drops 1015 DFMA and 1016 integer pipe) -> `core-prof` (drops 1013-1016) -> `basic-nonprof` (power/temp/util/fb/clock/replay/throttle, no PROF field). The last profile that worked is tried first after a re-watch. Board-power fields 1132/1133 are in no profile. DCGM 3.1.x on the LS6 A100 rejects `full-prof` because it lacks 1015 / 1016 (#116) and runs `split-prof-no-dfma`.
- **`gpu_flops_rate` / `gpu_flops`** (`nvidia_gpu_estimate.c`): sum over the fp64 / fp32 / fp16 / HMMA / DMMA pipes of duty cycle x that pipe's FLOPs per SM per clock for the GPU's compute capability, x SM count x SM clock. Per-SM peaks come from the CUDA C++ Programming Guide arithmetic-throughput table (scalar pipes) and the A100 / H100 whitepapers (HMMA at the dense FP16 tensor peak, DMMA at the FP64 tensor peak) for CC 8.0 and 9.0. CC 12.0 (RTX PRO 6000 Blackwell) HMMA is 1024, measured with `measurement/bin/gpu_tensor_calibrate.sh` (cuBLAS FP16 GEMM against the monitor's own `tensor_hmma_active`; no datasheet gives a per-SM value), and its DMMA is 0 (its DGEMM does not use tensor cores). CC 10.0 (GB200) HMMA is 8192 and DMMA 128, measured the same way on Horizon (cuBLAS FP16 GEMM and DGEMM against `tensor_hmma_active` / `tensor_dmma_active`). **Step at the upgrade:** RTX PRO 6000 and GB200 `gpu_flops` / `gpu_flops_rate` step up for tensor-heavy jobs from the monitor build with these peaks (3.1, 2026-11-23 on; e.g. RTX PRO 6000 ~4.8e11 -> ~3.9e14 FLOP/s per GPU under a 94% HMMA load; GB200 ~1.5e15 FLOP/s under a 95% HMMA load and ~3.8e13 under a 95% DMMA load, where those pipes added nothing before). No backfill; older jobs keep the old values. DFMA (same pipe as fp64), IMMA, `integer_active` and the `tensor_active` remainder not attributed to HMMA / DMMA are excluded. An unknown CC or SM count makes both keys `NOT_PRESENT`; `gpu_flops` integrates the rate over the collect interval.
- **Hardware sniff (`lspci`) gaps**: optional **`nvidia_gpu`** stays enabled when **`lspci`** is unavailable (prior behavior). When **`lspci`** is present but omits vendor prose, **`/proc/driver/nvidia/version`**, **`/dev/nvidia0`**, or **`lspci -nn`** lines matching **`[10de:`** plus usual GPU PCI classes still defeat disabling **`nvidia_gpu`** before DCGM runs. PCI class/vendor heuristics are shared between runtime (`src/gpu_pci_detect.c` / `hwdetect.c`) and build/configure probes (`scripts/gpu_lspci_probe.sh`); **`tests/test_gpu_lspci_detect_parity.sh`** guards parity on fixture lines.
- Runtime override: set **`HPCPERFSTATS_FORCE_NVIDIA_GPU=1`** to keep `nvidia_gpu` enabled even if runtime hardware sniffing misses NVIDIA.
- **DCGM loaded at runtime on every arch**: the monitor is built with vendored DCGM headers and **loads `libdcgm` at runtime** (`dlopen`) only when `nvidia_gpu` needs the DCGM path — CPU-only and Grace-only nodes do not need DCGM installed, and the binary links neither `libdcgm` nor `libnvidia-ml` (`readelf -d` shows neither in `NEEDED`). GPU nodes on the DCGM path need a compatible **`libdcgm.so`** (override with **`HPCPERFSTATS_DCGM_LIB`**). No build links LIKWID (3.1 Phases 2+3; x86 and Grace counters are direct perf).
- **DCGM session**: `nvidia_gpu` is the only DCGM user (`monitor_dcgm_session_acquire` / `release` stay refcounted for teardown safety).
- **`libdcgm` + `lshw` (mlx5 MCIA dmesg)**: `libdcgm` may run **`lshw -json`** for serial discovery via absolute **`/usr/bin/lshw`** then **`/usr/sbin/lshw`** (minimal child env — **`PATH` cannot intercept**). On nodes with empty-module Ethernet mlx ports (Horizon), that spam `mlx5_query_module_id` / `query_mcia_reg failed: status: 0x3`. The RPM installs a no-op stub at **`/usr/libexec/hpcperfstats/stubs/lshw`**; **`hpcperfstats.service`** bind-mounts it over those absolute paths in the unit namespace (`BindReadOnlyPaths=`). The unit still prepends the stub dir on **`PATH`** for bare `lshw` only. For manual/foreground runs use **`systemd-run`** with the same binds, e.g. `systemd-run --wait --collect -p BindReadOnlyPaths=-/usr/libexec/hpcperfstats/stubs/lshw:/usr/bin/lshw -p BindReadOnlyPaths=-/usr/libexec/hpcperfstats/stubs/lshw:/usr/sbin/lshw /usr/sbin/hpcperfstatsd ...` (PATH alone still hits real `lshw`). If binds fail to start the unit: drop-in `InaccessiblePaths=-/usr/bin/lshw -/usr/sbin/lshw`.
- **`roofline_hw_peak` in daemon payloads**: emitted **only** when **`stats_wr_hdr()`** runs (`write_hdr=1`): same buffered payload as the **`$`** property banner / **`!`** schema lines from **`stats_wr_hdr`**, followed by the first **`stats_buffer_collect`** timestamp block (`roofline_hw_peak_collect` is gated by **`stats_collect_on_changeover`**). **SIGHUP** reload triggers an immediate header/sample collect so schema changes are visible without waiting for the periodic rotate timer.
- **`intel_x86_rapl` / `amd_x86_rapl`** (`x86_rapl.c`): one row per socket (`dev` = `physical_package_id`). Each domain reads the perf `power` PMU event (`energy-pkg` / `energy-cores` / `energy-gpu` / `energy-ram`, scaled by its `.scale`) on that socket's PMU cpumask CPU, else the socket's powercap zone `energy_uj` (both agree within 0.4% on every measured class); no MSR access. Intel keys `pkg_energy`, `pp0_energy`, `pp1_energy`, `dram_energy`; AMD `core_energy`, `pkg_energy` (`E,W=32,U=mJ`). Each value is the monitor's running total in mJ since begin, modulo 2^32, so Δ / Δt is watts. A domain the node lacks emits its sentinel.
- **Not in this monitor**: chassis / PSU input (BMC), rack PDU, NIC/DPU, and NVMe device power would need separate collectors or future types (SNMP/Modbus, vendor tools, NVMe-MI, etc.).

## hpcperfstatsd: syscalls and blocking I/O

- **RabbitMQ (blocking)**: Publishing still uses **synchronous** rabbitmq-c calls from **libev timer callbacks**, but connect uses **`amqp_socket_open_noblock`** with a bounded wait, **login/RPC timeouts** (`amqp_set_handshake_timeout`, `amqp_set_rpc_timeout`; enabled by default via **`MONITOR_AMQP_TIMEOUT_CPPFLAGS`** in `src/Makefile.am` and `tests/Makefile.am` for rabbitmq-c 0.9+), **`SO_RCVTIMEO` / `SO_SNDTIMEO`** on the broker socket, and a negotiated **non-zero AMQP heartbeat** so wedged sessions fail faster than kernel-default TCP stalls. Set **`MONITOR_AMQP_TIMEOUT_CPPFLAGS=`** empty when building against headers that lack those APIs. If the broker or path still blocks inside the library, the **whole event loop** waits until the call returns or times out. Mitigations for heavy deployments: offload AMQP to a **worker thread** with a bounded queue, or adopt a **non-blocking** client integrated with `ev_io` (larger change). Tuning broker, network, and payload size helps without code changes.
- **RabbitMQ reconnect pacing**: On connect/declare/publish failure, TCP reconnect uses **exponential backoff** (**2→4→8…** seconds) capped at **`min(send_freq, 60s)`** with a **2s** floor, plus **hostname-hash jitter** (0–25%) so fleet nodes do not align. Fail-streak resets only after a **stable publish window** (≥30s connected and ≥1 successful publish), not on the first publish after reconnect. After the first successful active queue declare+bind, later reconnects use **passive** declare (active only on `NOT_FOUND`). While recovering or when the ring is deep, per-tick resend batches/runtime are reduced so post-outage drain does not slam quorum. **Sample/payload cadence** remains driven by `send_freq`; only reconnect spacing and recovery drain are throttled.
- **Collect path (reduced syscalls)**:
  - `pscanf` uses a **stack read** for small files (e.g. JOBID, sysfs flags) and falls back to heap slurp only when the file does not fit.
  - **`cpu`** keeps one `FILE *` on `/proc/stat` and **`rewind`**s each sample; the stream is closed when collect caches are invalidated.
  - **`net`** caches **up** interface names and **re-scans** `/sys/class/net` every 32 samples (and on SIGHUP / jobid–rotate reset / shutdown) to avoid `opendir`/`readdir` and per-iface `flags` reads every tick.
- **Invalidation**: `stats_buffer_runtime_caches_reset()` (SIGHUP), `monitor_reset_all_stats_types()` (log rotation / jobid-driven re-init), and SIGINT shutdown call `cpu_stats_invalidate_file_caches()` and `net_stats_invalidate_iface_cache()` so cached fds and iface lists are not stale across reconfigure or exit.

## Overhead measurement

The paper-facing overhead numbers come from the **unmodified release RPM** under its real
systemd unit, measured by `measurement/bin/run_overhead_profile.sh` on an idle, reserved node
per CPU architecture. One command builds and measures:

```bash
./scripts/prepare_rpmbuild_dirs.sh --measure-overhead            # fleet wrappers pass it through
./scripts/prepare_rpmbuild_dirs.sh --measure-overhead --measure-arg --max-hours --measure-arg 8
```

The flag checks `sudo -n` and the build tools before building, runs the release
`rpmbuild -ba`, then runs the harness as root. It cannot be combined with `--debug-build`.
The harness runs bracketed OFF / NONE / ALL / SOLO(driver group) legs, samples the daemon and
node at 1 Hz, runs the FWQ noise probe, and writes `profile.json`, `report.md` and
`profile_row.csv` (every slot measured or `NOT_MEASURED`). Partial-payload legs publish to
`<queue>_measure`, which listend never consumes. The release `%install` stashes
`monitor-build-capabilities.json` under `rpmbuild/build-info/` for provenance. Full procedure:
[measurement/RUNBOOK.md](measurement/RUNBOOK.md) section 0.

## Canary GEMM load (`scripts/canary_gemm_load.sh`)

Steady, known-precision CPU load for node canaries: the 3.1 Grace rate check against
`perf stat`, the power / clock A/B, and the DGEMM / SGEMM / HGEMM precision slices. It builds a
small self-contained OpenMP GEMM (no BLAS or module needed) in double, single and half precision
under `--prefix` (default `$HOME/hpcperfstats-gemm-load`) and runs it in the background with a
pidfile. Half precision needs a compiler with `_Float16` (aarch64 GCC 11 has it; x86 GCC 11
does not, so `hp` is skipped there).

```bash
./scripts/canary_gemm_load.sh install                       # module load gcc first if cc lacks OpenMP
./scripts/canary_gemm_load.sh start --precision dp          # all online CPUs until stopped
./scripts/canary_gemm_load.sh status                        # pid, settings, last GFLOP/s line
./scripts/canary_gemm_load.sh stop
./scripts/canary_gemm_load.sh run --precision sp --seconds 600   # foreground, e.g. in an sbatch script
```

Load started from a shell outside Slurm is measured by the node-level checks but is not
attributed to any job; to see the CPU Multiprecision Mix pie for it, run it with `run` inside a
Slurm job. Regression: `tests/test_canary_gemm_load.sh` (`check-local`, skips without OpenMP).

## Grace counter check (`scripts/canary_counter_check.sh`)

One command for the 3.1 Grace counter canary, run as root from the monitor checkout on a node
with the debug RPM installed (it reads the `/dev/shm/hpcperfstatsd-debug` mirror). For each of
`dp sp hp idle` it starts the GEMM load, measures the monitor's `host_cpu_hw` rates from the
`fast` file and `perf stat -a` over the same window (default 120 s after 60 s warm-up, about
5 min per precision), then prints a summary:

- **O3** — monitor `instr_retired` / perf `instructions` within 5%.
- **slice** — under each load only that precision's `arm_fp_*_spec` counts.
- **accuracy** — GEMM GFLOP/s over the window ÷ monitor FP rate must not exceed the 128-bit SVE
  FMA ceiling (4 / 8 / 16 FLOPs per dp / sp / hp operation). The monitor and perf both scale by
  enabled/running time, so their agreement cannot show a count that reads low when another
  program (for example `nvidia-powerd`) holds most counters; this check can. The margin is 1%,
  or 5% when the core events ran under 50% of enabled time (the sharing line's `running`): a
  count scaled up from a fifth of the time scatters by a few percent run to run (c122-018:
  dp read 1-4% low at 20% running, while sp / hp passed).

```bash
./scripts/canary_counter_check.sh                        # dp sp hp idle, exit 0 only on RESULT: PASS
./scripts/canary_counter_check.sh --precisions dp --window 60 --warm 30
./scripts/canary_counter_check.sh --scf                  # SCF memory counters (see below)
```

`--scf` checks the `arm_aarch64_imc` SCF reader instead: it runs a DRAM-bound dp GEMM
(`--size 4096`, matrices larger than L3) and compares `dram_cas_reads` / `dram_cas_writes`
(64-byte transfers, summed over every `scf<N>` row) with `perf stat -a` on every
`nvidia_scf_pmu_<N>` (`cmem_rd_data` 32-byte beats, `cmem_wr_total_bytes`) over the same
window: perf starts right after one daemon sample, runs until the first sample after `--window`,
and is divided by its own wall time, so a load that varies cannot push both ratios off together.
PASS needs read and write within 5% and at least 1 GB/s of reads.

`--accuracy` (every architecture) is the counter-accuracy layer of `rpm_debug_shm_verify.sh`:
see [Counter accuracy](#counter-accuracy-counter_check-default-1).

It never reads the terminal, bounds every wait and external command with a timeout, and stops
the GEMM load on any exit including Ctrl-C (exit 130; the log `tee` ignores INT / TERM so the
"interrupted" line is not lost when the signal reaches the whole process group). Output is also
written to `$HOME/hpcperfstats-counter-check/<host>-<epoch>.log`. Regression:
`tests/test_canary_counter_check.sh` (fake shm writer / perf / load; `check-local`).

## x86 FLOP counter check (`scripts/perf_raw_flops_check.sh`)

Every x86 event row in `x86_core_perf.c` must be verified on that CPU class before it ships:
each event is either a name from the node's perf event list or a raw
`cpu/event=0xNN,umask=0xMM/` spec from vendor docs, never a guessed code. This script checks
them on the hardware (it verified the Intel `fp_arith_inst_retired.*` and AMD FLOP rows of 3.1). It uses perf only: perf opens each event through `perf_event_open`, the
kernel programs the counters, and nothing touches an MSR.

For each precision it rebuilds the GEMM load, runs a bounded GEMM under `perf stat`, and compares
the sum of weight × count with the GEMM's exact FLOP total (the `done total_flops=` line):

- **ratio** — FLOPs counted by that precision's events (plus `all` events) ÷ GEMM FLOPs, within
  `--tolerance` (default 10%) of 1.00. An undercount on AMD can mean a missing MERGE pairing.
- **leak** — FLOPs counted by the other precision's events ÷ GEMM FLOPs, below 2%.

Default events: on Intel the eight `fp_arith_inst_retired.*` widths (weights 1/2/4/8 dp,
1/4/8/16 sp; FMA counts twice); on AMD `fp_ret_sse_avx_ops.all` (total FLOPs only, as on Zen3
per D10). Events this perf cannot open are reported and skipped. A running share below 100%
means multiplexing (other counter users, the NMI watchdog), so the result is scaled.

```bash
./scripts/perf_raw_flops_check.sh --list                 # CPU, perf version, FP events + encodings
./scripts/perf_raw_flops_check.sh                        # dp then sp, 20 s each, exit 0 only on RESULT: PASS
./scripts/perf_raw_flops_check.sh --precisions dp --event 'dp-512:8:cpu/event=0xc7,umask=0x40/'
```

`--event CLASS-LABEL:WEIGHT:EVENT` (repeatable) replaces the defaults: CLASS is `dp`, `sp` or
`all`, WEIGHT is FLOPs per count. Run it as root on an otherwise idle node; `module load gcc`
first if `cc` lacks OpenMP. Logs go to `$HOME/hpcperfstats-gemm-load/perf_raw_flops/`.
Regression: `tests/test_perf_raw_flops_check.sh` (fake perf / load / cpuinfo; `check-local`).

## Per-node correctness run (`rpm_debug_shm_verify.sh`)

`./scripts/rpm_debug_shm_verify.sh` (run as root from `HPCPerfStats/monitor/`, optionally with
`RPM_BUILD=1`) is the one command of the cross-architecture correctness campaign (Intel skx /
icx / spr / spr-max / pvc-host / h100-host, AMD Milan / Genoa / Turin, Grace GB200 / GH200). In
order: build (optional), install and soak the DEBUG RPM (`preflight`), validate the `/dev/shm`
messages (every validate layer below), the **counter-accuracy layer**, the wired canary checks,
and last the live fault injection (it restarts the daemon). Nothing reads the terminal, every
wait and external command is bounded, and loads are stopped on every exit including Ctrl-C.

### Counter accuracy (`COUNTER_CHECK`, default 1)

`canary_counter_check.sh --accuracy` compares the installed DEBUG daemon's own numbers, read
from the `fast` mirror, with loads whose totals are exact (`scripts/canary_gemm_load.sh`). Each
load runs between two fresh `fast` samples, so the daemon delta covers all of it.
`scripts/lib/counter_accuracy.py` holds the payload parsing, the tolerance table and the
verdicts; `COUNTER_CHECK=0` skips the layer and a release RPM (no DEBUG mirror) prints SKIP.

| Check | Load | Daemon counter | Tolerance |
|-------|------|----------------|-----------|
| `mem_read` | `--kernel read` pinned (`taskset`) to the socket-0 CPUs, `total_bytes_read` | `dram_cas_reads` x 64 B (+ SPR / EMR `hbm_cas_reads` x 32 B) on every IMC / HBM row; AMD DF `dram_data_beats` (Zen2 / Zen3, read + write) or `dram_read_beats` (Zen4 / Zen5) x 64 B over every `chan<N>` row; Grace SCF `dram_cas_reads` x 64 B | 0.75-1.5 |
| `mem_write` | `--kernel write` (`total_bytes_written`) | `dram_cas_writes` (+ `hbm_cas_writes`); AMD Zen4 / Zen5 `dram_write_beats` x 64 B; NOT_RUN on Zen2 / Zen3 (one combined read + write event) | 0.75-1.5 |
| `mem_socket0` | the read load | Grace: `scf0` share of all SCF bytes; x86 NOT_RUN (each uncore row sums every socket) | >= 0.75 |
| `flops_dp` / `flops_sp` | dp / sp GEMM, `done total_flops` | Intel `fp_arith_inst_retired_*` lane-weighted (scalar 1, 128b 2/4, 256b 4/8, 512b 8/16 dp/sp; FMA counts twice); AMD `fp_ops_retired`, Zen5 `amd_fp_dp_ops` (sp = total - dp; Zen3 / Zen4 total only); Grace `arm_fp_*_spec`: GEMM FLOPs per op must not exceed 4 / 8 (128-bit SVE FMA) | Intel / AMD 0.9-1.1; Grace ceiling |
| `instructions` | the dp GEMM window | `instr_retired` (else `instr_retired_any`) vs `perf stat -a -e instructions` over the same window | 0.95-1.05 |
| `energy_pkg` / `energy_dram` | the dp GEMM window | `pkg_energy` / `dram_energy` (mJ, mod 2^32) vs powercap `energy_uj` of the `package-N` zones / `dram` subzones (mod `max_energy_range_uj` + 1); x86 only, AMD has no DRAM domain | 0.95-1.05 |

A missing source (no PMU or a sentinel on a counter key, no `perf`, no powercap, a load kernel
the installed `canary_gemm_load.sh` does not have, `taskset` missing) is `NOT_RUN` with the
reason, never `PASS`; a `fast` payload that stops advancing is `FAIL`. Knobs:
`COUNTER_CHECK_SECONDS` (load length, default 20), `COUNTER_CHECK_TIMEOUT` (default 3600 s); the
standalone script also takes `--seconds` and `--results FILE`. Regression:
`tests/test_rpm_debug_counter_check.sh`, `tests/test_counter_accuracy_lib.py`. A real node is
the only proof that the counters are right: the tests stub the PMU, the loads and powercap.

### Correctness check inventory

Every correctness check developed under `HPCPerfStats/monitor`, and whether
`rpm_debug_shm_verify.sh` runs it (a check is wired when it runs unattended as root on a compute
node in bounded time). Summary names are the `checks[].name` values of the campaign JSON.

| Check | Proves | Wired | Switch (summary name) | Reason when not wired |
|-------|--------|-------|-----------------------|-----------------------|
| install / service / first full payload | DEBUG RPM installed, `hpcperfstats.service` active, full (and, slow tier on, fast) payloads with rows, slug resolved; the same bounded wait follows every daemon restart in the run | yes | always (`preflight`) | |
| `validate_shm_messages.py` structure | schema, fast and full rows parse against the capability manifest; listend host contract | yes | always (`message_structure`) | |
| value sentinel layers | `$sentinels` banner, allowed codes per key, `NOT_PRESENT` checked against the live source, no key `NOT_UPDATED` twice; BeeGFS RDMA / SDP mounts must read `NOT_PRESENT` | yes | `STRICT_SENTINELS`, `SENTINEL_PROBE_CHECK` (`sentinels`) | |
| plausibility | values inside plausible ranges | yes | `STRICT_PLAUSIBILITY` (`plausibility`) | |
| cross-sample | cadence, `E`-counter monotonicity (width-aware wrap), bridge validity; the stimulus touches a Lustre mount so the llite source (procfs, else debugfs on Lustre 2.15) must move | yes | `CROSS_SAMPLE_CHECK`, `STRICT_CROSS_SAMPLE` (`cross_sample`) | |
| live spot | emitted values match live `/proc`, `/sys`, `nvidia-smi` reads; `host_power_socket` is compared on the next fresh `fast` sample (`full` without the slow tier) with the meters read as soon as it lands, and skipped with a NOTE when none lands within `--spot-check-max-age` | yes | `STRICT_LIVE_SPOT_CHECK` (`live_spot`) | |
| independent read (`INDEPENDENT_READ_CHECK`) | a live read of the collector's own source lies between two emitted values (`/proc/stat`, IB / OPA counters, block stat, node meminfo, hwmon power); every `host_ib` counter is sysfs `counters/` and bracketed on every build (`sw_*`, the only MAD keys, have no host file) | yes | `INDEPENDENT_READ_CHECK` (`independent_read`) | |
| consumer parse | the consumer parser reads the capture: sentinels never enter a rate, no negative delta, Codon parity when built | yes | `CONSUMER_PARSE_CHECK` (`consumer_parse`) | |
| golden diff | byte diff against a slug golden | opt-in | `GOLDEN_CHECK=1` / `GOLDEN_DIR` (`golden`) | |
| counter accuracy (`canary_counter_check.sh --accuracy`) | daemon memory bytes, FLOPs, instructions, energy vs known loads (table above) | yes | `COUNTER_CHECK` (`mem_read`, `mem_write`, `mem_socket0`, `flops_dp`, `flops_sp`, `instructions`, `energy_pkg`, `energy_dram`) | |
| `perf_raw_flops_check.sh` | x86 perf FLOP events (the encodings the daemon uses) vs exact GEMM FLOPs, no daemon involved | yes (Intel / AMD) | `PERF_RAW_FLOPS_CHECK` (`perf_raw_flops`) | NOT_RUN on Grace |
| `canary_counter_check.sh --scf` | Grace SCF `dram_cas_*` vs `perf` on every `nvidia_scf_pmu_<N>` within 5% | yes (Grace) | `SCF_PERF_CHECK` (`scf_perf`) | NOT_RUN without SCF PMUs |
| `canary_counter_check.sh --sharing-only` | PMU sharing keys: running share <= 100% on a quiet node | yes | `PMU_SHARING_CHECK` (`pmu_sharing`) | |
| `canary_counter_check.sh` (precision mode) | Grace precision slices, monitor vs `perf` `instr_retired` (5%), flops-per-op ceiling per precision | yes (Grace) | `FP_SLICES_CHECK` (`fp_slices`) | NOT_RUN on x86 (Grace keys only) |
| live fault injection (`FAULT_INJECT`, `fault_inject_verify.sh`) | forced failures map to the planned codes, values recover, gaps bridge, every registered site fires | yes | `FAULT_INJECT` (`fault_inject`) | release RPMs SKIP |
| `measurement/bin/verify_grace_scf_imc.sh` | Grace SCF units with `perf` and a transient DEBUG daemon: `scf0` delta vs exact read bytes, DCGM cadence, NVSwitch module denied | no | | stops `hpcperfstats.service` for the run and starts a transient daemon from a build tree (needs a service-free node and a build) |
| `validate_stampede3_profile.sh` | the same validate layers plus the Stampede3 queue type-presence contract | no | | needs the operator's queue profile name (`--profile`), and repeats layers this run already does |
| BeeGFS TCP byte countability | `vfs_*_bytes` equal the client's TCP bytes to its storage servers; RDMA gives `NOT_PRESENT` | partly (sentinel layer) | | a byte-exact live check needs a BeeGFS mount and controlled I/O on a shared filesystem; `tests/test_beegfs_tcp.c` / `test_beegfs_io.c` in `make check` |
| Lustre llite procfs / debugfs source | opcode stats come from procfs, else debugfs; neither gives `NOT_PRESENT` | partly (cross-sample, sentinels) | | byte-exact I/O needs a Lustre mount and a known load on a shared filesystem; C tests in `make check` |
| `test_rabbitmq_integration.sh` | payloads survive a real broker and the consumer contract | no | | needs a RabbitMQ broker (`RUN_RMQ_INTEGRATION=1`) |
| `check_sentinel_error_paths.py`, `check_fault_ci_files.py`, `check_emitted_variable_names.py`, `check_unsafe_c_patterns.sh`, C unit and fault-matrix tests | source lint and unit contracts | no | | build-tree checks (`make check`), not node checks |
| `run_valgrind_check.sh`, `run_cpp_linter.sh`, `cross_compile_test.sh`, `run_warning_gate.sh`, `run_shared_node_static_check.sh` | memory safety, format, foreign builds, warnings | no | | build-host gates (compile in a build tree), not node checks |
| measurement kit (`run_overhead_profile.sh`, `capture_jitter.sh`, `compare_schema_keys.py`, cadence capture) | daemon overhead, jitter, schema keys kept between two runs | no | | performance runs that stop the daemon or compare two profile runs (service-free node, operator schedule) |
| `catalog_hardware_error_packet.py`, peak SQL checks | Job Detail hardware-error coverage, roofline peaks in `host_data` | no | | need the database |
| `track_cpu_peg.sh` | CPU peg triage bundle | no | | a 20-minute diagnostic capture, not a pass / fail check |

### Campaign summary JSON

Every run, an early failure included, writes one file per node next to the text reports:
`test_runs/monitor/correctness_<host>_<slug>_<UTC>.json` (`scripts/lib/correctness_summary.py`,
schema locked by `tests/test_correctness_summary.py`). The run exits non-zero when any check
FAILed. Schema version 1:

| Field | Content |
|-------|---------|
| `schema_version` | `1`; bumped only on a breaking change |
| `generated_utc` | ISO-8601 UTC, seconds |
| `host` | node hostname |
| `arch` | `class` (`intel`, `amd`, `grace`, `other`), `vendor`, `family`, `model` (x86 cpuinfo; Grace implementer / part), `pmu_name` (`cpu/caps/pmu_name`, else `none`) |
| `capability_slug` | slug of the installed build (`unknown` before capabilities resolve) |
| `rpm_build` | `rpm -q hpcperfstatsd` |
| `debug_build` | `configure.debug` of the capabilities JSON |
| `overall` | `FAIL` when any check FAILs, else `PASS` |
| `counts` | number of checks per status |
| `checks` | every check name, always all of them and in this order: `preflight`, `message_structure`, `sentinels`, `plausibility`, `cross_sample`, `live_spot`, `independent_read`, `consumer_parse`, `golden`, `mem_read`, `mem_write`, `mem_socket0`, `flops_dp`, `flops_sp`, `instructions`, `energy_pkg`, `energy_dram`, `perf_raw_flops`, `scf_perf`, `pmu_sharing`, `fp_slices`, `fault_inject`; each `{name, layer, status, reason, ratio}` with status `PASS` / `FAIL` / `NOT_RUN` / `SKIP` and `ratio` the measured daemon / reference ratio (Grace FLOP checks: FLOPs per op) or null |

`NOT_RUN` means the check could not measure (missing source, or not reached after an earlier
failure); `SKIP` means it was switched off or does not apply to the build.

### Campaign matrix (`scripts/correctness_matrix.py`)

On the dev host, `scripts/correctness_matrix.py test_runs/monitor/campaign` aggregates every
node's JSON (directories are searched recursively for `correctness_*.json`; add
`--class-map HOST=milan-cpu` for the operator-picked Milan node) into one matrix: rows are the
campaign classes, columns the checks above, cells `P 1.00` / `F 0.62` / `N` / `S`. Only files
with `schema_version` 1 and `debug_build` true count, and the newest file per host wins.
`scripts/lib/correctness_classes.tsv` maps hosts to classes (unmapped hosts print as `?<host>`);
the hosts of one class merge with the worst status winning (FAIL > NOT_RUN > SKIP > PASS).
`scripts/lib/correctness_expected.tsv` holds the expected status per class and check (`P`, `N`,
`S`, or `P/N` for either; override with `--expected FILE`), and every PASS ratio of a
counter-accuracy check is re-checked against the `counter_accuracy.py` tolerances. The run
exits 1 on any FAIL, unexpected `NOT_RUN` / `SKIP`, ratio outside the tolerance, arch mismatch
or class without a file, and 2 on unreadable or invalid input; `--json` prints the same result
for machines. Tests: `tests/test_correctness_matrix.py`.

### Campaign node verify (`scripts/campaign_verify_node.sh`)

`scripts/campaign_verify_node.sh ls6|gb200` (root, any cwd) is the per-node operator command,
so nobody pastes a multi-KB block (pastes over 4 KB lose lines on cluster nodes). It writes
`site/hpcperfstats.site.conf` from the packaged conf when missing (key names printed, never
values), removes the other clusters' fleet markers, resets the packaged conf and spec, and runs
`git pull --ff-only`. It then checks the checkout gates and runs the debug RPM build and
`rpm_debug_shm_verify.sh` (logs in `/tmp/hps-cc-<host>/`, reports in `../test_runs/monitor/`).
`ls6` adapts to the node: Genoa (`lscpu` `EPYC 9xxx`) adds the DF channel step and needs the
Genoa DF commit, and the GPU field-response step (four `dcgmproftester` loads) runs only when
`nvidia-smi -L` lists a GPU. `gb200` needs the CC 10.0 peak row, turns the four standalone
checks off, and runs one tensor load (1004). The debug RPM is erased only when every step
returned 0; otherwise a `== KEEP debug RPM: ...` line names the step rc values.
Tests: `tests/test_campaign_verify_node.py`.

## Profiling `hpcperfstatsd`

Use these steps on a **representative host** (same kernel, privilege level, and enabled collectors as production). Run as the same user as the daemon when attaching with `strace`/`perf`, unless you use `sudo` and account for `ptrace` scope (`/proc/sys/kernel/yama/ptrace_scope` on many Linux distros).

### Syscall volume (`strace`)

1. **Summarize syscalls over a window** (good for before/after comparisons on collect-path changes):

   ```bash
   # Attach to a running daemon (replace PID)
   strace -c -p PID -o /tmp/hps_strace.txt
   # Let it run across several sample intervals, then Ctrl+C.
   ```

   Inspect `/tmp/hps_strace.txt` for totals on `open`, `openat`, `read`, `close`, `stat`, `fstat`, `getdents`, `connect`, `write`, `recvfrom`, and similar. Compare two captures taken with the same sample frequency and workload.

2. **Trace only relevant syscalls** (less noise, larger logs per syscall class):

   ```bash
   strace -p PID -e trace=openat,read,close,getdents64 -o /tmp/hps_trace.log
   ```

   Adjust the list for your kernel (`getdents` vs `getdents64`, `open` vs `openat`).

3. **Launch under strace** (when you can start the daemon yourself):

   ```bash
   strace -f -o /tmp/hps_spawn.log /path/to/hpcperfstatsd -d ...
   ```

   `-f` follows children if the process forks (e.g. after `-d` daemonize: attach to the **child** PID or use `-f` and filter the log).

### CPU and hot paths (`perf`)

1. **Lightweight counters** for a few sample intervals while attached:

   ```bash
   perf stat -p PID -- sleep 30
   ```

   Use this to spot high CPU time, cycles, or cache behavior; it does not give C line numbers by itself.

2. **Profile with stack traces** (requires debug symbols; build with `-g`, which the static bundle typically already uses):

   ```bash
   perf record -F 99 -g -p PID -- sleep 60
   perf report
   ```

   Look for time in `st_collect` implementations, `path_collect_*`, `pscanf`, `amqp_*`, and libev. On stripped binaries, install debuginfo or rebuild without stripping for meaningful symbol names.

3. **Permissions**: If `perf record` fails, check `perf_event_paranoid` (e.g. `sysctl kernel.perf_event_paranoid`) or run with appropriate capability; site policy varies.

### Interpreting results

- **High `open`/`read`/`close` counts** per sample interval usually point at **collectors** (`collect.c`, per-type `*_collect` in `src/`, sysfs walks such as `net` or `block`).
- **Blocking** in the libev thread often shows as long gaps in syscall timing or stalls under load; correlate with **RabbitMQ** and network health, since publish/login paths are synchronous (see **hpcperfstatsd: syscalls and blocking I/O** above).
- After code changes, re-run **`make check`** in the static build tree (see **Building and verifying**) and repeat the same `strace -c` / `perf record` procedure so comparisons are apples-to-apples.

## Building and verifying

1. **Canonical static build** (pinned deps + `hpcperfstatsd`): from this directory,

   ```bash
   ./scripts/build_static_bundle.sh
   ```

   **Optional accelerators:** when **`ccache`** and/or **`mold`** are installed and a one-line probe works, `build_static_bundle.sh` and `run_valgrind_check.sh` compile `hpcperfstatsd` through ccache and link with mold (`-B<prefix>/libexec/mold`, which works on EL9 GCC 11 and Clang). Without them the build is unchanged. Release builds (`--release` / RPM) keep the system linker unless **`HPC_BUNDLE_MOLD=1`**; ccache stays on because its output is identical. Turn either off with **`HPC_BUNDLE_CCACHE=0`** / **`HPC_BUNDLE_MOLD=0`**. Logic: `scripts/lib/build_accel.sh`; test: `tests/test_build_accel.sh`. Third-party deps (`--deps-only`) do not use them.

2. **Tests** (from the **configured build tree root**, e.g. `.build-static`):

   ```bash
   make check
   ```

   Or:

   ```bash
   ./tests/run_tests.sh
   ./tests/run_tests.sh /path/to/build-tree
   ```

   Do not rely on **`make check` only inside `build-tree/src/`**; the Automake `tests/` subdir runs from the top of the build mirror.

3. **Cleanup** (optional after a successful verify pass): from the same build tree root, **`make distclean`** removes generated Makefiles and artifacts unless you need to keep the tree configured.

See **monitor-static-build-verification**, **monitor-dual-verify-cross-and-static**, and **monitor-valgrind-cpp-linter-gate** in `monitor/cursor-rules/` for project expectations. Foreign `cross_compile_test.sh` is required only for **portable / multi-arch** diffs (including DCGM **GPU**, shared `perf_util` and both-arch bundle scripts). Skip aarch64 when the change is **x86-only** direct perf core / uncore / RAPL / CPUID. Skip x86_64 when the change is `arm` **CPU**-backend-only.

4. **Plan-close Valgrind + cpp-linter** (additive; required when closing a plan that touched monitor C/tests/scripts — no git hooks):

   ```bash
   ./scripts/run_valgrind_check.sh
   ./scripts/run_cpp_linter.sh
   ```

   Install clang tools into the workspace `.venv` when needed: `pip install -r tests/requirements-cpp-linter.txt` (after loading the matching Python module on TACC). Logs land under workspace `test_runs/valgrind-check-*.log` and `test_runs/cpp-linter-*.log`.

5. **Compiler-warning gate** (git hooks in the repo `.pre-commit-config.yaml`; also runnable by hand):

   ```bash
   ./scripts/run_warning_gate.sh            # pre-commit: default x86 config, daemon, -O2 -Wall -Wextra -Werror
   ./scripts/run_warning_gate.sh --push     # pre-push: three configurations + unreachable functions
   ```

   `--push` compiles with `-Wall -Wextra -Werror`: the default x86 config (daemon + all make-check programs), x86 with `--enable-debug --enable-shared-node-multi-jid` (daemon + make-check programs), and the aarch64 Grace config (arm backend, shared node, `--enable-ib-mad-dlopen`; daemon only, built with clang `--target=aarch64-linux-gnu` + `ld.lld` against the `cross_compile_test.sh` sysroot and aarch64 prefix; nothing runs). It then links each configuration at `-O0 -ffunction-sections` with `--gc-sections --print-gc-sections` and fails on any non-static function that is **unreachable in every configuration that compiles it** and not in `scripts/unreachable_functions.allowlist`. A helper only ARM, DEBUG or shared-node code calls counts as reached. The allowlist holds functions only tests call. The gate prints entries some configuration reaches, or none defines, as stale; remove a name when you delete or start calling that function.

   Builds live in cached `.build-warn/<variant>/` trees (gitignored, never `.build-static`), rebuilt when `configure.ac`, a `Makefile.am`, the compiler, the flags or a prefix change; do not run `make` there by hand (objects built without the gate's flags look up to date to it). Vendored and prefix headers are `-isystem`. **The gate SKIPS (exit 0, bold yellow banner) without Linux, gcc, autoreconf or the static prefix `.build/prefix-static`, so commits from such hosts (macOS checkouts, fresh clones) are not warning-checked.** Without clang, `ld.lld`, the aarch64 sysroot or prefix, `--push` still runs the x86 variants and skips only the aarch64 variant and the unreachable-function check, behind the same banner. Set `HPC_WARN_GATE_REQUIRE=1` to fail instead of skipping. Override paths with `PREFIX`, `ARM_SYSROOT`, `ARM_PREFIX`. Test: `tests/test_run_warning_gate.sh`.

## Cross-compile smoke (qemu-user)

Use `scripts/cross_compile_test.sh` when one machine should compile+test a **CPU family that actually builds the changed code**. Do not run aarch64 smoke for x86-backend-only work, and do not run x86_64 smoke for `arm` **CPU**-backend-only work. DCGM **GPU** is multi-architecture — GPU DCGM diffs still need a foreign family. Policy: **monitor-dual-verify-cross-and-static**.

- **Native triplet** (`target` CPU family matches host): the script runs the canonical native path (`scripts/build_static_bundle.sh` then `make check`) without qemu wrapping for compilation.
- **Foreign triplet** (`target` CPU family differs from host): the script uses host `make`/`cmake`/`pkg-config` and qemu-wrapped target compiler/binutils from a target sysroot (`gcc`, `g++`, `ar`, `ranlib`, `strip` under `SYSROOT/usr/bin`).
- If `SYSROOT`/`SYSROOT_<TRIPLET>` is not set, the script auto-creates a **Rocky 9** sysroot under `.build/sysroots/` by downloading an official Rocky container-base rootfs tarball and extracting it in user space (`AUTO_CREATE_ROCKY9_SYSROOT=1`, default).
- When the base rootfs lacks compiler/binutils, the script programmatically layers a Rocky **Container-Toolbox** rootfs tarball over the same sysroot to provide `gcc/g++/ar/ranlib/strip` in user space.
- If the toolbox layer still lacks compiler binaries, the script performs a rootless RPM payload fallback: it downloads selected Rocky toolchain RPMs and unpacks them into the sysroot via `rpm2cpio|cpio` (no root, no package install transaction).
- Fully explicit mode: set `ROOTFS_TARBALL` or `ROOTFS_TARBALL_<TRIPLET_SLUG>` to use your own rootfs tarball.
- If qemu user emulators are missing, the script bootstraps a local qemu-user build under `.build/qemu-local` (`LOCAL_QEMU_ROOT`).
- Rootless mode is the only mode: no `sudo`, no `podman/docker`, no `conmon/runc`, and no `binfmt_misc` registration.
- The script keys behavior from the **configured triplet**, not `uname -m`, because user-mode emulation can still report host architecture.
- Foreign monitor builds pass **`--disable-all-static`** so tests link against the sysroot’s dynamic **`libc`/`libm`** (full **`--enable-all-static`** against glibc tends to fail under qemu-user smoke due to static **`libm`** IFUNC resolvers). Native triplets still use the canonical static-bundle flow.
- **`make check`** for foreign triplets runs test binaries under **qemu-user** via Automake **`LOG_COMPILER`**; **`QEMU_LD_PREFIX`** (already set by the script from **`SYSROOT`**) resolves target **`ld-linux`** / **`libc`** for emulation—same semantics as **`qemu-* -L SYSROOT`**.
- For deterministic foreign smoke, monitor configure also forces:
  **`--disable-amd-gpu --disable-infiniband --disable-opa --disable-lustre`** (`nvidia_gpu` stays on: DCGM and NVML are `dlopen`ed at runtime, so the foreign build compiles the GPU path with only vendored headers).

Foreign prerequisites:

1. Install host build tooling: `make`, `cmake`, `pkg-config`, plus downloader (`curl` or `wget`).
   QEMU source bootstrap may also install Python `tomli` and `ninja` in user space via `python3 -m pip --user`.
2. Provide a sysroot per target triplet, or allow script auto-generation via Rocky rootfs tarball download.
3. No target builds LIKWID (removed in 3.1 Phases 2+3) or DCGM at build time (`libdcgm` is loaded at runtime); the static bundle pins only libev 4.33 and rabbitmq-c 0.18.1 (plus libbpf for the eBPF metric profiler). Foreign cross smoke builds the production backend of the target (`--with-cpu-counter-backend=auto`: `x86` on x86_64, `arm` on aarch64).
4. Export one of:
   - `SYSROOT_<TRIPLET_SLUG>` (preferred), e.g. `SYSROOT_AARCH64_LINUX_GNU=/opt/sysroots/aarch64`
   - `SYSROOT` as a fallback for all foreign targets.
5. For automatic Rocky rootfs generation, ensure the host can download:
   `https://download.rockylinux.org/pub/rocky/<release>/images/<arch>/Rocky-<release>-Container-Base.latest.<arch>.tar.xz`
6. For automatic toolchain layering, ensure the host can also download:
   `https://download.rockylinux.org/pub/rocky/<release>/images/<arch>/Rocky-<release>-Container-Toolbox.latest.<arch>.tar.xz`
7. For RPM fallback layering, ensure host `rpm2cpio` and `cpio` are available.

Example:

```bash
cd HPCPerfStats/monitor
TARGETS="x86_64-linux-gnu aarch64-linux-gnu riscv64-linux-gnu" \
SYSROOT_AARCH64_LINUX_GNU="/opt/sysroots/aarch64" \
SYSROOT_RISCV64_LINUX_GNU="/opt/sysroots/riscv64" \
./scripts/cross_compile_test.sh --fail-fast
```

If deps are already cached per target prefix, add `--skip-deps`.

Automatic Rocky 9 rootfs download + sysroot extraction example:

```bash
cd HPCPerfStats/monitor
TARGETS="x86_64-linux-gnu" \
AUTO_CREATE_ROCKY9_SYSROOT=1 \
./scripts/cross_compile_test.sh --force-foreign --fail-fast
```

Explicit rootfs tarball example:

```bash
cd HPCPerfStats/monitor
TARGETS="x86_64-linux-gnu" \
ROOTFS_TARBALL_X86_64_LINUX_GNU="/path/to/rocky9-x86_64-rootfs.tar.xz" \
AUTO_CREATE_ROCKY9_SYSROOT=0 \
./scripts/cross_compile_test.sh --force-foreign --fail-fast
```

## Adding code and tests

- New or extracted C helpers should get **`make check`** coverage; register drivers in **`tests/Makefile.am`** and keep **`monitor_unit_cppflags`** there aligned with **`src/Makefile.am`** `hpcperfstatsd_CPPFLAGS` for preprocessor defines. Rule: **monitor-c-new-function-unittests**.

## Documentation maintenance

When you change **layout**, **build/test steps**, **major modules**, or **consumer-visible output**, update this file and, if the change is test-only, **`tests/README.md`**. Cursor rule: **monitor-readme-maintenance**.
