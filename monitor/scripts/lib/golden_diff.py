"""Optional byte-normalized diff against committed shm golden files."""
from __future__ import annotations

from pathlib import Path

from lib.tacc_system_profiles import golden_basename


def _read_norm(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def golden_file_pairs(
    slug: str,
    *,
    enable_slow_tier: bool,
    profile: str | None = None,
) -> list[tuple[str, str]]:
    """Return (shm_name, golden_basename) pairs for the slug/profile."""
    pairs = [("schema", golden_basename("schema", slug, profile))]
    if enable_slow_tier:
        pairs.append(("fast", golden_basename("fast", slug, profile)))
    pairs.append(("full", golden_basename("full", slug, profile)))
    return pairs


def count_matching_goldens(
    golden_dir: Path,
    slug: str,
    *,
    enable_slow_tier: bool,
    profile: str | None = None,
) -> int:
    """How many expected golden basenames exist under golden_dir."""
    if not golden_dir.is_dir():
        return 0
    n = 0
    for _, golden_name in golden_file_pairs(
        slug, enable_slow_tier=enable_slow_tier, profile=profile
    ):
        if (golden_dir / golden_name).is_file():
            n += 1
    return n


def resolve_optin_golden_dir(
    *,
    monitor_dir: Path,
    slug: str,
    golden_dir_env: str | None,
    golden_check: bool,
    enable_slow_tier: bool,
    profile: str | None = None,
) -> Path | None:
    """Resolve golden directory only when operator opts in.

    Opt-in: GOLDEN_CHECK=1, GOLDEN_DIR=auto, or GOLDEN_DIR=/path.
    Returns None when not opted in, or when no matching shm_* files exist.
    """
    env = (golden_dir_env or "").strip()
    if not golden_check and not env:
        return None

    if env and env != "auto":
        root = Path(env)
    else:
        root = Path(monitor_dir) / "tests" / "expected"

    if not root.is_dir():
        return None
    if count_matching_goldens(
        root, slug, enable_slow_tier=enable_slow_tier, profile=profile
    ) < 1:
        return None
    return root.resolve()


def compare_golden_shm(
    shm_dir: Path,
    golden_dir: Path,
    slug: str,
    *,
    enable_slow_tier: bool,
    profile: str | None = None,
) -> tuple[list[str], int]:
    """Compare live shm files to goldens.

    Returns (errors, compared_count). compared_count is the number of golden
    files that existed and were compared (missing goldens are skipped).
    """
    errors: list[str] = []
    compared = 0
    for shm_name, golden_name in golden_file_pairs(
        slug, enable_slow_tier=enable_slow_tier, profile=profile
    ):
        live = shm_dir / shm_name
        golden = golden_dir / golden_name
        if not golden.is_file():
            continue
        compared += 1
        if not live.is_file():
            errors.append(f"FAIL golden {shm_name}: missing live file")
            continue
        if _read_norm(live) != _read_norm(golden):
            errors.append(f"FAIL golden {golden_name}: content differs from {shm_name}")
    return errors, compared
