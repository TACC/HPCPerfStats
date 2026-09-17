"""Compose-backed ingest-width screening against a derived smoke corpus."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.scaling_selection import select_thread_winner
from tests.sync_timedb_benchmark.screening_runner import (
    build_screening_manifest,
    corpus_hosts,
    parse_replicates_env,
    parse_widths_env,
    reset_screening_state,
    run_width_matrix,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = (
    REPO_ROOT / "test_runs" / "sync_timedb_bench" / "corpus_smoke"
)


pytestmark = [
    pytest.mark.sync_timedb_bench,
]


def test_ingest_width_screening_smoke(monkeypatch):
  """
  Sweep ingest widths against a planted smoke corpus; write screening JSON.

  Requires compose db/redis (``HPCPERFSTATS_COMPOSE_NETWORK=1``), the long-bench
  env flag, and ``HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1``. Does not run
  ``update_metrics`` in the timed window.
  """
  corpus_env = os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS", "").strip()
  corpus_dir = Path(corpus_env) if corpus_env else DEFAULT_CORPUS
  if not corpus_dir.is_dir() or not corpus_hosts(corpus_dir):
    pytest.fail("missing smoke corpus at %s" % corpus_dir)

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

  widths = parse_widths_env()
  replicates = parse_replicates_env()
  width_holder = {"n": int(widths[0])}

  def _set_width(n: int) -> None:
    width_holder["n"] = int(n)

  monkeypatch.setattr(
      cfg,
      "get_sync_ingest_pool_processes",
      lambda: int(width_holder["n"]),
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

  def _run_ingest_for_screening() -> None:
    """
    Run once-mode ingest, then request shutdown once files are marked complete.

    Full supervisor once-mode otherwise spends minutes in day_close/discover
    after durable ingest is already done; screening only needs ingest wall.
    """
    import threading
    import time as time_mod

    from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
        request_shutdown,
    )

    stop = threading.Event()

    def _watch_ingest_complete() -> None:
      deadline = time_mod.time() + 90.0
      while not stop.is_set() and time_mod.time() < deadline:
        # Durable ingest marks: host_data rows appear as files land.
        if host_data.objects.filter(host__in=list(hosts)).count() >= file_count:
          time_mod.sleep(2.0)
          request_shutdown()
          return
        time_mod.sleep(0.5)

    watcher = threading.Thread(
        target=_watch_ingest_complete,
        name="screening-ingest-watch",
        daemon=True,
    )
    watcher.start()
    try:
      run_ingest_entire_archive_once_for_tests()
    finally:
      stop.set()

  points = run_width_matrix(
      widths=widths,
      replicates=replicates,
      file_count=file_count,
      set_width=_set_width,
      reset_and_plant=_reset_and_plant,
      run_ingest=_run_ingest_for_screening,
      ingest_timeout_s=float(
          os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_TIMEOUT_S", "180")
      ),
  )
  winner = select_thread_winner(points)
  assert winner, "screening produced no eligible winner points"

  abi = "%s" % (
      getattr(sys, "version", "unknown"),
  )
  payload = build_screening_manifest(
      points=points,
      winner=winner,
      corpus_manifest_path=corpus_dir / "manifest.json",
      widths=widths,
      replicates=replicates,
      python_abi=abi,
  )
  out = write_screening_artifact(payload, repo_root=REPO_ROOT)
  assert out.is_file()
  assert out.name.startswith("screening_")
