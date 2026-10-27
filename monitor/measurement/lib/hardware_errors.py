"""Catalog Job Detail hardware-error coverage vs reviewer requests."""

from __future__ import annotations

from typing import Any

# Keys actually emitted by monitor/src/nvidia_gpu.h KEYS (keep in sync by hand
# when the collector changes; this kit does not import the C headers).
NVIDIA_GPU_EMITTED = (
    "gpu_util",
    "gpu_mem_util",
    "gpu_mem_total_mb",
    "gpu_mem_used_mb",
    "gpu_mem_free_mb",
    "gpu_sm_clock",
    "power_usage",
    "sysio_power_usage",
    "module_power_usage",
    "temperature",
    "fp64_active",
    "sm_active",
    "sm_occupancy",
    "fp32_active",
    "fp16_active",
    "tensor_active",
    "tensor_imma_active",
    "tensor_hmma_active",
    "tensor_dfma_active",
    "gpu_dram_active",
    "gpu_pcie_tx_bytes",
    "gpu_pcie_rx_bytes",
    "gpu_nvlink_tx_bytes",
    "gpu_nvlink_rx_bytes",
    "gpu_pcie_replay_counter",
    "clocks_event_reasons",
    "gpu_flops_rate",
    "gpu_mem_bw_bytes_rate",
    "gpu_flops",
    "gpu_mem_read_bytes",
    "gpu_mem_write_bytes",
    "gpu_mem_total_bytes",
    "gpu_io_link_total_bytes",
    "gpu_count",
)

REVIEWER_ERROR_CLASSES = (
    {
        "name": "NVIDIA XIDs",
        "status": "not_first_class",
        "notes": (
            "DCGM headers define XID fields; nvidia_gpu KEYS do not emit a "
            "device XID counter into host_data. Job Detail does not join XIDs."
        ),
        "related_emitted": (),
    },
    {
        "name": "ECC / row-remap",
        "status": "not_first_class",
        "notes": (
            "DCGM ECC/row-remap fields exist upstream; they are not in the "
            "nvidia_gpu KEYS list consumed by Job Detail."
        ),
        "related_emitted": (),
    },
    {
        "name": "thermal / power throttle reasons",
        "status": "partial",
        "notes": (
            "nvidia_gpu emits clocks_event_reasons (throttle bitmask) plus "
            "power_usage and temperature. That is not a full XID/ECC packet."
        ),
        "related_emitted": ("clocks_event_reasons", "power_usage", "temperature"),
    },
    {
        "name": "PCIe replay",
        "status": "partial",
        "notes": "nvidia_gpu emits gpu_pcie_replay_counter.",
        "related_emitted": ("gpu_pcie_replay_counter",),
    },
    {
        "name": "IB / link flaps",
        "status": "not_first_class",
        "notes": (
            "Fabric collectors record counters; Job Detail does not surface "
            "link-flap events as a first-class diagnosis packet."
        ),
        "related_emitted": (),
    },
    {
        "name": "kernel OOM / soft-lockup / hung-task",
        "status": "not_first_class",
        "notes": "Not joined into Job Detail from syslog/dmesg.",
        "related_emitted": (),
    },
    {
        "name": "Lustre client evictions / reconnects / timeouts",
        "status": "not_first_class",
        "notes": (
            "llite/Lustre stats may exist as host_data types; eviction events "
            "are not a first-class Job Detail surface."
        ),
        "related_emitted": (),
    },
)


def catalog() -> dict[str, Any]:
    """Return the error-packet coverage table for AUTHOR_NOTES item 5."""
    rows = []
    for item in REVIEWER_ERROR_CLASSES:
        related = list(item["related_emitted"])
        unknown = [k for k in related if k not in NVIDIA_GPU_EMITTED]
        rows.append(
            {
                "name": item["name"],
                "status": item["status"],
                "notes": item["notes"],
                "related_emitted": related,
                "related_missing_from_nvidia_keys": unknown,
            }
        )
    first_class = [r["name"] for r in rows if r["status"] == "first_class"]
    return {
        "nvidia_gpu_emitted_keys": list(NVIDIA_GPU_EMITTED),
        "reviewer_classes": rows,
        "first_class_count": len(first_class),
        "paper_sentence": (
            "Job Detail does not currently join NVIDIA XIDs, ECC/row-remap, "
            "IB/link flaps, kernel OOM/soft-lockup/hung-task, or Lustre client "
            "evictions as first-class surfaces. nvidia_gpu emits "
            "clocks_event_reasons, power/temp, PCIe replay, and util/pipe "
            "counters."
        ),
    }
