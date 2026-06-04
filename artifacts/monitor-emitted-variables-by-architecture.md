# Monitor emitted variables (by architecture and subsystem)

Inventory of variables emitted by **hpcperfstatsd** (`HPCPerfStats/monitor/`), organized by **host architecture** and **subsystem**.

**Source of truth:** `KEYS` / `SCHEMA_DEF` macros in `monitor/src/` (`stats.h`), registered in `stats_registry.c`. Consumer contract for message shape: `HPCPerfStats/hpcperfstats/listend.py`.

**Sample row format:**

```text
<timestamp> <jobid> <host> <stats_type> <device> <field1> <field2> ...
```

Schema rotation messages (`$…`) list the same field names per stats type.

---

## How architectures differ

| Architecture | CPU counter backend | Hardware PMC/RAPL/uncore | ARM memory controller |
|--------------|---------------------|--------------------------|------------------------|
| **x86_64 / i?86** | LIKWID (default) | Yes (`--enable-hardware`, default on) | No |
| **aarch64 / arm\*** | DCGM (default off x86) | No Intel/AMD uncore types | Yes (`arm_imc`) |
| **ppc64 / riscv64 / other non-x86** | DCGM | No x86 hardware types | No (unless ARM host) |

Optional types (GPU, extended IB, Lustre, OPA, MIC) depend on `./configure` flags and runtime hardware detection, not CPU family alone.

**Runtime note:** Many Intel types are always **compiled** on x86+LIKWID builds but only **emit** when CPUID/PCI/MSR probes enable that type (`st_begin`).

**Not emitted:** `osc` is implemented in `osc.c` but is **not** registered in `stats_registry.c` or `Makefile.am`.

---

## 1. Common — all architectures

Present in every normal daemon build (`stats_registry.c`).

### CPU / scheduler — `cpu` (per logical CPU)

- `user`
- `nice`
- `system`
- `idle`
- `iowait`
- `irq`
- `softirq`

### System — `ps` (device `NULL`)

- `ctxt`
- `processes`
- `load_1`
- `load_5`
- `load_15`
- `nr_running`
- `nr_threads`

### Per-process — `proc` (per PID)

- `Uid`
- `VmPeak`
- `VmSize`
- `VmLck`
- `VmHWM`
- `VmRSS`
- `VmData`
- `VmStk`
- `VmExe`
- `VmLib`
- `VmPTE`
- `VmSwap`
- `Threads`

### Memory (NUMA node) — `mem` (per node)

- `MemTotal`
- `MemFree`
- `MemUsed`
- `Active`
- `Inactive`
- `Dirty`
- `Writeback`
- `FilePages`
- `Mapped`
- `AnonPages`
- `PageTables`
- `NFS_Unstable`
- `Bounce`
- `Slab`
- `AnonHugePages`
- `HugePages_Total`
- `HugePages_Free`

### Virtual memory — `vm`

- `nr_anon_transparent_hugepages`
- `pgpgin`
- `pgpgout`
- `pswpin`
- `pswpout`
- `pgalloc_normal`
- `pgfree`
- `pgactivate`
- `pgdeactivate`
- `pgfault`
- `pgmajfault`
- `pgrefill_normal`
- `pgsteal_normal`
- `pgscan_kswapd_normal`
- `pgscan_direct_normal`
- `pginodesteal`
- `slabs_scanned`
- `kswapd_steal`
- `kswapd_inodesteal`
- `pageoutrun`
- `allocstall`
- `pgrotated`
- `thp_fault_alloc`
- `thp_fault_fallback`
- `thp_collapse_alloc`
- `thp_collapse_alloc_failed`
- `thp_split`

### NUMA — `numa` (per node)

- `numa_hit`
- `numa_miss`
- `numa_foreign`
- `interleave_hit`
- `local_node`
- `other_node`

### Block I/O — `block` (per block device)

- `rd_ios`
- `rd_merges`
- `rd_sectors`
- `rd_ticks`
- `wr_ios`
- `wr_merges`
- `wr_sectors`
- `wr_ticks`
- `in_flight`
- `io_ticks`
- `time_in_queue`

### Network — `net` (per interface)

- `collisions`
- `multicast`
- `rx_bytes`
- `rx_compressed`
- `rx_crc_errors`
- `rx_dropped`
- `rx_errors`
- `rx_fifo_errors`
- `rx_frame_errors`
- `rx_length_errors`
- `rx_missed_errors`
- `rx_over_errors`
- `rx_packets`
- `tx_aborted_errors`
- `tx_bytes`
- `tx_carrier_errors`
- `tx_compressed`
- `tx_dropped`
- `tx_errors`
- `tx_fifo_errors`
- `tx_heartbeat_errors`
- `tx_packets`
- `tx_window_errors`

### NFS — `nfs` (per mount)

| Subgroup | Variables |
|----------|-----------|
| Events | `delay` |
| Bytes | `normal_read`, `normal_write`, `direct_read`, `direct_write`, `server_read`, `server_write` |
| Transport | `xprt_bad_xids`, `xprt_req_u`, `xprt_bklog_u` |
| Per-op | `READ_ops`, `READ_timeouts`, `READ_queue`, `READ_rtt`, `WRITE_ops`, `WRITE_timeouts`, `WRITE_queue`, `WRITE_rtt` |

### VFS — `vfs`

- `dentry_use`
- `file_use`
- `inode_use`

### SysV SHM — `sysv_shm`

- `mem_used`
- `segs_used`

### tmpfs — `tmpfs`

- `bytes_used`
- `bytes_avail`
- `files_used`

### LNet — `lnet`

- `msgs_alloc`
- `msgs_alloc_max`
- `errors`
- `tx_msgs`
- `rx_msgs`
- `route_msgs`
- `rx_msgs_dropped`
- `tx_bytes`
- `rx_bytes`
- `route_bytes`
- `rx_bytes_dropped`

### InfiniBand (sysfs port) — `ib` (per IB port)

- `excessive_buffer_overrun_errors`
- `link_downed`
- `link_error_recovery`
- `local_link_integrity_errors`
- `port_rcv_constraint_errors`
- `port_rcv_data`
- `port_rcv_errors`
- `port_rcv_packets`
- `port_rcv_remote_physical_errors`
- `port_rcv_switch_relay_errors`
- `port_xmit_constraint_errors`
- `port_xmit_data`
- `port_xmit_discards`
- `port_xmit_packets`
- `port_xmit_wait`
- `symbol_error`
- `VL15_dropped`

### Roofline peaks — `roofline_hw_peak` (host-level)

- `cpu_peak_fp64_flops_per_s`
- `cpu_peak_dram_bw_bytes_per_s`
- `gpu_peak_fp64_flops_per_s`
- `gpu_peak_mem_bw_bytes_per_s`
- `gpu_peak_io_link_bw_bytes_per_s`
- `cpu_peak_source`
- `gpu_peak_source`
- `peak_calc_version`

---

## 2. x86_64 (LIKWID + hardware enabled)

Requires `--with-cpu-counter-backend=likwid` (x86 default) and `--enable-hardware`.

### CPU counters — `cpu_counter_metrics` (per CPU)

- `CPU_UTIL_TOTAL_ACCUM_US`
- `CPU_UTIL_USER_ACCUM_US`
- `CPU_UTIL_SYS_ACCUM_US`
- `CPU_UTIL_IRQ_ACCUM_US`
- `CPU_UTIL_NICE_ACCUM_US`
- `CPU_CLOCK_EST_CYCLES`
- `INSTR_RETIRED_ANY`
- `CPU_CLK_UNHALTED_CORE`
- `CPU_CLK_UNHALTED_REF`
- `MEM_LOAD_UOPS_RETIRED_L1_HIT`
- `MEM_LOAD_UOPS_RETIRED_L2_HIT`
- `MEM_LOAD_UOPS_RETIRED_LLC_HIT`
- `L1D_REPLACEMENT`
- `RETIRED_INSTRUCTIONS`
- `RETIRED_BRANCH_INSTR`
- `RETIRED_MISP_BRANCH_INSTR`
- `LS_DISPATCH`
- `FIXED_CTR0`
- `FIXED_CTR1`
- `FIXED_CTR2`
- `INST_RETIRED`
- `APERF`
- `MPERF`
- `EVENT_DRAM_CHANNEL_0`
- `EVENT_DRAM_CHANNEL_1`
- `EVENT_DRAM_CHANNEL_2`
- `EVENT_DRAM_CHANNEL_3`
- `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`
- `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`
- `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`
- `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`
- `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`
- `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`
- `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`
- `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`
- `ARM_EST_FLOPS`
- `ARM_DRAM_BW_BYTES`
- `DCGM_CPU_POWER_UTIL_W`
- `DCGM_CPU_POWER_LIMIT_W`

*Last three are schema placeholders on pure LIKWID x86; populated on DCGM paths.*

### Energy — `intel_rapl` (per socket, Intel)

- `MSR_PKG_ENERGY_STATUS`
- `MSR_PP0_ENERGY_STATUS`
- `MSR_PP1_ENERGY_STATUS`
- `MSR_DRAM_ENERGY_STATUS`

### Energy — `amd64_rapl` (per socket, AMD)

- `MSR_CORE_ENERGY_STAT`
- `MSR_PKG_ENERGY_STAT`

### AMD core PMC — `amd64_pmc` (per CPU)

- `FLOPS`
- `MERGE`
- `BRANCH_INST_RETIRED`
- `BRANCH_INST_RETIRED_MISS`
- `DISPATCH_STALL_CYCLES1`
- `DISPATCH_STALL_CYCLES0`
- `INST_RETIRED`
- `APERF`
- `MPERF`

*Family 10h with `--enable-legacy-pmcs`: subset — `FLOPS`, `MERGE`, `DISPATCH_STALL_CYCLES1`, `DISPATCH_STALL_CYCLES0` plus `INST_RETIRED`, `APERF`, `MPERF`.*

### AMD Data Fabric — `amd64_df` (per CPU; Zen 17h/19h)

- `EVENT_DRAM_CHANNEL_0`
- `EVENT_DRAM_CHANNEL_1`
- `EVENT_DRAM_CHANNEL_2`
- `EVENT_DRAM_CHANNEL_3`

### Intel core PMC — `intel_4pmc3` / `intel_8pmc3` (per CPU)

Full schema (`intel_pmc3.h` **KEYS**); active subset varies by microarchitecture:

- `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`
- `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`
- `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`
- `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`
- `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`
- `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`
- `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`
- `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`
- `MEM_UOPS_RETIRED_ALL_LOADS`
- `MEM_LOAD_UOPS_RETIRED_L1_HIT`
- `MEM_LOAD_UOPS_RETIRED_L2_HIT`
- `MEM_LOAD_UOPS_RETIRED_LLC_HIT`
- `L1D_REPLACEMENT`
- `DTLB_LOAD_MISSES_MISS_CAUSES_A_WALK`
- `RESOURCE_STALLS_ANY`
- `L2_LINES_IN_ALL`
- `MEM_UNCORE_RETIRED_REMOTE_DRAM`
- `MEM_UNCORE_RETIRED_LOCAL_DRAM`
- `FP_COMP_OPS_EXE_SSE_FP_PACKED`
- `FP_COMP_OPS_EXE_SSE_FP_SCALAR`
- `SIMD_FP_256_PACKED_DOUBLE`
- `FIXED_CTR0`
- `FIXED_CTR1`
- `FIXED_CTR2`

### Intel KNL core — `intel_knl` (per CPU)

- `MEM_UOPS_RETIRED_ALL_LOADS_KNL`
- `MEM_UOPS_RETIRED_L2_HIT_LOADS_KNL`
- `FIXED_CTR0`
- `FIXED_CTR1`
- `FIXED_CTR2`

### Intel CBO (cache box) — per core index

| Type | Variables |
|------|-----------|
| `intel_snb_cbo`, `intel_ivb_cbo` | `LLC_LOOKUP_DATA_READ`, `LLC_LOOKUP_WRITE`, `RING_IV_USED`, `COUNTER0_OCCUPANCY` |
| `intel_hsw_cbo`, `intel_bdw_cbo` | `RxR_OCCUPANCY`, `LLC_LOOKUP_DATA_READ`, `RING_IV_USED`, `LLC_LOOKUP_WRITE` |
| `intel_skx_cha` | `SF_EVICTIONS_MES`, `LLC_LOOKUP_DATA_READ_LOCAL`, `BYPASS_CHA_IMC_ALL`, `LLC_LOOKUP_WRITE` |

### Intel memory controller (PCI) — per device

| Type | Variables |
|------|-----------|
| `intel_snb_imc`, `intel_ivb_imc`, `intel_hsw_imc`, `intel_bdw_imc` | `CAS_READS`, `CAS_WRITES`, `ACT_COUNT`, `PRE_COUNT_MISS`, `FIXED_CTR` |
| `intel_skx_imc` | `CAS_READS`, `CAS_WRITES`, `ACT_COUNT`, `PRE_COUNT_MISS` |
| `intel_knl_mc` | `CAS_READS`, `CAS_WRITES`, `DCLK_CYCLES`, `UCLK_CYCLES` |
| `intel_knl_edc` | `EDC_HIT_CLEAN`, `EDC_HIT_DIRTY`, `EDC_MISS_CLEAN`, `EDC_MISS_DIRTY`, `RPQ_INSERTS`, `WPQ_INSERTS`, `ECLK_CYCLES` |

### Intel QPI — `intel_{snb,ivb,hsw,bdw}_qpi`

- `TxL_FLITS_G1_SNP`
- `TxL_FLITS_G1_HOM`
- `G1_DRS_DATA`
- `G2_NCB_DATA`

### Intel HA — `intel_{snb,ivb,hsw,bdw}_hau`

- `REQUESTS_READS`
- `REQUESTS_WRITES`
- `CLOCKTICKS`
- `IMC_WRITES`

### Intel R2PCI — `intel_{snb,ivb,hsw,bdw}_r2pci`

- `TxR_INSERTS`
- `RING_BL_USED_ALL`
- `RING_AD_USED_ALL`
- `RING_AK_USED_ALL`

### Intel PCU — `intel_pcu` (per socket; SNB–BDW)

- `FREQ_MAX_TEMP_CYCLES`
- `FREQ_MAX_POWER_CYCLES`
- `FREQ_MIN_IO_CYCLES`
- `FREQ_MIN_SNOOP_CYCLES`
- `FIXED_CTR0`
- `FIXED_CTR1`

---

## 3. aarch64 / ARM (DCGM + hardware)

Requires `--with-cpu-counter-backend=dcgm` (non-x86 default) and `--enable-hardware`.

### CPU counters — `cpu_counter_metrics` (per CPU)

Same **schema** as x86 (see §2). On ARM/Grace, typically populated:

- Util accumulators (`CPU_UTIL_*`)
- `ARM_EST_FLOPS`
- `ARM_DRAM_BW_BYTES`
- `DCGM_CPU_POWER_UTIL_W`
- `DCGM_CPU_POWER_LIMIT_W`

Many x86 PMU names remain in schema but may be zero/unused.

### ARM memory controller — `arm_imc` (per PMU device)

- `CAS_READS`
- `CAS_WRITES`

---

## 4. ppc64 / riscv64 / other non-x86 (DCGM, non-ARM)

- **Common** types (§1)
- **`cpu_counter_metrics`** with DCGM backend (same schema as §2/§3; no `arm_imc`)
- **No** LIKWID, Intel uncore, AMD PMC/RAPL/DF, or `arm_imc`
- **No** architecture-specific metric names for Power

---

## 5. Optional subsystems (any architecture, if built and enabled)

### NVIDIA GPU — `nvidia_gpu` (`--enable-gpu`)

- `gpu_util`
- `mem_util`
- `mem_total_mb`
- `mem_used_mb`
- `power_usage`
- `sysio_power_usage`
- `module_power_usage`
- `temperature`
- `fp64_active`
- `sm_active`
- `sm_occupancy`
- `fp32_active`
- `fp16_active`
- `tensor_active`
- `tensor_imma_active`
- `tensor_hmma_active`
- `clocks_event_reasons`
- `gpu_flops_rate`
- `gpu_mem_bw_bytes_rate`
- `gpu_flops`
- `gpu_mem_read_bytes`
- `gpu_mem_write_bytes`
- `gpu_mem_total_bytes`
- `gpu_io_link_total_bytes`
- `gpu_count`

### AMD GPU — `amd_gpu` (`--enable-amd-gpu`)

Same as NVIDIA except **omits**:

- `sysio_power_usage`
- `module_power_usage`
- `tensor_imma_active`
- `tensor_hmma_active`
- `gpu_io_link_total_bytes`

### InfiniBand extended — `ib_ext` (`--enable-infiniband`)

- `port_select`
- `counter_select`
- `port_xmit_data`
- `port_rcv_data`
- `port_xmit_pkts`
- `port_rcv_pkts`
- `port_unicast_xmit_pkts`
- `port_unicast_rcv_pkts`
- `port_multicast_xmit_pkts`
- `port_multicast_rcv_pkts`

### IB switch 64-bit — `ib_sw`

- `rx_bytes`
- `rx_packets`
- `tx_bytes`
- `tx_packets`

### Intel OPA — `opa` (`--enable-opa`)

- `PortXmitData`
- `PortRcvData`
- `PortXmitPkts`
- `PortRcvPkts`
- `PortMulticastXmitPkts`
- `PortMulticastRcvPkts`
- `PortXmitWait`
- `SwPortCongestion`
- `PortRcvFECN`
- `PortRcvBECN`
- `PortXmitTimeCong`
- `PortXmitWastedBW`
- `PortXmitWaitData`
- `PortRcvBubble`
- `PortMarkFECN`
- `PortErrorCounterSummary`

### Lustre — `mdc`, `llite` (`--enable-lustre`)

**`mdc`:**

- `ldlm_cancel`
- `mds_close`
- `mds_getattr`
- `mds_getattr_lock`
- `mds_getxattr`
- `mds_readpage`
- `mds_statfs`
- `mds_sync`
- `reqs`
- `wait`

**`llite`:**

- `read`
- `write`
- `read_bytes`
- `write_bytes`
- `direct_read`
- `direct_write`
- `osc_read`
- `osc_write`
- `dirty_pages_hits`
- `dirty_pages_misses`
- `ioctl`
- `open`
- `close`
- `mmap`
- `seek`
- `fsync`
- `setattr`
- `truncate`
- `flock`
- `getattr`
- `statfs`
- `alloc_inode`
- `setxattr`
- `getxattr`
- `listxattr`
- `removexattr`
- `inode_permission`
- `readdir`
- `create`
- `lookup`
- `link`
- `unlink`
- `symlink`
- `mkdir`
- `rmdir`
- `mknod`
- `rename`

### Intel MIC — `mic` (`--enable-mic`)

- `num_cores`
- `threads_core`
- `user_sum`
- `nice_sum`
- `sys_sum`
- `idle_sum`
- `jiffy_counter`

---

## Summary matrix

| Subsystem | Common | x86 LIKWID+HW | ARM DCGM+HW | ppc/riscv DCGM | Optional flags |
|-----------|--------|---------------|-------------|----------------|----------------|
| OS / proc / mem / vm / numa / block / net / nfs / vfs / shm / tmpfs / lnet / ib sysfs / roofline | ✓ | ✓ | ✓ | ✓ | — |
| `cpu_counter_metrics` | — | ✓ | ✓ | ✓ | `--enable-hardware` |
| Intel/AMD PMC, RAPL, uncore | — | ✓ | — | — | `--enable-hardware` |
| `arm_imc` | — | — | ✓ | — | ARM host + DCGM |
| GPU / IB ext / Lustre / OPA / MIC | — | if configured | if configured | if configured | respective `--enable-*` |

**Approximate scale:** ~120+ variables in common types; x86 with full hardware can add ~100+ more across Intel/AMD types (many generation-specific, only one variant active per machine); optional GPU/IB/Lustre adds ~60+.

---

## Related artifacts

- `monitor-variable-usage-gap-analysis.md` — downstream usage of emitted keys vs `hpcperfstats/`

## Code references

| Item | Path |
|------|------|
| Schema macro | `monitor/src/stats.h` |
| Type registry | `monitor/src/stats_registry.c` |
| CPU counter schema | `monitor/src/cpu_counter_metrics.h` |
| Intel PMC schema | `monitor/src/intel_pmc3.h` |
| Configure / backends | `monitor/configure.ac` |

*Generated from static analysis of monitor sources.*
