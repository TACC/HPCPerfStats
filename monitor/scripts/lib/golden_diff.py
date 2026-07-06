"""Optional byte-normalized diff against committed shm golden files."""
from __future__ import annotations

from pathlib import Path


def _read_norm(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def compare_golden_shm(
    shm_dir: Path,
    golden_dir: Path,
    slug: str,
    *,
    enable_slow_tier: bool,
) -> list[str]:
    errors: list[str] = []
    pairs = [("schema", f"shm_schema_{slug}.txt")]
    if enable_slow_tier:
        pairs.append(("fast", f"shm_fast_{slug}.txt"))
    pairs.append(("full", f"shm_full_{slug}.txt"))

    for shm_name, golden_name in pairs:
        live = shm_dir / shm_name
        golden = golden_dir / golden_name
        if not golden.is_file():
            continue
        if not live.is_file():
            errors.append(f"FAIL golden {shm_name}: missing live file")
            continue
        if _read_norm(live) != _read_norm(golden):
            errors.append(f"FAIL golden {golden_name}: content differs from {shm_name}")
    return errors
