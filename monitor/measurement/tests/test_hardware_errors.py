"""Hardware-error catalog matches AUTHOR_NOTES §5 coverage table."""

from __future__ import annotations

import re
from pathlib import Path

from lib.hardware_errors import NVIDIA_GPU_EMITTED, catalog

NVIDIA_GPU_H = Path(__file__).resolve().parents[2] / "src" / "nvidia_gpu.h"


def test_nvidia_gpu_emitted_matches_header_keys() -> None:
    """NVIDIA_GPU_EMITTED is the nvidia_gpu.h KEYS list, in order (no retired keys)."""
    text = NVIDIA_GPU_H.read_text(encoding="utf-8")
    keys_block = text[text.index("#define KEYS"):]
    keys_block = keys_block[: keys_block.index("\n\n")]
    assert tuple(re.findall(r"\bX\((\w+),", keys_block)) == NVIDIA_GPU_EMITTED
    assert "integer_active" in NVIDIA_GPU_EMITTED
    assert "tensor_dmma_active" in NVIDIA_GPU_EMITTED
    assert "sysio_power_usage" not in NVIDIA_GPU_EMITTED
    assert "module_power_usage" not in NVIDIA_GPU_EMITTED


def test_hardware_error_catalog_matches_author_notes() -> None:
    payload = catalog()
    assert payload["first_class_count"] == 0
    names = {row["name"] for row in payload["reviewer_classes"]}
    assert "NVIDIA XIDs" in names
    assert "ECC / row-remap" in names
    assert "clocks_event_reasons" in NVIDIA_GPU_EMITTED
    assert "gpu_pcie_replay_counter" in NVIDIA_GPU_EMITTED
    xid_row = next(r for r in payload["reviewer_classes"] if r["name"] == "NVIDIA XIDs")
    assert xid_row["status"] == "not_first_class"
    throttle = next(
        r
        for r in payload["reviewer_classes"]
        if r["name"] == "thermal / power throttle reasons"
    )
    assert throttle["status"] == "partial"
