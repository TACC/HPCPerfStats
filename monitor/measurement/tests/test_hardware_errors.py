"""Hardware-error catalog matches AUTHOR_NOTES §5 coverage table."""

from __future__ import annotations

from lib.hardware_errors import NVIDIA_GPU_EMITTED, catalog


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
