"""Compose-backed supporting-knob sweeps at fixed ingest width 48."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    KNOB_SWEEPS,
    build_knobs_manifest,
    corpus_hosts,
    knob_getter_name,
    knobs_fixed_width,
    knobs_mode_enabled,
    parse_replicates_env,
    reset_screening_state,
    run_width_matrix,
    select_knob_winner,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STEADY_CORPUS = (
    REPO_ROOT / "test_runs" / "sync_timedb_bench" / "corpus_steady"
)


pytestmark = [
    pytest.mark.sync_timedb_bench,
]


def test_supporting_knobs_sequential_sweeps(monkeypatch):
  """
  Sweep supporting knobs one factor at a time at fixed ingest width 48.

  Requires compose db/redis (``HPCPERFSTATS_COMPOSE_NETWORK=1``), the long-bench
  env flag, and ``HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1``. Does not run
  ``update_metrics`` in the timed window. Does not write production INI.
  """
  if not knobs_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1 required for knobs study")

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

  ingest_width = knobs_fixed_width()
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

  def _run_ingest_for_knobs() -> None:
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
        name="knobs-ingest-watch",
        daemon=True,
    )
    watcher.start()
    try:
      run_ingest_entire_archive_once_for_tests()
    finally:
      stop.set()

  factors: list[dict] = []
  for factor, values in KNOB_SWEEPS:
    getter = knob_getter_name(factor)
    holder = {"n": int(values[0])}

    def _set_value(n: int, _holder=holder) -> None:
      _holder["n"] = int(n)

    monkeypatch.setattr(cfg, getter, lambda _h=holder: int(_h["n"]))
    width_points = run_width_matrix(
        widths=values,
        replicates=replicates,
        file_count=file_count,
        set_width=_set_value,
        reset_and_plant=_reset_and_plant,
        run_ingest=_run_ingest_for_knobs,
        ingest_timeout_s=ingest_timeout_s,
    )
    points = [
        {
            "value": int(point["threads"]),
            "lower_ci_files_per_s": float(point["lower_ci_files_per_s"]),
            "upper_ci_files_per_s": float(point["upper_ci_files_per_s"]),
            "mean_files_per_s": float(point["mean_files_per_s"]),
            "replicates": int(point["replicates"]),
            "long_lock_wait": bool(point.get("long_lock_wait")),
        }
        for point in width_points
    ]
    winner = select_knob_winner(points)
    factors.append(
        {
            "factor": factor,
            "getter": getter,
            "values": list(values),
            "points": points,
            "winner": winner,
        }
    )

  abi = "%s" % (getattr(sys, "version", "unknown"),)
  payload = build_knobs_manifest(
      factors=factors,
      ingest_width=ingest_width,
      replicates=replicates,
      python_abi=abi,
  )
  out = write_screening_artifact(
      payload,
      repo_root=REPO_ROOT,
      prefix="knobs",
  )
  assert out.is_file()
  assert out.name.startswith("knobs_")
  assert factors, "knobs study produced no factors"
