# Monitor Variable Usage Gap Analysis

Static code-derived comparison between monitor-emitted schema keys in `monitor/src` and explicit quoted-key usage in `hpcperfstats/` + `tests/` (excluding `monitor/`).

## 1) Total emitted variables (by type)

- `amd64_df`: emitted **8**, used **2**, unused **6**
- `amd64_pmc`: emitted **15**, used **5**, unused **10**
- `amd64_rapl`: emitted **2**, used **2**, unused **0**
- `amd_gpu`: emitted **20**, used **20**, unused **0**
- `arm_imc`: emitted **2**, used **2**, unused **0**
- `block`: emitted **11**, used **11**, unused **0**
- `cpu`: emitted **7**, used **7**, unused **0**
- `cpu_counter_metrics`: emitted **38**, used **24**, unused **14**
- `ib`: emitted **17**, used **15**, unused **2**
- `ib_ext`: emitted **10**, used **10**, unused **0**
- `ib_sw`: emitted **4**, used **4**, unused **0**
- `intel_4pmc3`: emitted **11**, used **5**, unused **6**
- `intel_8pmc3`: emitted **19**, used **5**, unused **14**
- `intel_bdw_cbo`: emitted **8**, used **2**, unused **6**
- `intel_bdw_hau`: emitted **8**, used **2**, unused **6**
- `intel_bdw_imc`: emitted **9**, used **2**, unused **7**
- `intel_bdw_qpi`: emitted **8**, used **2**, unused **6**
- `intel_bdw_r2pci`: emitted **8**, used **2**, unused **6**
- `intel_hsw_cbo`: emitted **8**, used **2**, unused **6**
- `intel_hsw_hau`: emitted **8**, used **2**, unused **6**
- `intel_hsw_imc`: emitted **9**, used **2**, unused **7**
- `intel_hsw_qpi`: emitted **8**, used **2**, unused **6**
- `intel_hsw_r2pci`: emitted **8**, used **2**, unused **6**
- `intel_ivb_cbo`: emitted **8**, used **2**, unused **6**
- `intel_ivb_hau`: emitted **8**, used **2**, unused **6**
- `intel_ivb_imc`: emitted **9**, used **2**, unused **7**
- `intel_ivb_qpi`: emitted **8**, used **2**, unused **6**
- `intel_ivb_r2pci`: emitted **8**, used **2**, unused **6**
- `intel_knl`: emitted **7**, used **5**, unused **2**
- `intel_knl_edc`: emitted **8**, used **2**, unused **6**
- `intel_knl_mc`: emitted **8**, used **2**, unused **6**
- `intel_pcu`: emitted **10**, used **2**, unused **8**
- `intel_rapl`: emitted **4**, used **4**, unused **0**
- `intel_skx_cha`: emitted **8**, used **2**, unused **6**
- `intel_skx_imc`: emitted **8**, used **2**, unused **6**
- `intel_snb_cbo`: emitted **8**, used **2**, unused **6**
- `intel_snb_hau`: emitted **8**, used **2**, unused **6**
- `intel_snb_imc`: emitted **9**, used **2**, unused **7**
- `intel_snb_qpi`: emitted **8**, used **2**, unused **6**
- `intel_snb_r2pci`: emitted **8**, used **2**, unused **6**
- `llite`: emitted **37**, used **37**, unused **0**
- `lnet`: emitted **11**, used **11**, unused **0**
- `mdc`: emitted **10**, used **2**, unused **8**
- `mem`: emitted **17**, used **17**, unused **0**
- `mic`: emitted **7**, used **7**, unused **0**
- `net`: emitted **23**, used **23**, unused **0**
- `nfs`: emitted **18**, used **18**, unused **0**
- `numa`: emitted **6**, used **6**, unused **0**
- `nvidia_gpu`: emitted **25**, used **23**, unused **2**
- `opa`: emitted **16**, used **16**, unused **0**
- `osc`: emitted **10**, used **4**, unused **6**
- `proc`: emitted **13**, used **13**, unused **0**
- `ps`: emitted **7**, used **7**, unused **0**
- `roofline_hw_peak`: emitted **8**, used **5**, unused **3**
- `sysv_shm`: emitted **2**, used **2**, unused **0**
- `tmpfs`: emitted **3**, used **2**, unused **1**
- `vfs`: emitted **3**, used **3**, unused **0**
- `vm`: emitted **27**, used **13**, unused **14**

**Totals**
- Total monitor types: **58**
- Total emitted variables: **624**
- Total explicitly used variables: **380**
- Total unused variables: **244**

## 2) Total used variables

- Explicitly used monitor variable keys (by quoted literal match in usage scope): **380**

## 3) Exhaustive unused variables grouped by type

### `amd64_df` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `amd64_pmc` (10)
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

### `cpu_counter_metrics` (14)
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

### `ib` (2)
- `port_rcv_packets`: packets received on IB port. **Usefulness:** Medium / network
- `port_xmit_packets`: packets transmitted on IB port. **Usefulness:** Medium / network

### `intel_4pmc3` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_8pmc3` (14)
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

### `intel_bdw_cbo` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_bdw_hau` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_bdw_imc` (7)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_bdw_qpi` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_bdw_r2pci` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_hsw_cbo` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_hsw_hau` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_hsw_imc` (7)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_hsw_qpi` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_hsw_r2pci` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_ivb_cbo` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_ivb_hau` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_ivb_imc` (7)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_ivb_qpi` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_ivb_r2pci` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_knl` (2)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_knl_edc` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_knl_mc` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_pcu` (8)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute
- `0x3FC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0x3FD`: raw uncore event-select register value. **Usefulness:** Medium / memory

### `intel_skx_cha` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_skx_imc` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_snb_cbo` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_snb_hau` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_snb_imc` (7)
- `0xD8`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xDC`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE0`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `0xE4`: raw uncore event-select register value. **Usefulness:** Medium / memory
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / memory
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / memory

### `intel_snb_qpi` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `intel_snb_r2pci` (6)
- `CTL1`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL2`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTL3`: control register selector for configured hardware counter. **Usefulness:** Low / compute
- `CTR1`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR2`: raw hardware counter value. **Usefulness:** Medium / compute
- `CTR3`: raw hardware counter value. **Usefulness:** Medium / compute

### `mdc` (8)
- `ldlm_cancel`: Lustre metadata lock cancel operations. **Usefulness:** Medium / storage
- `mds_close`: Lustre metadata server close ops. **Usefulness:** Medium / storage
- `mds_getattr`: Lustre metadata getattr ops. **Usefulness:** Medium / storage
- `mds_getattr_lock`: Lustre getattr lock operations. **Usefulness:** Medium / storage
- `mds_getxattr`: Lustre extended-attribute reads. **Usefulness:** Medium / storage
- `mds_readpage`: Lustre metadata readpage ops. **Usefulness:** Medium / storage
- `mds_statfs`: Lustre filesystem-stat operations. **Usefulness:** Medium / storage
- `mds_sync`: Lustre metadata sync operations. **Usefulness:** Medium / storage

### `nvidia_gpu` (2)
- `tensor_imma_active`: DCGM tensor IMMA pipe duty cycle (%). **Usefulness:** High / compute
- `tensor_hmma_active`: DCGM tensor HMMA pipe duty cycle (%). **Usefulness:** High / compute

### `osc` (6)
- `ost_destroy`: Lustre OST object destroy ops. **Usefulness:** Medium / storage
- `ost_punch`: Lustre OST punch-hole ops. **Usefulness:** Medium / storage
- `ost_read`: Lustre OST read operations. **Usefulness:** Medium / storage
- `ost_setattr`: Lustre OST setattr operations. **Usefulness:** Medium / storage
- `ost_statfs`: Lustre OST statfs operations. **Usefulness:** Medium / storage
- `ost_write`: Lustre OST write operations. **Usefulness:** Medium / storage

### `roofline_hw_peak` (3)
- `cpu_peak_source`: metadata tag for CPU roofline peak source. **Usefulness:** Low / general
- `gpu_peak_source`: metadata tag for GPU roofline peak source. **Usefulness:** Low / general
- `peak_calc_version`: roofline peak-calculation schema/version marker. **Usefulness:** Low / general

### `tmpfs` (1)
- `bytes_avail`: available tmpfs bytes. **Usefulness:** Low / storage

### `vm` (14)
- `pgpgin`: paged-in kilobytes counter. **Usefulness:** Medium / memory
- `pgpgout`: paged-out kilobytes counter. **Usefulness:** Medium / memory
- `pgalloc_normal`: normal-zone page allocations. **Usefulness:** Medium / memory
- `pgfree`: freed pages counter. **Usefulness:** Medium / memory
- `pgactivate`: pages activated (moved to active list). **Usefulness:** Medium / memory
- `pgdeactivate`: pages deactivated (moved to inactive list). **Usefulness:** Medium / memory
- `pgfault`: total page faults. **Usefulness:** Medium / memory
- `pgmajfault`: major page faults. **Usefulness:** Medium / memory
- `pgrefill_normal`: normal-zone page refills. **Usefulness:** Medium / memory
- `pgsteal_normal`: normal-zone page steals. **Usefulness:** Medium / memory
- `pgscan_kswapd_normal`: normal-zone kswapd scan activity. **Usefulness:** Medium / memory
- `pgscan_direct_normal`: normal-zone direct reclaim scans. **Usefulness:** Medium / memory
- `pginodesteal`: inode reclaim steals. **Usefulness:** Medium / memory
- `pgrotated`: pages rotated between reclaim lists. **Usefulness:** Medium / memory

## 4) Assumptions / caveats

- Usage matching was done against explicit quoted key literals in `hpcperfstats/` and `tests/`; dynamically generated keys or indirect mappings can be undercounted.
- This is static source analysis, not runtime coverage.
- Compile-time gated monitor drivers may exist in source but be disabled in a given deployment.
- NFS runtime-composed keys are included: `READ_ops`, `READ_timeouts`, `READ_queue`, `READ_rtt`, `WRITE_ops`, `WRITE_timeouts`, `WRITE_queue`, `WRITE_rtt`.
- Reused raw key names (`CTL*`, `CTR*`, `0xD8`, etc.) are treated per monitor type context.

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

### `intel_bdw_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (`avg_mbw`, `dram_bw_node_imbalance`), roofline helpers
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`, `dram_bw_node_imbalance`), Summary (`mbw`), CPU roofline memory path

### `intel_hsw_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** same IMC path as above
- **Figures/metrics/displays:** same IMC-backed memory metrics/plots as above

### `intel_ivb_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** same IMC path as above
- **Figures/metrics/displays:** same IMC-backed memory metrics/plots as above

### `intel_knl_mc_dclk`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** same IMC path as above
- **Figures/metrics/displays:** same IMC-backed memory metrics/plots as above

### `intel_skx_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** same IMC path as above
- **Figures/metrics/displays:** same IMC-backed memory metrics/plots as above

### `intel_snb_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** same IMC path as above
- **Figures/metrics/displays:** same IMC-backed memory metrics/plots as above

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
- **Used variables:** `gpu_util`, `utilization`, `tensor_active`, `fp16_active`, `fp32_active`, `fp64_active`, `gpu_mem_bw_bytes_rate`, `power_usage`, `clocks_event_reasons`, `gpu_io_link_total_bytes`, `mem_used_mb`, `mem_util`, `sm_occupancy`, `gpu_count`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, `hpcperfstats/analysis/metrics/gpu_job_detail_summary.py`, summary and roofline plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_gpuutil`, precision/tensor/GPU-link/GPU-power metrics, `detail_gpu_*`, GPU imbalance metrics), Summary GPU panels (`nv_*`), GPU roofline and GPU multiprecision

### `amd_gpu`
- **Used variables:** `gpu_util`, `tensor_active`, `fp16_active`, `fp32_active`, `fp64_active`, `gpu_mem_bw_bytes_rate`, `power_usage`, `clocks_event_reasons`, `gpu_count`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py`, `hpcperfstats/analysis/metrics/gpu_job_detail_summary.py`
- **Figures/metrics/displays:** Job Detail Metrics (GPU fallback paths for `avg_gpuutil`, precision/tensor, memory bandwidth, power, imbalance, `detail_gpu_count`)

### `opa`
- **Used variables:** `PortXmitData`, `PortRcvData`, `PortXmitPkts`, `PortRcvPkts`, `PortXmitWait`, `SwPortCongestion`, `PortRcvFECN`, `PortRcvBECN`
- **Where used in code:** `hpcperfstats/analysis/metrics/metrics.py` (fabric metrics, congestion metrics, imbalance), summary OPA/error plots
- **Figures/metrics/displays:** Job Detail Metrics (fabric fallbacks and `max_opa_congestion_rate`), Summary (`opa_wait_cong`, `opa_ecn`, `summary_hardware_error_rates`)

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