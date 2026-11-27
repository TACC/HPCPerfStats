# Build the monitor RPM

This page shows monitor developers and RPM builders how to configure `hpcperfstatsd` and build a release or debug RPM for one cluster.
Use it when you need an RPM for a fleet (Stampede3, Lonestar6, Horizon) or a one-off build for a node.

## Prerequisites

- A monitor checkout with the static bundle prerequisites (`cmake` for rabbitmq-c, network access for the pinned dependency downloads).
- `rpm-build` on the build host.
- Run every command on this page from `HPCPerfStats/monitor/`.

## Choose configure options

`hpcperfstatsd` is always the RabbitMQ daemon (`monitor.c`, `monitor_cli.c`, `monitor_daemon.c`, `stats_buffer.c`).
It uses AMQP and libev and reads hardware counters by direct `perf_event_open`, with no PMU library, so `configure` requires rabbitmq-c.
Automake conditionals select the optional collectors; `./configure --help` lists every option.

| Option                                       | Effect                                                                                               |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `--enable-all-static`                        | Link the pinned static libev and rabbitmq-c (the default).                                           |
| `--enable-shared-node-multi-jid`             | Shared-node build: `%jobs`, `%affinity` and `host_mem_job` (see [design](../explanation/design.md)). |
| `--enable-opa`, `--enable-opa-mad-dlopen`    | Omni-Path STL MAD, link-time or runtime `dlopen`.                                                    |
| `--enable-ib-mad-dlopen`                     | InfiniBand MAD by runtime `dlopen`.                                                                  |
| `--enable-infiniband`                        | Link-time `-libmad` when the devel stack is present (default outside fleets).                        |
| `--enable-intel-gpu={auto,yes,no}`           | Intel PVC collector (`auto` runs `scripts/gpu_lspci_probe.sh intel`).                                |
| `--enable-metric-profiler`                   | Compile-time collector timing instrumentation.                                                       |
| `--with-metric-profiler-backend={none,ebpf}` | Where the profiler attributes time.                                                                  |

`--enable-opa` needs the Cornelis or Intel IFS `liboib_utils` and its `oib_utils.h` on the build host.
The static bundle passes `--disable-infiniband` (sysfs-only IB) when its MAD probe fails, outside fleet builds.
`scripts/build_static_bundle.sh` builds only the pinned `libev` and `librabbitmq` (rabbitmq-c) static archives, plus `libbpf` for the eBPF profiler; the pins are its `STATIC_PIN_*` variables.
To configure by hand against `scripts/ensure_dotbuild_prefix_static.sh`'s prefix, point `CPPFLAGS` and `LDFLAGS` at it.
Collector behaviour behind each flag is in [source modules](../reference/source-modules.md).

### Metric profiler

`--enable-metric-profiler` times each type's `st_collect` and each metric's `stats_set` / `stats_inc` path.
`--with-metric-profiler-backend=none` keeps attribution internal (wall time, thread CPU time, inferred wait).
`--with-metric-profiler-backend=ebpf` also emits host-side attribution signals with each periodic report.
When `--with-metric-profiler-backend=ebpf` is passed through `scripts/build_static_bundle.sh`, the script also builds a pinned static `libbpf` into the same `PREFIX`.

The daemon profiles every collect cycle through `stats_runtime_collect_cycle` (`metric_profiler_cycle_begin` / `_end`).
It prints `metric-profiler:*` lines with call counts, total, average, minimum and maximum wall time, CPU time, and inferred wait (`wall-cpu`, clamped at zero).
The periodic report comes every 120 cycles (one hour at `sample_freq 30`), and `metric_profiler_report` prints the cumulative report once at shutdown (`#ifdef MONITOR_METRIC_PROFILER` in `monitor.c`).
Without `--enable-metric-profiler` none of it is compiled.
`measurement/bin/run_overhead_profile.sh --profile-collectors` uses it for one labelled, non-headline leg.

### Shared-node build

To check the shared-node flavour, run `./scripts/run_shared_node_static_check.sh`, or `./scripts/build_static_bundle.sh --enable-shared-node-multi-jid` followed by `make -C .build-static check`.

## Set the site conf

Node checkouts never edit the tracked `src/hpcperfstats.conf` or `hpcperfstats.spec`.
Broker and credential settings go in `site/hpcperfstats.site.conf`: untracked (gitignored), one per checkout, same `key value` syntax as the conf.

To create the site conf:

1. Copy the tracked template and restrict it:

   ```bash
   cp site/hpcperfstats.site.conf.example site/hpcperfstats.site.conf
   chmod 600 site/hpcperfstats.site.conf
   ```

2. Set `server`, `queue`, `port`, `user` and `password` in it.

`make dist` (`dist-hook`) copies the site file into the tarball when it exists.
The spec `%build` runs `scripts/site_conf.sh overlay`, which replaces the value of every key the default conf has, appends keys it lacks, and keeps the default's comments and order.
With no site file the RPM ships the repo default (`server localhost`).
The script prints key names only, never values.

> [!WARNING]
> The tarball and SRPM under `rpmbuild/` hold the broker credentials, and `make dist` makes every member world-readable.
> Keep the `rpmbuild/` tree private.

A fleet prepare (`prepare_rpmbuild_stampede3.sh`, `_ls6.sh`, `_horizon.sh`, or any `HPCS_BUNDLE_FLEET` or `scripts/fleet/*.force`) runs `scripts/site_conf.sh fleet-guard` before any build step.
The guard refuses when the effective `server` is empty, `localhost` or a loopback address.
`HPCS_ALLOW_DEFAULT_BROKER=1` turns the refusal into a warning for a deliberate local-broker build.
A plain `prepare_rpmbuild_dirs.sh` dev build needs no site file.

## Build a fleet RPM

Each cluster has one prepare wrapper that calls `prepare_rpmbuild_dirs.sh` and prints the `rpmbuild` line for the requested mode (release by default, debug with `--debug-build`).

To build a fleet RPM:

1. Run the wrapper for your cluster:

   ```bash
   ./scripts/prepare_rpmbuild_stampede3.sh
   ```

2. Run the `rpmbuild` line it prints:

   ```bash
   rpmbuild -ba --define "_topdir ${PWD}/rpmbuild" \
     "${PWD}/rpmbuild/SPECS/hpcperfstats.spec"
   ```

The fleet signal is opt-in: `HPCS_BUNDLE_FLEET=<CLUSTER>` and/or `scripts/fleet/<CLUSTER>.force`, which the wrapper creates for that dist only and `dist-hook` embeds.
Never commit a force file; a default `prepare_rpmbuild_dirs.sh` neither ships nor needs one.
An explicit `HPCS_BUNDLE_FLEET` wins when several fleet markers exist.
`<CLUSTER>` is `stampede3`, `ls6` or `horizon`.

| Cluster                         | Wrapper                                   | Fleet signal                                                   | Fleet configure matrix                                                                                                          |
| ------------------------------- | ----------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Stampede3 (x86_64)              | `./scripts/prepare_rpmbuild_stampede3.sh` | `HPCS_BUNDLE_FLEET=stampede3`, `scripts/fleet/stampede3.force` | `--enable-ib-mad-dlopen`, `--enable-opa-mad-dlopen`, `--disable-amd-gpu`, `--enable-intel-gpu` when vendored XPUM headers exist |
| Lonestar6 (x86_64)              | `./scripts/prepare_rpmbuild_ls6.sh`       | `HPCS_BUNDLE_FLEET=ls6`, `scripts/fleet/ls6.force`             | `--enable-ib-mad-dlopen`, `--disable-amd-gpu`, `--disable-intel-gpu` (forced even with vendored XPUM headers); no OPA MAD       |
| Horizon (aarch64 Grace + GB200) | `./scripts/prepare_rpmbuild_horizon.sh`   | `HPCS_BUNDLE_FLEET=horizon`, `scripts/fleet/horizon.force`     | `--enable-shared-node-multi-jid`, `--enable-ib-mad-dlopen`, `--disable-amd-gpu`, `--disable-intel-gpu`; no OPA MAD              |

No fleet build auto-adds `--disable-infiniband`.
The binary's `NEEDED` list must not name `libibmad` or `liboib_utils`.
On aarch64 a default prepare does not enable intel-gpu from vendored headers alone; it needs the Stampede3 fleet or `HPCS_BUNDLE_ENABLE_INTEL_GPU=1`.
Lonestar6 NVIDIA support stays on the default x86 DCGM runtime-dlopen path.
Install a Horizon RPM only after the consumer is upgraded ([deploy to nodes](deploy-to-nodes.md)).

### Stampede3 queues

One x86_64 RPM covers every Stampede3 LSPCI profile (skx, clx, icx, spr, h100, pvc, amd-rtx).

| Queue           | Collectors (when hardware and libraries are present)  | Runtime libraries for full fidelity             |
| --------------- | ----------------------------------------------------- | ----------------------------------------------- |
| skx / icx / spr | `host_opa` (sysfs); CPU direct perf                   | `liboib_utils` optional (MAD)                   |
| clx             | `host_ib` (CX6); CPU direct perf; no NVIDIA           | `libibmad` optional                             |
| h100            | `nvidia_gpu` + `host_ib` + `host_opa`                 | `libdcgm`, `libibmad`; `liboib_utils` optional  |
| pvc             | `intel_gpu` + `host_opa`                              | `libxpum` / xpumanager; `liboib_utils` optional |
| amd-rtx         | NVIDIA Blackwell + IB + OPA + AMD CPU (not `amd_gpu`) | `libdcgm`, `libibmad`; `liboib_utils` optional  |

### Lonestar6 queues

One x86_64 RPM covers the `genoa`, `milan` and `a100` profiles.

| Queue         | Collectors (when hardware and libraries are present) | Runtime libraries for full fidelity |
| ------------- | ---------------------------------------------------- | ----------------------------------- |
| genoa / milan | `host_ib` (CX6); AMD CPU direct perf; no NVIDIA      | `libibmad` optional                 |
| a100          | `nvidia_gpu` + `host_ib` + AMD CPU direct perf       | `libdcgm`, `libibmad` optional      |

The A100 has no GPM, so `nvidia_gpu` uses DCGM.
No watch profile carries the soft-defined board-power field IDs 1132/1133, which DCGM 3.1.x rejects at `FieldGroupCreate`.
DCGM 3.1.x on the A100 lists 1013/1014 but not 1015 (DFMA) or 1016 (integer pipe) in `dcgmi profile -l`, so the watch does not take `full-prof` and lands on `split-prof-no-dfma`.
That profile keeps `tensor_imma_active` and `tensor_hmma_active`; `tensor_dfma_active` and `integer_active` publish `NOT_PRESENT`.

### LSPCI fixtures

| Fixture directory                                      | Profiles                                   | Wired into a validate wrapper                   |
| ------------------------------------------------------ | ------------------------------------------ | ----------------------------------------------- |
| `tests/fixtures/stampede3_lspci_profiles/`             | skx clx icx spr h100 pvc amd-rtx           | Yes (`validate_stampede3_profile.sh`)           |
| `tests/fixtures/ls6_lspci_profiles/{genoa,milan,a100}` | genoa milan a100                           | No                                              |
| `tests/fixtures/horizon_lspci_profiles/gb`             | GB200 / HGX (roofline and fleet inventory) | No                                              |
| `tests/fixtures/vista_lspci_profiles/{gg,gh}`          | gg gh                                      | No; the Stampede3 wrapper rejects `gg` and `gh` |

## Validate each Stampede3 queue

To check one DEBUG fleet binary against every Stampede3 profile:

1. Install the same DEBUG fleet RPM on a node of each queue class (skx, clx, icx, spr, h100, pvc, amd-rtx), so every run sees the same `CAPS_JSON` and fleet slug.

2. On each node, run the wrapper with that node's profile:

   ```bash
   ./scripts/validate_stampede3_profile.sh --profile h100
   ```

The run writes `expectations_<SLUG>__<PROFILE>.json` and `test_runs/monitor/validate_<SLUG>__<PROFILE>_<DATE>.txt`.
`<SLUG>` is the capability slug, `<PROFILE>` the queue profile, `<DATE>` the run date.

## Build a debug RPM

A debug RPM carries symbols and the behavioural `DEBUG` build that writes the `/dev/shm` mirror.

To build and verify one:

1. Prepare with the debug flag:

   ```bash
   ./scripts/prepare_rpmbuild_dirs.sh --debug-build
   ```

2. Run the debug `rpmbuild` line and the `rpm_debug_shm_verify.sh` line it prints; the verify notes appear only with `--debug-build`.

The debug `%install` stashes `monitor-build-capabilities.json` under `rpmbuild/debug-verify/`, outside `BUILD/`.
That path survives the EL10 `rpmbuild` rmbuild step, which deletes `BUILD/hpcperfstats-<VERSION>/` after a successful build.
`./scripts/rpm_debug_shm_verify.sh` prefers the stash; `BUILD/.../.build-static` is only a fallback (for example with `rpmbuild --noclean`).
After install, the spec `%post` starts `hpcperfstats.service`, and payloads appear under `/dev/shm/hpcperfstatsd-debug/` (override with `HPCPERFSTATS_DEBUG_SHM_DIR`).

The spec sets `HPC_BUNDLE_ENABLE_DEBUG=1` only for `hpc_debug_build` RPMs.
The default release `rpmbuild` sets `HPC_BUNDLE_RELEASE_BUILD=1`, strips the binary and passes `--disable-debug`, so it has no `/dev/shm` mirror.
Compiler `-g` alone does not enable the mirror.

## Set a one-off Release

The spec line is `Release: %{?hpc_release}%{!?hpc_release:1}%{?dist}`.
The committed number is the fleet release for every cluster; it changes only for a user-approved production release (`cursor-rules/monitor-version-and-packaging.mdc`).

For a one-off build (debug, canary, a node that needs a higher release), use one of:

- `HPCS_RELEASE=<N>` before the prepare script; the printed `rpmbuild` lines gain `--define "hpc_release <N>"`.
- `HPCS_RELEASE=<N>` before `RPM_BUILD=1 ./scripts/rpm_debug_shm_verify.sh`.
- `--define "hpc_release <N>"` passed to `rpmbuild` yourself.

`<N>` starts with a digit and uses only `[0-9A-Za-z._]`.

## Move node edits into the site conf

A node checkout with local edits in the tracked conf or spec moves them into the site file before it pulls.

To migrate it:

1. Review the local edits:

   ```bash
   git diff src/hpcperfstats.conf hpcperfstats.spec
   ```

2. Copy the broker lines into the site file, then add any other key you changed:

   ```bash
   mkdir -p site
   grep -E '^(server|queue|port|user|password)[[:space:]:=]' \
     src/hpcperfstats.conf > site/hpcperfstats.site.conf
   chmod 600 site/hpcperfstats.site.conf
   ```

3. Restore the tracked files, pull, and build with your cluster's wrapper:

   ```bash
   git checkout -- src/hpcperfstats.conf hpcperfstats.spec
   git pull
   ./scripts/prepare_rpmbuild_stampede3.sh
   ```

## Verify the result

- `rpmspec -P hpcperfstats.spec` parses after any spec change.
- `rpm -qpR` on the built RPM and `readelf -d` on the binary show no `libibmad` or `liboib_utils` in `NEEDED` for a fleet build.
- A debug RPM shows payloads under `/dev/shm/hpcperfstatsd-debug/` after install.

## Troubleshooting

- The fleet guard refuses the build: the site file does not set a real `server`.
- An A100 node has no `nvidia_gpu` rows: check the journal for `field group creation failed`, the `stage=` value in the `no device rows emitted` warning, and the `Driver/stack probe` line (`libdcgm=`).

## Related

- [Deploy to nodes](deploy-to-nodes.md)
- [Run a correctness campaign](run-correctness-campaign.md)
- [Cross-compile](cross-compile.md)
- [Source modules](../reference/source-modules.md)
- [Monitor landing page](../../README.md)
