"""Optional byte-normalized diff against committed shm golden files."""
from __future__ import annotations

from pathlib import Path

from lib.tacc_system_profiles import golden_basename


def _read_norm(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def compare_golden_shm(
    shm_dir: Path,
    golden_dir: Path,
    slug: str,
    *,
    enable_slow_tier: bool,
    profile: str | None = None,
) -> list[str]:
    errors: list[str] = []
    pairs = [("schema", golden_basename("schema", slug, profile))]
    if enable_slow_tier:
        pairs.append(("fast", golden_basename("fast", slug, profile)))
    pairs.append(("full", golden_basename("full", slug, profile)))

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
