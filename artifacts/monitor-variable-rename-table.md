# Monitor variable rename table

Generated 2026-06-04 from `docs/monitor_variable_rename_map.yaml`.

**Clean break:** no ingest aliases. Downstream must adopt new names.

## Downstream impact (document-only)

- `hpcperfstats/monitor_naming/canonical.py`
- `hpcperfstats/monitor_naming/legacy.py`
- `hpcperfstats/monitor_naming/resolve.py`
- `hpcperfstats/dbload/sync_timedb_parsing.py`
- `hpcperfstats/dbload/sync_timedb_parsing_legacy.py`
- `hpcperfstats/analysis/metrics/metrics.py`
- `tests/pipeline_e2e/monitor_payloads.py`
- `hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEvents.js`
- `hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEventsLegacy.js`
- `docs/MONITOR_VARIABLES.md`

## Type renames (`st_name`)

- `amd64_df` → `amd_x86_uncore_df`
- `amd64_pmc` → `amd_x86_pmc`
- `amd64_rapl` → `amd_x86_rapl`
- `arm_imc` → `arm_aarch64_imc`
- `block` → `host_block`
- `cpu` → `host_cpu`
- `cpu_counter_metrics` → `host_cpu_hw`
- `host_ib_ext` → `host_ib`
- `host_ib_sw` → `host_ib`
- `ib` → `host_ib`
- `ib_ext` → `host_ib`
- `ib_sw` → `host_ib`
- `intel_4pmc3` → `intel_x86_pmc_gpr4`
- `intel_8pmc3` → `intel_x86_pmc_gpr8`
- `intel_bdw_cbo` → `intel_x86_uncore_cbo_bdw`
- `intel_bdw_hau` → `intel_x86_uncore_hau_bdw`
- `intel_bdw_imc` → `intel_x86_uncore_imc_bdw`
- `intel_bdw_qpi` → `intel_x86_uncore_qpi_bdw`
- `intel_bdw_r2pci` → `intel_x86_uncore_r2pci_bdw`
- `intel_hsw_cbo` → `intel_x86_uncore_cbo_hsw`
- `intel_hsw_hau` → `intel_x86_uncore_hau_hsw`
- `intel_hsw_imc` → `intel_x86_uncore_imc_hsw`
- `intel_hsw_qpi` → `intel_x86_uncore_qpi_hsw`
- `intel_hsw_r2pci` → `intel_x86_uncore_r2pci_hsw`
- `intel_ivb_cbo` → `intel_x86_uncore_cbo_ivb`
- `intel_ivb_hau` → `intel_x86_uncore_hau_ivb`
- `intel_ivb_imc` → `intel_x86_uncore_imc_ivb`
- `intel_ivb_qpi` → `intel_x86_uncore_qpi_ivb`
- `intel_ivb_r2pci` → `intel_x86_uncore_r2pci_ivb`
- `intel_pcu` → `intel_x86_pcu`
- `intel_rapl` → `intel_x86_rapl`
- `intel_skx_cha` → `intel_x86_uncore_cha_skx`
- `intel_skx_imc` → `intel_x86_uncore_imc_skx`
- `intel_snb_cbo` → `intel_x86_uncore_cbo_snb`
- `intel_snb_hau` → `intel_x86_uncore_hau_snb`
- `intel_snb_imc` → `intel_x86_uncore_imc_snb`
- `intel_snb_qpi` → `intel_x86_uncore_qpi_snb`
- `intel_snb_r2pci` → `intel_x86_uncore_r2pci_snb`
- `llite` → `lustre_llite`
- `lnet` → `host_lnet`
- `mdc` → `lustre_mdc`
- `mem` → `host_mem`
- `net` → `host_net`
- `nfs` → `host_nfs`
- `numa` → `host_numa`
- `opa` → `host_opa`
- `osc` → `lustre_osc`
- `proc` → `host_proc`
- `ps` → `host_ps`
- `roofline_hw_peak` → `host_roofline_peak`
- `sysv_shm` → `host_sysv_shm`
- `tmpfs` → `host_tmpfs`
- `vfs` → `host_vfs`
- `vm` → `host_vm`

## Event renames (global)

- `ACT_COUNT` → `dram_act_count`
- `APERF` → `aperf`
- `ARM_EST_FLOPS` → `arm_est_flops`
- `Active` → `active`
- `AnonHugePages` → `anon_huge_pages`
- `AnonPages` → `anon_pages`
- `BRANCH_INST_RETIRED` → `branch_inst_retired`
- `BRANCH_INST_RETIRED_MISS` → `branch_inst_retired_miss`
- `Bounce` → `bounce`
- `CAS_READS` → `dram_cas_reads`
- `CAS_WRITES` → `dram_cas_writes`
- `CPU_CLK_UNHALTED_CORE` → `cycles_unhalted_core`
- `CPU_CLK_UNHALTED_REF` → `cycles_unhalted_ref`
- `DCGM_CPU_POWER_LIMIT_W` → `dcgm_cpu_power_limit_w`
- `DCGM_CPU_POWER_UTIL_W` → `dcgm_cpu_power_util_w`
- `DISPATCH_STALL_CYCLES0` → `dispatch_stall_cycles0`
- `DISPATCH_STALL_CYCLES1` → `dispatch_stall_cycles1`
- `Dirty` → `dirty`
- `EVENT_DRAM_CHANNEL_0` → `dram_chan0_bytes`
- `EVENT_DRAM_CHANNEL_1` → `dram_chan1_bytes`
- `EVENT_DRAM_CHANNEL_2` → `dram_chan2_bytes`
- `EVENT_DRAM_CHANNEL_3` → `dram_chan3_bytes`
- `FIXED_CTR` → `dram_fixed_ctr`
- `FIXED_CTR0` → `instr_retired`
- `FIXED_CTR1` → `aperf`
- `FIXED_CTR2` → `mperf`
- `FLOPS` → `fp_ops_retired`
- `FilePages` → `file_pages`
- `HugePages_Free` → `huge_pages_free`
- `HugePages_Total` → `huge_pages_total`
- `INSTR_RETIRED_ANY` → `instr_retired_any`
- `INST_RETIRED` → `instr_retired`
- `Inactive` → `inactive`
- `MERGE` → `fp_ops_merge`
- `MPERF` → `mperf`
- `MSR_CORE_ENERGY_STAT` → `core_energy`
- `MSR_DRAM_ENERGY_STATUS` → `dram_energy`
- `MSR_PKG_ENERGY_STAT` → `pkg_energy`
- `MSR_PKG_ENERGY_STATUS` → `pkg_energy`
- `MSR_PP0_ENERGY_STATUS` → `pp0_energy`
- `MSR_PP1_ENERGY_STATUS` → `pp1_energy`
- `Mapped` → `mapped`
- `MemFree` → `mem_free`
- `MemTotal` → `mem_total`
- `MemUsed` → `mem_used`
- `NFS_Unstable` → `nfs_unstable`
- `PRE_COUNT_MISS` → `dram_pre_count_miss`
- `PageTables` → `page_tables`
- `READ_ops` → `read_ops`
- `READ_queue` → `read_queue`
- `READ_rtt` → `read_rtt`
- `READ_timeouts` → `read_timeouts`
- `RETIRED_BRANCH_INSTR` → `retired_branch_instr`
- `RETIRED_INSTRUCTIONS` → `retired_instructions`
- `RETIRED_MISP_BRANCH_INSTR` → `retired_misp_branch_instr`
- `Slab` → `slab`
- `Threads` → `threads`
- `Uid` → `uid`
- `VmData` → `vm_data`
- `VmExe` → `vm_exe`
- `VmHWM` → `vm_hwm`
- `VmLck` → `vm_lck`
- `VmLib` → `vm_lib`
- `VmPTE` → `vm_pte`
- `VmPeak` → `vm_peak`
- `VmRSS` → `vm_rss`
- `VmSize` → `vm_size`
- `VmStk` → `vm_stk`
- `VmSwap` → `vm_swap`
- `WRITE_ops` → `write_ops`
- `WRITE_queue` → `write_queue`
- `WRITE_rtt` → `write_rtt`
- `WRITE_timeouts` → `write_timeouts`
- `Writeback` → `writeback`
- `dram_active` → `gpu_dram_active`
- `fb_free` → `gpu_mem_free_mb`
- `mem_total_mb` → `gpu_mem_total_mb`
- `mem_used_mb` → `gpu_mem_used_mb`
- `mem_util` → `gpu_mem_util`
- `nvlink_rx_bytes` → `gpu_nvlink_rx_bytes`
- `nvlink_tx_bytes` → `gpu_nvlink_tx_bytes`
- `pcie_replay_counter` → `gpu_pcie_replay_counter`
- `pcie_rx_bytes` → `gpu_pcie_rx_bytes`
- `pcie_tx_bytes` → `gpu_pcie_tx_bytes`
- `sm_clock` → `gpu_sm_clock`

## Host mem aliases (kernel → emit)

- `Active` → `active`
- `AnonHugePages` → `anon_huge_pages`
- `AnonPages` → `anon_pages`
- `Bounce` → `bounce`
- `Dirty` → `dirty`
- `FilePages` → `file_pages`
- `HugePages_Free` → `huge_pages_free`
- `HugePages_Total` → `huge_pages_total`
- `Inactive` → `inactive`
- `Mapped` → `mapped`
- `MemFree` → `mem_free`
- `MemTotal` → `mem_total`
- `MemUsed` → `mem_used`
- `NFS_Unstable` → `nfs_unstable`
- `PageTables` → `page_tables`
- `Slab` → `slab`
- `Writeback` → `writeback`

## Host proc aliases (kernel → emit)

- `Threads` → `threads`
- `Uid` → `uid`
- `VL15_dropped` → `vl15_dropped`
- `VmData` → `vm_data`
- `VmExe` → `vm_exe`
- `VmHWM` → `vm_hwm`
- `VmLck` → `vm_lck`
- `VmLib` → `vm_lib`
- `VmPTE` → `vm_pte`
- `VmPeak` → `vm_peak`
- `VmRSS` → `vm_rss`
- `VmSize` → `vm_size`
- `VmStk` → `vm_stk`
- `VmSwap` → `vm_swap`

## Removed legacy symbols (never re-emit)

- `host_ib_ext`
- `host_ib_sw`
- `host_mic`
- `intel_knl`
- `intel_knl_edc`
- `intel_knl_mc`
- `intel_x86_pmc_knl`
- `intel_x86_uncore_edc_knl`
- `intel_x86_uncore_mc_knl`
- `CTL0`
- `CTL1`
- `CTL2`
- `CTL3`
- `CTL4`
- `CTL5`
- `CTL6`
- `CTL7`
- `CTR0`
- `CTR1`
- `CTR2`
- `CTR3`
- `CTR4`
- `CTR5`
- `CTR6`
- `CTR7`
- `FIXED_CTR`
- `FIXED_CTR0`
- `FIXED_CTR1`
- `FIXED_CTR2`

## Semantic replacements for removed register-shaped keys

- `FIXED_CTR0` → `instr_retired`
- `FIXED_CTR1` → `aperf`
- `FIXED_CTR2` → `mperf`
- `CTLn`/`CTRn` pairs → logical PMU names at emit time (decode path retired for new archives)

