"""Shared helpers for sync_timedb ingest-width screening runs."""
from __future__ import annotations

import json
import os
import random
import shutil
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

SCREENING_WIDTHS: tuple[int, ...] = (
    1, 2, 4, 8, 16, 24, 32, 40, 48, 64, 80, 96,
)
KNEE_WIDTHS: tuple[int, ...] = (48, 64, 80, 96)
DEFAULT_REPLICATES = 2
DEFAULT_KNEE_REPLICATES = 5
DEFAULT_KNOBS_WIDTH = 48
DEFAULT_KNOBS_REPLICATES = 3
DEFAULT_E6_WIDTH = 48
DEFAULT_E6_REPLICATES = 5
DEFAULT_E7_WIDTH = 48
DEFAULT_E7_REPLICATES = 5
DEFAULT_CONTENTION_WIDTH = 48
DEFAULT_CONTENTION_REPLICATES = 5
DEFAULT_LOADED48_WIDTH = 48
DEFAULT_LOADED48_HOURS = 6.0
DEFAULT_LOADED48_WARMUP_S = 300.0
DEFAULT_LOADED48_SAMPLE_S = 30.0
DEFAULT_LOADED48_MIN_FULL_FRAC = 0.95
CONTENTION_WAVES: tuple[str, ...] = (
    "caches",
    "park_resume",
    "manifest_io",
    "members_shard",
    "claim_heap",
    "tar_ex",
    "thread_id",
    "pool_split",
    "log_drain",
    "discover",
    "telem_tls",
)
CONTENTION_NO_REGRESSION_WAVES: frozenset[str] = frozenset(
    {"caches", "thread_id"},
)
KNOB_SWEEPS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("sync_day_close_max_inflight", (1, 2, 4, 8, 16)),
    ("sync_archive_pool_processes", (1, 2, 4, 8)),
    ("sync_archive_members_populate_pool_processes", (1, 2, 4, 8)),
    ("sync_bulk_create_batch_size", (500, 1000, 2000, 4000)),
)
KNOB_GETTERS: dict[str, str] = {
    "sync_day_close_max_inflight": "get_sync_day_close_max_inflight",
    "sync_archive_pool_processes": "get_sync_archive_pool_processes",
    "sync_archive_members_populate_pool_processes": (
        "get_sync_archive_members_populate_pool_processes"
    ),
    "sync_bulk_create_batch_size": "get_sync_bulk_create_batch_size",
}
ARTIFACT_SUBDIR = Path("test_runs") / "sync_timedb_bench"


def knee_mode_enabled(raw: str | None = None) -> bool:
  """
  Return whether knee-confirmation mode is enabled.

  Args:
    raw (str | None): Override string; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_KNEE``.

  Returns:
    bool: True when the env/override is a truthy flag.

  Examples:
    >>> knee_mode_enabled("1")
    True
    >>> knee_mode_enabled("")
    False
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_KNEE", "")
  ).strip().lower()
  return text in ("1", "yes", "true")


def knobs_mode_enabled(raw: str | None = None) -> bool:
  """
  Return whether supporting-knob sweep mode is enabled.

  Args:
    raw (str | None): Override string; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_KNOBS``.

  Returns:
    bool: True when the env/override is a truthy flag.

  Examples:
    >>> knobs_mode_enabled("1")
    True
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_KNOBS", "")
  ).strip().lower()
  return text in ("1", "yes", "true")


def e6_mode_enabled(raw: str | None = None) -> bool:
  """
  Return whether E6 parse_feed A/B mode is enabled.

  Args:
    raw (str | None): Override string; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_E6``.

  Returns:
    bool: True when the env/override is a truthy flag.

  Examples:
    >>> e6_mode_enabled("1")
    True
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E6", "")
  ).strip().lower()
  return text in ("1", "yes", "true")


def e6_arm(raw: str | None = None) -> str:
  """
  Return the E6 A/B arm name (``baseline`` or ``candidate``).

  Args:
    raw (str | None): Override; defaults to ``HPCPERFSTATS_E6_ARM`` or
      ``baseline``.

  Returns:
    str: ``baseline`` or ``candidate``.

  Raises:
    ValueError: When the arm name is not recognized.

  Examples:
    >>> e6_arm("candidate")
    'candidate'
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_E6_ARM", "baseline")
  ).strip().lower()
  if text not in ("baseline", "candidate"):
    raise ValueError("E6 arm must be baseline|candidate: %r" % text)
  return text


def e6_fixed_width(raw: str | None = None) -> int:
  """
  Return the fixed ingest width for E6 A/B runs.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH`` or :data:`DEFAULT_E6_WIDTH`.

  Returns:
    int: Positive ingest width.

  Examples:
    >>> e6_fixed_width("48")
    48
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH", "")
  ).strip()
  if not text:
    return DEFAULT_E6_WIDTH
  value = int(text)
  if value < 1:
    raise ValueError("E6 width must be >= 1: %r" % text)
  return value


def e6_retain_candidate(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
  """
  Return True when candidate lower CI clears the E6 retain gate.

  Retain only when candidate ``lower_ci_files_per_s`` is at least the
  baseline mean **and** at least 5% above baseline ``lower_ci_files_per_s``.

  Args:
    baseline (dict[str, Any]): Baseline arm stats with mean/lower CI.
    candidate (dict[str, Any]): Candidate arm stats with mean/lower CI.

  Returns:
    bool: True when the candidate should be retained.

  Examples:
    >>> e6_retain_candidate(
    ...     baseline={"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9},
    ...     candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
    ... )
    True
  """
  base_mean = float(baseline["mean_files_per_s"])
  base_lo = float(baseline["lower_ci_files_per_s"])
  cand_lo = float(candidate["lower_ci_files_per_s"])
  return cand_lo >= base_mean and cand_lo >= base_lo * 1.05


def build_e6_ab_manifest(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    ingest_width: int,
    replicates: int,
    python_abi: str,
    retain: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
  """
  Build the paired E6 parse_feed A/B artifact payload.

  Args:
    baseline (dict[str, Any]): Baseline arm summary point.
    candidate (dict[str, Any]): Candidate arm summary point.
    ingest_width (int): Fixed ingest pool width.
    replicates (int): Replicates per arm.
    python_abi (str): Interpreter identity string.
    retain (bool): Whether the candidate cleared the retain gate.
    run_id (str | None): Optional run id.

  Returns:
    dict[str, Any]: E6 A/B artifact dictionary.

  Examples:
    >>> build_e6_ab_manifest(
    ...     baseline={"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9},
    ...     candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
    ...     ingest_width=48, replicates=5, python_abi="3.14", retain=True,
    ... )["kind"]
    'e6_parse_feed_ab'
  """
  return {
      "run_id": run_id or uuid.uuid4().hex,
      "kind": "e6_parse_feed_ab",
      "python_abi": python_abi,
      "ingest_width": int(ingest_width),
      "replicates": int(replicates),
      "baseline": dict(baseline),
      "candidate": dict(candidate),
      "retain": bool(retain),
      "note": (
          "retain True only when candidate lower CI clears the E6 gate; "
          "not a production INI change"
      ),
  }


def latest_e6_baseline_artifact(repo_root: Path) -> Path | None:
  """
  Return the newest ``e6_arm_baseline_*.json`` under the bench artifact dir.

  Args:
    repo_root (Path): HPCPerfStats checkout root.

  Returns:
    Path | None: Newest baseline arm path, or None when none exist.

  Examples:
    >>> latest_e6_baseline_artifact(Path("/tmp")) is None
    True
  """
  out_dir = repo_root / ARTIFACT_SUBDIR
  if not out_dir.is_dir():
    return None
  files = sorted(
      out_dir.glob("e6_arm_baseline_*.json"),
      key=lambda path: path.stat().st_mtime,
  )
  return files[-1] if files else None


def e7_mode_enabled(raw: str | None = None) -> bool:
  """
  Return whether E7 proc_merge/build_df A/B mode is enabled.

  Args:
    raw (str | None): Override string; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_E7``.

  Returns:
    bool: True when the env/override is a truthy flag.

  Examples:
    >>> e7_mode_enabled("1")
    True
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E7", "")
  ).strip().lower()
  return text in ("1", "yes", "true")


def e7_arm(raw: str | None = None) -> str:
  """
  Return the E7 A/B arm name (``baseline`` or ``candidate``).

  Args:
    raw (str | None): Override; defaults to ``HPCPERFSTATS_E7_ARM`` or
      ``baseline``.

  Returns:
    str: ``baseline`` or ``candidate``.

  Raises:
    ValueError: When the arm name is not recognized.

  Examples:
    >>> e7_arm("candidate")
    'candidate'
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_E7_ARM", "baseline")
  ).strip().lower()
  if text not in ("baseline", "candidate"):
    raise ValueError("E7 arm must be baseline|candidate: %r" % text)
  return text


def e7_fixed_width(raw: str | None = None) -> int:
  """
  Return the fixed ingest width for E7 A/B runs.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH`` or :data:`DEFAULT_E7_WIDTH`.

  Returns:
    int: Positive ingest width.

  Examples:
    >>> e7_fixed_width("48")
    48
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH", "")
  ).strip()
  if not text:
    return DEFAULT_E7_WIDTH
  value = int(text)
  if value < 1:
    raise ValueError("E7 width must be >= 1: %r" % text)
  return value


def build_e7_ab_manifest(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    ingest_width: int,
    replicates: int,
    python_abi: str,
    retain: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
  """
  Build the paired E7 proc_merge/build_df A/B artifact payload.

  Args:
    baseline (dict[str, Any]): Baseline arm summary point.
    candidate (dict[str, Any]): Candidate arm summary point.
    ingest_width (int): Fixed ingest pool width.
    replicates (int): Replicates per arm.
    python_abi (str): Interpreter identity string.
    retain (bool): Whether the candidate cleared the retain gate.
    run_id (str | None): Optional run id.

  Returns:
    dict[str, Any]: E7 A/B artifact dictionary.

  Examples:
    >>> build_e7_ab_manifest(
    ...     baseline={"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9},
    ...     candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
    ...     ingest_width=48, replicates=5, python_abi="3.14", retain=True,
    ... )["kind"]
    'e7_proc_build_ab'
  """
  return {
      "run_id": run_id or uuid.uuid4().hex,
      "kind": "e7_proc_build_ab",
      "python_abi": python_abi,
      "ingest_width": int(ingest_width),
      "replicates": int(replicates),
      "baseline": dict(baseline),
      "candidate": dict(candidate),
      "retain": bool(retain),
      "note": (
          "retain True only when candidate lower CI clears the E6/E7 gate; "
          "not a production INI change"
      ),
  }


def latest_e7_baseline_artifact(repo_root: Path) -> Path | None:
  """
  Return the newest ``e7_arm_baseline_*.json`` under the bench artifact dir.

  Args:
    repo_root (Path): Repository root containing ``test_runs/``.

  Returns:
    Path | None: Newest baseline artifact, or ``None``.

  Examples:
    >>> latest_e7_baseline_artifact(Path("/tmp")) is None
    True
  """
  out_dir = repo_root / ARTIFACT_SUBDIR
  if not out_dir.is_dir():
    return None
  files = sorted(
      out_dir.glob("e7_arm_baseline_*.json"),
      key=lambda path: path.stat().st_mtime,
  )
  return files[-1] if files else None


def loaded48_mode_enabled(raw: str | None = None) -> bool:
  """
  Return whether loaded-48 continuous-fill soak mode is enabled.

  Args:
    raw (str | None): Override; defaults to ``HPCPERFSTATS_SYNC_TIMEDB_LOADED48``.

  Returns:
    bool: True when the env/override is a truthy flag.

  Examples:
    >>> loaded48_mode_enabled("1")
    True
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_LOADED48", "")
  ).strip().lower()
  return text in ("1", "yes", "true")


def loaded48_hours(raw: str | None = None) -> float:
  """
  Return soak wall hours for loaded-48 (default 6.0).

  Args:
    raw (str | None): Override; defaults to ``HPCPERFSTATS_LOADED48_HOURS``.

  Returns:
    float: Positive hours.

  Raises:
    ValueError: When hours are not > 0.

  Examples:
    >>> loaded48_hours("0.1")
    0.1
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_LOADED48_HOURS", "")
  ).strip()
  if not text:
    return DEFAULT_LOADED48_HOURS
  value = float(text)
  if value <= 0:
    raise ValueError("loaded48 hours must be > 0: %r" % text)
  return value


def loaded48_fixed_width(raw: str | None = None) -> int:
  """
  Return fixed ingest width for loaded-48 (default 48).

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH``.

  Returns:
    int: Positive width.

  Examples:
    >>> loaded48_fixed_width("48")
    48
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH", "")
  ).strip()
  if not text:
    return DEFAULT_LOADED48_WIDTH
  value = int(text)
  if value < 1:
    raise ValueError("loaded48 width must be >= 1: %r" % text)
  return value


def loaded48_occupancy_ok(
    samples: Sequence[tuple[Any, ...]],
    *,
    width: int,
    warmup_s: float = DEFAULT_LOADED48_WARMUP_S,
    min_full_frac: float = DEFAULT_LOADED48_MIN_FULL_FRAC,
    full_threshold: int | None = None,
) -> bool:
  """
  Return whether post-warmup busy occupancy stays full enough.

  Samples are bucketed by whole second; each second contributes the **max**
  ``in_flight_n`` among busy ticks that second (avoids drain/fill jitter
  diluting the fraction). Idle empty-queue seconds are skipped.

  Each sample is ``(elapsed_s, in_flight_n)`` or
  ``(elapsed_s, in_flight_n, zcard, submitted)``.

  Args:
    samples (Sequence[tuple[Any, ...]]): Occupancy rows (see above).
    width (int): Target ingest pool size (used when ``full_threshold`` is None).
    warmup_s (float): Seconds excluded from the fraction.
    min_full_frac (float): Required fraction of busy seconds at/above threshold.
    full_threshold (int | None): Per-second max ``in_flight`` needed to count
      as full; defaults to ``width``.

  Returns:
    bool: True when enough busy post-warmup seconds meet the threshold.

  Examples:
    >>> loaded48_occupancy_ok([(0.0, 10), (400.0, 48), (430.0, 48)], width=48)
    True
    >>> loaded48_occupancy_ok(
    ...   [(400.0, 0, 0, 0), (430.0, 48, 100, 5)], width=48,
    ... )
    True
  """
  need = int(width if full_threshold is None else full_threshold)
  buckets: dict[int, int] = {}
  for row in samples:
    elapsed = float(row[0])
    if elapsed < float(warmup_s):
      continue
    n = int(row[1])
    if len(row) > 2:
      zcard = int(row[2])
      submitted = int(row[3]) if len(row) > 3 else 0
    else:
      zcard = 1 if n > 0 else 0
      submitted = 0
    if n <= 0 and zcard <= 0 and submitted <= 0:
      continue
    sec = int(elapsed)
    prev = buckets.get(sec, 0)
    if n > prev:
      buckets[sec] = n
  if not buckets:
    return False
  vals = list(buckets.values())
  full = sum(1 for n in vals if int(n) >= need)
  return (full / float(len(vals))) >= float(min_full_frac)


def build_loaded48_manifest(
    *,
    hours: float,
    ingest_width: int,
    mean_files_per_s: float,
    lower_ci_files_per_s: float,
    upper_ci_files_per_s: float,
    occupancy_ok: bool,
    occupancy_full_frac: float,
    python_abi: str,
    arm: str = "baseline",
    run_id: str | None = None,
    peak_post_warmup: int | None = None,
    occupancy_gate: str | None = None,
) -> dict[str, Any]:
  """
  Build a loaded-48 soak artifact payload.

  Args:
    hours (float): Configured soak hours.
    ingest_width (int): Ingest pool width.
    mean_files_per_s (float): Durable files/s over the measurement window.
    lower_ci_files_per_s (float): Lower CI on files/s.
    upper_ci_files_per_s (float): Upper CI on files/s.
    occupancy_ok (bool): Occupancy oracle result.
    occupancy_full_frac (float): Post-warmup full-slot fraction.
    python_abi (str): Interpreter version string.
    arm (str): ``baseline`` or ``candidate``.
    run_id (str | None): Optional run id.
    peak_post_warmup (int | None): Max busy in_flight after warmup.
    occupancy_gate (str | None): ``sustained`` / ``smoke_peak`` / None.

  Returns:
    dict[str, Any]: Artifact dictionary.

  Examples:
    >>> build_loaded48_manifest(
    ...     hours=0.1, ingest_width=48, mean_files_per_s=0.05,
    ...     lower_ci_files_per_s=0.04, upper_ci_files_per_s=0.06,
    ...     occupancy_ok=True, occupancy_full_frac=0.99, python_abi="3.14",
    ... )["kind"]
    'loaded48_arm'
  """
  payload: dict[str, Any] = {
      "kind": "loaded48_arm",
      "arm": arm,
      "hours": float(hours),
      "ingest_width": int(ingest_width),
      "mean_files_per_s": float(mean_files_per_s),
      "lower_ci_files_per_s": float(lower_ci_files_per_s),
      "upper_ci_files_per_s": float(upper_ci_files_per_s),
      "occupancy_ok": bool(occupancy_ok),
      "occupancy_full_frac": float(occupancy_full_frac),
      "python_abi": python_abi,
      "run_id": run_id or uuid.uuid4().hex,
      "note": (
          "continuous refill; retain A/B uses same lower-CI gate as E6/E7; "
          "not a production INI change"
      ),
  }
  if peak_post_warmup is not None:
    payload["peak_post_warmup"] = int(peak_post_warmup)
  if occupancy_gate is not None:
    payload["occupancy_gate"] = str(occupancy_gate)
  return payload


def contention_mode_enabled(raw: str | None = None) -> bool:
  """
  Return whether FT contention A/B mode is enabled.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_CONTENTION``.

  Returns:
    bool: True when the env/override is a truthy flag.

  Examples:
    >>> contention_mode_enabled("1")
    True
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_CONTENTION", "")
  ).strip().lower()
  return text in ("1", "yes", "true")


def contention_arm(raw: str | None = None) -> str:
  """
  Return the contention A/B arm name.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_CONTENTION_ARM`` or ``baseline``.

  Returns:
    str: ``baseline`` or ``candidate``.

  Raises:
    ValueError: When the arm name is not recognized.

  Examples:
    >>> contention_arm("candidate")
    'candidate'
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_CONTENTION_ARM", "baseline")
  ).strip().lower()
  if text not in ("baseline", "candidate"):
    raise ValueError("contention arm must be baseline|candidate: %r" % text)
  return text


def contention_wave(raw: str | None = None) -> str:
  """
  Return the contention A/B wave id.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_CONTENTION_WAVE``.

  Returns:
    str: Wave id from :data:`CONTENTION_WAVES`.

  Raises:
    ValueError: When the wave id is missing or unknown.

  Examples:
    >>> contention_wave("caches")
    'caches'
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_CONTENTION_WAVE", "")
  ).strip().lower()
  if text not in CONTENTION_WAVES:
    raise ValueError(
        "contention wave must be one of %s: %r"
        % (",".join(CONTENTION_WAVES), text),
    )
  return text


def contention_fixed_width(raw: str | None = None) -> int:
  """
  Return the fixed ingest width for contention A/B runs.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH`` or
      :data:`DEFAULT_CONTENTION_WIDTH`.

  Returns:
    int: Positive ingest width.

  Examples:
    >>> contention_fixed_width("48")
    48
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH", "")
  ).strip()
  if not text:
    return DEFAULT_CONTENTION_WIDTH
  value = int(text)
  if value < 1:
    raise ValueError("contention width must be >= 1: %r" % text)
  return value


def contention_retain_candidate(
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    wave: str,
) -> bool:
  """
  Return True when the candidate clears the wave-specific retain gate.

  Throughput waves use the E6 gate. No-regression waves (caches, thread_id)
  retain unless candidate lower CI falls more than 5% below baseline lower CI.

  Args:
    baseline (dict[str, Any]): Baseline arm stats.
    candidate (dict[str, Any]): Candidate arm stats.
    wave (str): Contention wave id.

  Returns:
    bool: True when the candidate should be retained.

  Examples:
    >>> contention_retain_candidate(
    ...     baseline={"mean_files_per_s": 1.0, "lower_ci_files_per_s": 1.0},
    ...     candidate={"mean_files_per_s": 0.99, "lower_ci_files_per_s": 0.96},
    ...     wave="caches",
    ... )
    True
  """
  base_lo = float(baseline["lower_ci_files_per_s"])
  cand_lo = float(candidate["lower_ci_files_per_s"])
  if wave in CONTENTION_NO_REGRESSION_WAVES:
    return cand_lo >= base_lo * 0.95
  return e6_retain_candidate(baseline=baseline, candidate=candidate)


def build_contention_ab_manifest(
    *,
    wave: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    ingest_width: int,
    replicates: int,
    python_abi: str,
    retain: bool,
    run_id: str | None = None,
) -> dict[str, Any]:
  """
  Build a contention wave A/B artifact payload.

  Args:
    wave (str): Wave id.
    baseline (dict[str, Any]): Baseline arm summary.
    candidate (dict[str, Any]): Candidate arm summary.
    ingest_width (int): Fixed ingest pool width.
    replicates (int): Replicates per arm.
    python_abi (str): Interpreter identity string.
    retain (bool): Whether the candidate cleared the retain gate.
    run_id (str | None): Optional run id.

  Returns:
    dict[str, Any]: Contention A/B artifact dictionary.

  Examples:
    >>> build_contention_ab_manifest(
    ...     wave="caches",
    ...     baseline={"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9},
    ...     candidate={"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9},
    ...     ingest_width=48, replicates=5, python_abi="3.14", retain=True,
    ... )["kind"]
    'contention_caches_ab'
  """
  return {
      "run_id": run_id or uuid.uuid4().hex,
      "kind": "contention_%s_ab" % wave,
      "wave": wave,
      "python_abi": python_abi,
      "ingest_width": int(ingest_width),
      "replicates": int(replicates),
      "baseline": dict(baseline),
      "candidate": dict(candidate),
      "retain": bool(retain),
      "gate": (
          "no_regression"
          if wave in CONTENTION_NO_REGRESSION_WAVES
          else "throughput"
      ),
      "note": (
          "contention wave A/B; not a production INI change"
      ),
  }


def latest_contention_baseline_artifact(
    repo_root: Path,
    wave: str,
) -> Path | None:
  """
  Return the newest baseline artifact for ``wave``.

  Args:
    repo_root (Path): HPCPerfStats checkout root.
    wave (str): Wave id.

  Returns:
    Path | None: Newest baseline path, or None when none exist.

  Examples:
    >>> latest_contention_baseline_artifact(Path("/tmp"), "caches") is None
    True
  """
  out_dir = repo_root / ARTIFACT_SUBDIR
  if not out_dir.is_dir():
    return None
  pattern = "contention_%s_arm_baseline_*.json" % wave
  files = sorted(
      out_dir.glob(pattern),
      key=lambda path: path.stat().st_mtime,
  )
  return files[-1] if files else None


def knobs_fixed_width(raw: str | None = None) -> int:
  """
  Return the fixed ingest width for supporting-knob sweeps.

  Args:
    raw (str | None): Override; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH`` or
      :data:`DEFAULT_KNOBS_WIDTH`.

  Returns:
    int: Positive ingest width.

  Examples:
    >>> knobs_fixed_width("48")
    48
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH", "")
  ).strip()
  if not text:
    return DEFAULT_KNOBS_WIDTH
  value = int(text)
  if value < 1:
    raise ValueError("knobs width must be >= 1: %r" % text)
  return value


def select_knob_winner(points: Sequence[dict[str, Any]]) -> dict[str, Any]:
  """
  Pick the smallest knob value within 5% of the peak mean files/s.

  Args:
    points (Sequence[dict[str, Any]]): Per-value study points with
      ``value`` and ``mean_files_per_s``.

  Returns:
    dict[str, Any]: Winner point (empty when ``points`` is empty).

  Examples:
    >>> select_knob_winner(
    ...     [{"value": 2, "mean_files_per_s": 1.0},
    ...      {"value": 8, "mean_files_per_s": 1.02}],
    ... )["value"]
    2
  """
  if not points:
    return {}
  peak = max(float(point["mean_files_per_s"]) for point in points)
  threshold = peak * 0.95
  eligible = [
      point for point in points
      if float(point["mean_files_per_s"]) >= threshold
  ]
  return min(eligible, key=lambda point: int(point["value"]))


def build_knobs_manifest(
    *,
    factors: Sequence[dict[str, Any]],
    ingest_width: int,
    replicates: int,
    python_abi: str,
    run_id: str | None = None,
) -> dict[str, Any]:
  """
  Build a supporting-knobs campaign artifact payload.

  Args:
    factors (Sequence[dict[str, Any]]): Per-factor sweep results.
    ingest_width (int): Fixed ingest pool width for the study.
    replicates (int): Replicates per knob value.
    python_abi (str): Interpreter identity string.
    run_id (str | None): Optional run id.

  Returns:
    dict[str, Any]: Knobs artifact dictionary.

  Examples:
    >>> build_knobs_manifest(
    ...     factors=[], ingest_width=48, replicates=3, python_abi="3.14",
    ... )["kind"]
    'supporting_knobs'
  """
  return {
      "run_id": run_id or uuid.uuid4().hex,
      "kind": "supporting_knobs",
      "python_abi": python_abi,
      "ingest_width": int(ingest_width),
      "replicates": int(replicates),
      "factors": list(factors),
      "note": (
          "knob winners are campaign candidates only; not a production "
          "INI recommendation"
      ),
  }


def parse_widths_env(raw: str | None = None) -> tuple[int, ...]:
  """
  Parse a comma-separated ingest-width list from the environment.

  Args:
    raw (str | None): Override string; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS`` or the campaign default matrix.

  Returns:
    tuple[int, ...]: Positive ingest widths in ascending order.

  Raises:
    ValueError: When a token is not a positive integer.

  Examples:
    >>> parse_widths_env("1,4,8")
    (1, 4, 8)
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS", "")
  ).strip()
  if not text:
    return KNEE_WIDTHS if knee_mode_enabled() else SCREENING_WIDTHS
  widths: list[int] = []
  for token in text.split(","):
    token = token.strip()
    if not token:
      continue
    value = int(token)
    if value < 1:
      raise ValueError("ingest width must be >= 1: %r" % token)
    widths.append(value)
  if not widths:
    return KNEE_WIDTHS if knee_mode_enabled() else SCREENING_WIDTHS
  return tuple(sorted(set(widths)))


def parse_replicates_env(raw: str | None = None) -> int:
  """
  Parse the screening replicate count from the environment.

  Args:
    raw (str | None): Override string; defaults to
      ``HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES`` or
      :data:`DEFAULT_REPLICATES`.

  Returns:
    int: Replicate count (>= 2 for campaign screening).

  Raises:
    ValueError: When the value is not an integer >= 1.

  Examples:
    >>> parse_replicates_env("3")
    3
  """
  text = (
      raw
      if raw is not None
      else os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES", "")
  ).strip()
  if not text:
    if contention_mode_enabled():
      return DEFAULT_CONTENTION_REPLICATES
    if e7_mode_enabled():
      return DEFAULT_E7_REPLICATES
    if e6_mode_enabled():
      return DEFAULT_E6_REPLICATES
    if knobs_mode_enabled():
      return DEFAULT_KNOBS_REPLICATES
    return (
        DEFAULT_KNEE_REPLICATES if knee_mode_enabled() else DEFAULT_REPLICATES
    )
  value = int(text)
  if value < 1:
    raise ValueError("replicates must be >= 1")
  return value


def knob_getter_name(factor: str) -> str:
  """
  Map a supporting-knob factor key to its ``conf_parser`` getter name.

  Args:
    factor (str): Campaign knob key (INI-style name).

  Returns:
    str: ``get_sync_*`` attribute name on ``conf_parser``.

  Raises:
    KeyError: When ``factor`` is not in :data:`KNOB_GETTERS`.

  Examples:
    >>> knob_getter_name("sync_day_close_max_inflight")
    'get_sync_day_close_max_inflight'
  """
  return KNOB_GETTERS[factor]


def summarize_knob_replicates(
    value: int,
    files_per_s_samples: Sequence[float],
    *,
    long_lock_wait: bool = False,
) -> dict[str, Any]:
  """
  Build a supporting-knob study point from replicate throughput samples.

  Args:
    value (int): Knob setting for the point.
    files_per_s_samples (Sequence[float]): Per-replicate durable files/s.
    long_lock_wait (bool): When true, mark the point ineligible to win.

  Returns:
    dict[str, Any]: Point dict with ``value`` and ``mean_files_per_s``.

  Examples:
    >>> summarize_knob_replicates(8, [1.0, 1.2])["value"]
    8
  """
  point = summarize_width_replicates(
      value,
      files_per_s_samples,
      long_lock_wait=long_lock_wait,
  )
  point["value"] = int(point.pop("threads"))
  return point


def corpus_hosts(corpus_dir: Path) -> list[str]:
  """
  List derived host directory names under ``corpus_dir``.

  Args:
    corpus_dir (Path): Derived corpus root containing host subdirectories.

  Returns:
    list[str]: Sorted host directory basenames (excludes ``manifest.json``).

  Examples:
    >>> corpus_hosts(Path("/tmp/missing"))  # doctest: +SKIP
    []
  """
  if not corpus_dir.is_dir():
    return []
  return sorted(
      path.name
      for path in corpus_dir.iterdir()
      if path.is_dir() and not path.name.startswith(".")
  )


def plant_corpus_into_archive(
    corpus_dir: Path,
    archive_dir: Path,
    *,
    max_file_bytes: int | None = None,
) -> list[str]:
  """
  Copy derived host trees into ``archive_dir`` for ingest discovery.

  Args:
    corpus_dir (Path): Derived corpus root (host/epoch leaves).
    archive_dir (Path): Destination ``PIPELINE.archive_dir``.
    max_file_bytes (int | None): When set, skip files larger than this many
      bytes (host dirs are still created). Used by loaded-48 soaks so the
      initial seed does not plant multi‑MB exemplars.

  Returns:
    list[str]: Host directory names planted.

  Raises:
    FileNotFoundError: When ``corpus_dir`` does not exist.
    ValueError: When no host directories are present.

  Examples:
    >>> plant_corpus_into_archive(Path("/tmp/c"), Path("/tmp/a"))  # doctest: +SKIP
  """
  if not corpus_dir.is_dir():
    raise FileNotFoundError("corpus_dir missing: %s" % corpus_dir)
  hosts = corpus_hosts(corpus_dir)
  if not hosts:
    raise ValueError("corpus_dir has no host directories: %s" % corpus_dir)
  archive_dir.mkdir(parents=True, exist_ok=True)
  for host in hosts:
    src = corpus_dir / host
    dst = archive_dir / host
    if dst.exists():
      shutil.rmtree(dst)
    if max_file_bytes is None:
      shutil.copytree(src, dst)
      continue
    dst.mkdir(parents=True, exist_ok=True)
    for path in src.iterdir():
      if not path.is_file():
        continue
      try:
        if path.stat().st_size > int(max_file_bytes):
          continue
      except OSError:
        continue
      shutil.copy2(path, dst / path.name)
  return hosts


def corpus_template_files_by_size_tier(
    corpus_dir: Path,
) -> tuple[list[Path], list[Path], list[Path]]:
  """
  Partition corpus host files into small / medium / large size terciles.

  Args:
    corpus_dir (Path): Derived corpus root (host/epoch leaves).

  Returns:
    tuple[list[Path], list[Path], list[Path]]: ``(small, medium, large)``
      template paths (may be empty lists when few files exist).

  Examples:
    >>> corpus_template_files_by_size_tier(Path("/tmp/missing"))
    ([], [], [])
  """
  files: list[Path] = []
  for host in corpus_hosts(corpus_dir):
    host_dir = corpus_dir / host
    files.extend(path for path in host_dir.iterdir() if path.is_file())
  if not files:
    return [], [], []
  files.sort(key=lambda path: path.stat().st_size)
  n = len(files)
  if n == 1:
    return files[:], [], []
  if n == 2:
    return [files[0]], [], [files[1]]
  t1 = max(1, n // 3)
  t2 = max(t1 + 1, (2 * n) // 3)
  return files[:t1], files[t1:t2], files[t2:]


def refill_corpus_epochs(
    corpus_dir: Path,
    archive_dir: Path,
    *,
    copies_per_host: int = 2,
    epoch_prefix: str | None = None,
    mix_size_tiers: bool = True,
) -> int:
  """
  Plant additional unique epoch files from corpus templates into ``archive_dir``.

  Does not wipe existing host trees. New epoch basenames use ``epoch_prefix``
  (default: time-based) so discovery sees fresh unfinished work for pool fill.
  When ``mix_size_tiers`` is True (default), each call clones a round-robin mix
  of **small / medium / large** corpus files (byte-size terciles) rather than
  uniform per-host copies — loaded-48 soaks require heterogeneous lengths.

  Args:
    corpus_dir (Path): Template corpus root.
    archive_dir (Path): Live archive root.
    copies_per_host (int): Target clones per host this call (minimum mix size
      is ``3 * host_count`` when tiers are mixed).
    epoch_prefix (str | None): Optional basename prefix; default monotonic hex.
    mix_size_tiers (bool): When True, plant from small/medium/large terciles.

  Returns:
    int: Number of new files planted.

  Examples:
    >>> refill_corpus_epochs(Path("/tmp/c"), Path("/tmp/a"))  # doctest: +SKIP
    0
  """
  hosts = corpus_hosts(corpus_dir)
  if not hosts:
    return 0
  prefix = epoch_prefix or ("%d_%s" % (int(time.time()), uuid.uuid4().hex[:8]))
  planted = 0
  archive_dir.mkdir(parents=True, exist_ok=True)
  if mix_size_tiers:
    small, medium, large = corpus_template_files_by_size_tier(corpus_dir)
    tiers = [t for t in (small, medium, large) if t]
    if not tiers:
      return 0
    # At least one file from each non-empty tier per host each tick.
    per_host = max(int(copies_per_host), len(tiers))
    tier_i = 0
    for host_i, host in enumerate(hosts):
      dst_host = archive_dir / host
      dst_host.mkdir(parents=True, exist_ok=True)
      for j in range(per_host):
        tier = tiers[tier_i % len(tiers)]
        tier_i += 1
        src = tier[(host_i + j) % len(tier)]
        dst = dst_host / (
            "%s_%s_t%d_%s" % (prefix, j, tier_i % len(tiers), src.name)
        )
        if dst.exists():
          continue
        shutil.copy2(src, dst)
        planted += 1
    return planted

  for host in hosts:
    src_host = corpus_dir / host
    dst_host = archive_dir / host
    dst_host.mkdir(parents=True, exist_ok=True)
    templates = sorted(
        path for path in src_host.iterdir() if path.is_file()
    )
    if not templates:
      continue
    for i in range(max(1, int(copies_per_host))):
      src = templates[i % len(templates)]
      dst = dst_host / ("%s_%s_%s" % (prefix, i, src.name))
      if dst.exists():
        continue
      shutil.copy2(src, dst)
      planted += 1
  return planted


def plant_loaded48_queue_padding(
    archive_dir: Path,
    hosts: Sequence[str],
    *,
    count: int,
    templates: Sequence[Path] | None = None,
    payload_bytes: int | None = None,
) -> int:
  """
  Plant additional unfinished epoch files so ingest width can stay loaded.

  Prefer cloning real parsable ``templates`` under **fresh epoch basenames**
  (``int(time.time())`` + index). Reusing template epoch names (for example
  ``1700000000``) lets day-close treat them as old closed days and delete the
  unfinished work mid-soak — that starved the 6h H1 run after warm-up.

  Raw synthetic blobs quarantine immediately (``initial_timestamp_not_found``)
  and do **not** keep workers busy — only used when ``templates`` is empty and
  ``payload_bytes`` is set (unit tests).

  Args:
    archive_dir (Path): Live archive root.
    hosts (Sequence[str]): Host directory names to plant under.
    count (int): Number of files to plant this call.
    templates (Sequence[Path] | None): Real stats files to clone.
    payload_bytes (int | None): Synthetic size when no templates (tests only).

  Returns:
    int: Files planted.

  Examples:
    >>> plant_loaded48_queue_padding(Path("/tmp/a"), ["h.ext"], count=2)
    2
  """
  if not hosts or int(count) <= 0:
    return 0
  archive_dir.mkdir(parents=True, exist_ok=True)
  now = int(time.time())
  planted = 0
  tmpl = [Path(p) for p in (templates or ()) if Path(p).is_file()]
  if tmpl:
    for i in range(int(count)):
      host = hosts[i % len(hosts)]
      src = tmpl[i % len(tmpl)]
      dst_host = archive_dir / host
      dst_host.mkdir(parents=True, exist_ok=True)
      # Fresh epoch basename — never reuse template epoch (day-close delete).
      dst = dst_host / ("%d" % (now + i))
      if dst.exists():
        dst = dst_host / ("%d_%d" % (now, i))
      if dst.exists():
        continue
      shutil.copy2(src, dst)
      planted += 1
    return planted
  if payload_bytes is None:
    return 0
  size = max(1, int(payload_bytes))
  for i in range(int(count)):
    host = hosts[i % len(hosts)]
    dst_host = archive_dir / host
    dst_host.mkdir(parents=True, exist_ok=True)
    dst = dst_host / ("synth_%d_%d" % (now, i))
    if dst.exists():
      continue
    dst.write_bytes(b"x" * size)
    planted += 1
  return planted


def corpus_files_under_max_bytes(
    corpus_dir: Path,
    *,
    max_bytes: int = 1_048_576,
) -> list[Path]:
  """
  List corpus template files at or under ``max_bytes``.

  Loaded-48 soaks must not plant multi‑MB ``corpus_steady`` exemplars on every
  refill tick — those can take many minutes each and outlive a short smoke wall.

  Args:
    corpus_dir (Path): Template corpus root.
    max_bytes (int): Inclusive size cap (default 1 MiB).

  Returns:
    list[Path]: Matching files sorted by size then path.

  Examples:
    >>> corpus_files_under_max_bytes(Path("/tmp/c"), max_bytes=100)  # doctest: +SKIP
    []
  """
  out: list[Path] = []
  if not corpus_dir.is_dir():
    return out
  for host_dir in sorted(p for p in corpus_dir.iterdir() if p.is_dir()):
    for path in sorted(p for p in host_dir.iterdir() if p.is_file()):
      try:
        if path.stat().st_size <= int(max_bytes):
          out.append(path)
      except OSError:
        continue
  return out


def clear_archive_sidecars(archive_dir: Path) -> None:
  """
  Remove sync_timedb job-store / members sidecars under ``archive_dir``.

  Args:
    archive_dir (Path): Archive root that may contain sidecar files/dirs.

  Returns:
    None

  Examples:
    >>> clear_archive_sidecars(Path("/tmp/archive"))  # doctest: +SKIP
  """
  job_store = archive_dir / ".sync_timedb_job_store.json"
  if job_store.exists():
    job_store.unlink()
  members = archive_dir / ".sync_timedb_archive_members"
  if members.exists():
    shutil.rmtree(members)
  for path in archive_dir.glob(".sync_timedb_*"):
    if path.is_file():
      path.unlink()
    elif path.is_dir():
      shutil.rmtree(path)


def reset_screening_state(
    *,
    corpus_dir: Path,
    archive_dir: Path,
    clear_orm: Callable[[Sequence[str]], None] | None = None,
    reset_inprocess: Callable[[], None] | None = None,
    max_file_bytes: int | None = None,
) -> list[str]:
  """
  Wipe archive hosts, re-seed corpus trees, and clear sidecars between runs.

  Args:
    corpus_dir (Path): Derived corpus root.
    archive_dir (Path): Destination archive directory.
    clear_orm (Callable | None): Optional callback receiving planted hosts to
      delete DB rows for those hosts.
    reset_inprocess (Callable | None): Optional callback to reset in-process
      sync_timedb caches/flags for tests.
    max_file_bytes (int | None): Forwarded to ``plant_corpus_into_archive``.

  Returns:
    list[str]: Planted host names.

  Examples:
    >>> reset_screening_state(  # doctest: +SKIP
    ...     corpus_dir=Path("/tmp/c"), archive_dir=Path("/tmp/a"),
    ... )
  """
  archive_dir.mkdir(parents=True, exist_ok=True)
  for child in list(archive_dir.iterdir()):
    if child.name.startswith("."):
      continue
    if child.is_dir():
      shutil.rmtree(child)
    elif child.is_file():
      child.unlink()
  hosts = plant_corpus_into_archive(
      corpus_dir, archive_dir, max_file_bytes=max_file_bytes,
  )
  clear_archive_sidecars(archive_dir)
  if clear_orm is not None:
    clear_orm(hosts)
  if reset_inprocess is not None:
    reset_inprocess()
  return hosts


def summarize_width_replicates(
    threads: int,
    files_per_s_samples: Sequence[float],
    *,
    long_lock_wait: bool = False,
) -> dict[str, Any]:
  """
  Build a scaling-study point from replicate throughput samples.

  Args:
    threads (int): Ingest pool width for the point.
    files_per_s_samples (Sequence[float]): Per-replicate durable files/s.
    long_lock_wait (bool): When true, mark the point ineligible to win.

  Returns:
    dict[str, Any]: Point dict for :func:`select_thread_winner`.

  Raises:
    ValueError: When no samples are provided.

  Examples:
    >>> summarize_width_replicates(8, [1.0, 1.2])["threads"]
    8
  """
  if not files_per_s_samples:
    raise ValueError("files_per_s_samples must not be empty")
  values = [float(sample) for sample in files_per_s_samples]
  lower = min(values)
  upper = max(values)
  return {
      "threads": int(threads),
      "lower_ci_files_per_s": lower,
      "upper_ci_files_per_s": upper,
      "mean_files_per_s": float(statistics.fmean(values)),
      "replicates": len(values),
      "long_lock_wait": bool(long_lock_wait),
  }


def build_screening_manifest(
    *,
    points: Sequence[dict[str, Any]],
    winner: dict[str, Any],
    corpus_manifest_path: Path | None,
    widths: Sequence[int],
    replicates: int,
    python_abi: str,
    run_id: str | None = None,
) -> dict[str, Any]:
  """
  Assemble the machine-readable screening artifact payload.

  Args:
    points (Sequence[dict[str, Any]]): Per-width study points.
    winner (dict[str, Any]): Output of ``select_thread_winner``.
    corpus_manifest_path (Path | None): Path to the derived corpus manifest.
    widths (Sequence[int]): Widths swept in this run.
    replicates (int): Replicates per width.
    python_abi (str): Interpreter identity string (for example ``3.14t``).
    run_id (str | None): Optional run id; generated when omitted.

  Returns:
    dict[str, Any]: Screening artifact dictionary.

  Examples:
    >>> build_screening_manifest(
    ...     points=[], winner={}, corpus_manifest_path=None,
    ...     widths=(1,), replicates=2, python_abi="3.14",
    ... )["replicates"]
    2
  """
  kind = (
      "ingest_width_knee"
      if knee_mode_enabled()
      else "ingest_width_screening"
  )
  note = (
      "winner is a knee candidate; not a production INI recommendation"
      if kind == "ingest_width_knee"
      else (
          "winner is a screening hint only; not a production INI "
          "recommendation"
      )
  )
  return {
      "run_id": run_id or uuid.uuid4().hex,
      "kind": kind,
      "python_abi": python_abi,
      "widths": list(widths),
      "replicates": int(replicates),
      "corpus_manifest": (
          str(corpus_manifest_path) if corpus_manifest_path else None
      ),
      "points": list(points),
      "winner": dict(winner),
      "note": note,
  }


def write_screening_artifact(
    payload: dict[str, Any],
    *,
    repo_root: Path,
    prefix: str | None = None,
) -> Path:
  """
  Write ``payload`` under ``test_runs/sync_timedb_bench/{prefix}_*.json``.

  Args:
    payload (dict[str, Any]): Screening manifest from
      :func:`build_screening_manifest`.
    repo_root (Path): HPCPerfStats checkout root.
    prefix (str | None): Filename prefix; defaults to ``knee`` when knee
      mode is enabled, otherwise ``screening``.

  Returns:
    Path: Absolute path of the written JSON file.

  Examples:
    >>> write_screening_artifact({}, repo_root=Path("/tmp"))  # doctest: +SKIP
  """
  out_dir = repo_root / ARTIFACT_SUBDIR
  out_dir.mkdir(parents=True, exist_ok=True)
  run_id = str(payload.get("run_id") or uuid.uuid4().hex)
  name_prefix = prefix
  if name_prefix is None:
    name_prefix = "knee" if knee_mode_enabled() else "screening"
  out_path = out_dir / ("%s_%s.json" % (name_prefix, run_id))
  out_path.write_text(
      json.dumps(payload, indent=2, sort_keys=True) + "\n",
      encoding="utf-8",
  )
  return out_path.resolve()


def run_width_matrix(
    *,
    widths: Sequence[int],
    replicates: int,
    file_count: int,
    set_width: Callable[[int], None],
    reset_and_plant: Callable[[], None],
    run_ingest: Callable[[], None],
    seed: int = 17,
    ingest_timeout_s: float = 300.0,
) -> list[dict[str, Any]]:
  """
  Execute randomized width×replicate ingest timing and return study points.

  Args:
    widths (Sequence[int]): Ingest widths to sweep.
    replicates (int): Replicates per width.
    file_count (int): Number of corpus files used for files/s.
    set_width (Callable[[int], None]): Apply the ingest pool width.
    reset_and_plant (Callable[[], None]): Reset archive/DB before a replicate.
    run_ingest (Callable[[], None]): Run one full-archive ingest once.
    seed (int): RNG seed for replicate order shuffling.
    ingest_timeout_s (float): Fail a replicate if ``run_ingest`` exceeds this
      many seconds (default 300). Prevents silent multi-hour hangs when
      ``run_once`` never reaches idle exit.

  Returns:
    list[dict[str, Any]]: Summarized points suitable for winner selection.

  Raises:
    ValueError: When ``file_count`` is not positive.
    TimeoutError: When a replicate exceeds ``ingest_timeout_s``.

  Examples:
    >>> run_width_matrix(  # doctest: +SKIP
    ...     widths=(1,), replicates=1, file_count=1,
    ...     set_width=lambda _n: None,
    ...     reset_and_plant=lambda: None,
    ...     run_ingest=lambda: None,
    ... )
  """
  from concurrent.futures import ThreadPoolExecutor
  from concurrent.futures import TimeoutError as FuturesTimeout

  if file_count < 1:
    raise ValueError("file_count must be >= 1")
  schedule: list[tuple[int, int]] = []
  for width in widths:
    for replicate in range(replicates):
      schedule.append((int(width), replicate))
  rng = random.Random(seed)
  rng.shuffle(schedule)

  samples: dict[int, list[float]] = {int(width): [] for width in widths}
  for width, replicate in schedule:
    print(
        "screening replicate width=%s replicate=%s/%s"
        % (width, replicate + 1, replicates),
        flush=True,
    )
    set_width(width)
    reset_and_plant()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
      future = pool.submit(run_ingest)
      try:
        future.result(timeout=float(ingest_timeout_s))
      except FuturesTimeout as exc:
        try:
          from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
              request_shutdown,
          )
          request_shutdown()
        except Exception:
          pass
        raise TimeoutError(
            "ingest timed out after %.1fs (width=%s replicate=%s)"
            % (ingest_timeout_s, width, replicate),
        ) from exc
    elapsed = max(time.perf_counter() - started, 1e-9)
    print(
        "screening replicate done width=%s replicate=%s elapsed_s=%.2f"
        % (width, replicate + 1, elapsed),
        flush=True,
    )
    samples[int(width)].append(float(file_count) / elapsed)

  return [
      summarize_width_replicates(width, samples[int(width)])
      for width in widths
  ]
