#!/usr/bin/env python3
"""Insert **Diagnostic guidance** into docs/MONITOR_VARIABLES.md for events not wired into analysis.

An event is considered *not used directly* when it is not referenced from product analysis code
(metrics, non-test plots, node_power_est, api, gen/utils) — only ingest (sync_timedb_parsing) or
tests, or no references beyond universal ingest.

Run from repo root (HPCPerfStats/):
  .venv/bin/python docs/augment_monitor_variables_diagnostics.py
"""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parent / "MONITOR_VARIABLES.md"

CATALOG_MARKER = "## Variable catalog (alphabetical)\n\n"


def path_is_analysis_wired(p: str) -> bool:
    """True if this path is product analysis (not ingest-only, not tests)."""
    if "sync_timedb_parsing.py" in p or "hardware_counter_maps" in p:
        return False
    if "/tests/" in p or p.endswith(".test.js"):
        return False
    name = p.rsplit("/", 1)[-1]
    if name.startswith("test_"):
        return False
    if "analysis/metrics/metrics.py" in p:
        return True
    if "analysis/gen/node_power_est.py" in p:
        return True
    if "analysis/gen/utils.py" in p:
        return True
    if "analysis/plot/" in p:
        return True
    if "site/machine/api.py" in p:
        return True
    if "site/frontend/src/" in p and "variableMetadataMonitorEvents.js" not in p:
        if "generate-variable-metadata-monitor-events.py" in p:
            return False
        return True
    return False


def section_needs_diagnostic(body: str) -> bool:
    if "**Diagnostic guidance:**" in body:
        return False
    if "schema placeholders" in body and "map_hardware_counter_vals" in body:
        return True
    if "none outside universal ingest" in body:
        return True
    m = re.search(
        r"\*\*Application / library code:\*\*\s*(.+?)(?=\n- \*\*|\Z)",
        body,
        re.DOTALL,
    )
    paths = []
    if m:
        paths = [p.strip() for p in m.group(1).split(";") if p.strip()]
    if paths:
        return not any(path_is_analysis_wired(p) for p in paths)
    # Only Tests or only Additional none-case already handled
    if "**Application / library code:**" not in body:
        return True
    return True


def diagnostic_for_key(key: str, definition: str) -> str:
    """Return one or two sentences of operator-facing diagnostic use."""
    d = definition.lower()
    k = key

    # PMC schema rows (not persisted as these names)
    if re.fullmatch(r"CTL\d+", k) or re.fullmatch(r"CTR\d+", k):
        return (
            "These labels exist only in raw `!` schema lines; the database stores decoded event "
            "names. For diagnosis, use the decoded counters (for example cache, memory, or uop "
            "events) correlated with time and node to localize stalls or contention."
        )
    if re.match(r"V[13]_CTL\d+", k) or re.match(r"V[13]_CTR\d+", k):
        return (
            "Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the "
            "logical uncore events (often cache or IMC-related) to relate LLC traffic, memory "
            "behavior, or cross-socket coherence to application phases."
        )

    # --- Block ---
    if k in (
        "rd_sectors",
        "wr_sectors",
        "rd_ios",
        "wr_ios",
        "rd_merges",
        "wr_merges",
        "rd_ticks",
        "wr_ticks",
        "io_ticks",
        "time_in_queue",
        "in_flight",
    ):
        return (
            "Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput "
            "limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` "
            "often precede local filesystem or single-device saturation on a node."
        )

    # --- Ethernet ---
    if k in (
        "rx_bytes",
        "tx_bytes",
        "rx_packets",
        "tx_packets",
        "rx_errors",
        "tx_errors",
        "rx_dropped",
        "tx_dropped",
        "collisions",
        "multicast",
        "rx_crc_errors",
        "rx_fifo_errors",
        "rx_frame_errors",
        "rx_length_errors",
        "rx_missed_errors",
        "rx_over_errors",
        "rx_compressed",
        "tx_aborted_errors",
        "tx_carrier_errors",
        "tx_fifo_errors",
        "tx_heartbeat_errors",
        "tx_window_errors",
        "tx_compressed",
    ):
        return (
            "Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, "
            "cable, or switch issues; packet rate vs byte rate helps distinguish small-message "
            "storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or "
            "duplex/speed mismatch. Correlate with MPI or TCP job phases."
        )

    # --- IB / MAD selectors (metadata) ---
    if k in ("port_select", "counter_select"):
        return (
            "Configuration/metadata for counter queries rather than workload metrics. Use adjacent "
            "payload counters (`port_*`) for link health diagnosis."
        )

    # --- IB data path (generic) ---
    if k.startswith("port_") and k not in ("port_select",):
        return (
            "Use xmit vs rcv data and packet counters to find asymmetric communication patterns; "
            "error and discard counters flag link, credit, or congestion problems on the HFI."
        )

    # --- OPA ---
    if k.startswith("Port") or k in (
        "SwPortCongestion",
        "PortErrorCounterSummary",
    ):
        return (
            "FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare "
            "with application all-to-all or IO bursts. Rising error summaries warrant port "
            "cleaning, firmware checks, or topology review."
        )

    # --- Lustre llite single-op counters ---
    if k in (
        "open",
        "close",
        "mmap",
        "fsync",
        "setattr",
        "truncate",
        "flock",
        "getattr",
        "statfs",
        "alloc_inode",
        "setxattr",
        "listxattr",
        "removexattr",
        "readdir",
        "create",
        "lookup",
        "link",
        "unlink",
        "symlink",
        "mkdir",
        "rmdir",
        "mknod",
        "rename",
    ):
        return (
            "High rates of metadata-heavy ops (`open`, `getattr`, `readdir`, …) vs low byte "
            "traffic suggest metadata-bound shared filesystem usage; compare across nodes for "
            "MDS hot spots."
        )

    # --- Lustre mdc / osc / ldlm ---
    if k.startswith("mds_") or k.startswith("ost_") or k.startswith("ldlm_"):
        return (
            "MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during "
            "specific job steps often indicate small-file churn, lock contention, or aggressive "
            "stat/cache behavior."
        )

    # --- LNET ---
    if k.startswith("msgs_") or k in (
        "errors",
        "tx_msgs",
        "rx_msgs",
        "route_msgs",
        "rx_msgs_dropped",
        "route_bytes",
        "rx_bytes_dropped",
    ):
        return (
            "LNET message and drop counters isolate router or NIC issues on Lustre networks; "
            "correlate drops with application I/O phases and remote mount health."
        )

    # --- NFS ---
    if k in (
        "delay",
        "normal_read",
        "normal_write",
        "direct_read",
        "direct_write",
        "server_read",
        "server_write",
    ) or k.startswith("xprt_") or k.startswith("READ_") or k.startswith("WRITE_"):
        return (
            "Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare "
            "timeouts and queue times with server or network events. Asymmetric read/write behavior "
            "helps separate metadata from data path problems."
        )

    # --- NUMA meminfo fields ---
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
        return (
            "Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` "
            "with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs "
            "file-backed footprint. Compare across NUMA nodes for imbalance."
        )

    # --- vmstat-style ---
    if k.startswith("thp_"):
        return (
            "Transparent hugepage counters show THP allocation success vs fallback/split; high "
            "`thp_fault_fallback` or `thp_split` with memory-bound jobs can mean fragmentation "
            "or conflicting `madvise` behavior—compare with `pgmajfault` and RSS from `proc`."
        )
    if k.startswith("dirty_pages_"):
        return (
            "Lustre dirty-page cache hits/misses on the client; low hit rates with heavy write "
            "loads can push more work to OSS and increase observed write latency."
        )
    if k in ("link_downed", "link_error_recovery", "local_link_integrity_errors", "excessive_buffer_overrun_errors"):
        return (
            "InfiniBand link state and reliability counters; correlate transitions with job start/end, "
            "cable reseats, or switch maintenance windows."
        )
    if k in ("read", "write", "seek", "wait", "ioctl", "getxattr", "inode_permission", "reqs"):
        return (
            "Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates "
            "often accompany metadata-heavy tools or security modules scanning many files."
        )
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
        return (
            "VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd "
            "activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM "
            "events and per-job `Vm*` process stats."
        )

    # --- Process status ---
    if k.startswith("Vm") or k in ("Threads", "Uid"):
        return (
            "Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or "
            "OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags "
            "thrashing."
        )

    # --- tmpfs / vfs / sysv ---
    if k in ("bytes_used", "files_used"):
        return (
            "tmpfs growth on compute nodes can exhaust RAM-backed `/dev/shm` or job-local buffers; "
            "relate to staging or MPI shared-memory use."
        )
    if k in ("dentry_use", "file_use", "inode_use"):
        return (
            "VFS cache pressure can precede slow path lookups under millions of small files; "
            "compare with Lustre metadata rates."
        )
    if k in ("mem_used", "segs_used"):
        return (
            "SysV shared memory usage can indicate legacy IPC or shared arrays; unexpected growth "
            "may leak segments across job steps."
        )

    # --- MIC ---
    if k in (
        "user_sum",
        "nice_sum",
        "sys_sum",
        "idle_sum",
        "jiffy_counter",
        "num_cores",
        "threads_core",
    ):
        return (
            "Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated "
            "user/system/idle against expected offload utilization."
        )

    # --- ps / global ---
    if k.startswith("load_") or k in ("ctxt", "processes", "nr_running", "nr_threads"):
        return (
            "Load average and context-switch rates contextualize CPU contention: high `ctxt` with "
            "moderate CPU counters may indicate excessive threading or I/O wakeups."
        )

    # --- idle CPU fields (cpu type) ---
    if k in ("idle", "iowait", "irq", "softirq", "nice"):
        return (
            "Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms "
            "(`irq`/`softirq`), and low-priority work (`nice`) from useful compute."
        )

    # --- AMD DF raw (not MBW_CHANNEL decode) ---
    if k.startswith("DF_CTR"):
        return (
            "Raw Data Fabric counter slots; meaning depends on monitor programming. Use alongside "
            "decoded `MBW_CHANNEL_*` rates to validate DRAM traffic or debug counter setup."
        )

    # --- Intel uncore / RAPL extras ---
    if k.startswith("MSR_") or k.startswith("MSR"):
        return (
            "RAPL energy counters support power/cap diagnosis: compare package vs DRAM domain "
            "trends with workload phases; sudden plateaus may reflect power or thermal limits."
        )

    # --- Fixed CTR generic (when only in ingest map) ---
    if k.startswith("FIXED_CTR") and "uncore" in d:
        return (
            "Uncore fixed counter; semantics depend on programming. Use with other uncore types "
            "on the same socket to relate package-level behavior to memory or cache events."
        )

    # --- GPU (not all wired to summary) ---
    if k in (
        "gpu_flops_rate",
        "gpu_flops",
        "gpu_mem_read_bytes",
        "gpu_mem_write_bytes",
        "gpu_mem_total_bytes",
        "sysio_power_usage",
        "clocks_event_reasons",
        "gpu_io_link_total_bytes",
    ) or (
        k.startswith("gpu_")
        and k
        not in (
            "gpu_util",
            "gpu_count",
        )
    ):
        return (
            "GPU cumulative or instantaneous model counters support kernel efficiency and memory "
            "traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. "
            "Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs."
        )

    # --- IB symbol / VL ---
    if k == "symbol_error":
        return (
            "Physical-layer symbol-error counters warrant link quality checks; intermittent spikes "
            "often correlate with cable wear or switch port errors."
        )

    # --- numa hit/miss ---
    if k.startswith("numa_") or k in ("interleave_hit", "local_node", "other_node"):
        return (
            "NUMA allocator and access counters guide process placement: rising `numa_miss` or "
            "`other_node` with compute-bound jobs suggests binding or first-touch policy tuning."
        )

    # --- DCGM limit only ---
    if k == "DCGM_CPU_POWER_LIMIT_W":
        return (
            "Compare against `DCGM_CPU_POWER_UTIL_W` to see headroom to the cap; sustained "
            "utilization near limit with performance loss may indicate power-governed frequency."
        )

    if k.startswith("ACT_COUNT") or k.startswith("PRE_COUNT"):
        return (
            "DRAM activate/precharge style events (generation-specific); useful for validating "
            "IMC programming and comparing with CAS-based bandwidth when present."
        )

    # --- Fallback from definition keywords ---
    if "lustre" in d or "llite" in d:
        return (
            "Lustre client statistic; plot rates over time and compare nodes for skew. Combine "
            "with MDS/OSC counters when metadata or lock contention is suspected."
        )
    if "infiniband" in d or "ibta" in d:
        return (
            "Fabric counter; use deltas over time for rates, and pair error/discards with "
            "application communication phases."
        )
    if "nfs" in d or "mountstats" in d:
        return (
            "NFS client statistic; latency-oriented fields complement byte counters for "
            "diagnosing server or network-induced stalls."
        )
    if "telemetry field" in d:
        return (
            "Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and "
            "time windows, and correlate with job scheduler steps or known I/O phases."
        )

    return (
        "Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for "
        "imbalance, and correlate peaks with application logs or known I/O/communication phases. "
        "Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it."
    )


def extract_definition(body: str) -> str:
    m = re.search(r"- \*\*Definition:\*\*\s*(.+?)(?=\n- \*\*|\Z)", body, re.DOTALL)
    return (m.group(1).strip() if m else "").replace("\n", " ")


def augment(text: str) -> str:
    if CATALOG_MARKER not in text:
        return text
    pre, cat = text.split(CATALOG_MARKER, 1)
    pieces = re.split(r"(^### `[^`]+\`\n\n)", cat, flags=re.MULTILINE)
    # pieces[0] is text before first ### (usually empty); then header, body, header, body, ...
    if len(pieces) < 3:
        return text
    out: list[str] = [pre + CATALOG_MARKER + pieces[0]]
    j = 1
    while j < len(pieces):
        header = pieces[j]
        body = pieces[j + 1] if j + 1 < len(pieces) else ""
        j += 2
        km = re.match(r"^### `([^`]+)`", header)
        key = km.group(1) if km else ""
        if not key or not section_needs_diagnostic(body):
            out.append(header)
            out.append(body)
            continue
        defn = extract_definition(body)
        diag = diagnostic_for_key(key, defn)
        typ_m = re.search(
            r"^(- \*\*Typical `host_data\.type` values:\*\*[^\n]*\n)",
            body,
            re.M,
        )
        if typ_m:
            nb = (
                body[: typ_m.end()]
                + f"- **Diagnostic guidance:** {diag}\n"
                + body[typ_m.end() :]
            )
        else:
            nb = body
        out.append(header)
        out.append(nb)
    return "".join(out)


def main() -> int:
    raw = DOC.read_text(encoding="utf-8")
    if CATALOG_MARKER not in raw:
        print("Missing catalog marker in", DOC, file=__import__("sys").stderr)
        return 1
    updated = augment(raw)
    if updated == raw:
        print("No changes (already augmented or no matching sections).")
        return 0
    DOC.write_text(updated, encoding="utf-8")
    print("Updated", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
