"""Host unit tests for sync_timedb queue orchestrator cutover (slice 4)."""
from __future__ import annotations

import inspect
import os
from multiprocessing import Process

import pytest

from hpcperfstats.dbload.lib.sync_timedb_archive_dir_lock import (
    exclusive_archive_dir_flock,
    orchestrator_lock_path,
)
import hpcperfstats.dbload.sync_timedb as st


def test_exclusive_archive_dir_flock_rejects_second_nonblocking_holder(tmp_path):
  """Second non-blocking flock must fail while the first holder is live."""
  archive = tmp_path / "archive"
  archive.mkdir()
  lock_path = orchestrator_lock_path(str(archive))
  assert lock_path.endswith(".sync_timedb_orchestrator.fnctl.lock")

  with exclusive_archive_dir_flock(str(archive), blocking=True):
    with pytest.raises(OSError):
      with exclusive_archive_dir_flock(str(archive), blocking=False):
        pass


def _child_try_nonblocking(archive_dir: str, result_path: str) -> None:
  try:
    with exclusive_archive_dir_flock(archive_dir, blocking=False):
      open(result_path, "w", encoding="utf-8").write("acquired")
  except OSError:
    open(result_path, "w", encoding="utf-8").write("contended")


def test_exclusive_archive_dir_flock_cross_process(tmp_path):
  """Cross-process non-blocking acquire fails while parent holds the flock."""
  archive = tmp_path / "archive"
  archive.mkdir()
  result = tmp_path / "result.txt"
  with exclusive_archive_dir_flock(str(archive), blocking=True):
    proc = Process(
        target=_child_try_nonblocking,
        args=(str(archive), str(result)),
    )
    proc.start()
    proc.join(timeout=10)
    assert proc.exitcode == 0
    assert result.read_text(encoding="utf-8") == "contended"


def test_from_parsed_wires_queue_orchestrator():
  """Production entry must call the greenfield orchestrator."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_from_parsed)
  assert "run_sync_timedb_queue_orchestrator" in source
  assert "run_sync_timedb_supervisor_loop(" not in source


def test_supervisor_loop_symbol_removed():
  """Retired supervisor_loop must not remain as an importable dual path."""
  assert not hasattr(st, "run_sync_timedb_supervisor_loop")


def test_sliding_window_ingest_enqueues_append_while_other_inflight():
  """First completed ingest enqueues append while another ingest stays inflight."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      return ("/a", True, True, 0.1, {})

  class _Pending:
    def ready(self):
      return False

  class _Client:
    def __init__(self):
      self.appends = []

    def rpush(self, key, value):
      self.appends.append((key, value))
      return 1

    def eval(self, *a, **k):
      return 1

    def evalsha(self, *a, **k):
      return 1

    def script_load(self, s):
      return "sha"

  client = _Client()
  inflight = {
      "/a|1|1": _Ready(),
      "/b|2|2": _Pending(),
  }
  leases = {"/a|1|1": "tok-a", "/b|2|2": "tok-b"}
  done = qo._drain_ingest_ready(
      client,
      inflight=inflight,
      leases=leases,
      tgz_archive_dir="/daily",
  )
  assert done == 1
  assert "/b|2|2" in inflight
  assert "/a|1|1" not in inflight
  assert any(
      k.endswith(":queue:append") or jq.JOB_KIND_APPEND in str(k)
      for k, _v in client.appends
  ) or any(v == "/a" for _k, v in client.appends)


def test_day_close_job_tar_drops_when_sealed_and_no_raw(tmp_path, monkeypatch):
  """day_close must seal then tar-drop when zst exists and closed raw is gone."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  day = "2020-01-01"
  tar = daily / ("%s.tar" % day)
  zst = daily / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  zst.write_bytes(b"zst")

  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
      lambda *a, **k: None,
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def apply_batch_delete(self, _tar_path):
      return 0

    def has_closed_raw_on_disk(self, _tar_path):
      return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      day,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "tar_dropped"
  assert not tar.exists()
  assert zst.exists()


def test_day_close_dc01_stage_order(tmp_path, monkeypatch):
  """DC-01 order: pre-seal verify → dedupe → seal → post-seal → delete → tar-drop."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  day = "2020-01-01"
  tar = daily / ("%s.tar" % day)
  zst = daily / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  zst.write_bytes(b"zst")
  stages = []

  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
      lambda *a, **k: stages.append("seal"),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.dedupe_tar_keep_largest_file_per_member",
      lambda *a, **k: stages.append("dedupe") or True,
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def run_pre_seal_verify_sync(self, _tar_path):
      stages.append("pre_seal")
      return True

    def run_post_seal_verify_sync(self, _tar_path):
      stages.append("post_seal")
      return True

    def apply_batch_delete(self, _tar_path):
      stages.append("delete")
      return 0

    def has_closed_raw_on_disk(self, _tar_path):
      return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      day,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "tar_dropped"
  assert stages == ["pre_seal", "dedupe", "seal", "post_seal", "delete"]


def test_enqueue_day_closes_for_daily_dir_calls_reconstruct(tmp_path, monkeypatch):
  """Orchestrator must enqueue day_close for incomplete daily tars."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  (daily / "2020-01-01.tar").write_bytes(b"x")
  seen = []

  monkeypatch.setattr(
      jr,
      "enqueue_day_close_if_needed",
      lambda client, tar, **k: seen.append(tar) or True,
  )

  class _C:
    pass

  n = qo._enqueue_day_closes_for_daily_dir(_C(), tgz_archive_dir=str(daily))
  assert n == 1
  assert any(p.endswith("2020-01-01.tar") for p in seen)


def test_boot_stream_discover_does_not_call_run_find_stats():
  """Boot discover must stream stdout chunks, not capture-all run_find_stats."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  import inspect

  src = inspect.getsource(qo._boot_stream_discover)
  assert "run_find_stats(" not in src
  assert "iter_find_stats_stdout_chunks" in src
  assert "stream_enqueue_ingest_from_find_stdout_chunks" in src


def test_idle_reconstruct_enqueues_discover_and_rescans(monkeypatch):
  """Idle reconstruct must use JOB_KIND_DISCOVER then re-run streaming discover."""
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  calls = {"boot": 0, "rpush": []}

  class _C:
    def __init__(self):
      self._q = []

    def rpush(self, key, value):
      self._q.append(value)
      calls["rpush"].append((key, value))
      return 1

    def lpop(self, key):
      del key
      return self._q.pop(0) if self._q else None

  def _boot(*a, **k):
    calls["boot"] += 1
    return type(
        "S",
        (),
        {
            "enqueued_ingest": 0,
            "enqueued_append": 0,
            "enqueued_day_close": 0,
            "seen": 0,
            "skipped_complete": 0,
        },
    )()

  monkeypatch.setattr(qo, "_boot_stream_discover", _boot)
  monkeypatch.setattr(qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0)
  monkeypatch.setattr(
      jq,
      "enqueue_list_job",
      lambda client, *, kind, identity: client.rpush(kind, identity) or True,
  )
  monkeypatch.setattr(
      jq,
      "pop_list_job",
      lambda client, *, kind: client.lpop(kind),
  )
  n = qo._idle_reconstruct_pass(
      _C(),
      "/archive",
      tgz_archive_dir="/daily",
      log_fn=lambda *a, **k: None,
  )
  assert calls["boot"] == 1
  assert n >= 1
  assert any(v == "rescan" for _k, v in calls["rpush"])


def test_cli_backlog_current_retired():
  """Dual-mode backlog/current argv must fail closed."""
  with pytest.raises(SystemExit) as ei:
    st.parse_sync_timedb_argv(["sync_timedb.py", "backlog"])
  assert "retired" in str(ei.value).lower()
  with pytest.raises(SystemExit) as ei:
    st.parse_sync_timedb_argv(["sync_timedb.py", "current"])
  assert "retired" in str(ei.value).lower()
