"""Compose-backed FT contention wave A/B at fixed ingest width 48."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    build_contention_ab_manifest,
    contention_arm,
    contention_fixed_width,
    contention_mode_enabled,
    contention_retain_candidate,
    contention_wave,
    corpus_hosts,
    latest_contention_baseline_artifact,
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


def test_contention_ab_arm(monkeypatch):
  """
  Measure one contention wave arm at width 48; merge A/B when candidate.

  Requires compose db/redis, ``HPCPERFSTATS_SYNC_TIMEDB_CONTENTION=1``,
  ``HPCPERFSTATS_CONTENTION_WAVE=<id>``, and
  ``HPCPERFSTATS_CONTENTION_ARM=baseline|candidate``. Baseline writes
  ``contention_<wave>_arm_baseline_*.json``. Candidate loads the latest
  baseline and writes ``contention_<wave>_ab_*.json`` with a retain
  decision. Does not write production INI.
  """
  if not contention_mode_enabled():
    pytest.fail(
        "HPCPERFSTATS_SYNC_TIMEDB_CONTENTION=1 required for contention study",
    )

  wave = contention_wave()
  arm = contention_arm()
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
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      reset_parse_stage_timing,
  )
  from hpcperfstats.dbload.lib.sync_timedb_store_lock_timing import (
      reset_store_lock_timing,
      snapshot_store_lock_timing,
  )
  from hpcperfstats.dbload.sync_timedb import (
      _reset_ingest_write_timing,
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

  ingest_width = contention_fixed_width()
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

  def _run_ingest_for_contention() -> None:
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
    reset_store_lock_timing(enabled=True)
    # Wave telem_tls A/B measures campaign-lock cost with telem forced on.
    force_campaign_telem = wave == "telem_tls"
    if force_campaign_telem:
      reset_parse_stage_timing(enabled=True)
      _reset_ingest_write_timing(enabled=True)

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
        name="contention-ingest-watch",
        daemon=True,
    )
    watcher.start()
    try:
      run_ingest_entire_archive_once_for_tests()
    finally:
      stop.set()
      store_snap = snapshot_store_lock_timing()
      arm_point_store["members_store_wait_s"] = float(
          store_snap.get("members_store_wait_s", 0.0),
      )
      arm_point_store["job_store_wait_s"] = float(
          store_snap.get("job_store_wait_s", 0.0),
      )
      reset_store_lock_timing(enabled=False)
      if force_campaign_telem:
        reset_parse_stage_timing(enabled=False)
        _reset_ingest_write_timing(enabled=False)

  arm_point_store: dict[str, float] = {
      "members_store_wait_s": 0.0,
      "job_store_wait_s": 0.0,
  }
  width_points = run_width_matrix(
      widths=(ingest_width,),
      replicates=replicates,
      file_count=file_count,
      set_width=lambda _n: None,
      reset_and_plant=_reset_and_plant,
      run_ingest=_run_ingest_for_contention,
      ingest_timeout_s=ingest_timeout_s,
  )
  assert len(width_points) == 1
  arm_point = {
      "arm": arm,
      "wave": wave,
      "threads": int(width_points[0]["threads"]),
      "lower_ci_files_per_s": float(width_points[0]["lower_ci_files_per_s"]),
      "upper_ci_files_per_s": float(width_points[0]["upper_ci_files_per_s"]),
      "mean_files_per_s": float(width_points[0]["mean_files_per_s"]),
      "replicates": int(width_points[0]["replicates"]),
      "long_lock_wait": bool(width_points[0].get("long_lock_wait")),
      "members_store_wait_s": float(arm_point_store["members_store_wait_s"]),
      "job_store_wait_s": float(arm_point_store["job_store_wait_s"]),
  }
  abi = "%s" % (getattr(sys, "version", "unknown"),)

  if arm == "baseline":
    out = write_screening_artifact(
        {
            "kind": "contention_%s_arm_baseline" % wave,
            "wave": wave,
            "python_abi": abi,
            "ingest_width": ingest_width,
            "replicates": replicates,
            "baseline": arm_point,
            "note": (
                "contention baseline arm only; pair with candidate for "
                "retain gate"
            ),
        },
        repo_root=REPO_ROOT,
        prefix="contention_%s_arm_baseline" % wave,
    )
    assert out.is_file()
    assert out.name.startswith("contention_%s_arm_baseline_" % wave)
    return

  baseline_path = latest_contention_baseline_artifact(REPO_ROOT, wave)
  if baseline_path is None:
    pytest.fail(
        "candidate arm requires contention_%s_arm_baseline_*.json; "
        "run baseline first" % wave,
    )
  baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
  baseline_point = dict(baseline_payload["baseline"])
  retain = contention_retain_candidate(
      baseline=baseline_point,
      candidate=arm_point,
      wave=wave,
  )
  payload = build_contention_ab_manifest(
      wave=wave,
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
      prefix="contention_%s_ab" % wave,
  )
  assert out.is_file()
  assert out.name.startswith("contention_%s_ab_" % wave)
  print(
      "contention_ab wave=%s retain=%s baseline_mean=%s candidate_lo=%s path=%s"
      % (
          wave,
          retain,
          baseline_point.get("mean_files_per_s"),
          arm_point.get("lower_ci_files_per_s"),
          out,
      ),
      flush=True,
  )
