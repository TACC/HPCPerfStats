"""Compose-backed loaded-48 continuous-refill soak at fixed ingest width."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
  DEFAULT_LOADED48_MIN_FULL_FRAC,
  DEFAULT_LOADED48_SAMPLE_S,
  DEFAULT_LOADED48_WARMUP_S,
  build_loaded48_manifest,
  corpus_hosts,
  loaded48_fixed_width,
  loaded48_hours,
  loaded48_mode_enabled,
  loaded48_occupancy_ok,
  loaded48_size_mix_templates,
  loaded48_smoke_peak_ok,
  plant_loaded48_queue_padding,
  reset_screening_state,
  summarize_width_replicates,
  write_screening_artifact,
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
      reset_shutdown_for_tests,
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

  complete_counts: list[int] = []
  stop = threading.Event()
  # Occupancy is sampled in the supervisor child (Codon stays out of pytest).
  _ = orch  # keep import for monkeypatched width above

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

  # Same small/medium/large cycle for every arm. Order is size then path.
  size_mix = loaded48_size_mix_templates(corpus_dir)
  if not size_mix:
    pytest.fail("corpus %s has no template files" % corpus_dir)
  epoch_base = int(time.time())
  plant_cursor = 0

  def _plant_mix(count: int) -> int:
    nonlocal plant_cursor
    planted = plant_loaded48_queue_padding(
        archive_dir,
        hosts,
        count=int(count),
        templates=size_mix,
        start_index=plant_cursor,
        epoch_base=epoch_base,
    )
    plant_cursor += int(count)
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
  # Deep mixed seed so large files hold slots through warmup while small
  # and medium files still complete. Copy finishes before the soak clock.
  # Refill continues the same cycle.
  _plant_mix(max(ingest_width * 8, 512))

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
    planted = 0
    # ~200 files per slot covers a 20-minute run when each file is short.
    plant_cap = int(ingest_width) * 200
    target = int(ingest_width) * 8
    while not stop.is_set():
      unfinished = _count_unfinished()
      if planted < plant_cap and unfinished < target:
        need = min(target - unfinished, plant_cap - planted)
        need = max(int(need), int(ingest_width))
        planted += _plant_mix(int(need))
      complete_counts.append(_count_complete())
      time.sleep(1.0)

  child_mod = (
      Path(__file__).resolve().parent / "loaded48_supervisor_child.py"
  )
  occ_file = Path(tempfile.mkstemp(prefix="loaded48_occ_", suffix=".json")[1])
  done_file = Path(tempfile.mkstemp(prefix="loaded48_done_", suffix=".txt")[1])
  child_env = os.environ.copy()
  child_env["HPCPERFSTATS_LOADED48_INGEST_WIDTH"] = str(int(ingest_width))
  child_env["HPCPERFSTATS_LOADED48_OCC_PATH"] = str(occ_file)
  child_env["HPCPERFSTATS_LOADED48_DONE_PATH"] = str(done_file)

  # Seed is already on disk. The wall clock starts here, not during the copy.
  t0 = time.monotonic()
  refill_thr = threading.Thread(target=_refill_loop, name="loaded48-refill", daemon=True)
  refill_thr.start()

  child = subprocess.Popen(
      [sys.executable, str(child_mod)],
      env=child_env,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.PIPE,
  )
  try:
    deadline = t0 + wall_s
    # Stop a dead run. Do not sit through the whole window at inflight 0.
    zero_check_at = t0 + 180.0
    while time.monotonic() < deadline and child.poll() is None:
      time.sleep(5.0)
      if time.monotonic() < zero_check_at:
        continue
      peak = 0
      if occ_file.is_file():
        try:
          raw = json.loads(occ_file.read_text(encoding="utf-8"))
          peak = max((int(row[1]) for row in raw), default=0)
        except (OSError, ValueError, TypeError, IndexError):
          peak = 0
      if peak > 0:
        zero_check_at = deadline + 1.0
        continue
      child.send_signal(signal.SIGTERM)
      try:
        child.wait(timeout=60.0)
      except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=15.0)
      pytest.fail(
          "stopped: ingest occupancy still 0 after 180s (peak_inflight=%d)"
          % peak
      )
    if child.poll() is None:
      # Cooperative drain via SIGTERM (handlers → request_shutdown).
      child.send_signal(signal.SIGTERM)
      try:
        child.wait(timeout=180.0)
      except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=30.0)
  finally:
    stop.set()
    if child.poll() is None:
      child.kill()
      child.wait(timeout=10.0)
    refill_thr.join(timeout=5.0)

  if child.returncode not in (0, -signal.SIGTERM) and not done_file.is_file():
    err = b""
    if child.stderr is not None:
      err = child.stderr.read() or b""
    pytest.fail(
        "loaded48 supervisor child failed rc=%s stderr=%r"
        % (child.returncode, err[:2000])
    )

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
  samples: list[tuple[float, int, int, int]] = []
  if occ_file.is_file():
    try:
      raw = json.loads(occ_file.read_text(encoding="utf-8"))
      samples = [
          (float(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in raw
      ]
    except Exception:
      samples = []
  for p in (occ_file, done_file):
    try:
      p.unlink(missing_ok=True)
    except Exception:
      pass
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
  # Short smoke at high width: prove the pool can reach near-full once
  # (warmup-inclusive peak_n). Sustained full_frac is owned by multi-hour soaks.
  occupancy_gate = "sustained"
  if (not occ_ok) and loaded48_smoke_peak_ok(
      hours=hours, peak_inflight=peak_n, width=ingest_width
  ):
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
  # Codon A/B: env divert flag (1/true = CPython arm).
  payload["codon_parse_disable"] = bool(
      os.environ.get("HPCPERFSTATS_CODON_PARSE_DISABLE", "").strip().lower()
      in {"1", "true", "yes", "on"}
  )
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
