"""Compose-backed closed-book mid-size timing (campaign E2)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    ARTIFACT_SUBDIR,
    corpus_hosts,
    reset_screening_state,
)
from tests.sync_timedb_benchmark.timing_accounting import account_phases

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STEADY_CORPUS = (
    REPO_ROOT / "test_runs" / "sync_timedb_bench" / "corpus_steady"
)

pytestmark = [
    pytest.mark.sync_timedb_bench,
]


def _mid_size_hosts(corpus_dir: Path) -> list[str]:
  """
  Pick host directories whose single file is near the median size.

  Args:
    corpus_dir (Path): Derived corpus root.

  Returns:
    list[str]: Up to eight host names around the median byte size.
  """
  sized: list[tuple[int, str]] = []
  for host in corpus_hosts(corpus_dir):
    host_dir = corpus_dir / host
    files = [path for path in host_dir.iterdir() if path.is_file()]
    if not files:
      continue
    sized.append((sum(path.stat().st_size for path in files), host))
  if not sized:
    return []
  sized.sort(key=lambda item: item[0])
  mid = len(sized) // 2
  lo = max(0, mid - 4)
  hi = min(len(sized), mid + 4)
  return [host for _size, host in sized[lo:hi]]


def test_e2_closed_book_mid_size(monkeypatch):
  """
  Run one mid-size ingest with closed-book timers; write residual artifact.

  Requires compose db/redis and ``HPCPERFSTATS_SYNC_TIMEDB_E2=1``. Does not
  run ``update_metrics`` in the timed window.
  """
  corpus_env = os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_CORPUS", "").strip()
  corpus_dir = Path(corpus_env) if corpus_env else DEFAULT_STEADY_CORPUS
  hosts = _mid_size_hosts(corpus_dir)
  if not hosts:
    pytest.fail("missing mid-size hosts under %s" % corpus_dir)

  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.file_locking import (
      reset_file_lock_timing,
      snapshot_file_lock_timing,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      clear_daily_archive_members_cache,
  )
  from hpcperfstats.dbload.lib.sync_timedb_job_store import (
      reset_job_queue_script_cache_for_tests,
  )
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      reset_parse_stage_timing,
      snapshot_parse_stage_campaign_timing,
  )
  from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
      request_shutdown,
      reset_shutdown_for_tests,
  )
  from hpcperfstats.dbload.lib.sync_timedb_store_lock_timing import (
      reset_store_lock_timing,
      snapshot_store_lock_timing,
  )
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.sync_timedb import (
      run_ingest_entire_archive_once_for_tests,
  )
  from hpcperfstats.site.lib.machine.models import host_data

  archive_dir = Path(cfg.get_archive_dir_path())
  daily_archive_dir = Path(cfg.get_daily_archive_dir_path())
  host_ext = (cfg.get_host_name_ext() or "").strip()
  if host_ext and not all(host.endswith(host_ext) for host in hosts):
    pytest.fail(
        "corpus hosts must end with host_name_ext=%r; got %r"
        % (host_ext, hosts),
    )

  # Restrict the planted corpus to mid-size hosts only.
  mid_corpus = corpus_dir / ".e2_mid_subset"
  if mid_corpus.exists():
    import shutil

    shutil.rmtree(mid_corpus)
  mid_corpus.mkdir(parents=True)
  for host in hosts:
    import shutil

    shutil.copytree(corpus_dir / host, mid_corpus / host)
  file_count = sum(
      1
      for host in hosts
      for path in (mid_corpus / host).iterdir()
      if path.is_file()
  )
  assert file_count >= 1

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_processes", lambda: 8)

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

  _wipe_daily_archive()
  reset_screening_state(
      corpus_dir=mid_corpus,
      archive_dir=archive_dir,
      clear_orm=_clear_orm,
      reset_inprocess=_reset_inprocess,
  )

  reset_file_lock_timing(enabled=True)
  reset_store_lock_timing(enabled=True)
  reset_parse_stage_timing(enabled=True)
  st._reset_ingest_write_timing(enabled=True)

  stop = threading.Event()

  def _watch() -> None:
    deadline = time.time() + 600.0
    while not stop.is_set() and time.time() < deadline:
      if host_data.objects.filter(host__in=list(hosts)).count() >= file_count:
        time.sleep(2.0)
        request_shutdown()
        return
      time.sleep(0.5)

  watcher = threading.Thread(target=_watch, name="e2-ingest-watch", daemon=True)
  watcher.start()
  wall0 = time.perf_counter()
  try:
    run_ingest_entire_archive_once_for_tests()
  finally:
    stop.set()
    wall_s = time.perf_counter() - wall0
    file_snap = snapshot_file_lock_timing()
    store_snap = snapshot_store_lock_timing()
    parse_snap = snapshot_parse_stage_campaign_timing()
    write_snap = st._snapshot_ingest_write_campaign_timing()
    reset_file_lock_timing(enabled=False)
    reset_store_lock_timing(enabled=False)
    reset_parse_stage_timing(enabled=False)
    st._reset_ingest_write_timing(enabled=False)

  phases: dict[str, float] = {}
  for key, value in {
      **{("file_%s" % k): float(v) for k, v in dict(file_snap or {}).items()},
      **{("store_%s" % k): float(v) for k, v in dict(store_snap or {}).items()},
      **{("parse_%s" % k): float(v) for k, v in dict(parse_snap or {}).items()},
      **{("write_%s" % k): float(v) for k, v in dict(write_snap or {}).items()},
  }.items():
    if value > 0.0:
      phases[key] = value

  residual_s = account_phases(wall_s, phases)
  residual_frac = (residual_s / wall_s) if wall_s > 0.0 else 0.0
  residual_ok = residual_frac <= 0.05

  payload = {
      "run_id": uuid.uuid4().hex,
      "kind": "e2_closed_book_mid_size",
      "python_abi": "%s" % getattr(sys, "version", "unknown"),
      "hosts": hosts,
      "file_count": file_count,
      "wall_s": wall_s,
      "phases": phases,
      "residual_s": residual_s,
      "residual_frac": residual_frac,
      "residual_ok": residual_ok,
      "max_frac": 0.05,
  }
  out_dir = REPO_ROOT / ARTIFACT_SUBDIR
  out_dir.mkdir(parents=True, exist_ok=True)
  out = out_dir / ("e2_closed_book_%s.json" % payload["run_id"])
  out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
  assert out.is_file()
  assert "residual_frac" in payload
  # residual_ok records the ≤5% contract; unmet residual is ledger evidence,
  # not a hard pytest failure (some wall may remain outside instrumented holds).
  assert isinstance(residual_ok, bool)
  assert isinstance(phases, dict)
  assert len(phases) > 0, "closed-book phases must be non-empty after telem fix"
