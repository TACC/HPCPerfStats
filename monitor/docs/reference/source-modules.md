# Monitor source modules

This page maps the monitor's `src/` modules to what they do and describes per-collector behaviour for monitor and consumer developers.
Build flags and fleet configure matrices are in [build the RPM](../how-to/build-the-rpm.md); keys and units are in [the metrics reference](../METRICS_REFERENCE.md).

## Module map

The daemon splits into small, testable units along these lines (not exhaustive; all paths under `src/`).

| Area                                                    | Files                                                                                                        |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Stats type registry                                     | `stats_registry.c`, `stats_registry.h`                                                                       |
| Shared direct perf helpers                              | `perf_util.c`, `perf_util.h`                                                                                 |
| Grace core counters                                     | `arm_core_perf.c`, `arm_core_perf.h`, `cpu_counter_metrics_arm_publish.c`                                    |
| Grace DRAM CAS (`arm_aarch64_imc`)                      | `arm_imc.c`, `arm_imc_scf.c`, `arm_imc_scf.h`                                                                |
| `host_cpu_hw` on aarch64 (`arm` backend)                | `cpu_counter_metrics.c`, `cpu_counter_metrics_arm_{publish,util}.{c,h}`, `cpu_counter_metrics_arm_state.h`   |
| `host_power_socket`                                     | `arm_power_hwmon.{c,h}`, `arm_power_socket.{c,h}`                                                            |
| `nvidia_gpu` NVML and GPM path                          | `nvidia_gpu_nvml.{c,h}`, `nvidia_gpu_nvml_convert.{c,h}`, `third_party/nvidia-nvml/nvml.h`                   |
| `nvidia_gpu` DCGM path                                  | `nvidia_gpu_dcgm_watch.c`, `dcgm_gpu_dyn.c`, `dcgm_session.c`, `nvidia_gpu_dcgm_profiles.c`                  |
| DCGM watch cadence                                      | `dcgm_watch_policy.c`, `dcgm_watch_policy.h`                                                                 |
| GPU FLOP estimate                                       | `nvidia_gpu_estimate.c`                                                                                      |
| Collect cycles and sinks                                | `stats_runtime.c`, `stats_runtime.h`, `stats_sink.h`                                                         |
| Archive and buffer row text, schema suffixes, `%` marks | `stats_text_format.c`, `stats_text_format.h`                                                                 |
| Options and argv parsing                                | `monitor_options.c`, `monitor_options.h`, `monitor_cli.c`, `monitor_cli.h`                                   |
| Daemon loop, RabbitMQ send, ring buffer                 | `monitor_daemon.c`, `monitor_daemon.h`, `monitor_release_log.c`, `monitor_timing.c`                          |
| Schema text                                             | `schema_entry_parse.c` (`parse_schema_entry`), `schema.c`                                                    |
| RabbitMQ payloads                                       | `stats_buffer.c`, `stats_buffer_data_append.c`                                                               |
| DEBUG shm mirror                                        | `stats_buffer_debug_shm.c`, `stats_buffer_debug_shm.h`                                                       |
| Private dirs, privileged `dlopen` path gate             | `secure_path.c` (`ensure_private_dir`), `dyn_lib_path.c` (`dyn_lib_path_allowed`)                            |
| Intel CPUID and generation gating                       | `cpuid.c`, `intel_cpuid_match.c`, `intel_processor.c`                                                        |
| AMD EPYC CPUID                                          | `amd_cpuid_match.c`, `amd_processor.c`                                                                       |
| x86 core counters (`x86` backend)                       | `x86_core_perf.c`, `x86_core_perf.h`, `cpu_counter_metrics_x86_publish.{c,h}` (`publish_x86_measured_stats`) |
| x86 uncore DRAM counters                                | `x86_uncore_perf.c`, `x86_uncore_perf.h`, `intel_{skx,icx,spr,emr,gnr,srf}_imc.c`, `amd_x86_uncore_df.c`     |
| x86 RAPL energy                                         | `x86_rapl.c`, `x86_rapl.h`, `rapl_powercap.c`, `intel_rapl.c`, `amd64_rapl.c`                                |
| Omni-Path and Cornelis HFI (`host_opa`)                 | `opa.c`, `opa_sysfs.c`, `opa_mad_backoff.c`, `opa_mad_dyn.c`, `host_opa.h`                                   |
| NFS client (`host_nfs`)                                 | `nfs.c`, `host_nfs.h`                                                                                        |
| BeeGFS client (`beegfs_client`)                         | `beegfs.c`, `beegfs_client.h`, `beegfs_mount.c`, `beegfs_tcp.c`                                              |
| Intel Data Center GPU (`intel_gpu`)                     | `intel_gpu.c`, `intel_gpu.h`, `intel_gpu_xpumcli.c`, `xpum_gpu_dyn.c`, `intel_gpu_pci.c`                     |
| InfiniBand MAD `dlopen`                                 | `ib_mad.c`, `ib_mad_dyn.c`, `ib_mad_api.h`, `third_party/ibmad-shim/`                                        |
| InfiniBand versus HFI routing                           | `ib_common.c` (`ib_hca_is_opa_hfi`), `ib_family.c`                                                           |
| GPU PCI detection                                       | `gpu_pci_detect.c`, `hwdetect.c`                                                                             |

## Module notes

Stats type registry
: The `st_name` table for lookup and iteration is ordered by legacy variable names, so `stats_type_get` scans linearly.
Optional modules are guarded by configure `MONITOR_WITH_*` and backend macros; keep them aligned with `TYPES` in `Makefile.am`.

Shared direct perf helpers
: `perf_util` is portable and unit-tested on every host through `tests/test_fake_perf.{c,h}`.
It opens events by raw code or sysfs attributes, reads the per-boot PMU `type` and `cpumask`, reads `{value, time_enabled, time_running}` scaled by the enabled and running deltas, and maps open errno to a sentinel; events are never pinned.

Grace core counters
: Nine events per CPU (CYC, INS, FP DP, SP and HP, FIXED, SCALE, SVE INT8 and INT16), one fd each on `armv8_pmuv3*`.
Events resolve from sysfs names first, then from a MIDR-keyed raw-code table with one row (Arm Neoverse V2, MIDR `0x410fd4f0`).
Another core logs its MIDR once and leaves those keys at 0; a key whose event is not open is not published.
`publish_arm_measured_stats` maps the events to keys: `arm_fp_{dp,sp,hp}_spec` (`arm_fp_dp_spec`, `arm_fp_sp_spec`, `arm_fp_hp_spec`; `E,W=64`, FP instructions by precision over all vector widths, from the `FP_*_SPEC` events), `arm_est_flops` (`FP_FIXED_OPS_SPEC` + `FP_SCALE_OPS_SPEC`, measured FLOPs), and `arm_int8_ops` / `arm_int16_ops` (`ASE_SVE_INT8_SPEC`, `ASE_SVE_INT16_SPEC`, W=48).
The Intel names `fp_arith_inst_retired_scalar_{single,double}` stay 0 on Grace.
Analysis reads the FP keys as `avg_fp{64,32,16}_instr`.

Grace DRAM CAS
: One `scf<N>` device per `nvidia_scf_pmu_<N>` (1 on GH200, 2 on GB200 and Grace-Grace) reads `cmem_rd_data` and `cmem_wr_total_bytes` (64-byte CAS) by direct perf on its own fds, with a perf `dmc` or `imc` scan as fallback.
While no memory PMU is bound it re-scans every 10 ticks.

`host_cpu_hw` on aarch64
: Util comes from `/proc/stat` jiffies over the collect interval, and `cpu_util_{total,user,sys,irq,nice}_accum_us` (the `cpu_util_*_accum_us` keys) are CPU microseconds.
The clock comes from cpufreq `scaling_cur_freq`, never `cpuinfo_cur_freq` (which reports bogus CPPC values on Grace), falling back to `base_frequency`, `cpuinfo_max_freq` and `scaling_max_freq`.
Fail-soft cycle keys accumulate from util and `clock_khz`: `mperf` is reference cycles (`ref = clock_khz x delta_t_us / 1000`), `aperf` is active cycles (`act = ref x util_total / 100`), and `cpu_clock_est_cycles` equals `aperf` (W=64).
When the CYC event is open and its scaled lifetime is above 0, all three carry measured cycles instead.
`instr_retired`, the `arm_fp_*` keys, `arm_est_flops` and the SVE integer keys come only from measured events; nothing synthesizes instructions or FLOPs from util.
The backend uses no DCGM: embedded DCGM ran five 100-200 Hz timer loops (about 615 wakeups/s) whatever the watch cadence, about 8 core-s/h under the `performance` governor and about 109 under `ondemand`.

`host_power_socket`
: ACPI `<Name> Power Socket <N>` meters (CPU, Grace or socket, SysIO, Module), one row per socket, disabled where no meter exists.
`arm_power_hwmon` indexes labels across the EL10 `hwmonN/` and EL9 `hwmonN/device/` layouts with one fd per meter.

DCGM watch cadence
: `dcgmWatchFields` refresh equals the collect interval (`sample_freq`, set on conf load and SIGHUP), and history is 2 refreshes; with no recorded interval each caller's fallback refresh applies.
`dcgm_session.c` starts the embedded engine with the NVSwitch module denied (`DcgmModuleIdNvSwitch`) and logs which start path ran.

Daemon loop
: `monitor_release_log.c` gives release builds a compact startup block, an hourly status line and first-fail RabbitMQ errors; DEBUG builds keep verbose tick status and resend chatter.
The full `$` schema header goes out every 6 h and when JOBID unloads to `-`.
After each collect, `monitor_daemon_reanchor_sample_timer` re-arms a one-shot timer to the next `sample_freq` boundary measured from libev's cached `ev_now()` (which predates the collect).
A boundary less than a quarter period after the last collect start counts as served, so each boundary gets one collect (`monitor_timing_reanchor_wait` in `monitor_timing.c`).

Options
: `monitor_options` parses the long options; `monitor_cli` holds thin entrypoints and the `monitor_cli_lit_*` literals it shares with `monitor_daemon.c`.

RabbitMQ payloads
: A persistent AMQP connection, a cached `uname` for header and sample lines, and batched rows; the `syslog` INFO declare runs in `DEBUG` builds only.
The soft merge cap `STATS_BUFFER_RMQ_SOFT_MAX_BYTES` (32 MiB) stops adding samples to one AMQP body when the next would exceed it; one drain call can still publish several such messages, and a single oversize sample or `$` payload publishes alone.

x86 counters
: [x86 counters and roofline peaks](../explanation/x86-counters-and-roofline.md) describes the event rows, uncore profiles and CPU classes.

NFS client
: Reads `/proc/self/mountstats`, one row per `fstype nfs` or `nfs4` mount point; the positional `events:`, `xprt:` and per-op fields are read by kernel field index.

## Omni-Path and Cornelis (`host_opa`)

- `host_opa` is always built and collects Cornelis CN5000 and Intel Omni-Path HFI 100 Series devices (`hfi1_*` under `/sys/class/infiniband`).
- Device ids use a slash (`hfi1_0/1`), not the InfiniBand dot form.
- Sysfs is the default source: overlapping utilization counters come from `ports/N/counters` first (Stampede3 OPA100), with `port_*_packets` mapped to schema `*_pkts`.
- A schema key without its counter file (for example without `multicast_*`) emits `NOT_PRESENT` (`-2`).
- On Cornelis CN5000 NDR the classic `counters/` files often exist but read `EINVAL`; when no classic key is readable the collector maps `ports/N/hw_counters` instead.
  `TxWords`, `RxWords`, `TxPkt`, `RxPkt` and `TxWait` map to `port_xmit_data`, `port_rcv_data`, `port_xmit_pkts`, `port_rcv_pkts` and `port_xmit_wait`; `DmaWait` and `RcvHdrOvr*` are not mapped into `KEYS`.
- STL-only keys (FECN, BECN, bubbles and others) emit `NOT_BUILT` (`-10`) on a build without OPA MAD, and `NOT_PRESENT` (`-2`) on a MAD-dlopen build whose `liboib_utils` is missing at runtime.
- Only collectible ports are read; inactive or DOWN ports are skipped (`ib_port_collectible`), so a CN5000 dual-port card with port 1 Offline emits only `hfi1_0/2`.
- With `--enable-opa-mad-dlopen`, STL MAD compiles against `third_party/oib-shim/` and loads `liboib_utils` at runtime (`HPCPERFSTATS_OIB_LIB`).
  When the library is missing (common on PVC and other nodes with only `opa-basic-tools`, which does not provide it), MAD is probed once and disabled with no recurring journal lines, and sysfs `host_opa` continues.
- `host_ib` never claims `hfi1_*` HCAs.
- Stampede3 PCI examples: Cornelis CN5000 HFI (SPR); Intel Omni-Path HFI Silicon 100 Series (ICX, SKX, H100).

## InfiniBand (`host_ib`)

- Sysfs under `/sys/class/infiniband` (non-`hfi1_*`) is always compiled and supplies every host counter: `ports/<P>/counters/` and `hw_counters/`, including the 64-bit `port_{xmit,rcv}_data` and `port_{xmit,rcv}_packets` (cumulative since boot).
- The collector sends no host-port MAD query.
- MAD serves only the switch-side keys `sw_rx_bytes`, `sw_rx_packets`, `sw_tx_bytes` and `sw_tx_packets`: an SMP PortInfo to the attached switch port, then its PortCountersExtended.
- MAD failures use per-port backoff (`BACKOFF`, `BACKEND_ERROR`), `NOT_PRESENT` once the port refused the switch MAD open, and `NOT_BUILT` without MAD.
- Ports must be ACTIVE or phys LinkUp, and `link_layer=InfiniBand` when that sysfs file exists, so Ethernet-mode mlx HCAs (for example Horizon `mlx5_2`) are skipped and MAD is not attempted on them.
- With `--enable-ib-mad-dlopen`, MAD compiles against `third_party/ibmad-shim/` and loads `libibmad` at runtime (`HPCPERFSTATS_IBMAD_LIB`).

`<P>` is a port number.

## Intel Data Center GPU (`intel_gpu`)

- `--enable-intel-gpu={auto,yes,no}` defaults to `auto` through `scripts/gpu_lspci_probe.sh intel` and compiles against the vendored `third_party/intel-xpum/` headers (XPUM 1.2.33 only, never system `/usr/include`).
- The default collect backend is `xpumcli`, a short-lived child.
  It avoids in-process `libxpum` and Level Zero, which on Stampede3 PVC held hundreds of `i915.gem` maps (about 16 TiB VSZ) and stressed GuC error capture.
- Choose the backend with conf `intel_gpu_backend xpumcli|libxpum` or `HPCPERFSTATS_INTEL_GPU_BACKEND` (default `xpumcli`).
- The `xpumcli` path runs `discovery`, then `dump -d <IDS> -m 0,1,2,3,4,5,9,17,18,31,35 -i 1 -n 1` (`INTEL_GPU_XPUMCLI_DUMP_METRICS`) with a bounded fork/exec timeout of about 8 s.
  Dump IDs are not API `XPUM_STATS_*` values: dump 1 is `power_usage` and dump 31 is `compute_engine_util`.
  PCIe and Xe Link counters emit `NOT_BUILT` on this path, because the dump has throughput, not cumulative counters.
- The opt-in `libxpum` backend loads the library at runtime (`/usr/lib64/libxpum.so` and others; override with `HPCPERFSTATS_XPUM_LIB`); the build has no link-time `-lxpum`.
  Before `xpumInit` it sets `XPUM_DISABLE_PERIODIC_METRIC_MONITOR=1` and overwrites `XPUM_METRICS=0,1,4,6-10,15,30,34,35,36` (15 is compute engine group utilization, 30 is memory temperature).
  Use it only in a lab, because of the GuC, VSIZE and segfault risk.
- `compute_engine_util` is compute engine group utilization in percent (max over tiles), from dump metric 31 or `XPUM_STATS_ENGINE_GROUP_COMPUTE_ALL_UTILIZATION` (`XPUM_METRICS` 15).
- `sm_active` (EU Array Active) is `NOT_PRESENT` on PVC GFX firmware older than `PVC2_1.23423`, the XPUM minimum, where XPUM prints `N/A`.
  A metric the dump omits or prints as `N/A` is `NOT_PRESENT` for that collect only, so `sm_active` reports after a firmware update with no monitor change.
- Backend failure keeps the rows: when `xpumd` is unreachable, `xpumcli` discovery or dump fails, libxpum init fails or every device fetch fails, each GPU still emits its row with every key a sentinel (`BACKEND_ERROR` on a failed attempt, `BACKOFF` while skipping).
- After `INTEL_GPU_MISS_STREAK_MAX` (3) failed attempts in a row the collector waits `INTEL_GPU_BACKOFF_SEC` (300 s) before the next one, so a dead `xpumd` costs one `xpumcli` attempt per cooldown.
  The type is never disabled: when `xpumd` returns, the next attempt publishes real values with no restart.
- Failure rows use the last discovered device ids.
  When discovery never succeeded they are `"0"` to `"N-1"`, where N counts the Data Center GPU Max PCI functions in sysfs (`intel_gpu_pci.c`: vendor 8086, display or accelerator class, the Ponte Vecchio device ids).
  On the `xpumcli` path the PCIe and Xe Link keys stay `NOT_BUILT` in failure rows too.
- A node without such a function emits no `intel_gpu` rows; the validator probe `probe_intel_gpu_devices` counts the same sysfs functions.
- Stampede3 PVC has four Data Center GPU Max 1550 cards, device rows `"0"` to `"3"`.
- `HPCPERFSTATS_FORCE_INTEL_GPU` forces the type on for testing; startup logs Enabled types and Disabled types.

`<IDS>` is the comma-separated device id list from discovery.

## Lustre (`lustre_mdc`, `lustre_osc`, `lustre_llite`)

- `lustre_mdc` and `lustre_osc` read `md_stats` and `osc_stats`, falling back to `.../stats`.
- `lustre_osc` is built and registered with `--enable-lustre`.
- `lustre_llite` publishes `vfs_*` opcode keys and the capacity gauges `fs_bytes_*` and `fs_files_*` from `/sys/fs/lustre/llite`.
- llite opcode `stats` come from `/proc/fs/lustre/llite/<INST>/stats` when that directory exists, else debugfs `/sys/kernel/debug/lustre/llite/<INST>/stats` (Lustre 2.15, root only).
  With neither, the `vfs_*` keys get the errno code (`NOT_PRESENT`) rather than `NOT_UPDATED`.
- The llite event map for older archives is `type_events.lustre_llite` in `hpcperfstats/dbload/lib/monitor_naming/monitor_variable_rename_map.yaml`.

`<INST>` is the llite instance directory name.

## BeeGFS (`beegfs_client`)

Detection
: Mounts with fstype `beegfs` or `beegfs_nodev`; the device id is the mount path.

Capacity
: `statvfs` gives `fs_bytes_*` and `fs_files_*` every sample.

I/O bytes
: `vfs_write_bytes` and `vfs_read_bytes` are bytes acknowledged and received on the client's established TCP connections to its storage servers (`NETLINK_SOCK_DIAG` `tcp_info`, unprivileged), with no fork, no server request and no DNS.
They are wire bytes: they include BeeGFS message headers (+0.03% on 1 GiB direct I/O), count buffered writes when the client flushes them, and miss reads served from the client page cache.

Mount to client
: `/proc/fs/beegfs/<ID>/config` `cfgFile` matched with the mount's `cfgFile=` option, else the only instance, else `NOT_PRESENT`.

Totals
: Per-socket growth, keyed by socket cookie, adds to a per-mount total, so reconnects never make the counter go backwards.

Not countable
: `NOT_PRESENT` when the client's `storage_nodes` lists RDMA or SDP connections (that traffic bypasses TCP), when a node has no readable connection line, when fewer sockets match than its listed TCP connection count (the listing names one address per server, so connections to a multi-homed server's other addresses would be missed), or when no instance exists.
A listing whose nodes are all idle (`<none>`) is a real 0 growth.

Metadata ops
: The 15 `vfs_*_ops` keys are `NOT_PRESENT`: the 7.3 client keeps no op counters, and `beegfs-ctl --clientstats` makes every server dump its whole client table per call.

Cadence
: Slow tier when `enable_slow_tier` is on; otherwise at most once per `max(sample_freq, 30s)`; skipped ticks republish the cached totals or code.

Configure
: `--enable-beegfs` (default yes); capability slug fragment `beegfs`.

Consumer
: The web FSIO view includes BeeGFS alongside Lustre and NFS (`beegfs_client` in Resources, Summary and `detail_fsio_beegfs_*`).

`<ID>` is the BeeGFS client instance id.

## DEBUG `/dev/shm` mirror

A daemon built with `--enable-debug` mirrors its latest outbound payloads under `/dev/shm/hpcperfstatsd-debug/`.

| File                                         | Content                                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------ |
| `schema`                                     | Complete `$` rotation payload (`write_hdr=1`)                                  |
| `fast`                                       | Latest `@fast` sample (one payload)                                            |
| `full`                                       | Latest `@full` or full-width sample (one payload)                              |
| `fast.burst`, `full.burst`                   | Every payload of the latest sample tick, concatenated                          |
| `sentinels`, `sentinel_sites`, `fault_fired` | Sentinel provenance, failure-site hit counts, `debug_fault_inject` fire counts |

- On shared-node builds a multi-jid tick emits N jid payloads plus one node-wide `-` payload.
  `fast` and `full` keep the last jid payload (the trailing `-` still goes to RabbitMQ but does not overwrite them), and `fast.burst` and `full.burst` hold the whole tick, parts separated by `#---burst-part---`.
- An idle node (`disc.n==0`) still mirrors `-` into `fast`, `full` and the matching `.burst` file.
- At startup the daemon removes `fast`, `full` and both `.burst` files left by the previous daemon; the new `schema` is written at once and the samples on the first tick, so a reader never pairs an old build's sample with the new schema.
- `validate_shm_messages.py --wait-shm-seconds <N>` waits until `fast` and `full` exist with a header timestamp not older than the `schema` header, and fails naming the stale file after N seconds.
  `rpm_debug_shm_verify.sh` and `validate_stampede3_profile.sh` pass N = 300 unless `WAIT_SHM_SECONDS` is set, because the first `fast` arrives one `sample_freq` after the first `full`.
- `HPCPERFSTATS_DEBUG_SHM_DIR` overrides the base directory; it must be an absolute path under `/dev/shm/` with no `..` components.
- Payloads contain job id, hostname and workload metrics, so treat them as sensitive on shared nodes.
  Files are mode `0600` under a `0700` directory owned by the daemon euid (`ensure_private_dir`), written as `O_EXCL|O_NOFOLLOW` `*.tmp` files and renamed.
- If the directory is removed while the daemon runs, the next sample recreates it; writes never abort the process.

`<N>` is a wait in seconds.

## Power and GPU telemetry

`host_power_socket`
: One row per socket from the ACPI power meters (`acpi_power_meter` hwmon, `power1_average` in µW converted to W): `cpu_power_w`, `socket_power_w`, `sysio_power_w`, `module_power_w`.
A missing meter emits `NOT_PRESENT` (`-2`), a failed read `READ_FAILED` (`-3`), and an unknown `<Name> Power Socket <N>` label is logged once.
`socket_power_w` already contains `cpu_power_w` and `sysio_power_w`, and `module_power_w` (GH200) contains the GPU, so do not add them together.
Consumers keep the per-socket rows (`dev` 0 and 1) and sum by `dev`; on shared nodes the type rides only the node-wide `-` payload, never a `j:` payload.
The analysis node power estimate is the sum of `module_power_w` when above 0 (GH200), else the sum of `socket_power_w` plus GPU power (GB200), or the `socket_power_w` sum alone on GPU-less Grace-Grace and Vera.

`nvidia_gpu` sources
: `power_usage` is the per-GPU draw.
On GPM-capable GPUs (Hopper and newer) every key comes from NVML and GPM: `libnvidia-ml` loads at runtime (override `HPCPERFSTATS_NVML_LIB`, allowlisted like the DCGM override).
GPM percent x 0.01 equals the DCGM ratio and GPM MiB/s x 1048576 equals DCGM PROF bytes/s (checked against DCGM under load on GB200).
NVML and GPM run no background threads, where embedded DCGM ran about 400 wakeups/s for the same fields.
GPUs without GPM, or a node without a usable `libnvidia-ml`, use the DCGM path; an NVML failure mid-run switches to DCGM for the rest of the process (logged once; DEBUG builds force one with the `nvml_fetch` fault point).
`dev` is the NVML index, which equals the DCGM GPU id order on GB200 and GH200.
The GPM window is the collect interval; a collect less than 150 ms after the last GPM sample (`NVIDIA_GPU_NVML_GPM_MIN_GAP_US`) takes no new sample and republishes the window that closed last, so GPM keys are `NOT_UPDATED` only before the first window closes.

Pipe splits
: `tensor_imma_active`, `tensor_hmma_active` and `tensor_dfma_active` come from DCGM PROF 1013, 1014 and 1015 or GPM IMMA, HMMA and DFMA; `integer_active` from DCGM 1016 or GPM metric 4; `tensor_dmma_active` (FP64 tensor MMA) from GPM metric 8 only, so it is `NOT_PRESENT` on the DCGM path.
All are duty cycles, 0-100.
IMMA is a low-precision tensor signal only, not an FP8 FLOP counter.
`tensor_dfma_active` is the FP64 FMA pipe, not FP64 tensor; it equals `fp64_active` on H100, GH200, GB200 and RTX (measured).
On the DCGM path a delivered-fields bitmask marks every field the active watch profile did not deliver `NOT_PRESENT` (`nvidia_gpu_dcgm_row_mark_unseen`), never 0.

DCGM extras
: Per-direction link counters `gpu_pcie_*_bytes` and `gpu_nvlink_*_bytes` (schema suffix `E`) are running totals the monitor builds by integrating the NVML GPM or DCGM PROF byte rate over each collect interval, not hardware counters.
`gpu_mem_free_mb`, `gpu_sm_clock`, `gpu_pcie_replay_counter` and `gpu_dram_active` appear when the active watch profile includes those fields.
`gpu_io_link_total_bytes` is a separate delta-accumulated counter built from the same PROF byte fields.

DCGM watch profiles
: `nvidia_gpu_dcgm_profiles.c` (`NVIDIA_GPU_NFIELDS` 25) falls back in order: `full-prof` (all 25 fields, including 1013-1016), `split-prof-no-dfma` (drops 1015 DFMA and 1016 integer pipe), `core-prof` (drops 1013-1016), `basic-nonprof` (power, temperature, util, framebuffer, clock, replay, throttle; no PROF field).
The last profile that worked is tried first after a re-watch, and board-power fields 1132 and 1133 are in no profile.
DCGM 3.1.x on the LS6 A100 lacks 1015 and 1016 in `dcgmi profile -l`, rejects `full-prof` and runs `split-prof-no-dfma`, so `tensor_dfma_active` and `integer_active` publish `NOT_PRESENT` there.

GPU FLOP estimate
: `gpu_flops_rate` and `gpu_flops` (`nvidia_gpu_estimate.c`) sum, over the fp64, fp32, fp16, HMMA and DMMA pipes, duty cycle x that pipe's FLOPs per SM per clock for the GPU's compute capability, x SM count x SM clock.
Per-SM peaks come from the CUDA C++ Programming Guide arithmetic-throughput table (scalar pipes) and the A100 and H100 whitepapers (HMMA at the dense FP16 tensor peak, DMMA at the FP64 tensor peak) for CC 8.0 and 9.0.
CC 12.0 (RTX PRO 6000 Blackwell) HMMA is 1024, measured with `measurement/bin/gpu_tensor_calibrate.sh` (cuBLAS FP16 GEMM against the monitor's own `tensor_hmma_active`), and its DMMA is 0 because its DGEMM does not use tensor cores.
CC 10.0 (GB200) HMMA is 8192 and DMMA 128, measured the same way on Horizon.
DFMA (same pipe as fp64), IMMA, `integer_active` and the `tensor_active` remainder not attributed to HMMA or DMMA are excluded.
An unknown CC or SM count makes both keys `NOT_PRESENT`; `gpu_flops` integrates the rate over the collect interval.

GPU detection
: `nvidia_gpu` stays enabled when `lspci` is unavailable.
When `lspci` omits vendor prose, `/proc/driver/nvidia/version`, `/dev/nvidia0` or `lspci -nn` lines matching `[10de:` with the usual GPU PCI classes still keep `nvidia_gpu` enabled before DCGM runs.
Runtime detection (`src/gpu_pci_detect.c`, `hwdetect.c`) and the configure probe (`scripts/gpu_lspci_probe.sh`) share the PCI class and vendor heuristics; `tests/test_gpu_lspci_detect_parity.sh` guards parity on fixture lines.
`HPCPERFSTATS_FORCE_NVIDIA_GPU=1` keeps `nvidia_gpu` enabled even when detection misses NVIDIA.

DCGM loading
: The monitor builds with vendored DCGM headers and loads `libdcgm` at runtime (`dlopen`) only when `nvidia_gpu` needs the DCGM path, so CPU-only and Grace-only nodes need no DCGM.
The binary links neither `libdcgm` nor `libnvidia-ml` (`readelf -d` shows neither in `NEEDED`).
GPU nodes on the DCGM path need a compatible `libdcgm.so` (override with `HPCPERFSTATS_DCGM_LIB`).
`nvidia_gpu` is the only DCGM user; `monitor_dcgm_session_acquire` and `release` stay refcounted for teardown safety.

`lshw` stub
: `libdcgm` may run `lshw -json` for serial discovery through absolute `/usr/bin/lshw`, then `/usr/sbin/lshw`, with a minimal child environment, so `PATH` cannot intercept it.
On nodes with empty-module Ethernet mlx ports (for example Horizon) that spams `mlx5_query_module_id` / `query_mcia_reg failed: status: 0x3`.
The RPM installs a no-op stub at `/usr/libexec/hpcperfstats/stubs/lshw`, and `hpcperfstats.service` bind-mounts it over both absolute paths in the unit namespace (`BindReadOnlyPaths=`); the unit also prepends the stub directory on `PATH` for bare `lshw`.
For a manual foreground run use `systemd-run` with the same binds (PATH alone still hits the real `lshw`):

```bash
S=/usr/libexec/hpcperfstats/stubs/lshw
systemd-run --wait --collect \
  -p BindReadOnlyPaths=-$S:/usr/bin/lshw \
  -p BindReadOnlyPaths=-$S:/usr/sbin/lshw \
  /usr/sbin/hpcperfstatsd <ARGS>
```

`<ARGS>` are the daemon's command-line options.
If the binds stop the unit from starting, use a drop-in with `InaccessiblePaths=-/usr/bin/lshw -/usr/sbin/lshw`.

`roofline_hw_peak` in payloads
: Emitted only when `stats_wr_hdr()` runs (`write_hdr=1`): in the same buffered payload as the `$` property banner and `!` schema lines, followed by the first `stats_buffer_collect` timestamp block (`roofline_hw_peak_collect` is gated by `stats_collect_on_changeover`).
A SIGHUP reload triggers an immediate header and sample collect, so schema changes show without waiting for the rotate timer.

`intel_x86_rapl`, `amd_x86_rapl`
: One row per socket (`dev` = `physical_package_id`), from `x86_rapl.c`.
Each domain reads its perf `power` PMU `energy-*` event (`energy-pkg`, `energy-cores`, `energy-gpu`, `energy-ram`, scaled by its `.scale`) on that socket's PMU cpumask CPU, else the socket's powercap zone `energy_uj`; both agree within 0.4% on every measured class, and nothing reads MSRs.
Intel keys are `pkg_energy`, `pp0_energy`, `pp1_energy`, `dram_energy`; AMD keys are `core_energy`, `pkg_energy` (`E,W=32,U=mJ`).
Each value is the monitor's running total in mJ since begin, modulo 2^32, so delta / delta_t is mW and delta / (1000 delta_t) is W.
A domain the node lacks emits its sentinel.

Not collected
: Chassis or PSU input (BMC), rack PDU, NIC or DPU, and NVMe device power would need separate collectors or new types (SNMP, Modbus, vendor tools, NVMe-MI).

## Related

- [Monitor daemon design](../explanation/design.md)
- [x86 counters and roofline peaks](../explanation/x86-counters-and-roofline.md)
- [Raw data format](../RAW_DATA_FORMAT.md)
- [Metrics reference](../METRICS_REFERENCE.md)
- [Legacy formats](legacy-formats.md)
  </content>
  </invoke>
