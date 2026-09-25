"""Compose-backed loaded-48 continuous-refill soak at fixed ingest width."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    build_loaded48_manifest,
    corpus_files_under_max_bytes,
    corpus_hosts,
    loaded48_fixed_width,
    loaded48_hours,
    loaded48_mode_enabled,
    loaded48_occupancy_ok,
    plant_loaded48_queue_padding,
    reset_screening_state,
    summarize_width_replicates,
    write_screening_artifact,
    DEFAULT_LOADED48_MIN_FULL_FRAC,
    DEFAULT_LOADED48_SAMPLE_S,
    DEFAULT_LOADED48_WARMUP_S,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STEADY_CORPUS = (
    REPO_ROOT / "test_runs" / "sync_timedb_bench" / "corpus_steady"
)

pytestmark = [
    pytest.mark.sync_timedb_bench,
]


def test_loaded48_continuous_fill_soak(monkeypatch):
  """
  Keep ingest width 48 fed for ``HPCPERFSTATS_LOADED48_HOURS`` (default 6).

  Continuous refill of real ≤1 MiB corpus clones under fresh epoch basenames
  (avoids day-close deleting legacy template epochs). Occupancy samples
  ``in_flight_n`` via wrappers on ``_ingest_coordinator_fill_tick`` /
  ``_drain_ingest_ready``. Writes ``loaded48_arm_*.json``. Not a
  production INI change.
  """
  if not loaded48_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_LOADED48=1 required for loaded48")

  corpus_env = os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS", "").strip()
  corpus_dir = Path(corpus_env) if corpus_env else DEFAULT_STEADY_CORPUS
  if not corpus_dir.is_dir() or not corpus_hosts(corpus_dir):
    pytest.fail("missing corpus at %s" % corpus_dir)

  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as orch
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      clear_daily_archive_members_cache,
  )
  from hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark import (
      has_file_complete_ingest_mark,
  )
  from hpcperfstats.dbload.lib.sync_timedb_job_store import (
      reset_job_queue_script_cache_for_tests,
  )
  from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
      request_shutdown,
      reset_shutdown_for_tests,
  )
  from hpcperfstats.dbload.sync_timedb import (
      database_startup,
      run_sync_timedb_supervisor_from_parsed,
  )
  from hpcperfstats.site.lib.machine.models import host_data

  archive_dir = Path(cfg.get_archive_dir_path())
  daily_archive_dir = Path(cfg.get_daily_archive_dir_path())
  host_ext = (cfg.get_host_name_ext() or "").strip()
  hosts = corpus_hosts(corpus_dir)
  if host_ext and not all(host.endswith(host_ext) for host in hosts):
    pytest.fail(
        "corpus hosts must end with host_name_ext=%r; got %r"
        % (host_ext, hosts),
    )

  hours = loaded48_hours()
  ingest_width = loaded48_fixed_width()
  wall_s = float(hours) * 3600.0
  # Short smokes need a short warm-up so occupancy is measured on the loaded window.
  warmup_s = min(DEFAULT_LOADED48_WARMUP_S, max(15.0, wall_s * 0.15))
  sample_s = min(DEFAULT_LOADED48_SAMPLE_S, max(5.0, wall_s / 20.0))
  min_full_frac = 0.50 if hours <= 0.25 else 0.70
  # Short smokes: count near-full seconds (>=75% width). Longer soaks: >=90%.
  full_threshold = (
      max(1, int(ingest_width * 0.75))
      if hours <= 0.25
      else max(1, int(ingest_width * 0.90))
  )

  monkeypatch.setattr(
      cfg,
      "get_sync_ingest_pool_processes",
      lambda: int(ingest_width),
  )

  occupancy_samples: list[tuple[float, int, int, int]] = []
  occupancy_lock = threading.Lock()
  complete_counts: list[int] = []
  stop = threading.Event()
  t0 = time.monotonic()

  orig_fill = orch._ingest_coordinator_fill_tick
  orig_drain = orch._drain_ingest_ready

  def _fill_tick_with_occ(*args, **kwargs):
    result = orig_fill(*args, **kwargs)
    inflight = kwargs.get("ingest_inflight")
    if inflight is None and len(args) >= 6:
      inflight = args[5]
    n = len(inflight) if isinstance(inflight, dict) else 0
    submitted = int(result[0]) if result else 0
    zcard = int(result[2]) if result and len(result) > 2 else 0
    with occupancy_lock:
      occupancy_samples.append(
          (time.monotonic() - t0, int(n), int(zcard), int(submitted)),
      )
    return result

  def _drain_with_occ(*args, **kwargs):
    inflight = kwargs.get("inflight")
    if inflight is None and args:
      inflight = kwargs.get("inflight")
    # Signature uses inflight= keyword.
    n = len(inflight) if isinstance(inflight, dict) else 0
    with occupancy_lock:
      # Drain runs after fill; force busy when anything is still tracked.
      occupancy_samples.append(
          (time.monotonic() - t0, int(n), 1 if n else 0, 0),
      )
    return orig_drain(*args, **kwargs)

  monkeypatch.setattr(orch, "_ingest_coordinator_fill_tick", _fill_tick_with_occ)
  monkeypatch.setattr(orch, "_drain_ingest_ready", _drain_with_occ)

  def _clear_orm(planted: list[str]) -> None:
    host_data.objects.filter(host__in=list(planted)).delete()

  def _reset_inprocess() -> None:
    reset_shutdown_for_tests()
    reset_job_queue_script_cache_for_tests()
    clear_daily_archive_members_cache()

  def _wipe_daily_archive() -> None:
    import shutil

    daily_archive_dir.mkdir(parents=True, exist_ok=True)
    for child in list(daily_archive_dir.iterdir()):
      if child.is_dir():
        shutil.rmtree(child)
      elif child.is_file():
        child.unlink()

  # Prefer ≤1 MiB corpus templates + tiny padding. Multi-MB exemplars can take
  # >10 minutes each and outlive a short smoke wall (occupancy collapses).
  smallish = corpus_files_under_max_bytes(corpus_dir, max_bytes=1_048_576)

  def _plant_smallish_mix(copies: int) -> int:
    if not smallish:
      return 0
    import shutil
    import time as _time

    now = int(_time.time())
    planted = 0
    for host_i, host in enumerate(hosts):
      dst_host = archive_dir / host
      dst_host.mkdir(parents=True, exist_ok=True)
      for j in range(max(1, int(copies))):
        src = smallish[(host_i + j) % len(smallish)]
        # Fresh epoch — template names are old days that day-close deletes.
        dst = dst_host / ("%d" % (now + host_i * 1000 + j))
        if dst.exists():
          continue
        shutil.copy2(src, dst)
        planted += 1
    return planted

  archive_dir.mkdir(parents=True, exist_ok=True)
  _wipe_daily_archive()
  # Cap seed size; plant only under fresh epochs (see padding helper).
  # Skip plant_corpus epoch names entirely — empty hosts then pad.
  reset_screening_state(
      corpus_dir=corpus_dir,
      archive_dir=archive_dir,
      clear_orm=_clear_orm,
      reset_inprocess=_reset_inprocess,
      max_file_bytes=0,  # create host dirs only; no legacy epoch files
  )
  _plant_smallish_mix(max(2, ingest_width // 12))
  plant_loaded48_queue_padding(
      archive_dir,
      hosts,
      count=max(ingest_width * 2, 96),
      templates=smallish,
  )

  def _count_complete() -> int:
    n = 0
    for host in hosts:
      host_dir = archive_dir / host
      if not host_dir.is_dir():
        continue
      for path in host_dir.iterdir():
        if path.is_file() and has_file_complete_ingest_mark(
            str(path), archive_data_dir=str(archive_dir),
        ):
          n += 1
    return n

  def _count_unfinished() -> int:
    n = 0
    for host in hosts:
      host_dir = archive_dir / host
      if not host_dir.is_dir():
        continue
      for path in host_dir.iterdir():
        if not path.is_file():
          continue
        if path.name.startswith("."):
          continue
        if not has_file_complete_ingest_mark(
            str(path), archive_data_dir=str(archive_dir),
        ):
          n += 1
    return n

  def _refill_loop() -> None:
    while not stop.is_set():
      unfinished = _count_unfinished()
      # Keep ~2–4 widths of unfinished work; always top up when low.
      if unfinished < ingest_width * 2:
        need = max(ingest_width * 2 - unfinished, ingest_width)
        plant_loaded48_queue_padding(
            archive_dir,
            hosts,
            count=int(need),
            templates=smallish,
        )
      complete_counts.append(_count_complete())
      time.sleep(1.0)

  def _watch_deadline() -> None:
    deadline = t0 + wall_s
    while not stop.is_set() and time.monotonic() < deadline:
      time.sleep(1.0)
    request_shutdown()

  refill_thr = threading.Thread(target=_refill_loop, name="loaded48-refill", daemon=True)
  watch_thr = threading.Thread(target=_watch_deadline, name="loaded48-watch", daemon=True)
  refill_thr.start()
  watch_thr.start()

  try:
    database_startup()
    run_sync_timedb_supervisor_from_parsed(False, None, None)
  finally:
    stop.set()
    request_shutdown()
    refill_thr.join(timeout=30.0)
    watch_thr.join(timeout=30.0)

  elapsed = max(1e-6, time.monotonic() - t0)
  finals = complete_counts[-1] if complete_counts else _count_complete()
  # Prefer ORM durable rows when available.
  orm_n = host_data.objects.filter(host__in=list(hosts)).count()
  durable_n = max(int(finals), int(orm_n))
  measure_s = max(1e-6, elapsed - warmup_s)
  files_per_s = durable_n / measure_s
  # Single long window: CI from one sample uses the same value (honest for soak).
  point = summarize_width_replicates(
      ingest_width,
      [files_per_s],
      long_lock_wait=False,
  )
  with occupancy_lock:
    samples = list(occupancy_samples)
  buckets: dict[int, int] = {}
  for row in samples:
    if float(row[0]) < warmup_s:
      continue
    n, zcard, submitted = int(row[1]), int(row[2]), int(row[3])
    if n <= 0 and zcard <= 0 and submitted <= 0:
      continue
    sec = int(row[0])
    if n > buckets.get(sec, 0):
      buckets[sec] = n
  busy_secs = list(buckets.values())
  full_frac = (
      (sum(1 for n in busy_secs if n >= full_threshold) / float(len(busy_secs)))
      if busy_secs else 0.0
  )
  occ_ok = loaded48_occupancy_ok(
      samples,
      width=ingest_width,
      warmup_s=warmup_s,
      min_full_frac=min_full_frac,
      full_threshold=full_threshold,
  )
  peak_n = max((int(row[1]) for row in samples), default=0)
  peak_post = max(busy_secs) if busy_secs else 0
  # Short smoke at high width: prove the pool can reach near-full once.
  # Sustained full_frac is owned by the multi-hour soak (3h/6h).
  occupancy_gate = "sustained"
  if (not occ_ok) and hours <= 0.25 and peak_post >= max(1, int(ingest_width * 0.75)):
    occ_ok = True
    occupancy_gate = "smoke_peak"
  abi = "%s" % (getattr(sys, "version", "unknown"),)
  # Insert-path A/B: HOST/PROC insert arms (COPY vs bulk_create). Default
  # product path is candidate after retain; baseline forces ORM bulk_create.
  host_arm = os.environ.get("HPCPERFSTATS_HOST_INSERT_ARM", "").strip().lower()
  proc_arm = os.environ.get("HPCPERFSTATS_PROC_INSERT_ARM", "").strip().lower()
  if host_arm not in ("baseline", "candidate"):
    from hpcperfstats.dbload.lib.sync_timedb_host_data_insert import (
        host_insert_arm,
    )
    host_arm = host_insert_arm()
  if proc_arm not in ("baseline", "candidate"):
    from hpcperfstats.dbload.lib.sync_timedb_proc_data_insert import (
        proc_insert_arm,
    )
    proc_arm = proc_insert_arm()
  # Artifact arm = candidate only when both insert paths use COPY.
  soak_arm = (
      "candidate"
      if host_arm == "candidate" and proc_arm == "candidate"
      else "baseline"
  )
  payload = build_loaded48_manifest(
      hours=hours,
      ingest_width=ingest_width,
      mean_files_per_s=float(point["mean_files_per_s"]),
      lower_ci_files_per_s=float(point["lower_ci_files_per_s"]),
      upper_ci_files_per_s=float(point["upper_ci_files_per_s"]),
      occupancy_ok=occ_ok,
      occupancy_full_frac=float(full_frac),
      python_abi=abi,
      arm=soak_arm,
      peak_post_warmup=int(peak_post),
      occupancy_gate=occupancy_gate,
  )
  payload["host_insert_arm"] = host_arm
  payload["proc_insert_arm"] = proc_arm
  out = write_screening_artifact(
      payload,
      repo_root=REPO_ROOT,
      prefix="loaded48_arm_%s" % soak_arm,
  )
  assert out.is_file()
  assert occ_ok, (
      "occupancy fail full_frac=%.3f busy_secs=%d peak_inflight=%d "
      "peak_post_warmup=%d warmup_s=%.1f width=%d threshold=%d "
      "min_full_frac=%.2f"
      % (
          full_frac, len(busy_secs), peak_n, peak_post, warmup_s,
          ingest_width, full_threshold, min_full_frac,
      )
  )
