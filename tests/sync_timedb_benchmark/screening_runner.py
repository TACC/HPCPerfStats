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
    return (
        DEFAULT_KNEE_REPLICATES if knee_mode_enabled() else DEFAULT_REPLICATES
    )
  value = int(text)
  if value < 1:
    raise ValueError("replicates must be >= 1")
  return value


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
) -> list[str]:
  """
  Copy derived host trees into ``archive_dir`` for ingest discovery.

  Args:
    corpus_dir (Path): Derived corpus root (host/epoch leaves).
    archive_dir (Path): Destination ``PIPELINE.archive_dir``.

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
    shutil.copytree(src, dst)
  return hosts


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
  hosts = plant_corpus_into_archive(corpus_dir, archive_dir)
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
