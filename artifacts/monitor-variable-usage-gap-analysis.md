# Monitor Variable Usage Gap Analysis

Static code-derived comparison between monitor-emitted schema keys in `monitor/src` and explicit quoted-key usage in `hpcperfstats/` + `tests/` (excluding `monitor/`).

*Regenerated: 2026-06-04 via `docs/regenerate_monitor_variable_usage_gap_analysis.py`.*

## 1) Total emitted variables (by type)

- `amd64_df`: emitted **8**, used **8**, unused **0**
- `amd64_pmc`: emitted **5**, used **1**, unused **4**
- `amd_gpu`: emitted **20**, used **13**, unused **7**
- `amd_x86_pmc`: emitted **13**, used **0**, unused **13**
- `amd_x86_rapl`: emitted **2**, used **0**, unused **2**
- `amd_x86_uncore_df`: emitted **4**, used **0**, unused **4**
- `host_block`: emitted **11**, used **3**, unused **8**
- `host_cpu`: emitted **7**, used **5**, unused **2**
- `host_ib`: emitted **17**, used **13**, unused **4**
- `host_ib_ext`: emitted **10**, used **4**, unused **6**
- `host_ib_sw`: emitted **4**, used **4**, unused **0**
- `host_lnet`: emitted **11**, used **3**, unused **8**
- `host_mem`: emitted **17**, used **2**, unused **15**
- `host_net`: emitted **23**, used **17**, unused **6**
- `host_nfs`: emitted **18**, used **6**, unused **12**
- `host_numa`: emitted **6**, used **3**, unused **3**
- `host_opa`: emitted **16**, used **4**, unused **12**
- `host_proc`: emitted **13**, used **0**, unused **13**
- `host_ps`: emitted **7**, used **1**, unused **6**
- `host_roofline_peak`: emitted **8**, used **5**, unused **3**
- `host_sysv_shm`: emitted **2**, used **0**, unused **2**
- `host_tmpfs`: emitted **3**, used **0**, unused **3**
- `host_vfs`: emitted **3**, used **0**, unused **3**
- `host_vm`: emitted **27**, used **0**, unused **27**
- `intel_8pmc3`: emitted **15**, used **14**, unused **1**
- `intel_hsw_imc`: emitted **2**, used **2**, unused **0**
- `intel_knl_mc_dclk`: emitted **2**, used **2**, unused **0**
- `intel_skx_imc`: emitted **4**, used **2**, unused **2**
- `intel_snb_imc`: emitted **2**, used **2**, unused **0**
- `intel_x86_pcu`: emitted **6**, used **0**, unused **6**
- `intel_x86_pmc_gpr4`: emitted **24**, used **0**, unused **24**
- `intel_x86_pmc_gpr8`: emitted **24**, used **0**, unused **24**
- `intel_x86_rapl`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_cbo_bdw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_cbo_hsw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_cbo_ivb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_cbo_snb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_cha_skx`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_hau_bdw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_hau_hsw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_hau_ivb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_hau_snb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_imc_bdw`: emitted **5**, used **0**, unused **5**
- `intel_x86_uncore_imc_hsw`: emitted **5**, used **0**, unused **5**
- `intel_x86_uncore_imc_ivb`: emitted **5**, used **0**, unused **5**
- `intel_x86_uncore_imc_skx`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_imc_snb`: emitted **5**, used **0**, unused **5**
- `intel_x86_uncore_qpi_bdw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_qpi_hsw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_qpi_ivb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_qpi_snb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_r2pci_bdw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_r2pci_hsw`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_r2pci_ivb`: emitted **4**, used **0**, unused **4**
- `intel_x86_uncore_r2pci_snb`: emitted **4**, used **0**, unused **4**
- `lustre_llite`: emitted **37**, used **28**, unused **9**
- `lustre_mdc`: emitted **10**, used **2**, unused **8**
- `lustre_osc`: emitted **10**, used **3**, unused **7**
- `nvidia_gpu`: emitted **34**, used **16**, unused **18**
- `osc`: emitted **10**, used **3**, unused **7**

**Totals**
- Total monitor types: **60**
- Total emitted variables: **531**
- Total explicitly used variables (quoted literals, global): **166**
- Total unused variables: **365**

## 2) Total used variables

- Explicitly used monitor variable keys (quoted literal match in usage scope): **127**

## 3) Exhaustive unused variables grouped by type

### `amd64_pmc` (4)
- `BRANCH_INST_RETIRED`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `BRANCH_INST_RETIRED_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `DISPATCH_STALL_CYCLES0`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `DISPATCH_STALL_CYCLES1`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `amd_gpu` (7)
- `gpu_flops_rate`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_read_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_total_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_total_mb`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_used_mb`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_util`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_write_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU

### `amd_x86_pmc` (13)
- `aperf`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `branch_inst_retired`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `branch_inst_retired_miss`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dispatch_stall_cycles0`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dispatch_stall_cycles1`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan0_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan1_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan2_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan3_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_ops_merge`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_ops_retired`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `instr_retired`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mperf`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `amd_x86_rapl` (2)
- `core_energy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pkg_energy`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `amd_x86_uncore_df` (4)
- `dram_chan0_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan1_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan2_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_chan3_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_block` (8)
- `io_ticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rd_ios`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rd_merges`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rd_ticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `time_in_queue`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `wr_ios`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `wr_merges`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `wr_ticks`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_cpu` (2)
- `iowait`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `softirq`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_ib` (4)
- `port_rcv_packets`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_packets`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_wait`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `vl15_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_ib_ext` (6)
- `counter_select`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `port_multicast_rcv_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_multicast_xmit_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_select`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_unicast_rcv_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_unicast_xmit_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network

### `host_lnet` (8)
- `msgs_alloc`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `msgs_alloc_max`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `route_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `route_msgs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_bytes_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_msgs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_msgs_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_msgs`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_mem` (15)
- `anon_huge_pages`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `anon_pages`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `bounce`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `file_pages`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `huge_pages_free`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `huge_pages_total`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `inactive`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mapped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_free`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_total`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nfs_unstable`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `page_tables`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `slab`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `writeback`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_net` (6)
- `collisions`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `multicast`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_compressed`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_compressed`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_nfs` (12)
- `delay`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `read_ops`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `read_queue`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `read_rtt`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `read_timeouts`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `write_ops`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `write_queue`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `write_rtt`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `write_timeouts`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `xprt_bad_xids`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `xprt_bklog_u`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `xprt_req_u`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_numa` (3)
- `interleave_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `local_node`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `numa_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_opa` (12)
- `port_error_counter_summary`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_mark_fecn`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_multicast_rcv_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_multicast_xmit_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_rcv_becn`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_rcv_bubble`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_rcv_fecn`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_time_cong`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_wait`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_wait_data`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_wasted_bw`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `sw_port_congestion`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_proc` (13)
- `threads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `uid`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_exe`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_hwm`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_lck`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_lib`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_peak`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_pte`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_rss`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_size`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_stk`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `vm_swap`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_ps` (6)
- `ctxt`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `load_1`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `load_15`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `load_5`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nr_running`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nr_threads`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_roofline_peak` (3)
- `cpu_peak_source`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `gpu_peak_source`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `peak_calc_version`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_sysv_shm` (2)
- `mem_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `segs_used`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_tmpfs` (3)
- `bytes_avail`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `bytes_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `files_used`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_vfs` (3)
- `dentry_use`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `file_use`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `inode_use`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `host_vm` (27)
- `allocstall`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `kswapd_inodesteal`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `kswapd_steal`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nr_anon_transparent_hugepages`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pageoutrun`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pgactivate`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgalloc_normal`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgdeactivate`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgfault`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgfree`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pginodesteal`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgmajfault`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgpgin`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgpgout`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgrefill_normal`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgrotated`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgscan_direct_normal`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgscan_kswapd_normal`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pgsteal_normal`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `pswpin`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pswpout`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `slabs_scanned`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `thp_collapse_alloc`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `thp_collapse_alloc_failed`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `thp_fault_alloc`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `thp_fault_fallback`: monitor-emitted telemetry field. **Usefulness:** Low / memory
- `thp_split`: monitor-emitted telemetry field. **Usefulness:** Low / memory

### `intel_8pmc3` (1)
- `SSE_DOUBLE_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_skx_imc` (2)
- `ACT_COUNT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `PRE_COUNT_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_pcu` (6)
- `freq_max_power_cycles`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `freq_max_temp_cycles`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `freq_min_io_cycles`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `freq_min_snoop_cycles`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pcu_ctr0`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pcu_ctr1`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_pmc_gpr4` (24)
- `aperf`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dtlb_load_misses_miss_causes_a_walk`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_128b_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_128b_packed_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_256b_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_256b_packed_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_512b_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_512b_packed_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_scalar_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_scalar_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_comp_ops_exe_sse_fp_packed`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_comp_ops_exe_sse_fp_scalar`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `instr_retired`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `l1d_replacement`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `l2_lines_in_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_load_uops_retired_l1_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_load_uops_retired_l2_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_load_uops_retired_llc_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_uncore_retired_local_dram`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_uncore_retired_remote_dram`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_uops_retired_all_loads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mperf`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `resource_stalls_any`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `simd_fp_256_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_pmc_gpr8` (24)
- `aperf`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dtlb_load_misses_miss_causes_a_walk`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_128b_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_128b_packed_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_256b_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_256b_packed_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_512b_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_512b_packed_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_scalar_double`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_arith_inst_retired_scalar_single`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_comp_ops_exe_sse_fp_packed`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `fp_comp_ops_exe_sse_fp_scalar`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `instr_retired`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `l1d_replacement`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `l2_lines_in_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_load_uops_retired_l1_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_load_uops_retired_l2_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_load_uops_retired_llc_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_uncore_retired_local_dram`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_uncore_retired_remote_dram`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mem_uops_retired_all_loads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mperf`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `resource_stalls_any`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `simd_fp_256_packed_double`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_rapl` (4)
- `dram_energy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pkg_energy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pp0_energy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `pp1_energy`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_cbo_bdw` (4)
- `llc_lookup_data_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_iv_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_r_occupancy`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_cbo_hsw` (4)
- `llc_lookup_data_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_iv_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_r_occupancy`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_cbo_ivb` (4)
- `counter0_occupancy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_data_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_iv_used`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_cbo_snb` (4)
- `counter0_occupancy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_data_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_iv_used`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_cha_skx` (4)
- `bypass_cha_imc_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_data_read_local`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `llc_lookup_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `sf_evictions_mes`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_hau_bdw` (4)
- `clockticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `imc_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_hau_hsw` (4)
- `clockticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `imc_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_hau_ivb` (4)
- `clockticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `imc_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_hau_snb` (4)
- `clockticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `imc_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `requests_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_imc_bdw` (5)
- `dram_act_count`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_fixed_ctr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_pre_count_miss`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_imc_hsw` (5)
- `dram_act_count`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_fixed_ctr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_pre_count_miss`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_imc_ivb` (5)
- `dram_act_count`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_fixed_ctr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_pre_count_miss`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_imc_skx` (4)
- `dram_act_count`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_pre_count_miss`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_imc_snb` (5)
- `dram_act_count`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_reads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_cas_writes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_fixed_ctr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dram_pre_count_miss`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_qpi_bdw` (4)
- `g1_drs_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `g2_ncb_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_hom`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_snp`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_qpi_hsw` (4)
- `g1_drs_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `g2_ncb_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_hom`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_snp`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_qpi_ivb` (4)
- `g1_drs_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `g2_ncb_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_hom`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_snp`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_qpi_snb` (4)
- `g1_drs_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `g2_ncb_data`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_hom`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_l_flits_g1_snp`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_r2pci_bdw` (4)
- `ring_ad_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_ak_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_bl_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_r_inserts`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_r2pci_hsw` (4)
- `ring_ad_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_ak_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_bl_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_r_inserts`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_r2pci_ivb` (4)
- `ring_ad_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_ak_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_bl_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_r_inserts`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_x86_uncore_r2pci_snb` (4)
- `ring_ad_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_ak_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ring_bl_used_all`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_r_inserts`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `lustre_llite` (9)
- `dirty_pages_hits`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dirty_pages_misses`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `getxattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `inode_permission`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ioctl`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `osc_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `osc_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `seek`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `write`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `lustre_mdc` (8)
- `mds_close`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_getattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_getattr_lock`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_getxattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_readpage`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_statfs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_sync`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `reqs`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `lustre_osc` (7)
- `ost_destroy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_punch`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_setattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_statfs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `reqs`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `nvidia_gpu` (18)
- `gpu_dram_active`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_flops_rate`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_free_mb`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_read_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_total_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_total_mb`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_used_mb`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_util`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_write_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_nvlink_rx_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_nvlink_tx_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_pcie_replay_counter`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_pcie_rx_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_pcie_tx_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_sm_clock`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `tensor_dfma_active`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tensor_hmma_active`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tensor_imma_active`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `osc` (7)
- `ost_destroy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_punch`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_setattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_statfs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `reqs`: monitor-emitted telemetry field. **Usefulness:** Low / other

## 4) Methodology and caveats

- Emitted keys = monitor `KEYS` / `stats_set` literals per `st_name` (post naming migration; no synthetic `CTL*`/`CTR*` or hex control tokens).
- Usage matching scans explicit quoted key literals in `hpcperfstats/` and `tests/`; dynamically generated keys or indirect mappings can be undercounted.
- Downstream still references **legacy** type/event strings until updated; section 5 lists pre-rename keys used by metrics/plots.
- This is static source analysis, not runtime coverage.
- Compile-time gated monitor drivers may exist in source but be disabled in a given deployment.
- NFS runtime-composed keys are included: `read_ops`, `read_timeouts`, `read_queue`, `read_rtt`, `write_ops`, `write_timeouts`, `write_queue`, `write_rtt`.
- `osc` is parsed from `osc.c` for completeness but is **not** registered in `stats_registry.c` (not emitted by the daemon).
- See also `artifacts/monitor-emitted-variables-by-architecture.md` for a subsystem/architecture inventory.

## 5) Strict per-type, per-variable used inventory

This section is a strict `host_data.type` inventory for variables actively wired into current metrics/plots/displays (not just grouped families). For each type, variables listed are the monitor keys consumed by active compute or display paths.

### `amd64_df`
- **Used variables:** `MBW_CHANNEL_0`, `MBW_CHANNEL_1`, `MBW_CHANNEL_2`, `MBW_CHANNEL_3`, `MBW_CHANNEL_4`, `MBW_CHANNEL_5`, `MBW_CHANNEL_6`, `MBW_CHANNEL_7`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_mbw`, `dram_bw_node_imbalance`)
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`, `dram_bw_node_imbalance`), Summary plot (`amd_mbw`), CPU roofline memory path

### `amd64_pmc`
- **Used variables:** `FLOPS`, `APERF`, `MPERF`, `INST_RETIRED`, `BRANCH_INST_RETIRED`, `BRANCH_INST_RETIRED_MISS`, `DISPATCH_STALL_CYCLES0`, `DISPATCH_STALL_CYCLES1`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, summary/roofline/heatmap plot modules
- **Figures/metrics/displays:** Job Detail Metrics (`avg_flops`, `avg_freq`), Summary plot (`amd_flops`, `amd_instr`, `amd_mcycles`, `amd_acycles`)

### `amd64_rapl`
- **Used variables:** `MSR_PKG_ENERGY_STAT`
- **Where used in code:** node power estimation helpers and metric paths
- **Figures/metrics/displays:** Job Detail Metrics (`max_node_power_est_w`, `avg_node_power_est_w`)

### `amd_gpu`
- **Used variables:** `gpu_util`, `tensor_active`, `fp16_active`, `fp32_active`, `fp64_active`, `gpu_mem_bw_bytes_rate`, `power_usage`, `clocks_event_reasons`, `gpu_count`, `mem_used_mb`, `mem_util`, `sm_occupancy`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, `hpcperfstats/analysis/metrics/gpu_job_detail_summary.py`
- **Figures/metrics/displays:** Job Detail Metrics (GPU fallback paths), `detail_gpu_*`

### `arm_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_mbw`) and roofline helpers
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`), CPU roofline memory path

### `block`
- **Used variables:** `rd_sectors`, `wr_sectors`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_blockbw`)
- **Figures/metrics/displays:** Job Detail Metrics (`avg_blockbw`)

### `cpu`
- **Used variables:** `user`, `system`, `nice`, `idle`, `irq`, `softirq`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_cpuusage`, `node_imbalance`, `time_imbalance`), summary plot
- **Figures/metrics/displays:** Job Detail Metrics (`avg_cpuusage`, `node_imbalance`, `time_imbalance`), Summary plot (`cpu`)

### `cpu_counter_metrics`
- **Used variables:** `ARM_EST_FLOPS`, `ARM_DRAM_BW_BYTES`, `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`, `SSE_DOUBLE_SCALAR`, `SSE_DOUBLE_PACKED`, `SIMD_DOUBLE_256`, `APERF`, `MPERF`, `INST_RETIRED`, `DCGM_CPU_POWER_UTIL_W`, `DCGM_CPU_POWER_LIMIT_W`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, roofline and node power estimate paths
- **Figures/metrics/displays:** Job Detail Metrics (FLOP/vector/frequency/memory/power-derived rows), CPU roofline, Summary power/frequency/counter panels

### `ib_ext`
- **Used variables:** `port_xmit_data`, `port_rcv_data`, `port_xmit_pkts`, `port_rcv_pkts`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_ibbw`, `avg_packetsize`, `max_fabricbw`, `max_packetrate`, `fabric_node_imbalance`)
- **Figures/metrics/displays:** Job Detail Metrics (fabric averages/peaks/imbalance/ratios), Summary plot (`ibbw`)

### `intel_4pmc3`
- **Used variables:** `APERF`, `MPERF`, `INST_RETIRED`, `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`, `SSE_DOUBLE_SCALAR`, `SSE_DOUBLE_PACKED`, `SIMD_DOUBLE_256`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, summary/roofline/heatmap plot modules
- **Figures/metrics/displays:** Job Detail Metrics (FLOP/vector/frequency rows), Summary plot (`flops64b`, `flops32b`, `instr`, `mcycles`, `acycles`, `freq`), CPU roofline and CPU multiprecision

### `intel_8pmc3`
- **Used variables:** `APERF`, `MPERF`, `INST_RETIRED`, `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`, `SSE_DOUBLE_SCALAR`, `SSE_DOUBLE_PACKED`, `SIMD_DOUBLE_256`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, summary/roofline/heatmap plot modules
- **Figures/metrics/displays:** Job Detail Metrics (FLOP/vector/frequency rows), Summary plot (`flops64b`, `flops32b`, `instr`, `mcycles`, `acycles`, `freq`), CPU roofline and CPU multiprecision

### `intel_bdw_imc`, `intel_hsw_imc`, `intel_ivb_imc`, `intel_snb_imc`, `intel_skx_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_mbw`, `dram_bw_node_imbalance`), roofline helpers
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`, `dram_bw_node_imbalance`), Summary (`mbw`), CPU roofline memory path

### `intel_rapl`
- **Used variables:** `MSR_PKG_ENERGY_STATUS`
- **Where used in code:** node power estimation and summary power plotting paths
- **Figures/metrics/displays:** Job Detail Metrics (`max_node_power_est_w`, `avg_node_power_est_w`), Summary (`watts`, `node_power_est_w`)

### `llite`
- **Used variables:** `read_bytes`, `write_bytes`, `open`, `close`, `mmap`, `fsync`, `setattr`, `truncate`, `flock`, `getattr`, `statfs`, `alloc_inode`, `setxattr`, `listxattr`, `removexattr`, `readdir`, `create`, `lookup`, `link`, `unlink`, `symlink`, `mkdir`, `rmdir`, `mknod`, `rename`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, `hpcperfstats/analysis/metrics/job_detail_fsio.py`, summary plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_sharedfs_bw`, `avg_sharedfs_iops`, `max_mds`, FSIO `detail_fsio_llite_*`), Summary (`lustre_read_mb_s`, `lustre_write_mb_s`, `liops`)

### `lnet`
- **Used variables:** `tx_bytes`, `rx_bytes`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`max_lnetbw`, `lnet_node_imbalance`)
- **Figures/metrics/displays:** Job Detail Metrics (`max_lnetbw`, `lnet_node_imbalance`)

### `mem`
- **Used variables:** `MemUsed`, `MemTotal`, `Slab`, `FilePages`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`mem_hwm`), summary plot memory panels
- **Figures/metrics/displays:** Job Detail Metrics (`mem_hwm`), Summary (`mem`)

### `net`
- **Used variables:** `rx_bytes`, `tx_bytes`, `rx_packets`, `tx_packets`, `rx_errors`, `tx_errors`, `rx_dropped`, `tx_dropped`, `collisions`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_ethbw`, network fallbacks), summary error-rate builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_ethbw`, fallback fabric packet/byte rates), Summary (`summary_hardware_error_rates`)

### `nfs`
- **Used variables:** `READ_ops`, `WRITE_ops`, `normal_read`, `normal_write`, `direct_read`, `direct_write`, `server_read`, `server_write`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, `hpcperfstats/analysis/metrics/job_detail_fsio.py`, summary plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_sharedfs_bw`, `avg_sharedfs_iops`, `max_mds`, FSIO `detail_fsio_nfs_*`), Summary (`nfs_read_mb_s`, `nfs_write_mb_s`, `nfs_iops`)

### `numa`
- **Used variables:** `numa_miss`, `numa_foreign`, `other_node`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`max_numa_remote_rate`), summary NUMA panel
- **Figures/metrics/displays:** Job Detail Metrics (`max_numa_remote_rate`), Summary (`numa_remote_refs`)

### `nvidia_gpu`
- **Used variables:** `gpu_util`, `tensor_active`, `fp16_active`, `fp32_active`, `fp64_active`, `gpu_mem_bw_bytes_rate`, `power_usage`, `sysio_power_usage`, `module_power_usage`, `clocks_event_reasons`, `gpu_io_link_total_bytes`, `mem_used_mb`, `mem_util`, `sm_occupancy`, `gpu_count`, `gpu_flops`, `gpu_mem_read_bytes`, `gpu_mem_write_bytes`, `gpu_mem_total_bytes`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, `hpcperfstats/analysis/metrics/gpu_job_detail_summary.py`, summary and roofline plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_gpuutil`, precision/tensor/GPU-link/GPU-power metrics, `detail_gpu_*`, GPU imbalance metrics), Summary GPU panels (`nv_*`), GPU roofline and GPU multiprecision

### `opa`
- **Used variables:** `PortXmitData`, `PortRcvData`, `PortXmitPkts`, `PortRcvPkts`, `PortXmitWait`, `SwPortCongestion`, `PortRcvFECN`, `PortRcvBECN`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (fabric metrics, congestion metrics, imbalance), summary OPA/error plots
- **Figures/metrics/displays:** Job Detail Metrics (fabric fallbacks and `max_opa_congestion_rate`), Summary (`opa_wait_cong`, `opa_ecn`, `summary_hardware_error_rates`)

### `roofline_hw_peak`
- **Used variables:** `cpu_peak_fp64_flops_per_s`, `cpu_peak_dram_bw_bytes_per_s`, `gpu_peak_fp64_flops_per_s`, `gpu_peak_mem_bw_bytes_per_s`, `gpu_peak_io_link_bw_bytes_per_s`
- **Where used in code:** `hpcperfstats/analysis/plot/roofline_peaks.py`, roofline plot builders
- **Figures/metrics/displays:** CPU/GPU roofline peak reference lines


## 6) Where used in codebase (canonical files)

- `hpcperfstats/analysis/metrics/metrics.py`: authoritative mapping from monitor keys to persisted job-level metrics (`metrics_data`) and many Job Detail table rows.
- `hpcperfstats/analysis/plot/summary_metric_descriptions.py`: canonical summary-plot metric keys and user-facing descriptions for all summary subplot columns.
- `hpcperfstats/site/frontend/src/utils/variableMetadata.js`: canonical UI tooltip mapping for monitor events, derived metrics, summary metrics, and Job Detail Bokeh plot help keys.
- `hpcperfstats/site/frontend/src/utils/jobMetricDisplayLabels.js`: short-label mapping for metrics shown in Job Detail metrics table.
- `hpcperfstats/analysis/metrics/job_detail_fsio.py`: filesystem detail aggregation rows shown on Job Detail.
- `hpcperfstats/analysis/metrics/gpu_job_detail_summary.py`: GPU aggregate summary rows (`detail_gpu_*`) shown on Job Detail.

## 7) Figure/metric/display crosswalk for used variables

### Persisted Job Detail Metrics table (`metrics_list`)

Used variables in section 5 directly feed these metric families:

- CPU/runtime: `avg_cpuusage`, `avg_freq`, `node_imbalance`, `time_imbalance`
- FLOPs/vectorization: `avg_flops`, `vecpercent_64b`, `vecpercent_32b`, `avg_vector_width_64b`, `avg_vector_width_32b`, `flops_node_imbalance`
- Memory/NUMA: `avg_mbw`, `mem_hwm`, `max_numa_remote_rate`, `dram_bw_node_imbalance`
- Network/fabric: `avg_ethbw`, `avg_ibbw`, `avg_packetsize`, `max_fabricbw`, `max_packetrate`, `max_opa_congestion_rate`, `fabric_node_imbalance`
- Filesystem: `avg_sharedfs_iops`, `avg_sharedfs_bw`, `max_mds`, `max_lnetbw`, `lnet_node_imbalance`, plus `detail_fsio_*`
- GPU: `avg_gpuutil`, `avg_tensor_active`, `avg_fp16_active`, `avg_fp32_active`, `avg_fp64_active`, `avg_gpu_mem_bw_gbps`, `max_gpu_power`, `max_gpu_link_gbps`, `max_gpu_clock_event_reasons`, `gpu_util_node_imbalance`, `tensor_node_imbalance`, plus `detail_gpu_*`
- Ratios/power: `avg_fabric_mb_per_gflops`, `avg_fabric_mb_per_avg_tensor`, `max_node_power_est_w`, `avg_node_power_est_w`

### Summary figure subplots (Bokeh summary grid)

Used variables in section 5 feed these summary keys:

- CPU/memory: `cpu`, `mem`, `numa_remote_refs`, `mbw`, `amd_mbw`
- CPU compute/counters/power: `amd_flops`, `flops64b`, `flops32b`, `instr`, `amd_instr`, `mcycles`, `acycles`, `amd_mcycles`, `amd_acycles`, `freq`, `watts`, `cha_counter_arc_sum`
- GPU: `nv_gpu_util`, `nv_mem_used_mb`, `nv_mem_util_pct`, `nv_tensor_active`, `nv_sm_occupancy`, `nv_fp16_active`, `nv_fp32_active`, `nv_gpu_mem_bw_gbs`, `nv_power_w`, `node_power_est_w`, `nv_gpu_link_gbs`
- Filesystem/network/errors: `lustre_read_mb_s`, `lustre_write_mb_s`, `liops`, `nfs_read_mb_s`, `nfs_write_mb_s`, `nfs_iops`, `ibbw`, `summary_hardware_error_rates`, `opa_wait_cong`, `opa_ecn`

### Job Detail advanced Bokeh panels

- CPU roofline: consumes CPU FLOP and DRAM bandwidth variables (`FLOPS` / `FP_ARITH_*` / `ARM_EST_FLOPS`, plus IMC/DF/ARM DRAM bandwidth keys).
- GPU roofline: consumes GPU FLOP and link-traffic variables (`fp16_active`/`fp32_active`/`fp64_active` families and `gpu_io_link_total_bytes` paths where present).
- Multiprecision mix (CPU/GPU): consumes precision-resolved FLOP/tensor activity variables.

## 8) Notes on “all used variables”

- “All used variables” here means all monitor event keys currently wired into metric compute paths, summary plot builders, and Job Detail displays in this repository’s active code.
- Legacy labels in UI metadata (for historical data compatibility) are intentionally excluded from this section unless they map to current monitor event keys.
- Regenerate this file with `docs/regenerate_monitor_variable_usage_gap_analysis.py` after monitor or analysis changes.

