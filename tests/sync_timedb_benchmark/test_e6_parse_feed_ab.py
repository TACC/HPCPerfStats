"""Compose-backed E6 parse_feed A/B at fixed ingest width 48."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    build_e6_ab_manifest,
    corpus_hosts,
    e6_arm,
    e6_fixed_width,
    e6_mode_enabled,
    e6_retain_candidate,
    latest_e6_baseline_artifact,
    parse_replicates_env,
    reset_screening_state,
    run_width_matrix,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STEADY_CORPUS = (
    REPO_ROOT / "test_runs" / "sync_timedb_bench" / "corpus_steady"
)


pytestmark = [
    pytest.mark.sync_timedb_bench,
]


def test_e6_parse_feed_ab_arm(monkeypatch):
  """
  Measure one E6 arm at width 48; merge A/B when arm is candidate.

  Requires compose db/redis, ``HPCPERFSTATS_SYNC_TIMEDB_E6=1``, and
  ``HPCPERFSTATS_E6_ARM=baseline|candidate``. Baseline writes
  ``e6_arm_baseline_*.json``. Candidate loads the latest baseline, writes
  ``e6_parse_feed_ab_*.json`` with a retain decision. Does not write
  production INI.
  """
  if not e6_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_E6=1 required for E6 study")

  arm = e6_arm()
  corpus_env = os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS", "").strip()
  corpus_dir = Path(corpus_env) if corpus_env else DEFAULT_STEADY_CORPUS
  if not corpus_dir.is_dir() or not corpus_hosts(corpus_dir):
    pytest.fail("missing corpus at %s" % corpus_dir)

  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      clear_daily_archive_members_cache,
  )
  from hpcperfstats.dbload.lib.sync_timedb_job_store import (
      reset_job_queue_script_cache_for_tests,
  )
  from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
      reset_shutdown_for_tests,
  )
  from hpcperfstats.dbload.sync_timedb import (
      run_ingest_entire_archive_once_for_tests,
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

  file_count = sum(
      1
      for host in hosts
      for path in (corpus_dir / host).iterdir()
      if path.is_file()
  )
  assert file_count >= 1

  ingest_width = e6_fixed_width()
  replicates = parse_replicates_env()
  monkeypatch.setattr(
      cfg,
      "get_sync_ingest_pool_processes",
      lambda: int(ingest_width),
  )

  def _clear_orm(planted: list[str]) -> None:
    host_data.objects.filter(host__in=list(planted)).delete()

  def _reset_inprocess() -> None:
    reset_shutdown_for_tests()
    reset_job_queue_script_cache_for_tests()
    clear_daily_archive_members_cache()

  def _wipe_daily_archive() -> None:
    daily_archive_dir.mkdir(parents=True, exist_ok=True)
    for child in list(daily_archive_dir.iterdir()):
      if child.is_dir():
        import shutil
        shutil.rmtree(child)
      elif child.is_file():
        child.unlink()

  def _reset_and_plant() -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    _wipe_daily_archive()
    reset_screening_state(
        corpus_dir=corpus_dir,
        archive_dir=archive_dir,
        clear_orm=_clear_orm,
        reset_inprocess=_reset_inprocess,
    )

  ingest_timeout_s = float(
      os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S", "3600")
  )

  def _run_ingest_for_e6() -> None:
    import threading
    import time as time_mod

    from hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark import (
        has_file_complete_ingest_mark,
    )
    from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
        request_shutdown,
    )

    stop = threading.Event()
    archive_root = str(archive_dir)

    def _planted_paths() -> list[str]:
      paths: list[str] = []
      for host in hosts:
        host_dir = archive_dir / host
        if not host_dir.is_dir():
          continue
        for path in host_dir.iterdir():
          if path.is_file():
            paths.append(str(path))
      return paths

    def _durable_complete() -> bool:
      if host_data.objects.filter(host__in=list(hosts)).count() >= file_count:
        return True
      planted = _planted_paths()
      if len(planted) < file_count:
        return False
      return all(
          has_file_complete_ingest_mark(path, archive_data_dir=archive_root)
          for path in planted
      )

    def _watch_ingest_complete() -> None:
      deadline = time_mod.time() + float(ingest_timeout_s)
      while not stop.is_set() and time_mod.time() < deadline:
        if _durable_complete():
          time_mod.sleep(2.0)
          request_shutdown()
          return
        time_mod.sleep(0.5)

    watcher = threading.Thread(
        target=_watch_ingest_complete,
        name="e6-ingest-watch",
        daemon=True,
    )
    watcher.start()
    try:
      run_ingest_entire_archive_once_for_tests()
    finally:
      stop.set()

  width_points = run_width_matrix(
      widths=(ingest_width,),
      replicates=replicates,
      file_count=file_count,
      set_width=lambda _n: None,
      reset_and_plant=_reset_and_plant,
      run_ingest=_run_ingest_for_e6,
      ingest_timeout_s=ingest_timeout_s,
  )
  assert len(width_points) == 1
  arm_point = {
      "arm": arm,
      "threads": int(width_points[0]["threads"]),
      "lower_ci_files_per_s": float(width_points[0]["lower_ci_files_per_s"]),
      "upper_ci_files_per_s": float(width_points[0]["upper_ci_files_per_s"]),
      "mean_files_per_s": float(width_points[0]["mean_files_per_s"]),
      "replicates": int(width_points[0]["replicates"]),
      "long_lock_wait": bool(width_points[0].get("long_lock_wait")),
  }
  abi = "%s" % (getattr(sys, "version", "unknown"),)

  if arm == "baseline":
    out = write_screening_artifact(
        {
            "kind": "e6_arm_baseline",
            "python_abi": abi,
            "ingest_width": ingest_width,
            "replicates": replicates,
            "baseline": arm_point,
            "note": "E6 baseline arm only; pair with candidate for retain gate",
        },
        repo_root=REPO_ROOT,
        prefix="e6_arm_baseline",
    )
    assert out.is_file()
    assert out.name.startswith("e6_arm_baseline_")
    return

  baseline_path = latest_e6_baseline_artifact(REPO_ROOT)
  if baseline_path is None:
    pytest.fail(
        "candidate arm requires e6_arm_baseline_*.json; run baseline first"
    )
  baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
  baseline_point = dict(baseline_payload["baseline"])
  retain = e6_retain_candidate(baseline=baseline_point, candidate=arm_point)
  payload = build_e6_ab_manifest(
      baseline=baseline_point,
      candidate=arm_point,
      ingest_width=ingest_width,
      replicates=replicates,
      python_abi=abi,
      retain=retain,
  )
  out = write_screening_artifact(
      payload,
      repo_root=REPO_ROOT,
      prefix="e6_parse_feed_ab",
  )
  assert out.is_file()
  assert out.name.startswith("e6_parse_feed_ab_")
  print(
      "e6_ab retain=%s baseline_mean=%s candidate_lo=%s path=%s"
      % (
          retain,
          baseline_point.get("mean_files_per_s"),
          arm_point.get("lower_ci_files_per_s"),
          out,
      ),
      flush=True,
  )
