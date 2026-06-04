# Monitor Variable Usage Gap Analysis

Static code-derived comparison between monitor-emitted schema keys in `monitor/src` and explicit quoted-key usage in `hpcperfstats/` + `tests/` (excluding `monitor/`).

*Regenerated: 2026-05-22 via `docs/regenerate_monitor_variable_usage_gap_analysis.py`.*

## 1) Total emitted variables (by type)

- `amd64_df`: emitted **18**, used **8**, unused **10**
- `amd64_pmc`: emitted **23**, used **4**, unused **19**
- `amd64_rapl`: emitted **2**, used **1**, unused **1**
- `amd_gpu`: emitted **20**, used **16**, unused **4**
- `arm_imc`: emitted **2**, used **2**, unused **0**
- `block`: emitted **11**, used **3**, unused **8**
- `cpu`: emitted **7**, used **5**, unused **2**
- `cpu_counter_metrics`: emitted **53**, used **18**, unused **35**
- `ib`: emitted **17**, used **13**, unused **4**
- `ib_ext`: emitted **10**, used **4**, unused **6**
- `ib_sw`: emitted **4**, used **4**, unused **0**
- `intel_4pmc3`: emitted **39**, used **18**, unused **21**
- `intel_8pmc3`: emitted **47**, used **18**, unused **29**
- `intel_bdw_cbo`: emitted **10**, used **1**, unused **9**
- `intel_bdw_hau`: emitted **10**, used **0**, unused **10**
- `intel_bdw_imc`: emitted **12**, used **3**, unused **9**
- `intel_bdw_qpi`: emitted **10**, used **0**, unused **10**
- `intel_bdw_r2pci`: emitted **10**, used **0**, unused **10**
- `intel_hsw_cbo`: emitted **10**, used **1**, unused **9**
- `intel_hsw_hau`: emitted **10**, used **0**, unused **10**
- `intel_hsw_imc`: emitted **12**, used **3**, unused **9**
- `intel_hsw_qpi`: emitted **10**, used **0**, unused **10**
- `intel_hsw_r2pci`: emitted **10**, used **0**, unused **10**
- `intel_ivb_cbo`: emitted **10**, used **1**, unused **9**
- `intel_ivb_hau`: emitted **10**, used **0**, unused **10**
- `intel_ivb_imc`: emitted **12**, used **3**, unused **9**
- `intel_ivb_qpi`: emitted **10**, used **0**, unused **10**
- `intel_ivb_r2pci`: emitted **10**, used **0**, unused **10**
- `intel_knl`: emitted **7**, used **3**, unused **4**
- `intel_knl_edc`: emitted **3**, used **0**, unused **3**
- `intel_knl_mc`: emitted **2**, used **2**, unused **0**
- `intel_knl_mc_dclk`: emitted **2**, used **2**, unused **0**
- `intel_pcu`: emitted **6**, used **2**, unused **4**
- `intel_rapl`: emitted **4**, used **1**, unused **3**
- `intel_skx_cha`: emitted **10**, used **1**, unused **9**
- `intel_skx_imc`: emitted **4**, used **2**, unused **2**
- `intel_snb_cbo`: emitted **10**, used **1**, unused **9**
- `intel_snb_hau`: emitted **10**, used **0**, unused **10**
- `intel_snb_imc`: emitted **12**, used **3**, unused **9**
- `intel_snb_qpi`: emitted **10**, used **0**, unused **10**
- `intel_snb_r2pci`: emitted **10**, used **0**, unused **10**
- `llite`: emitted **37**, used **27**, unused **10**
- `lnet`: emitted **11**, used **2**, unused **9**
- `mdc`: emitted **10**, used **1**, unused **9**
- `mem`: emitted **17**, used **5**, unused **12**
- `mic`: emitted **7**, used **0**, unused **7**
- `net`: emitted **23**, used **17**, unused **6**
- `nfs`: emitted **18**, used **8**, unused **10**
- `numa`: emitted **6**, used **3**, unused **3**
- `nvidia_gpu`: emitted **25**, used **19**, unused **6**
- `opa`: emitted **16**, used **9**, unused **7**
- `osc`: emitted **10**, used **3**, unused **7**
- `proc`: emitted **13**, used **0**, unused **13**
- `ps`: emitted **7**, used **1**, unused **6**
- `roofline_hw_peak`: emitted **8**, used **5**, unused **3**
- `sysv_shm`: emitted **2**, used **0**, unused **2**
- `tmpfs`: emitted **3**, used **0**, unused **3**
- `vfs`: emitted **3**, used **0**, unused **3**
- `vm`: emitted **27**, used **0**, unused **27**

**Totals**
- Total monitor types: **59**
- Total emitted variables: **742**
- Total explicitly used variables (quoted literals, global): **243**
- Total unused variables: **499**

## 2) Total used variables

- Explicitly used monitor variable keys (quoted literal match in usage scope): **153**

## 3) Exhaustive unused variables grouped by type

### `amd64_df` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `EVENT_DRAM_CHANNEL_0`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_1`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_2`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_3`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other

### `amd64_pmc` (19)
- `BRANCH_INST_RETIRED`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `BRANCH_INST_RETIRED_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL4`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL5`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR4`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR5`: raw hardware counter value. **Usefulness:** Medium / compute
- `DISPATCH_STALL_CYCLES0`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `DISPATCH_STALL_CYCLES1`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_0`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_1`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_2`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_3`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `MERGE`: hardware performance counter for floating-point or retirement. **Usefulness:** Low / other

### `amd64_rapl` (1)
- `MSR_CORE_ENERGY_STAT`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `amd_gpu` (4)
- `gpu_flops_rate`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_read_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_total_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_write_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU

### `block` (8)
- `io_ticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rd_ios`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rd_merges`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rd_ticks`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `time_in_queue`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `wr_ios`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `wr_merges`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `wr_ticks`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `cpu` (2)
- `iowait`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `softirq`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `cpu_counter_metrics` (35)
- `CPU_CLK_UNHALTED_CORE`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_CLK_UNHALTED_REF`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_CLOCK_EST_CYCLES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_UTIL_IRQ_ACCUM_US`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_UTIL_NICE_ACCUM_US`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_UTIL_SYS_ACCUM_US`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_UTIL_TOTAL_ACCUM_US`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CPU_UTIL_USER_ACCUM_US`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL4`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL5`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL6`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL7`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR4`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR5`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR6`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR7`: raw hardware counter value. **Usefulness:** Medium / compute
- `EVENT_DRAM_CHANNEL_0`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_1`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_2`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `EVENT_DRAM_CHANNEL_3`: raw AMD DF DRAM channel counter before ingest decode. **Usefulness:** Low / other
- `INSTR_RETIRED_ANY`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `L1D_REPLACEMENT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `LS_DISPATCH`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_L1_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_L2_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_LLC_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RETIRED_BRANCH_INSTR`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RETIRED_INSTRUCTIONS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RETIRED_MISP_BRANCH_INSTR`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `ib` (4)
- `VL15_dropped`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_rcv_packets`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_packets`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_xmit_wait`: monitor-emitted telemetry field. **Usefulness:** Medium / network

### `ib_ext` (6)
- `counter_select`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `port_multicast_rcv_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_multicast_xmit_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_select`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_unicast_rcv_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `port_unicast_xmit_pkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network

### `intel_4pmc3` (21)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `DTLB_LOAD_MISSES_MISS_CAUSES_A_WALK`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FP_COMP_OPS_EXE_SSE_FP_PACKED`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FP_COMP_OPS_EXE_SSE_FP_SCALAR`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `L1D_REPLACEMENT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `L2_LINES_IN_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_L1_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_L2_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_LLC_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UNCORE_RETIRED_LOCAL_DRAM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UNCORE_RETIRED_REMOTE_DRAM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UOPS_RETIRED_ALL_LOADS_KNL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UOPS_RETIRED_L2_HIT_LOADS_KNL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RESOURCE_STALLS_ANY`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `SIMD_FP_256_PACKED_DOUBLE`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `SSE_DOUBLE_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_8pmc3` (29)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL4`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL5`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL6`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL7`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR4`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR5`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR6`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR7`: raw hardware counter value. **Usefulness:** Medium / compute
- `DTLB_LOAD_MISSES_MISS_CAUSES_A_WALK`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FP_COMP_OPS_EXE_SSE_FP_PACKED`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FP_COMP_OPS_EXE_SSE_FP_SCALAR`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `L1D_REPLACEMENT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `L2_LINES_IN_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_L1_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_L2_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_LOAD_UOPS_RETIRED_LLC_HIT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UNCORE_RETIRED_LOCAL_DRAM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UNCORE_RETIRED_REMOTE_DRAM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UOPS_RETIRED_ALL_LOADS_KNL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UOPS_RETIRED_L2_HIT_LOADS_KNL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RESOURCE_STALLS_ANY`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `SIMD_FP_256_PACKED_DOUBLE`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `SSE_DOUBLE_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_bdw_cbo` (9)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `LLC_LOOKUP_DATA_READ`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_IV_USED`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RxR_OCCUPANCY`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_bdw_hau` (10)
- `CLOCKTICKS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `IMC_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_READS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_bdw_imc` (9)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `ACT_COUNT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `PRE_COUNT_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_bdw_qpi` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `G1_DRS_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `G2_NCB_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_HOM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_SNP`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_bdw_r2pci` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `RING_AD_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_AK_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_BL_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxR_INSERTS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_hsw_cbo` (9)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `LLC_LOOKUP_DATA_READ`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_IV_USED`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RxR_OCCUPANCY`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_hsw_hau` (10)
- `CLOCKTICKS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `IMC_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_READS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_hsw_imc` (9)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `ACT_COUNT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `PRE_COUNT_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_hsw_qpi` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `G1_DRS_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `G2_NCB_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_HOM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_SNP`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_hsw_r2pci` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `RING_AD_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_AK_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_BL_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxR_INSERTS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_ivb_cbo` (9)
- `COUNTER0_OCCUPANCY`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `LLC_LOOKUP_DATA_READ`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_IV_USED`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_ivb_hau` (10)
- `CLOCKTICKS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `IMC_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_READS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_ivb_imc` (9)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `ACT_COUNT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `PRE_COUNT_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_ivb_qpi` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `G1_DRS_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `G2_NCB_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_HOM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_SNP`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_ivb_r2pci` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `RING_AD_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_AK_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_BL_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxR_INSERTS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_knl` (4)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `MEM_UOPS_RETIRED_ALL_LOADS_KNL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MEM_UOPS_RETIRED_L2_HIT_LOADS_KNL`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_knl_edc` (3)
- `EDC_HIT_CLEAN`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `EDC_HIT_DIRTY`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RPQ_INSERTS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_pcu` (4)
- `FREQ_MAX_POWER_CYCLES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FREQ_MAX_TEMP_CYCLES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FREQ_MIN_IO_CYCLES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `FREQ_MIN_SNOOP_CYCLES`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_rapl` (3)
- `MSR_DRAM_ENERGY_STATUS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MSR_PP0_ENERGY_STATUS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `MSR_PP1_ENERGY_STATUS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_skx_cha` (9)
- `BYPASS_CHA_IMC_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `LLC_LOOKUP_DATA_READ_LOCAL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `SF_EVICTIONS_MES`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_skx_imc` (2)
- `ACT_COUNT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `PRE_COUNT_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_snb_cbo` (9)
- `COUNTER0_OCCUPANCY`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `LLC_LOOKUP_DATA_READ`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_IV_USED`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_snb_hau` (10)
- `CLOCKTICKS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `IMC_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_READS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `REQUESTS_WRITES`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_snb_imc` (9)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `ACT_COUNT`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `PRE_COUNT_MISS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_snb_qpi` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `G1_DRS_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `G2_NCB_DATA`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_HOM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxL_FLITS_G1_SNP`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `intel_snb_r2pci` (10)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `RING_AD_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_AK_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `RING_BL_USED_ALL`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `TxR_INSERTS`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `llite` (10)
- `dirty_pages_hits`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `dirty_pages_misses`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `getxattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `inode_permission`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ioctl`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `osc_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `osc_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `seek`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `write`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `lnet` (9)
- `errors`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `msgs_alloc`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `msgs_alloc_max`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `route_bytes`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `route_msgs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_bytes_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_msgs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_msgs_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_msgs`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `mdc` (9)
- `ldlm_cancel`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_close`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_getattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_getattr_lock`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_getxattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_readpage`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_statfs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `mds_sync`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `reqs`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `mem` (12)
- `Active`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `AnonHugePages`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `AnonPages`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `Bounce`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `Dirty`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `HugePages_Free`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `HugePages_Total`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `Inactive`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `Mapped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `NFS_Unstable`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `PageTables`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `Writeback`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `mic` (7)
- `idle_sum`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `jiffy_counter`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nice_sum`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `num_cores`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `sys_sum`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `threads_core`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `user_sum`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `net` (6)
- `collisions`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `multicast`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_compressed`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `rx_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_compressed`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tx_dropped`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `nfs` (10)
- `READ_queue`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `READ_rtt`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `READ_timeouts`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `WRITE_queue`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `WRITE_rtt`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `WRITE_timeouts`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `delay`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `xprt_bad_xids`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `xprt_bklog_u`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `xprt_req_u`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `numa` (3)
- `interleave_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `local_node`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `numa_hit`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `nvidia_gpu` (6)
- `gpu_flops_rate`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_read_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_total_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `gpu_mem_write_bytes`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `tensor_hmma_active`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `tensor_imma_active`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `opa` (7)
- `PortMarkFECN`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `PortMulticastRcvPkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `PortMulticastXmitPkts`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `PortRcvBubble`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `PortXmitTimeCong`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `PortXmitWaitData`: monitor-emitted telemetry field. **Usefulness:** Medium / network
- `PortXmitWastedBW`: monitor-emitted telemetry field. **Usefulness:** Medium / network

### `osc` (7)
- `ost_destroy`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_punch`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_read`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_setattr`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_statfs`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `ost_write`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `reqs`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `proc` (13)
- `Threads`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `Uid`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmData`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmExe`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmHWM`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmLck`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmLib`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmPTE`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmPeak`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmRSS`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmSize`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmStk`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `VmSwap`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `ps` (6)
- `ctxt`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `load_1`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `load_15`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `load_5`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nr_running`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `nr_threads`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `roofline_hw_peak` (3)
- `cpu_peak_source`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `gpu_peak_source`: monitor-emitted telemetry field. **Usefulness:** Medium / GPU
- `peak_calc_version`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `sysv_shm` (2)
- `mem_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `segs_used`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `tmpfs` (3)
- `bytes_avail`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `bytes_used`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `files_used`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `vfs` (3)
- `dentry_use`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `file_use`: monitor-emitted telemetry field. **Usefulness:** Low / other
- `inode_use`: monitor-emitted telemetry field. **Usefulness:** Low / other

### `vm` (27)
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

## 4) Methodology and caveats

- Emitted keys = monitor `KEYS` macros per `st_name`, plus ingest-decoded aliases from `sync_timedb_parsing.EVENTMAPS_BY_TYPE`, plus synthetic `CTL*`/`CTR*` (and Intel IMC `0xD8`…`0xE4`) where raw schema lines carry them before decode.
- Usage matching scans explicit quoted key literals in `hpcperfstats/` and `tests/`; dynamically generated keys or indirect mappings can be undercounted.
- This is static source analysis, not runtime coverage.
- Compile-time gated monitor drivers may exist in source but be disabled in a given deployment.
- NFS runtime-composed keys are included: `READ_ops`, `READ_timeouts`, `READ_queue`, `READ_rtt`, `WRITE_ops`, `WRITE_timeouts`, `WRITE_queue`, `WRITE_rtt`.
- Reused raw key names (`CTL*`, `CTR*`, `0xD8`, etc.) are treated per monitor type context.
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

### `intel_bdw_imc`, `intel_hsw_imc`, `intel_ivb_imc`, `intel_snb_imc`, `intel_skx_imc`, `intel_knl_mc_dclk`
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

