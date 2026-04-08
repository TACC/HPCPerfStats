#!/usr/bin/env python3
"""Emit variableMetadataMonitorEvents.js from HPCPerfStats/monitor schema KEYS macros."""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
MONITOR_SRC = REPO / "monitor" / "src"
OUT = Path(__file__).resolve().parent / "variableMetadataMonitorEvents.js"
SKIP_NAMES = frozenset({"uint32_t", "uint64_t", "k", "t", "m", "f", "n"})

MARKERS = (
    "#define KEYS",
    "#define ARM_IMC_KEYS",
    "#define CPU_COUNTER_METRICS_KEYS",
    "#define EVENT_KEYS",
    "#define BYTE_KEYS",
    "#define XPRT_KEYS",
)


def extract_x_keys(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        s = line.strip()
        if s.startswith("#") or not s.startswith("X("):
            continue
        m = re.match(r"X\(\s*([A-Za-z0-9_]+)\s*,", s)
        if not m:
            continue
        k = m.group(1)
        if k not in SKIP_NAMES and len(k) >= 2:
            out.add(k)
    return out


def macro_block(lines: list[str], start: int) -> list[str]:
    block: list[str] = []
    for j in range(start, min(start + 200, len(lines))):
        block.append(lines[j])
        if j > start and lines[j].strip().startswith("#endif"):
            break
        if j > start and lines[j].strip().startswith("static "):
            break
        if j > start and lines[j].strip().startswith("struct stats_type"):
            break
    return block


def collect_all_names() -> set[str]:
    names: set[str] = set()
    for path in sorted(MONITOR_SRC.glob("*.c")):
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(text_lines):
            st = line.strip()
            if not any(st.startswith(m) for m in MARKERS):
                continue
            names |= extract_x_keys(macro_block(text_lines, i))
    for path in sorted(MONITOR_SRC.glob("*.h")):
        text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(text_lines):
            st = line.strip()
            if not any(st.startswith(m) for m in MARKERS):
                continue
            names |= extract_x_keys(macro_block(text_lines, i))
    for op in ("READ", "WRITE"):
        for suf in ("ops", "timeouts", "queue", "rtt"):
            names.add(f"{op}_{suf}")
    for i in range(8):
        names.add(f"MBW_CHANNEL_{i}")
    # Intel legacy FP proxy events (analysis/utils.py); may appear in host_data when programmed.
    for legacy in (
        "SSE_DOUBLE_SCALAR",
        "SSE_DOUBLE_PACKED",
        "SIMD_DOUBLE_256",
    ):
        names.add(legacy)
    return names


DESC: dict[str, str] = {
    "user": "Cumulative CPU time in user mode (per-core counter; units per Linux /proc/stat, typically jiffies).",
    "nice": "Cumulative CPU time in low-priority user mode.",
    "system": "Cumulative CPU time in kernel mode.",
    "idle": "Cumulative CPU idle time.",
    "iowait": "Cumulative CPU time waiting for block I/O.",
    "irq": "Cumulative CPU time handling hardware interrupts.",
    "softirq": "Cumulative CPU time in softirq bottom halves.",
    "rd_sectors": "Sectors read from the block device (512-byte sectors, Linux sysfs block stat).",
    "wr_sectors": "Sectors written to the block device (512-byte sectors).",
    "rd_ios": "Completed read I/O operations.",
    "wr_ios": "Completed write I/O operations.",
    "rd_merges": "Read requests merged with the in-queue I/O queue.",
    "wr_merges": "Write requests merged with the in-queue I/O queue.",
    "rd_ticks": "Time spent on read I/O (milliseconds).",
    "wr_ticks": "Time spent on write I/O (milliseconds).",
    "io_ticks": "Time the disk queue was actively servicing I/O (milliseconds).",
    "time_in_queue": "Accumulated time requests spent in the I/O scheduler queue (milliseconds).",
    "in_flight": "Number of I/O requests currently in flight to the device.",
    "rx_bytes": "Bytes received (Ethernet per-interface sysfs, or another type—disambiguate with host_data.type).",
    "tx_bytes": "Bytes transmitted (Ethernet per-interface sysfs, Lustre LNET, or other type—use host_data.type).",
    "rx_packets": "Packets received (per-interface sysfs).",
    "tx_packets": "Packets transmitted.",
    "rx_errors": "Receive errors reported by the driver.",
    "tx_errors": "Transmit errors reported by the driver.",
    "rx_dropped": "Receive drops (kernel or driver).",
    "tx_dropped": "Transmit drops.",
    "collisions": "Ethernet collision counter (half-duplex legacy).",
    "multicast": "Multicast packet counter (receive path; naming varies by driver).",
    "port_xmit_data": "InfiniBand counter: payload bytes transmitted (width/units per IBTA and sysfs docs).",
    "port_rcv_data": "InfiniBand counter: payload bytes received.",
    "port_xmit_pkts": "Packets transmitted (IB counters).",
    "port_rcv_pkts": "Packets received (IB counters).",
    "port_select": "IB MAD extended counter query selector field (configuration metadata).",
    "counter_select": "IB MAD extended counter query selector field.",
    "port_unicast_xmit_pkts": "Unicast packets transmitted (IB extended).",
    "port_unicast_rcv_pkts": "Unicast packets received (IB extended).",
    "port_multicast_xmit_pkts": "Multicast packets transmitted (IB extended).",
    "port_multicast_rcv_pkts": "Multicast packets received (IB extended).",
    "port_rcv_remote_physical_errors": "InfiniBand inbound physical-layer error counter.",
    "port_rcv_switch_relay_errors": "InfiniBand switch relay errors on received traffic.",
    "port_xmit_discards": "Packets not transmitted because the port was down or congested.",
    "port_xmit_wait": "Time waiting for credits or arbitration (vendor-specific units).",
    "symbol_error": "Minor link symbol errors on InfiniBand.",
    "VL15_dropped": "InfiniBand VL 15 dropped frames.",
    "CAS_READS": "Memory-controller DRAM CAS read events (Intel uncore IMC or normalized ARM IMC), used for DRAM bandwidth.",
    "CAS_WRITES": "Memory-controller DRAM CAS write events.",
    "FLOPS": "AMD core performance counter: retired SSE/AVX floating-point operations (family-specific event encoding).",
    "MERGE": "AMD PMC auxiliary counter used with merged FLOPS counting.",
    "BRANCH_INST_RETIRED": "Retired branch instructions (AMD PMC).",
    "BRANCH_INST_RETIRED_MISS": "Retired mispredicted branches (AMD PMC).",
    "DISPATCH_STALL_CYCLES0": "Dispatch stall cycles (AMD event slot 0).",
    "DISPATCH_STALL_CYCLES1": "Dispatch stall cycles (AMD event slot 1).",
    "INST_RETIRED": "Instructions retired (fixed counter or MSR alias aligned with IA32_FIXED_CTR0).",
    "APERF": "Actual frequency clock ticks (MSR); with MPERF yields effective CPU frequency.",
    "MPERF": "Reference clock ticks while the core is active; pairs with APERF for frequency.",
    "FIXED_CTR0": "Intel fixed counter 0 (typically instructions retired).",
    "FIXED_CTR1": "Intel fixed counter 1 (typically unhalted core cycles).",
    "FIXED_CTR2": "Intel fixed counter 2 (typically reference cycles).",
    "FIXED_CTR": "Intel uncore fixed counter (IMC devices; meaning per uncore setup).",
    "DF_CTR0": "AMD Data Fabric performance counter 0 (or zero-filled placeholder in unified schema).",
    "DF_CTR1": "AMD Data Fabric performance counter 1.",
    "DF_CTR2": "AMD Data Fabric performance counter 2.",
    "DF_CTR3": "AMD Data Fabric performance counter 3.",
    "ARM_EST_FLOPS": "Synthetic cumulative floating-point work estimate for ARM/DCGM-backed paths (monitor-derived).",
    "ARM_DRAM_BW_BYTES": "Synthetic cumulative DRAM byte traffic estimate for ARM/DCGM-backed paths (monitor-derived).",
    "DCGM_CPU_POWER_UTIL_W": "Per-socket CPU power draw from DCGM on Grace/superchip hosts (watts; replicated per logical CPU in that socket).",
    "DCGM_CPU_POWER_LIMIT_W": "Per-socket CPU power limit from DCGM when exposed (watts).",
    "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE": "Intel PMU: scalar double-precision floating-point arithmetic instructions retired (Intel SDM).",
    "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE": "Intel PMU: 128-bit packed double-precision FP arithmetic instructions retired.",
    "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE": "Intel PMU: 256-bit packed double-precision FP arithmetic instructions retired.",
    "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE": "Intel PMU: 512-bit packed double-precision FP arithmetic instructions retired.",
    "FP_ARITH_INST_RETIRED_SCALAR_SINGLE": "Intel PMU: scalar single-precision FP arithmetic instructions retired.",
    "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE": "Intel PMU: 128-bit packed single-precision FP arithmetic instructions retired.",
    "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE": "Intel PMU: 256-bit packed single-precision FP arithmetic instructions retired.",
    "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE": "Intel PMU: 512-bit packed single-precision FP arithmetic instructions retired.",
    "tensor_active": (
        "GPU tensor/matrix pipe activity as percent in monitor output. "
        "On `nvidia_gpu` this comes from DCGM PROF; `amd_gpu` shares the same KEY name with a slimmer schema."
    ),
    "gpu_util": (
        "GPU utilization percent. Populated from DCGM on `nvidia_gpu`; from GPUPerfAPI (or stub zeros) on `amd_gpu`."
    ),
    "mem_util": (
        "GPU memory copy/engine utilization percent. DCGM on `nvidia_gpu`; same field exists on `amd_gpu` when the backend is wired."
    ),
    "gpu_mem_bw_bytes_rate": (
        "Estimated GPU memory bandwidth (bytes/s) from the monitor model. DCGM-backed on `nvidia_gpu`; `amd_gpu` uses the same schema key."
    ),
    "power_usage": "GPU power draw (watts). DCGM on `nvidia_gpu`; device-reported path on `amd_gpu` when available.",
    "sysio_power_usage": (
        "NVIDIA-only (`nvidia_gpu` / DCGM): SysIO instantaneous power (watts). Not present in the slimmer `amd_gpu` monitor schema."
    ),
    "module_power_usage": (
        "NVIDIA-only (`nvidia_gpu` / DCGM): module-scope power (watts) on integrated packages. Omitted from `amd_gpu` KEYS."
    ),
    "temperature": "GPU temperature (degrees C). DCGM on `nvidia_gpu`; `amd_gpu` when GPUPerfAPI supplies it.",
    "fp64_active": (
        "FP64 pipe activity ratio as percent. DCGM PROF on `nvidia_gpu`; same semantic field on `amd_gpu` (wave/SM naming differs)."
    ),
    "fp32_active": "FP32 pipe activity percent (DCGM PROF on NVIDIA; shared KEY on `amd_gpu`).",
    "fp16_active": "FP16 pipe activity percent (DCGM PROF on NVIDIA; shared KEY on `amd_gpu`).",
    "sm_active": "SM (or CU) activity percent: DCGM PROF on `nvidia_gpu`; GPUPerfAPI-style on `amd_gpu`.",
    "sm_occupancy": "Occupancy percent: DCGM warp occupancy on NVIDIA; AMD uses active-wave occupancy wording in schema.",
    "clocks_event_reasons": "GPU clock throttle reasons bitmask (DCGM on `nvidia_gpu`; same KEY on `amd_gpu` when populated).",
    "gpu_flops_rate": "Instantaneous estimated GPU FLOP/s (monitor model; both GPU types expose this KEY).",
    "gpu_flops": "Cumulative estimated GPU FLOPs (monitor-integrated model; both GPU types).",
    "gpu_mem_read_bytes": "Cumulative estimated GPU memory read bytes (model; shared `amd_gpu` / `nvidia_gpu` schema).",
    "gpu_mem_write_bytes": "Cumulative estimated GPU memory write bytes (model; shared schema).",
    "gpu_mem_total_bytes": "Cumulative estimated GPU memory traffic bytes (model; shared schema).",
    "gpu_io_link_total_bytes": (
        "NVIDIA-only (`nvidia_gpu` / DCGM): cumulative PCIe plus NVLink bytes from DCGM PROF link counters (not HBM/VRAM traffic). "
        "This KEY is not in `amd_gpu.h`; the AMD monitor schema stops at `gpu_mem_total_bytes` plus `gpu_count`."
    ),
    "gpu_count": (
        "GPU device count for the monitor row. DCGM-visible GPU count on `nvidia_gpu`; `amd_gpu` schema documents a stub row count (often 1)."
    ),
    "mem_total_mb": "Total GPU framebuffer memory (MB), device-reported (shared `nvidia_gpu` / `amd_gpu` KEY).",
    "mem_used_mb": "Used GPU framebuffer memory (MB), device-reported (shared `nvidia_gpu` / `amd_gpu` KEY).",
    "numa_hit": "NUMA allocations satisfied on the preferred node (/sys/.../numastat).",
    "numa_miss": "NUMA allocations that missed the preferred node (numastat).",
    "numa_foreign": "Pages counted as foreign to the allocating node.",
    "interleave_hit": "NUMA interleave policy hits.",
    "local_node": "Local-node memory accesses (numastat).",
    "other_node": "Remote-node memory accesses (numastat).",
    "PortXmitData": "Intel Omni-Path port counter: data transmitted.",
    "PortRcvData": "Intel Omni-Path port counter: data received.",
    "PortXmitPkts": "OPA packets transmitted.",
    "PortRcvPkts": "OPA packets received.",
    "PortMulticastXmitPkts": "OPA multicast packets transmitted.",
    "PortMulticastRcvPkts": "OPA multicast packets received.",
    "PortXmitWait": "OPA wait or stall counter related to credits or arbitration.",
    "SwPortCongestion": "OPA switch congestion indicator.",
    "PortRcvFECN": "OPA forward explicit congestion notifications received.",
    "PortRcvBECN": "OPA backward explicit congestion notifications received.",
    "PortXmitTimeCong": "OPA time spent in congestion-related transmit delay.",
    "PortXmitWastedBW": "OPA bandwidth lost to congestion or backpressure.",
    "PortXmitWaitData": "OPA data-volume related wait counter.",
    "PortRcvBubble": "OPA bubble / idle counter on receive path.",
    "PortMarkFECN": "OPA FECN marks applied.",
    "PortErrorCounterSummary": "OPA aggregated port error summary counter.",
    "msgs_alloc": "Lustre LNET: messages currently allocated.",
    "msgs_alloc_max": "LNET high-water mark of allocated messages.",
    "errors": "LNET error counter.",
    "tx_msgs": "LNET messages transmitted.",
    "rx_msgs": "LNET messages received.",
    "route_msgs": "LNET routed messages (routers).",
    "rx_msgs_dropped": "LNET receive messages dropped.",
    "route_bytes": "LNET routed bytes.",
    "rx_bytes_dropped": "LNET receive bytes dropped.",
    "delay": "NFS client delay events from /proc/self/mountstats.",
    "normal_read": "NFS normal read bytes.",
    "normal_write": "NFS normal write bytes.",
    "direct_read": "NFS direct I/O read bytes.",
    "direct_write": "NFS direct I/O write bytes.",
    "server_read": "NFS server-side read bytes (mountstats accounting).",
    "server_write": "NFS server-side write bytes.",
    "xprt_bad_xids": "NFS RPC transport bad XID count.",
    "xprt_req_u": "NFS transport accumulated in-flight request measure (kernel xprt stats).",
    "xprt_bklog_u": "NFS transport backlog utilization accumulator.",
    "READ_ops": "NFS READ RPC operation count (mountstats).",
    "WRITE_ops": "NFS WRITE RPC operation count.",
    "READ_timeouts": "NFS READ major timeouts.",
    "WRITE_timeouts": "NFS WRITE major timeouts.",
    "READ_queue": "NFS READ time queued before transmission (milliseconds).",
    "WRITE_queue": "NFS WRITE time queued before transmission (milliseconds).",
    "READ_rtt": "NFS READ round-trip time (milliseconds).",
    "WRITE_rtt": "NFS WRITE round-trip time (milliseconds).",
    "VmPeak": "Peak virtual memory size (kB) for a sampled process.",
    "VmSize": "Current virtual memory size (kB).",
    "VmLck": "Locked memory (kB).",
    "VmHWM": "Peak resident set size (kB)—used for memory high-water style metrics.",
    "VmRSS": "Current resident set size (kB).",
    "VmData": "Data segment size (kB).",
    "VmStk": "Stack size (kB).",
    "VmExe": "Executable code size (kB).",
    "VmLib": "Shared library mapping size (kB).",
    "VmPTE": "Page table entries footprint (kB).",
    "VmSwap": "Swapped-out anonymous memory (kB).",
    "Threads": "Thread count from sampled process status.",
    "Uid": "User ID of sampled process.",
    "load_1": "One-minute load average (monitor often scales by 100; see unit metadata).",
    "load_5": "Five-minute load average.",
    "load_15": "Fifteen-minute load average.",
    "ctxt": "Context switches (global, from /proc/stat via ps stats type).",
    "processes": "Processes created (forks) from /proc/stat.",
    "nr_running": "Runnable tasks on the run queue (ps/global stat).",
    "nr_threads": "Thread count from global /proc/stat-derived ps sample.",
    "user_sum": "Intel Xeon Phi (MIC): aggregate user time across cores.",
    "nice_sum": "MIC: aggregate nice time.",
    "sys_sum": "MIC: aggregate system time.",
    "idle_sum": "MIC: aggregate idle time.",
    "jiffy_counter": "MIC: jiffy count at query time.",
    "num_cores": "MIC: number of cores.",
    "threads_core": "MIC: hardware threads per core.",
    "dentry_use": "VFS: directory cache entries in use (approximate from dentry-state).",
    "file_use": "VFS: open file handles in use (/proc/sys/fs/file-nr).",
    "inode_use": "VFS: inodes in use (inode-state).",
    "bytes_used": "Tmpfs: bytes used on the tmpfs mount.",
    "files_used": "Tmpfs: number of files in use on the mount.",
    "mem_used": "System V shared memory: total bytes used across segments.",
    "segs_used": "System V shared memory: number of segments in use.",
    "MSR_PKG_ENERGY_STATUS": "Intel RAPL MSR: package energy status (raw units).",
    "MSR_DRAM_ENERGY_STATUS": "Intel RAPL MSR: DRAM domain energy status.",
    "MSR_PP0_ENERGY_STATUS": "Intel RAPL MSR: PP0 (cores) energy status.",
    "MSR_PP1_ENERGY_STATUS": "Intel RAPL MSR: PP1 (uncore/GT when present) energy status.",
    "MSR_CORE_ENERGY_STAT": "Intel RAPL-related core energy (platform-specific naming).",
    "MSR_PKG_ENERGY_STAT": "Intel RAPL package energy status (alternate MSR naming).",
    "SSE_DOUBLE_SCALAR": "Intel core PMU: retired SSE/AVX double-precision scalar FP operations (legacy FLOP proxy).",
    "SSE_DOUBLE_PACKED": "Intel core PMU: retired SSE/AVX packed double-precision FP operations (legacy FLOP proxy).",
    "SIMD_DOUBLE_256": "Intel core PMU: retired 256-bit packed double-precision SIMD FP operations (legacy FLOP proxy).",
    # Lustre llite: /proc/fs/lustre/llite/*/stats (monitor llite.c; llite_opcode_table lineage).
    "alloc_inode": "Lustre llite: inode allocation operations counted in per-mount `/proc/fs/lustre/llite/*/stats`.",
    "close": "Lustre llite: `close(2)` operation count per mount (`/proc/fs/lustre/llite/*/stats`).",
    "create": "Lustre llite: file create operation count.",
    "dirty_pages_hits": "Lustre llite: dirty client-page cache hits (samples line in `/proc/fs/lustre/llite/*/stats`).",
    "dirty_pages_misses": "Lustre llite: dirty client-page cache misses; high misses vs hits can mean poor cache reuse.",
    "flock": "Lustre llite: advisory `flock` operation count.",
    "fsync": "Lustre llite: `fsync` operation count.",
    "getattr": "Lustre llite: getattr / stat-style metadata operation count.",
    "getxattr": "Lustre llite: `getxattr` syscall count.",
    "inode_permission": "Lustre llite: inode permission check count (security / ACL path activity).",
    "ioctl": "Lustre llite: `ioctl` operation count on this mount.",
    "link": "Lustre llite: hard `link` operation count.",
    "listxattr": "Lustre llite: `listxattr` syscall count.",
    "lookup": "Lustre llite: pathname `lookup` operation count.",
    "mkdir": "Lustre llite: `mkdir` operation count.",
    "mknod": "Lustre llite: `mknod` operation count.",
    "mmap": "Lustre llite: `mmap` operation count.",
    "open": "Lustre llite: `open(2)` operation count.",
    "osc_read": "Lustre llite: bytes attributed to OSC read path in llite stats (byte sum field; complements OSC counters).",
    "osc_write": "Lustre llite: bytes attributed to OSC write path in llite stats (byte sum field; complements OSC counters).",
    "read": "Lustre llite: `read(2)` syscall operation count (volume is in `read_bytes`, not return-value based in llite stats).",
    "read_bytes": (
        "Cumulative bytes read on the Lustre client from llite or OSC `/proc/fs/lustre/*/stats` "
        "(llite counts requested read size per `read(2)`; OSC aggregates byte totals from OST RPCs)."
    ),
    "readdir": "Lustre llite: `readdir` operation count.",
    "removexattr": "Lustre llite: `removexattr` syscall count.",
    "rename": "Lustre llite: `rename` operation count.",
    "rmdir": "Lustre llite: `rmdir` operation count.",
    "seek": "Lustre llite: `seek` operation count.",
    "setattr": "Lustre llite: `setattr` metadata updates (mode/owner/size, etc.).",
    "setxattr": "Lustre llite: `setxattr` syscall count.",
    "statfs": "Lustre llite: `statfs` operation count.",
    "symlink": "Lustre llite: `symlink` creation operation count.",
    "truncate": "Lustre llite: `truncate` operation count.",
    "unlink": "Lustre llite: `unlink` operation count.",
    "write": "Lustre llite: `write(2)` syscall operation count (byte volume in `write_bytes`).",
    "write_bytes": "Cumulative bytes written on the Lustre client from llite or OSC `/proc/fs/lustre/*/stats`.",
    # Lustre OSC: /proc/fs/lustre/osc/*/stats (monitor osc.c; req_waittime aggregation).
    "reqs": "Lustre OSC: sample count taken from `req_waittime` lines in `/proc/fs/lustre/osc/*/stats` (paired with `wait`).",
    "wait": (
        "Lustre OSC: cumulative microseconds from the `req_waittime` sum field in "
        "`/proc/fs/lustre/osc/*/stats` (use deltas with `reqs` for average wait in a window)."
    ),
    # InfiniBand base sysfs (monitor ib.c; /sys/class/infiniband/.../ports/.../counters).
    "excessive_buffer_overrun_errors": "InfiniBand port counter: excessive buffer overruns (base IB sysfs counters).",
    "link_downed": "InfiniBand port counter: failed link error recoveries (link went down; monitor comment in ib.c).",
    "link_error_recovery": "InfiniBand port counter: successful link error recovery events (monitor ib.c).",
    "local_link_integrity_errors": "InfiniBand port counter: local link integrity errors (hardware / cable quality).",
    # Linux netdev extended statistics (/sys/class/net/*/statistics; monitor net.c).
    "rx_compressed": "Linux netdev: received compressed frames (per-interface sysfs statistics).",
    "rx_crc_errors": "Linux netdev: frames received with CRC or FCS errors (physical layer or interference).",
    "rx_fifo_errors": "Linux netdev: receiver FIFO overrun errors.",
    "rx_frame_errors": "Linux netdev: framing errors (misaligned or malformed Ethernet frames).",
    "rx_length_errors": "Linux netdev: received frames with invalid length field.",
    "rx_missed_errors": "Linux netdev: packets missed by the receiver (often ring buffer exhaustion).",
    "rx_over_errors": "Linux netdev: receiver overrun errors.",
    "tx_aborted_errors": "Linux netdev: aborted transmissions (driver or hardware abort).",
    "tx_carrier_errors": "Linux netdev: loss of carrier during transmit (cable, duplex, or link partner).",
    "tx_compressed": "Linux netdev: transmitted compressed frames.",
    "tx_fifo_errors": "Linux netdev: transmit FIFO errors (underrun/overrun, driver dependent).",
    "tx_heartbeat_errors": "Linux netdev: heartbeat / half-duplex loss-of-carrier style errors.",
    "tx_window_errors": "Linux netdev: classic transmitter window errors on outbound frames.",
    # Linux /proc/vmstat transparent hugepages (monitor vm.c).
    "thp_fault_alloc": "Linux /proc/vmstat: transparent hugepage allocations satisfied on fault.",
    "thp_fault_fallback": "Linux /proc/vmstat: THP fault handling fell back to small pages.",
    "thp_collapse_alloc": "Linux /proc/vmstat: successful collapse of page tables into a THP.",
    "thp_collapse_alloc_failed": "Linux /proc/vmstat: failed attempts to collapse mappings into a THP.",
    "thp_split": "Linux /proc/vmstat: transparent hugepage splits (e.g. unmap, compaction, or policy).",
}


def generic(k: str) -> str:
    if re.fullmatch(r"CTL\d+", k):
        return "Performance event select register (programs the paired general-purpose counter)."
    if re.fullmatch(r"CTR\d+", k):
        return "General-purpose performance-monitoring counter value; meaning depends on paired CTL programming."
    if re.match(r"V[13]_CTL\d+", k) or re.match(r"V[13]_CTR\d+", k):
        return "Intel uncore / mesh CHA counter or control register (Skylake-class naming)."
    if k.startswith("MBW_CHANNEL_"):
        return "AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n)."
    if k.startswith("mds_") or k.startswith("ost_") or k.startswith("ldlm_"):
        return "Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre."
    if k.startswith("pg") or k in (
        "pswpin",
        "pswpout",
        "allocstall",
        "pageoutrun",
        "slabs_scanned",
        "kswapd_steal",
        "kswapd_inodesteal",
        "nr_anon_transparent_hugepages",
    ):
        return "Kernel VM statistic from /proc/vmstat (Linux kernel documentation)."
    if k in (
        "MemTotal",
        "MemFree",
        "MemUsed",
        "Active",
        "Inactive",
        "Dirty",
        "Writeback",
        "Slab",
        "Mapped",
        "AnonPages",
        "PageTables",
        "HugePages_Total",
        "HugePages_Free",
        "AnonHugePages",
        "NFS_Unstable",
        "Bounce",
        "FilePages",
    ):
        return "NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted)."
    if k.startswith("port_") or (k.startswith("Port") and k not in DESC):
        return "Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path)."
    return "Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type)."


def main() -> int:
    if not MONITOR_SRC.is_dir():
        print("monitor/src not found at", MONITOR_SRC, file=sys.stderr)
        return 1
    names = collect_all_names()
    lines = [
        "/**",
        " * Monitor `host_data.event` names: definitions align with HPCPerfStats/monitor schema KEYS.",
        " * Regenerate: python3 hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py",
        " * (see HPCPerfStats/cursor-rules/variable-metadata-monitor-contract.mdc).",
        " */",
        "",
        "export const MONITOR_EVENT_METADATA = {",
    ]
    for k in sorted(names):
        d = (DESC.get(k) or generic(k)).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'  {k}: {{ description: "{d}" }},')
    lines.append("};")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote", OUT, "keys=", len(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
