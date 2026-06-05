# Monitor emitted variables (by architecture and subsystem)

Inventory of variables emitted by **hpcperfstatsd** (`HPCPerfStats/monitor/`), organized by **host architecture** and **subsystem**.

**Source of truth:** `KEYS` / `SCHEMA_DEF` macros in `monitor/src/` (`stats.h`), registered in `stats_registry.c`. Naming rules: `HPCPerfStats/docs/MONITOR_NAMING_SCHEME.md`.

Generated: 2026-06-04 (`docs/regenerate_monitor_emitted_variables_by_architecture.py`).

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
| **aarch64 / arm\*** | DCGM (default off x86) | No Intel/AMD uncore types | Yes (`arm_aarch64_imc`) |
| **ppc64 / riscv64 / other non-x86** | DCGM | No x86 hardware types | No (unless ARM host) |

Optional types (GPU, extended IB, Lustre, OPA, MIC) depend on `./configure` flags and runtime hardware detection, not CPU family alone.

**Runtime note:** Many Intel types are always **compiled** on x86+LIKWID builds but only **emit** when CPUID/PCI/MSR probes enable that type (`st_begin`).

**Not emitted:** `lustre_osc` is implemented in `osc.c` but is **not** registered in `stats_registry.c` or `Makefile.am`.

---

## 1. Common — all architectures

Present in every normal daemon build (`stats_registry.c`).

### CPU / scheduler — `host_cpu` (per logical CPU)

- `idle`
- `iowait`
- `irq`
- `nice`
- `softirq`
- `system`
- `user`

### System — `host_ps` (device `NULL`)

- `ctxt`
- `load_1`
- `load_15`
- `load_5`
- `nr_running`
- `nr_threads`
- `processes`

### Per-process — `host_proc` (per PID)

- `threads`
- `uid`
- `vm_data`
- `vm_exe`
- `vm_hwm`
- `vm_lck`
- `vm_lib`
- `vm_peak`
- `vm_pte`
- `vm_rss`
- `vm_size`
- `vm_stk`
- `vm_swap`

### Memory (NUMA node) — `host_mem` (per node)

- `active`
- `anon_huge_pages`
- `anon_pages`
- `bounce`
- `dirty`
- `file_pages`
- `huge_pages_free`
- `huge_pages_total`
- `inactive`
- `mapped`
- `mem_free`
- `mem_total`
- `mem_used`
- `nfs_unstable`
- `page_tables`
- `slab`
- `writeback`

### Virtual memory — `host_vm`

- `allocstall`
- `kswapd_inodesteal`
- `kswapd_steal`
- `nr_anon_transparent_hugepages`
- `pageoutrun`
- `pgactivate`
- `pgalloc_normal`
- `pgdeactivate`
- `pgfault`
- `pgfree`
- `pginodesteal`
- `pgmajfault`
- `pgpgin`
- `pgpgout`
- `pgrefill_normal`
- `pgrotated`
- `pgscan_direct_normal`
- `pgscan_kswapd_normal`
- `pgsteal_normal`
- `pswpin`
- `pswpout`
- `slabs_scanned`
- `thp_collapse_alloc`
- `thp_collapse_alloc_failed`
- `thp_fault_alloc`
- `thp_fault_fallback`
- `thp_split`

### NUMA — `host_numa` (per node)

- `interleave_hit`
- `local_node`
- `numa_foreign`
- `numa_hit`
- `numa_miss`
- `other_node`

### Block I/O — `host_block` (per block device)

- `in_flight`
- `io_ticks`
- `rd_ios`
- `rd_merges`
- `rd_sectors`
- `rd_ticks`
- `time_in_queue`
- `wr_ios`
- `wr_merges`
- `wr_sectors`
- `wr_ticks`

### Network — `host_net` (per interface)

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

### NFS — `host_nfs` (per mount)

- `delay`
- `direct_read`
- `direct_write`
- `normal_read`
- `normal_write`
- `read_ops`
- `read_queue`
- `read_rtt`
- `read_timeouts`
- `server_read`
- `server_write`
- `write_ops`
- `write_queue`
- `write_rtt`
- `write_timeouts`
- `xprt_bad_xids`
- `xprt_bklog_u`
- `xprt_req_u`

### VFS — `host_vfs`

- `dentry_use`
- `file_use`
- `inode_use`

### SysV SHM — `host_sysv_shm`

- `mem_used`
- `segs_used`

### tmpfs — `host_tmpfs`

- `bytes_avail`
- `bytes_used`
- `files_used`

### LNet — `host_lnet`

- `errors`
- `msgs_alloc`
- `msgs_alloc_max`
- `route_bytes`
- `route_msgs`
- `rx_bytes`
- `rx_bytes_dropped`
- `rx_msgs`
- `rx_msgs_dropped`
- `tx_bytes`
- `tx_msgs`

### InfiniBand — `host_ib` (per IB port; sysfs + MAD + switch counters)

- `counter_select`
- `excessive_buffer_overrun_errors`
- `link_downed`
- `link_error_recovery`
- `local_link_integrity_errors`
- `port_multicast_rcv_pkts`
- `port_multicast_xmit_pkts`
- `port_rcv_constraint_errors`
- `port_rcv_data`
- `port_rcv_errors`
- `port_rcv_packets`
- `port_rcv_pkts`
- `port_rcv_remote_physical_errors`
- `port_rcv_switch_relay_errors`
- `port_select`
- `port_unicast_rcv_pkts`
- `port_unicast_xmit_pkts`
- `port_xmit_constraint_errors`
- `port_xmit_data`
- `port_xmit_discards`
- `port_xmit_packets`
- `port_xmit_pkts`
- `port_xmit_wait`
- `sw_rx_bytes`
- `sw_rx_packets`
- `sw_tx_bytes`
- `sw_tx_packets`
- `symbol_error`

### Roofline peaks — `host_roofline_peak` (host-level)

- `cpu_peak_dram_bw_bytes_per_s`
- `cpu_peak_fp64_flops_per_s`
- `cpu_peak_source`
- `gpu_peak_fp64_flops_per_s`
- `gpu_peak_io_link_bw_bytes_per_s`
- `gpu_peak_mem_bw_bytes_per_s`
- `gpu_peak_source`
- `peak_calc_version`

---

## 2. x86_64 (LIKWID + hardware enabled)

Requires `--with-cpu-counter-backend=likwid` (x86 default) and `--enable-hardware`.

### Energy — `intel_x86_rapl` (per socket, Intel)

- `dram_energy`
- `pkg_energy`
- `pp0_energy`
- `pp1_energy`

### Energy — `amd_x86_rapl` (per socket, AMD)

- `core_energy`
- `pkg_energy`

### AMD core PMC — `amd_x86_pmc` (per CPU)

- `aperf`
- `branch_inst_retired`
- `branch_inst_retired_miss`
- `dispatch_stall_cycles0`
- `dispatch_stall_cycles1`
- `dram_chan0_bytes`
- `dram_chan1_bytes`
- `dram_chan2_bytes`
- `dram_chan3_bytes`
- `fp_ops_merge`
- `fp_ops_retired`
- `instr_retired`
- `mperf`

### AMD Data Fabric — `amd_x86_uncore_df` (per CPU; Zen 17h/19h)

- `dram_chan0_bytes`
- `dram_chan1_bytes`
- `dram_chan2_bytes`
- `dram_chan3_bytes`

### Intel core PMC (8 GPR) — `intel_x86_pmc_gpr8` (per CPU)

*Full schema in `intel_pmc3.h`.*

- `aperf`
- `dtlb_load_misses_miss_causes_a_walk`
- `fp_arith_inst_retired_128b_packed_double`
- `fp_arith_inst_retired_128b_packed_single`
- `fp_arith_inst_retired_256b_packed_double`
- `fp_arith_inst_retired_256b_packed_single`
- `fp_arith_inst_retired_512b_packed_double`
- `fp_arith_inst_retired_512b_packed_single`
- `fp_arith_inst_retired_scalar_double`
- `fp_arith_inst_retired_scalar_single`
- `fp_comp_ops_exe_sse_fp_packed`
- `fp_comp_ops_exe_sse_fp_scalar`
- `instr_retired`
- `l1d_replacement`
- `l2_lines_in_all`
- `mem_load_uops_retired_l1_hit`
- `mem_load_uops_retired_l2_hit`
- `mem_load_uops_retired_llc_hit`
- `mem_uncore_retired_local_dram`
- `mem_uncore_retired_remote_dram`
- `mem_uops_retired_all_loads`
- `mperf`
- `resource_stalls_any`
- `simd_fp_256_packed_double`

### Intel CBO SNB/IVB — `intel_x86_uncore_cbo_snb` (per core index)

*Same keys as `intel_x86_uncore_cbo_ivb`.*

- `counter0_occupancy`
- `llc_lookup_data_read`
- `llc_lookup_write`
- `ring_iv_used`

### Intel CBO SNB/IVB — `intel_x86_uncore_cbo_ivb` (per core index)

- `counter0_occupancy`
- `llc_lookup_data_read`
- `llc_lookup_write`
- `ring_iv_used`

### Intel CBO HSW/BDW — `intel_x86_uncore_cbo_hsw` (per core index)

*Same keys as `intel_x86_uncore_cbo_bdw`.*

- `llc_lookup_data_read`
- `llc_lookup_write`
- `ring_iv_used`
- `rx_r_occupancy`

### Intel CBO HSW/BDW — `intel_x86_uncore_cbo_bdw` (per core index)

- `llc_lookup_data_read`
- `llc_lookup_write`
- `ring_iv_used`
- `rx_r_occupancy`

### Intel CHA SKX — `intel_x86_uncore_cha_skx` (per core index)

- `bypass_cha_imc_all`
- `llc_lookup_data_read_local`
- `llc_lookup_write`
- `sf_evictions_mes`

### Intel IMC SNB — `intel_x86_uncore_imc_snb` (per PCI device)

*Same keys as IVB/HSW/BDW IMC variants.*

- `dram_act_count`
- `dram_cas_reads`
- `dram_cas_writes`
- `dram_fixed_ctr`
- `dram_pre_count_miss`

### Intel IMC IVB — `intel_x86_uncore_imc_ivb` (per PCI device)

- `dram_act_count`
- `dram_cas_reads`
- `dram_cas_writes`
- `dram_fixed_ctr`
- `dram_pre_count_miss`

### Intel IMC HSW — `intel_x86_uncore_imc_hsw` (per PCI device)

- `dram_act_count`
- `dram_cas_reads`
- `dram_cas_writes`
- `dram_fixed_ctr`
- `dram_pre_count_miss`

### Intel IMC BDW — `intel_x86_uncore_imc_bdw` (per PCI device)

- `dram_act_count`
- `dram_cas_reads`
- `dram_cas_writes`
- `dram_fixed_ctr`
- `dram_pre_count_miss`

### Intel IMC SKX — `intel_x86_uncore_imc_skx` (per PCI device)

- `dram_act_count`
- `dram_cas_reads`
- `dram_cas_writes`
- `dram_pre_count_miss`

### Intel QPI SNB — `intel_x86_uncore_qpi_snb`

*Same keys across SNB/IVB/HSW/BDW QPI types.*

- `g1_drs_data`
- `g2_ncb_data`
- `tx_l_flits_g1_hom`
- `tx_l_flits_g1_snp`

### Intel QPI IVB — `intel_x86_uncore_qpi_ivb`

- `g1_drs_data`
- `g2_ncb_data`
- `tx_l_flits_g1_hom`
- `tx_l_flits_g1_snp`

### Intel QPI HSW — `intel_x86_uncore_qpi_hsw`

- `g1_drs_data`
- `g2_ncb_data`
- `tx_l_flits_g1_hom`
- `tx_l_flits_g1_snp`

### Intel QPI BDW — `intel_x86_uncore_qpi_bdw`

- `g1_drs_data`
- `g2_ncb_data`
- `tx_l_flits_g1_hom`
- `tx_l_flits_g1_snp`

### Intel HA SNB — `intel_x86_uncore_hau_snb`

*Same keys across SNB/IVB/HSW/BDW HA types.*

- `clockticks`
- `imc_writes`
- `requests_reads`
- `requests_writes`

### Intel HA IVB — `intel_x86_uncore_hau_ivb`

- `clockticks`
- `imc_writes`
- `requests_reads`
- `requests_writes`

### Intel HA HSW — `intel_x86_uncore_hau_hsw`

- `clockticks`
- `imc_writes`
- `requests_reads`
- `requests_writes`

### Intel HA BDW — `intel_x86_uncore_hau_bdw`

- `clockticks`
- `imc_writes`
- `requests_reads`
- `requests_writes`

### Intel R2PCI SNB — `intel_x86_uncore_r2pci_snb`

*Same keys across SNB/IVB/HSW/BDW R2PCI types.*

- `ring_ad_used_all`
- `ring_ak_used_all`
- `ring_bl_used_all`
- `tx_r_inserts`

### Intel R2PCI IVB — `intel_x86_uncore_r2pci_ivb`

- `ring_ad_used_all`
- `ring_ak_used_all`
- `ring_bl_used_all`
- `tx_r_inserts`

### Intel R2PCI HSW — `intel_x86_uncore_r2pci_hsw`

- `ring_ad_used_all`
- `ring_ak_used_all`
- `ring_bl_used_all`
- `tx_r_inserts`

### Intel R2PCI BDW — `intel_x86_uncore_r2pci_bdw`

- `ring_ad_used_all`
- `ring_ak_used_all`
- `ring_bl_used_all`
- `tx_r_inserts`

### Intel PCU — `intel_x86_pcu` (per socket; SNB–BDW)

*Uses `pcu_ctr0`/`pcu_ctr1` for fixed PCU counters.*

- `freq_max_power_cycles`
- `freq_max_temp_cycles`
- `freq_min_io_cycles`
- `freq_min_snoop_cycles`
- `pcu_ctr0`
- `pcu_ctr1`

---

## 3. aarch64 / ARM (DCGM + hardware)

Requires `--with-cpu-counter-backend=dcgm` (non-x86 default) and `--enable-hardware`.

---

## 4. ppc64 / riscv64 / other non-x86 (DCGM, non-ARM)

- **Common** types (§1)
- **`host_cpu_hw`** with DCGM backend (same schema as §2/§3; no `arm_aarch64_imc` on non-ARM)
- **No** LIKWID, Intel uncore, AMD PMC/RAPL/DF, or `arm_aarch64_imc`
- **No** architecture-specific metric names for Power

---

## 5. Optional subsystems (any architecture, if built and enabled)

### NVIDIA GPU — `nvidia_gpu` (`--enable-gpu`)

- `clocks_event_reasons`
- `fp16_active`
- `fp32_active`
- `fp64_active`
- `gpu_count`
- `gpu_dram_active`
- `gpu_flops`
- `gpu_flops_rate`
- `gpu_io_link_total_bytes`
- `gpu_mem_bw_bytes_rate`
- `gpu_mem_free_mb`
- `gpu_mem_read_bytes`
- `gpu_mem_total_bytes`
- `gpu_mem_total_mb`
- `gpu_mem_used_mb`
- `gpu_mem_util`
- `gpu_mem_write_bytes`
- `gpu_nvlink_rx_bytes`
- `gpu_nvlink_tx_bytes`
- `gpu_pcie_replay_counter`
- `gpu_pcie_rx_bytes`
- `gpu_pcie_tx_bytes`
- `gpu_sm_clock`
- `gpu_util`
- `module_power_usage`
- `power_usage`
- `sm_active`
- `sm_occupancy`
- `sysio_power_usage`
- `temperature`
- `tensor_active`
- `tensor_dfma_active`
- `tensor_hmma_active`
- `tensor_imma_active`

### AMD GPU — `amd_gpu` (`--enable-amd-gpu`)

*Omits some NVIDIA-only keys.*

- `clocks_event_reasons`
- `fp16_active`
- `fp32_active`
- `fp64_active`
- `gpu_count`
- `gpu_flops`
- `gpu_flops_rate`
- `gpu_mem_bw_bytes_rate`
- `gpu_mem_read_bytes`
- `gpu_mem_total_bytes`
- `gpu_mem_total_mb`
- `gpu_mem_used_mb`
- `gpu_mem_util`
- `gpu_mem_write_bytes`
- `gpu_util`
- `power_usage`
- `sm_active`
- `sm_occupancy`
- `temperature`
- `tensor_active`

### Intel OPA — `host_opa` (`--enable-opa`)

- `port_error_counter_summary`
- `port_mark_fecn`
- `port_multicast_rcv_pkts`
- `port_multicast_xmit_pkts`
- `port_rcv_becn`
- `port_rcv_bubble`
- `port_rcv_data`
- `port_rcv_fecn`
- `port_rcv_pkts`
- `port_xmit_data`
- `port_xmit_pkts`
- `port_xmit_time_cong`
- `port_xmit_wait`
- `port_xmit_wait_data`
- `port_xmit_wasted_bw`
- `sw_port_congestion`

### Lustre MDC — `lustre_mdc` (`--enable-lustre`)

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

### Lustre llite — `lustre_llite` (`--enable-lustre`)

- `alloc_inode`
- `close`
- `create`
- `direct_read`
- `direct_write`
- `dirty_pages_hits`
- `dirty_pages_misses`
- `flock`
- `fsync`
- `getattr`
- `getxattr`
- `inode_permission`
- `ioctl`
- `link`
- `listxattr`
- `lookup`
- `mkdir`
- `mknod`
- `mmap`
- `open`
- `osc_read`
- `osc_write`
- `read`
- `read_bytes`
- `readdir`
- `removexattr`
- `rename`
- `rmdir`
- `seek`
- `setattr`
- `setxattr`
- `statfs`
- `symlink`
- `truncate`
- `unlink`
- `write`
- `write_bytes`

---

## Summary matrix

| Subsystem | Common | x86 LIKWID+HW | ARM DCGM+HW | ppc/riscv DCGM | Optional flags |
|-----------|--------|---------------|-------------|----------------|----------------|
| OS / proc / mem / vm / numa / block / net / nfs / vfs / shm / tmpfs / lnet / ib sysfs / roofline | ✓ | ✓ | ✓ | ✓ | — |
| `host_cpu_hw` | — | ✓ | ✓ | ✓ | `--enable-hardware` |
| Intel/AMD PMC, RAPL, uncore | — | ✓ | — | — | `--enable-hardware` |
| `arm_aarch64_imc` | — | — | ✓ | — | ARM host + DCGM |
| GPU / IB ext / Lustre / OPA / MIC | — | if configured | if configured | if configured | respective `--enable-*` |

**Approximate scale:** ~184 keys in common types; x86 hardware adds ~145 more across Intel/AMD types (generation-specific; one variant active per machine); optional subsystems add ~117 more.

---

## Related artifacts

- `monitor-variable-rename-table.md` — old→new migration table
- `monitor-variable-usage-gap-analysis.md` — downstream usage of emitted keys vs `hpcperfstats/`

## Code references

- `HPCPerfStats/monitor/src/stats_registry.c` — registered types
- `HPCPerfStats/monitor/scripts/check_emitted_variable_names.py` — naming lint
- `HPCPerfStats/hpcperfstats/listend.py` — consumer message contract

