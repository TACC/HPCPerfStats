# Build the monitor RPM

This page shows RPM builders and node operators how to build an `hpcperfstatsd` RPM for their own cluster and roll it out, and shows monitor developers how to build a debug RPM.
Use it when you need an RPM for a set of compute nodes; the per-node install steps are in [Install the RPM](../../README.md#install-the-rpm).

## Prerequisites

- A monitor checkout on a build host with the same CPU family (x86_64 or aarch64) and OS major release as the compute nodes.
  The RPM takes the build host's architecture, and the daemon links glibc dynamically.
- The build packages from [Build the monitor RPM in the install tutorial](../../../docs/tutorials/install-hpcperfstats.md#build-the-monitor-rpm), including `cmake` for rabbitmq-c and `rpm-build`.
- Network access for the pinned dependency downloads.
- Run every command on this page from `HPCPerfStats/monitor/`.

## Build the RPM for your cluster

The default build probes the build host and needs no cluster-specific code.

To build it:

1. Put your broker settings in the site conf, so the RPM ships them:

   ```bash
   cp site/hpcperfstats.site.conf.example site/hpcperfstats.site.conf
   chmod 600 site/hpcperfstats.site.conf
   ```

   Set `server`, `queue`, `port`, `user` and `password` in it ([Set the site conf](#set-the-site-conf)).

2. Prepare the `rpmbuild` tree and source tarball:

   ```bash
   ./scripts/prepare_rpmbuild_dirs.sh
   ```

3. Run the `rpmbuild` line it prints:

   ```bash
   rpmbuild -ba --define "_topdir ${PWD}/rpmbuild" \
     "${PWD}/rpmbuild/SPECS/hpcperfstats.spec"
   ```

The RPM lands in `rpmbuild/RPMS/<ARCH>/hpcperfstatsd-<VERSION>-<RELEASE>.<ARCH>.rpm` and the SRPM in `rpmbuild/SRPMS/`.
`<ARCH>` is the build host architecture; `<VERSION>` and `<RELEASE>` come from `hpcperfstats.spec`.

The probes decide which optional collectors the RPM carries:

- InfiniBand MAD links `libibmad` when its devel files are on the build host; otherwise the build passes `--disable-infiniband` and IB counters come from sysfs only.
- Omni-Path MAD (`--enable-opa`) is added when `liboib_utils` and `oib_utils.h` are present; `host_opa` sysfs counters are always built.
- AMD GPU support needs the GPUPerfAPI SDK on the build host.
- NVIDIA GPU support needs nothing at build time: `libdcgm` and `libnvidia-ml` are loaded at runtime against vendored headers.

If one RPM must cover node classes the build host does not match, or must not depend on the build host's devel packages, write a fleet script ([Create your own fleet build script](#create-your-own-fleet-build-script)).

## Set the site conf

The spec installs the default conf `src/hpcperfstats.conf` as `/etc/hpcperfstats/hpcperfstats.conf`, overlaid with the untracked `site/hpcperfstats.site.conf` when it exists.
Never edit the tracked `src/hpcperfstats.conf` or `hpcperfstats.spec` on a build checkout; the site file is one per checkout, gitignored, with the same `key value` syntax.

The keys a site sets:

| Key                               | Default                | Meaning                                                                 |
| --------------------------------- | ---------------------- | ----------------------------------------------------------------------- |
| `server`, `port`                  | `localhost`, `5672`    | RabbitMQ host and TCP port the daemon publishes to                      |
| `queue`                           | `default`              | Queue name, one per cluster                                             |
| `user`, `password`                | `guest`                | Broker account                                                          |
| `sample_freq`, `sample_freq_slow` | `30`, `600`            | Fast-tier and full-sample intervals in seconds                          |
| `send_freq`                       | `300`                  | Publish interval in seconds                                             |
| `jobid_file`                      | `/var/run/stats_jobid` | Job id file the scheduler prolog writes (ignored by shared-node builds) |

`src/hpcperfstats.conf` lists every key the daemon reads, with a comment on each.

The spec `%build` runs `scripts/site_conf.sh overlay`: it replaces the value of every key the default conf has, appends keys it lacks, and keeps the default's comments and order.
`make dist` (`dist-hook`) copies the site file into the tarball.
With no site file the RPM ships `server localhost`, so each node needs its conf edited after install.
The script prints key names only, never values.

> [!WARNING]
> The tarball and SRPM under `rpmbuild/` hold the broker credentials, and `make dist` makes every member world-readable.
> Keep the `rpmbuild/` tree private.

### What the spec installs

| Path                                   | Mode   | Notes                                                       |
| -------------------------------------- | ------ | ----------------------------------------------------------- |
| `/usr/sbin/hpcperfstatsd`              | `0755` | The daemon                                                  |
| `/etc/hpcperfstats/hpcperfstats.conf`  | `0600` | `%config(noreplace)`: holds broker credentials              |
| `/etc/sysconfig/hpcperfstatsd`         | `0644` | `%config(noreplace)`: optional `HPCPERFSTATS_*` environment |
| `hpcperfstats.service` in the unit dir | `0644` | Runs `hpcperfstatsd -c /etc/hpcperfstats/hpcperfstats.conf` |
| `/usr/libexec/hpcperfstats/stubs/lshw` | `0755` | No-op `lshw` the unit binds over the real one for DCGM      |
| `/var/lib/hpcperfstats/dump`           | `0700` | Unsent samples spilled when the ring buffer is full         |

The package is `hpcperfstatsd` and the systemd unit is `hpcperfstats.service`.
`%post` enables the unit and starts (or restarts) it.

### Set a one-off Release

The spec line is `Release: %{?hpc_release}%{!?hpc_release:1}%{?dist}`.
The committed number is the fleet release for every cluster; it changes only for a user-approved production release (`cursor-rules/monitor-version-and-packaging.mdc`).

For a one-off build (debug, canary, a node that needs a higher release), use one of:

- `HPCS_RELEASE=<N>` before the prepare script; the printed `rpmbuild` lines gain `--define "hpc_release <N>"`.
- `HPCS_RELEASE=<N>` before `RPM_BUILD=1 ./scripts/rpm_debug_shm_verify.sh`.
- `--define "hpc_release <N>"` passed to `rpmbuild` yourself.

`<N>` starts with a digit and uses only `[0-9A-Za-z._]`.

### Move node edits into the site conf

A checkout with local edits in the tracked conf or spec moves them into the site file before it pulls.

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

3. Restore the tracked files, pull, and build again:

   ```bash
   git checkout -- src/hpcperfstats.conf hpcperfstats.spec
   git pull
   ./scripts/prepare_rpmbuild_dirs.sh
   ```

## Create your own fleet build script

A fleet build fixes the configure flags for one cluster instead of probing the build host, so one RPM fits every node class and links no optional fabric library.
The repository carries fleets for three TACC clusters; they are worked examples, not the way every site must build.

### What a fleet script does

A fleet `<NAME>` is four pieces that agree on the name:

1. A wrapper, `scripts/prepare_rpmbuild_<NAME>.sh`, creates the empty marker `scripts/fleet/<NAME>.force`, exports `HPCS_BUNDLE_FLEET=<NAME>` and runs `prepare_rpmbuild_dirs.sh` with its arguments.
2. The `dist-hook` in `Makefile.am` copies the marker into the tarball, so `build_static_bundle.sh` inside `rpmbuild` sees the fleet too.
3. `build_static_bundle.sh` has a `fleet_<NAME>_enabled` test and a branch in `static_bundle_print_detection_summary` that sets the fleet's configure flags instead of the host probes; `intel_gpu_bundle_auto_enable` decides `intel_gpu`.
4. `scripts/site_conf.sh fleet-guard` runs before any build step and refuses when the effective `server` is empty, `localhost` or a loopback address.
   `HPCS_ALLOW_DEFAULT_BROKER=1` turns the refusal into a warning.

An explicit `HPCS_BUNDLE_FLEET` wins when several markers exist.
Never commit a `.force` file; the default `prepare_rpmbuild_dirs.sh` neither ships nor needs one.

### Compare the example fleets

The three fleets differ in the hardware facts that drive every fleet decision:

| Build question            | Stampede3                               | Lonestar6               | Horizon                  |
| ------------------------- | --------------------------------------- | ----------------------- | ------------------------ |
| CPU family (RPM arch)     | x86_64, Intel and AMD                   | x86_64, AMD             | aarch64, Grace           |
| GPU stack                 | NVIDIA (DCGM/NVML) and Intel PVC (XPUM) | NVIDIA A100 (DCGM/NVML) | NVIDIA GB200 (NVML/DCGM) |
| Interconnect              | InfiniBand and Omni-Path                | InfiniBand              | InfiniBand               |
| Nodes shared between jobs | No                                      | No                      | Yes                      |

Each answer maps to a configure flag:

| Flag                             | Stampede3               | Lonestar6 | Horizon | Why                                                                |
| -------------------------------- | ----------------------- | --------- | ------- | ------------------------------------------------------------------ |
| `--enable-ib-mad-dlopen`         | Yes                     | Yes       | Yes     | IB MAD without `libibmad` in `NEEDED` or InfiniBand devel at build |
| `--enable-opa-mad-dlopen`        | Yes                     | No        | No      | Omni-Path MAD loads `liboib_utils` at runtime                      |
| `--enable-intel-gpu`             | When XPUM headers exist | No        | No      | Intel PVC nodes run XPU Manager                                    |
| `--disable-amd-gpu`              | Yes                     | Yes       | Yes     | No AMD GPUs, and no GPUPerfAPI SDK needed at build                 |
| `--enable-shared-node-multi-jid` | No                      | No        | Yes     | Several jobs per node: `%jobs`, `%affinity`, `host_mem_job`        |

The rest is the same for every fleet:

- Static dependencies are the pinned `libev` and `rabbitmq-c` archives (plus `libbpf` for the eBPF profiler), built once into `embedded-static-prefix/`; the pins are the `STATIC_PIN_*` variables in `scripts/build_static_bundle.sh`.
- CPU counters need no flag: `--with-cpu-counter-backend=auto` picks `x86` or `arm` from the build host, and the CPU vendor is read at runtime.
- NVIDIA GPU support is always built and loaded at runtime, so a CPU-only node class costs nothing.
- The release build adds `-O3 -DNDEBUG` and no `-march` or `-mcpu`, so the binary runs on every CPU of its family.
  The debug RPM compiles with the distribution `%{optflags}`, which on EL9 x86_64 includes `-march=x86-64-v2`.
- No fleet adds `--disable-infiniband`; the binary's `NEEDED` list must not name `libibmad` or `liboib_utils`.
- The RPM lands in `rpmbuild/RPMS/x86_64/` for the x86 fleets and `rpmbuild/RPMS/aarch64/` for Horizon, built on a host of that family.

On aarch64 a default build does not enable `intel_gpu` from vendored headers alone; it needs a fleet that enables it or `HPCS_BUNDLE_ENABLE_INTEL_GPU=1`.

### Write the script for your cluster

To add a fleet `<NAME>`:

1. Answer the build questions above for every node class of your cluster.

2. Copy the example wrapper closest to your cluster and change the marker and fleet name:

   ```bash
   sed 's/ls6/<NAME>/g; s/Lonestar6/<CLUSTER>/g' \
     scripts/prepare_rpmbuild_ls6.sh \
     > scripts/prepare_rpmbuild_<NAME>.sh
   chmod 755 scripts/prepare_rpmbuild_<NAME>.sh
   ```

3. In `scripts/build_static_bundle.sh`, add `fleet_<NAME>_enabled` next to `fleet_ls6_enabled`, a branch in `static_bundle_print_detection_summary` that appends your flags to `STATIC_BUNDLE_FEAT_FLAGS`, and your fleet to `intel_gpu_bundle_auto_enable` and the `Fleet matrix:` summary.

4. In `Makefile.am`, add a `dist-hook` block that copies `scripts/fleet/<NAME>.force`, and add the wrapper to `EXTRA_DIST` and to the file list `prepare_rpmbuild_dirs.sh` checks before `make dist`.

5. Add the marker to `scripts/fleet/README`, copy `tests/test_prepare_rpmbuild_ls6.sh` for the new wrapper, and register it in `tests/Makefile.am`.

6. Check the flags without building:

   ```bash
   HPCS_BUNDLE_FLEET=<NAME> \
     ./scripts/build_static_bundle.sh --print-configure-flags
   ```

7. Build with `./scripts/prepare_rpmbuild_<NAME>.sh`, then run the `rpmbuild` line it prints.

`<NAME>` is a short lowercase fleet name and `<CLUSTER>` the cluster's display name.
A fleet that turns on `--enable-shared-node-multi-jid` changes the wire format; deploy the consumer first ([Deploy to nodes](#deploy-to-nodes)).

### Configure options

`./configure --help` lists every option; these are the ones a fleet chooses between:

| Option                                    | Effect                                                                                               |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `--enable-all-static`                     | Link the pinned static libev and rabbitmq-c (the default).                                           |
| `--enable-shared-node-multi-jid`          | Shared-node build: `%jobs`, `%affinity` and `host_mem_job` (see [design](../explanation/design.md)). |
| `--enable-opa`, `--enable-opa-mad-dlopen` | Omni-Path STL MAD, link-time or runtime `dlopen`.                                                    |
| `--enable-ib-mad-dlopen`                  | InfiniBand MAD by runtime `dlopen`.                                                                  |
| `--enable-infiniband`                     | Link-time `-libmad` when the devel stack is present (the default outside fleets).                    |
| `--enable-intel-gpu={auto,yes,no}`        | Intel PVC collector (`auto` runs `scripts/gpu_lspci_probe.sh intel`).                                |

`hpcperfstatsd` uses AMQP and libev and reads hardware counters by direct `perf_event_open`, with no PMU library, so `configure` requires rabbitmq-c.
`--enable-opa` needs the Cornelis or Intel IFS `liboib_utils` and its `oib_utils.h` on the build host.
To configure by hand against the prefix of `scripts/ensure_dotbuild_prefix_static.sh`, point `CPPFLAGS` and `LDFLAGS` at it.
Collector behaviour behind each flag is in [source modules](../reference/source-modules.md).

### Example: Stampede3 and Lonestar6 queues

One x86_64 Stampede3 RPM covers every LSPCI profile (skx, clx, icx, spr, h100, pvc, amd-rtx):

| Queue           | Collectors (when hardware and libraries are present)  | Runtime libraries for full fidelity             |
| --------------- | ----------------------------------------------------- | ----------------------------------------------- |
| skx / icx / spr | `host_opa` (sysfs); CPU direct perf                   | `liboib_utils` optional (MAD)                   |
| clx             | `host_ib` (CX6); CPU direct perf; no NVIDIA           | `libibmad` optional                             |
| h100            | `nvidia_gpu` + `host_ib` + `host_opa`                 | `libdcgm`, `libibmad`; `liboib_utils` optional  |
| pvc             | `intel_gpu` + `host_opa`                              | `libxpum` / xpumanager; `liboib_utils` optional |
| amd-rtx         | NVIDIA Blackwell + IB + OPA + AMD CPU (not `amd_gpu`) | `libdcgm`, `libibmad`; `liboib_utils` optional  |

One x86_64 Lonestar6 RPM covers the `genoa` and `milan` CPU nodes (`host_ib`, AMD CPU direct perf, `libibmad` optional) and the `a100` nodes, which add `nvidia_gpu` with `libdcgm`.
The A100 DCGM watch profile is in [source modules](../reference/source-modules.md).

The LSPCI fixtures that tests and validators read:

| Fixture directory                                      | Profiles                                   | Wired into a validate wrapper                   |
| ------------------------------------------------------ | ------------------------------------------ | ----------------------------------------------- |
| `tests/fixtures/stampede3_lspci_profiles/`             | skx clx icx spr h100 pvc amd-rtx           | Yes (`validate_stampede3_profile.sh`)           |
| `tests/fixtures/ls6_lspci_profiles/{genoa,milan,a100}` | genoa milan a100                           | No                                              |
| `tests/fixtures/horizon_lspci_profiles/gb`             | GB200 / HGX (roofline and fleet inventory) | No                                              |
| `tests/fixtures/vista_lspci_profiles/{gg,gh}`          | gg gh                                      | No; the Stampede3 wrapper rejects `gg` and `gh` |

## Deploy to nodes

Roll an RPM out in this order, because the consumer must parse what the new monitor sends.

To deploy:

1. On a cluster that runs a shared-node RPM (`--enable-shared-node-multi-jid`, such as the Horizon example), upgrade the consumer (`listend`) first, because shared-node payloads carry `%jobs` and `%affinity` marks.
2. Install the RPM on a few nodes, check them ([Install the RPM](../../README.md#install-the-rpm)), then install it on the rest.
3. Optional: on shared-node builds only, set `jid_require_step_id`, `jid_discover_pid_cap` and `affinity_pid_sample_limit` in the conf.

An upgrade keeps a conf edited on the node and writes the packaged one as `hpcperfstats.conf.rpmnew`; an unedited conf is replaced by the packaged one.

### Disable one collector

To switch a collector off without a rebuild (for example `intel_gpu` on PVC nodes, which clears its GEM maps and VSZ), use any one of:

- `disable_types intel_gpu` in the conf.
- `--disable-types intel_gpu` on the daemon command line.
- `HPCPERFSTATS_DISABLE_TYPES=intel_gpu` in `/etc/sysconfig/hpcperfstatsd`.

The startup journal lines `Enabled types` and `Disabled types` list the collectors a run uses:

```bash
journalctl -u hpcperfstats --since -1h | grep -E 'abled types'
```

## Build a debug RPM

A debug RPM is for monitor developers: it carries symbols and the behavioural `DEBUG` build that writes the `/dev/shm` mirror the correctness checks read.

To build and verify one:

1. Prepare with the debug flag (or `./scripts/prepare_rpmbuild_<NAME>.sh --debug-build` for a fleet):

   ```bash
   ./scripts/prepare_rpmbuild_dirs.sh --debug-build
   ```

2. Run the debug `rpmbuild` line and the `rpm_debug_shm_verify.sh` line it prints; the verify notes appear only with `--debug-build`.

The debug `%install` stashes `monitor-build-capabilities.json` under `rpmbuild/debug-verify/`, outside `BUILD/`.
That path survives the EL10 `rpmbuild` rmbuild step, which deletes `BUILD/hpcperfstats-<VERSION>/` after a successful build.
`./scripts/rpm_debug_shm_verify.sh` prefers the stash; `BUILD/.../.build-static` is only a fallback (for example with `rpmbuild --noclean`).
After install, `%post` starts `hpcperfstats.service`, and payloads appear under `/dev/shm/hpcperfstatsd-debug/` (override with `HPCPERFSTATS_DEBUG_SHM_DIR`).
The debug RPM also sets `sample_freq 30` and `sample_freq_slow 60`.

The spec sets `HPC_BUNDLE_ENABLE_DEBUG=1` only for `hpc_debug_build` RPMs.
The release `rpmbuild` sets `HPC_BUNDLE_RELEASE_BUILD=1`, strips the binary and passes `--disable-debug`, so it has no `/dev/shm` mirror.
Compiler `-g` alone does not enable the mirror.

The full campaign on a DEBUG build is in [Run a correctness campaign](run-correctness-campaign.md).

### Validate each Stampede3 queue

The Stampede3 example has a wrapper that checks one DEBUG fleet binary against each of its LSPCI profiles.

To run it:

1. Install the same DEBUG fleet RPM on a node of each queue class (skx, clx, icx, spr, h100, pvc, amd-rtx), so every run sees the same `CAPS_JSON` and fleet slug.

2. On each node, run the wrapper with that node's profile:

   ```bash
   ./scripts/validate_stampede3_profile.sh --profile h100
   ```

The run writes `expectations_<SLUG>__<PROFILE>.json` and `test_runs/monitor/validate_<SLUG>__<PROFILE>_<DATE>.txt`.
`<SLUG>` is the capability slug, `<PROFILE>` the queue profile, `<DATE>` the run date.

### Metric profiler

`--enable-metric-profiler` times each type's `st_collect` and each metric's `stats_set` / `stats_inc` path; without it none of the profiler is compiled.
`--with-metric-profiler-backend=none` keeps attribution internal (wall time, thread CPU time, inferred wait).
`--with-metric-profiler-backend=ebpf` also emits host-side attribution signals with each periodic report, and `scripts/build_static_bundle.sh` then builds a pinned static `libbpf` into the same `PREFIX`.

The daemon profiles every collect cycle through `stats_runtime_collect_cycle` (`metric_profiler_cycle_begin` / `_end`).
It prints `metric-profiler:*` lines with call counts, total, average, minimum and maximum wall time, CPU time, and inferred wait (`wall-cpu`, clamped at zero).
The periodic report comes every 120 cycles (one hour at `sample_freq 30`), and `metric_profiler_report` prints the cumulative report once at shutdown (`#ifdef MONITOR_METRIC_PROFILER` in `monitor.c`).
`measurement/bin/run_overhead_profile.sh --profile-collectors` uses it for one labelled, non-headline leg.

### Shared-node build check

To check the shared-node flavour, run `./scripts/run_shared_node_static_check.sh`, or `./scripts/build_static_bundle.sh --enable-shared-node-multi-jid` followed by `make -C .build-static check`.

## Verify the result

- `rpmspec -P hpcperfstats.spec` parses after any spec change.
- `rpm -qpR` on the built RPM and `readelf -d` on the binary show no `libibmad` or `liboib_utils` in `NEEDED` for a fleet build.
- On a node, `systemctl status hpcperfstats --no-pager` shows the unit active and `rpm -q hpcperfstatsd` prints the installed release.
- A debug RPM shows payloads under `/dev/shm/hpcperfstatsd-debug/` after install.

## Troubleshooting

- The fleet guard refuses the build: the site file does not set a real `server`.
- `systemctl is-active hpcperfstatsd` prints `inactive`: that unit does not exist; query `hpcperfstats`.
- A changed conf did not take effect after an upgrade: the node conf was edited, so the packaged one is in `hpcperfstats.conf.rpmnew`; merge it by hand.
- An A100 node has no `nvidia_gpu` rows: check the journal for `field group creation failed`, the `stage=` value in the `no device rows emitted` warning, and the `Driver/stack probe` line (`libdcgm=`).

## Related

- [Install the RPM](../../README.md#install-the-rpm)
- [Run a correctness campaign](run-correctness-campaign.md)
- [Cross-compile](cross-compile.md)
- [Source modules](../reference/source-modules.md)
- [Design](../explanation/design.md)
